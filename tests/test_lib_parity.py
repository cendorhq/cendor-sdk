"""SDK↔lib surface-parity + identity pins (W2, plan/PLAN-SDK-LIB-INHERITANCE.md).

Makes inheritance drift LOUD instead of silent: every re-export the docs call "the identical
library object" is pinned with ``is``; the ``sdk.rules`` namespace is diffed against the library's
``__all__`` (with ``DELIBERATE_EXCLUSIONS`` as the reviewed, single authority on what is
deliberately NOT re-exported); and the lib signatures the SDK forwards are pinned so a new lib
kwarg fails this build and forces a conscious forward/decline decision, never a silent lag.
No network.
"""

from __future__ import annotations

import inspect

import cendor.acttrace as acttrace
import cendor.core as core
import cendor.guardrails as guardrails
import cendor.guardrails.rules as lib_rules
import cendor.tokenguard as tokenguard

import cendor.sdk as sdk

# --------------------------------------------------------------------------- identity pins
#
# Every name here is documented as "the identical library object, re-exported". `is` — not `==`.
# The 19 identities the 2026-07-13 report verified, plus the 1.7.0 additions: `guard` (the swap —
# acttrace 1.5.0's dual-shape return made the wrapper unnecessary), `PolicyViolation`,
# `GuardrailDecision`, `Verdict`, `downgrades`/`clamps`, and the core event types.

IDENTITY_PINS = {
    # tokenguard
    "budget": ("tokenguard", tokenguard.budget),
    "track": ("tokenguard", tokenguard.track),
    "report": ("tokenguard", tokenguard.report),
    "configure": ("tokenguard", tokenguard.configure),
    "downgrades": ("tokenguard", tokenguard.downgrades),
    "clamps": ("tokenguard", tokenguard.clamps),
    "BudgetExceeded": ("tokenguard", tokenguard.BudgetExceeded),
    # acttrace — incl. guard: the origin finding, restored to a true identity in 1.7.0
    "guard": ("acttrace", acttrace.guard),
    "PolicyViolation": ("acttrace", acttrace.PolicyViolation),
    "Policy": ("acttrace", acttrace.Policy),
    "AuditLog": ("acttrace", acttrace.AuditLog),
    "verify": ("acttrace", acttrace.verify),
    # guardrails
    "Guardrail": ("guardrails", guardrails.Guardrail),
    "guardrail": ("guardrails", guardrails.guardrail),
    "GuardrailTripped": ("guardrails", guardrails.GuardrailTripped),
    "GuardrailDecision": ("guardrails", guardrails.GuardrailDecision),
    "Verdict": ("guardrails", guardrails.Verdict),
    "LoadedPolicy": ("guardrails", guardrails.LoadedPolicy),
    "load_policy": ("guardrails", guardrails.load_policy),
    "policy_schema": ("guardrails", guardrails.policy_schema),
    "presets": ("guardrails", guardrails.presets),
    "judge": ("guardrails", guardrails.judge),
    "task_adherence": ("guardrails", guardrails.judge.task_adherence),
    # core
    "trace": ("core", core.trace),
    "current_trace_id": ("core", core.current_trace_id),
    "LLMCall": ("core", core.LLMCall),
    "ToolCall": ("core", core.ToolCall),
    "Usage": ("core", core.Usage),
    "Money": ("core", core.Money),
}


def test_every_documented_reexport_is_the_identical_library_object():
    wrong = {
        name: lib for name, (lib, obj) in IDENTITY_PINS.items() if getattr(sdk, name) is not obj
    }
    assert not wrong, f"SDK re-exports that are NOT the library object: {wrong}"


def test_guard_identity_is_literal():
    # The origin finding of the whole investigation — keep it as its own named test.
    assert sdk.guard is acttrace.guard


# --------------------------------------------------------------------------- rules namespace diff
#
# The single authority on what the SDK `rules` module deliberately does NOT re-export. Python
# re-exports ALL of the library's rule factories (D1 gave TS the same), so the exclusion set is
# empty — any name the library adds lands here as a failure until it is re-exported or explicitly
# allow-listed below with a rationale (report §2 pattern).
DELIBERATE_EXCLUSIONS: set[str] = set()

#: SDK-only additions to `rules` — the acttrace bridge (the SDK may compose libs; they can't).
SDK_ONLY_RULES = {"pii", "secrets", "entropy"}


def test_rules_namespace_reexports_the_full_library_catalogue():
    missing = set(lib_rules.__all__) - set(sdk.rules.__all__) - DELIBERATE_EXCLUSIONS
    assert not missing, (
        f"cendor.guardrails.rules grew factories the SDK rules module doesn't re-export: "
        f"{sorted(missing)} — re-export them or add to DELIBERATE_EXCLUSIONS with a rationale"
    )
    extras = set(sdk.rules.__all__) - set(lib_rules.__all__)
    assert extras == SDK_ONLY_RULES  # only the documented bridge names are SDK-side additions


def test_shared_rules_names_are_identical_objects():
    diff = [
        n
        for n in set(lib_rules.__all__) & set(sdk.rules.__all__)
        if getattr(sdk.rules, n) is not getattr(lib_rules, n)
    ]
    assert not diff, f"sdk.rules re-exports that are NOT the library object: {diff}"


# --------------------------------------------------------------------------- signature pins
#
# The SDK forwards these lib signatures. Pin the parameter sets so the day a lib grows a kwarg,
# this build fails and forces a conscious decision (forward it / document the lag) — the report's
# G2 fix. Update the pin in the same PR as the decision.


def test_acttrace_guard_signature_pin():
    assert list(inspect.signature(acttrace.guard).parameters) == ["policy", "audit", "on_block"]


def test_tokenguard_budget_config_pin_vs_scope_forwarding():
    # `_governance._scope` forwards usd (as `max_usd`) + hardcodes on_exceed="block", and — since
    # the V2 emission wave (tokenguard 1.3) — a `name`/`description` identifying the per-agent
    # ceiling so a block is attributable in a monitor. Any new budget() field lands here first;
    # decide whether the per-agent path forwards it, and update this pin in the same PR.
    assert list(inspect.signature(tokenguard.budget).parameters) == [
        "usd",
        "tokens",
        "on_exceed",
        "scope",
        "downgrade",
        "output_reserve",
        "reasoning_reserve",
        "name",
        "description",
    ]


def test_guardrails_stages_canary():
    # The SDK loop gates exactly these four stages at hardcoded sites (_guardrails.py). A 5th
    # library stage would be silently never evaluated inside run() — this canary makes it loud.
    assert guardrails.STAGES == ("input", "tool_call", "tool_output", "output")


# --------------------------------------------------------------------------- shim-expiry harness
#
# House pattern (report §5.4): when the SDK ships a WORKAROUND for a lib gap, add a test here
# asserting the gap STILL EXISTS — the lib catching up turns the test red and forces the shim's
# deletion (what didn't happen for the TS bridge's stale "0.2.0" comment).
#
#   def test_shim_expiry_<name>():
#       """DELETE <sdk shim> when this fails: <lib> now supports <capability>."""
#       assert not hasattr(<lib>, "<capability>"), "lib caught up -> delete the shim + this test"
#
# As of 1.7.0 there are NO active shims: the embeddings emit path (the last one) was deleted when
# core 1.6.0 grew embeddings capture. The inverse pin below guards the adoption itself.


def test_core_embeddings_capture_adopted_no_shim_left():
    """core detects embeddings.create (the reason the SDK's hand-built emit path was deleted)."""
    from types import SimpleNamespace

    from cendor.core.instrument import _find_targets

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: None)),
        embeddings=SimpleNamespace(create=lambda **k: None),
    )
    tags = {provider for _, _, provider in _find_targets(client)}
    assert "openai_embeddings" in tags
