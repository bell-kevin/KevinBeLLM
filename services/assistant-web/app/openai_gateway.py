# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict OpenAI Chat Completions adapter for authenticated coding clients."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from fastapi import Request

from .config import Settings


OPENAI_MAX_REQUEST_BYTES = 2 * 1024 * 1024
OPENAI_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_MESSAGES = 256
MAX_TOOLS = 128
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_ALLOWED_BODY_KEYS = {
    "model",
    "messages",
    "stream",
    "stream_options",
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "stop",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "n",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "reasoning_effort",
    "response_format",
    "user",
}
_ALLOWED_MESSAGE_KEYS = {"role", "content", "name", "tool_calls", "tool_call_id"}


class GatewayRequestError(ValueError):
    def __init__(self, message: str, *, param: str | None = None):
        super().__init__(message)
        self.message = message
        self.param = param


def bearer_token(request: Request) -> str | None:
    """Read exactly one strict Bearer credential; duplicate headers fail closed."""
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        return None
    scheme, separator, credential = values[0].partition(" ")
    if separator != " " or scheme.casefold() != "bearer":
        return None
    if not credential or credential != credential.strip() or any(
        character.isspace() for character in credential
    ):
        return None
    return credential


def _plain_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bounded_string(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and len(value) <= maximum


def _serialized_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _validate_tool_calls(value: Any, message_index: int) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_TOOLS:
        raise GatewayRequestError(
            "Assistant tool_calls must be a bounded list",
            param=f"messages.{message_index}.tool_calls",
        )
    for call in value:
        if not isinstance(call, dict) or set(call) - {"id", "type", "function"}:
            raise GatewayRequestError("A tool call is invalid", param="messages")
        if call.get("type", "function") != "function":
            raise GatewayRequestError("Only function tool calls are supported", param="messages")
        if not _bounded_string(call.get("id"), 200):
            raise GatewayRequestError("A tool call ID is invalid", param="messages")
        function = call.get("function")
        if not isinstance(function, dict) or set(function) - {"name", "arguments"}:
            raise GatewayRequestError("A tool call function is invalid", param="messages")
        if not isinstance(function.get("name"), str) or not _TOOL_NAME_RE.fullmatch(
            function["name"]
        ):
            raise GatewayRequestError("A tool call name is invalid", param="messages")
        if not _bounded_string(function.get("arguments"), 256_000):
            raise GatewayRequestError("Tool call arguments are invalid", param="messages")


def _validate_content(value: Any, message_index: int) -> None:
    if value is None or isinstance(value, str):
        return
    if not isinstance(value, list) or len(value) > 256:
        raise GatewayRequestError(
            "Message content must be text", param=f"messages.{message_index}.content"
        )
    for part in value:
        # This deployment is text-only. Reject image/file URLs rather than making
        # the inference server fetch or decode content it cannot use.
        if (
            not isinstance(part, dict)
            or set(part) - {"type", "text", "cache_control"}
            or part.get("type") != "text"
            or not _bounded_string(part.get("text"), 1_000_000)
        ):
            raise GatewayRequestError(
                "Only text content parts are supported",
                param=f"messages.{message_index}.content",
            )


def _validate_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_MESSAGES:
        raise GatewayRequestError(
            f"messages must contain between 1 and {MAX_MESSAGES} items",
            param="messages",
        )
    allowed_roles = {"system", "developer", "user", "assistant", "tool"}
    for index, message in enumerate(value):
        if not isinstance(message, dict) or set(message) - _ALLOWED_MESSAGE_KEYS:
            raise GatewayRequestError("A message is invalid", param=f"messages.{index}")
        role = message.get("role")
        if role not in allowed_roles:
            raise GatewayRequestError("A message role is invalid", param=f"messages.{index}.role")
        _validate_content(message.get("content"), index)
        if "name" in message and not _bounded_string(message["name"], 128):
            raise GatewayRequestError("A message name is invalid", param=f"messages.{index}.name")
        if "tool_call_id" in message and not _bounded_string(message["tool_call_id"], 200):
            raise GatewayRequestError(
                "A tool_call_id is invalid", param=f"messages.{index}.tool_call_id"
            )
        if "tool_calls" in message:
            if role != "assistant":
                raise GatewayRequestError(
                    "Only assistant messages may contain tool_calls",
                    param=f"messages.{index}.tool_calls",
                )
            _validate_tool_calls(message["tool_calls"], index)
    return value


def _validate_tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_TOOLS:
        raise GatewayRequestError(
            f"tools must contain between 1 and {MAX_TOOLS} items", param="tools"
        )
    for item in value:
        if not isinstance(item, dict) or set(item) - {"type", "function"}:
            raise GatewayRequestError("A tool definition is invalid", param="tools")
        if item.get("type") != "function":
            raise GatewayRequestError("Only function tools are supported", param="tools")
        function = item.get("function")
        if not isinstance(function, dict) or set(function) - {
            "name",
            "description",
            "parameters",
            "strict",
        }:
            raise GatewayRequestError("A function definition is invalid", param="tools")
        name = function.get("name")
        if not isinstance(name, str) or not _TOOL_NAME_RE.fullmatch(name):
            raise GatewayRequestError("A function name is invalid", param="tools")
        if "description" in function and not _bounded_string(function["description"], 16_000):
            raise GatewayRequestError("A function description is too large", param="tools")
        if not isinstance(function.get("parameters"), dict):
            raise GatewayRequestError("Function parameters must be an object", param="tools")
        if len(json.dumps(function["parameters"], ensure_ascii=False)) > 256_000:
            raise GatewayRequestError("A function schema is too large", param="tools")
        if "strict" in function and not isinstance(function["strict"], bool):
            raise GatewayRequestError("Function strict must be boolean", param="tools")
    return value


def validate_chat_body(payload: Any, settings: Settings) -> dict[str, Any]:
    """Allow the Zoo/OpenAI contract while excluding llama.cpp control knobs."""
    if not isinstance(payload, dict):
        raise GatewayRequestError("The request body must be a JSON object")
    unknown = set(payload) - _ALLOWED_BODY_KEYS
    if unknown:
        raise GatewayRequestError(
            "The request contains unsupported fields", param=sorted(unknown)[0]
        )

    model = payload.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= 200:
        raise GatewayRequestError("model is required", param="model")
    result: dict[str, Any] = {
        "model": model,
        "messages": _validate_messages(payload.get("messages")),
    }

    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise GatewayRequestError("stream must be boolean", param="stream")
    result["stream"] = stream

    stream_options = payload.get("stream_options")
    if stream_options is not None:
        if (
            not stream
            or not isinstance(stream_options, dict)
            or set(stream_options) - {"include_usage"}
            or not isinstance(stream_options.get("include_usage", False), bool)
        ):
            raise GatewayRequestError("stream_options is invalid", param="stream_options")
        result["stream_options"] = {
            "include_usage": stream_options.get("include_usage", False)
        }

    supplied_max = payload.get("max_tokens", payload.get("max_completion_tokens"))
    if "max_tokens" in payload and "max_completion_tokens" in payload:
        raise GatewayRequestError(
            "Use only one output-token field", param="max_completion_tokens"
        )
    if supplied_max is None:
        supplied_max = settings.zoo_max_output_tokens
    if (
        not isinstance(supplied_max, int)
        or isinstance(supplied_max, bool)
        or not 1 <= supplied_max <= settings.zoo_max_output_tokens
    ):
        raise GatewayRequestError(
            f"Output tokens must be between 1 and {settings.zoo_max_output_tokens}",
            param="max_tokens",
        )
    # llama.cpp's stable Chat Completions spelling.
    result["max_tokens"] = supplied_max

    for key, minimum, maximum in (
        ("temperature", 0.0, 2.0),
        ("top_p", 0.0, 1.0),
        ("presence_penalty", -2.0, 2.0),
        ("frequency_penalty", -2.0, 2.0),
    ):
        if key in payload:
            value = payload[key]
            if not _plain_number(value) or not minimum <= value <= maximum:
                raise GatewayRequestError(f"{key} is out of range", param=key)
            result[key] = value

    if "seed" in payload:
        seed = payload["seed"]
        if not isinstance(seed, int) or isinstance(seed, bool) or not -(2**31) <= seed < 2**31:
            raise GatewayRequestError("seed is invalid", param="seed")
        result["seed"] = seed
    if payload.get("n", 1) != 1:
        raise GatewayRequestError("Only one completion is supported", param="n")

    if "stop" in payload:
        stop = payload["stop"]
        valid_stop = _bounded_string(stop, 500) or (
            isinstance(stop, list)
            and len(stop) <= 8
            and all(_bounded_string(item, 200) for item in stop)
        )
        if not valid_stop:
            raise GatewayRequestError("stop is invalid", param="stop")
        result["stop"] = stop

    tools = payload.get("tools")
    if tools is not None:
        result["tools"] = _validate_tools(tools)
    prompt_bytes = _serialized_bytes(result["messages"])
    if tools is not None:
        prompt_bytes += _serialized_bytes(result["tools"])
    # This deliberately lenient byte-to-token estimate is not a tokenizer. It is
    # an aggregate safety ceiling that prevents individually valid messages and
    # tool schemas from consuming unbounded context together.
    input_token_budget = max(1, settings.zoo_context_window - supplied_max)
    if prompt_bytes > input_token_budget * 8:
        raise GatewayRequestError(
            "The combined messages and tools exceed the configured context budget",
            param="messages",
        )
    if "parallel_tool_calls" in payload:
        if not isinstance(payload["parallel_tool_calls"], bool):
            raise GatewayRequestError(
                "parallel_tool_calls must be boolean", param="parallel_tool_calls"
            )
        result["parallel_tool_calls"] = payload["parallel_tool_calls"]
    if "tool_choice" in payload:
        choice = payload["tool_choice"]
        valid_choice = choice in {"auto", "none", "required"} if isinstance(choice, str) else False
        if isinstance(choice, dict):
            function = choice.get("function")
            valid_choice = (
                set(choice) == {"type", "function"}
                and choice.get("type") == "function"
                and isinstance(function, dict)
                and set(function) == {"name"}
                and isinstance(function.get("name"), str)
                and _TOOL_NAME_RE.fullmatch(function["name"]) is not None
            )
        if not valid_choice or (tools is None and choice != "none"):
            raise GatewayRequestError("tool_choice is invalid", param="tool_choice")
        result["tool_choice"] = choice

    if "response_format" in payload:
        response_format = payload["response_format"]
        if not isinstance(response_format, dict) or response_format.get("type") not in {
            "text",
            "json_object",
        }:
            raise GatewayRequestError("response_format is invalid", param="response_format")
        result["response_format"] = {"type": response_format["type"]}

    effort = payload.get("reasoning_effort", "none")
    if effort != "none":
        raise GatewayRequestError(
            "Reasoning level is not supported for this Zoo Code profile; disable it",
            param="reasoning_effort",
        )
    result["reasoning_effort"] = "none"
    result["parse_tool_calls"] = True
    result["chat_template_kwargs"] = {"enable_thinking": False}
    return result


async def open_upstream_chat(
    client: httpx.AsyncClient,
    settings: Settings,
    body: dict[str, Any],
) -> httpx.Response:
    request = client.build_request(
        "POST",
        f"{settings.inference_base_url}/v1/chat/completions",
        json=body,
        headers={
            "Accept": "text/event-stream" if body["stream"] else "application/json",
            "Content-Type": "application/json",
        },
        timeout=settings.chat_deadline_seconds,
    )
    return await client.send(request, stream=True, follow_redirects=False)


async def read_upstream_json(response: httpx.Response) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > OPENAI_MAX_RESPONSE_BYTES:
            raise GatewayRequestError("The local model response was too large")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayRequestError("The local model returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise GatewayRequestError("The local model returned invalid JSON")
    return bytes(content)
