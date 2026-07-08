"""Governance composes through core's seams with zero SDK glue: budgets block, guard redacts
before send, spend is attributed, and the audit chain records it all (plan §7 Phase 1)."""

from __future__ import annotations

import pytest
import respx

from cendor.sdk import (
    Agent,
    AuditLog,
    BudgetExceeded,
    Policy,
    budget,
    guard,
    report,
    run,
    track,
    verify,
)


def _agent(model="gpt-4o"):
    return Agent(name="a", model=model, instructions="Be brief.")


def test_budget_block_stops_the_call_pre_flight(build):
    """on_exceed='block' raises BEFORE the over-budget call runs — the provider is never hit."""
    agent = _agent()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(BudgetExceeded):
            with budget(usd=0.0000001, on_exceed="block"):
                run(agent, "hello")
        assert route.call_count == 0  # blocked pre-flight


def test_budget_raise_stops_after_crossing(build):
    """on_exceed='raise' lets the crossing call return, then raises (post-flight)."""
    agent = _agent()
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("hi", prompt=1000, completion=1000))
        )
        with pytest.raises(BudgetExceeded):
            with budget(usd=0.0000001, on_exceed="raise"):
                run(agent, "hello")
        assert route.call_count == 1


def test_guard_redacts_pii_before_send(build, tmp_path):
    """guard(Policy) scrubs PII from the outbound request — the provider never sees the email."""
    agent = _agent()
    log = AuditLog(system="support", path=str(tmp_path / "audit.jsonl"))
    with respx.mock as mock:
        route = mock.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        with guard(Policy.default(), audit=log):
            run(agent, "email me at alice@example.com", audit=log)
    log.detach()

    body = route.calls.last.request.content.decode()
    assert "alice@example.com" not in body
    assert "<redacted>" in body

    ok, detail = verify(str(tmp_path / "audit.jsonl"))
    assert ok, detail
    flags = [e for e in log.entries if e.type == "policy_flag"]
    assert any(f.payload.get("action") == "redacted" for f in flags)


def test_track_attributes_spend_by_feature(build):
    agent = _agent()
    with respx.mock as mock:
        mock.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("hi", prompt=100, completion=50))
        )
        with track(feature="support"):
            run(agent, "hello")

    r = report(group_by=["feature"])
    assert r.total().amount > 0
    assert r.assert_under(usd=1.0, feature="support") is True


def test_downgrade_reroutes_to_cheaper_model(build):
    """on_exceed='downgrade' reroutes the model id; the emitted step reflects the cheaper model."""
    agent = _agent("gpt-4o")
    with respx.mock as mock:
        mock.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("hi", prompt=5000, completion=50))
        )
        with budget(usd=0.0001, on_exceed="downgrade", downgrade={"gpt-4o": "gpt-4o-mini"}):
            result = run(agent, "hello")
    assert result.llm_steps[0].call.model == "gpt-4o-mini"


# --------------------------------------------------------------------------- Agent.max_usd cap
#
# Regression: Agent.max_usd was enforced only inside the multi-agent orchestrator; a single-agent
# run() / run.aio() / run.stream() billed with no cap. It is now a pre-flight ceiling on every path.


def test_agent_max_usd_blocks_single_agent_run(build):
    """Agent.max_usd is a pre-flight ceiling on a single-agent run() — the provider is never hit."""
    agent = Agent(name="a", model="gpt-4o", instructions="Be brief.", max_usd=0.0000001)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(BudgetExceeded):
            run(agent, "hello")
        assert route.call_count == 0  # blocked pre-flight (was silently ignored before the fix)


async def test_agent_max_usd_blocks_single_agent_run_async(build):
    """The cap is enforced on the async single-agent path too."""
    agent = Agent(name="a", model="gpt-4o", instructions="Be brief.", max_usd=0.0000001)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(BudgetExceeded):
            await run.aio(agent, "hello")
        assert route.call_count == 0


def test_agent_max_usd_blocks_single_agent_stream(build):
    """The cap is enforced on the single-agent streaming path too."""
    agent = Agent(name="a", model="gpt-4o", instructions="Be brief.", max_usd=0.0000001)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(BudgetExceeded):
            list(run.stream(agent, "hello"))
        assert route.call_count == 0
