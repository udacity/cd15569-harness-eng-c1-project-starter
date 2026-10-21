"""Load a `.env` file into the environment.

This keeps the API key out of the config file, which gets committed. It reads `KEY=value`
lines, `#` comments and optional quotes, nothing more. A variable that is already set
in the environment is never overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path

FILENAME = ".env"


def load_env(start: Path | str | None = None, *, override: bool = False) -> dict[str, str]:
    """Read the nearest `.env` at or above ``start``. Returns what it applied."""
    path = find_env(Path(start) if start is not None else Path.cwd())
    if path is None:
        return {}
    applied: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse(line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def find_env(start: Path | str) -> Path | None:
    start = Path(start).resolve()
    for directory in (start, *start.parents):
        candidate = directory / FILENAME
        if candidate.is_file():
            return candidate
    return None


def _parse(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.removeprefix("export ").strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return (key, value) if key else None
