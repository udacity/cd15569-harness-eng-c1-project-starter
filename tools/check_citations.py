"""Check every `file:line` citation in a markdown file against the service's checkout.

    python3 tools/check_citations.py runs/<run>/ONBOARDING.md --fixture <path> [--tag TAG]

Exits non-zero if any citation does not resolve. A citation of the agent's own notes,
such as architecture.md, also fails: it resolves, but it is not evidence about the service.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# `path/to/file.py:12` or `docs/setup.md:3-5`, optionally inside backticks.
CITATION = re.compile(
    r"`?([A-Za-z0-9_./-]+\.(?:py|md|txt|toml|cfg|ini|yaml|yml)):(\d+)(?:-(\d+))?`?"
)

# A citation with something other than a line number after the colon, such as
# `README.md:??` or `app.py:L12`; these fail. `::` is excluded because
# `tests/test_x.py::test_name` is a pytest node id, not a citation.
MALFORMED = re.compile(
    r"`?([A-Za-z0-9_./-]+\.(?:py|md|txt|toml|cfg|ini|yaml|yml)):(?![\d:\s])([^\s`),;]+)`?"
)

# Files the agent writes itself. A citation into one of these is circular: it looks like
# evidence and is only a pointer back to the claim.
SELF_REFERENTIAL = {"onboarding.md", "architecture.md", "discrepancies.md", "notes.md",
                    "progress.json", "tasks.json"}


@dataclass(frozen=True)
class Citation:
    raw: str
    path: str
    start: int
    end: int
    source_line: int


@dataclass(frozen=True)
class Problem:
    citation: Citation
    reason: str


def extract(markdown: str) -> list[Citation]:
    """Every citation, including malformed ones, which carry line 0 — never a real line."""
    found: list[Citation] = []
    for lineno, line in enumerate(markdown.splitlines(), start=1):
        for match in CITATION.finditer(line):
            start = int(match.group(2))
            end = int(match.group(3) or start)
            found.append(
                Citation(match.group(0), match.group(1), start, end, lineno)
            )
        for match in MALFORMED.finditer(line):
            found.append(Citation(match.group(0), match.group(1), 0, 0, lineno))
    return found


def check(
    citations: list[Citation], fixture: Path, warnings: list[Problem] | None = None
) -> list[Problem]:
    """Unresolvable citations. Pass ``warnings`` to also collect non-fatal notes."""
    problems: list[Problem] = []
    if warnings is None:
        warnings = []
    for citation in citations:
        if citation.start == 0:
            problems.append(Problem(citation, "no line number: a citation needs file:line"))
            continue
        name = Path(citation.path).name.lower()
        if name in SELF_REFERENTIAL:
            problems.append(
                Problem(citation, "circular: cites the agent's own output, not the service")
            )
            continue
        target = fixture / citation.path
        if not target.is_file():
            # A bare filename resolves if exactly one file in the tree has that name. That
            # is reported as a warning, not a failure.
            matches = [
                m for m in fixture.rglob(Path(citation.path).name)
                if m.is_file() and ".git" not in m.parts
            ]
            if len(matches) == 1:
                warnings.append(
                    Problem(
                        citation,
                        f"unqualified path; resolved to {matches[0].relative_to(fixture)}",
                    )
                )
                target = matches[0]
            else:
                reason = (
                    f"ambiguous: {len(matches)} files named {Path(citation.path).name}"
                    if matches
                    else f"no such file under {fixture}"
                )
                problems.append(Problem(citation, reason))
                continue
        total = len(target.read_text(errors="replace").splitlines())
        if citation.start < 1 or citation.end > total:
            problems.append(
                Problem(citation, f"line out of range: {citation.path} has {total} lines")
            )
        elif citation.end < citation.start:
            problems.append(Problem(citation, "line range runs backwards"))
    return problems


def verify_tag(fixture: Path, tag: str) -> str | None:
    """None if the checkout is at ``tag`` with no local changes; otherwise what is wrong."""
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=fixture, capture_output=True, text=True, check=False
        )

    if git("rev-parse", "--git-dir").returncode != 0:
        return f"{fixture} is not a git checkout, so the pinned tag cannot be verified"
    head = git("rev-parse", "HEAD").stdout.strip()
    tagged = git("rev-list", "-n", "1", tag).stdout.strip()
    if not tagged:
        return f"{fixture} has no tag {tag!r}"
    if head != tagged:
        return f"{fixture} is at {head[:8]}, not {tag} ({tagged[:8]})"
    if diff := git("diff", "--stat", tag).stdout.strip():
        return f"{fixture} has uncommitted changes against {tag}:\n{diff}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, help="markdown file whose citations to check")
    parser.add_argument("--fixture", type=Path, required=True, help="fixture checkout root")
    parser.add_argument("--tag", default="inherited-service-v1.0", help="pinned fixture tag")
    parser.add_argument("--skip-tag-check", action="store_true")
    args = parser.parse_args(argv)

    if not args.document.is_file():
        print(f"no such document: {args.document}", file=sys.stderr)
        return 2

    if not args.skip_tag_check and (drift := verify_tag(args.fixture, args.tag)):
        print(f"FIXTURE: {drift}", file=sys.stderr)
        return 2

    citations = extract(args.document.read_text())
    if not citations:
        print(f"{args.document}: no file:line citations at all", file=sys.stderr)
        return 1

    warnings: list[Problem] = []
    problems = check(citations, args.fixture, warnings)
    for warning in warnings:
        print(
            f"{args.document}:{warning.citation.source_line}: "
            f"{warning.citation.raw} — {warning.reason}"
        )
    for problem in problems:
        print(
            f"{args.document}:{problem.citation.source_line}: "
            f"{problem.citation.raw} — {problem.reason}",
            file=sys.stderr,
        )
    resolved = len(citations) - len(problems)
    print(f"{resolved}/{len(citations)} citations resolve against {args.fixture}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
