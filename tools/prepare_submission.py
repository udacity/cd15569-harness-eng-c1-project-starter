"""Write the submission folder from a finished run.

    python tools/prepare_submission.py runs/<run> --fixture ../webhook-delivery

Run it once, after the last session. It copies the three documents the agent wrote into
``submission/`` and writes ``submission/RUN.md``, the record a reviewer reads instead of
running anything: the SHA-256 of ONBOARDING.md, the run's checkpoint log, a table of the
tasks and one of the sessions, the model, and the citation check, run again here.

It refuses only when ONBOARDING.md is missing, or when the copy in the run folder differs
from the version the run last committed: the submitted document has to be the agent's own.
Everything else, such as a failed session or a citation that doesn't resolve, is reported in
RUN.md as it is.

It reads only what the provided code writes, so it works whatever your own code does: the
task list as plain JSON, the run's git history, and the events the provided code emits. Of
the events your loop emits, it counts ``model_call`` and ``tool_result`` and reads nothing
from them but ``tool_result``'s ``ok``. It never reads ``progress.json`` or a commit message.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
CHECKER = TOOLS / "check_citations.py"
DEFAULT_OUT = TOOLS.parent / "submission"
DOCUMENTS = ("ONBOARDING.md", "architecture.md", "discrepancies.md")
RECORD = "RUN.md"
NOT_RECORDED = "not recorded"


class Refused(Exception):
    """The run can't be submitted as it stands."""


class NotARun(Exception):
    """The folder isn't a run folder with its own git history."""


@dataclass
class Commit:
    sha: str
    time: int  # committer time, in seconds since the epoch
    author: str
    files: list[str]
    added: list[str]
    session: str | None = None  # the session this commit is the checkpoint of


@dataclass
class Session:
    id: str
    start: datetime | None = None
    # The session_end event, or else the last event the provided code wrote.
    end: datetime | None = None
    ended: bool = False
    prompt: str | None = None
    model: str | None = None
    outcome: str | None = None
    turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    tool_calls: dict[str, int] = field(default_factory=dict)
    failed_calls: int = 0
    model_call_events: int = 0
    tool_result_events: int = 0
    service_modified: list[str] = field(default_factory=list)
    unreadable_lines: int = 0
    log_committed: bool = True
    touched_onboarding: bool = False  # a tool call named ONBOARDING.md in its arguments
    checkpoint: Commit | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("run", type=Path, help="the run folder, runs/<run>")
    parser.add_argument("--fixture", type=Path, required=True, help="the service's checkout")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="where to write the submission (default: submission/)")
    args = parser.parse_args(argv)

    try:
        summary = prepare(args.run, args.fixture, args.out)
    except NotARun as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(summary)
    return 0


def prepare(run: Path, fixture: Path, out: Path) -> str:
    """Write ``out`` from ``run`` and return a short summary for the terminal."""
    given, run = run, run.resolve()
    if not (run / ".git").exists():
        raise NotARun(f"{given} has no git history of its own. Pass a run folder, runs/<run>.")
    if not _git(run, "rev-parse", "--verify", "--quiet", "HEAD").strip():
        raise NotARun(f"{given} has no commits yet")
    if not fixture.is_dir():
        raise NotARun(f"there is no service at {fixture}. Pass its checkout, such as "
                      "../webhook-delivery.")

    if not (run / "ONBOARDING.md").is_file():
        raise Refused(f"{given} has no ONBOARDING.md. The third task writes it.")
    # Compare blob ids rather than trust `git status`, which an ignore rule can silence.
    committed = _git(run, "rev-parse", "--verify", "--quiet", "HEAD:ONBOARDING.md").strip()
    if not committed:
        raise Refused("ONBOARDING.md was never committed, so it isn't the agent's checkpointed "
                      "output. Run the sessions until the third task commits it.")
    if _git(run, "hash-object", "ONBOARDING.md").strip() != committed:
        raise Refused(
            "ONBOARDING.md differs from the version the run last committed. Submit the "
            f"agent's own document: see the change with `git -C {given} diff ONBOARDING.md`, "
            f"and put it back with `git -C {given} restore ONBOARDING.md`."
        )

    tasks, tasks_problem = _tasks(run)
    sessions = _sessions(run)
    commits = _commits(run)
    _match_checkpoints(sessions, commits)
    last_change = _last_change(run, commits)

    out.mkdir(parents=True, exist_ok=True)
    for name in (*DOCUMENTS, RECORD):
        (out / name).unlink(missing_ok=True)  # never leave a stale copy from an earlier run
    copied: dict[str, str] = {}
    for name in DOCUMENTS:
        copied[name] = _copy_document(run, name, out)

    onboarding = (out / "ONBOARDING.md").read_bytes()
    digest = hashlib.sha256(onboarding).hexdigest()
    check = _citation_check(out, fixture)
    warnings = _warnings(tasks, sessions, commits, last_change, copied, check)

    record = _render(run, digest, tasks, tasks_problem, sessions, commits, last_change, copied,
                     check, warnings)
    (out / RECORD).write_text(record, encoding="utf-8")

    done = sum(1 for t in tasks if t.get("status") == "done")
    lines = [
        f"Wrote {out}: {', '.join(n for n in DOCUMENTS if copied[n] != 'missing')}, {RECORD}",
        f"  ONBOARDING.md sha256 {digest}",
        f"  tasks done: {done} of {len(tasks)}; sessions: {len(sessions)}; "
        f"citation check: {check.verdict}",
    ]
    lines += [f"  check: {w}" for w in warnings]
    return "\n".join(lines)


# -- reading the run -------------------------------------------------------------------------


def _tasks(run: Path) -> tuple[list[dict], str | None]:
    path = run / "tasks.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], "tasks.json is missing"
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"tasks.json can't be read: {exc}"
    entries = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return [], "tasks.json has no list of tasks"
    return [t for t in entries if isinstance(t, dict)], None


def _sessions(run: Path) -> list[Session]:
    """Every session, read from its log as the run committed it.

    A log that was never committed, because its session never reached a checkpoint, is read
    from the run folder instead, and flagged.
    """
    on_disk = {p.name for p in (run / "sessions").glob("*") if p.is_dir()}
    tracked = set(_git(run, "ls-tree", "--name-only", "HEAD", "sessions/").split("\n")) - {""}
    sessions = []
    for name in sorted(on_disk | {Path(t).name for t in tracked}):
        session = Session(id=name)
        path = f"sessions/{name}/events.jsonl"
        committed = subprocess.run(
            ["git", "show", f"HEAD:{path}"], cwd=run, capture_output=True, check=False
        )
        if committed.returncode == 0:
            _read_events(committed.stdout.decode("utf-8", errors="replace"), session)
        else:
            session.log_committed = False
            if (run / path).is_file():
                _read_events((run / path).read_text(encoding="utf-8", errors="replace"), session)
        sessions.append(session)
    return sorted(sessions, key=lambda s: (s.start or datetime.max.replace(tzinfo=UTC), s.id))


# The events the provided code writes. Only these set a session's start and end.
PROVIDED_EVENTS = {"session_start", "tool_call", "session_end", "service_modified"}


def _read_events(text: str, session: Session) -> None:
    first: datetime | None = None
    last: datetime | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            session.unreadable_lines += 1
            continue
        if not isinstance(event, dict):
            session.unreadable_lines += 1
            continue
        name = event.get("event")
        when = _time(event.get("ts"))
        if when is not None and name in PROVIDED_EVENTS:
            first = first or when
            last = when
        if name == "session_start":
            session.start = when or session.start
            session.prompt = _text(event.get("task"))
            session.model = _text(event.get("model"))
        elif name == "tool_call":
            tool = str(event.get("tool"))
            session.tool_calls[tool] = session.tool_calls.get(tool, 0) + 1
            if "ONBOARDING.md" in json.dumps(event.get("arguments"), default=str):
                session.touched_onboarding = True
        elif name == "session_end":
            session.ended = True
            session.end = when or session.end
            session.outcome = _text(event.get("outcome"))
            session.turns = _number(event.get("turns"))
            session.input_tokens = _number(event.get("input_tokens"))
            session.output_tokens = _number(event.get("output_tokens"))
            session.error = _text(event.get("error"))
        elif name == "service_modified":
            paths = event.get("paths")
            session.service_modified += [str(p) for p in paths] if isinstance(paths, list) else []
        elif name == "model_call":
            session.model_call_events += 1
        elif name == "tool_result":
            session.tool_result_events += 1
            if event.get("ok") is False:
                session.failed_calls += 1
    session.start = session.start or first
    session.end = session.end or last


def _commits(run: Path) -> list[Commit]:
    commits = []
    for line in _git(run, "log", "--reverse", "--format=%H%x09%ct%x09%an").splitlines():
        sha, when, author = line.split("\t", 2)
        fields = _git(run, "diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-z",
                      sha).split("\0")
        files, added, index = [], [], 0
        while index + 1 < len(fields):
            status = fields[index]
            width = 3 if status[:1] in ("R", "C") else 2  # renames and copies carry two paths
            path = fields[index + width - 1]
            files.append(path)
            if status == "A":
                added.append(path)
            index += width
        commits.append(Commit(sha, int(when), author, files, added))
    return commits


def _match_checkpoints(sessions: list[Session], commits: list[Commit]) -> None:
    """Give each session the commit that added its own event log.

    A checkpoint stages everything in the run folder, so it adds the log of the session that
    made it. A session that crashed before its checkpoint has its log picked up by the next
    session's, and that commit belongs to the later of the two. Matching on content, not on
    time, means no clock and no rounding decides it. A commit that adds no session's log was
    made some other way: the seed commit, or a commit by hand.
    """
    order = {s.id: i for i, s in enumerate(sessions)}
    by_log = {f"sessions/{s.id}/events.jsonl": s for s in sessions}
    for commit in commits:
        owners = [by_log[p] for p in commit.added if p in by_log and by_log[p].checkpoint is None]
        if owners:
            owner = max(owners, key=lambda s: order[s.id])
            commit.session = owner.id
            owner.checkpoint = commit


def _last_change(run: Path, commits: list[Commit]) -> Commit | None:
    sha = _git(run, "log", "-1", "--format=%H", "--", "ONBOARDING.md").strip()
    return next((c for c in commits if c.sha == sha), None)


def _copy_document(run: Path, name: str, out: Path) -> str:
    """Copy ``name`` from the run's last commit; say where it came from."""
    committed = subprocess.run(
        ["git", "show", f"HEAD:{name}"], cwd=run, capture_output=True, check=False
    )
    if committed.returncode == 0:
        (out / name).write_bytes(committed.stdout)
        blob = _git(run, "rev-parse", "--verify", "--quiet", f"HEAD:{name}").strip()
        if (run / name).is_file() and _git(run, "hash-object", name).strip() != blob:
            return "last commit (the copy in the run folder differs)"
        return "last commit"
    if (run / name).is_file():
        (out / name).write_bytes((run / name).read_bytes())
        return "run folder (never committed)"
    return "missing"


@dataclass
class CitationCheck:
    command: str
    exit_code: int | None
    output: str

    @property
    def verdict(self) -> str:
        return {0: "passed", 1: "failed"}.get(self.exit_code, "could not run")


def _citation_check(out: Path, fixture: Path) -> CitationCheck:
    # Run from where this script was run, with the fixture path as it was given, so RUN.md
    # shows the command you would type yourself.
    document = os.path.relpath(out.resolve() / "ONBOARDING.md")
    command = [sys.executable, str(CHECKER), document, "--fixture", str(fixture)]
    shown = f"python tools/check_citations.py {document} --fixture {fixture}"
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return CitationCheck(shown, None, str(exc))
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return CitationCheck(shown, result.returncode, output)


# -- writing RUN.md --------------------------------------------------------------------------


def _render(
    run: Path,
    digest: str,
    tasks: list[dict],
    tasks_problem: str | None,
    sessions: list[Session],
    commits: list[Commit],
    last_change: Commit | None,
    copied: dict[str, str],
    check: CitationCheck,
    warnings: list[str],
) -> str:
    head = commits[-1].sha if commits else NOT_RECORDED
    models = sorted({s.model for s in sessions if s.model}) or [NOT_RECORDED]
    task_ids = {
        t["session_id"]: str(t.get("id")) for t in tasks if isinstance(t.get("session_id"), str)
    }
    by_prompt: dict[str, list[str]] = {}
    for task in tasks:
        by_prompt.setdefault(str(task.get("prompt")), []).append(str(task.get("id")))

    def task_of(session: Session) -> str:
        if session.id in task_ids:
            return task_ids[session.id]
        candidates = by_prompt.get(session.prompt or "", [])
        return candidates[0] if len(candidates) == 1 else NOT_RECORDED

    out = [
        "# Run record",
        "",
        f"Written by `tools/prepare_submission.py` at {_stamp(datetime.now(UTC))}. "
        "Don't edit this file: it is the record your reviewer checks.",
        "",
        f"- **Run:** `{run.name}`",
        f"- **Last commit:** `{head}`",
        f"- **SHA-256 of ONBOARDING.md:** `{digest}`",
        f"- **Model:** {', '.join(f'`{m}`' for m in models)}",
        "",
        "## Warnings",
        "",
        *([f"- {w}" for w in warnings] or ["None."]),
        "",
        "## ONBOARDING.md",
        "",
    ]
    if last_change is None:
        out.append(f"- **Last changed by:** {NOT_RECORDED}")
    else:
        owner = (
            f"the checkpoint of session `{last_change.session}`"
            if last_change.session
            else "**not a session's checkpoint**"
        )
        out.append(f"- **Last changed by:** commit `{last_change.sha[:12]}` at "
                   f"{_stamp(_from_epoch(last_change.time))}, {owner}")
        # A checkpoint is measured from its own session's end; any other commit from the end
        # of the last session.
        made_by = next((s for s in sessions if s.id == last_change.session), None)
        reference = made_by or next((s for s in reversed(sessions) if s.end), None)
        if reference is not None and reference.end is not None:
            which = f"session `{reference.id}`" if made_by else "the last session"
            gap = last_change.time - int(reference.end.timestamp())
            when = "after" if gap >= 0 else "before"
            out.append(f"- **Committed {when} {which} ended** ({_stamp(reference.end)}): "
                       f"{abs(gap)} seconds")
    out += [f"- **{name} copied from:** {copied[name]}" for name in DOCUMENTS]

    out += ["", "## Tasks", ""]
    if tasks_problem:
        out.append(f"{tasks_problem}.")
    else:
        out += ["| Task | Title | Status | Session |", "|---|---|---|---|"]
        out += [
            _row(t.get("id"), t.get("title"), t.get("status"), t.get("session_id") or "")
            for t in tasks
        ]

    out += ["", "## Sessions", ""]
    if not sessions:
        out.append("No session folders under `sessions/`.")
    else:
        out += [
            "| Session | Task | Outcome | Turns | Tokens in / out | Tool calls | Failed calls "
            "| `model_call` events | `tool_result` events | Checkpoint | Warnings |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in sessions:
            calls = ", ".join(f"{name} {n}" for name, n in sorted(s.tool_calls.items())) or "none"
            tokens = (
                f"{s.input_tokens} / {s.output_tokens}"
                if s.input_tokens is not None and s.output_tokens is not None
                else NOT_RECORDED
            )
            notes = []
            if s.service_modified:
                notes.append(f"changed the service: {', '.join(s.service_modified)}")
            if not s.ended:
                notes.append("no session_end event")
            if s.error:
                notes.append(f"error: {s.error}")
            if s.unreadable_lines:
                notes.append(f"{s.unreadable_lines} unreadable event lines")
            if not s.log_committed:
                notes.append("event log never committed")
            out.append(_row(
                f"`{s.id}`",
                task_of(s),
                s.outcome or NOT_RECORDED,
                NOT_RECORDED if s.turns is None else s.turns,
                tokens,
                calls,
                s.failed_calls if s.tool_result_events else NOT_RECORDED,
                s.model_call_events,
                s.tool_result_events,
                f"`{s.checkpoint.sha[:12]}`" if s.checkpoint else "none",
                "; ".join(notes),
            ))

    out += ["", "## Checkpoint log", "", "| Commit | Time | Author | Files changed | Session |",
            "|---|---|---|---|---|"]
    out += [
        _row(f"`{c.sha[:12]}`", _stamp(_from_epoch(c.time)), c.author,
             ", ".join(f"`{f}`" for f in c.files) or "none",
             f"`{c.session}`" if c.session else "none")
        for c in commits
    ]

    out += ["", "## Citation check", "", f"`{check.command}`: **{check.verdict}**"
            + (f" (exit {check.exit_code})" if check.exit_code is not None else ""), "",
            "```", check.output or "(no output)", "```", ""]
    return "\n".join(out)


def _warnings(
    tasks: list[dict],
    sessions: list[Session],
    commits: list[Commit],
    last_change: Commit | None,
    copied: dict[str, str],
    check: CitationCheck,
) -> list[str]:
    found = []
    if not tasks or any(t.get("status") != "done" for t in tasks):
        found.append("not every task is done")
    if check.verdict != "passed":
        found.append(f"the citation check {check.verdict}")
    if last_change is not None and last_change.session is None:
        found.append("ONBOARDING.md was last changed by a commit no session made")
    if last_change is not None and last_change.session is not None:
        maker = next(s for s in sessions if s.id == last_change.session)
        if not maker.touched_onboarding:
            found.append(f"session {maker.id} committed the last change to ONBOARDING.md "
                         "without any tool call naming it")
    # The first commit is the run's seed; every other one should be a session's checkpoint.
    stray = [c.sha[:12] for c in commits[1:] if c.session is None]
    if stray:
        found.append(f"{len(stray)} commit(s) made by no session: {', '.join(stray)}")
    found += [f"{name} is missing" for name in DOCUMENTS if copied[name] == "missing"]
    found += [f"session {s.id} changed the service" for s in sessions if s.service_modified]
    found += [f"session {s.id}'s event log was never committed"
              for s in sessions if not s.log_committed]
    return found


# -- helpers ---------------------------------------------------------------------------------


def _git(run: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=run, capture_output=True, text=True, check=False)
    if result.returncode != 0 and args[:2] != ("rev-parse", "--verify"):
        raise NotARun(f"git {' '.join(args)} failed in {run}: {result.stderr.strip()}")
    return result.stdout


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _from_epoch(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _stamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _row(*cells: object) -> str:
    def clean(cell: object) -> str:
        return str(cell).replace("|", "\\|").replace("\n", " ")

    return "| " + " | ".join(clean(c) for c in cells) + " |"


if __name__ == "__main__":
    sys.exit(main())
