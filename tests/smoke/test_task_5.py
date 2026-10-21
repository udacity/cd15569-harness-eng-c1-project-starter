"""Smoke test for Task 5: the configuration (TODO 5.1-5.6), and then the whole harness.

    python -m pytest tests/smoke/test_task_5.py

A small service made up in a temporary folder stands in for webhook-delivery, so the
service itself is never touched. The last test runs the harness the way you will, three
sessions as three separate commands, with your config/agent.yaml. Only the model is
replaced, by a script, so it needs no key and makes no API calls.

The config is checked for shape only: that each section is set and the loader accepts it.
Whether each line is right is for you to defend.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from onboarding_agent.config import (
    HarnessConfig,
    Limits,
    ModelSettings,
    ToolSettings,
    WorkspaceSettings,
    load_config,
)
from onboarding_agent.state import TaskList
from onboarding_agent.tools.base import ToolError
from onboarding_agent.tools.registry import ToolRegistry
from onboarding_agent.workspace import FIXTURE_ROOT_VAR, Workspace

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


# TODO 5.1-5.4
def test_your_config_sets_each_section_and_loads(service: Path) -> None:
    raw = yaml.safe_load(CONFIG.read_text()) or {}
    sections = {
        "model": {"name", "max_output_tokens"},
        "limits": {"turn_cap", "token_budget", "retry_cap", "max_tool_output_bytes"},
        "tools": {"allow"},
        "system_prompt": {"rules"},
    }
    for section, keys in sections.items():
        missing = sorted(keys - set(raw.get(section) or {}))
        assert not missing, f"config/agent.yaml does not set {section}: {', '.join(missing)}"

    config = load_config(CONFIG)  # a ConfigError here lists every problem the loader found

    assert config.system_prompt.rules, "write at least one rule"
    assert not config.system_prompt.over_budget


# TODO 5.5
def test_the_registry_builds_only_the_tools_the_config_allows(service: Path) -> None:
    workspace = Workspace(root=service)

    registry = ToolRegistry.from_config(a_config(service, ["search_code", "file_read"]), workspace)
    assert registry.names() == ["search_code", "file_read"]

    (service / "big.txt").write_text("z" * 5000)
    small = ToolRegistry.from_config(a_config(service, ["file_read"], cap=200), workspace)
    assert len(small.call("file_read", {"path": "big.txt"})) < 1000, "apply the config's cap"

    # The loader refuses a name like this one, so this config skips the loader.
    with pytest.raises(ToolError):
        ToolRegistry.from_config(a_config(service, ["file_read", "teleport"]), workspace)


# TODO 5.6
def test_the_registry_runs_a_tool_by_name_and_refuses_made_up_ones(service: Path) -> None:
    allowed = ["file_read", "search_code"]
    registry = ToolRegistry.from_config(a_config(service, allowed), Workspace(root=service))

    assert "print('hello')" in registry.call("file_read", {"path": "app.py"})

    with pytest.raises(ToolError) as caught:
        registry.call("read_file", {"path": "app.py"})
    assert all(name in caught.value.hint for name in allowed), "list the tools there are"


# Everything
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


def a_config(service: Path, allow: list[str], cap: int = 20_000) -> HarnessConfig:
    return HarnessConfig(
        model=ModelSettings(name="unused"),
        workspace=WorkspaceSettings(service),
        tools=ToolSettings(allow=allow),
        limits=Limits(max_tool_output_bytes=cap),
    )
