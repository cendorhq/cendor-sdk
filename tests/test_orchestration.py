"""Multi-agent orchestration: handoff, supervisor/router, sequential, parallel — with nested trace
correlation, per-agent governance, and one verifiable audit trail (plan §7 Phase 2)."""

from __future__ import annotations

import pytest
import respx
from cendor.acttrace import verify

from cendor.sdk import (
    Agent,
    AuditLog,
    BudgetExceeded,
    Session,
    parallel,
    parallel_async,
    run,
    sequential,
    supervisor,
)


def test_supervisor_routes_to_subagent_with_correlated_audit(build, tmp_path):
    researcher = Agent(name="researcher", model="gpt-4o", instructions="Do the research.")
    coordinator = Agent(name="coordinator", model="gpt-4o", instructions="Route to a specialist.")
    log = AuditLog(system="team", risk_tier="limited", path=str(tmp_path / "audit.jsonl"))

    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                # coordinator decides to hand off
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[
                            build.openai_tool_call(
                                "transfer_to_researcher", {"reason": "needs research"}
                            )
                        ],
                    )
                ),
                # researcher answers
                build.resp(build.openai_chat("Here is the research on X.")),
            ]
        )
        result = supervisor(coordinator, [researcher], "Research X", audit=log)
    log.detach()

    assert result.output == "Here is the research on X."
    assert result.agents == ["coordinator", "researcher"]

    # nested-trace correlation: one tree — every step's trace_id starts with the parent run id
    assert all(s.trace_id.startswith(result.trace_id) for s in result.steps)
    assert {s.agent for s in result.steps} == {"coordinator", "researcher"}

    # one verifiable audit trail with a decision per agent segment
    ok, detail = verify(str(tmp_path / "audit.jsonl"))
    assert ok, detail
    decisions = [e for e in log.entries if e.type == "decision"]
    assert len(decisions) == 2  # coordinator + researcher


def test_run_list_is_handoff_team(build):
    """run([entry, peer], ...) is a handoff team — the entry can transfer to a peer."""
    writer = Agent(name="writer", model="gpt-4o", instructions="Write the brief.")
    planner = Agent(
        name="planner", model="gpt-4o", instructions="Plan, then hand off.", handoffs=["writer"]
    )
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("transfer_to_writer", {})],
                    )
                ),
                build.resp(build.openai_chat("The brief.")),
            ]
        )
        result = run([planner, writer], "Research and write a brief")
    assert result.output == "The brief."
    assert result.agents == ["planner", "writer"]


def test_handoff_across_providers(build):
    """Conversation is canonical, so control transfers OpenAI -> Anthropic seamlessly."""
    writer = Agent(name="writer", model="claude-opus-4-8", instructions="Write.")
    planner = Agent(
        name="planner", model="gpt-4o", instructions="Plan, then hand off.", handoffs=["writer"]
    )
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("transfer_to_writer", {})],
                    )
                )
            ]
        )
        respx.post(build.ANTHROPIC_URL).mock(
            return_value=build.resp(build.anthropic_message(text="The finished brief."))
        )
        result = run([planner, writer], "Research and write")
    assert result.output == "The finished brief."
    assert result.agents == ["planner", "writer"]
    assert result.llm_steps[0].call.provider == "openai"
    assert result.llm_steps[-1].call.provider == "anthropic"


def test_per_agent_budget_enforced(build):
    cheap = Agent(name="cheap", model="gpt-4o", instructions="x", max_usd=0.0000001)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(BudgetExceeded):
            run([cheap], "hello")
        assert route.call_count == 0  # the agent's own budget blocked it pre-flight


def test_sequential_pipeline(build):
    first = Agent(name="first", model="gpt-4o", instructions="Draft.")
    second = Agent(name="second", model="gpt-4o", instructions="Polish.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(build.openai_chat("a draft")),
                build.resp(build.openai_chat("the final brief")),
            ]
        )
        result = sequential([first, second], "start")
    assert result.output == "the final brief"
    assert result.agents == ["first", "second"]
    assert len(result.llm_steps) == 2
    assert all(s.trace_id.startswith(result.trace_id) for s in result.steps)


def test_parallel_fanout(build):
    a = Agent(name="a", model="gpt-4o", instructions="A")
    b = Agent(name="b", model="gpt-4o", instructions="B")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(build.openai_chat("out-a")),
                build.resp(build.openai_chat("out-b")),
            ]
        )
        result = parallel([a, b], "same input")
    assert set(result.output.values()) == {"out-a", "out-b"}
    assert set(result.output.keys()) == {"a", "b"}
    assert len(result.llm_steps) == 2


async def test_parallel_async_fanout(build):
    a = Agent(name="a", model="gpt-4o", instructions="A")
    b = Agent(name="b", model="gpt-4o", instructions="B")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(build.openai_chat("out-1")),
                build.resp(build.openai_chat("out-2")),
            ]
        )
        result = await parallel_async([a, b], "x")
    assert set(result.output.keys()) == {"a", "b"}
    assert set(result.output.values()) == {"out-1", "out-2"}


def test_session_local_persistence(build, tmp_path):
    path = str(tmp_path / "session.json")
    session = Session()
    agent = Agent(name="a", model="gpt-4o", instructions="Remember.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("Hi Alice.")))
        run(agent, "My name is Alice.", session=session)
    session.save(path)

    loaded = Session.load(path)
    assert len(loaded) == len(session) > 0
    assert loaded.messages == session.messages
