"""Structured output — parse the final message into a dataclass or a JSON-schema dict (plan §7)."""

from __future__ import annotations

from dataclasses import dataclass

import respx

from cendor.sdk import Agent, run


@dataclass
class Weather:
    city: str
    conditions: str


def test_dataclass_output(build):
    agent = Agent(name="w", model="gpt-4o", instructions="Report weather.", output_type=Weather)
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat('{"city": "Paris", "conditions": "sunny"}'))
        )
        result = run(agent, "weather in Paris?")
    assert isinstance(result.output, Weather)
    assert result.output.city == "Paris"
    assert result.output.conditions == "sunny"


def test_dataclass_output_from_fenced_json(build):
    """FINDINGS 2026-07-18 B6: providers without a native JSON-schema mode (Anthropic/Ollama/HF)
    fence their output; the parser must strip the fence, not silently return the raw string."""
    agent = Agent(name="w", model="gpt-4o", instructions="Report weather.", output_type=Weather)
    fenced = '```json\n{"city": "Paris", "conditions": "sunny"}\n```'
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat(fenced)))
        result = run(agent, "weather in Paris?")
    assert isinstance(result.output, Weather)
    assert result.output.city == "Paris"


def test_dataclass_output_from_prose_wrapped_json(build):
    """B6: a JSON object embedded in prose is extracted (first balanced object)."""
    agent = Agent(name="w", model="gpt-4o", instructions="Report weather.", output_type=Weather)
    prose = 'Sure! {"city": "Paris", "conditions": "sunny"} — hope that helps.'
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat(prose)))
        result = run(agent, "weather in Paris?")
    assert isinstance(result.output, Weather)
    assert result.output.conditions == "sunny"


def test_json_schema_dict_output(build):
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    agent = Agent(name="q", model="gpt-4o", instructions="Answer.", output_type=schema)
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat('{"answer": "42"}'))
        )
        result = run(agent, "the answer?")
    assert result.output == {"answer": "42"}


def test_json_mode_requests_json_from_provider(build):
    """With an output_type, OpenAI uses its native json_schema format (beats json_object)."""
    agent = Agent(name="w", model="gpt-4o", instructions="Report.", output_type=Weather)
    with respx.mock as mock:
        route = mock.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat('{"city": "Paris", "conditions": "sunny"}'))
        )
        run(agent, "weather?")
    body = route.calls.last.request.content.decode()
    assert "response_format" in body
    assert "json_schema" in body  # native schema-constrained output
    assert "conditions" in body  # the derived schema is actually sent


def test_agent_extra_passthrough(build):
    """Agent.extra is merged into the outbound request (tool_choice / top_p / reasoning_effort)."""
    agent = Agent(
        name="a", model="gpt-4o", instructions="x", extra={"tool_choice": "required", "top_p": 0.1}
    )
    with respx.mock as mock:
        route = mock.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        run(agent, "hi")
    body = route.calls.last.request.content.decode()
    assert "tool_choice" in body
    assert "top_p" in body
