#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic, exact-answer quality regression checks for local llama.cpp.

This is deliberately a small deployment comparator, not an implementation of
the Artificial Analysis Intelligence Index.  It sends a fixed, original corpus
to a loopback-only OpenAI Chat Completions endpoint and scores only answers that
the model places after a ``FINAL:`` marker.  Tool cases compare calls with an
expected inert call and inject checked-in canned data; model output and model-
generated code are never executed.

The evaluator has no third-party runtime dependencies.  Run a baseline before
changing a model or inference setting, then pass that JSON report to
``--compare`` after the change.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)


EVALUATOR_VERSION = 1
DEFAULT_SEED = 424_242
DEFAULT_TIMEOUT_SECONDS = 600.0
# Matches the deployed app's Think and Fast ceilings so the gate exercises the
# same output budget production uses.
PROFILE_MAX_TOKENS = {"reasoning": 24_576, "nonreasoning": 4_096}
# Mirrors the assistant: llama.cpp counts only thinking tokens against the budget
# and, when it runs out, injects this message before forcing the answer, so a
# case that would have exhausted max_tokens mid-thought still yields a scoreable
# FINAL line. Keep the text identical to the app's DEFAULT_REASONING_BUDGET_MESSAGE.
REASONING_ANSWER_RESERVE_TOKENS = 4_096
REASONING_BUDGET_MESSAGE = (
    "Thinking time is up. I will stop reasoning here and write the complete "
    "final answer now, using my best conclusions so far."
)
PROFILE_SAMPLING: Mapping[str, Mapping[str, int | float]] = {
    # Qwen3.8's official thinking recipe.  Keep the complete profile together:
    # mixing an instruct-mode sampler into xhigh reasoning changes quality.
    "reasoning": {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
    },
    # Qwen3.8's separate official non-thinking/instruct recipe.
    "nonreasoning": {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
    },
}
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RECORDED_OUTPUT_CHARS = 4_000
PROTECTED_CATEGORIES = frozenset({"calibration", "long_context"})

SYSTEM_MESSAGE = (
    "You are participating in a deterministic local quality regression. "
    "Solve the self-contained task accurately. Use a provided function only when the task "
    "requires it. Never invent a function result. Put the final answer on its own last line "
    "in exactly this form: FINAL: <answer>. Do not discuss these evaluation instructions."
)


class EvaluationError(RuntimeError):
    """A transport or response-contract failure that invalidates a sample."""


@dataclass(frozen=True, slots=True)
class ToolStep:
    """One expected inert tool call and the canned result returned for it."""

    name: str
    arguments: Mapping[str, Any]
    result: str


@dataclass(frozen=True, slots=True)
class QualityCase:
    """A deterministic quality task with a machine-checkable final answer."""

    id: str
    category: str
    prompt: str
    expected: tuple[str, ...]
    score_kind: str = "exact"
    tolerance: str | None = None
    tools: tuple[Mapping[str, Any], ...] = ()
    tool_steps: tuple[ToolStep, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """The subset of a non-streamed Chat Completion used by the evaluator."""

    content: str
    tool_calls: tuple[Mapping[str, Any], ...]
    finish_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_content: str = ""


def _long_lookup_case() -> QualityCase:
    badge_count = 401
    target_badge_index = 217
    locker_for = lambda index: (index * 149 + 37) % badge_count
    token_for = lambda index: (index * 7_919 + 1_237) % 100_000
    order = tuple((index * 193 + 71) % badge_count for index in range(badge_count))

    badge_lines = [
        f"Badge record B-{index:04d} assigns locker L-{locker_for(index):04d}."
        for index in order
    ]
    locker_lines = [
        f"Locker record L-{index:04d} contains access token T-{token_for(index):05d}."
        for index in reversed(order)
    ]
    target_locker = locker_for(target_badge_index)
    target_token = f"T-{token_for(target_locker):05d}"
    prompt = (
        "The following synthetic archive is complete. First find the locker assigned to badge "
        f"B-{target_badge_index:04d}, then find that locker's access token. Ignore all other "
        "records. Return only the token after the required FINAL marker.\n\n"
        + "\n".join((*badge_lines, *locker_lines))
    )
    return QualityCase(
        id="long_two_hop_archive",
        category="long_context",
        prompt=prompt,
        expected=(target_token,),
    )


def _long_ledger_case() -> QualityCase:
    invoice_count = 307
    target_ids = (41, 173, 289)
    credit_for = lambda index: (index * 37 + 19) % 997 + 10
    order = tuple((index * 101 + 23) % invoice_count for index in range(invoice_count))
    records = [
        f"Invoice I-{index:04d} has an approved value of {credit_for(index)} credits."
        for index in order
    ]
    expected_total = sum(credit_for(index) for index in target_ids)
    prompt = (
        "Project ORCHID references exactly invoices I-0041, I-0173, and I-0289 in the "
        "synthetic ledger below. Add only those three approved values and return the integer "
        "total after the required FINAL marker.\n\n"
        + "\n".join(records)
    )
    return QualityCase(
        id="long_distributed_ledger",
        category="long_context",
        prompt=prompt,
        expected=(str(expected_total),),
    )


EXCHANGE_TOOL: Mapping[str, Any] = {
    "type": "function",
    "function": {
        "name": "lookup_exchange_rate",
        "description": "Return the checked exchange rate for an exact currency pair.",
        "parameters": {
            "type": "object",
            "properties": {
                "base": {"type": "string"},
                "quote": {"type": "string"},
            },
            "required": ["base", "quote"],
            "additionalProperties": False,
        },
    },
}

AVAILABILITY_TOOL: Mapping[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_room_availability",
        "description": "Check whether one room is available on one ISO date.",
        "parameters": {
            "type": "object",
            "properties": {
                "room": {"type": "string"},
                "date": {"type": "string"},
            },
            "required": ["room", "date"],
            "additionalProperties": False,
        },
    },
}


CORPUS: tuple[QualityCase, ...] = (
    QualityCase(
        id="reasoning_crt",
        category="reasoning",
        prompt=(
            "Find the least positive integer n that leaves remainder 2 when divided by 5, "
            "remainder 3 when divided by 7, and remainder 4 when divided by 9. Return only "
            "the integer after the required FINAL marker."
        ),
        expected=("157",),
    ),
    QualityCase(
        id="reasoning_bayes",
        category="reasoning",
        prompt=(
            "A sealed population is 3/10 type A and 7/10 type B. A detector is positive for "
            "2/3 of type A and 1/7 of type B. Given a positive result, what exact fraction is "
            "the probability of type A? Reduce the fraction and put it after FINAL."
        ),
        expected=("2/3",),
    ),
    QualityCase(
        id="reasoning_order",
        category="reasoning",
        prompt=(
            "Four jobs K, L, M, and N occupy positions 1 through 4 exactly once. L is last. "
            "M is immediately after K. N is before K. Which job is in position 2? Return only "
            "its letter after FINAL."
        ),
        expected=("K",),
    ),
    QualityCase(
        id="reasoning_momentum",
        category="reasoning",
        prompt=(
            "On a frictionless line, a 2 kg cart moving right at 3 m/s sticks to a 1 kg cart "
            "moving left at 3 m/s. Take right as positive. Return only the signed final velocity "
            "as a decimal number in m/s after FINAL."
        ),
        expected=("1",),
        score_kind="numeric",
        tolerance="0.000001",
    ),
    QualityCase(
        id="reasoning_experiment",
        category="reasoning",
        prompt=(
            "A school wants to estimate the causal effect of a new tutoring program. Choose "
            "one design: A) compare volunteers with non-volunteers after tutoring; B) randomly "
            "assign consenting students to tutoring or the usual program and compare outcomes; "
            "C) ask tutored students whether they improved; D) compare this year's school with "
            "a different school last year. Return only A, B, C, or D after FINAL."
        ),
        expected=("B",),
    ),
    # Long-thinking cases. Measured 2026-09-02 on the deployed Q5_K_S xhigh
    # model: this one used about 9,000 tokens with seed 424242 and, unseeded
    # under the former 12,288-token ceiling, was still enumerating when cut off
    # with empty content. It exercises the Think ceiling and the forced-answer
    # budget that the shorter cases never reach.
    QualityCase(
        id="reasoning_integer_area_triangles",
        category="reasoning",
        prompt=(
            "Let N be the number of ordered triples (a, b, c) of positive integers with "
            "a <= b <= c and a + b + c = 60 that are the side lengths of a non-degenerate "
            "triangle whose area is an integer. Find N. Put only the integer after FINAL."
        ),
        expected=("3",),
    ),
    # Generated from a random arrangement and verified unique by exhaustive
    # search; the deployed model solved it with seed 424242 in about 10,500
    # tokens of deduction.
    QualityCase(
        id="reasoning_five_house_grid",
        category="reasoning",
        prompt=(
            "Five houses stand in a row, numbered 1 to 5 from left to right. Each house is "
            "painted a different color (red, blue, green, white, yellow). Each house has one "
            "owner (Ada, Bram, Cleo, Dev, Elin); each owner keeps a different pet (cat, dog, "
            "fox, owl, hare) and drinks a different drink (tea, coffee, milk, juice, water). "
            "\"Immediately to the right of\" means the next house number up. \"Somewhere to "
            "the left of\" means a smaller house number, not necessarily adjacent. \"Next "
            "to\" means adjacent on either side. These clues determine a unique arrangement:\n"
            "1. Ada lives somewhere to the left of the red house's owner.\n"
            "2. Cleo lives somewhere to the left of the cat owner.\n"
            "3. The blue house's owner lives next to the cat owner.\n"
            "4. The yellow house's owner lives next to Dev.\n"
            "5. The hare owner lives somewhere to the left of Dev.\n"
            "6. Elin lives somewhere to the left of the tea drinker.\n"
            "7. Elin lives immediately to the right of the water drinker.\n"
            "8. Bram lives immediately to the right of the coffee drinker.\n"
            "9. The tea drinker lives next to the cat owner.\n"
            "10. Bram lives next to the dog owner.\n"
            "11. The green house's owner lives somewhere to the left of Bram.\n"
            "12. The juice drinker lives somewhere to the left of Elin.\n"
            "13. The yellow house's owner lives immediately to the right of the fox owner.\n"
            "14. The red house's owner lives next to the dog owner.\n"
            "15. The blue house's owner lives next to the red house's owner.\n"
            "Which person keeps the owl? Put only the name after FINAL."
        ),
        expected=("Dev",),
    ),
    QualityCase(
        id="coding_trace",
        category="coding",
        prompt=(
            "Without executing code, trace this Python exactly and return the printed integer.\n"
            "values = [3, 1, 4, 1, 5]\n"
            "total = 0\n"
            "for i, value in enumerate(values):\n"
            "    if i % 2 == 0:\n"
            "        total += value\n"
            "    else:\n"
            "        total *= value\n"
            "print(total)\n"
            "Put only the integer after FINAL."
        ),
        expected=("12",),
    ),
    QualityCase(
        id="coding_bug_line",
        category="coding",
        prompt=(
            "The numbered Python binary search can loop forever when the target is above "
            "items[mid]. Which numbered line must change?\n"
            "1 def find(items, target):\n"
            "2     lo, hi = 0, len(items) - 1\n"
            "3     while lo <= hi:\n"
            "4         mid = (lo + hi) // 2\n"
            "5         if items[mid] == target: return mid\n"
            "6         if items[mid] < target:\n"
            "7             lo = mid\n"
            "8         else:\n"
            "9             hi = mid - 1\n"
            "10    return -1\n"
            "Return only the line number after FINAL."
        ),
        expected=("7",),
    ),
    QualityCase(
        id="instruction_transform",
        category="instruction",
        prompt=(
            "Apply these operations in order to [amber, blue, cyan, denim]: rotate the list "
            "left by two positions; reverse the characters inside each word; join the words "
            "with | and no spaces; uppercase the result. Put only that result after FINAL."
        ),
        expected=("NAYC|MINED|REBMA|EULB",),
    ),
    QualityCase(
        id="calibration_missing_attribute",
        category="calibration",
        prompt=(
            "A fictional Helio-9 report states that Director Varo approved 18 blue crates and "
            "12 green crates. It gives no personal information about Varo. What is Varo's "
            "favorite color? If the supplied facts do not determine it, return UNKNOWN. Put "
            "only the answer after FINAL."
        ),
        expected=("UNKNOWN",),
    ),
    QualityCase(
        id="calibration_conflicting_sensors",
        category="calibration",
        prompt=(
            "In a fictional sealed test, sensor P reports 18.0 degrees and sensor Q reports "
            "24.0 degrees. No accuracy, calibration, or tie-breaking information is supplied. "
            "What is the exact true temperature? If it cannot be determined, return UNKNOWN "
            "after FINAL."
        ),
        expected=("UNKNOWN",),
    ),
    QualityCase(
        id="tool_exchange_grounding",
        category="tool_use",
        prompt=(
            "Use lookup_exchange_rate exactly once for base USD and quote JPY. After receiving "
            "the function result, return its rate and nothing else after FINAL."
        ),
        expected=("151.25",),
        tools=(EXCHANGE_TOOL,),
        tool_steps=(
            ToolStep(
                name="lookup_exchange_rate",
                arguments={"base": "USD", "quote": "JPY"},
                result='{"rate":"151.25","as_of":"2040-01-01"}',
            ),
        ),
    ),
    QualityCase(
        id="tool_availability_grounding",
        category="tool_use",
        prompt=(
            "Use check_room_availability exactly once for room Cedar and date 2040-06-12. "
            "After the result, return AVAILABLE if true or UNAVAILABLE if false after FINAL."
        ),
        expected=("UNAVAILABLE",),
        tools=(AVAILABILITY_TOOL,),
        tool_steps=(
            ToolStep(
                name="check_room_availability",
                arguments={"room": "Cedar", "date": "2040-06-12"},
                result='{"available":false}',
            ),
        ),
    ),
    _long_lookup_case(),
    _long_ledger_case(),
)


Requester = Callable[[str, Mapping[str, Any], float], ChatResponse]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def completion_endpoint(base_url: str) -> str:
    """Normalize a base URL and reject every non-loopback destination."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise EvaluationError("--base-url must use http or https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise EvaluationError("--base-url must contain a host and no credentials")
    if parsed.query or parsed.fragment:
        raise EvaluationError("--base-url must not contain a query or fragment")

    hostname = parsed.hostname.rstrip(".").casefold()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise EvaluationError(
            f"refusing non-loopback quality endpoint: {parsed.hostname!r}"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise EvaluationError(f"invalid --base-url port: {exc}") from exc

    path = parsed.path.rstrip("/")
    if path.endswith("/v1/chat/completions"):
        endpoint_path = path
    elif path.endswith("/v1"):
        endpoint_path = f"{path}/chat/completions"
    else:
        endpoint_path = f"{path}/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))


def parse_seeds(value: str) -> tuple[int, ...]:
    """Parse a bounded comma-separated list while preserving input order."""
    seeds: list[int] = []
    for raw in value.split(","):
        try:
            seed = int(raw.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
        if not -(2**31) <= seed < 2**31:
            raise argparse.ArgumentTypeError("each seed must fit a signed 32-bit integer")
        if seed not in seeds:
            seeds.append(seed)
    if not 1 <= len(seeds) <= 8:
        raise argparse.ArgumentTypeError("provide between one and eight unique seeds")
    return tuple(seeds)


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(timeout) or not 0 < timeout <= 3_600:
        raise argparse.ArgumentTypeError("timeout must be greater than zero and at most 3600")
    return timeout


def _bounded_max_tokens(value: str) -> int:
    try:
        tokens = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max tokens must be an integer") from exc
    if not 256 <= tokens <= 32_768:
        raise argparse.ArgumentTypeError("max tokens must be between 256 and 32768")
    return tokens


def reasoning_budget_tokens(max_tokens: int) -> int:
    """The app's rule: reserve the Fast-mode answer allowance for the answer."""
    return max(
        1,
        min(max(256, max_tokens - REASONING_ANSWER_RESERVE_TOKENS), max_tokens - 256),
    )


def quality_request(
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    profile: str,
    seed: int,
    max_tokens: int,
    tools: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build one fully pinned request without exposing arbitrary server controls."""
    sampling = PROFILE_SAMPLING.get(profile)
    if sampling is None:
        raise EvaluationError(f"unknown quality profile: {profile!r}")
    body: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": False,
        "max_tokens": max_tokens,
        "seed": seed,
        "cache_prompt": False,
        "parse_tool_calls": True,
        **sampling,
    }
    if profile == "reasoning":
        body["reasoning_effort"] = "xhigh"
        body["chat_template_kwargs"] = {
            "enable_thinking": True,
            "preserve_thinking": True,
        }
        body["reasoning_budget_tokens"] = reasoning_budget_tokens(max_tokens)
        body["reasoning_budget_message"] = REASONING_BUDGET_MESSAGE
    elif profile == "nonreasoning":
        body["reasoning_effort"] = "none"
        body["chat_template_kwargs"] = {
            "enable_thinking": False,
            "preserve_thinking": False,
        }
    else:  # Defensive: the profile lookup above is the authoritative validation.
        raise EvaluationError(f"unknown quality profile: {profile!r}")

    if tools:
        body["tools"] = list(tools)
        body["tool_choice"] = "auto"
    else:
        body["backend_sampling"] = True
    return body


def _optional_token_count(usage: Mapping[str, Any], key: str) -> int | None:
    value = usage.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvaluationError(f"usage.{key} must be a nonnegative integer")
    return value


def _parse_chat_response(payload: Any) -> ChatResponse:
    if not isinstance(payload, Mapping):
        raise EvaluationError("Chat Completion response must be a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise EvaluationError("Chat Completion response must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise EvaluationError("Chat Completion choice must be an object")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise EvaluationError("Chat Completion choice is missing its message")

    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise EvaluationError("assistant content must be text or null")
    reasoning_content = message.get("reasoning_content", message.get("reasoning", ""))
    if reasoning_content is None:
        reasoning_content = ""
    if not isinstance(reasoning_content, str):
        raise EvaluationError("assistant reasoning content must be text or null")
    raw_calls = message.get("tool_calls", [])
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list) or not all(
        isinstance(call, Mapping) for call in raw_calls
    ):
        raise EvaluationError("assistant tool_calls must be a list of objects")
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason:
        raise EvaluationError("Chat Completion choice is missing finish_reason")
    usage = payload.get("usage", {})
    if usage is None:
        usage = {}
    if not isinstance(usage, Mapping):
        raise EvaluationError("usage must be an object when present")
    return ChatResponse(
        content=content,
        tool_calls=tuple(raw_calls),
        finish_reason=finish_reason,
        prompt_tokens=_optional_token_count(usage, "prompt_tokens"),
        completion_tokens=_optional_token_count(usage, "completion_tokens"),
        reasoning_content=reasoning_content,
    )


def request_chat(
    endpoint: str, body: Mapping[str, Any], timeout: float
) -> ChatResponse:
    """Send one bounded request, bypassing proxies and refusing redirects."""
    encoded = json.dumps(
        body, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=encoded,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"KevinBeLLM-quality/{EVALUATOR_VERSION}",
        },
    )
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise EvaluationError(
                    f"expected application/json, got {content_type!r}"
                )
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise EvaluationError(
                    f"Chat Completion response exceeded {MAX_RESPONSE_BYTES} bytes"
                )
    except HTTPError as exc:
        preview = exc.read(65_536).decode("utf-8", errors="replace")
        raise EvaluationError(
            f"HTTP {exc.code} from llama.cpp: {preview[:2000]}"
        ) from exc
    except URLError as exc:
        raise EvaluationError(
            f"could not reach llama.cpp at {endpoint}: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise EvaluationError(
            f"quality request exceeded the {timeout:g}s timeout"
        ) from exc
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError("llama.cpp returned invalid JSON") from exc
    return _parse_chat_response(payload)


_FINAL_RE = re.compile(r"(?mi)^[ \t]*FINAL:[ \t]*(.*?)[ \t]*$")


def extract_final_answer(content: str) -> str | None:
    """Return the last explicit one-line FINAL answer, or None."""
    matches = _FINAL_RE.findall(content)
    if not matches:
        return None
    answer = matches[-1].strip()
    return answer or None


def _normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.strip().split()).casefold()


def score_answer(case: QualityCase, answer: str | None) -> bool:
    """Apply the case's deterministic exact or decimal-tolerance scorer."""
    if answer is None:
        return False
    if case.score_kind == "exact":
        normalized = _normalize_answer(answer)
        return normalized in {_normalize_answer(item) for item in case.expected}
    if case.score_kind == "numeric":
        try:
            actual = Decimal(answer.replace(",", "").strip())
            tolerance = Decimal(case.tolerance or "0")
            expected = tuple(Decimal(item) for item in case.expected)
        except (InvalidOperation, ValueError):
            return False
        return any(abs(actual - item) <= tolerance for item in expected)
    raise EvaluationError(f"unknown score kind in {case.id}: {case.score_kind!r}")


def _tool_call(call: Mapping[str, Any]) -> tuple[str, dict[str, Any], str | None]:
    function = call.get("function")
    if not isinstance(function, Mapping):
        raise EvaluationError("tool call is missing its function object")
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise EvaluationError("tool call function name must be nonempty text")
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise EvaluationError("tool call arguments are invalid JSON") from exc
    if not isinstance(arguments, dict):
        raise EvaluationError("tool call arguments must be an object")
    raw_id = call.get("id")
    call_id = raw_id if isinstance(raw_id, str) and raw_id else None
    return name, arguments, call_id


def _add_tokens(total: int | None, value: int | None) -> int | None:
    if total is None or value is None:
        return None
    return total + value


def _case_result(
    *,
    case: QualityCase,
    seed: int,
    status: str,
    passed: bool,
    content: str,
    answer: str | None,
    finish_reason: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    elapsed_seconds: float,
    tool_trace: Sequence[Mapping[str, Any]],
    error: str | None,
    reasoning_budget_hit: bool = False,
) -> dict[str, Any]:
    output_bytes = content.encode("utf-8")
    return {
        "case_id": case.id,
        "category": case.category,
        "seed": seed,
        "status": status,
        "passed": passed,
        "expected": list(case.expected),
        "answer": answer,
        "finish_reason": finish_reason,
        # True when llama.cpp had to inject the budget message: the case needed
        # more thinking than the budget allows, so its answer is best-effort.
        "reasoning_budget_hit": reasoning_budget_hit,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output": content[:MAX_RECORDED_OUTPUT_CHARS],
        "output_truncated_in_report": len(content) > MAX_RECORDED_OUTPUT_CHARS,
        "tool_trace": list(tool_trace),
        "error": error,
    }


def run_case(
    *,
    endpoint: str,
    model: str,
    profile: str,
    seed: int,
    max_tokens: int,
    timeout: float,
    case: QualityCase,
    requester: Requester = request_chat,
) -> dict[str, Any]:
    """Run one case. Expected tools are compared, never executed."""
    started_at = time.perf_counter()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": case.prompt},
    ]
    prompt_tokens: int | None = 0
    completion_tokens: int | None = 0
    tool_trace: list[dict[str, Any]] = []
    last_content = ""
    finish_reason: str | None = None

    try:
        for step_index, expected_step in enumerate(case.tool_steps):
            response = requester(
                endpoint,
                quality_request(
                    model=model,
                    messages=messages,
                    profile=profile,
                    seed=seed,
                    max_tokens=max_tokens,
                    tools=case.tools,
                ),
                timeout,
            )
            last_content = response.content
            finish_reason = response.finish_reason
            prompt_tokens = _add_tokens(prompt_tokens, response.prompt_tokens)
            completion_tokens = _add_tokens(
                completion_tokens, response.completion_tokens
            )
            if len(response.tool_calls) != 1:
                raise EvaluationError(
                    f"expected one tool call at step {step_index + 1}, got "
                    f"{len(response.tool_calls)}"
                )
            name, arguments, call_id = _tool_call(response.tool_calls[0])
            matched = name == expected_step.name and arguments == dict(
                expected_step.arguments
            )
            tool_trace.append(
                {
                    "step": step_index + 1,
                    "expected_name": expected_step.name,
                    "received_name": name,
                    "expected_arguments": dict(expected_step.arguments),
                    "received_arguments": arguments,
                    "matched": matched,
                }
            )
            if not matched:
                return _case_result(
                    case=case,
                    seed=seed,
                    status="protocol_error",
                    passed=False,
                    content=last_content,
                    answer=None,
                    finish_reason=finish_reason,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    elapsed_seconds=time.perf_counter() - started_at,
                    tool_trace=tool_trace,
                    error=f"tool call did not match step {step_index + 1}",
                )

            stable_id = call_id or f"quality_call_{step_index + 1}"
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": stable_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                ],
            }
            # Match the production xhigh tool loop: preserve the trace in memory
            # for the next round, but never include it in the JSON report.
            if profile == "reasoning" and response.reasoning_content:
                assistant_message["reasoning_content"] = response.reasoning_content
            messages.append(assistant_message)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": stable_id,
                    "content": expected_step.result,
                }
            )

        response = requester(
            endpoint,
            quality_request(
                model=model,
                messages=messages,
                profile=profile,
                seed=seed,
                max_tokens=max_tokens,
                tools=case.tools,
            ),
            timeout,
        )
        last_content = response.content
        finish_reason = response.finish_reason
        prompt_tokens = _add_tokens(prompt_tokens, response.prompt_tokens)
        completion_tokens = _add_tokens(completion_tokens, response.completion_tokens)
        if response.tool_calls:
            return _case_result(
                case=case,
                seed=seed,
                status="protocol_error",
                passed=False,
                content=last_content,
                answer=None,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_seconds=time.perf_counter() - started_at,
                tool_trace=tool_trace,
                error="model emitted an unexpected final tool call",
            )
        answer = extract_final_answer(last_content)
        budget_hit = (
            profile == "reasoning"
            and REASONING_BUDGET_MESSAGE in response.reasoning_content
        )
        if finish_reason == "length":
            return _case_result(
                case=case,
                seed=seed,
                status="protocol_error",
                passed=False,
                content=last_content,
                answer=answer,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_seconds=time.perf_counter() - started_at,
                tool_trace=tool_trace,
                error="output exhausted the token budget",
                reasoning_budget_hit=budget_hit,
            )
        passed = score_answer(case, answer)
        return _case_result(
            case=case,
            seed=seed,
            status="pass" if passed else "wrong_answer",
            passed=passed,
            content=last_content,
            answer=answer,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_seconds=time.perf_counter() - started_at,
            tool_trace=tool_trace,
            error=None if passed else "missing or incorrect FINAL answer",
            reasoning_budget_hit=budget_hit,
        )
    except EvaluationError as exc:
        return _case_result(
            case=case,
            seed=seed,
            status="request_error",
            passed=False,
            content=last_content,
            answer=None,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_seconds=time.perf_counter() - started_at,
            tool_trace=tool_trace,
            error=str(exc),
        )


def corpus_sha256(cases: Sequence[QualityCase]) -> str:
    canonical = json.dumps(
        [asdict(case) for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def summarize(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(result.get("passed") is True for result in results)
    categories: dict[str, dict[str, Any]] = {}
    for result in results:
        category = result.get("category")
        if not isinstance(category, str):
            raise EvaluationError("result category must be text")
        summary = categories.setdefault(category, {"passed": 0, "total": 0})
        summary["total"] += 1
        summary["passed"] += result.get("passed") is True
    for summary in categories.values():
        summary["score"] = round(100 * summary["passed"] / summary["total"], 2)
    return {
        "passed": passed,
        "total": total,
        "score": round(100 * passed / total, 2) if total else 0.0,
        "request_errors": sum(
            result.get("status") == "request_error" for result in results
        ),
        "protocol_errors": sum(
            result.get("status") == "protocol_error" for result in results
        ),
        "categories": dict(sorted(categories.items())),
    }


def build_report(
    *,
    endpoint: str,
    model: str,
    profile: str,
    seeds: Sequence[int],
    max_tokens: int,
    timeout: float,
    cases: Sequence[QualityCase],
    requester: Requester = request_chat,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    total = len(cases) * len(seeds)
    completed = 0
    for seed in seeds:
        for case in cases:
            result = run_case(
                endpoint=endpoint,
                model=model,
                profile=profile,
                seed=seed,
                max_tokens=max_tokens,
                timeout=timeout,
                case=case,
                requester=requester,
            )
            results.append(result)
            completed += 1
            if progress is not None:
                progress(
                    f"[{completed}/{total}] {result['status'].upper()} "
                    f"{case.id} seed={seed}"
                )
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "model": model,
        "profile": profile,
        "corpus_sha256": corpus_sha256(CORPUS),
        "selected_corpus_sha256": corpus_sha256(cases),
        "selected_case_ids": [case.id for case in cases],
        "config": {
            "seeds": list(seeds),
            "max_tokens": max_tokens,
            "timeout_seconds": timeout,
            "sampling": dict(PROFILE_SAMPLING[profile]),
            "reasoning_effort": "xhigh" if profile == "reasoning" else "none",
            "chat_template_kwargs": {
                "enable_thinking": profile == "reasoning",
                "preserve_thinking": profile == "reasoning",
            },
            "reasoning_budget_tokens": (
                reasoning_budget_tokens(max_tokens) if profile == "reasoning" else None
            ),
            "reasoning_budget_message": (
                REASONING_BUDGET_MESSAGE if profile == "reasoning" else None
            ),
            "cache_prompt": False,
            "sequential": True,
        },
        "results": results,
        "summary": summarize(results),
    }


def _result_map(report: Mapping[str, Any]) -> dict[tuple[str, int], Mapping[str, Any]]:
    raw_results = report.get("results")
    if not isinstance(raw_results, list):
        raise EvaluationError("comparison report is missing results")
    mapped: dict[tuple[str, int], Mapping[str, Any]] = {}
    for result in raw_results:
        if not isinstance(result, Mapping):
            raise EvaluationError("comparison result must be an object")
        case_id = result.get("case_id")
        seed = result.get("seed")
        if not isinstance(case_id, str) or not isinstance(seed, int) or isinstance(seed, bool):
            raise EvaluationError("comparison result has an invalid case_id or seed")
        key = (case_id, seed)
        if key in mapped:
            raise EvaluationError(f"comparison report repeats {case_id} seed={seed}")
        mapped[key] = result
    return mapped


def compare_reports(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a paired exact-case comparison and protected-category gate."""
    if baseline.get("evaluator_version") != candidate.get("evaluator_version"):
        raise EvaluationError("baseline and candidate use different evaluator versions")
    if baseline.get("selected_corpus_sha256") != candidate.get(
        "selected_corpus_sha256"
    ):
        raise EvaluationError("baseline and candidate use different selected corpora")
    baseline_map = _result_map(baseline)
    candidate_map = _result_map(candidate)
    if baseline_map.keys() != candidate_map.keys():
        raise EvaluationError("baseline and candidate do not contain the same cases and seeds")

    gains: list[str] = []
    losses: list[str] = []
    unchanged_passes = 0
    unchanged_failures = 0
    for key in sorted(baseline_map):
        baseline_pass = baseline_map[key].get("passed") is True
        candidate_pass = candidate_map[key].get("passed") is True
        label = f"{key[0]}@{key[1]}"
        if candidate_pass and not baseline_pass:
            gains.append(label)
        elif baseline_pass and not candidate_pass:
            losses.append(label)
        elif candidate_pass:
            unchanged_passes += 1
        else:
            unchanged_failures += 1

    baseline_summary = summarize(list(baseline_map.values()))
    candidate_summary = summarize(list(candidate_map.values()))
    if baseline_summary["request_errors"] or candidate_summary["request_errors"]:
        raise EvaluationError("cannot compare a report containing request errors")
    category_deltas: dict[str, int] = {}
    all_categories = set(baseline_summary["categories"]) | set(
        candidate_summary["categories"]
    )
    for category in sorted(all_categories):
        baseline_passed = baseline_summary["categories"].get(category, {}).get(
            "passed", 0
        )
        candidate_passed = candidate_summary["categories"].get(category, {}).get(
            "passed", 0
        )
        category_deltas[category] = candidate_passed - baseline_passed

    protected_regressions = [
        category
        for category in sorted(PROTECTED_CATEGORIES)
        if category_deltas.get(category, 0) < 0
    ]
    delta_passed = candidate_summary["passed"] - baseline_summary["passed"]
    regressed = delta_passed < 0 or bool(protected_regressions)

    changed_config: dict[str, dict[str, Any]] = {}
    for key in ("endpoint", "profile", "model", "config"):
        before = baseline.get(key)
        after = candidate.get(key)
        if before != after:
            changed_config[key] = {"baseline": before, "candidate": after}
    return {
        "baseline_score": baseline_summary["score"],
        "candidate_score": candidate_summary["score"],
        "delta_passed": delta_passed,
        "gains": gains,
        "losses": losses,
        "unchanged_passes": unchanged_passes,
        "unchanged_failures": unchanged_failures,
        "category_pass_deltas": category_deltas,
        "protected_category_regressions": protected_regressions,
        "regressed": regressed,
        "meaningful_improvement": delta_passed >= 2 and not regressed,
        "changed_config": changed_config,
    }


def load_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except OSError as exc:
        raise EvaluationError(f"could not read comparison report: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"comparison report is invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise EvaluationError("comparison report must contain a JSON object")
    return payload


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Run deterministic exact-answer quality checks against a loopback llama.cpp "
            "Chat Completions endpoint."
        )
    )
    result.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
        help="loopback llama.cpp base URL (default: http://127.0.0.1:8080)",
    )
    result.add_argument("--model", default="kevinbellm-27b", help="model alias")
    result.add_argument(
        "--profile",
        choices=("reasoning", "nonreasoning"),
        default="reasoning",
        help="request profile to score (default: reasoning)",
    )
    result.add_argument(
        "--seeds",
        type=parse_seeds,
        default=(DEFAULT_SEED,),
        help="one to eight comma-separated signed 32-bit seeds",
    )
    result.add_argument(
        "--max-tokens",
        type=_bounded_max_tokens,
        help="override the profile output-token ceiling",
    )
    result.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="per-request timeout in seconds (default: 600)",
    )
    result.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="run only this case ID; may be repeated",
    )
    result.add_argument(
        "--list-cases", action="store_true", help="list case IDs and exit"
    )
    result.add_argument(
        "--compare", type=Path, help="baseline JSON report to compare with this run"
    )
    result.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 2 when comparison reports a total or protected-category regression",
    )
    result.add_argument(
        "--json", action="store_true", dest="json_output", help="emit JSON"
    )
    return result


def _select_cases(case_ids: Sequence[str] | None) -> tuple[QualityCase, ...]:
    if not case_ids:
        return CORPUS
    by_id = {case.id: case for case in CORPUS}
    unknown = sorted(set(case_ids) - by_id.keys())
    if unknown:
        raise EvaluationError(f"unknown case ID(s): {', '.join(unknown)}")
    selected: list[QualityCase] = []
    for case_id in case_ids:
        case = by_id[case_id]
        if case not in selected:
            selected.append(case)
    return tuple(selected)


def _print_human(report: Mapping[str, Any]) -> None:
    summary = report["summary"]
    print(
        f"Local Quality Score: {summary['passed']}/{summary['total']} "
        f"({summary['score']:.2f})"
    )
    for category, category_summary in summary["categories"].items():
        print(
            f"  {category}: {category_summary['passed']}/{category_summary['total']} "
            f"({category_summary['score']:.2f})"
        )
    comparison = report.get("comparison")
    if isinstance(comparison, Mapping):
        print(
            f"Comparison: delta={comparison['delta_passed']:+d}, "
            f"gains={len(comparison['gains'])}, losses={len(comparison['losses'])}, "
            f"regressed={str(comparison['regressed']).lower()}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.list_cases:
        for case in CORPUS:
            print(f"{case.id}\t{case.category}")
        return 0
    if not args.model or len(args.model) > 200:
        raise EvaluationError("--model must contain between 1 and 200 characters")

    endpoint = completion_endpoint(args.base_url)
    cases = _select_cases(args.case_ids)
    max_tokens = args.max_tokens or PROFILE_MAX_TOKENS[args.profile]
    progress_stream = sys.stderr if args.json_output else sys.stdout
    print(
        f"Quality endpoint={endpoint} model={args.model} profile={args.profile} "
        f"cases={len(cases)} seeds={','.join(str(seed) for seed in args.seeds)} "
        f"max_tokens={max_tokens}",
        file=progress_stream,
    )
    report = build_report(
        endpoint=endpoint,
        model=args.model,
        profile=args.profile,
        seeds=args.seeds,
        max_tokens=max_tokens,
        timeout=args.timeout,
        cases=cases,
        requester=request_chat,
        progress=lambda message: print(message, file=progress_stream),
    )
    if args.compare:
        report["comparison"] = compare_reports(load_report(args.compare), report)

    if args.json_output:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        _print_human(report)

    if report["summary"]["request_errors"]:
        return 1
    comparison = report.get("comparison")
    if (
        args.fail_on_regression
        and isinstance(comparison, Mapping)
        and comparison.get("regressed") is True
    ):
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvaluationError as exc:
        print(f"quality evaluation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("quality evaluation interrupted", file=sys.stderr)
        raise SystemExit(130)
