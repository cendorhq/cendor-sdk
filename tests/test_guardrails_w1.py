"""Wave-1 guardrails additions in the SDK: the acttrace PII/secrets/entropy bridge
(``rules.pii`` / ``secrets`` / ``entropy``), ``Result.guardrail_decisions``, and the
``guardrail_mode="parallel"`` input-stage overlap. respx-mocked, no network.
"""

from __future__ import annotations

import json

import pytest
import respx

from cendor.sdk import Agent, GuardrailTripped, judge, rules, run, tool


def _agent(model="gpt-4o", **kw):
    return Agent(name="assistant", model=model, instructions="Be helpful.", **kw)


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


def _tool_msg(result) -> str:
    return next(m for m in result.messages if m.get("role") == "tool")["content"]


# --------------------------------------------------------------------------- PII / secrets bridge


def test_pii_redacts_email_before_send(build):
    agent = _agent(guardrails=[rules.pii(stage="input")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        result = run(agent, "please email alice@example.com the report")
    sent = json.dumps(_body(route))
    assert "alice@example.com" not in sent  # acttrace scrubbed the email before the provider saw it
    assert "<redacted>" in sent
    assert result.output == "ok"


def test_secrets_blocks_leaked_key_pre_spend(build):
    agent = _agent(guardrails=[rules.secrets(action="block", stage="input")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped) as ei:
            run(agent, "my key is sk-abcdEFGH1234ijklMNOP")
    assert route.call_count == 0  # blocked before the model — $0
    assert "api_key" in ei.value.decisions[-1].reason  # category named, not the value
    assert "sk-abcdEFGH1234ijklMNOP" not in ei.value.decisions[-1].reason


def test_pii_scans_tool_output_the_new_capability(build):
    @tool
    def fetch_record() -> str:
        """Fetch a user record."""
        return "user is bob@corp.example with card 4111 1111 1111 1111"

    agent = _agent(
        tools=[fetch_record],
        guardrails=[rules.pii(action="redact", stage="tool_output")],
    )
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("fetch_record", {})],
                    )
                ),
                build.resp(build.openai_chat("done")),
            ]
        )
        result = run(agent, "look it up")
    # the tool ran (raw result on the bus), but the model saw it scrubbed — guard() can't reach here
    assert (
        result.tool_steps[0].call.result == "user is bob@corp.example with card 4111 1111 1111 1111"
    )
    assert "bob@corp.example" not in _tool_msg(result)
    assert "<redacted>" in _tool_msg(result)


def test_entropy_flags_high_entropy_blob(build):
    # a long random-looking token the anchored patterns miss — entropy detection catches it
    blob = "aZ9k2Lp7Qw3Rt6Yx1Vb8Nc4Md0Ef5Gh2Ij7Kl"
    agent = _agent(guardrails=[rules.entropy(action="flag", stage="input")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        result = run(agent, f"here is a value: {blob}")
    assert route.call_count == 1  # flag does not block
    assert any(d.action == "flag" for d in result.guardrail_decisions)


# --------------------------------------------------------------- Result.guardrail_decisions


def test_result_carries_guardrail_decisions(build):
    agent = _agent(
        guardrails=[
            rules.keyword_deny(["watchword"], action="flag"),
            rules.regex_rule(r"sk-\w+", action="redact", stage="input"),
        ]
    )
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("done")))
        result = run(agent, "watchword and a key sk-abc123")
    actions = {d.action for d in result.guardrail_decisions}
    assert "flag" in actions and "redact" in actions
    assert all(d.stage == "input" for d in result.guardrail_decisions)


def test_result_guardrail_decisions_empty_when_none(build):
    agent = _agent()  # no guardrails
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        result = run(agent, "hello")
    assert result.guardrail_decisions == []


# --------------------------------------------------------------------------- parallel mode (async)


async def test_parallel_mode_passes_through(build):
    # a passing input guardrail in parallel mode: the run completes, the check overlapped the call
    agent = _agent(guardrails=[rules.keyword_deny(["forbidden"], action="flag")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        result = await run.aio(agent, "a clean request", guardrail_mode="parallel")
    assert route.call_count == 1 and result.output == "hi"


async def test_parallel_mode_block_still_raises(build):
    agent = _agent(guardrails=[rules.keyword_deny(["forbidden"], action="block")])
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            await run.aio(agent, "a forbidden request", guardrail_mode="parallel")


async def test_parallel_mode_via_agent_field(build):
    async def slow_judge(payload, ctx):
        return None  # passes

    agent = _agent(
        guardrail_mode="parallel",
        guardrails=[rules.custom(slow_judge, stage="input", name="judge")],
    )
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        result = await run.aio(agent, "anything")
    assert result.output == "ok"


async def test_invalid_guardrail_mode_raises(build):
    agent = _agent(guardrail_mode="nonsense", guardrails=[rules.keyword_deny(["x"])])
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        with pytest.raises(ValueError):  # mode validated on the async path
            await run.aio(agent, "hi")


# --------------------------------------------------------------------------- judge helper re-export


def test_judge_helper_is_reexported():
    # cendor.sdk re-exports the library judge helpers for building rules.llm_judge checks
    check = judge.judge(lambda s, u: '{"trip": false, "reason": "ok"}', "policy")
    g = rules.llm_judge(check)
    assert g.name == "llm_judge"
