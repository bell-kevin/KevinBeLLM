# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EVALUATOR_PATH = REPOSITORY_ROOT / "scripts" / "cluster" / "evaluate-quality.py"
SPEC = importlib.util.spec_from_file_location("quality_evaluator_under_test", EVALUATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
quality = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = quality
SPEC.loader.exec_module(quality)


def _case_by_id(case_id: str):
    return next(case for case in quality.CORPUS if case.id == case_id)


def test_corpus_is_compact_unique_and_covers_the_quality_risks() -> None:
    assert len(quality.CORPUS) == 16
    assert len({case.id for case in quality.CORPUS}) == len(quality.CORPUS)
    assert {case.category for case in quality.CORPUS} == {
        "calibration",
        "coding",
        "instruction",
        "long_context",
        "reasoning",
        "tool_use",
    }
    assert min(
        len(case.prompt)
        for case in quality.CORPUS
        if case.category == "long_context"
    ) > 10_000
    digest = quality.corpus_sha256(quality.CORPUS)
    assert len(digest) == 64
    assert digest == quality.corpus_sha256(tuple(quality.CORPUS))


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080/v1/chat/completions"),
        ("http://localhost:18080/v1", "http://localhost:18080/v1/chat/completions"),
        (
            "http://[::1]:8080/v1/chat/completions",
            "http://[::1]:8080/v1/chat/completions",
        ),
    ],
)
def test_completion_endpoint_accepts_only_normalized_loopback(
    base_url: str, expected: str
) -> None:
    assert quality.completion_endpoint(base_url) == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.test/v1",
        "http://192.0.2.10:8080",
        "http://user:password@127.0.0.1:8080",
        "file:///tmp/socket",
        "http://127.0.0.1:8080?redirect=true",
    ],
)
def test_completion_endpoint_rejects_nonlocal_or_ambiguous_urls(base_url: str) -> None:
    with pytest.raises(quality.EvaluationError):
        quality.completion_endpoint(base_url)


def test_request_profiles_pin_the_official_qwen_sampling_and_thinking_modes() -> None:
    messages = [{"role": "user", "content": "test"}]
    reasoning = quality.quality_request(
        model="model",
        messages=messages,
        profile="reasoning",
        seed=17,
        max_tokens=12_288,
    )
    assert {
        key: reasoning[key]
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "repeat_penalty",
        )
    } == {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
    }
    assert reasoning["reasoning_effort"] == "xhigh"
    assert reasoning["chat_template_kwargs"] == {
        "enable_thinking": True,
        "preserve_thinking": True,
    }
    # The gate uses the app's forced-answer budget rule and its exact message.
    assert reasoning["reasoning_budget_tokens"] == 12_288 - 4_096
    assert reasoning["reasoning_budget_message"] == quality.REASONING_BUDGET_MESSAGE
    assert reasoning["backend_sampling"] is True
    assert reasoning["cache_prompt"] is False
    assert quality.PROFILE_MAX_TOKENS == {"reasoning": 28_672, "nonreasoning": 4_096}

    nonreasoning = quality.quality_request(
        model="model",
        messages=messages,
        profile="nonreasoning",
        seed=17,
        max_tokens=4_096,
    )
    assert {
        key: nonreasoning[key]
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "presence_penalty",
            "repeat_penalty",
        )
    } == {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
    }
    assert nonreasoning["reasoning_effort"] == "none"
    assert nonreasoning["chat_template_kwargs"] == {
        "enable_thinking": False,
        "preserve_thinking": False,
    }
    assert "reasoning_budget_tokens" not in nonreasoning
    assert "reasoning_budget_message" not in nonreasoning

    with_tools = quality.quality_request(
        model="model",
        messages=messages,
        profile="reasoning",
        seed=17,
        max_tokens=12_288,
        tools=_case_by_id("tool_exchange_grounding").tools,
    )
    assert "backend_sampling" not in with_tools
    assert with_tools["tool_choice"] == "auto"


def test_final_marker_and_exact_or_numeric_scorers_are_deterministic() -> None:
    assert quality.extract_final_answer("work\nFINAL: First\nFINAL:  SECOND  \n") == "SECOND"
    assert quality.extract_final_answer("The answer is SECOND") is None

    exact = quality.QualityCase(
        id="exact", category="reasoning", prompt="x", expected=("Café au lait",)
    )
    assert quality.score_answer(exact, "  CAFÉ   AU LAIT ") is True
    assert quality.score_answer(exact, "Café") is False

    numeric = quality.QualityCase(
        id="numeric",
        category="reasoning",
        prompt="x",
        expected=("1",),
        score_kind="numeric",
        tolerance="0.000001",
    )
    assert quality.score_answer(numeric, "1.0000004") is True
    assert quality.score_answer(numeric, "1.01") is False


def test_standard_library_transport_posts_to_a_loopback_json_endpoint() -> None:
    received = {}
    response_body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "FINAL: ok",
                        "reasoning_content": "private trace",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            received["path"] = self.path
            received["body"] = json.loads(self.rfile.read(length))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, _format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        response = quality.request_chat(endpoint, {"model": "test"}, 2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert received == {
        "path": "/v1/chat/completions",
        "body": {"model": "test"},
    }
    assert response == quality.ChatResponse(
        content="FINAL: ok",
        tool_calls=(),
        finish_reason="stop",
        prompt_tokens=5,
        completion_tokens=2,
        reasoning_content="private trace",
    )


def test_run_case_scores_visible_answer_and_never_records_reasoning() -> None:
    case = quality.QualityCase(
        id="answer", category="reasoning", prompt="Compute it", expected=("42",)
    )
    captured = []

    def requester(endpoint, body, timeout):
        captured.append((endpoint, body, timeout))
        return quality.ChatResponse(
            content="A concise result.\nFINAL: 42",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=20,
            completion_tokens=4,
        )

    result = quality.run_case(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="model",
        profile="reasoning",
        seed=99,
        max_tokens=12_288,
        timeout=30,
        case=case,
        requester=requester,
    )
    assert result["passed"] is True
    assert result["answer"] == "42"
    assert result["prompt_tokens"] == 20
    assert result["completion_tokens"] == 4
    assert result["reasoning_budget_hit"] is False
    assert len(captured) == 1
    assert captured[0][1]["reasoning_effort"] == "xhigh"
    assert "reasoning_content" not in result


def test_run_case_flags_a_forced_answer_without_recording_the_reasoning() -> None:
    case = quality.QualityCase(
        id="forced", category="reasoning", prompt="Count it", expected=("3",)
    )

    def requester(_endpoint, _body, _timeout):
        return quality.ChatResponse(
            content="Best effort.\nFINAL: 3",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=30,
            completion_tokens=16_400,
            reasoning_content="enumerating... " + quality.REASONING_BUDGET_MESSAGE,
        )

    result = quality.run_case(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="model",
        profile="reasoning",
        seed=7,
        max_tokens=20_480,
        timeout=30,
        case=case,
        requester=requester,
    )
    assert result["passed"] is True
    assert result["reasoning_budget_hit"] is True
    assert "reasoning_content" not in result
    assert "enumerating" not in json.dumps(result)


def test_reasoning_budget_rule_and_message_match_the_deployed_app() -> None:
    from app.config import DEFAULT_REASONING_BUDGET_MESSAGE

    assert quality.REASONING_BUDGET_MESSAGE == DEFAULT_REASONING_BUDGET_MESSAGE
    assert quality.reasoning_budget_tokens(28_672) == 24_576
    assert quality.reasoning_budget_tokens(20_480) == 16_384
    assert quality.reasoning_budget_tokens(12_288) == 8_192
    assert quality.reasoning_budget_tokens(1_024) == 256
    assert quality.reasoning_budget_tokens(256) == 1


def test_tool_case_compares_call_and_only_injects_canned_result() -> None:
    tool = {
        "type": "function",
        "function": {
            "name": "lookup",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    }
    case = quality.QualityCase(
        id="tool",
        category="tool_use",
        prompt="Look it up",
        expected=("safe-value",),
        tools=(tool,),
        tool_steps=(
            quality.ToolStep(
                name="lookup", arguments={"key": "alpha"}, result='{"value":"safe-value"}'
            ),
        ),
    )
    bodies = []

    def requester(_endpoint, body, _timeout):
        bodies.append(body)
        if len(bodies) == 1:
            return quality.ChatResponse(
                content="",
                tool_calls=(
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "arguments": '{"key":"alpha"}',
                        },
                    },
                ),
                finish_reason="tool_calls",
                prompt_tokens=10,
                completion_tokens=2,
                reasoning_content="I should look up the checked value.",
            )
        assert body["messages"][-2]["reasoning_content"] == (
            "I should look up the checked value."
        )
        assert body["messages"][-1] == {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": '{"value":"safe-value"}',
        }
        return quality.ChatResponse(
            content="FINAL: safe-value",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=12,
            completion_tokens=3,
        )

    result = quality.run_case(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="model",
        profile="reasoning",
        seed=1,
        max_tokens=12_288,
        timeout=30,
        case=case,
        requester=requester,
    )
    assert result["passed"] is True
    assert result["prompt_tokens"] == 22
    assert result["completion_tokens"] == 5
    assert result["tool_trace"][0]["matched"] is True
    assert len(bodies) == 2
    assert all("backend_sampling" not in body for body in bodies)


def test_wrong_tool_call_fails_without_injecting_or_executing_anything() -> None:
    case = _case_by_id("tool_exchange_grounding")
    requests = 0

    def requester(_endpoint, _body, _timeout):
        nonlocal requests
        requests += 1
        return quality.ChatResponse(
            content="",
            tool_calls=(
                {
                    "function": {
                        "name": "different_tool",
                        "arguments": '{"base":"USD","quote":"JPY"}',
                    }
                },
            ),
            finish_reason="tool_calls",
            prompt_tokens=1,
            completion_tokens=1,
        )

    result = quality.run_case(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="model",
        profile="reasoning",
        seed=1,
        max_tokens=12_288,
        timeout=30,
        case=case,
        requester=requester,
    )
    assert result["passed"] is False
    assert result["status"] == "protocol_error"
    assert requests == 1


def _comparison_result(case_id: str, category: str, passed: bool) -> dict:
    return {
        "case_id": case_id,
        "category": category,
        "seed": 1,
        "passed": passed,
        "status": "pass" if passed else "wrong_answer",
    }


def test_compare_mode_is_paired_and_protects_calibration_and_long_context() -> None:
    baseline = {
        "selected_corpus_sha256": "same",
        "profile": "reasoning",
        "model": "baseline",
        "config": {"seeds": [1]},
        "results": [
            _comparison_result("cal", "calibration", True),
            _comparison_result("long", "long_context", True),
            _comparison_result("reason", "reasoning", False),
        ],
    }
    candidate = {
        "selected_corpus_sha256": "same",
        "profile": "reasoning",
        "model": "candidate",
        "config": {"seeds": [1]},
        "results": [
            _comparison_result("cal", "calibration", True),
            _comparison_result("long", "long_context", False),
            _comparison_result("reason", "reasoning", True),
        ],
    }
    comparison = quality.compare_reports(baseline, candidate)
    assert comparison["delta_passed"] == 0
    assert comparison["gains"] == ["reason@1"]
    assert comparison["losses"] == ["long@1"]
    assert comparison["protected_category_regressions"] == ["long_context"]
    assert comparison["regressed"] is True
    assert comparison["meaningful_improvement"] is False


def test_main_json_output_is_machine_readable(monkeypatch, capsys) -> None:
    case = quality.QualityCase(
        id="smoke", category="reasoning", prompt="answer", expected=("ok",)
    )

    def requester(_endpoint, _body, _timeout):
        return quality.ChatResponse(
            content="FINAL: ok",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=5,
            completion_tokens=2,
        )

    monkeypatch.setattr(quality, "CORPUS", (case,))
    monkeypatch.setattr(quality, "request_chat", requester)
    assert quality.main(["--json", "--case", "smoke"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["summary"] == {
        "categories": {"reasoning": {"passed": 1, "score": 100.0, "total": 1}},
        "passed": 1,
        "protocol_errors": 0,
        "request_errors": 0,
        "score": 100.0,
        "total": 1,
    }
    assert "Quality endpoint=" in captured.err
    assert "[1/1] PASS smoke seed=424242" in captured.err


def test_main_compare_mode_reports_and_can_fail_the_regression_gate(
    monkeypatch, tmp_path, capsys
) -> None:
    case = quality.QualityCase(
        id="smoke", category="reasoning", prompt="answer", expected=("ok",)
    )

    def good_requester(_endpoint, _body, _timeout):
        return quality.ChatResponse(
            content="FINAL: ok",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=5,
            completion_tokens=2,
        )

    endpoint = quality.completion_endpoint("http://127.0.0.1:8080")
    monkeypatch.setattr(quality, "CORPUS", (case,))
    baseline = quality.build_report(
        endpoint=endpoint,
        model="model",
        profile="reasoning",
        seeds=(quality.DEFAULT_SEED,),
        max_tokens=12_288,
        timeout=30,
        cases=(case,),
        requester=good_requester,
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    def wrong_requester(_endpoint, _body, _timeout):
        return quality.ChatResponse(
            content="FINAL: wrong",
            tool_calls=(),
            finish_reason="stop",
            prompt_tokens=5,
            completion_tokens=2,
        )

    monkeypatch.setattr(quality, "request_chat", wrong_requester)
    assert quality.main(
        [
            "--json",
            "--case",
            "smoke",
            "--compare",
            str(baseline_path),
            "--fail-on-regression",
        ]
    ) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["comparison"]["delta_passed"] == -1
    assert report["comparison"]["losses"] == ["smoke@424242"]
    assert report["comparison"]["regressed"] is True
