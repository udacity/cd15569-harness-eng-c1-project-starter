"""``search_code``: regular-expression search over the files in the workspace.

The matcher is Python's ``re``; what matters is the interface around it. There are three
output modes, caps on matches and bytes that say what they cut, an explicit result when
nothing matches, and a frame that marks returned file content as data, not instructions.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onboarding_agent.tools.base import ToolError, capped, require
from onboarding_agent.workspace import Workspace

DEFAULT_MAX_MATCHES = 100
DEFAULT_MAX_BYTES = 20_000
MAX_LINE_LENGTH = 300
MODES = ("files_with_matches", "count", "content")

# TODO 3.1: Define UNTRUSTED_HEADER and UNTRUSTED_FOOTER, the markers wrapped around
# every search result, so text inside a searched file reads to the model as data,
# never as instructions to follow.

# Never searched: version-control internals, caches and virtualenvs.
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "node_modules", ".mypy_cache"}


@dataclass
class _Match:
    path: str
    line_no: int
    line: str
    context_before: list[str]
    context_after: list[str]


# SearchCode is the search_code tool. call() checks the arguments, _scan() walks the
# files and collects the matches, and the functions after the class format the result.
# The matcher, the file walk, binary detection and .gitignore handling are given. You
# build the interface around them:
#   3.1  the markers that frame every result as untrusted data (above)
#   3.2  call(): check the mode, clamp the caps, and pick the result
#   3.3  _scan(): count every match but keep only match_cap; _shorten(): cap each line
#   3.4  _nothing(): the result when nothing matches
#   3.5  _render() and _cut(): the body for each mode, the frame, and the cap notes
#   3.6  the description, last, once you know what the tool does
class SearchCode:
    name = "search_code"

    def __init__(
        self,
        workspace: Workspace,
        max_matches: int = DEFAULT_MAX_MATCHES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._ws = workspace
        self._max_matches = max_matches
        self._max_bytes = max_bytes
        # TODO 3.6: Write the description, which is all the model knows about this
        # tool. Write it last, once 3.1-3.5 work. Say that the pattern is a regular
        # expression and when to pass literal=true; list the three modes, cheapest
        # first; state the match, byte and line-length caps by interpolating them,
        # never as literal numbers; and say what is not searched and what an empty
        # result means.
        self.description = ...

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Python regular expression to match against each line. "
                        "Set literal=true to search for this text exactly instead."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search under, relative to the workspace root.",
                },
                "mode": {
                    "type": "string",
                    "enum": list(MODES),
                    "description": (
                        "files_with_matches (paths only), count (paths and counts), or "
                        "content (paths, line numbers and text). Defaults to content."
                    ),
                },
                "context": {
                    "type": "integer",
                    "description": "Lines of context to show either side of a match, in "
                    "content mode. Defaults to 0.",
                },
                "literal": {
                    "type": "boolean",
                    "description": (
                        "Treat the pattern as exact text rather than a regex. Use this "
                        "whenever the pattern contains spaces or punctuation."
                    ),
                },
                "ignore_case": {"type": "boolean", "description": "Case-insensitive match."},
                "glob": {
                    "type": "string",
                    "description": "Only search files whose name matches this glob, e.g. '*.py'.",
                },
                "max_matches": {
                    "type": "integer",
                    "description": f"Match cap. Defaults to {self._max_matches}, which is "
                    "also the ceiling — a larger value is clamped.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": f"Output cap in bytes. Defaults to {self._max_bytes}, "
                    "which is also the ceiling.",
                },
                "respect_gitignore": {
                    "type": "boolean",
                    "description": "Skip files matched by .gitignore. Defaults to true.",
                },
            },
            "required": ["pattern"],
        }

    # TODO 3.2: Implement call(), the tool's entry point. Whatever the arguments, the
    # model must get back something it can act on: a ToolError with a hint, a result
    # that says nothing matched, or the rendered matches.
    def call(self, args: dict[str, Any]) -> str:
        pattern = require(args, "pattern")

        # TODO: Read the mode, defaulting to "content", and raise a ToolError whose
        # hint lists MODES when it is not one of them.

        matcher = _compile(pattern, literal=bool(args.get("literal")),
                           ignore_case=bool(args.get("ignore_case")))
        root = self._ws.resolve(str(args.get("path") or "."))
        if not root.is_dir():
            raise ToolError(
                f"{args.get('path')!r} is not a directory",
                hint="pass a directory to search under, or omit `path` for the whole workspace",
            )

        # TODO: Resolve the match cap and the byte cap with capped(), so a call can
        # lower the configured ceilings but never raise them.
        match_cap = ...

        context = max(0, int(args.get("context") or 0))
        ignored = _gitignore(self._ws.root) if args.get("respect_gitignore", True) else []

        matches, total, files = self._scan(
            root, matcher, match_cap, context, str(args.get("glob") or ""), ignored
        )

        # TODO: Return _nothing() when there are no matches, and _render() otherwise.

    # TODO 3.3: Finish _scan(), which walks the files and collects the matches, and
    # _shorten(), which caps each line it keeps. The walk and binary detection are
    # given; you add the bookkeeping that makes the match cap honest, so the result
    # can say how many matches it left out.
    def _scan(
        self,
        root: Path,
        matcher: re.Pattern[str],
        match_cap: int,
        context: int,
        glob: str,
        ignored: list[str],
    ) -> tuple[list[_Match], int, dict[str, int]]:
        matches: list[_Match] = []
        counts: dict[str, int] = {}
        total = 0
        workspace_root = self._ws.root.resolve()

        for path in sorted(root.rglob("*")):
            if not path.is_file() or _skipped(path, workspace_root, glob, ignored):
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:  # a NUL byte in the first 8 KB means a binary file
                continue
            rel = str(path.relative_to(workspace_root))
            lines = raw.decode("utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines):
                if not matcher.search(line):
                    continue
                # TODO: Count every match, in `total` and in `counts` for this
                # file. Keep it in `matches` only while there are fewer than
                # match_cap, as a _Match whose line and `context` lines either
                # side all go through _shorten().
        return matches, total, counts


def _compile(pattern: str, *, literal: bool, ignore_case: bool) -> re.Pattern[str]:
    flags = re.IGNORECASE if ignore_case else 0
    if literal:
        return re.compile(re.escape(pattern), flags)
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(
            f"{pattern!r} is not a valid regular expression: {exc}",
            hint="pass literal=true to search for this text exactly, or escape the "
            "special characters",
        ) from exc


def _skipped(path: Path, root: Path, glob: str, ignored: list[str]) -> bool:
    rel = path.relative_to(root)
    if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
        return True
    if rel.name.startswith("."):
        return True
    if glob and not fnmatch.fnmatch(rel.name, glob):
        return True
    return _is_ignored(rel, ignored)


def _gitignore(root: Path) -> list[str]:
    """Patterns from the workspace's root ``.gitignore``; matching paths are skipped.

    Nested ``.gitignore`` files and ``!`` negation are not supported.
    """
    path = root.resolve() / ".gitignore"
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(errors="replace").splitlines()
        if line.strip() and not line.startswith(("#", "!"))
    ]


def _is_ignored(rel: Path, patterns: list[str]) -> bool:
    for pattern in patterns:
        cleaned = pattern.rstrip("/")
        if fnmatch.fnmatch(rel.name, cleaned) or fnmatch.fnmatch(str(rel), cleaned):
            return True
        if any(fnmatch.fnmatch(part, cleaned) for part in rel.parts[:-1]):
            return True
    return False


def _shorten(line: str) -> str:
    """``line``, cut to ``MAX_LINE_LENGTH`` characters with a marker if it was longer."""
    # TODO: Return a line of up to MAX_LINE_LENGTH characters unchanged. Cut a longer
    # one to MAX_LINE_LENGTH and append a marker naming how many characters were
    # dropped, such as "… [line truncated, 120 chars]".


# TODO 3.4: Implement _nothing(), the result when no line matches. An empty string
# reads to the model like a call that failed, and it retries the same search.
def _nothing(pattern: str, args: dict[str, Any]) -> str:
    """The result when nothing matches: says so plainly, and suggests what to try instead."""
    # TODO: Name the pattern and where it was searched, and say if it was read as a
    # regex. Say that the same search will give the same answer, and suggest a
    # shorter pattern, literal=true, or another directory.


# TODO 3.5: Implement _render() and _cut() below it, which turn the matches into the
# tool's result: the body for the chosen mode, inside the untrusted-content frame,
# plus a note for anything a cap cut.
def _render(
    matches: list[_Match],
    mode: str,
    total: int,
    counts: dict[str, int],
    match_cap: int,
    byte_cap: int,
) -> str:
    """Format the matches for ``mode`` inside the frame, with a note for anything cut."""
    # TODO: Build the body. files_with_matches lists the paths in `counts`, count
    # gives "path: N" for each, and content renders each match with _render_match().

    # TODO: In content mode, if the match cap dropped matches, add a note naming the
    # cap and how many matches were suppressed.

    # TODO: Wrap the body between UNTRUSTED_HEADER and UNTRUSTED_FOOTER. If the
    # result is over byte_cap, shorten it with _cut() and add a note naming the bytes
    # suppressed. Return the result with the notes after it, one per line.


def _render_match(match: _Match) -> str:
    """One match, with its context lines, as ``path:line:text`` lines."""
    lines = [*match.context_before, match.line, *match.context_after]
    first = match.line_no - len(match.context_before)
    return "\n".join(f"{match.path}:{first + offset}:{text}" for offset, text in enumerate(lines))


def _cut(body: str, byte_cap: int) -> tuple[str, int]:
    """Trim ``body`` so the framed result fits ``byte_cap``; return it and the bytes cut."""
    # TODO: Work out how many bytes the frame leaves for the body within byte_cap. Keep
    # that many bytes of the body without splitting a character, and return the framed
    # result with the number of bytes dropped.
