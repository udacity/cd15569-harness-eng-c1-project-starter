"""The event log: one JSONL file per session, one event per line.

Every model call, tool call and session outcome is recorded here and nowhere else, so
tokens, timings and errors can all be worked out from the log afterwards. Each line is
flushed as it is written, so a session that crashes still leaves its last events.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Any
from uuid import uuid4


class EventLog:
    """Append-only session log. Not thread-safe; the harness is synchronous."""

    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self._seq = 0
        self._started = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: IO[str] = self.path.open("a", encoding="utf-8")

    @classmethod
    def for_session(cls, output_root: Path, session_id: str | None = None) -> EventLog:
        """Open a new session's log at ``<output_root>/<session_id>/events.jsonl``."""
        # The random suffix is there because two sessions can start in the same second.
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        session = session_id or f"{stamp}-{uuid4().hex[:6]}"
        return cls(Path(output_root) / session / "events.jsonl", session)

    @property
    def session_dir(self) -> Path:
        return self.path.parent

    def emit(self, event: str, **fields: Any) -> None:
        self._seq += 1
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "seq": self._seq,
            "session_id": self.session_id,
            "event": event,
            **fields,
        }
        self._handle.write(json.dumps(record, default=str) + "\n")
        self._handle.flush()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
