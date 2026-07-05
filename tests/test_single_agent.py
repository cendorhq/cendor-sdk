"""Governed single-agent runs across OpenAI *and* Anthropic — respx-mocked, no network (plan §7).

Asserts the Phase-1 exit criteria: usage/cost/reasoning captured, a tool call executes, the audit
chain verifies, and every step correlates under one ``trace_id``.
"""

from __future__ import annotations

import respx
from cendor.acttrace import verify

from cendor.sdk import Agent, AuditLog, run, tool


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"


def _weather_agent(model: str) -> Agent:
    return Agent(
        name="assistant",
        model=model,
        tools=[get_weather],
        instructions="Answer using tools when helpful.",
    )


def test_openai_governed_run_with_tool(build, tmp_path):
    agent = _weather_agent("gpt-4o")
    log = AuditLog(system="support", risk_tier="limited", path=str(tmp_path / "audit.jsonl"))
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        prompt=50,
                        completion=15,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("get_weather", {"city": "Paris"})],
                    )
                ),
                build.resp(build.openai_chat("It's sunny in Paris.", prompt=80, completion=10)),
            ]
        )
        result = run(agent, "What's the weather in Paris?", audit=log)
    log.detach()

    # output + tool execution
    assert result.output == "It's sunny in Paris."
    assert [s.name for s in result.tool_steps] == ["get_weather"]
    assert result.tool_steps[0].call.result == "Sunny in Paris"

    # usage + cost captured (priced in Decimal)
    assert result.usage.input_tokens == 130
    assert result.usage.output_tokens == 25
    assert result.cost.amount > 0

    # correlation: two LLM turns + one tool call, all one trace_id
    assert len(result.llm_steps) == 2
    assert len({s.trace_id for s in result.steps}) == 1
    assert result.trace_id == result.steps[0].trace_id

    # audit chain verifies and recorded the decision + calls
    ok, detail = verify(str(tmp_path / "audit.jsonl"))
    assert ok, detail
    types = [e.type for e in log.entries]
    assert "decision" in types
    assert "llm_call" in types
    assert "tool_call" in types
    # the llm_call entries are correlated to the run's decision
    llm = next(e for e in log.entries if e.type == "llm_call")
    assert llm.payload["decision_id"] is not None
    assert llm.payload["cost"] is not None


def test_anthropic_governed_run_with_tool(build, tmp_path):
    agent = _weather_agent("claude-opus-4-8")
    log = AuditLog(system="support", risk_tier="limited", path=str(tmp_path / "audit.jsonl"))
    with respx.mock:
        respx.post(build.ANTHROPIC_URL).mock(
            side_effect=[
                build.resp(
                    build.anthropic_message(
                        text="Let me check.",
                        input_t=50,
                        output_t=15,
                        stop="tool_use",
                        tool_use={
                            "id": "toolu_1",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        },
                    )
                ),
                build.resp(
                    build.anthropic_message(text="It's sunny in Paris.", input_t=80, output_t=10)
                ),
            ]
        )
        result = run(agent, "What's the weather in Paris?", audit=log)
    log.detach()

    assert result.output == "It's sunny in Paris."
    assert [s.name for s in result.tool_steps] == ["get_weather"]
    assert result.tool_steps[0].call.result == "Sunny in Paris"
    assert result.usage.input_tokens == 130
    assert result.cost.amount > 0
    assert len({s.trace_id for s in result.steps}) == 1

    ok, detail = verify(str(tmp_path / "audit.jsonl"))
    assert ok, detail
    assert result.llm_steps[0].call.provider == "anthropic"


def test_reasoning_tokens_captured(build):
    """A reasoning model's reasoning_tokens surface in the aggregate usage."""
    agent = Agent(name="thinker", model="gpt-4o", instructions="Think.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=build.resp(
                build.openai_chat("Done.", prompt=200, completion=1200, reasoning=1000)
            )
        )
        result = run(agent, "hard problem")
    assert result.output == "Done."
    assert result.usage.reasoning_tokens == 1000
    assert result.usage.output_tokens == 1200


def test_ungoverned_run_works_on_core_alone(build):
    """No budget/guard/audit — a bare run still works (governance is optional)."""
    agent = Agent(name="a", model="gpt-4o", instructions="Be brief.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("Hi there.")))
        result = run(agent, "Hi")
    assert result.output == "Hi there."
    assert result.cost.amount > 0
    assert result.trace_id  # correlation still set


def test_provider_inferred_from_model():
    assert Agent(name="a", model="gpt-4o", instructions="").provider_impl.name == "openai"
    assert (
        Agent(name="b", model="claude-opus-4-8", instructions="").provider_impl.name == "anthropic"
    )
    assert Agent(name="c", model="gemini-2.0-flash", instructions="").provider_impl.name == "google"
