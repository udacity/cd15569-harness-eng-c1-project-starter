"""``shell_exec``: run a shell command in the service's folder."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from onboarding_agent.tools.base import ToolError, capped, require, truncate
from onboarding_agent.workspace import Workspace

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 30_000


def _path_tokens(command: str) -> list[str]:
    """The arguments in ``command`` that are absolute, home-relative or ``..`` paths.

    Splits on shell separators, so the ``/`` inside a relative path such as
    ``src/app.py`` is not taken for the start of an absolute one.
    """
    words = re.split(r"[\s|;&<>()]+", command)
    tokens = []
    for word in words:
        stripped = word.strip("\"'")
        if not stripped or stripped.startswith("-"):
            continue
        if stripped.startswith(("/", "~")) or ".." in stripped.split("/"):
            tokens.append(stripped)
    return tokens


class ShellExec:
    name = "shell_exec"
    description = (
        "Run a shell command inside the service being examined, which is the working "
        "directory. Use it to run the test suite and to inspect the tree. Returns combined "
        f"stdout and stderr, capped at {DEFAULT_MAX_BYTES} bytes with an explicit truncation "
        f"marker. Commands are killed after {DEFAULT_TIMEOUT:.0f}s by default.\n"
        "\n"
        "Do not use it to create or change files. The service is read-only, and anything a "
        "command writes lands inside it. Write your own files with file_write.\n"
        "\n"
        "It cannot see files you have written: those live outside the service tree, and "
        "`ls` will not show them. Read them with file_read by bare name instead."
    )

    def __init__(self, workspace: Workspace, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._ws = workspace
        self._max_bytes = max_bytes

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout": {
                    "type": "number",
                    "description": (
                        f"Seconds before the command is killed. Default {DEFAULT_TIMEOUT}."
                    ),
                },
                "max_bytes": {
                    "type": "integer",
                    "description": (
                        f"Output cap in bytes. Defaults to {self._max_bytes}, "
                        "which is also the ceiling."
                    ),
                },
            },
            "required": ["command"],
        }

    # TODO 2.4: Implement _reject_escapes() and call(), shell_exec's guard and runner.
    # Commands run in the service's folder. Refuse one that names a path outside it,
    # stop one that runs too long, and never hide a failing exit code.
    def _reject_escapes(self, command: str) -> None:
        """Refuse a command that names a path outside the service's folder.

        Not a sandbox: a command can still reach other files, through ``python -c`` or a
        path built at run time. It stops the common accidents, such as ``ls ..`` or
        ``cat ../notes``.
        """
        # TODO: Resolve each path-like argument from _path_tokens(): an absolute or
        # home path as it is, anything else against the service's root. Raise a
        # ToolError with a hint if one lands outside the root.

    def call(self, args: dict[str, Any]) -> str:
        command = require(args, "command")
        self._reject_escapes(command)
        timeout = float(args.get("timeout") or DEFAULT_TIMEOUT)
        cap = capped(args.get("max_bytes"), self._max_bytes)

        # TODO: Run the command through the shell in the service's folder, capturing
        # stdout and stderr as text, and stop it after `timeout` seconds. A timeout is
        # a ToolError with a hint.

        # TODO: Join stdout and stderr and cap them with truncate(). If the command
        # failed, start the result with "[exit N]" so the model sees the exit code.
        body = ...
        return body
