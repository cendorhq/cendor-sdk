"""Opt-in content on run spans (G17/G18), auto conversation id (G19), replay flag (G22).

Content is OFF by default — the headline assertion is that nothing content-bearing appears on a
span unless capture is explicitly turned on. Offline via respx + the OTel in-memory exporter.
"""

from __future__ import annotations

import pytest
import respx
from cendor.core import otel as core_otel
from cendor.core.types import LLMCall

from cendor.sdk import Agent, Session, run, tool
from cendor.sdk.otel import live_spans, span_tree
from cendor.sdk.result import Result, Step


@pytest.fixture(autouse=True)
def _reset_capture():
    core_otel._reset_capture()
    yield
    core_otel._reset_capture()


def _mem_tracer():
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


def _run_with_tool(build, tracer, *, session=None):
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
            return run(agent, "weather in Paris?", session=session)


def test_content_off_by_default(build):
    tracer, exporter = _mem_tracer()
    _run_with_tool(build, tracer)
    for s in exporter.get_finished_spans():
        assert core_otel.GENAI_INPUT_MESSAGES not in s.attributes
        assert core_otel.GENAI_OUTPUT_MESSAGES not in s.attributes
        assert core_otel.CENDOR_TOOL_ARGUMENTS not in s.attributes


def test_content_on_captures_messages_and_tool_values(build):
    core_otel.capture_content()
    tracer, exporter = _mem_tracer()
    _run_with_tool(build, tracer)
    spans = exporter.get_finished_spans()
    chat = [s for s in spans if s.name.startswith("chat ")]
    assert chat
    joined_in = " ".join(str(s.attributes.get(core_otel.GENAI_INPUT_MESSAGES, "")) for s in chat)
    assert "weather in Paris" in joined_in
    joined_out = " ".join(str(s.attributes.get(core_otel.GENAI_OUTPUT_MESSAGES, "")) for s in chat)
    assert "sunny in paris" in joined_out.lower()
    tool_spans = [s for s in spans if s.name.startswith("execute_tool ")]
    assert tool_spans
    assert "Paris" in str(tool_spans[0].attributes.get(core_otel.CENDOR_TOOL_ARGUMENTS, ""))


def test_conversation_id_auto_from_session_live(build):
    tracer, exporter = _mem_tracer()
    result = _run_with_tool(build, tracer, session=Session(id="chat-42"))
    root = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert root.attributes["gen_ai.conversation.id"] == "chat-42"
    assert result.conversation_id == "chat-42"


def test_conversation_id_span_tree_from_result():
    tracer, exporter = _mem_tracer()
    result = Result(output="ok", steps=[], trace_id="t1", conversation_id="chat-9", agents=["a"])
    span_tree(result, tracer=tracer)
    root = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert root.attributes["gen_ai.conversation.id"] == "chat-9"


def test_replayed_flag_on_span_tree():
    tracer, exporter = _mem_tracer()
    call = LLMCall(
        id="1", provider="openai", model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
    )
    call.metadata["replayed"] = True
    result = Result(
        output="ok",
        steps=[Step(agent="a", kind="llm", call=call)],
        trace_id="t1",
        agents=["a"],
    )
    span_tree(result, tracer=tracer)
    chat = next(s for s in exporter.get_finished_spans() if s.name.startswith("chat "))
    assert chat.attributes.get("cendor.replayed") is True


def test_explicit_conversation_id_wins(build):
    tracer, exporter = _mem_tracer()
    agent = Agent(name="a", model="gpt-4o", instructions="x")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("hi"))])
        with live_spans(tracer=tracer, conversation_id="explicit"):
            run(agent, "hi", session=Session(id="from-session"))
    root = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert root.attributes["gen_ai.conversation.id"] == "explicit"
