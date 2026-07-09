"""Bounded re-ask on an output-stage block (Wave 4). respx-mocked, no network.

When an output guardrail blocks the final answer and `reask_on_output_trip` > 0, the runner appends
a corrective message and re-asks the model instead of raising — up to the cap, then fail-closed.
"""

from __future__ import annotations

import pytest
import respx

from cendor.sdk import Agent, GuardrailTripped, rules, run


def _agent(**kw):
    return Agent(name="assistant", model="gpt-4o", instructions="Be helpful.", **kw)


def _out_block():
    return rules.keyword_deny(["bad"], stage="output", action="block")


def test_reask_revises_after_an_output_block(build):
    agent = _agent(guardrails=[_out_block()], reask_on_output_trip=2)
    with respx.mock:
        route = respx.post(build.CHAT_URL)
        route.side_effect = [
            build.resp(build.openai_chat("a bad answer")),  # blocked at output
            build.resp(build.openai_chat("a fine answer")),  # the re-ask passes
        ]
        result = run(agent, "hello")
    assert result.output == "a fine answer"
    assert route.call_count == 2  # initial + one re-ask
    # the block is still recorded as evidence, even though the run recovered
    assert any(d.action == "block" and d.stage == "output" for d in result.guardrail_decisions)


def test_reask_exhausted_raises_fail_closed(build):
    agent = _agent(guardrails=[_out_block()], reask_on_output_trip=1)
    with respx.mock:
        route = respx.post(build.CHAT_URL)
        route.side_effect = [
            build.resp(build.openai_chat("bad one")),
            build.resp(build.openai_chat("bad two")),  # re-ask still blocked → raise
        ]
        with pytest.raises(GuardrailTripped):
            run(agent, "hello")
    assert route.call_count == 2  # initial + one re-ask, both blocked


def test_no_reask_by_default_raises_immediately(build):
    agent = _agent(guardrails=[_out_block()])  # reask_on_output_trip defaults to 0
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("bad")))
        with pytest.raises(GuardrailTripped):
            run(agent, "hello")
    assert route.call_count == 1  # no re-ask — a single call, then the block raises


@pytest.mark.asyncio
async def test_reask_revises_async(build):
    agent = _agent(guardrails=[_out_block()], reask_on_output_trip=2)
    with respx.mock:
        route = respx.post(build.CHAT_URL)
        route.side_effect = [
            build.resp(build.openai_chat("bad answer")),
            build.resp(build.openai_chat("clean answer")),
        ]
        result = await run.aio(agent, "hello")
    assert result.output == "clean answer"
    assert route.call_count == 2


def test_reask_passes_through_when_first_answer_is_clean(build):
    agent = _agent(guardrails=[_out_block()], reask_on_output_trip=3)
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("all good"))
        )
        result = run(agent, "hello")
    assert result.output == "all good"
    assert route.call_count == 1  # no block → no re-ask
