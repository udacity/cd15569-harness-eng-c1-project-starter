"""The model client, and the message types the agent loop works with.

Speaks the OpenAI ``/chat/completions`` format over plain ``httpx``, to whichever endpoint
``base_url`` names: OpenAI itself, or an OpenAI-compatible proxy such as Vocareum's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Reasoning models. They reject `temperature`, take `max_completion_tokens` instead of
# `max_tokens`, and count their reasoning against that output limit.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class ModelError(RuntimeError):
    """The endpoint could not be reached, or answered with something unusable."""


class TruncatedResponse(ModelError):
    """The model hit its output limit mid-answer.

    Raise ``max_output_tokens``; retrying will not help. Reasoning models hit this easily,
    because their reasoning counts against the same limit as the answer.
    """


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None
    is_error: bool = False
    # The tool calls an assistant turn made. They must stay in the transcript: the
    # endpoint rejects a tool result whose call id it has not seen.
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ModelResponse:
    """One model turn: either text, or tool calls, or both."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


class ModelClient:
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        max_output_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> None:
        # Logged with every call, so token counts can be priced per model.
        self.model = model
        self._endpoint = base_url.rstrip("/")
        self._api_key = api_key
        self._max_output_tokens = max_output_tokens
        self._http = httpx.Client(timeout=timeout)

    @property
    def _is_reasoning(self) -> bool:
        return self.model.startswith(_REASONING_PREFIXES)

    def complete(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        payload = self._body(messages, tools)
        # The model name comes from the config: the Vocareum proxy rejects GET /v1/models,
        # so a wrong name only shows up as an error here.
        try:
            response = self._http.post(
                f"{self._endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ModelError(f"{self._endpoint} unreachable: {exc}") from exc

        if response.status_code != 200:
            raise ModelError(
                f"{self.model} returned HTTP {response.status_code}: {_detail(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            # A proxy can answer 200 with a body that is not JSON. As a ModelError, the task
            # fails cleanly and the next session retries it.
            raise ModelError(
                f"{self.model} returned HTTP 200 with a body that is not JSON: "
                f"{_detail(response)}"
            ) from exc
        return self._parse(payload)

    def _body(self, messages: list[Message], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_wire_message(m) for m in messages],
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]
        if self._is_reasoning:
            body["max_completion_tokens"] = self._max_output_tokens
        else:
            body["max_tokens"] = self._max_output_tokens
            body["temperature"] = 0
        return body

    def _parse(self, payload: dict[str, Any]) -> ModelResponse:
        try:
            choice = payload["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError) as exc:
            raise ModelError(f"unrecognised response shape: {json.dumps(payload)[:400]}") from exc

        usage = payload.get("usage") or {}
        if choice.get("finish_reason") == "length":
            raise TruncatedResponse(self._truncation_detail(usage))

        calls = [
            ToolCall(
                id=raw["id"],
                name=raw["function"]["name"],
                arguments=_decode_arguments(raw["function"].get("arguments")),
            )
            for raw in message.get("tool_calls") or []
        ]
        return ModelResponse(
            text=message.get("content"),
            tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )

    def _truncation_detail(self, usage: dict[str, Any]) -> str:
        limit = self._max_output_tokens
        reasoning = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))
        if reasoning:
            return (
                f"{self.model} hit the {limit}-token output limit with "
                f"{reasoning} of them spent on reasoning, leaving no answer. Reasoning is "
                "billed against max_completion_tokens — raise it."
            )
        return f"{self.model} hit the {limit}-token output limit mid-answer."


def _wire_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        # Send the calls this turn made; the tool results that follow refer to them by id.
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return wire


def _decode_arguments(raw: object) -> dict[str, Any]:
    """Decode a tool call's arguments, which arrive as a JSON string and may be invalid."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        decoded = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ModelError(f"model emitted unparseable tool arguments: {raw!r}") from exc
    if not isinstance(decoded, dict):
        raise ModelError(f"tool arguments were {type(decoded).__name__}, expected an object")
    return decoded


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        if text.lstrip().lower().startswith(("<!doctype", "<html")):
            # A proxy's error page rather than an API response: summarise it, don't dump it.
            return (
                f"the endpoint returned an HTML page rather than JSON "
                f"({len(text)} bytes) — check the base_url path and that the proxy is up"
            )
        return text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", error))
    return json.dumps(body)[:300]
