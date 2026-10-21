"""The tool registry: which tools the model gets, their schemas, and dispatch by name."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from onboarding_agent.tools.base import Tool, ToolError
from onboarding_agent.tools.files import DEFAULT_MAX_BYTES, FileEdit, FileRead, FileWrite
from onboarding_agent.tools.search import SearchCode
from onboarding_agent.tools.shell import ShellExec
from onboarding_agent.workspace import Workspace

if TYPE_CHECKING:  # type-only import: config imports this module
    from onboarding_agent.config import HarnessConfig

# Every tool the registry can build. The config's allow-list is checked against this, so a
# new tool goes both here and in `_buildable`.
BUILDABLE_TOOLS = ("file_read", "file_write", "file_edit", "shell_exec", "search_code")


class ToolRegistry:
    """Name-to-tool lookup, plus the JSON schemas sent to the model."""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    @staticmethod
    def _buildable(workspace: Workspace, max_bytes: int) -> list[Tool]:
        return [
            FileRead(workspace, max_bytes=max_bytes),
            FileWrite(workspace),
            FileEdit(workspace),
            ShellExec(workspace, max_bytes=max_bytes),
            SearchCode(workspace, max_bytes=max_bytes),
        ]

    @classmethod
    def default(cls, workspace: Workspace, max_bytes: int = DEFAULT_MAX_BYTES) -> ToolRegistry:
        """Every tool, with the default output cap."""
        return cls(cls._buildable(workspace, max_bytes))

    # TODO 5.5: Implement from_config(), which turns the config's allow-list into the
    # tools the model is offered. The list is enforced here, in code: a tool that is
    # not listed is never built, so its schema never reaches the model.
    @classmethod
    def from_config(cls, config: HarnessConfig, workspace: Workspace) -> ToolRegistry:
        """Only the tools the config allows, with the output cap it sets.

        An allowed name that cannot be built is an error rather than a silent omission.
        """
        # TODO: Build every tool with _buildable(), capped at the config's
        # max_tool_output_bytes. Raise a ToolError if the allow-list names a tool that
        # cannot be built, and return a registry of the allowed tools, in the order
        # the config lists them.

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters()}
            for t in self._tools.values()
        ]

    # TODO 5.6: Implement call(), which runs a tool by name for _execute(). A tool
    # name the model made up must come back as an error it can act on, not crash
    # the loop.
    def call(self, name: str, args: dict[str, Any]) -> str:
        """Run the tool called ``name`` with ``args``. An unknown name raises ToolError."""
        # TODO: Look the tool up by name. If there is none, raise a ToolError whose
        # hint lists the available tools; otherwise return the tool's result.
