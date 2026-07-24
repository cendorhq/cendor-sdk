"""SDK domain telemetry — RAG, memory, orchestration, checkpoints, tools, MCP.

The seven libraries emit ``cendor.core`` spans; this SDK emits ``cendor.sdk`` spans. This module
adds the *structural* SDK signals a monitor renders as first-class domains, all **zero-core**:

* **Run-scoped** signals ride ``cendor-core``'s type-agnostic bus as small event objects (the
  :class:`~cendor.sdk.runner.ContextBudgetFallback` precedent) — an active
  :func:`~cendor.sdk.otel.live_spans` subscriber turns each into a ``cendor.sdk`` child span,
  correlated to the run by ``trace_id``. Stock bus subscribers ignore unknown event types, so
  emitting is side-effect-free when nobody is watching.
* **Setup-time** MCP lifecycle (server discovery happens *before* a run) emits ``cendor.sdk`` spans
  directly via :func:`mcp_span`, a no-op without OpenTelemetry.

Everything here is opt-in and content-free by design: only ids, labels, and counts land on a span
— never message bodies, tool arguments, or results (those follow the existing content-capture
opt-in on ``chat`` / ``execute_tool`` spans). Emission is best-effort and never raises into a run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cendor.core import bus

# --------------------------------------------------------------------------- run-scoped bus events
#
# Plain data objects, emitted on core's bus from run-path code. ``live_spans`` renders each as a
# ``cendor.sdk`` child span; unknown types are ignored by every other subscriber (bus-events spec),
# so a run with no ``live_spans`` context pays only the cost of a locked, empty fan-out.


@dataclass
class MemoryOp:
    """A session load (run start) or save (write-back) → ``memory.load`` / ``memory.save``."""

    op: str  # "load" | "save"
    session_id: str
    turns: int
    bytes: int
    trace_id: str


@dataclass
class CheckpointEvent:
    """A checkpoint write or a resume decision → ``checkpoint.save`` / ``checkpoint.resume``."""

    op: str  # "save" | "resume"
    trace_id: str
    done: bool
    turns: int
    segment: int | None = None


@dataclass
class OrchestrationEdge:
    """A multi-agent handoff (parent → child) → an ``orchestration.handoff`` span; the monitor
    reconstructs the per-run agent DAG from these edges rather than parsing trace-id families."""

    from_agent: str
    to_agent: str
    segment: int
    transfer_tool: str
    trace_id: str


@dataclass
class ToolGate:
    """A tool call the ``tool_call`` guardrail stage BLOCKED before execution — there is no
    ``ToolCall`` on the bus for a blocked call (the tool never ran), so this is the only signal.
    Rendered as an ``execute_tool {name}`` span with ``cendor.tool.outcome="blocked"``."""

    name: str
    blocked_by: str  # the guardrail's name (never the reason text — may be sensitive)
    trace_id: str
    agent: str = ""


def _emit(ev: Any) -> None:
    """Emit a domain event on the bus, swallowing any error — telemetry never breaks a run."""
    try:
        bus.emit(ev)
    except Exception:  # noqa: BLE001, S110 - diagnostics must never break the run
        pass


def emit_memory(op: str, session: Any, trace_id: str) -> None:
    """Emit a :class:`MemoryOp` for a session load/save (no-op when ``session`` is ``None``)."""
    if session is None:
        return
    try:
        msgs = getattr(session, "messages", None) or []
        sid = getattr(session, "id", None) or ""
        nbytes = len(json.dumps(msgs, default=str))
    except Exception:  # noqa: BLE001 - never let telemetry inspection break a run
        msgs, sid, nbytes = [], "", 0
    _emit(
        MemoryOp(op=op, session_id=str(sid), turns=len(msgs), bytes=nbytes, trace_id=trace_id or "")
    )


def emit_checkpoint(
    op: str, trace_id: str, done: bool, turns: int, segment: int | None = None
) -> None:
    """Emit a :class:`CheckpointEvent` for a save/resume."""
    _emit(
        CheckpointEvent(
            op=op, trace_id=trace_id or "", done=bool(done), turns=int(turns), segment=segment
        )
    )


def emit_handoff(
    from_agent: str, to_agent: str, segment: int, transfer_tool: str, trace_id: str
) -> None:
    """Emit an :class:`OrchestrationEdge` for a parent → child agent handoff."""
    _emit(
        OrchestrationEdge(
            from_agent=from_agent or "",
            to_agent=to_agent or "",
            segment=int(segment),
            transfer_tool=transfer_tool or "",
            trace_id=trace_id or "",
        )
    )


def emit_tool_blocked(name: str, blocked_by: str, trace_id: str, agent: str = "") -> None:
    """Emit a :class:`ToolGate` for a tool call blocked by a ``tool_call`` guardrail."""
    _emit(
        ToolGate(
            name=name or "tool", blocked_by=blocked_by or "", trace_id=trace_id or "", agent=agent
        )
    )


# --------------------------------------------------------------------------- tool source registry
#
# A blocked/ok/error tool span needs to know whether the tool is LOCAL or came from an MCP server —
# core's provider-agnostic ``ToolCall`` carries no such marker. We record it SDK-side, keyed by tool
# name, populated when an MCP tool is wrapped (:func:`cendor.sdk.mcp._wrap_mcp_tool`). Unregistered
# names default to "local". Process-global (a dev-tool convenience); last-writer-wins on a name
# collision across servers — recorded as an honest limit in the SDK observability docs.

_TOOL_SOURCES: dict[str, dict[str, str]] = {}


def register_tool_source(name: str, source: str, *, server: str = "", transport: str = "") -> None:
    """Record a tool's source (``"local"`` | ``"mcp"``) + optional MCP server/transport."""
    info: dict[str, str] = {"source": source}
    if server:
        info["server"] = server
    if transport:
        info["transport"] = transport
    _TOOL_SOURCES[str(name)] = info


def tool_source(name: str) -> dict[str, str] | None:
    """The recorded source for a tool name, or ``None`` (caller treats absence as ``"local"``)."""
    return _TOOL_SOURCES.get(str(name))


# --------------------------------------------------------------------------- setup-time MCP spans

#: MCP servers we've already emitted an ``mcp.connect`` for (connect once; list_tools per call).
_MCP_SEEN: set[str] = set()


def mcp_connect_once(server: str = "", transport: str = "") -> None:
    """Emit ``mcp.connect`` the first time the SDK lists a named server (the SDK doesn't own the
    transport — this marks *SDK first contact*). No server name ⇒ no connect event (honest)."""
    if not server or server in _MCP_SEEN:
        return
    _MCP_SEEN.add(server)
    mcp_span("mcp.connect", server=server, transport=transport)


def mcp_span(
    kind: str, *, server: str = "", transport: str = "", tool_count: int | None = None
) -> None:
    """Emit a standalone ``cendor.sdk`` MCP-lifecycle span (``mcp.connect`` / ``mcp.list_tools``).

    Setup-time server discovery happens before any run, so this is a top-level span (not a run
    child). A **no-op** if OpenTelemetry isn't installed. Server attribution only — no tool bodies.
    """
    try:
        from opentelemetry import trace as ot
    except ImportError:
        return
    tracer = ot.get_tracer("cendor.sdk")
    with tracer.start_as_current_span(kind) as span:
        span.set_attribute("cendor.sdk.kind", kind)
        span.set_attribute("gen_ai.operation.name", kind)
        if server:
            span.set_attribute("cendor.mcp.server", server)
        if transport:
            span.set_attribute("cendor.mcp.transport", transport)
        if tool_count is not None:
            span.set_attribute("cendor.mcp.tool_count", int(tool_count))
