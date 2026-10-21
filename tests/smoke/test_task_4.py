"""Smoke test for Task 4: state that survives the process (TODO 4.1-4.8).

    python -m pytest tests/smoke/test_task_4.py

The runner's _execute() runs the agent loop with the model and the tools from your config,
which you finish in Task 5. The run_one() tests replace it with a stand-in that pretends
the task ran, so they check only what you write in 4.8. Task 5's smoke test runs the
real thing, across three separate processes.

Passing means the main behaviour of each TODO works, not that every case is handled.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from onboarding_agent.checkpoint import Checkpoint
from onboarding_agent.config import HarnessConfig, ModelSettings, WorkspaceSettings
from onboarding_agent.runner import PROGRESS_FILE, TASKS_FILE, NoWorkRemaining, SessionRunner
from onboarding_agent.state import (
    SCHEMA_VERSION,
    AppendOnlyViolation,
    CorruptState,
    Progress,
    ProgressFile,
    StateError,
    Task,
    TaskList,
)

FIRST = Task(id="t1", title="First", prompt="Do the first thing.")
SECOND = Task(id="t2", title="Second", prompt="Do the second thing.")
THIRD = Task(id="t3", title="Third", prompt="Do the third thing.")


# TODO 4.1
def test_the_task_list_saves_every_change_and_refuses_bad_ones(tmp_path: Path) -> None:
    path = tmp_path / TASKS_FILE
    tasks = TaskList.create(path, [FIRST, SECOND])
    assert [t.id for t in TaskList.load(path).tasks] == ["t1", "t2"]

    tasks.update("t1", status="done")
    assert TaskList.load(path).by_id("t1").status == "done"

    tasks.append(THIRD)
    assert [t.id for t in TaskList.load(path).tasks] == ["t1", "t2", "t3"]

    with pytest.raises(StateError):
        tasks.update("t2", colour="blue")
    with pytest.raises(StateError):
        tasks.update("t2", status="finished")
    with pytest.raises(AppendOnlyViolation):
        tasks.append(replace(FIRST, title="First, again"))


# TODO 4.2 and 4.3
def test_the_progress_file_round_trips_and_catches_tampering(tmp_path: Path) -> None:
    path = tmp_path / PROGRESS_FILE
    ProgressFile.write(
        path,
        Progress(
            task_list=TASKS_FILE,
            next_task_id="t2",
            completed=("t1",),
            handoff="t1 is done.",
            session_count=1,
        ),
    )

    loaded = ProgressFile.load(path)
    assert (loaded.next_task_id, loaded.completed, loaded.handoff, loaded.session_count) == (
        "t2",
        ("t1",),
        "t1 is done.",
        1,
    )

    raw = json.loads(path.read_text())
    raw["payload"]["next_task_id"] = "t3"  # a hand edit that skips a task: the checksum fails
    path.write_text(json.dumps(raw))
    with pytest.raises(CorruptState):
        ProgressFile.load(path)


# TODO 4.4
def test_the_task_list_is_append_only(tmp_path: Path) -> None:
    tasks = TaskList.create(tmp_path / TASKS_FILE, [FIRST, SECOND])

    removed, reordered, reworded = [FIRST], [SECOND, FIRST], [FIRST, replace(SECOND, prompt="No.")]
    for proposed in (removed, reordered, reworded):
        with pytest.raises(AppendOnlyViolation):
            tasks.replace_all(proposed)

    tasks.replace_all([replace(FIRST, status="done"), SECOND, THIRD])
    statuses = [t.status for t in TaskList.load(tmp_path / TASKS_FILE).tasks]
    assert statuses == ["done", "pending", "pending"]


# TODO 4.5
def test_a_damaged_task_list_is_refused_when_it_is_read(tmp_path: Path) -> None:
    path = tmp_path / TASKS_FILE

    def saved(entry: object) -> Path:
        path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "tasks": [entry]}))
        return path

    good = {"id": "t1", "title": "First", "prompt": "Do the first thing."}
    assert TaskList.load(saved(good)).by_id("t1").status == "pending"

    for damaged in (
        "not a task",
        {**good, "colour": "blue"},
        {"id": "t1", "title": "First"},
        {**good, "status": "finished"},
    ):
        with pytest.raises(CorruptState):
            TaskList.load(saved(damaged))


# TODO 4.6
def test_a_failed_write_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / TASKS_FILE
    tasks = TaskList.create(path, [FIRST, SECOND])
    before = path.read_text()
    assert before.endswith("\n")

    def crash(*args: object) -> None:
        raise OSError("the process died before the rename")

    # A write that goes through os.replace() fails here. One that writes straight to the
    # file never reaches it, and this test fails with "DID NOT RAISE".
    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        tasks.update("t1", status="done")

    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == [TASKS_FILE], "delete the temporary file"


# TODO 4.7
def test_a_corrupt_progress_file_is_restored_from_the_last_checkpoint(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    checkpoint = Checkpoint(run_dir)
    checkpoint.init()
    ProgressFile.write(
        run_dir / PROGRESS_FILE, Progress(task_list=TASKS_FILE, next_task_id="t2", session_count=1)
    )
    checkpoint.commit("a good checkpoint")
    (run_dir / PROGRESS_FILE).write_text("{ not json")

    assert SessionRunner(a_config(tmp_path), run_dir).load_progress().next_task_id == "t2"

    no_checkpoint = tmp_path / "fresh"
    Checkpoint(no_checkpoint).init()
    (no_checkpoint / PROGRESS_FILE).write_text("{ not json")
    with pytest.raises(CorruptState):
        SessionRunner(a_config(tmp_path), no_checkpoint).load_progress()


# TODO 4.8
def test_a_session_runs_exactly_one_task_and_hands_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SessionRunner, "_execute", writes_what_the_task_expects)
    config, run_dir, seed = a_run(tmp_path)

    first = SessionRunner.start(config, run_dir, seed).run_one()

    assert (first.task_id, first.status, first.remaining) == ("t1", "done", 1)
    assert [t.status for t in TaskList.load(run_dir / TASKS_FILE).tasks] == ["done", "pending"]
    progress = ProgressFile.load(run_dir / PROGRESS_FILE)
    assert (progress.next_task_id, progress.completed, progress.session_count) == (
        "t2",
        ("t1",),
        1,
    )
    assert first.commit == Checkpoint(run_dir).last_commit(), "end the session with a checkpoint"

    assert SessionRunner.start(config, run_dir).run_one().task_id == "t2"
    with pytest.raises(NoWorkRemaining):
        SessionRunner.start(config, run_dir).run_one()


def test_a_task_that_leaves_its_file_unwritten_has_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SessionRunner, "_execute", writes_nothing)
    config, run_dir, seed = a_run(tmp_path)

    result = SessionRunner.start(config, run_dir, seed).run_one()

    assert result.status == "failed"
    task = TaskList.load(run_dir / TASKS_FILE).by_id("t1")
    assert task.status == "failed"
    assert "t1.md" in task.note, "the note should name the file that was not written"
    assert ProgressFile.load(run_dir / PROGRESS_FILE).next_task_id == "t1", "it runs again next"


# --------------------------------------------------------------------------------------
# The stand-ins
# --------------------------------------------------------------------------------------


def a_config(tmp_path: Path) -> HarnessConfig:
    service = tmp_path / "service"
    service.mkdir(exist_ok=True)
    return HarnessConfig(model=ModelSettings(name="unused"), workspace=WorkspaceSettings(service))


def a_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A config file, a run folder that doesn't exist yet, and a seed of two tasks, each of
    which must write ``<id>.md``."""
    (tmp_path / "service").mkdir(exist_ok=True)
    config = tmp_path / "agent.yaml"
    config.write_text("model:\n  name: unused\nworkspace:\n  root: service\n")
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {"id": t.id, "title": t.title, "prompt": t.prompt, "expects": [f"{t.id}.md"]}
                for t in (FIRST, SECOND)
            ]
        )
    )
    return config, tmp_path / "run", seed


def writes_what_the_task_expects(
    self: SessionRunner, task: Task, *args: Any
) -> tuple[tuple[int, int, int], str, str]:
    """Stands in for _execute(): writes the task's files, then reports 2 turns and done."""
    for name in task.expects:
        (self.run_dir / name).write_text(f"{task.title}\n")
    return (2, 200, 40), "done", f"{task.id} is finished."


def writes_nothing(
    self: SessionRunner, task: Task, *args: Any
) -> tuple[tuple[int, int, int], str, str]:
    """Stands in for _execute(): reports done without writing anything."""
    return (2, 200, 40), "done", "Done, I think."
