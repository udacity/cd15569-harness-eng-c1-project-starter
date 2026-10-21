"""A scripted stand-in for the model, for tests only.

No key, no network, byte-identical transcripts run to run. The loop takes a ``ModelClient``
and calls nothing on it but ``model`` and ``complete``, so this duck-types in its place.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

from onboarding_agent.model import Message, ModelResponse, ToolCall


def scripted(turns: list[dict[str, Any]]) -> list[ModelResponse]:
    """Build a response script from a compact spec.

    ``{"tool": name, "args": {...}}`` becomes a tool call; ``{"text": "..."}`` a final answer.

    Call ids are numbered per script, never from a module-level counter: a shared counter
    makes a transcript depend on how many other scripts the process built first, which
    breaks the byte-identical guarantee.
    """
    ids = itertools.count(1)
    responses: list[ModelResponse] = []
    for turn in turns:
        if "tool" in turn:
            responses.append(
                ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id=f"call_{next(ids)}",
                            name=str(turn["tool"]),
                            arguments=dict(turn.get("args") or {}),
                        )
                    ],
                    input_tokens=100,
                    output_tokens=20,
                )
            )
        else:
            responses.append(
                ModelResponse(text=str(turn.get("text", "")), input_tokens=100, output_tokens=20)
            )
    return responses


class ScriptedModel:
    """Replays a fixed script, ignoring what the model was actually asked."""

    model = "scripted"

    def __init__(self, script: list[ModelResponse] | None = None) -> None:
        self._script: list[ModelResponse] = list(script or [])
        self._index = 0

    @classmethod
    def from_script_file(cls, path: Path) -> ScriptedModel:
        """Build from a JSON array of turns, so a whole session can be scripted from disk."""
        turns = json.loads(Path(path).read_text())
        if not isinstance(turns, list):
            raise ValueError(f"{path} must contain a JSON array of turns")
        return cls(scripted(turns))

    def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        if self._index >= len(self._script):
            raise AssertionError(
                f"script exhausted after {len(self._script)} turns; "
                "the loop asked for one more response than the test scripted"
            )
        response = self._script[self._index]
        self._index += 1
        return response
