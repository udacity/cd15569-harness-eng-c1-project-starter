"""State that outlives a session: the task list and the progress file.

The task list (``tasks.json``) is the plan. It is append-only: tasks can be added and their
status can change, but none is removed, reordered or reworded. The progress file
(``progress.json``) is the handoff a new session starts from. It carries a checksum, so a
damaged file is detected rather than trusted. Both files are written atomically, so a
crash mid-write leaves the previous version intact.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATUSES = ("pending", "running", "done", "failed")


class StateError(RuntimeError):
    """Something is wrong with the persisted state."""


class CorruptState(StateError):
    """The file on disk cannot be trusted: bad schema, bad checksum, or unparseable."""


class AppendOnlyViolation(StateError):
    """An edit would have rewritten history rather than extending it."""


@dataclass(frozen=True)
class Task:
    """One task: what to ask the model, and what happened when it ran."""

    id: str
    title: str
    prompt: str
    status: str = "pending"
    session_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    artifacts: tuple[str, ...] = ()
    # Files the task must leave in the run folder. A session that doesn't write them
    # fails the task.
    expects: tuple[str, ...] = ()
    note: str = ""


_TASK_FIELDS = set(Task.__dataclass_fields__)


# TODO 4.1: Finish TaskList, the plan on disk. It is strict about what it accepts, and
# append-only: tasks can be added and their status can change, but nothing that was
# asked is ever removed or rewritten. Every change is saved straight away.
class TaskList:
    """The plan, on disk. Append-only: tasks may be added and their status advanced, but
    never removed, reordered, or reworded."""

    def __init__(self, path: Path, tasks: list[Task]) -> None:
        self.path = Path(path)
        self.tasks = tasks

    @classmethod
    def create(cls, path: Path, tasks: Sequence[Task]) -> TaskList:
        """Start a task list at ``path`` from ``tasks``, and save it."""
        # TODO: Build the list from `tasks` and save it with _save(), so a new run's
        # plan is on disk before its first session starts.
        instance = ...
        return instance

    @classmethod
    def load(cls, path: Path) -> TaskList:
        raw = _read_json(Path(path))
        _check_version(raw.get("schema_version"), path)
        entries = raw.get("tasks")
        if not isinstance(entries, list):
            raise CorruptState(f"{path}: 'tasks' must be a list")
        return cls(Path(path), [_task(entry, path) for entry in entries])

    def by_id(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise StateError(f"no task {task_id!r}; known tasks are {[t.id for t in self.tasks]}")

    # A failed task keeps its `failed` status, and the next session runs it again.
    OUTSTANDING = ("pending", "running", "failed")

    def next_pending(self) -> Task | None:
        return next((t for t in self.tasks if t.status in self.OUTSTANDING), None)

    def remaining(self) -> int:
        return sum(1 for t in self.tasks if t.status in self.OUTSTANDING)

    def update(self, task_id: str, **fields: Any) -> Task:
        """Change fields of the task ``task_id``, save, and return the updated task."""
        # TODO: Raise StateError if `fields` names something that is not a Task field,
        # or sets a status that is not in STATUSES.

        # TODO: Find the task by id (by_id() raises for an unknown one), put a copy with
        # the new fields in its place (tasks are frozen, so use replace()), save, and
        # return the copy.
        updated = ...
        return updated

    def append(self, task: Task) -> None:
        """Add ``task`` at the end, and save."""
        # TODO: Raise AppendOnlyViolation if a task with the same id is already in the
        # list. Otherwise add it at the end and save.

    def replace_all(self, tasks: Sequence[Task]) -> None:
        """Replace the whole list, provided it only extends the current one.

        Statuses may change; ids, order and wording may not.
        """
        _check_append_only(self.tasks, tasks)
        self.tasks = list(tasks)
        self._save()

    def _save(self) -> None:
        _write_json(
            self.path,
            {
                "schema_version": SCHEMA_VERSION,
                "updated_at": _now(),
                "tasks": [_serialise(t) for t in self.tasks],
            },
        )


@dataclass(frozen=True)
class Progress:
    """What a fresh session needs, and nothing else."""

    task_list: str
    next_task_id: str | None
    completed: tuple[str, ...] = ()
    last_commit: str | None = None
    handoff: str = ""
    session_count: int = 0
    updated_at: str = field(default_factory=lambda: _now())


_PROGRESS_FIELDS = set(Progress.__dataclass_fields__)


class ProgressFile:
    """Reads and writes ``progress.json``: ``{"schema_version", "checksum", "payload"}``.

    The checksum is not security, since anyone can recompute it. It catches a truncated
    write or a hand edit before a session trusts the file.
    """

    # TODO 4.2: Implement write(), which saves the handoff for the next session.
    @staticmethod
    def write(path: Path, progress: Progress) -> None:
        # TODO: Turn `progress` into a dict, with updated_at set to now, and save
        # {"schema_version", "checksum", "payload"} with _write_json(), where the
        # checksum comes from _checksum(payload).
        pass

    # TODO 4.3: Implement load(). A new session trusts this file only after checking
    # it, and raises CorruptState for anything wrong, so the runner knows to fall
    # back to the last checkpoint.
    @staticmethod
    def load(path: Path) -> Progress:
        raw = _read_json(Path(path))
        _check_version(raw.get("schema_version"), path)

        # TODO: Take the payload and check it: it must be a dict, with every Progress
        # field (updated_at may be missing) and no others, and its checksum must
        # match the stored one. Raise CorruptState naming the problem if not.
        payload = ...

        return Progress(
            task_list=str(payload["task_list"]),
            next_task_id=payload["next_task_id"],
            completed=tuple(payload.get("completed") or ()),
            last_commit=payload.get("last_commit"),
            handoff=str(payload.get("handoff") or ""),
            session_count=int(payload.get("session_count") or 0),
            updated_at=str(payload.get("updated_at") or _now()),
        )


def _checksum(payload: dict[str, Any]) -> str:
    """``sha256:<hex>`` of the payload as canonical JSON: sorted keys, no spaces."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


# TODO 4.4: Implement _check_append_only(), which keeps the task list append-only:
# new tasks may be added at the end, and statuses may change, but what was asked
# can never be removed, reordered or reworded.
def _check_append_only(current: Sequence[Task], proposed: Sequence[Task]) -> None:
    # TODO: Raise AppendOnlyViolation if `proposed` is shorter than `current`, or if
    # any task in `current` has a different id, prompt or title at the same position.
    pass


# TODO 4.5: Implement _task(), which checks each task as a task list is read back from
# disk. A list that was edited by hand or damaged must be refused, not half-trusted.
def _task(entry: object, path: Path) -> Task:
    """One task read back from ``path``, or CorruptState if it is not a valid task."""
    # TODO: Raise CorruptState, naming `path`, if the entry is not a dict, has a field
    # Task does not have, lacks id, title or prompt, or has a status that is not in
    # STATUSES. A missing status means "pending".
    status = ...

    return Task(
        id=str(entry["id"]),
        title=str(entry["title"]),
        prompt=str(entry["prompt"]),
        status=str(status),
        session_id=entry.get("session_id"),
        started_at=entry.get("started_at"),
        completed_at=entry.get("completed_at"),
        artifacts=tuple(entry.get("artifacts") or ()),
        expects=tuple(entry.get("expects") or ()),
        note=str(entry.get("note") or ""),
    )


def _serialise(task: Task) -> dict[str, Any]:
    data = asdict(task)
    data["artifacts"] = list(task.artifacts)
    data["expects"] = list(task.expects)
    return data


def _check_version(version: object, path: Path) -> None:
    if version != SCHEMA_VERSION:
        raise CorruptState(
            f"{path}: schema_version is {version!r}, this build understands {SCHEMA_VERSION}"
        )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CorruptState(f"no state file at {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise CorruptState(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorruptState(f"{path} must be a JSON object, found {type(raw).__name__}")
    return raw


# TODO 4.6: Implement _write_json(), which saves every state file. A crash in the
# middle of a write must leave the previous version of the file intact.
def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as JSON, atomically: a temp file, fsync, then rename over ``path``.

    The temp file goes in the same folder because a rename is only atomic within one
    filesystem, and the fsync stops the rename landing before the data does.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # TODO: Write the JSON, indented and ending in a newline, to a temporary file in
    # the same folder. Flush and fsync it, then os.replace() it over `path`. If
    # anything fails, delete the temporary file and re-raise.


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def tasks_from_seed(path: Path) -> list[Task]:
    """Read the task definitions a run starts from: a JSON array of id/title/prompt."""
    try:
        entries = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"cannot read task seed {path}: {exc}") from exc
    if not isinstance(entries, list) or not entries:
        raise StateError(f"{path} must be a non-empty JSON array of tasks")
    return [_seed_task(entry, path) for entry in entries]


def _seed_task(entry: object, path: Path) -> Task:
    if not isinstance(entry, dict) or not {"id", "title", "prompt"} <= set(entry):
        raise StateError(f"{path}: every task needs 'id', 'title' and 'prompt'")
    return Task(
        id=str(entry["id"]),
        title=str(entry["title"]),
        prompt=str(entry["prompt"]),
        expects=tuple(entry.get("expects") or ()),
    )


def unique(values: Iterable[str]) -> tuple[str, ...]:
    """Order-preserving dedupe, for the completed list."""
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)
