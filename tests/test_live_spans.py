"""Live OTel spans: ``live_spans()`` emits gen_ai spans as each call completes (the streaming
counterpart to the post-hoc ``span_tree``). Offline via respx; uses the OTel in-memory exporter."""

from __future__ import annotations

import respx

from cendor.sdk import Agent, run, tool
from cendor.sdk.otel import live_spans


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
