"""``Agent`` — a small, opinionated, provider-agnostic agent definition.

An ``Agent`` is declarative data: a name, a model id, instructions, tools, and a few knobs. The
loop lives in ``runner.py``; governance lives in the surrounding ``budget()``/``guard()`` contexts.
The provider is inferred from the model id (override with ``provider=``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .providers import Provider, resolve_provider
from .tools import Tool, as_tool


@dataclass
class Agent:
    """A provider-agnostic agent.

    Args:
        name: A short identifier used in results, audit decisions, and handoffs.
        model: Any core-supported model id (``"gpt-4o"``, ``"claude-opus-4-8"``, ``"gemini-…"``).
        instructions: The system prompt.
        tools: ``@tool``-decorated callables, plain functions, or ``Tool`` objects.
        provider: Override the provider inferred from ``model``.
        output_type: Structured output — a dataclass, a Pydantic model, or a JSON-schema dict.
        max_turns: Upper bound on ReAct iterations (loop-termination guarantee).
        context_budget: If set, assemble the history to this token budget via ``contextkit``.
        temperature / max_tokens: Optional generation controls.
        handoffs: Names of peer agents this agent may transfer to (Phase 2).
        api_key / base_url / client: Optional client config, or an explicit instrumented client.
    """

    name: str
    model: str
    instructions: str = ""
    tools: list[Any] = field(default_factory=list)
    provider: str | None = None
    output_type: Any = None
    max_turns: int = 8
    context_budget: int | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    handoffs: list[str] = field(default_factory=list)
    api_key: str | None = None
    base_url: str | None = None
    client: Any = None

    _tools: list[Tool] = field(default_factory=list, init=False, repr=False)
    _tool_map: dict[str, Tool] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tools = [as_tool(t) for t in self.tools]
        self._tool_map = {t.name: t for t in self._tools}

    @property
    def provider_impl(self) -> Provider:
        """The resolved provider implementation for this agent's model."""
        return resolve_provider(self.model, self.provider)

    @property
    def toolset(self) -> list[Tool]:
        """The agent's tools as ``Tool`` objects."""
        return self._tools

    def get_tool(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        return self._tool_map.get(name)

    def config(self) -> dict[str, Any]:
        """Client construction config (api_key / base_url)."""
        cfg: dict[str, Any] = {}
        if self.api_key:
            cfg["api_key"] = self.api_key
        if self.base_url:
            cfg["base_url"] = self.base_url
        return cfg

    def add_tool(self, tool: Any) -> None:
        """Register an extra tool at runtime (used by MCP/handoff wiring in later phases)."""
        t = as_tool(tool)
        self._tools.append(t)
        self._tool_map[t.name] = t
