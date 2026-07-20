"""Live OTel spans: ``live_spans()`` emits gen_ai spans as each call completes (the streaming
counterpart to the post-hoc ``span_tree``). Offline via respx; uses the OTel in-memory exporter."""

from __future__ import annotations

import respx

from cendor.sdk import Agent, run, tool
from cendor.sdk.otel import live_spans, span_tree


def _mem_tracer():
    """A TracerProvider wired to an in-memory exporter; returns (tracer, exporter)."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


@tool
def get_weather(city: str) -> str:
    """Weather for a city."""
    return f"Sunny in {city}"


def test_live_spans_emitted_under_a_run_span(build):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="Use tools.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("get_weather", {"city": "Paris"})],
                    )
                ),
                build.resp(build.openai_chat("It's sunny in Paris.")),
            ]
        )
        with live_spans(tracer=tracer):
            result = run(agent, "weather in Paris?")

    assert result.output == "It's sunny in Paris."
    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "agent.run" in names
    assert any(n.startswith("chat ") for n in names)
    assert any(n.startswith("execute_tool ") for n in names)

    chat = next(s for s in spans if s.name.startswith("chat "))
    assert chat.parent is not None  # nested under the run span
    assert chat.attributes["gen_ai.request.model"] == "gpt-4o"
    assert chat.attributes["gen_ai.system"] == "openai"

    # "live": each call span finished as the call completed — before the root run span closed.
    root = next(s for s in spans if s.name == "agent.run")
    assert chat.end_time <= root.end_time


def test_live_spans_never_breaks_a_run(build):
    # With no explicit tracer (global no-op provider in tests) it must still just run the block.
    agent = Agent(name="a", model="gpt-4o", instructions="hi")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with live_spans():
            result = run(agent, "hi")
    assert result.output == "hi"


def test_live_spans_stamps_conversation_id(build):
    # W6: a known session/conversation id lands on the root as gen_ai.conversation.id.
    tracer, exporter = _mem_tracer()
    agent = Agent(name="a", model="gpt-4o", instructions="hi")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with live_spans(tracer=tracer, conversation_id="chat-42"):
            run(agent, "hi")
    root = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert root.attributes["gen_ai.conversation.id"] == "chat-42"


def test_span_tree_conversation_id_present_only_when_given(build):
    tracer, exporter = _mem_tracer()
    agent = Agent(name="a", model="gpt-4o", instructions="hi")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        result = run(agent, "hi")

    assert span_tree(result, tracer=tracer, conversation_id="chat-42") is True
    root = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert root.attributes["gen_ai.conversation.id"] == "chat-42"

    # Default: no key stamped (nothing leaks when the caller isn't grouping a conversation).
    exporter.clear()
    assert span_tree(result, tracer=tracer) is True
    root2 = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert "gen_ai.conversation.id" not in root2.attributes


# ---------------------------------------------------------------- V2: G13 parity + G14 run label


def test_live_spans_parity_agent_step_rollups_label(build):  # G13b + G14
    tracer, exporter = _mem_tracer()
    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="Use tools.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("get_weather", {"city": "Paris"})],
                    )
                ),
                build.resp(build.openai_chat("It's sunny in Paris.")),
            ]
        )
        with live_spans(tracer=tracer, label="weather triage"):
            run(agent, "weather in Paris?")

    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "agent.run")
    # G14: the run label lands on the root (a chosen tag — never the prompt).
    assert root.attributes["cendor.run.label"] == "weather triage"
    # G13b: the root learns the run id + carries usage/cost rollups at close.
    assert root.attributes["cendor.run.id"]  # non-empty (learned from the first event)
    assert root.attributes["cendor.trace_id"] == root.attributes["cendor.run.id"]
    assert root.attributes["gen_ai.usage.input_tokens"] >= 0
    assert "cendor.run.cost_usd" in root.attributes

    # G13a/b: each child carries the making agent + a 1-based cendor.step ordinal.
    children = [s for s in spans if s.name.startswith(("chat ", "execute_tool "))]
    assert children, "expected child call spans"
    for c in children:
        assert c.attributes["gen_ai.agent.name"] == "assistant"
    steps = sorted(c.attributes["cendor.step"] for c in children)
    assert steps == list(range(1, len(children) + 1))  # 1-based, contiguous


def test_span_tree_step_and_label(build):  # G13 (span_tree) + G14
    tracer, exporter = _mem_tracer()
    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="Use tools.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("get_weather", {"city": "Paris"})],
                    )
                ),
                build.resp(build.openai_chat("It's sunny in Paris.")),
            ]
        )
        result = run(agent, "weather in Paris?")

    assert span_tree(result, tracer=tracer, label="weather triage") is True
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "agent.run")
    assert root.attributes["cendor.run.label"] == "weather triage"
    children = [s for s in spans if s.name.startswith(("chat ", "execute_tool "))]
    for c in children:
        assert c.attributes["gen_ai.agent.name"] == "assistant"
        assert c.attributes["cendor.step"] >= 1

    # No label => no attribute (nothing invented).
    exporter.clear()
    assert span_tree(result, tracer=tracer) is True
    root2 = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert "cendor.run.label" not in root2.attributes
