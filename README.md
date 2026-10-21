# The Inherited Service

You have just joined the Platform team at **Hexstack** and inherited
`webhook-delivery`, the service that takes events off the ingest endpoint,
queues them, and delivers them to the URLs subscribers have registered, with
retries, per-subscriber rate limiting and a dead-letter queue. Nobody who wrote
it still works here. Its README and docstrings look thorough, and some of what
they say is not true.

Your lead wants an onboarding document the next engineer can trust, where
every claim points at the line of code that backs it. Rather than read the
service by hand, you build an agent harness to do the reading. It maps the
service, checks the documentation against the code, and writes
`ONBOARDING.md`, across three separate sessions.

There is no agent framework here. The harness is what you are learning to
build, so it is written out: the loop, the tools, the configuration and the
state on disk.

## Architecture

```
  tasks/mapping-run.json ──▶ runner ──▶ agent loop ◀──▶ model client ──▶ gpt-5.4-mini
  (three tasks)              (one task   │    ▲         (provided)
                              per run)   ▼    │
                                      tool registry ── file_read, file_write, file_edit,
                                         │             search_code, shell_exec
                                         ▼
                              ../webhook-delivery/     read-only: the service being mapped
                              runs/<run>/              the agent's notes and ONBOARDING.md,
                                                       the task list, the progress file,
                                                       the event logs, git checkpoints
```

- **One task per session.** Each `run` command does one task and exits. The
  next task starts in a new process, so the only thing one session hands the
  next is what it left in the run folder: the task list, the progress file and
  a git checkpoint.
- **Rules live in code.** The turn cap, the token budget, the retry cap, the
  tool allow-list and the output cap are all enforced by the harness, not
  requested in the prompt.
- **The service is read-only.** The agent reads `webhook-delivery`, and the
  file tools only write inside the run folder.
- **Every session is logged.** Each one writes
  `runs/<run>/sessions/<session>/events.jsonl`, one JSON line per event: the
  session's start and end, every model call, and every tool result.

## Project structure

The files you work in are marked with their TODO numbers. Everything else is
provided.

```
config/agent.yaml                   The harness configuration (TODO 5.1-5.4)
tasks/mapping-run.json              The three tasks, one per session
onboarding_agent/
  loop.py                             The agent loop (TODO 1.1-1.2)
  tools/
    base.py                             The tool contract and output cap (TODO 1.3)
    files.py                            file_read, file_write, file_edit (TODO 2.1-2.3)
    shell.py                            shell_exec (TODO 2.4)
    search.py                           search_code (TODO 3.1-3.6)
    registry.py                         The tool allow-list and dispatch (TODO 5.5-5.6)
  state.py                            The task list and the progress file (TODO 4.1-4.6)
  runner.py                           One session: one task, then a checkpoint (TODO 4.7-4.8)
  model.py                            The model client: provided, you call it
  config.py                           Loads and validates config/agent.yaml
  workspace.py                        Keeps the tools inside the service and the run folder
  events.py                           The per-session event log
  checkpoint.py                       Git checkpoints in the run folder
  runs.py                             Run folders under runs/
  env.py                              Loads .env
  __main__.py                         The command line: run and status
tools/check_citations.py            Checks every file:line citation in a document
tests/
  smoke/test_task_0.py ... _5.py      A smoke test for your setup and for each task
  fakes.py                            The scripted model the smoke tests run on
  scripted_cli.py                     The command line with that model, for Task 5's test
```

## Setup

### Prerequisites

- Python 3.11 or newer
- Git
- A Udacity-issued Vocareum API key

### Getting started (TODO 0)

1. Clone this repo and the service **side by side**, with the service at its
   tag. The harness finds the service as `../webhook-delivery`, next to this
   repo.

   ```bash
   git clone https://github.com/udacity/cd15569-harness-eng-c1-project-starter.git
   git clone --branch inherited-service-v1.0 https://github.com/udacity/webhook-delivery.git
   cd cd15569-harness-eng-c1-project-starter
   ```

   If the service has to live somewhere else, point the harness at it instead:
   `export ONBOARDING_FIXTURE=/path/to/webhook-delivery`.

2. Create a virtual environment and install the harness. Keep the environment
   activated whenever you run the agent: the agent runs the service's own tests
   through `shell_exec`, so `pytest` has to be on its PATH.

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[test]"
   ```

   `requirements.txt` pins one set of versions known to work together, if you
   want them exact.

3. Add your API key. The key is never a config field, so it can't end up in a
   commit.

   ```bash
   cp .env.example .env
   ```

   Then set `OPENAI_API_KEY` in `.env`.

4. Check your setup. The first command checks Python, the install, your PATH,
   the service and your key. The second makes one small call to the model, to
   show the key works.

   ```bash
   python -m pytest tests/smoke/test_task_0.py
   python -m pytest tests/smoke/test_task_0.py -m live
   ```

## Project tasks

Work through the tasks in order. Each TODO's comment says what to build and
why, and the docstring under it gives the contract: the inputs, what comes
back, and what is raised. Read the provided modules too. The code you write
calls them, and the exceptions and helpers you need are already defined.

The agent can't run until all five tasks are done, because the command line
needs every part of the harness. Each task has a smoke test instead, which
you can run as soon as that task is done. The smoke tests need no key and
cost nothing: where a model is needed, a scripted one stands in. Anything a
later task builds is replaced by a stand-in too, so a task's test passes once
that task and the ones before it are done.

A smoke test checks the main behaviour of each TODO, not every case. Passing it
means you can move on, not that the task is finished. When one fails, its
message says what to look at.

### Task 1: The agent loop and the tool contract (TODO 1.1-1.3)

Build the loop that calls the model, runs the tools it asks for, and feeds
the results back until the model answers. The token budget and the turn cap
stop a run in code. A tool that fails comes back to the model as an error it
can read, until too many fail in a row. Every tool's output is capped, and a
result that was cut short says so.

```bash
python -m pytest tests/smoke/test_task_1.py
```

### Task 2: The file and shell tools (TODO 2.1-2.4)

Implement `file_read`, `file_write`, `file_edit` and `shell_exec`. `file_read`
numbers every line, so whatever the model quotes it can cite as `path:LINE`.
`file_write` never replaces a file unless asked. `file_edit` fails loudly
whenever an edit would change nothing. `shell_exec` runs commands in the
service's folder and refuses any that name a path outside it.

```bash
python -m pytest tests/smoke/test_task_2.py
```

### Task 3: The search tool's interface (TODO 3.1-3.6)

The file walk and the matcher are provided. You build the interface the model
sees: three output modes, a match cap and a byte cap that say what they left
out, an explicit result when nothing matches, a frame that marks file content
as untrusted, and a description that states the tool's own limits. Write the
description last.

```bash
python -m pytest tests/smoke/test_task_3.py
```

### Task 4: State that survives the process (TODO 4.1-4.8)

Implement the task list and the progress file, the only things a new session
reads. The task list is strict about what it accepts and append-only. The
progress file is checked before it is trusted. Every state file is written
atomically. Then build the runner, which does exactly one task per session,
checkpoints the run folder with git, and recovers from the last checkpoint
when the progress file is corrupt.

```bash
python -m pytest tests/smoke/test_task_4.py
```

### Task 5: The configuration (TODO 5.1-5.6)

Write `config/agent.yaml`: the model, the limits, the tools the agent may use,
and a short list of system prompt rules. `onboarding_agent/config.py` shows
every key the loader accepts and its default. A rule belongs in the prompt
only if the harness doesn't already enforce it in code. Then make the
registry build only the tools on the allow-list, and turn a tool name the
model made up into an error it can act on. Be ready to say why each line of
the config is there.

```bash
python -m pytest tests/smoke/test_task_5.py
```

Task 5's smoke test checks only that each section of the config is set and
that it loads, because what goes in it is your decision. Then it runs the
whole harness the way you will: three sessions as three separate commands,
with your config, a scripted model in place of the real one, and a small
made-up service in place of `webhook-delivery`. Get it passing before your
first real run.

## Running the agent

Run the three tasks as three separate commands. `--tasks` starts a new run in
`runs/mapping-run_<YYYYMMDD_HHMMSS>/`. Without it, the command continues the
newest run.

```bash
python -m onboarding_agent run --config config/agent.yaml --tasks tasks/mapping-run.json
python -m onboarding_agent run --config config/agent.yaml      # session 2
python -m onboarding_agent run --config config/agent.yaml      # session 3
python -m onboarding_agent status --config config/agent.yaml
```

`shell_exec` runs arbitrary commands, so nothing can stop one from writing
into the service. If a session does, the command prints a warning naming the
files. Put the service back before the next session:

```bash
git -C ../webhook-delivery reset --hard inherited-service-v1.0
git -C ../webhook-delivery clean -fd
```

## Before you submit

Check every citation in the document against the service:

```bash
python tools/check_citations.py runs/<run>/ONBOARDING.md --fixture ../webhook-delivery
```

It fails any `file:line` that doesn't resolve, and any citation of the
agent's own notes, such as `architecture.md`: those resolve, but they are not
evidence about the service. Then spot-check the claims against the code
yourself.

**`ONBOARDING.md` must be exactly what the agent wrote.** It is graded as
evidence of your harness, so never edit it by hand. To improve it, change the
harness or the configuration and run the three sessions again.

## License

[License](LICENSE.txt)
