"""Where the agent may read and write.

Every file tool resolves its paths here. Reads are confined to the service being mapped,
and writes go to the run folder, so the service is read-only in code rather than only in
the prompt. This keeps the tools inside their folders; it is not a sandbox.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from onboarding_agent.tools.base import ToolError


@dataclass(frozen=True)
class Workspace:
    """A folder the agent may read, and a separate one it may write to.

    With ``writable_root`` unset, the whole of ``root`` is writable.
    """

    root: Path
    writable_root: Path | None = None

    def resolve(self, candidate: str, *, for_write: bool = False) -> Path:
        """Resolve ``candidate`` for reading, or for writing with ``for_write``.

        Raises ToolError for a path outside the allowed folder. Symlinks are resolved
        first, so a link cannot lead outside.
        """
        real_root = self.root.resolve()
        writable = self.writable_root.resolve() if self.writable_root else None

        if for_write:
            # A write to a name that exists in the service is refused, not redirected to
            # the run folder, where the agent would believe it had edited the service.
            shadowed = (real_root / candidate).resolve()
            if writable is not None and _within(shadowed, real_root) and shadowed.exists():
                # The hints here and below name no paths: a model shown a folder's path
                # tends to copy it into its next write.
                raise ToolError(
                    f"{candidate!r} belongs to the service being mapped, which is read-only",
                    hint=(
                        "put your findings in a file of your own with a different name; "
                        "file_write paths are already inside the run folder, so pass a bare name"
                    ),
                )
            # Every other write path is relative to the run folder.
            base = writable or real_root
            target = (base / candidate).resolve()
            if not _within(target, base):
                raise ToolError(
                    f"path {candidate!r} is not writable",
                    hint=(
                        "pass a bare name, or a path inside the run folder; nothing else is "
                        "writable"
                    ),
                )
            return target

        target = (real_root / candidate).resolve()
        if _within(target, real_root) and target.exists():
            return target
        if writable is not None:
            # Not in the service: try the run folder, so the agent can read its own notes.
            # The bare-name fallback is there because the agent cannot list the run folder
            # and often guesses a directory prefix that does not exist.
            candidates = (
                (writable / candidate).resolve(),
                target,
                (writable / Path(candidate).name).resolve(),
            )
            for attempt in candidates:
                if _within(attempt, writable) and attempt.exists():
                    return attempt
        if _within(target, real_root):
            return target  # missing; the tool reports "no such file" with the real path
        raise ToolError(
            f"path {candidate!r} resolves outside the workspace",
            hint="use a path relative to the service root, or the bare name of a file you wrote",
        )

    def describe(self) -> str:
        """The file layout, as the model is told it at the start of every session.

        Kept beside ``resolve()`` so the two change together. It names no paths, for the
        same reason the error hints don't.
        """
        if self.writable_root is None:
            return "Every path is relative to the workspace, which you may read and write."
        return (
            "Two places matter. The service you are mapping is read-only. file_read and "
            "search_code read it, and shell_exec runs inside it, including its test suite, so "
            "anything a shell command writes lands in the service. Never create or change "
            "files with shell_exec. Your own notes live in a separate run folder. Write them "
            "with file_write (append=true creates the file if it does not exist yet) and read "
            "them back by bare name. ls and shell_exec cannot see them."
        )

    def service_changes(self) -> frozenset[str] | None:
        """Paths `git status` reports as changed in the service, or None if it is not a repo.

        ``shell_exec`` can write into the service and nothing stops it, so the runner
        compares this before and after each session to notice when it happens. Ignored
        files such as ``.pytest_cache/`` do not count.
        """
        root = self.root.resolve()
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True
        )
        # Only when the service is the repository root; inside a larger repo, `git status`
        # would report that repo's changes too.
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
            return None
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True
        )
        return frozenset(line[3:] for line in status.stdout.splitlines() if line.strip())

    def written_files(self) -> list[str]:
        """What this run has produced so far, by bare name."""
        if self.writable_root is None or not self.writable_root.is_dir():
            return []
        return sorted(
            p.name
            for p in self.writable_root.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.suffix in {".md", ".txt", ".json"}
        )


FIXTURE_ROOT_VAR = "ONBOARDING_FIXTURE"

# Where to look for the service's checkout, relative to this repo.
_KNOWN_LAYOUTS = (
    # `git clone https://github.com/udacity/webhook-delivery` beside this repo.
    Path("../webhook-delivery"),
)

_CHECKOUT = Path(__file__).resolve().parent.parent

# A file only the service has, to confirm that a candidate folder is its checkout.
_FIXTURE_MARKER = Path("webhooks/delivery.py")


def _is_fixture(candidate: Path) -> bool:
    return (candidate / _FIXTURE_MARKER).is_file()


def fixture_root() -> Path | None:
    """Find the service's checkout, or return None if there isn't one.

    ``ONBOARDING_FIXTURE`` wins if it is set; otherwise a ``webhook-delivery`` clone beside
    this repo.
    """
    override = os.environ.get(FIXTURE_ROOT_VAR)
    if override:
        candidate = Path(override).expanduser()
        return candidate.resolve() if candidate.is_dir() else None
    for relative in _KNOWN_LAYOUTS:
        candidate = (_CHECKOUT / relative).resolve()
        if _is_fixture(candidate):
            return candidate
    return None


def fixture_hint() -> str:
    """The message for when the service's checkout cannot be found."""
    return (
        f"fixture checkout not found. Clone https://github.com/udacity/webhook-delivery "
        f"beside this repo, or export {FIXTURE_ROOT_VAR}=/path/to/checkout"
    )


def _within(target: Path, root: Path) -> bool:
    return target == root or root in target.parents
