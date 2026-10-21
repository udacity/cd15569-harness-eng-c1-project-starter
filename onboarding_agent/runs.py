"""Run folders: one per run, under the config's ``workspace.runs_dir``.

A run folder is ``<tasks>_<YYYYMMDD_HHMMSS>/``, named after the task file that started it.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

STAMP_FORMAT = "%Y%m%d_%H%M%S"
BASELINE_SUFFIX = "_baseline"
_STAMPED = re.compile(r"_\d{8}_\d{6}$")


class NoRunFound(LookupError):
    """There is no run to continue, and no task file to start one from."""


def new_run_dir(runs_dir: Path, tasks: Path, now: datetime | None = None) -> Path:
    """A fresh folder name for a run seeded from ``tasks``. Not created here."""
    stamp = (now or datetime.now()).strftime(STAMP_FORMAT)
    run_dir = Path(runs_dir) / f"{Path(tasks).stem}_{stamp}"
    if run_dir.exists():
        # Refuse, rather than share a folder with a run started in the same second.
        raise FileExistsError(f"{run_dir} already exists; start the new run a second later")
    return run_dir


def latest_run_dir(runs_dir: Path) -> Path:
    """The newest timestamped run: what ``run`` without ``--run-dir`` continues.

    Only timestamped folders count, so a renamed copy such as ``<tasks>_baseline/`` is
    never resumed.
    """
    stamped = [
        p for p in Path(runs_dir).glob("*_*") if p.is_dir() and _STAMPED.search(p.name)
    ]
    if not stamped:
        raise NoRunFound(
            f"no runs under {runs_dir}; pass --tasks to start one"
        )
    # Compare the timestamps alone; the task-file prefix would otherwise sort first.
    return max(stamped, key=lambda p: p.name[-15:])
