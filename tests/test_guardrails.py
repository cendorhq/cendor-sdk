"""Agent(guardrails=[...]) wired at the four stages — respx-mocked, no network (Phase B).

Covers: input block pre-spend (the provider is never called), input redact rewriting the outgoing
messages, a tool_call block returning "[blocked …]" while the loop continues, tool_output redaction,
output block/redact, the per-run override, an orchestrated team, streaming, async paths, and that
every decision lands on the audit chain as a `guardrail_decision`, correlated with the run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import respx
from cendor.acttrace import verify
from cendor.core import instrument

from cendor.sdk import Agent, AuditLog, GuardrailTripped, rules, run, tool


def _agent(model="gpt-4o", **kw):
    return Agent(name="assistant", model=model, instructions="Be helpful.", **kw)


def _body(route) -> dict:
    """The JSON body of the last request the route received."""
    return json.loads(route.calls.last.request.content)


def _tool_msg(result) -> str:
    """The content of the (first) tool-result turn in the conversation the model saw."""
    return next(m for m in result.messages if m.get("role") == "tool")["content"]


# --------------------------------------------------------------------------- input stage


def test_input_block_is_pre_spend(build):
    agent = _agent(guardrails=[rules.keyword_deny(["forbidden"], action="block")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            run(agent, "a forbidden request")
    assert route.call_count == 0  # blocked before the model was ever called — $0 spent


def test_input_redact_rewrites_outgoing_messages(build):
    agent = _agent(guardrails=[rules.regex_rule(r"sk-\w+", action="redact", stage="input")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        result = run(agent, "my key is sk-abc123secret")
    sent = json.dumps(_body(route))
    assert "sk-abc123secret" not in sent and "[redacted]" in sent  # provider got cleaned content
    assert result.output == "ok"


def test_input_flag_records_and_proceeds(build):
    agent = _agent(guardrails=[rules.keyword_deny(["watchword"], action="flag")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("done")))
        result = run(agent, "contains a watchword here")
    assert route.call_count == 1 and result.output == "done"  # flagged but not blocked


# --------------------------------------------------------------------------- tool_call stage


_SIDE_EFFECTS: list[str] = []


@tool
def run_command(cmd: str) -> str:
    """Run a shell command."""
    _SIDE_EFFECTS.append(cmd)
    return f"ran: {cmd}"


def test_tool_call_block_returns_blocked_and_loop_continues(build):
    _SIDE_EFFECTS.clear()
    agent = _agent(
        tools=[run_command],
        guardrails=[rules.keyword_deny(["rm -rf"], stage="tool_call", action="block")],
    )
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("run_command", {"cmd": "rm -rf /"})],
                    )
                ),
                build.resp(build.openai_chat("I can't run that.")),
            ]
        )
        result = run(agent, "delete everything")
    assert result.output == "I can't run that."  # the loop continued past the blocked tool
    assert _SIDE_EFFECTS == []  # the tool's side effect never happened
    # the blocked tool never ran, so no ToolCall on the bus; the model saw a "[blocked …]" result
    assert result.tool_steps == []
    assert _tool_msg(result).startswith("[blocked by keyword_deny]")


def test_tool_output_redaction(build):
    @tool
    def fetch_secret() -> str:
        """Fetch a value."""
        return "the token is sk-LEAK9999"

    agent = _agent(
        tools=[fetch_secret],
        guardrails=[rules.regex_rule(r"sk-\w+", action="redact", stage="tool_output")],
    )
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("fetch_secret", {})],
                    )
                ),
                build.resp(build.openai_chat("done")),
            ]
        )
        result = run(agent, "get it")
    # the tool ran (bus result is raw), but the model saw the redacted tool-result message
    assert result.tool_steps[0].call.result == "the token is sk-LEAK9999"
    assert "sk-LEAK9999" not in _tool_msg(result) and "[redacted]" in _tool_msg(result)


# --------------------------------------------------------------------------- output stage


def test_output_block_raises_after_generation(build):
    agent = _agent(guardrails=[rules.keyword_deny(["classified"], stage="output", action="block")])
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("this is classified info"))
        )
        with pytest.raises(GuardrailTripped):
            run(agent, "tell me")


def test_output_redact_rewrites_result(build):
    ssn = rules.regex_rule(r"\d{3}-\d{2}-\d{4}", action="redact", stage="output")
    agent = _agent(guardrails=[ssn])
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("the ssn is 123-45-6789"))
        )
        result = run(agent, "give it")
    assert result.output == "the ssn is [redacted]"


# --------------------------------------------------------------------------- per-run override


def test_per_run_override_replaces_agent_guardrails(build):
    agent = _agent()  # no guardrails on the agent
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            run(agent, "a forbidden thing", guardrails=[rules.keyword_deny(["forbidden"])])
    assert route.call_count == 0


def test_per_run_empty_override_disables_agent_guardrails(build):
    agent = _agent(guardrails=[rules.keyword_deny(["forbidden"], action="block")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        result = run(agent, "a forbidden thing", guardrails=[])  # [] overrides → no gate
    assert route.call_count == 1 and result.output == "hi"


# --------------------------------------------------------------------------- audit chain


def test_guardrail_decision_lands_on_the_audit_chain(build, tmp_path):
    agent = _agent(guardrails=[rules.keyword_deny(["watchword"], action="flag")])
    log = AuditLog(system="support", path=str(tmp_path / "audit.jsonl"))
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        run(agent, "a watchword appears", audit=log)
    log.detach()
    entries = [e for e in log.entries if e.type == "guardrail_decision"]
    assert len(entries) == 1
    assert entries[0].payload["guardrail"] == "keyword_deny"
    assert entries[0].payload["stage"] == "input"
    assert entries[0].payload["action"] == "flag"
    assert entries[0].payload["decision_id"]  # correlated with the run's decision
    ok, detail = verify(str(tmp_path / "audit.jsonl"))
    assert ok, detail


def test_input_block_records_the_decision_before_raising(build, tmp_path):
    agent = _agent(guardrails=[rules.keyword_deny(["forbidden"], action="block")])
    log = AuditLog(system="support", path=str(tmp_path / "audit.jsonl"))
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            run(agent, "a forbidden request", audit=log)
    log.detach()
    blocks = [e for e in log.entries if e.type == "guardrail_decision"]
    assert blocks and blocks[0].payload["action"] == "block"
    assert not any(e.type == "llm_call" for e in log.entries)  # never called the model


# --------------------------------------------------------------------------- orchestration


def test_team_uses_each_agents_guardrails(build):
    entry = Agent(
        name="entry",
        model="gpt-4o",
        instructions="Route.",
        guardrails=[rules.keyword_deny(["forbidden"], action="block")],
    )
    peer = Agent(name="peer", model="gpt-4o", instructions="Help.")
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            run([entry, peer], "a forbidden request")
    assert route.call_count == 0  # the entry agent's input guardrail blocked pre-spend


# --------------------------------------------------------------------------- streaming


def _stream_chunk(content=None, finish=None):
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2) if finish else None
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish
            )
        ],
        usage=usage,
    )


def _stream_client(chunks):
    class Completions:
        def create(self, **kwargs):
            return iter(chunks)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_streaming_output_block_raises_after_deltas(build):
    # a real streamed response (stub client) reassembles to text an output guardrail blocks
    client = _stream_client(
        [_stream_chunk("this is "), _stream_chunk("classified"), _stream_chunk(None, finish="stop")]
    )
    agent = Agent(
        name="a",
        model="gpt-4o",
        instructions="x",
        client=client,
        guardrails=[rules.keyword_deny(["classified"], stage="output", action="block")],
    )
    with pytest.raises(GuardrailTripped):
        list(run.stream(agent, "tell me"))  # deltas already streamed; the block raises post-hoc


def test_streaming_input_block_is_pre_spend(build):
    agent = _agent(guardrails=[rules.keyword_deny(["forbidden"], action="block")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            list(run.stream(agent, "a forbidden request"))
    assert route.call_count == 0


# --------------------------------------------------------------------------- async


async def test_async_input_block_is_pre_spend(build):
    agent = _agent(guardrails=[rules.keyword_deny(["forbidden"], action="block")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            await run.aio(agent, "a forbidden request")
    assert route.call_count == 0


async def test_async_output_redact(build):
    agent = _agent(guardrails=[rules.regex_rule(r"sk-\w+", action="redact", stage="output")])
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("key sk-xyz")))
        result = await run.aio(agent, "give it")
    assert result.output == "key [redacted]"


async def test_async_custom_async_check(build):
    from cendor.guardrails import Verdict

    async def acheck(payload, ctx):
        return Verdict("block", reason="async says no")

    agent = _agent(guardrails=[rules.custom(acheck, stage="input", name="acheck")])
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped):
            await run.aio(agent, "anything")
    assert route.call_count == 0


# --------------------------------------------------------------------------- no-op / defaults


def test_no_guardrails_is_unchanged(build):
    agent = _agent()
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("plain")))
        result = run(agent, "hello")
    assert result.output == "plain"
