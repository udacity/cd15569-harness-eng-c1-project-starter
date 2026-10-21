"""The real CLI, as its own OS process, with the model replaced by a script.

    python -m tests.scripted_cli run --config agent.yaml --run-dir run --tasks tasks.json

Tests that run sessions as separate processes cannot hand a process a fake object. So this
swaps ``HarnessConfig.model_client`` for one that replays the JSON script
the config's ``model.name`` points at, then hands over to ``onboarding_agent``'s own
``main``. Everything else — config, state, checkpoints, the event log — is the real thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from onboarding_agent.__main__ import main
from onboarding_agent.config import HarnessConfig
from tests.fakes import ScriptedModel


def _scripted(self: HarnessConfig) -> ScriptedModel:
    return ScriptedModel.from_script_file(Path(self.model.name))


HarnessConfig.model_client = _scripted  # type: ignore[assignment,method-assign]

if __name__ == "__main__":
    sys.exit(main())
