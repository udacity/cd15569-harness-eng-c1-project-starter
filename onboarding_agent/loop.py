"""The agent loop.

Send the transcript to the model, run the tool calls it asks for, add the results to the
transcript, and repeat until the model gives a final answer or a limit is hit. A limit
raises rather than returning a partial result, so a run that never answered cannot pass
for one that did.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from onboarding_agent.events import EventLog
from onboarding_agent.model import Message, ModelClient, ToolCall
from onboarding_agent.tools.base import ToolError
from onboarding_agent.tools.registry import ToolRegistry


class RunAborted(RuntimeError):
    """Base for the ways a run can stop without answering."""

    outcome = "aborted"


class TurnCapExceeded(RunAborted):
    """The model was still calling tools when the turn cap was reached."""

    outcome = "turn_cap"

    def __init__(self, turn_cap: int) -> None:
        super().__init__(f"turn cap of {turn_cap} exceeded without a final answer")
        self.turn_cap = turn_cap


class RetryCapExceeded(RunAborted):
    """Too many tool calls failed in a row. A successful call resets the count."""

    outcome = "retry_cap"

    def __init__(self, retry_cap: int, last_error: str) -> None:
        super().__init__(
            f"{retry_cap} tool calls failed in a row without a success; last error: {last_error}"
        )
        self.retry_cap = retry_cap
        self.last_error = last_error


class BudgetExhausted(RunAborted):
    """The token budget ran out mid-run."""

    outcome = "budget_exhausted"

    def __init__(self, token_budget: int, used: int) -> None:
        super().__init__(f"token budget of {token_budget} exhausted after {used} tokens")
        self.token_budget = token_budget
        self.used = used


@dataclass
class RunResult:
    """A finished run: the final answer, the transcript, and the token totals."""

    final_text: str
    turns: int
    messages: list[Message] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class AgentLoop:
    """Runs one task to completion, or to a limit."""

    def __init__(
        self,
        client: ModelClient,
        registry: ToolRegistry,
        turn_cap: int = 40,
        system_prompt: str | None = None,
        retry_cap: int = 3,
        token_budget: int = 200_000,
        events: EventLog | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._turn_cap = turn_cap
        self._system_prompt = system_prompt
        self._retry_cap = retry_cap
        self._token_budget = token_budget
        self._events = events
        self._consecutive_failures = 0

    # TODO 1.1: Implement run(), the agent loop. Call the model, run the tools it
    # asks for, add their results to the transcript, and repeat until the model
    # answers. The token budget and the turn cap are enforced here, in code: hitting
    # either one raises, it never returns a partial answer.
    def run(self, task: str) -> RunResult:
        """Run ``task`` to a final answer, or raise a ``RunAborted`` when a limit is hit."""
        messages: list[Message] = []

        # TODO: Start the transcript with the system prompt, if there is one, and
        # then the task as a user message.

        schemas = self._registry.schemas()
        self._consecutive_failures = 0
        turns = 0
        input_tokens = 0
        output_tokens = 0

        self._emit(
            "session_start",
            task=task,
            model=self._client.model,
            tools=self._registry.names(),
            turn_cap=self._turn_cap,
            retry_cap=self._retry_cap,
            token_budget=self._token_budget,
            session_dir=str(self._events.session_dir) if self._events else None,
        )

        try:
            while turns < self._turn_cap:
                # TODO: Call the model with the transcript and the tool schemas, and
                # time the call. Count the turn, and add the response's input and
                # output tokens to the running totals.

                # TODO: Emit a "model_call" event with the turn, the model, both token
                # counts, duration_ms, the names of the tools called, and whether the
                # response was final.

                # TODO: If the response is final, append it as an assistant message,
                # build the RunResult, emit the session's end with the outcome
                # "final_answer", and return the result.

                # TODO: Otherwise append the assistant message with its tool_calls
                # attached, then one result message per call, from self._execute().

                # TODO: Raise BudgetExhausted once input and output tokens together
                # reach the token budget.
                pass

            raise TurnCapExceeded(self._turn_cap)
        except RunAborted as exc:
            self._emit_end(
                exc.outcome,
                RunResult("", turns, messages, input_tokens, output_tokens),
                error=str(exc),
            )
            raise

    # TODO 1.2: Implement _execute(), which runs one tool call. A tool that fails
    # must come back to the model as an error it can read, not as an exception,
    # until enough calls fail in a row to reach the retry cap.
    def _execute(self, call: ToolCall, turn: int) -> Message:
        """Run one tool call and return its result message.

        A failing tool becomes an error result the model can read rather than an
        exception, until failures in a row reach the retry cap: that one raises.
        """
        self._emit(
            "tool_call", turn=turn, call_id=call.id, tool=call.name, arguments=call.arguments
        )
        started = time.monotonic()

        # TODO: Dispatch the call through the registry. If it raises ToolError, count
        # one more consecutive failure and emit a failed "tool_result" event (ok=False,
        # the error, output_bytes, duration_ms and the failure count). Raise
        # RetryCapExceeded when the count reaches the retry cap; otherwise return the
        # error payload as a tool message with is_error=True.

        # TODO: On success, reset the failure count, emit a successful "tool_result"
        # event, and return the output as a tool message for this call's id.

    def _emit(self, event: str, **fields: object) -> None:
        if self._events is not None:
            self._events.emit(event, **fields)

    def _emit_end(self, outcome: str, result: RunResult, error: str | None = None) -> None:
        if self._events is None:
            return
        self._events.emit(
            "session_end",
            outcome=outcome,
            turns=result.turns,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=self._events.elapsed_ms(),
            error=error,
        )


def _ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
