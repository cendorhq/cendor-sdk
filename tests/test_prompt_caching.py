"""Prompt caching: ``Agent(cache=True)`` puts an Anthropic ``cache_control`` breakpoint on the
system prompt + last tool, and cache-read tokens flow into ``Result.usage``. Default (no cache)
leaves the request untouched. Offline — the real anthropic SDK with HTTP mocked by respx."""

from __future__ import annotations

import json

import httpx
import respx

from cendor.sdk import Agent, run, tool

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


@tool
def get_weather(city: str) -> str:
    """Weather for a city."""
    return f"Sunny in {city}"


def _answer(text: str, *, input_t: int = 100, output_t: int = 5, cache_read: int = 0) -> dict:
    usage: dict = {"input_tokens": input_t, "output_tokens": output_t}
    if cache_read:
        usage["cache_read_input_tokens"] = cache_read
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-8",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage,
    }


def test_cache_injects_breakpoints_and_accounts_cached_tokens():
    agent = Agent(
        name="a",
        model="claude-opus-4-8",
        instructions="You are helpful.",
        tools=[get_weather],
        cache=True,
        api_key="sk-ant-test",
    )
    with respx.mock:
        route = respx.post(ANTHROPIC_URL).mock(
            return_value=httpx.Response(200, json=_answer("Hi.", cache_read=80))
        )
        result = run(agent, "hello")

    body = json.loads(route.calls[0].request.content)
    # system became a block list with a cache_control breakpoint on its last block
    assert isinstance(body["system"], list)
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["system"][-1]["text"] == "You are helpful."
    # the last tool definition carries the breakpoint (caches the whole tool prefix)
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    # cache-read tokens surfaced by Anthropic flow into the aggregate usage
    assert result.usage.cached_tokens == 80


def test_default_leaves_request_uncached():
    agent = Agent(
        name="a",
        model="claude-opus-4-8",
        instructions="You are helpful.",
        tools=[get_weather],
        api_key="sk-ant-test",
    )  # cache defaults to False
    with respx.mock:
        route = respx.post(ANTHROPIC_URL).mock(
            return_value=httpx.Response(200, json=_answer("Hi."))
        )
        run(agent, "hello")

    body = json.loads(route.calls[0].request.content)
    assert body["system"] == "You are helpful."  # plain string, untouched
    assert "cache_control" not in body["tools"][-1]
