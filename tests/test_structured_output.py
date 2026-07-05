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
    """When output_type is set, the outbound request asks the provider for JSON."""
    agent = Agent(name="w", model="gpt-4o", instructions="Report.", output_type=Weather)
    with respx.mock as mock:
        route = mock.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat('{"city": "Paris", "conditions": "sunny"}'))
        )
        run(agent, "weather?")
    body = route.calls.last.request.content.decode()
    assert "response_format" in body
    assert "json_object" in body
