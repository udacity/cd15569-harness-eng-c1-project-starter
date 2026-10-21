"""The tool protocol, the error tools raise, and helpers the tools share."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable


class ToolError(Exception):
    """A tool failure the model should see. ``hint`` tells it what to do differently."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        """The message and the hint together, for logs."""
        return f"{self.message} ({self.hint})" if self.hint else self.message

    def as_payload(self) -> str:
        """The JSON the model receives: ``{"error": message, "hint": hint}``."""
        return json.dumps({"error": self.message, "hint": self.hint})


@runtime_checkable
class Tool(Protocol):
    """One callable capability exposed to the model."""

    name: str
    description: str

    def parameters(self) -> dict[str, Any]:
        """JSON Schema for this tool's arguments."""
        ...

    def call(self, args: dict[str, Any]) -> str:
        """Execute and return output as text. Raises ToolError on failure."""
        ...


# TODO 1.3: Implement truncate(), which caps the output of every tool. A result
# that is cut short must say so, or the model will reason from part of a file as
# if it were all of it.
def truncate(text: str, max_bytes: int) -> str:
    """Cut ``text`` to its first ``max_bytes`` bytes of UTF-8, and say so.

    Text that fits is returned unchanged. Otherwise the kept part is followed by a marker
    naming the bytes shown, the total, and how many were suppressed, so the model knows it
    is looking at part of the output.
    """
    # TODO: Encode the text as UTF-8, and return it unchanged if it fits.

    # TODO: Keep the first max_bytes bytes, dropping a character cut in half, and
    # append a marker such as
    # "[output truncated: showing 400 of 10000 bytes, 9600 suppressed]".


def capped(requested: object, ceiling: int) -> int:
    """The output cap a tool call asked for, clamped to ``ceiling``.

    A missing or invalid request gets the ceiling. A call can ask for less, never more.
    """
    if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
        return ceiling
    return min(requested, ceiling)


def require(args: dict[str, Any], key: str) -> str:
    """The string argument ``key``, or a ToolError if it is missing or empty."""
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ToolError(
            f"missing required argument {key!r}",
            hint=f"pass {key!r} as a non-empty string",
        )
    return value
