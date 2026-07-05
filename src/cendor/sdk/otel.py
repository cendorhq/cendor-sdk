"""Full-run OpenTelemetry span tree for a completed run (plan §7 Phase 3).

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


def _group_by_agent(steps: list[Step]) -> list[tuple[str, list[Step]]]:
    """Contiguous groups of steps by agent, preserving order."""
    groups: list[tuple[str, list[Step]]] = []
    for step in steps:
        if groups and groups[-1][0] == step.agent:
            groups[-1][1].append(step)
        else:
            groups.append((step.agent, [step]))
    return groups


def span_tree(result: Result, tracer: Any = None) -> bool:
    """Emit a ``gen_ai`` span tree for ``result``. ``True`` if emitted, ``False`` if OTel absent.

    ```python
    from cendor.sdk import run
    from cendor.sdk.otel import span_tree
    result = run(agent, "...")
    span_tree(result)   # -> spans exported to your configured OTel pipeline
    ```
    """
    try:
        from opentelemetry import trace as ot
    except ImportError:
        return False

    tracer = tracer or ot.get_tracer("cendor.sdk")
    with tracer.start_as_current_span("agent.run") as root:
        root.set_attribute("gen_ai.operation.name", "agent")
        root.set_attribute("cendor.run.id", result.trace_id)
        root.set_attribute("cendor.run.agents", ",".join(result.agents))
        root.set_attribute("gen_ai.usage.input_tokens", result.usage.input_tokens)
        root.set_attribute("gen_ai.usage.output_tokens", result.usage.output_tokens)
        root.set_attribute("cendor.run.cost_usd", str(result.cost.amount))

        for agent_name, group in _group_by_agent(result.steps):
            with tracer.start_as_current_span(f"agent {agent_name}") as agent_span:
                agent_span.set_attribute("gen_ai.operation.name", "invoke_agent")
                agent_span.set_attribute("gen_ai.agent.name", agent_name)
                for step in group:
                    if step.kind == "llm" and isinstance(step.call, LLMCall):
                        with tracer.start_as_current_span(f"chat {step.name}") as s:
                            s.set_attribute("gen_ai.operation.name", "chat")
                            s.set_attribute("gen_ai.system", step.call.provider)
                            s.set_attribute("gen_ai.request.model", step.call.model)
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
                    else:
                        with tracer.start_as_current_span(f"execute_tool {step.name}") as s:
                            s.set_attribute("gen_ai.operation.name", "execute_tool")
                            s.set_attribute("gen_ai.tool.name", step.name)
                            _set_tool_attrs(s, step.call)
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
def live_spans(tracer: Any = None, *, name: str = "agent.run") -> Any:
    """Emit ``gen_ai`` spans **live** as a run progresses — the streaming counterpart to
    :func:`span_tree` (which builds the tree post-hoc from a finished ``Result``). Wrap a run:

    ```python
    from cendor.sdk import run
    from cendor.sdk.otel import live_spans
    with live_spans():
        result = run(agent, "...")
    ```

    A root ``agent.run`` span brackets the block; a child ``chat {model}`` / ``execute_tool {name}``
    span is emitted the moment each call completes (its start time backdated by the call's
    ``latency_ms`` so the duration is accurate), so a live backend sees the trajectory in real time
    rather than all at once at the end. Works for single- and multi-agent runs (each span carries a
    ``cendor.trace_id``). A **no-op** (still runs the block) if OpenTelemetry isn't installed.
    """
    try:
        from opentelemetry import trace as ot
    except ImportError:
        yield
        return

    import time

    from cendor.core import bus

    tracer = tracer or ot.get_tracer("cendor.sdk")
    with tracer.start_as_current_span(name) as root:
        root.set_attribute("gen_ai.operation.name", "agent")
        ctx = ot.set_span_in_context(root)

        def on_event(ev: Any) -> None:
            if not isinstance(ev, (LLMCall, ToolCall)):
                return
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
                span.set_attribute("cendor.trace_id", getattr(ev, "trace_id", ""))
                _set_call_attrs(span, ev)
                if ev.usage is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", ev.usage.input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", ev.usage.output_tokens)
                    if ev.usage.reasoning_tokens:
                        span.set_attribute(
                            "gen_ai.usage.reasoning_tokens", ev.usage.reasoning_tokens
                        )
                if ev.cost is not None:
                    span.set_attribute("gen_ai.usage.cost", str(ev.cost.amount))
            else:
                span = tracer.start_span(f"execute_tool {ev.name}", context=ctx, start_time=start)
                span.set_attribute("gen_ai.operation.name", "execute_tool")
                span.set_attribute("gen_ai.tool.name", ev.name)
                span.set_attribute("cendor.trace_id", getattr(ev, "trace_id", ""))
                _set_tool_attrs(span, ev)
            span.end(end_time=end)

        bus.subscribe(on_event)
        try:
            yield
        finally:
            bus.unsubscribe(on_event)
