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

    from cendor.core import bus
    from cendor.core import otel as _co

    from ._governance import current_agent, current_conversation

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

        def on_event(ev: Any) -> None:
            nonlocal step_no, total_input, total_output, total_cost, run_id_set, conv_set, family
            if not isinstance(ev, (LLMCall, ToolCall)):
                return
            trace_id = getattr(ev, "trace_id", "") or ""
            if not run_id_set and trace_id:
                # Learn the run-family root from the first observed event: the segment before the
                # first ":" (orchestration segments are ``{parent}:{agent}#{seg}``; a single-agent
                # run is the bare run id) — matches the collector's match + the monitor's key.
                family = trace_id.split(":", 1)[0]
                root.set_attribute("cendor.run.id", family)
                root.set_attribute("cendor.trace_id", family)
                run_id_set = True
            # GLR-3: render only events from THIS run family — a concurrent run sharing the process
            # bus must not pollute this run's steps / rollups / children.
            if family and trace_id != family and not trace_id.startswith(family + ":"):
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
