"""Smoke test for Task 2: the file and shell tools (TODO 2.1-2.4).

    python -m pytest tests/smoke/test_task_2.py

Each test makes a small service in a temporary folder, with a run folder beside it: the
same layout the harness uses for webhook-delivery. The tools call truncate() from Task 1.

Passing means the main behaviour of each TODO works, not that every case is handled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from onboarding_agent.tools.base import ToolError
from onboarding_agent.tools.files import FileEdit, FileRead, FileWrite
from onboarding_agent.tools.shell import ShellExec
from onboarding_agent.workspace import Workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    (tmp_path / "service").mkdir()
    (tmp_path / "run").mkdir()
    return Workspace(root=tmp_path / "service", writable_root=tmp_path / "run")


@pytest.fixture
def run_dir(workspace: Workspace) -> Path:
    assert workspace.writable_root is not None
    return workspace.writable_root


# TODO 2.1
def test_file_read_numbers_every_line_and_caps_the_result(workspace: Workspace) -> None:
    lines = [f"line {n}" for n in range(1, 13)]
    (workspace.root / "notes.txt").write_text("\n".join(lines) + "\n")

    numbered = FileRead(workspace).call({"path": "notes.txt"}).splitlines()
    assert numbered[0] == " 1|line 1"
    assert numbered[11] == "12|line 12"

    (workspace.root / "big.txt").write_text("y" * 5000)
    assert len(FileRead(workspace, max_bytes=200).call({"path": "big.txt"})) < 1000


def test_file_read_refuses_a_missing_path_or_a_folder(workspace: Workspace) -> None:
    (workspace.root / "src").mkdir()
    for path in ("nowhere.txt", "src"):
        with pytest.raises(ToolError) as caught:
            FileRead(workspace).call({"path": path})
        assert caught.value.hint, f"the error for {path!r} needs a hint"


# TODO 2.2
def test_file_write_never_replaces_a_file_unless_asked(workspace: Workspace, run_dir: Path) -> None:
    write = FileWrite(workspace).call
    notes = run_dir / "notes.md"

    write({"path": "notes.md", "content": "first"})
    assert notes.read_text() == "first"

    with pytest.raises(ToolError):
        write({"path": "notes.md", "content": "second"})
    assert notes.read_text() == "first", "a refused write must leave the file as it was"

    write({"path": "notes.md", "content": "second", "append": True})
    assert notes.read_text() == "first\nsecond"

    write({"path": "notes.md", "content": "third", "overwrite": True})
    assert notes.read_text() == "third"

    write({"path": "log.md", "content": "created", "append": True})
    assert (run_dir / "log.md").read_text() == "created"


# TODO 2.3
def test_file_edit_replaces_one_occurrence(workspace: Workspace, run_dir: Path) -> None:
    draft = run_dir / "draft.md"
    draft.write_text("status: draft\nowner: nobody\n")

    FileEdit(workspace).call({"path": "draft.md", "old": "draft", "new": "final"})

    assert draft.read_text() == "status: final\nowner: nobody\n"


def test_file_edit_fails_whenever_the_edit_would_change_nothing(
    workspace: Workspace, run_dir: Path
) -> None:
    draft = run_dir / "draft.md"
    draft.write_text("one two two\n")

    # No match, two matches, and a replacement that is the same as the original.
    for old, new in (("three", "3"), ("two", "2"), ("one", "one")):
        with pytest.raises(ToolError) as caught:
            FileEdit(workspace).call({"path": "draft.md", "old": old, "new": new})
        assert caught.value.hint, f"the error for old={old!r} needs a hint"

    assert draft.read_text() == "one two two\n"


# TODO 2.4
def test_shell_exec_runs_in_the_service_and_shows_a_failing_exit_code(
    workspace: Workspace,
) -> None:
    (workspace.root / "marker.txt").write_text("here\n")
    shell = ShellExec(workspace)

    assert "marker.txt" in shell.call({"command": "ls"})
    assert shell.call({"command": "echo oops; exit 3"}).startswith("[exit 3]")


def test_shell_exec_refuses_paths_outside_the_service(
    workspace: Workspace, tmp_path: Path
) -> None:
    (tmp_path / "secret.txt").write_text("not for the agent\n")

    for command in ("cat ../secret.txt", f"cat {tmp_path / 'secret.txt'}"):
        with pytest.raises(ToolError) as caught:
            ShellExec(workspace).call({"command": command})
        assert caught.value.hint, f"the error for {command!r} needs a hint"


def test_shell_exec_stops_a_command_that_runs_too_long(workspace: Workspace) -> None:
    with pytest.raises(ToolError) as caught:
        ShellExec(workspace).call({"command": "sleep 5", "timeout": 0.5})
    assert caught.value.hint
