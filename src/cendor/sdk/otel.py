"""Full-run OpenTelemetry span tree for a completed run.

Emits a ``gen_ai.*`` span tree so a whole agent trajectory shows up in Foundry / Datadog / etc.:
a root ``agent.run`` span, a child span per agent segment, and a grandchild per model call
(``chat {model}``) and tool execution (``execute_tool {name}``) — mirroring the correlated
``Result`` tree. Uses the OpenTelemetry API directly (behind the ``[otel]`` extra); a **no-op
returning ``False``** if OpenTelemetry isn't installed (local-first — telemetry is always optional).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from cendor.core.types import LLMCall, ToolCall

from .result import Result, Step


def _system_from_call(call: Any) -> Any:
    """Best-effort system prompt from a call's request kwargs — covers providers where the system
    prompt is a kwarg (Anthropic ``system``, Responses ``instructions``, Gemini
    ``system_instruction``, Bedrock ``system``) rather than a message. For chat-completions/Ollama
    it's already in ``call.messages`` (so ``None`` here avoids double-capture). Content only.
    """
    kw = (getattr(call, "metadata", None) or {}).get("request_kwargs")
    if not isinstance(kw, dict):
        return None
    for key in ("instructions", "system", "system_instruction"):
        v = kw.get(key)
        if v:
            return v
    cfg = kw.get("config")
    if isinstance(cfg, dict) and cfg.get("system_instruction"):
        return cfg["system_instruction"]
    if cfg is not None and getattr(cfg, "system_instruction", None):
        return cfg.system_instruction
    return None


def _call_content_attrs(call: Any) -> dict[str, str]:
    """The ``gen_ai.*`` content span attributes for a chat call (G17/G18), or ``{}`` when capture is
    off. Input from the call's messages, output (incl. thinking) parsed from the raw response."""
    from cendor.core import otel as _co

    return _co.content_attrs(
        system=_system_from_call(call),
        input_messages=getattr(call, "messages", None),
        output_messages=_co.response_messages(call),
    )


def _tool_content_attrs(call: Any) -> dict[str, str]:
    """The tool arg/result content span attributes (G17), or ``{}`` when capture is off."""
    from cendor.core import otel as _co

    return _co.tool_content_attrs(
        arguments=getattr(call, "arguments", None), result=getattr(call, "result", None)
    )


def _group_by_agent(steps: list[Step]) -> list[tuple[str, list[Step]]]:
    """Contiguous groups of steps by agent, preserving order."""
    groups: list[tuple[str, list[Step]]] = []
    for step in steps:
        if groups and groups[-1][0] == step.agent:
            groups[-1][1].append(step)
        else:
            groups.append((step.agent, [step]))
    return groups


def span_tree(
    result: Result,
    tracer: Any = None,
    *,
    conversation_id: str | None = None,
    label: str | None = None,
) -> bool:
    """Emit a ``gen_ai`` span tree for ``result``. ``True`` if emitted, ``False`` if OTel absent.

    Args:
        result: The finished run to render as a span tree.
        tracer: An OTel tracer; defaults to ``get_tracer("cendor.sdk")``.
        conversation_id: Optional multi-turn grouping id (e.g. your ``SQLiteSessionStore`` key).
            When set, the root ``agent.run`` span carries it as the ``gen_ai.conversation.id``
            semantic-convention attribute, so a backend can group the runs of one session.
        label: Optional **short, human-authored** name for this run (e.g. ``"nightly sweep"``),
            stamped on the root as ``cendor.run.label`` so a monitor can show what a run was *for*.
            **Never derive it from the prompt** — prompts/tool values stay off spans by design; a
            label is a deliberate, non-sensitive tag you choose.

    ```python
    from cendor.sdk import run
    from cendor.sdk.otel import span_tree
    result = run(agent, "...")
    span_tree(result, conversation_id="chat-42", label="refund triage")  # -> spans to your pipeline
    ```
    """
    try:
        from opentelemetry import trace as ot
    except ImportError:
        return False

    tracer = tracer or ot.get_tracer("cendor.sdk")
    # G19: fall back to the conversation id the runner propagated from the session key (explicit
    # arg wins). semconv: only a real key is used, never a synthesized one.
    conversation_id = conversation_id or getattr(result, "conversation_id", "") or None
    with tracer.start_as_current_span("agent.run") as root:
        root.set_attribute("gen_ai.operation.name", "agent")
        root.set_attribute("cendor.run.id", result.trace_id)
        if conversation_id:
            root.set_attribute("gen_ai.conversation.id", conversation_id)
        if label:
            root.set_attribute("cendor.run.label", label)
        root.set_attribute("cendor.run.agents", ",".join(result.agents))
        root.set_attribute("gen_ai.usage.input_tokens", result.usage.input_tokens)
        root.set_attribute("gen_ai.usage.output_tokens", result.usage.output_tokens)
        root.set_attribute("cendor.run.cost_usd", str(result.cost.amount))

        step_no = 0  # 1-based ordinal across the whole run (matches live_spans' cendor.step)
        for agent_name, group in _group_by_agent(result.steps):
            with tracer.start_as_current_span(f"agent {agent_name}") as agent_span:
                agent_span.set_attribute("gen_ai.operation.name", "invoke_agent")
                agent_span.set_attribute("gen_ai.agent.name", agent_name)
                for step in group:
                    step_no += 1
                    if step.kind == "llm" and isinstance(step.call, LLMCall):
                        with tracer.start_as_current_span(f"chat {step.name}") as s:
                            s.set_attribute("gen_ai.operation.name", "chat")
                            s.set_attribute("gen_ai.system", step.call.provider)
                            s.set_attribute("gen_ai.request.model", step.call.model)
                            s.set_attribute("gen_ai.agent.name", agent_name)
                            s.set_attribute("cendor.step", step_no)
                            _set_call_attrs(s, step.call)
                            if step.usage is not None:
                                s.set_attribute(
                                    "gen_ai.usage.input_tokens", step.usage.input_tokens
                                )
                                s.set_attribute(
                                    "gen_ai.usage.output_tokens", step.usage.output_tokens
                                )
                                if step.usage.reasoning_tokens:
                                    s.set_attribute(
                                        "gen_ai.usage.reasoning_tokens", step.usage.reasoning_tokens
                                    )
                            if step.cost is not None:
                                s.set_attribute("gen_ai.usage.cost", str(step.cost.amount))
                            if (step.call.metadata or {}).get("replayed"):
                                s.set_attribute("cendor.replayed", True)  # G22 cassette replay
                            for k, v in _call_content_attrs(step.call).items():  # G17/G18
                                s.set_attribute(k, v)
                    else:
                        with tracer.start_as_current_span(f"execute_tool {step.name}") as s:
                            s.set_attribute("gen_ai.operation.name", "execute_tool")
                            s.set_attribute("gen_ai.tool.name", step.name)
                            s.set_attribute("gen_ai.agent.name", agent_name)
                            s.set_attribute("cendor.step", step_no)
                            _set_tool_attrs(s, step.call)
                            for k, v in _tool_source_attrs(step.name).items():  # E-wave source
                                s.set_attribute(k, v)
                            s.set_attribute("cendor.tool.outcome", _tool_outcome(step.call))
                            for k, v in _tool_content_attrs(step.call).items():  # G17
                                s.set_attribute(k, v)
    return True


def _set_call_attrs(span: Any, call: Any) -> None:
    """Enrich an LLM span with real latency, finish reason, and any recorded error."""
    latency = getattr(call, "latency_ms", None)
    if latency is not None:
        span.set_attribute("gen_ai.latency_ms", latency)
    meta = getattr(call, "metadata", {}) or {}
    finish = meta.get("finish_reason") or getattr(call, "finish_reason", None)
    if finish:
        span.set_attribute("gen_ai.response.finish_reason", str(finish))
    if meta.get("streamed"):
        span.set_attribute("gen_ai.response.streamed", True)
    # G-V4-1: time-to-first-token, recovered on the first streamed chunk (instrument() records it in
    # call.metadata). Stamping it here brings governed journeys (span_tree + live_spans) to parity
    # with the libs-only span emitter, so TTFT shows on real SDK workloads, not just bare streams.
    ttft = meta.get("ttft_ms")
    if ttft is not None:
        span.set_attribute("cendor.ttft_ms", ttft)
    # G-V4-3: streamed token counts estimated offline (no provider usage on the stream) are flagged
    # so a monitor renders them as "est." — truth = the product. String "true", only when set.
    if meta.get("usage_estimated"):
        span.set_attribute("cendor.usage_estimated", "true")
    err = meta.get("error")
    if err:
        span.set_attribute("error", True)
        span.set_attribute("gen_ai.error", str(err))


def _set_tool_attrs(span: Any, call: Any) -> None:
    """Enrich a tool span with latency and argument names (values omitted — may be sensitive)."""
    latency = getattr(call, "latency_ms", None)
    if latency is not None:
        span.set_attribute("gen_ai.latency_ms", latency)
    args = getattr(call, "arguments", None)
    if isinstance(args, dict):
        inner = args.get("kwargs") if isinstance(args.get("kwargs"), dict) else args
        if isinstance(inner, dict) and inner:
            span.set_attribute("gen_ai.tool.arg_names", ",".join(sorted(map(str, inner))))


def _tool_source_attrs(name: str) -> dict[str, Any]:
    """Source attribution for a tool span — ``local`` unless it was wrapped from an MCP server
    (E-wave: :func:`cendor.sdk._telemetry.register_tool_source`). Labels only, no bodies."""
    from . import _telemetry as _tel

    src = _tel.tool_source(name) or {}
    attrs: dict[str, Any] = {"cendor.tool.source": src.get("source", "local")}
    if src.get("server"):
        attrs["cendor.tool.mcp.server"] = src["server"]
    if src.get("transport"):
        attrs["cendor.tool.mcp.transport"] = src["transport"]
    return attrs


def _tool_outcome(call: Any) -> str:
    """Derive a tool call's outcome (``ok`` | ``error``) from the runner's own result convention —
    a failed tool returns ``"[error] …"`` (runner._exec_tool_*). Computed in-process from the
    result value; only the ``ok``/``error`` LABEL lands on the span, never the result text."""
    result = getattr(call, "result", None)
    return "error" if isinstance(result, str) and result.startswith("[error]") else "ok"


# --- E-wave domain spans (RAG / memory / orchestration / checkpoints / blocked tools) ------------
# Rendered by ``live_spans`` from bus events (see _telemetry.py). Each returns (span_name, attrs);
# every attrs dict carries ``cendor.sdk.kind`` so the monitor can switch on it robustly.


def _rag_assemble_attrs(ev: Any) -> tuple[str, dict[str, Any]]:
    decisions = list(getattr(ev, "decisions", None) or [])
    kept = sum(1 for d in decisions if getattr(d, "action", "") == "kept")
    dropped = sum(1 for d in decisions if getattr(d, "action", "") == "dropped")
    before = sum(int(getattr(d, "tokens_before", 0) or 0) for d in decisions)
    after = sum(int(getattr(d, "tokens_after", 0) or 0) for d in decisions)
    return "rag.assemble", {
        "cendor.sdk.kind": "rag.assemble",
        "gen_ai.operation.name": "rag.assemble",
        "cendor.rag.budget": int(getattr(ev, "budget", 0) or 0),
        "cendor.rag.used": int(getattr(ev, "used", 0) or 0),
        "cendor.rag.reserved_output": int(getattr(ev, "reserved_output", 0) or 0),
        "cendor.rag.model": str(getattr(ev, "model", "") or ""),
        "cendor.rag.blocks": len(decisions),
        "cendor.rag.kept": kept,
        "cendor.rag.dropped": dropped,
        "cendor.rag.tokens_before": before,
        "cendor.rag.tokens_after": after,
    }


def _rag_compress_attrs(ev: Any) -> tuple[str, dict[str, Any]]:
    return "rag.compress", {
        "cendor.sdk.kind": "rag.compress",
        "gen_ai.operation.name": "rag.compress",
        "cendor.rag.technique": str(getattr(ev, "technique", "") or ""),
        "cendor.rag.tokens_before": int(getattr(ev, "tokens_before", 0) or 0),
        "cendor.rag.tokens_after": int(getattr(ev, "tokens_after", 0) or 0),
        "cendor.rag.ratio": str(getattr(ev, "ratio", "") or ""),
        "cendor.rag.store_kind": str(getattr(ev, "store_kind", "") or ""),
        "cendor.rag.kind": str(getattr(ev, "kind", "") or ""),
    }


def _memory_attrs(ev: Any) -> tuple[str, dict[str, Any]]:
    name = f"memory.{ev.op}"
    attrs: dict[str, Any] = {
        "cendor.sdk.kind": name,
        "gen_ai.operation.name": name,
        "cendor.memory.op": ev.op,
        "cendor.memory.turns": int(ev.turns),
        "cendor.memory.bytes": int(ev.bytes),
    }
    if ev.session_id:
        attrs["cendor.memory.session_id"] = ev.session_id
        attrs["gen_ai.conversation.id"] = ev.session_id
    return name, attrs


def _checkpoint_attrs(ev: Any) -> tuple[str, dict[str, Any]]:
    name = f"checkpoint.{ev.op}"
    attrs: dict[str, Any] = {
        "cendor.sdk.kind": name,
        "gen_ai.operation.name": name,
        "cendor.checkpoint.op": ev.op,
        "cendor.checkpoint.done": bool(ev.done),
        "cendor.checkpoint.turns": int(ev.turns),
    }
    if ev.segment is not None:
        attrs["cendor.checkpoint.segment"] = int(ev.segment)
    return name, attrs


def _handoff_attrs(ev: Any) -> tuple[str, dict[str, Any]]:
    return "orchestration.handoff", {
        "cendor.sdk.kind": "orchestration.handoff",
        "gen_ai.operation.name": "orchestration.handoff",
        "cendor.orch.from_agent": ev.from_agent,
        "cendor.orch.to_agent": ev.to_agent,
        "cendor.orch.segment": int(ev.segment),
        "cendor.orch.transfer_tool": ev.transfer_tool,
    }


def _tool_blocked_attrs(ev: Any) -> tuple[str, dict[str, Any]]:
    name = f"execute_tool {ev.name}"
    attrs: dict[str, Any] = {
        "cendor.sdk.kind": "execute_tool",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": ev.name,
        "cendor.tool.outcome": "blocked",
        "cendor.tool.blocked_by": ev.blocked_by,
        **_tool_source_attrs(ev.name),
    }
    if ev.agent:
        attrs["gen_ai.agent.name"] = ev.agent
    return name, attrs


def _governance_attrs(ev: Any) -> tuple[str, dict[str, Any]] | None:
    """Option C (DR-2c): an enforcement event as a ``governance.*`` child of the run root.

    Core owns the vocabulary (``cendor.gov.*``) and the rule-6 posture — no ``audit.*`` names and
    no ``reason`` strings (a guardrail's reason can carry input-derived text; see core's Option C
    note). The SDK only re-uses it so the same decision lands **inside the run** rather than beside
    it. Returns ``None`` when the event isn't an enforcement event, or when an audit mirror is
    already putting governance on the wire (the mirror wins).
    """
    from cendor.core import otel as _co

    mapper = getattr(_co, "_gov_attrs", None)
    if mapper is None or _co.governance_mirror_active():
        return None  # older core (no Option C), or the mirror owns the wire
    mapped = mapper(ev)
    if mapped is None:
        return None
    name, attrs = mapped
    return name, {k: v for k, v in attrs.items() if v is not None}


@contextmanager
def live_spans(
    tracer: Any = None,
    *,
    name: str = "agent.run",
    conversation_id: str | None = None,
    label: str | None = None,
) -> Any:
    """Emit ``gen_ai`` spans **live** as a run progresses — the streaming counterpart to
    :func:`span_tree` (which builds the tree post-hoc from a finished ``Result``). Wrap a run:

    ```python
    from cendor.sdk import run
    from cendor.sdk.otel import live_spans
    with live_spans(conversation_id="chat-42", label="refund triage"):
        result = run(agent, "...", session=mem)
    ```

    A root ``agent.run`` span brackets the block; a child ``chat {model}`` / ``execute_tool {name}``
    span is emitted the moment each call completes (its start time backdated by the call's
    ``latency_ms`` so the duration is accurate), so a live backend sees the trajectory in real time
    rather than all at once at the end. Each child carries the making agent's ``gen_ai.agent.name``
    and a 1-based ``cendor.step`` ordinal; the root carries ``cendor.run.id`` / ``cendor.trace_id``
    (the run's correlation id, learned from the first observed event) and, at close, the run's
    ``gen_ai.usage.input_tokens`` / ``output_tokens`` and ``cendor.run.cost_usd`` rollups — bringing
    the live tree to parity with :func:`span_tree`.

    Args:
        conversation_id: Optional multi-turn grouping id → ``gen_ai.conversation.id`` on the root.
        label: Optional **short, human-authored** run name → ``cendor.run.label`` on the root, so a
            monitor shows what a run was *for*. **Never derive it from the prompt** — prompts/tool
            values stay off spans by design; a label is a deliberate, non-sensitive tag you choose.

    A **no-op** (still runs the block) if OpenTelemetry isn't installed.
    """
    try:
        from opentelemetry import trace as ot
    except ImportError:
        yield
        return

    import time
    from decimal import Decimal

    from cendor.core import bus, current_trace_id
    from cendor.core import otel as _co

    from ._governance import current_agent, current_conversation

    # E-wave: bus event class → attrs builder (RAG rides library events; the rest are SDK events).
    _DOMAIN_BUILDERS = {
        "AssemblyReport": _rag_assemble_attrs,
        "CompressionEvent": _rag_compress_attrs,
        "MemoryOp": _memory_attrs,
        "CheckpointEvent": _checkpoint_attrs,
        "OrchestrationEdge": _handoff_attrs,
        "ToolGate": _tool_blocked_attrs,
        # Option C: enforcement decisions as run children (core renders the same two events flat for
        # a libs-only app). Duck-typed by class name, like every other row here.
        "BudgetEvent": _governance_attrs,
        "GuardrailDecision": _governance_attrs,
    }

    tracer = tracer or ot.get_tracer("cendor.sdk")
    _co.enter_live_spans()  # G20: the core bus→span emitter stands down while we own the spans
    with tracer.start_as_current_span(name) as root:
        root.set_attribute("gen_ai.operation.name", "agent")
        if conversation_id:
            root.set_attribute("gen_ai.conversation.id", conversation_id)
        if label:
            root.set_attribute("cendor.run.label", label)
        ctx = ot.set_span_in_context(root)
        # Rollup/step state accumulated across the run (typed closure vars, mutated via nonlocal).
        step_no = 0
        total_input = 0
        total_output = 0
        total_cost = Decimal("0")
        run_id_set = False
        conv_set = bool(conversation_id)
        family = ""  # the run-family root, learned from the first event (GLR-3)
        agents_seen: dict[str, None] = {}  # ordered-unique agent names → cendor.run.agents (G-V4-2)

        def _learn_and_filter(trace_id: str) -> bool:
            """Learn the run-family root from the first event; reject foreign runs (GLR-3). The
            family is the segment before the first ``:`` (orchestration segments are
            ``{parent}:{agent}#{seg}``; a single-agent run is the bare run id) — matches the
            collector's match + the monitor's key. Returns ``False`` if the event is foreign."""
            nonlocal family, run_id_set
            if not run_id_set and trace_id:
                family = trace_id.split(":", 1)[0]
                root.set_attribute("cendor.run.id", family)
                root.set_attribute("cendor.trace_id", family)
                run_id_set = True
            return not (family and trace_id != family and not trace_id.startswith(family + ":"))

        def _domain_span(ev: Any, builder: Any) -> None:
            """Render an E-wave domain event (RAG/memory/orchestration/checkpoint/blocked-tool) as a
            ``cendor.sdk`` child span, correlated by trace_id. ``AssemblyReport`` carries no
            trace_id — read the ambient run scope (``bus.emit`` is a synchronous same-thread fanout,
            so the emitting call's ``trace()`` scope is still active)."""
            trace_id = getattr(ev, "trace_id", "") or current_trace_id() or ""
            if not _learn_and_filter(trace_id):
                return
            mapped = builder(ev)
            if mapped is None:
                return  # Option C stood down (an audit mirror owns the wire) — nothing to render
            name, attrs = mapped
            now = time.time_ns()
            span = tracer.start_span(name, context=ctx, start_time=now)
            span.set_attribute("cendor.trace_id", trace_id)
            for k, v in attrs.items():
                if v is not None:
                    span.set_attribute(k, v)
            span.end(end_time=now)

        def on_event(ev: Any) -> None:
            nonlocal step_no, total_input, total_output, total_cost, run_id_set, conv_set, family
            builder = _DOMAIN_BUILDERS.get(type(ev).__name__)
            if builder is not None:  # E-wave: a new structural SDK/RAG signal
                _domain_span(ev, builder)
                return
            if not isinstance(ev, (LLMCall, ToolCall)):
                return
            trace_id = getattr(ev, "trace_id", "") or ""
            if not _learn_and_filter(trace_id):
                return
            if not conv_set:
                # G19: prefer the conversation id stamped on the event at construction (GLR-2 —
                # survives an out-of-scope delivery), else the ambient scope. Only a real key.
                cid = (getattr(ev, "metadata", None) or {}).get(
                    "conversation_id"
                ) or current_conversation()
                if cid:
                    root.set_attribute("gen_ai.conversation.id", cid)
                    conv_set = True
            step_no += 1
            # Agent: the ambient current agent (in scope), falling back to the event's stamped
            # metadata (GLR-2 — correct even for an out-of-scope streamed delivery).
            agent = current_agent() or (getattr(ev, "metadata", None) or {}).get("agent") or ""
            if agent:
                agents_seen.setdefault(agent, None)  # G-V4-2: collect participants for the root
            end = time.time_ns()
            latency = getattr(ev, "latency_ms", None)
            start = end - int(
                (latency or 0) * 1_000_000
            )  # backdate by latency for accurate duration
            if isinstance(ev, LLMCall):
                span = tracer.start_span(f"chat {ev.model}", context=ctx, start_time=start)
                span.set_attribute("gen_ai.operation.name", "chat")
                span.set_attribute("gen_ai.system", ev.provider)
                span.set_attribute("gen_ai.request.model", ev.model)
                span.set_attribute("cendor.trace_id", trace_id)
                span.set_attribute("cendor.step", step_no)
                if agent:
                    span.set_attribute("gen_ai.agent.name", agent)
                _set_call_attrs(span, ev)
                if ev.usage is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", ev.usage.input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", ev.usage.output_tokens)
                    total_input += ev.usage.input_tokens
                    total_output += ev.usage.output_tokens
                    if ev.usage.reasoning_tokens:
                        span.set_attribute(
                            "gen_ai.usage.reasoning_tokens", ev.usage.reasoning_tokens
                        )
                if ev.cost is not None:
                    span.set_attribute("gen_ai.usage.cost", str(ev.cost.amount))
                    total_cost += ev.cost.amount
                if (ev.metadata or {}).get("replayed"):
                    span.set_attribute("cendor.replayed", True)  # G22 cassette replay
                for k, v in _call_content_attrs(ev).items():  # G17/G18
                    span.set_attribute(k, v)
            else:
                span = tracer.start_span(f"execute_tool {ev.name}", context=ctx, start_time=start)
                span.set_attribute("gen_ai.operation.name", "execute_tool")
                span.set_attribute("gen_ai.tool.name", ev.name)
                span.set_attribute("cendor.trace_id", trace_id)
                span.set_attribute("cendor.step", step_no)
                if agent:
                    span.set_attribute("gen_ai.agent.name", agent)
                _set_tool_attrs(span, ev)
                for k, v in _tool_source_attrs(ev.name).items():  # E-wave: local vs mcp + server
                    span.set_attribute(k, v)
                span.set_attribute("cendor.tool.outcome", _tool_outcome(ev))  # ok | error
                for k, v in _tool_content_attrs(ev).items():  # G17
                    span.set_attribute(k, v)
            span.end(end_time=end)

        bus.subscribe(on_event)
        try:
            yield
        finally:
            bus.unsubscribe(on_event)
            _co.exit_live_spans()
            # Usage/cost rollups on the root at close — parity with span_tree's finished totals.
            root.set_attribute("gen_ai.usage.input_tokens", total_input)
            root.set_attribute("gen_ai.usage.output_tokens", total_output)
            root.set_attribute("cendor.run.cost_usd", str(total_cost))
            # G-V4-2: stamp the run's agents on the root (span_tree already does this from
            # result.agents) so the runs-list Agents column fills for live-streamed runs too.
            root.set_attribute("cendor.run.agents", ",".join(agents_seen))
