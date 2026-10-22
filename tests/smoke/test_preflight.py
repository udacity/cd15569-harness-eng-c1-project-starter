"""Pre-flight: the whole harness, offline, before your first real run.

    python -m pytest tests/smoke/test_preflight.py

Run it once Tasks 1-5 are all done. It runs the harness the way you will, three sessions
as three separate commands, with your config/agent.yaml. Only two things are replaced: a
script plays the model, so it needs no key and makes no API calls, and a small service
made up in a temporary folder stands in for webhook-delivery, so the service itself is
never touched.

It checks outcomes only: each session finishes its task, a fourth finds nothing left to
do, and each task ran in its own session. When a session fails, the test shows its output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from onboarding_agent.state import TaskList
from onboarding_agent.workspace import FIXTURE_ROOT_VAR

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "agent.yaml"
SESSION_TIMEOUT = 60


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    service = tmp_path / "service"
    service.mkdir()
    (service / "app.py").write_text("print('hello')\n")
    # Points the config at the made-up service instead of webhook-delivery.
    monkeypatch.setenv(FIXTURE_ROOT_VAR, str(service))
    return service


def test_the_harness_runs_three_sessions_offline(service: Path, tmp_path: Path) -> None:
    # Your config, with a script for the model and the made-up service for the workspace.
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {"tool": "file_write", "args": {"path": "notes.md", "content": "one more line",
                                                "append": True}},
                {"text": "Added a line to notes.md."},
            ]
        )
    )
    raw = yaml.safe_load(CONFIG.read_text()) or {}
    raw.setdefault("model", {})["name"] = str(script)  # tests/scripted_cli.py replays it
    raw["workspace"] = {"root": str(service), "runs_dir": str(tmp_path / "runs")}
    config = tmp_path / "agent.yaml"
    config.write_text(yaml.safe_dump(raw))

    seed = tmp_path / "tasks.json"
    seed.write_text(
        json.dumps(
            [
                {"id": f"task-{n}", "title": f"Task {n}", "prompt": "Add a line to notes.md.",
                 "expects": ["notes.md"]}
                for n in (1, 2, 3)
            ]
        )
    )

    def session(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [sys.executable, "-m", "tests.scripted_cli", "run", "--config", str(config),
                 *args],
                capture_output=True,
                text=True,
                cwd=tmp_path,
                timeout=SESSION_TIMEOUT,
                env={**os.environ, "PYTHONPATH": str(REPO), FIXTURE_ROOT_VAR: str(service)},
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"a session did not finish within {SESSION_TIMEOUT}s")

    # The same three commands as in the README.
    for n, result in enumerate([session("--tasks", str(seed)), session(), session()], start=1):
        assert result.returncode == 0, (
            f"session {n} did not finish its task. Its output:\n{result.stdout}\n{result.stderr}"
        )
    assert session().returncode == 3, "a fourth session should find nothing left to do"

    [run_dir] = (tmp_path / "runs").iterdir()
    tasks = TaskList.load(run_dir / "tasks.json").tasks
    assert [t.status for t in tasks] == ["done", "done", "done"]
    assert len({t.session_id for t in tasks}) == 3, "each task should run in its own session"
    assert (run_dir / "notes.md").read_text().count("one more line") == 3
