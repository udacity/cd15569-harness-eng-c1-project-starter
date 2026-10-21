"""File tools: ``file_read``, ``file_write`` and ``file_edit``.

``shell_exec`` could read and write files too, but shell quoting mangles markdown, ``cat``
has no output cap, and these tools can be allowed or denied one at a time.
"""

from __future__ import annotations

from typing import Any

from onboarding_agent.tools.base import ToolError, capped, require, truncate
from onboarding_agent.workspace import Workspace

DEFAULT_MAX_BYTES = 30_000


class FileRead:
    name = "file_read"

    def __init__(self, workspace: Workspace, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._ws = workspace
        self._max_bytes = max_bytes
        self.description = (
            "Read a UTF-8 text file from the workspace. Returns its contents with a line "
            "number on every line, in the form `LINE|text`, so any line you quote can be "
            "cited as path:LINE. Output is capped at "
            f"{max_bytes} bytes; if the file is larger the result ends with an explicit "
            "truncation marker naming how many bytes were suppressed."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root."},
                "max_bytes": {
                    "type": "integer",
                    "description": (
                        f"Output cap in bytes. Defaults to {self._max_bytes}, which is also "
                        "the ceiling — a larger value is clamped, not honoured."
                    ),
                },
            },
            "required": ["path"],
        }

    def _not_found_hint(self, requested: str) -> str:
        """List the files this run has written, which the agent cannot see with ``ls``."""
        written = self._ws.written_files()
        if written:
            return (
                f"files written by this run, readable by bare name: {', '.join(written)}. "
                "Everything else is a path inside the service, relative to its root."
            )
        return "check the path with shell_exec `ls`, or search for the filename first"

    # TODO 2.1: Implement file_read's call(). Return the file with a number on every
    # line, so whatever the model quotes it can cite as path:LINE, capped with
    # truncate(). A missing path or a folder is a ToolError with a hint.
    def call(self, args: dict[str, Any]) -> str:
        path = self._ws.resolve(require(args, "path"))
        cap = capped(args.get("max_bytes"), self._max_bytes)

        # TODO: Raise a ToolError when the path does not exist, with _not_found_hint()
        # as its hint, and another when it is a folder. Otherwise read the file, number
        # its lines with _numbered(), and cap the result with truncate().


def _numbered(text: str) -> str:
    """Prefix each line with its number, as ``N|text``, so anything quoted can be cited."""
    # TODO: Number the lines from 1, as "N|text", right-aligning the numbers to the
    # width of the largest one.


class FileWrite:
    name = "file_write"
    description = (
        "Write a UTF-8 text file into the run folder. Creates parent "
        "directories as needed and reports how many bytes were written.\n"
        "\n"
        "Pass append=true to add to the end of a file; if the file does not exist yet, "
        "append=true creates it, so there is no need to create it first. A file that "
        "already exists is never replaced unless you pass overwrite=true, so a second write "
        "cannot silently erase the first. Record a finding as soon as you have verified it, "
        "while the evidence is still in front of you — a file assembled at the end of a long "
        "session is written from memory, and detail read many turns ago is the detail that "
        "goes missing."
    )

    def __init__(self, workspace: Workspace) -> None:
        self._ws = workspace

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the run folder."},
                "content": {"type": "string", "description": "Text to write."},
                "append": {
                    "type": "boolean",
                    "description": "Add to the end of the file rather than replacing it.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace the file if it already exists. Without this, "
                    "writing to an existing file is refused.",
                },
            },
            "required": ["path", "content"],
        }

    # TODO 2.2: Implement file_write's call(). Never replace an existing file unless
    # the model asks with overwrite=true: a silent overwrite loses whatever the file
    # held. append=true adds to a file, and creates it if it does not exist yet.
    def call(self, args: dict[str, Any]) -> str:
        path = self._ws.resolve(require(args, "path"), for_write=True)
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError(
                "missing required argument 'content'",
                hint="pass the full text to write as a string",
            )

        # TODO: Raise a ToolError when the file already exists and neither append nor
        # overwrite was passed. Name its size, and give both options in the hint.

        # TODO: Write the file, creating its folders. When appending, first add a
        # newline if the existing text does not end with one. Say whether it wrote,
        # appended or replaced, with the bytes written and the file's new size.


class FileEdit:
    name = "file_edit"
    description = (
        "Replace an exact string in a file. The 'old' string must appear exactly once. "
        "Fails loudly if it does not match, matches more than once, or would leave the file "
        "unchanged — a silent no-op is worse than an error, because the agent reports success."
    )

    def __init__(self, workspace: Workspace) -> None:
        self._ws = workspace

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root."},
                "old": {
                    "type": "string",
                    "description": "Exact text to replace, including whitespace. Must be unique.",
                },
                "new": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old", "new"],
        }

    # TODO 2.3: Implement file_edit's call(), which replaces exactly one occurrence
    # of `old` with `new`. Every way an edit can change nothing must raise a
    # ToolError, because a silent no-op reads to the model as success.
    def call(self, args: dict[str, Any]) -> str:
        path = self._ws.resolve(require(args, "path"), for_write=True)
        old = require(args, "old")
        new = args.get("new")
        if not isinstance(new, str):
            raise ToolError("'new' must be a string", hint="pass the replacement text")
        if not path.exists():
            raise ToolError(f"no such file: {args['path']}", hint="check the path first")

        # TODO: Read the file and count the occurrences of `old`. Raise a ToolError
        # with a hint when it matches nothing, matches more than once, or `new` is
        # the same as `old`. Otherwise write the file with the one occurrence
        # replaced, and say so.
