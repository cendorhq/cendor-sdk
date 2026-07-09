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
        cache: Opt into explicit provider prompt caching. For Anthropic this puts a
            ``cache_control`` breakpoint on the system prompt + tool schema (the stable prefix), so
            repeated runs pay the cached rate; cache reads/writes come back as usage and are priced
            by ``cendor-core``. A no-op where caching is automatic (OpenAI) or unsupported.
        extra: Extra provider request kwargs merged into every model call — the passthrough for
            things the SDK doesn't model first-class (``tool_choice``, ``reasoning_effort``,
            ``top_p``, ``stop``, ``seed``, ``response_format``, ``extra_body``, …). Merged at the
            top level of the request, matching the OpenAI/Anthropic/Ollama/HF/Azure shape.
        retriever: Optional ``query -> list[str]`` callable (e.g. ``VectorIndex.as_retriever()``).
            When set, context is retrieved for the run's query and injected as a system message
            before the call — "always-on" RAG. (For agentic retrieval, expose it as a tool instead.)
        handoffs: Names of peer agents this agent may transfer to.
        guardrails: ``cendor.guardrails.Guardrail`` objects gating this agent's four stages
            (``input`` / ``tool_call`` / ``tool_output`` / ``output``). A ``block`` fails closed
            (raising ``GuardrailTripped``, or — at ``tool_call`` — returning a ``[blocked …]`` tool
            result so the loop continues); ``redact`` rewrites the payload; ``flag`` records and
            continues. Every decision is recorded on the audit chain. Override per run with
            ``run(agent, input, guardrails=[…])``. Scoped to *this* agent (unlike the process-global
            ``guard()``).
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
    cache: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    retriever: Any = None  # Callable[[str], list[str]] — injected as context when set (RAG)
    handoffs: list[Any] = field(default_factory=list)
    guardrails: list[Any] = field(default_factory=list)  # cendor.guardrails.Guardrail — the gate
    max_usd: float | None = None  # per-agent spend cap (enforced by the orchestrator)
    api_key: str | None = field(default=None, repr=False)  # never surface a key in repr()
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
