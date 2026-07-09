"""Wave 3 SDK surface: hosted-rail / grounding rules + load_policy re-exported through the SDK, and
a file policy driving Agent(guardrails=…) with its hash/version on the decisions. respx-mocked."""

from __future__ import annotations

import json

import pytest
import respx

from cendor import guardrails as gr
from cendor.sdk import Agent, GuardrailTripped, LoadedPolicy, load_policy, rules, run


def _agent(**kw):
    return Agent(name="assistant", model="gpt-4o", instructions="Be helpful.", **kw)


def test_wave3_rules_reexported_from_library():
    # the SDK `rules` is a superset; the Wave-3 factories are the SAME objects as the library's
    for name in (
        "bedrock_guardrail",
        "azure_content_safety",
        "model_armor",
        "groundedness",
        "denied_topics",
    ):
        assert getattr(rules, name) is getattr(gr.rules, name)
    assert load_policy is gr.load_policy and LoadedPolicy is gr.LoadedPolicy


def test_load_policy_result_usable_in_agent_blocks_pre_spend(build):
    policy = load_policy(
        {
            "version": "2026-07-09",
            "guardrails": [
                {"rule": "keyword_deny", "args": {"words": ["forbidden"]}, "action": "block"}
            ],
        }
    )
    agent = _agent(guardrails=policy)  # a LoadedPolicy is a list[Guardrail]
    with respx.mock:
        route = respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        with pytest.raises(GuardrailTripped) as ei:
            run(agent, "a forbidden request")
    assert route.call_count == 0  # blocked before the model ran — $0 spent
    # the block decision carries the policy provenance load_policy stamped
    d = ei.value.decisions[-1]
    assert d.metadata["policy_hash"] == policy.policy_hash
    assert d.metadata["policy_version"] == "2026-07-09"


def test_policy_provenance_on_a_flag_reaches_result_decisions(build):
    policy = load_policy(
        {
            "version": "v7",
            "guardrails": [
                {
                    "rule": "regex_rule",
                    "args": {"pattern": r"secret"},
                    "action": "flag",
                    "stage": "input",
                }
            ],
        }
    )
    agent = _agent(guardrails=policy)
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        result = run(agent, "this is a secret request")
    flags = [d for d in result.guardrail_decisions if d.action == "flag"]
    assert flags and flags[0].metadata["policy_version"] == "v7"
    assert json.loads(json.dumps(flags[0].metadata))  # metadata is JSON-serialisable for the chain
