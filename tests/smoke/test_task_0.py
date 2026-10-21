"""Smoke test for TODO 0: your setup.

    python -m pytest tests/smoke/test_task_0.py            # everything but the API call
    python -m pytest tests/smoke/test_task_0.py -m live    # just the API call: one small one

Run it with the virtual environment activated, the same way you will run the agent.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TAG = "inherited-service-v1.0"
MODEL = "gpt-5.4-mini"
VOCAREUM_URL = "https://openai.vocareum.com/v1"


def test_python_is_3_11_or_newer() -> None:
    assert sys.version_info >= (3, 11), (
        f"this is Python {platform.python_version()}, and the harness needs 3.11 or newer. "
        "Recreate .venv with a newer python3 (Setup, step 2)."
    )


def test_the_harness_and_its_dependencies_are_installed() -> None:
    installed = {dist.metadata["Name"] for dist in importlib.metadata.distributions()}
    assert "onboarding-agent" in installed, (
        'the harness is not installed. Run pip install -e ".[test]" (Setup, step 2).'
    )
    import httpx  # noqa: F401
    import yaml  # noqa: F401


def test_git_and_pytest_are_on_your_path() -> None:
    assert shutil.which("git"), "git is not on PATH. Install git."
    assert shutil.which("pytest"), (
        "pytest is not on PATH. Activate the virtual environment: source .venv/bin/activate. "
        "The agent runs the service's tests through shell_exec, so it needs pytest on PATH too."
    )


def test_the_service_is_cloned_at_its_tag_and_unchanged() -> None:
    from onboarding_agent.workspace import fixture_hint, fixture_root

    root = fixture_root()
    assert root is not None, fixture_hint()

    head = _git(root, "rev-parse", "HEAD")
    tagged = _git(root, "rev-parse", f"{TAG}^{{commit}}")
    assert head == tagged, f"{root} is not at {TAG}. Run: git -C {root} checkout {TAG}"

    changed = _git(root, "status", "--porcelain")
    assert not changed, (
        f"{root} has changes, and the agent must see the service exactly as tagged:\n{changed}\n"
        f"Put it back with: git -C {root} reset --hard {TAG} && git -C {root} clean -fd"
    )


def test_your_api_key_is_set() -> None:
    key, _ = _credentials()
    assert key, "OPENAI_API_KEY is not set. Copy .env.example to .env and set it (Setup, step 3)."
    assert not re.search(r"\s|[\"']", key), "OPENAI_API_KEY has spaces or quotes in it."


@pytest.mark.live
def test_your_key_reaches_the_model() -> None:
    from onboarding_agent.model import Message, ModelClient

    key, base_url = _credentials()
    if not key:
        pytest.skip("OPENAI_API_KEY is not set")
    # Room for the reasoning tokens, which count against the same limit as the answer.
    client = ModelClient(model=MODEL, base_url=base_url, api_key=key, max_output_tokens=2000)

    response = client.complete([Message(role="user", content="Reply with the word OK.")], [])

    assert response.text, f"{MODEL} at {base_url} answered with no text"


def _credentials() -> tuple[str, str]:
    """The key and endpoint as the harness will see them: the environment first, then .env.

    The environment is put back afterwards, so nothing here leaks into the other tests.
    """
    from onboarding_agent.env import load_env

    saved = dict(os.environ)
    try:
        load_env(REPO / "config")
        return (
            os.environ.get("OPENAI_API_KEY", ""),
            os.environ.get("OPENAI_BASE_URL") or VOCAREUM_URL,
        )
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(
            f"git {' '.join(args)} failed in {root}: {result.stderr.strip()}\n"
            f"Clone the service at its tag: git clone --branch {TAG} "
            "https://github.com/udacity/webhook-delivery.git"
        )
    return result.stdout.strip()
