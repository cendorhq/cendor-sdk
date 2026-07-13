"""The pii/secrets/entropy bridge delegates per-category resolution to acttrace (V5, 1.7.0).

The policy's block/redact tiers are honored per finding; the explicit ``action=`` param applies
only to findings the policy leaves at flag tier. No network.
"""

from __future__ import annotations

from cendor.acttrace import Policy
from cendor.guardrails import Context, evaluate

from cendor.sdk import rules


def _ctx() -> Context:
    return Context(agent="t", stage="input")


def test_gdpr_special_category_blocks_even_under_action_redact():
    # The plan's canonical case: gdpr resolves special_category -> block; action="redact" must not
    # soften it to a scrub (the old flat behavior did).
    g = rules.pii(Policy.gdpr(), action="redact")
    verdict = g.check("patient diagnosed with diabetes, religion: catholic", _ctx())
    assert verdict is not None and verdict.action == "block"
    assert "special_category" in verdict.reason


def test_policy_redact_tier_still_redacts_and_scrubs():
    g = rules.pii(Policy.gdpr(), action="redact")
    verdict = g.check("email bob@acme.com", _ctx())  # gdpr: pii -> redact
    assert verdict is not None and verdict.action == "redact"
    assert "bob@acme.com" not in str(verdict.replacement)


def test_action_param_applies_only_to_flag_tier():
    # Policy.default() leaves e.g. phone-ish/other PII at flag tier; action="block" promotes ONLY
    # those — while a redact-tier finding (email under default) keeps redacting.
    g = rules.pii(Policy.default(), action="block")
    v_email = g.check("email bob@acme.com", _ctx())  # default: email -> redact tier
    assert v_email is not None and v_email.action == "redact"

    flagged = Policy(actions={}, default="flag")  # everything at flag tier
    g2 = rules.pii(flagged, action="block")
    v = g2.check("email bob@acme.com", _ctx())
    assert v is not None and v.action == "block"  # flag tier promoted by action=


def test_pure_observation_needs_a_flag_policy_not_action_flag():
    # action="flag" no longer mutes the policy: gdpr's block tier still blocks.
    g = rules.pii(Policy.gdpr(), action="flag")
    v = g.check("patient diagnosed with diabetes, religion: catholic", _ctx())
    assert v is not None and v.action == "block"
    # a true observe-only gate uses an all-flag policy
    observer = rules.pii(Policy(actions={}, default="flag"), action="flag")
    v2 = observer.check("patient diagnosed with diabetes, religion: catholic", _ctx())
    assert v2 is not None and v2.action == "flag"


def test_secrets_and_entropy_wrappers_keep_working():
    s = rules.secrets(action="block")
    v = s.check("key sk-ant-api03-ABCDEFGH12345678", _ctx())
    assert v is not None and v.action == "block"

    s2 = rules.secrets()  # default action="redact"
    v2 = s2.check("key sk-ant-api03-ABCDEFGH12345678", _ctx())
    assert v2 is not None and v2.action == "redact"
    assert "sk-ant-api03" not in str(v2.replacement)


def test_bridge_gates_via_the_real_guardrails_engine():
    # End-to-end through the lib's own evaluate(): the bridged rule is an ordinary Guardrail.
    gate = [rules.pii(Policy.gdpr(), action="redact")]
    payload, decisions = evaluate(gate, "input", "email bob@acme.com")
    assert "bob@acme.com" not in str(payload)
    assert decisions and decisions[0].action == "redact"
