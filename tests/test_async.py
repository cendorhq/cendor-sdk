"""Async parity: ``run.aio`` drives the same loop over async provider clients (plan §7)."""

from __future__ import annotations

import respx

from cendor.sdk import Agent, Session, run, tool


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"


async def test_async_openai_run(build):
    agent = Agent(name="a", model="gpt-4o", instructions="Be brief.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("Hello async.")))
        result = await run.aio(agent, "Hi")
    assert result.output == "Hello async."
    assert result.cost.amount > 0
    assert result.trace_id


async def test_async_tool_loop(build):
    agent = Agent(name="a", model="gpt-4o", tools=[get_weather], instructions="Use tools.")
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
        result = await run.aio(agent, "weather in Paris?")
    assert result.output == "It's sunny in Paris."
    assert [s.name for s in result.tool_steps] == ["get_weather"]
    assert len({s.trace_id for s in result.steps}) == 1


async def test_async_anthropic_run(build):
    agent = Agent(name="a", model="claude-opus-4-8", instructions="Be brief.")
    with respx.mock:
        respx.post(build.ANTHROPIC_URL).mock(
            return_value=build.resp(build.anthropic_message(text="Hello from Claude."))
        )
        result = await run.aio(agent, "Hi")
    assert result.output == "Hello from Claude."
    assert result.llm_steps[0].call.provider == "anthropic"


async def test_async_session_memory(build):
    agent = Agent(name="a", model="gpt-4o", instructions="Remember.")
    session = Session()
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(build.openai_chat("Nice to meet you, Alice.")),
                build.resp(build.openai_chat("Your name is Alice.")),
            ]
        )
        await run.aio(agent, "My name is Alice.", session=session)
        result = await run.aio(agent, "What's my name?", session=session)
    assert "Alice" in result.output
    # session accumulated both turns
    assert sum(1 for m in session.messages if m["role"] == "user") == 2
