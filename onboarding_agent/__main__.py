"""Command-line entry point: `run` one task, or show a run's `status`.

`run` executes exactly one task and exits. The next task needs a new invocation, so
everything one session hands to the next goes through the run folder.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from onboarding_agent import __version__
from onboarding_agent.checkpoint import Checkpoint
from onboarding_agent.config import ConfigError, load_config
from onboarding_agent.env import load_env
from onboarding_agent.model import ModelError
from onboarding_agent.runner import PROGRESS_FILE, NoWorkRemaining, SessionRunner
from onboarding_agent.runs import NoRunFound, latest_run_dir, new_run_dir
from onboarding_agent.state import CorruptState, ProgressFile, StateError, TaskList

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TASK_FAILED = 2
EXIT_NOTHING_TO_DO = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onboarding-agent",
        description="Map an undocumented service and produce a cited ONBOARDING.md.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every model call")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    run = commands.add_parser("run", help="run exactly one task, then exit")
    run.add_argument("--config", required=True, type=Path, help="path to agent.yaml")
    run.add_argument(
        "--run-dir",
        type=Path,
        help=(
            "the run folder. Default: with --tasks, a new <tasks>_<timestamp> folder "
            "under workspace.runs_dir; without it, the newest run there"
        ),
    )
    run.add_argument("--tasks", type=Path, help="task seed JSON; starts a new run")

    status = commands.add_parser("status", help="show what a run has done so far")
    status.add_argument("--run-dir", type=Path, help="the run to report on")
    status.add_argument(
        "--config",
        type=Path,
        help="without --run-dir, report on the newest run under its runs_dir",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if args.command == "run":
        # Load .env before anything reads OPENAI_API_KEY. The search starts at the
        # config's folder, so the command works from any directory.
        load_env(args.config.resolve().parent)
        return _run(args.config, args.run_dir, args.tasks)
    if args.command == "status":
        return _status(args.run_dir, args.config)
    parser.print_help()
    return EXIT_ERROR


def _default_run_dir(config: Path, tasks: Path | None) -> Path:
    """A new timestamped run when there is a task file to seed it; otherwise the newest."""
    runs_dir = load_config(config).workspace.runs_dir
    return new_run_dir(runs_dir, tasks) if tasks else latest_run_dir(runs_dir)


def _run(config: Path, run_dir: Path | None, tasks: Path | None) -> int:
    try:
        if run_dir is None:
            run_dir = _default_run_dir(config, tasks)
        print(f"run directory: {run_dir}")
        runner = SessionRunner.start(config, run_dir, tasks)
        result = runner.run_one()
    except NoWorkRemaining as exc:
        print(f"no tasks remaining: {exc}")
        return EXIT_NOTHING_TO_DO
    except (CorruptState, StateError) as exc:
        print(f"corrupt state: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (ConfigError, ModelError, NoRunFound, FileExistsError) as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"{result.task_id}: {result.status} in {result.turns} turns ({result.commit[:8]})")
    print(f"tokens: {result.input_tokens} in / {result.output_tokens} out")
    print(result.handoff)
    if result.service_modified:
        # Told to the person running the harness, not to the model, which might try to
        # undo the change with another shell command.
        print(
            f"\nwarning: this session changed the service, which is read-only: "
            f"{', '.join(result.service_modified)}. Restore it before the next session "
            f"(see `git -C {runner.config.workspace.root} status`).",
            file=sys.stderr,
        )
    if result.remaining:
        print(f"\n{result.remaining} task(s) remaining — run this command again to continue.")
    return EXIT_OK if result.status == "done" else EXIT_TASK_FAILED


def _status(run_dir: Path | None, config: Path | None) -> int:
    try:
        if run_dir is None:
            if config is None:
                print("pass --run-dir, or --config to report on the newest run", file=sys.stderr)
                return EXIT_ERROR
            run_dir = latest_run_dir(load_config(config).workspace.runs_dir)
            print(f"run directory: {run_dir}")
        progress = ProgressFile.load(run_dir / PROGRESS_FILE)
        tasks = TaskList.load(run_dir / progress.task_list)
    except (CorruptState, StateError) as exc:
        print(f"corrupt state: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (ConfigError, NoRunFound) as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    for task in tasks.tasks:
        print(f"  [{task.status:>7}] {task.id}  {task.title}")
    print(f"\nsessions run: {progress.session_count}")
    print(f"next: {progress.next_task_id or '(none)'}")
    for line in Checkpoint(run_dir).log():
        print(f"  {line}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
