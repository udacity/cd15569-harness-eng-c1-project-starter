"""Run one session: one task, then stop.

The runner connects the config, the model client, the tools, the loop and the state. It
runs a single task even when more are pending, so every later task starts from whatever
the run folder holds. It verifies the progress file before using it, and restores it
from the last git checkpoint if it is corrupt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from onboarding_agent.checkpoint import Checkpoint
from onboarding_agent.config import HarnessConfig, load_config
from onboarding_agent.events import EventLog
from onboarding_agent.loop import AgentLoop, RunAborted
from onboarding_agent.model import ModelError
from onboarding_agent.state import (
    CorruptState,
    Progress,
    ProgressFile,
    Task,
    TaskList,
    tasks_from_seed,
    unique,
)
from onboarding_agent.tools.registry import ToolRegistry
from onboarding_agent.workspace import Workspace

logger = logging.getLogger(__name__)

TASKS_FILE = "tasks.json"
PROGRESS_FILE = "progress.json"


class NoWorkRemaining(RuntimeError):
    """Every task is done. Not a failure — the run is finished."""


@dataclass(frozen=True)
class SessionResult:
    task_id: str
    status: str
    turns: int
    input_tokens: int
    output_tokens: int
    commit: str
    handoff: str
    remaining: int
    run_dir: Path
    # Files in the service that changed during the session. Reported, not undone.
    service_modified: tuple[str, ...] = ()


class SessionRunner:
    def __init__(self, config: HarnessConfig, run_dir: Path) -> None:
        self.config = config
        self.run_dir = Path(run_dir)
        self.checkpoint = Checkpoint(self.run_dir)

    @classmethod
    def start(
        cls, config_path: Path, run_dir: Path, task_seed: Path | None = None
    ) -> SessionRunner:
        """Open a run folder, creating it and seeding its task list on first use."""
        runner = cls(load_config(config_path), run_dir)
        runner.run_dir.mkdir(parents=True, exist_ok=True)
        runner.checkpoint.init()
        if not (runner.run_dir / TASKS_FILE).exists():
            if (runner.run_dir / PROGRESS_FILE).exists():
                raise CorruptState(
                    f"{runner.run_dir} has a {PROGRESS_FILE} but no {TASKS_FILE}. Re-seeding "
                    "would overwrite the progress of a run whose plan is missing; refusing "
                    "rather than starting over on top of it"
                )
            if task_seed is None:
                raise CorruptState(
                    f"{runner.run_dir} has no {TASKS_FILE} and no task seed was given; "
                    "pass --tasks to start a new run"
                )
            tasks = TaskList.create(runner.run_dir / TASKS_FILE, tasks_from_seed(task_seed))
            ProgressFile.write(
                runner.run_dir / PROGRESS_FILE,
                Progress(
                    task_list=TASKS_FILE,
                    next_task_id=tasks.tasks[0].id,
                    handoff="Run not yet started.",
                ),
            )
            runner.checkpoint.commit("run: seed task list")
        return runner

    # TODO 4.7: Implement load_progress(). A corrupt progress file is never used:
    # restore it from the last git checkpoint, or stop the run if there is no
    # checkpoint to restore it from.
    def load_progress(self) -> Progress:
        """Load the progress file, restoring it from the last checkpoint if it is corrupt."""
        path = self.run_dir / PROGRESS_FILE

        # TODO: Load the progress file. On CorruptState, log a warning, restore
        # PROGRESS_FILE with self.checkpoint.restore(), and load it again. If there
        # was nothing to restore, raise CorruptState saying so.

    # TODO 4.8: Implement run_one(), one session. It runs exactly one task, even when
    # more are waiting, and leaves everything the next session needs in the run
    # folder: the task list, the progress file and a git checkpoint.
    def run_one(self) -> SessionResult:
        """Run the next outstanding task, record the outcome, checkpoint, and return."""
        # TODO: Load the progress file, then the task list it names, and take the next
        # outstanding task. If there is none, raise NoWorkRemaining.
        progress = ...
        tasks = ...
        task = ...

        events = EventLog.for_session(self.run_dir / "sessions")
        workspace = Workspace(root=self.config.workspace.root, writable_root=self.run_dir)
        tasks.update(task.id, status="running", session_id=events.session_id)

        before = workspace.service_changes()
        try:
            result, status, note = self._execute(task, progress, workspace, events)
            after = workspace.service_changes()
            changed = sorted(after - before) if before is not None and after is not None else []
            if changed:
                events.emit("service_modified", paths=changed)
        finally:
            events.close()

        artifacts = self._artifacts_since(progress)

        # TODO: If the task finished but left any file in task.expects unwritten, mark
        # it failed, with a note naming the missing files.

        # TODO: Record the outcome in the task list: the status, the artifacts, the
        # note, and completed_at (the session id, or None if the task failed).

        handoff = self._handoff(task, status, result, note, artifacts, tasks)

        # TODO: Write the progress file for the next session: the next task's id, the
        # completed task ids (adding this one if it is done), the last commit, the
        # handoff, and the session count plus one. Then commit a checkpoint.
        commit = ...

        return SessionResult(
            task_id=task.id,
            status=status,
            turns=result[0],
            input_tokens=result[1],
            output_tokens=result[2],
            commit=commit,
            handoff=handoff,
            remaining=tasks.remaining(),
            run_dir=self.run_dir,
            service_modified=tuple(changed),
        )

    def _execute(
        self, task: Task, progress: Progress, workspace: Workspace, events: EventLog
    ) -> tuple[tuple[int, int, int], str, str]:
        loop = AgentLoop(
            self.config.model_client(),
            ToolRegistry.from_config(self.config, workspace),
            turn_cap=self.config.limits.turn_cap,
            system_prompt=self._prompt(task, progress, workspace),
            retry_cap=self.config.limits.retry_cap,
            token_budget=self.config.limits.token_budget,
            events=events,
        )
        try:
            run = loop.run(task.prompt)
        except (RunAborted, ModelError) as exc:
            # The task fails, and the next session runs it again.
            return (0, 0, 0), "failed", f"aborted: {exc}"
        return (run.turns, run.input_tokens, run.output_tokens), "done", run.final_text.strip()

    def _prompt(self, task: Task, progress: Progress, workspace: Workspace) -> str:
        """The system prompt: the config's rules, the file layout, the files the task must
        write, and the previous session's handoff."""
        # The layout comes from the Workspace, so it matches what resolve() enforces.
        parts = [self.config.system_prompt.render(), workspace.describe()]
        if task.expects:
            # Models tend to put the finished document in their final message instead of
            # writing the file, so the prompt says where the deliverable must go.
            files = ", ".join(task.expects)
            parts.append(
                f"This task is not finished until {files} exists on disk. Write it with "
                "file_write. Your final message is a short summary for the next session — "
                "it is not the deliverable, and putting the document there instead of "
                "writing it means the task failed."
            )
        if progress.handoff and progress.session_count:
            parts.append(
                f"Handoff from the previous session (session {progress.session_count}):\n"
                f"{progress.handoff}"
            )
        return "\n\n".join(parts)

    def _artifacts_since(self, progress: Progress) -> list[str]:
        """Every file in the run folder except the state files, session logs and git."""
        skip = {TASKS_FILE, PROGRESS_FILE}
        return sorted(
            str(p.relative_to(self.run_dir))
            for p in self.run_dir.rglob("*")
            if p.is_file()
            and ".git" not in p.parts
            and "sessions" not in p.parts
            and p.name not in skip
        )

    def _handoff(
        self,
        task: Task,
        status: str,
        result: tuple[int, int, int],
        note: str,
        artifacts: list[str],
        tasks: TaskList,
    ) -> str:
        """The note for the next session: what ran, what it wrote, and what comes next."""
        remaining = tasks.next_pending()
        lines = [
            f"Session ran {task.id} ({task.title}) — {status} in {result[0]} turns.",
            (
                f"Files written so far: {', '.join(artifacts)}. Read them with file_read "
                "using the bare name — no directory prefix; they are not inside the "
                "service tree and `ls` will not show them."
                if artifacts
                else "No files were written."
            ),
            f"What it concluded: {note or '(nothing recorded)'}",
        ]
        if status == "failed":
            lines.append(f"{task.id} did not complete and will be retried next session.")
        elif remaining:
            lines.append(f"Next: {remaining.id} — {remaining.title}.")
        else:
            lines.append("No tasks remain.")
        return "\n".join(lines)
