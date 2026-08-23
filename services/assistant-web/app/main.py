# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated FastAPI entry point for the self-hosted browser assistant."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Awaitable, Callable, Literal
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .assistant import AssistantError, installed_models, run_chat
from .config import STATIC_DIR, Settings, load_settings
from .database import Database, User
from .openai_gateway import (
    OPENAI_MAX_REQUEST_BYTES,
    OPENAI_MAX_RESPONSE_BYTES,
    GatewayRequestError,
    bearer_token,
    open_upstream_chat,
    read_upstream_json,
    validate_chat_body,
)
from .security import (
    SESSION_COOKIE,
    BoundedRateLimiter,
    check_csrf,
    check_request_origin,
    csrf_token,
    hash_password,
    normalize_email,
    valid_session_token,
    verify_password,
)


MAX_REQUEST_BYTES = 128 * 1024
UPSTREAM_CLOSE_TIMEOUT_SECONDS = 5


class OpenAIHTTPException(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        error_type: str,
        code: str,
        param: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.code = code
        self.param = param
        self.headers = headers or {}


class ManagedStreamingResponse(StreamingResponse):
    """Run cleanup around the response's full ASGI lifetime, including headers."""

    def __init__(
        self,
        *args: Any,
        cleanup: Callable[[], Awaitable[None]],
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._cleanup()


class RequestBodyLimitMiddleware:
    """Authenticate and bound bodies before framework parsers buffer them."""

    def __init__(
        self,
        application: ASGIApp,
        maximum: int,
        openai_maximum: int = OPENAI_MAX_REQUEST_BYTES,
        api_database: Database | None = None,
        api_client_limiter: BoundedRateLimiter | None = None,
        api_credential_limiter: BoundedRateLimiter | None = None,
        api_body_slots: asyncio.Semaphore | None = None,
    ):
        self.application = application
        self.maximum = maximum
        self.openai_maximum = openai_maximum
        self.api_database = api_database
        self.api_client_limiter = api_client_limiter
        self.api_credential_limiter = api_credential_limiter
        self.api_body_slots = api_body_slots

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.application(scope, receive, send)
            return
        is_openai_chat = scope.get("path") == "/v1/chat/completions"
        if is_openai_chat and self.api_database is not None:
            request = Request(scope)
            token = bearer_token(request)
            client_key = request.client.host if request.client else "unknown"
            client_rate = (
                await self.api_client_limiter.check(f"client:{client_key}")
                if self.api_client_limiter is not None
                else None
            )
            credential_rate = (
                await self.api_credential_limiter.check(
                    f"token:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"
                )
                if token and self.api_credential_limiter is not None
                else None
            )
            limited_rate = (
                client_rate
                if client_rate is not None and not client_rate.allowed
                else credential_rate
                if credential_rate is not None and not credential_rate.allowed
                else None
            )
            if limited_rate is not None:
                await self._error(
                    scope,
                    send,
                    429,
                    "Too many API requests; try again shortly",
                    error_type="rate_limit_error",
                    code="rate_limit_exceeded",
                    headers={"Retry-After": str(limited_rate.retry_after)},
                )
                return
            user = await self.api_database.user_for_api_token(token or "", touch=False)
            if user is None or token is None:
                await self._error(
                    scope,
                    send,
                    401,
                    "Invalid or expired API key",
                    error_type="authentication_error",
                    code="invalid_api_key",
                    headers={"WWW-Authenticate": 'Bearer realm="KevinBeLLM"'},
                )
                return
            state = scope.setdefault("state", {})
            state["preauthenticated_api_token"] = token
            state["preauthenticated_api_user"] = user

        maximum = (
            self.openai_maximum
            if is_openai_chat
            else self.maximum
        )
        declared: int | None = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await self._error(scope, send, 400, "Invalid Content-Length")
                    return
                if declared < 0:
                    await self._error(scope, send, 400, "Invalid Content-Length")
                    return
                if declared > maximum:
                    await self._error(scope, send, 413, "Request body is too large")
                    return
                break

        body_slot_acquired = False
        if is_openai_chat and self.api_body_slots is not None:
            if self.api_body_slots.locked():
                await self._error(
                    scope,
                    send,
                    503,
                    "The local model queue is full; try again shortly",
                    error_type="server_error",
                    code="queue_full",
                    headers={"Retry-After": "10"},
                )
                return
            await self.api_body_slots.acquire()
            body_slot_acquired = True

        body_complete = False
        try:
            body = bytearray()
            async with asyncio.timeout(30):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        continue
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > maximum:
                        await self._error(scope, send, 413, "Request body is too large")
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
            body_complete = True
        except TimeoutError:
            await self._error(scope, send, 408, "Request body timed out")
            return
        finally:
            if body_slot_acquired and not body_complete:
                self.api_body_slots.release()

        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if delivered:
                # StreamingResponse polls for a later disconnect; delegate after
                # the single replayed body instead of fabricating one immediately.
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        try:
            await self.application(scope, bounded_receive, send)
        finally:
            if body_slot_acquired:
                self.api_body_slots.release()

    @staticmethod
    async def _error(
        scope: Scope,
        send: Send,
        status: int,
        detail: str,
        *,
        error_type: str | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        state = scope.setdefault("state", {})
        request_id = state.setdefault("request_id", uuid4().hex)
        content = (
            {
                "error": {
                    "message": detail,
                    "type": error_type or "invalid_request_error",
                    "param": None,
                    "code": code or "invalid_request",
                },
                "request_id": request_id,
            }
            if scope.get("path", "").startswith("/v1/")
            else {"detail": detail, "request_id": request_id}
        )
        body = json.dumps(content).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
            (b"x-request-id", request_id.encode("ascii")),
        ]
        if headers:
            response_headers.extend(
                (name.lower().encode("ascii"), value.encode("latin-1"))
                for name, value in headers.items()
            )
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1_024)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class ChangePasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    current_password: str = Field(min_length=1, max_length=1_024)
    new_password: str = Field(min_length=14, max_length=256)

    @model_validator(mode="after")
    def passwords_must_differ(self) -> "ChangePasswordBody":
        if self.current_password == self.new_password:
            raise ValueError("The new password must be different")
        return self


class ApiTokenCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    name: str = Field(min_length=1, max_length=80)
    current_password: str = Field(min_length=1, max_length=1_024)

    @model_validator(mode="after")
    def normalize_name(self) -> "ApiTokenCreateBody":
        cleaned = " ".join(self.name.strip().split())
        if not cleaned:
            raise ValueError("The token name is required")
        self.name = cleaned
        return self


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=32)
    # Opt-in per request. Thinking measurably raises answer quality but costs
    # hundreds to thousands of extra tokens before any visible text appears.
    reasoning: bool = False
    # Fast mode is local-model-only: omitting live tools removes llama.cpp's
    # tool grammar and lets it keep sampling on the GPUs.
    fast: bool = False

    @model_validator(mode="after")
    def validate_history(self) -> "ChatBody":
        if self.messages[-1].role != "user":
            raise ValueError("The final message must be from the user")
        if sum(len(message.content) for message in self.messages) > 48_000:
            raise ValueError("The message history is too large")
        return self


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _json_error(request: Request, status: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "request_id": _request_id(request)},
    )


def _openai_error_response(
    request: Request,
    status: int,
    message: str,
    *,
    error_type: str,
    code: str,
    param: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": param,
                "code": code,
            },
            "request_id": _request_id(request),
        },
    )
    if headers:
        response.headers.update(headers)
    return response


def _zoo_setup(settings: Settings) -> dict[str, Any]:
    base_url = settings.zoo_api_base_url or f"{settings.public_url.rstrip('/')}/v1"
    return {
        "base_url": base_url,
        "model": settings.default_model,
        "context_window": settings.zoo_context_window,
        "max_output_tokens": settings.zoo_max_output_tokens,
        "token_ttl_days": settings.api_token_ttl_seconds // (24 * 3600),
    }


def _api_token_metadata(token: Any) -> dict[str, Any]:
    return {
        "id": token.id,
        "name": token.name,
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
    }


def _delete_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.secure_cookie,
        httponly=True,
        samesite="lax",
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _safe_static_file(path: Path, static_dir: Path) -> Path | None:
    try:
        resolved = path.resolve()
        root = static_dir.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


async def _close_upstream_response(response: httpx.Response) -> None:
    """Best-effort bounded close; queue release must never depend on its success."""
    try:
        async with asyncio.timeout(UPSTREAM_CLOSE_TIMEOUT_SECONDS):
            await response.aclose()
    except (Exception, asyncio.CancelledError):
        pass


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or load_settings()
    database = Database(
        configured.data_dir,
        configured.session_ttl_seconds,
        configured.database_concurrency,
    )
    login_limiter = BoundedRateLimiter(8, 15 * 60)
    password_limiter = BoundedRateLimiter(5, 15 * 60)
    chat_limiter = BoundedRateLimiter(20, 60)
    session_limiter = BoundedRateLimiter(120, 60)
    api_client_limiter = BoundedRateLimiter(240, 60)
    api_credential_limiter = BoundedRateLimiter(120, 60)
    api_body_slots = asyncio.Semaphore(
        max(1, configured.chat_concurrency + configured.chat_pending)
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        if await database.user_count() == 0:
            raise RuntimeError(
                "No administrator exists; run `python -m app.bootstrap` once before starting"
            )
        application.state.database = database
        application.state.http = httpx.AsyncClient(
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        application.state.chat_slots = asyncio.Semaphore(configured.chat_concurrency)
        application.state.chat_admission = asyncio.Semaphore(
            configured.chat_concurrency + configured.chat_pending
        )
        application.state.active_chats: dict[int, set[asyncio.Task[None]]] = {}
        try:
            yield
        finally:
            await application.state.http.aclose()

    application = FastAPI(
        title="KevinBeLLM",
        description="A private, self-hosted, read-only-tool local model assistant.",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = configured
    application.state.api_body_slots = api_body_slots
    application.add_middleware(
        RequestBodyLimitMiddleware,
        maximum=MAX_REQUEST_BYTES,
        api_database=database,
        api_client_limiter=api_client_limiter,
        api_credential_limiter=api_credential_limiter,
        api_body_slots=api_body_slots,
    )

    @application.middleware("http")
    async def security_middleware(request: Request, call_next: Any) -> Response:
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.path.startswith(("/api/", "/v1/")):
            response.headers["Cache-Control"] = "no-store"
        if configured.secure_cookie:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        # Pydantic's normal error body can echo rejected input, including passwords.
        return _json_error(request, 422, "Invalid request")

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        response = _json_error(request, exc.status_code, detail)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @application.exception_handler(OpenAIHTTPException)
    async def openai_http_error(
        request: Request, exc: OpenAIHTTPException
    ) -> JSONResponse:
        return _openai_error_response(
            request,
            exc.status_code,
            exc.message,
            error_type=exc.error_type,
            code=exc.code,
            param=exc.param,
            headers=exc.headers,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        return _json_error(request, 500, "Internal server error")

    async def optional_user(request: Request) -> tuple[User | None, str | None]:
        token = request.cookies.get(SESSION_COOKIE)
        if not valid_session_token(token):
            return None, None
        client_key = request.client.host if request.client else "unknown"
        probe_limit = await session_limiter.check(f"ip:{client_key}")
        if not probe_limit.allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many session checks; try again shortly",
                headers={"Retry-After": str(probe_limit.retry_after)},
            )
        user = await database.user_for_session(token)
        return user, token if user else None

    async def live_session_user(token: str, expected_user_id: int) -> User | None:
        user = await database.user_for_session(token)
        return user if user and user.id == expected_user_id else None

    async def require_user(request: Request) -> User:
        user, token = await optional_user(request)
        if user is None or token is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        request.state.session_token = token
        request.state.user = user
        return user

    async def require_api_user(request: Request) -> User:
        token = bearer_token(request)
        preauthenticated_user = getattr(
            request.state, "preauthenticated_api_user", None
        )
        preauthenticated_token = getattr(
            request.state, "preauthenticated_api_token", None
        )
        if preauthenticated_user is not None:
            live_user = (
                await database.user_for_api_token(token or "")
                if token is not None and token == preauthenticated_token
                else None
            )
            if live_user is None or live_user.id != preauthenticated_user.id:
                raise OpenAIHTTPException(
                    401,
                    "Invalid or expired API key",
                    error_type="authentication_error",
                    code="invalid_api_key",
                    headers={"WWW-Authenticate": 'Bearer realm="KevinBeLLM"'},
                )
            request.state.api_token = token
            request.state.user = live_user
            return live_user

        client_key = request.client.host if request.client else "unknown"
        client_rate = await api_client_limiter.check(f"client:{client_key}")
        credential_rate = (
            await api_credential_limiter.check(
                f"token:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"
            )
            if token
            else None
        )
        limited_rate = (
            client_rate
            if not client_rate.allowed
            else credential_rate
            if credential_rate is not None and not credential_rate.allowed
            else None
        )
        if limited_rate is not None:
            raise OpenAIHTTPException(
                429,
                "Too many API requests; try again shortly",
                error_type="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": str(limited_rate.retry_after)},
            )
        user = await database.user_for_api_token(token or "")
        if user is None or token is None:
            raise OpenAIHTTPException(
                401,
                "Invalid or expired API key",
                error_type="authentication_error",
                code="invalid_api_key",
                headers={"WWW-Authenticate": 'Bearer realm="KevinBeLLM"'},
            )
        request.state.api_token = token
        request.state.user = user
        return user

    async def require_csrf(
        request: Request,
        _user: Annotated[User, Depends(require_user)],
        supplied: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> None:
        check_request_origin(request, configured.public_origin)
        check_csrf(request.state.session_token, supplied)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "assistant-web"}

    @application.get("/source")
    async def source() -> RedirectResponse:
        return RedirectResponse(configured.source_url, status_code=302)

    @application.post("/api/auth/login")
    async def login(request: Request, body: LoginBody) -> JSONResponse:
        check_request_origin(request, configured.public_origin)
        client_key = request.client.host if request.client else "unknown"
        try:
            email = normalize_email(body.email)
        except ValueError:
            email = "invalid@invalid.local"
        email_key = hashlib.sha256(email.encode("utf-8")).hexdigest()
        for key in (f"ip:{client_key}", f"email:{email_key}"):
            limit = await login_limiter.check(key)
            if not limit.allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts; try again later",
                    headers={"Retry-After": str(limit.retry_after)},
                )

        user = await database.user_for_login(email)
        if not await verify_password(user.password_hash if user else None, body.password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        assert user is not None
        token = await database.create_session(user.id)
        response = JSONResponse(
            {
                "authenticated": True,
                "user": {"name": user.name, "email": user.email},
                "csrf_token": csrf_token(token),
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=configured.session_ttl_seconds,
            path="/",
            secure=configured.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return response

    @application.get("/api/auth/session")
    async def session(request: Request) -> JSONResponse:
        user, token = await optional_user(request)
        if user is None or token is None:
            response = JSONResponse({"authenticated": False})
            if request.cookies.get(SESSION_COOKIE):
                _delete_session_cookie(response, configured)
            return response
        return JSONResponse(
            {
                "authenticated": True,
                "user": {"name": user.name, "email": user.email},
                "csrf_token": csrf_token(token),
            }
        )

    @application.get("/api/auth/api-tokens")
    async def list_api_tokens(
        user: Annotated[User, Depends(require_user)],
    ) -> dict[str, Any]:
        tokens = await database.api_tokens_for_user(user.id)
        return {
            "tokens": [_api_token_metadata(token) for token in tokens],
            "setup": _zoo_setup(configured),
        }

    @application.post("/api/auth/api-tokens", status_code=201)
    async def create_api_token(
        request: Request,
        body: ApiTokenCreateBody,
        user: Annotated[User, Depends(require_user)],
        _csrf: Annotated[None, Depends(require_csrf)],
    ) -> dict[str, Any]:
        rate = await password_limiter.check(f"user:{user.id}")
        if not rate.allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many password attempts; try again later",
                headers={"Retry-After": str(rate.retry_after)},
            )
        login_user = await database.user_for_login(user.email)
        if not await verify_password(
            login_user.password_hash if login_user else None, body.current_password
        ):
            raise HTTPException(status_code=401, detail="Current password is incorrect")
        assert login_user is not None and login_user.password_hash is not None
        try:
            secret, credential = await database.create_api_token(
                user.id,
                body.name,
                configured.api_token_ttl_seconds,
                expected_password_hash=login_user.password_hash,
                session_token=request.state.session_token,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=401, detail="Authentication changed; sign in again"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "token": secret,
            "credential": _api_token_metadata(credential),
            "setup": _zoo_setup(configured),
        }

    @application.delete("/api/auth/api-tokens/{token_id}", status_code=204)
    async def revoke_api_token(
        request: Request,
        token_id: int,
        user: Annotated[User, Depends(require_user)],
        _csrf: Annotated[None, Depends(require_csrf)],
    ) -> Response:
        if token_id < 1 or not await database.delete_api_token(user.id, token_id):
            raise HTTPException(status_code=404, detail="API token not found")
        for task in tuple(request.app.state.active_chats.get(user.id, ())):
            task.cancel()
        return Response(status_code=204)

    @application.post("/api/auth/logout", status_code=204)
    async def logout(
        request: Request,
        _csrf: Annotated[None, Depends(require_csrf)],
    ) -> Response:
        await database.delete_session(request.state.session_token)
        for task in tuple(
            request.app.state.active_chats.get(request.state.user.id, ())
        ):
            task.cancel()
        response = Response(status_code=204)
        _delete_session_cookie(response, configured)
        return response

    @application.post("/api/auth/change-password", status_code=204)
    async def change_password(
        request: Request,
        body: ChangePasswordBody,
        user: Annotated[User, Depends(require_user)],
        _csrf: Annotated[None, Depends(require_csrf)],
    ) -> Response:
        rate = await password_limiter.check(f"user:{user.id}")
        if not rate.allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many password attempts; try again later",
                headers={"Retry-After": str(rate.retry_after)},
            )
        login_user = await database.user_for_login(user.email)
        if not await verify_password(
            login_user.password_hash if login_user else None, body.current_password
        ):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        password_hash = await hash_password(body.new_password)
        await database.change_password(user.id, password_hash)
        for task in tuple(request.app.state.active_chats.get(user.id, ())):
            task.cancel()
        response = Response(status_code=204)
        _delete_session_cookie(response, configured)
        return response

    @application.get("/api/models")
    async def models(
        request: Request,
        _user: Annotated[User, Depends(require_user)],
    ) -> dict[str, Any]:
        try:
            catalog = await installed_models(request.app.state.http, configured)
            return {
                **catalog,
                "fast_mode_available": True,
            }
        except AssistantError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @application.post("/api/chat")
    async def chat(
        request: Request,
        body: ChatBody,
        user: Annotated[User, Depends(require_user)],
        _csrf: Annotated[None, Depends(require_csrf)],
    ) -> StreamingResponse:
        rate = await chat_limiter.check(f"user:{user.id}")
        if not rate.allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many chat requests; try again shortly",
                headers={"Retry-After": str(rate.retry_after)},
            )
        request_id = _request_id(request)
        # Admission includes both executing and queued work, so streams cannot build
        # an unbounded waiter list behind the expensive local-model semaphore.
        # locked() is public API and true exactly when acquire() would block, and
        # the event loop cannot be interrupted between this check and acquire(),
        # making it a non-blocking bounded admission operation.
        if request.app.state.chat_admission.locked():
            raise HTTPException(
                status_code=503,
                detail="The local model queue is full; try again shortly",
                headers={"Retry-After": "10"},
            )
        await request.app.state.chat_admission.acquire()
        try:
            available = await installed_models(request.app.state.http, configured)
            installed_ids = {item["id"] for item in available["models"]}
            if body.model not in installed_ids:
                raise HTTPException(
                    status_code=400, detail="The selected model is not installed"
                )
        except BaseException as exc:
            request.app.state.chat_admission.release()
            if isinstance(exc, AssistantError):
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            raise
        session_token = request.state.session_token
        # A single bounded answer fits without letting a disconnected client
        # strand the worker while it emits its sentinel.
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(maxsize=512)

        async def emit(event: str, payload: dict[str, Any]) -> None:
            await queue.put((event, payload))

        async def worker() -> None:
            try:
                try:
                    await asyncio.wait_for(
                        request.app.state.chat_slots.acquire(),
                        timeout=configured.chat_queue_timeout_seconds,
                    )
                except TimeoutError:
                    await emit(
                        "error",
                        {
                            "message": "The local model remained busy; try again shortly",
                            "request_id": request_id,
                        },
                    )
                    return
                try:
                    # A password change or logout while queued must revoke this work.
                    if not await live_session_user(session_token, user.id):
                        await emit(
                            "error",
                            {"message": "The session expired before this chat started"},
                        )
                        return
                    async with asyncio.timeout(configured.chat_deadline_seconds):
                        content, sources = await run_chat(
                            request.app.state.http,
                            configured,
                            body.model,
                            [message.model_dump() for message in body.messages],
                            emit,
                            body.reasoning,
                            tools_enabled=not body.fast,
                        )
                finally:
                    request.app.state.chat_slots.release()
                # The visible answer already streamed token by token while the
                # model generated it. "done" carries the authoritative text so the
                # client can reconcile its plain live preview against the final
                # capped answer and render it as Markdown.
                await emit(
                    "done",
                    {"model": body.model, "sources": sources, "content": content},
                )
            except asyncio.CancelledError:
                raise
            except AssistantError as exc:
                await emit("error", {"message": str(exc), "request_id": request_id})
            except TimeoutError:
                await emit(
                    "error",
                    {"message": "The chat reached its safety deadline", "request_id": request_id},
                )
            except Exception:
                await emit(
                    "error",
                    {"message": "The chat request failed safely", "request_id": request_id},
                )
            finally:
                request.app.state.chat_admission.release()
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(None)

        async def event_stream() -> AsyncIterator[str]:
            task = asyncio.create_task(worker())
            active = request.app.state.active_chats.setdefault(user.id, set())
            active.add(task)
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=12.0)
                    except TimeoutError:
                        yield f": heartbeat {request_id}\n\n"
                        continue
                    if item is None:
                        break
                    event, payload = item
                    yield _sse(event, payload)
            finally:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                active.discard(task)
                if not active:
                    request.app.state.active_chats.pop(user.id, None)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Request-ID": request_id,
            },
        )

    @application.get("/v1/models")
    async def openai_models(
        request: Request,
        _user: Annotated[User, Depends(require_api_user)],
    ) -> dict[str, Any]:
        try:
            catalog = await installed_models(request.app.state.http, configured)
        except AssistantError as exc:
            raise OpenAIHTTPException(
                502,
                "The local model service is unavailable",
                error_type="server_error",
                code="upstream_unavailable",
            ) from exc
        return {
            "object": "list",
            "data": [
                {
                    "id": model["id"],
                    "object": "model",
                    "created": 0,
                    "owned_by": "kevinbellm",
                }
                for model in catalog["models"]
            ],
        }

    @application.post("/v1/chat/completions")
    async def openai_chat_completions(
        request: Request,
        user: Annotated[User, Depends(require_api_user)],
    ) -> Response:
        try:
            payload = await request.json()
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenAIHTTPException(
                400,
                "The request body is not valid JSON",
                error_type="invalid_request_error",
                code="invalid_json",
            ) from exc
        try:
            body = validate_chat_body(payload, configured)
        except GatewayRequestError as exc:
            raise OpenAIHTTPException(
                400,
                exc.message,
                error_type="invalid_request_error",
                code="invalid_request",
                param=exc.param,
            ) from exc
        loop = asyncio.get_running_loop()
        inference_deadline = loop.time() + configured.chat_deadline_seconds

        rate = await chat_limiter.check(f"user:{user.id}")
        if not rate.allowed:
            raise OpenAIHTTPException(
                429,
                "Too many chat requests; try again shortly",
                error_type="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": str(rate.retry_after)},
            )
        if request.app.state.chat_admission.locked():
            raise OpenAIHTTPException(
                503,
                "The local model queue is full; try again shortly",
                error_type="server_error",
                code="queue_full",
                headers={"Retry-After": "10"},
            )

        async with asyncio.timeout_at(inference_deadline):
            await request.app.state.chat_admission.acquire()
        slot_acquired = False
        handed_off = False
        released = False
        upstream: httpx.Response | None = None
        task = asyncio.current_task()
        active = request.app.state.active_chats.setdefault(user.id, set())
        if task is not None:
            active.add(task)

        def release_resources() -> None:
            nonlocal released
            if released:
                return
            released = True
            if slot_acquired:
                request.app.state.chat_slots.release()
            request.app.state.chat_admission.release()
            if task is not None:
                active.discard(task)
            if not active:
                request.app.state.active_chats.pop(user.id, None)

        try:
            try:
                async with asyncio.timeout_at(inference_deadline):
                    catalog = await installed_models(
                        request.app.state.http, configured
                    )
            except AssistantError as exc:
                raise OpenAIHTTPException(
                    502,
                    "The local model service is unavailable",
                    error_type="server_error",
                    code="upstream_unavailable",
                ) from exc
            installed_ids = {model["id"] for model in catalog["models"]}
            if body["model"] not in installed_ids:
                raise OpenAIHTTPException(
                    404,
                    "The requested model is not installed",
                    error_type="invalid_request_error",
                    code="model_not_found",
                    param="model",
                )

            try:
                queue_timeout_deadline = (
                    loop.time() + configured.chat_queue_timeout_seconds
                )
                queue_deadline = min(
                    inference_deadline,
                    queue_timeout_deadline,
                )
                async with asyncio.timeout_at(queue_deadline):
                    await request.app.state.chat_slots.acquire()
                slot_acquired = True
            except TimeoutError as exc:
                if inference_deadline <= queue_timeout_deadline:
                    raise
                raise OpenAIHTTPException(
                    503,
                    "The local model remained busy; try again shortly",
                    error_type="server_error",
                    code="queue_timeout",
                    headers={"Retry-After": "10"},
                ) from exc

            # A revoked or expired token must not start work after waiting in queue.
            async with asyncio.timeout_at(inference_deadline):
                live_user = await database.user_for_api_token(
                    request.state.api_token, touch=False
                )
            if live_user is None or live_user.id != user.id:
                raise OpenAIHTTPException(
                    401,
                    "Invalid or expired API key",
                    error_type="authentication_error",
                    code="invalid_api_key",
                    headers={"WWW-Authenticate": 'Bearer realm="KevinBeLLM"'},
                )

            try:
                async with asyncio.timeout_at(inference_deadline):
                    upstream = await open_upstream_chat(
                        request.app.state.http, configured, body
                    )
            except httpx.HTTPError as exc:
                raise OpenAIHTTPException(
                    502,
                    "The local model service is unavailable",
                    error_type="server_error",
                    code="upstream_unavailable",
                ) from exc

            if not 200 <= upstream.status_code < 300:
                response_to_close = upstream
                upstream = None
                await _close_upstream_response(response_to_close)
                raise OpenAIHTTPException(
                    502,
                    "The local model service rejected the request",
                    error_type="server_error",
                    code="upstream_rejected",
                )

            content_type = upstream.headers.get("content-type", "").split(";", 1)[0].lower()
            if body["stream"]:
                if content_type != "text/event-stream":
                    response_to_close = upstream
                    upstream = None
                    await _close_upstream_response(response_to_close)
                    raise OpenAIHTTPException(
                        502,
                        "The local model returned an invalid streaming response",
                        error_type="server_error",
                        code="invalid_upstream_response",
                    )
                stream_response = upstream
                stream_cleaned = False

                async def event_stream() -> AsyncIterator[bytes]:
                    received = 0
                    try:
                        async with asyncio.timeout_at(inference_deadline):
                            async for chunk in stream_response.aiter_bytes():
                                received += len(chunk)
                                if received > OPENAI_MAX_RESPONSE_BYTES:
                                    return
                                yield chunk
                    except Exception:
                        return

                async def cleanup_stream() -> None:
                    nonlocal stream_cleaned
                    if stream_cleaned:
                        return
                    stream_cleaned = True
                    # Return scarce capacity even if the transport cannot close.
                    release_resources()
                    await _close_upstream_response(stream_response)

                managed_response = ManagedStreamingResponse(
                    event_stream(),
                    cleanup=cleanup_stream,
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-store",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "X-Request-ID": _request_id(request),
                    },
                )
                handed_off = True
                return managed_response

            if content_type != "application/json":
                response_to_close = upstream
                upstream = None
                await _close_upstream_response(response_to_close)
                raise OpenAIHTTPException(
                    502,
                    "The local model returned an invalid response",
                    error_type="server_error",
                    code="invalid_upstream_response",
                )
            try:
                async with asyncio.timeout_at(inference_deadline):
                    content = await read_upstream_json(upstream)
            except (GatewayRequestError, httpx.HTTPError) as exc:
                raise OpenAIHTTPException(
                    502,
                    "The local model returned an invalid response",
                    error_type="server_error",
                    code="invalid_upstream_response",
                ) from exc
            finally:
                response_to_close = upstream
                upstream = None
                await _close_upstream_response(response_to_close)
            return Response(content=content, media_type="application/json")
        except TimeoutError as exc:
            raise OpenAIHTTPException(
                504,
                "The chat request reached its safety deadline",
                error_type="server_error",
                code="request_timeout",
            ) from exc
        finally:
            if not handed_off:
                response_to_close = upstream
                upstream = None
                release_resources()
                if response_to_close is not None:
                    await _close_upstream_response(response_to_close)

    @application.get("/")
    async def root(request: Request) -> Response:
        user, _token = await optional_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=302)
        index = _safe_static_file(STATIC_DIR / "index.html", STATIC_DIR)
        if index:
            return FileResponse(index)
        return JSONResponse({"authenticated": True, "service": "assistant-web"})

    @application.get("/login")
    async def login_page(request: Request) -> Response:
        user, _token = await optional_user(request)
        if user is not None:
            return RedirectResponse("/", status_code=302)
        page = _safe_static_file(STATIC_DIR / "login.html", STATIC_DIR)
        if page:
            return FileResponse(page)
        return JSONResponse({"authenticated": False, "message": "Sign in to continue"})

    @application.get("/{asset_path:path}")
    async def static_asset(request: Request, asset_path: str) -> Response:
        if asset_path.startswith(("api/", "v1/")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = _safe_static_file(STATIC_DIR / asset_path, STATIC_DIR)
        if candidate:
            if candidate.suffix.lower() == ".html" and candidate.name != "login.html":
                user, _token = await optional_user(request)
                if user is None:
                    return RedirectResponse("/login", status_code=302)
            return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="Not found")

    return application


app = create_app()
