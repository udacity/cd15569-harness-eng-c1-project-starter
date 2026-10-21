"""Git checkpoints in the run folder.

Each session ends with a commit in the run folder, never in the service. The history
shows what every session did, and it is where a corrupt progress file is restored from.
It runs the `git` command rather than using a library, so a run folder is an ordinary
repository you can inspect with `git log`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# The agent's identity, so a run's commits are never attributed to you.
AUTHOR_NAME = "onboarding-agent"
AUTHOR_EMAIL = "onboarding-agent@localhost"


class CheckpointError(RuntimeError):
    """A git operation failed."""


class Checkpoint:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def is_repo(self) -> bool:
        return (self.root / ".git").is_dir()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.is_repo():
            return
        self._git("init", "--quiet", "--initial-branch", "main")
        # Set per repository: on a machine with no global git identity the first commit
        # would fail.
        self._git("config", "user.name", AUTHOR_NAME)
        self._git("config", "user.email", AUTHOR_EMAIL)

    def commit(self, message: str) -> str:
        """Stage everything, commit, and return HEAD's sha. Nothing to commit is not an error."""
        self._git("add", "-A")
        if self._git("status", "--porcelain").strip():
            self._git("commit", "--quiet", "-m", message)
        head = self.last_commit()
        if head is None:
            raise CheckpointError(f"{self.root} has no commits after committing {message!r}")
        return head

    def last_commit(self) -> str | None:
        try:
            return self._git("rev-parse", "HEAD").strip()
        except CheckpointError:
            return None  # no commits yet

    def log(self) -> list[str]:
        if self.last_commit() is None:
            return []
        return self._git("log", "--format=%h %s").splitlines()

    def restore(self, relative_path: str) -> bool:
        """Put a file back to its state at the last commit. False if it was never committed."""
        if self.last_commit() is None:
            return False
        try:
            contents = self._git("show", f"HEAD:{relative_path}")
        except CheckpointError:
            return False
        (self.root / relative_path).write_text(contents)
        return True

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise CheckpointError(
                f"git {' '.join(args)} failed in {self.root}: {result.stderr.strip()}"
            )
        return result.stdout
