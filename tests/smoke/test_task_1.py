"""Smoke test for Task 1: the agent loop and the tool contract (TODO 1.1-1.3).

    python -m pytest tests/smoke/test_task_1.py

The loop needs a model and a tool registry. The model is a script, from tests/fakes.py: it
replays fixed responses, so there is no key and no network. The registry is a stand-in
with one tool, because the real one's call() is TODO 5.6.

Passing means the main behaviour of each TODO works, not that every case is handled.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

import pytest

from onboarding_agent.loop import (
    AgentLoop,
    BudgetExhausted,
    RetryCapExceeded,
    RunResult,
    TurnCapExceeded,
)
from onboarding_agent.tools.base import ToolError, truncate
from tests.fakes import ScriptedModel, scripted

TIMEOUT = 5.0

LOOKUP = {"tool": "lookup", "args": {"word": "harness"}}
FAILING_LOOKUP = {"tool": "lookup", "args": {"word": "missing"}}


class OneToolRegistry:
    """Stands in for ToolRegistry. Its one tool, ``lookup``, fails for the word "missing"."""

    def names(self) -> list[str]:
        return ["lookup"]

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "lookup",
                "description": "Look a word up.",
                "parameters": {
                    "type": "object",
                    "properties": {"word": {"type": "string"}},
                    "required": ["word"],
                },
            }
        ]

    def call(self, name: str, args: dict[str, Any]) -> str:
        if args.get("word") == "missing":
            raise ToolError("no entry for 'missing'", hint="try another word")
        return f"{args['word']}: found"


def a_loop(turns: list[dict[str, Any]], **settings: Any) -> AgentLoop:
    """A loop whose model replays ``turns``. Each turn costs 100 input and 20 output tokens."""
    return AgentLoop(ScriptedModel(scripted(turns)), OneToolRegistry(), **settings)  # type: ignore[arg-type]


def run(loop: AgentLoop) -> RunResult:
    """``loop.run()``, failed after a few seconds instead of left to hang."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = loop.run("Look a word up.")
        except BaseException as exc:  # re-raised below, in the test's own thread
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(TIMEOUT)
    if thread.is_alive():
        pytest.fail(
            f"run() did not return within {TIMEOUT:.0f}s. Does every pass through the loop "
            "call the model and count the turn?"
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


# TODO 1.1
def test_the_loop_runs_a_tool_and_feeds_the_result_back() -> None:
    result = run(a_loop([LOOKUP, {"text": "harness was found"}], system_prompt="Be brief."))

    assert result.final_text == "harness was found"
    assert result.turns == 2
    roles = [m.role for m in result.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    call_turn, tool_result = result.messages[2], result.messages[3]
    assert call_turn.tool_calls, "the assistant message must keep the tool calls it made"
    assert tool_result.tool_call_id == call_turn.tool_calls[0].id
    assert "harness: found" in tool_result.content
    assert (result.input_tokens, result.output_tokens) == (200, 40)


def test_the_turn_cap_and_the_token_budget_stop_a_run() -> None:
    never_answers = [LOOKUP] * 10

    with pytest.raises(TurnCapExceeded):
        run(a_loop(never_answers, turn_cap=3))
    # 120 tokens a turn, so the second turn reaches a budget of 200.
    with pytest.raises(BudgetExhausted):
        run(a_loop(never_answers, token_budget=200))


# TODO 1.2
def test_a_failing_tool_comes_back_to_the_model_as_an_error() -> None:
    result = run(a_loop([FAILING_LOOKUP, {"text": "it was not there"}]))

    assert result.final_text == "it was not there"
    error = result.messages[2]
    assert error.role == "tool" and error.is_error
    assert json.loads(error.content) == {"error": "no entry for 'missing'", "hint": "try another word"}


def test_the_retry_cap_counts_failures_in_a_row() -> None:
    # Two failures, then a success that resets the count: never three in a row.
    turns = [FAILING_LOOKUP, FAILING_LOOKUP, LOOKUP, FAILING_LOOKUP, FAILING_LOOKUP]
    assert run(a_loop([*turns, {"text": "done"}], retry_cap=3)).final_text == "done"

    with pytest.raises(RetryCapExceeded):
        run(a_loop([FAILING_LOOKUP] * 3 + [{"text": "never reached"}], retry_cap=3))


# TODO 1.3
def test_truncate_says_what_it_cut() -> None:
    assert truncate("short", 100) == "short"

    cut = truncate("x" * 1000, 100)
    assert cut.startswith("x" * 100) and not cut.startswith("x" * 101)
    assert {"100", "1000", "900"} <= set(re.findall(r"\d+", cut[100:])), (
        "the marker should name the bytes shown, the total, and how many were suppressed"
    )

    # "é" is two bytes in UTF-8, so a 5-byte cap falls in the middle of the third one.
    cut = truncate("é" * 10, 5)
    assert cut.startswith("éé") and not cut.startswith("ééé")
    assert "�" not in cut, "drop the half character rather than replacing it"
