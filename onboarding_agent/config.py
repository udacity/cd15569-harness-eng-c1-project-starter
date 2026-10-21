"""Load and validate the harness config file, ``config/agent.yaml``.

The config sets the model, the limits, the tool allow-list, the system prompt rules and
the workspace paths. Validation reports every problem at once rather than stopping at
the first one.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from onboarding_agent.model import DEFAULT_BASE_URL, ModelClient
from onboarding_agent.tools.registry import BUILDABLE_TOOLS
from onboarding_agent.workspace import FIXTURE_ROOT_VAR, fixture_hint

logger = logging.getLogger(__name__)

# The registry's own list, so the allow-list is checked against the tools that exist.
KNOWN_TOOLS = BUILDABLE_TOOLS

# Past roughly this many rules, each new rule competes with the rest for the model's
# attention. A config over its budget loads, with a warning.
DEFAULT_INSTRUCTION_BUDGET = 150


class ConfigError(ValueError):
    """The config file is missing, unparseable, or invalid. Raised once, listing everything."""


@dataclass(frozen=True)
class ModelSettings:
    name: str
    base_url: str | None = None
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class Limits:
    turn_cap: int = 40
    token_budget: int = 200_000
    retry_cap: int = 3
    max_tool_output_bytes: int = 20_000


@dataclass(frozen=True)
class ToolSettings:
    allow: list[str] = field(default_factory=lambda: list(KNOWN_TOOLS))


@dataclass(frozen=True)
class SystemPrompt:
    rules: list[str] = field(default_factory=list)
    instruction_budget: int = DEFAULT_INSTRUCTION_BUDGET

    @property
    def over_budget(self) -> bool:
        return len(self.rules) > self.instruction_budget

    def render(self) -> str:
        return "\n".join(f"- {rule}" for rule in self.rules)


@dataclass(frozen=True)
class WorkspaceSettings:
    root: Path
    runs_dir: Path = Path("runs")


@dataclass(frozen=True)
class HarnessConfig:
    model: ModelSettings
    workspace: WorkspaceSettings
    limits: Limits = field(default_factory=Limits)
    tools: ToolSettings = field(default_factory=ToolSettings)
    system_prompt: SystemPrompt = field(default_factory=SystemPrompt)

    def model_client(self) -> ModelClient:
        """Build the model client. The API key comes from ``OPENAI_API_KEY``, never the file."""
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ConfigError(
                "OPENAI_API_KEY is not set. Export it (or put it in a .env file you "
                "source) — it is deliberately not a config-file field."
            )
        return ModelClient(
            model=self.model.name,
            base_url=self.model.base_url
            or os.environ.get("OPENAI_BASE_URL")
            or DEFAULT_BASE_URL,
            api_key=api_key,
            max_output_tokens=self.model.max_output_tokens,
        )


_SECTIONS = {
    "model": {"name", "base_url", "max_output_tokens"},
    "limits": {"turn_cap", "token_budget", "retry_cap", "max_tool_output_bytes"},
    "tools": {"allow"},
    "system_prompt": {"rules", "instruction_budget"},
    "workspace": {"root", "runs_dir"},
}


def load_config(path: Path | str) -> HarnessConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"no config file at {path}")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping at the top level, got {type(raw).__name__}")

    problems: list[str] = []
    _check_sections(raw, problems)

    model = _model(raw.get("model") or {}, problems)
    limits = _limits(raw.get("limits") or {}, problems)
    tools = _tools(raw.get("tools") or {}, problems)
    prompt = _system_prompt(raw.get("system_prompt") or {}, problems)
    workspace = _workspace(raw.get("workspace") or {}, path, problems)

    if problems:
        raise ConfigError(
            f"{path} has {len(problems)} problem(s):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    assert model is not None and workspace is not None  # guaranteed by the problems check
    config = HarnessConfig(
        model=model, workspace=workspace, limits=limits, tools=tools, system_prompt=prompt
    )
    if config.system_prompt.over_budget:
        logger.warning(
            "system prompt carries %d rules against an instruction budget of %d — "
            "past the budget the marginal rule competes with the ones that matter",
            len(config.system_prompt.rules),
            config.system_prompt.instruction_budget,
        )
    return config


def _check_sections(raw: dict[str, Any], problems: list[str]) -> None:
    for name in raw:
        if name not in _SECTIONS:
            problems.append(f"unknown section {name!r}; expected one of: {_named(_SECTIONS)}")
    for name, allowed in _SECTIONS.items():
        section = raw.get(name)
        if isinstance(section, dict):
            for key in section:
                if key not in allowed:
                    problems.append(
                        f"unknown key {name}.{key!r}; expected one of: {_named(allowed)}"
                    )


def _model(section: dict[str, Any], problems: list[str]) -> ModelSettings | None:
    name = section.get("name")
    if not name:
        problems.append("model.name is required (the model identifier sent to the endpoint)")

    max_output = _positive(section, "max_output_tokens", 4096, "model", problems)
    if not name:
        return None
    return ModelSettings(
        name=str(name),
        base_url=section.get("base_url"),
        max_output_tokens=max_output,
    )


def _limits(section: dict[str, Any], problems: list[str]) -> Limits:
    defaults = Limits()
    return Limits(
        turn_cap=_positive(section, "turn_cap", defaults.turn_cap, "limits", problems),
        token_budget=_positive(section, "token_budget", defaults.token_budget, "limits", problems),
        retry_cap=_positive(section, "retry_cap", defaults.retry_cap, "limits", problems),
        max_tool_output_bytes=_positive(
            section, "max_tool_output_bytes", defaults.max_tool_output_bytes, "limits", problems
        ),
    )


def _tools(section: dict[str, Any], problems: list[str]) -> ToolSettings:
    allow = section.get("allow")
    if allow is None:
        return ToolSettings()
    if not isinstance(allow, list) or not all(isinstance(t, str) for t in allow):
        problems.append("tools.allow must be a list of tool names")
        return ToolSettings()
    for name in allow:
        if name not in KNOWN_TOOLS:
            problems.append(f"tools.allow names {name!r}, which is not a tool; "
                            f"known tools are: {_named(KNOWN_TOOLS)}")
    if not allow:
        problems.append("tools.allow is empty; the agent would have no tools at all")
    return ToolSettings(allow=list(allow))


def _system_prompt(section: dict[str, Any], problems: list[str]) -> SystemPrompt:
    rules = section.get("rules") or []
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        problems.append("system_prompt.rules must be a list of strings, one instruction each")
        rules = []
    budget = _positive(
        section, "instruction_budget", DEFAULT_INSTRUCTION_BUDGET, "system_prompt", problems
    )
    return SystemPrompt(rules=list(rules), instruction_budget=budget)


def _workspace(
    section: dict[str, Any], config_path: Path, problems: list[str]
) -> WorkspaceSettings | None:
    root = section.get("root")
    if not root:
        problems.append(
            "workspace.root is required (the directory the agent may read and write; "
            "for this project, the checkout of the inherited-service fixture)"
        )
        return None
    # Paths are relative to the config file, not to the current directory.
    base = config_path.resolve().parent
    resolved = _fixture_dir(str(root), base, problems)
    if resolved is None:
        return None
    return WorkspaceSettings(
        root=resolved,
        runs_dir=(base / str(section.get("runs_dir", "runs"))).resolve(),
    )


def _fixture_dir(root: str, base: Path, problems: list[str]) -> Path | None:
    """Resolve ``workspace.root``, unless ``ONBOARDING_FIXTURE`` names the checkout instead.

    The environment variable points at a checkout elsewhere without editing the config.
    Either way, a path that is not a directory is reported as a problem.
    """
    override = os.environ.get(FIXTURE_ROOT_VAR)
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.is_dir():
            problems.append(
                f"{FIXTURE_ROOT_VAR}={override!r} resolves to {candidate}, "
                f"which is not a directory"
            )
            return None
        return candidate

    configured = (base / root).resolve()
    if not configured.is_dir():
        problems.append(
            f"workspace.root {root!r} resolves to {configured}, which is not a directory "
            f"(paths are relative to {base}). {fixture_hint()}"
        )
        return None
    return configured


def _positive(
    section: dict[str, Any], key: str, default: int, prefix: str, problems: list[str]
) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        problems.append(f"{prefix}.{key} must be a positive integer, got {value!r}")
        return default
    return value


def _named(values: Iterable[object]) -> str:
    return ", ".join(sorted(str(v) for v in values))
