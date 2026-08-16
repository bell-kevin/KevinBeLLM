# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated FastAPI entry point for the self-hosted browser assistant."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal
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


class RequestBodyLimitMiddleware:
    """Read at most a bounded body before letting framework parsers buffer it."""

    def __init__(self, application: ASGIApp, maximum: int):
        self.application = application
        self.maximum = maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.application(scope, receive, send)
            return
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
                if declared > self.maximum:
                    await self._error(scope, send, 413, "Request body is too large")
                    return
                break

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
                    if len(body) + len(chunk) > self.maximum:
                        await self._error(scope, send, 413, "Request body is too large")
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await self._error(scope, send, 408, "Request body timed out")
            return

        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if delivered:
                # StreamingResponse polls for a later disconnect; delegate after
                # the single replayed body instead of fabricating one immediately.
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.application(scope, bounded_receive, send)

    @staticmethod
    async def _error(scope: Scope, send: Send, status: int, detail: str) -> None:
        state = scope.setdefault("state", {})
        request_id = state.setdefault("request_id", uuid4().hex)
        body = json.dumps({"detail": detail, "request_id": request_id}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-request-id", request_id.encode("ascii")),
                ],
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


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=32)
    # Opt-in per request. Thinking measurably raises answer quality but costs
    # hundreds to thousands of extra tokens before any visible text appears.
    reasoning: bool = False

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
    application.add_middleware(RequestBodyLimitMiddleware, maximum=MAX_REQUEST_BYTES)

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
        if request.url.path.startswith("/api/"):
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
            return await installed_models(request.app.state.http, configured)
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
        # The event loop cannot be interrupted between this counter check and
        # acquire(), making it a non-blocking bounded admission operation.
        if getattr(request.app.state.chat_admission, "_value", 0) <= 0:
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
        # A single bounded answer (including worst-case source URLs) fits without
        # letting a disconnected client strand the worker while it emits its sentinel.
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
                        )
                finally:
                    request.app.state.chat_slots.release()
                # The visible answer already streamed token by token while the
                # model generated it. "done" carries the authoritative text so the
                # client reconciles its live preview against post-processing such
                # as appended source URLs or the assistant-character cap.
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
        if asset_path.startswith("api/"):
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
