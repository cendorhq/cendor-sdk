"""The SDK rule catalogue — the deterministic ``cendor.guardrails`` rules re-exported for one
import, **plus** PII / secrets / entropy guardrails bridged from ``acttrace``'s detector catalogue.

The bridge lives here, in the SDK, on purpose. The library ``cendor.guardrails`` imports **only**
``cendor.core`` (constitution rule 2 — no tool→tool imports), so it can't call ``acttrace``. The
**SDK** imports every library, so it may compose them: :func:`pii` / :func:`secrets` /
:func:`entropy` are ordinary :class:`~cendor.guardrails.Guardrail`s whose check calls
``acttrace.scan`` / ``acttrace.redact``. This gives PII detection **at all four stages** — including
``tool_output``, which the process-global ``guard(Policy…)`` interceptor never sees (it only gates
LLM/tool *inputs*). There is one detection engine (acttrace's catalogue), not two.

Library (door-1) users get the same in three lines with ``rules.custom(fn)`` calling
``cendor.acttrace.scan`` — see the cookbook recipe ``recipes/governance/pii-guardrail``.

No detection catalogue is duplicated here; there is **no catch-rate claim** — coverage is exactly
acttrace's catalogue, measured in ``cendor-libs/docs/benchmarks.md`` on a documented corpus.
"""

from __future__ import annotations

from typing import Any

from cendor.guardrails import STAGES, Context, Guardrail, Verdict, normalize_stages

# Re-export the deterministic built-ins + the opt-in detection-tier adapters so
# `from cendor.sdk import rules` is the single surface (all from cendor-guardrails 1.1).
from cendor.guardrails.rules import (
    azure_content_safety,
    bedrock_guardrail,
    classifier,
    custom,
    custom_category,
    denied_topics,
    groundedness,
    intent,
    json_schema,
    keyword_deny,
    language,
    length_bounds,
    llm_judge,
    model_armor,
    openai_moderation,
    prompt_guard,
    regex_rule,
    spotlight,
    url_allowlist,
    url_deny,
)

__all__ = [
    # deterministic built-ins (re-exported from cendor.guardrails)
    "keyword_deny",
    "regex_rule",
    "spotlight",
    "url_allowlist",
    "url_deny",
    "length_bounds",
    "json_schema",
    "custom",
    "llm_judge",
    # opt-in detection-tier adapters + hosted rails (re-exported from cendor.guardrails)
    "classifier",
    "prompt_guard",
    "language",
    "openai_moderation",
    "bedrock_guardrail",
    "azure_content_safety",
    "model_armor",
    # similarity checks over a BYO embedding fn (re-exported from cendor.guardrails)
    "groundedness",
    "denied_topics",
    "custom_category",
    # pre-LLM intent screening (re-exported from cendor.guardrails)
    "intent",
    # acttrace-bridged detector guardrails (SDK-only)
    "pii",
    "secrets",
    "entropy",
]

#: PII/secret guardrails default to gating every stage — including ``tool_output``, the capability
#: the process-global ``guard()`` can't reach.
_DEFAULT_STAGES: tuple[str, ...] = STAGES


def _resolve_on_error(action: str, on_error: str | None) -> str:
    """A scanning error should never *leak*: redact/block fail closed; only an advisory flag fails
    open. An explicit ``on_error`` always wins."""
    if on_error is not None:
        return on_error
    return "fail_open" if action == "flag" else "fail_closed"


def _bridge(
    name: str,
    *,
    stages: Any,
    action: str,
    scan_policy: Any,
    groups: set[str] | None = None,
    categories: set[str] | None = None,
    timeout: float | None,
    on_error: str | None,
) -> Guardrail:
    """Build a :class:`Guardrail` whose check scans ``payload`` with ``acttrace.scan`` and enforces
    **per-category** actions via ``acttrace.resolve_findings`` — the same resolution ``guard()``
    applies, never flattened to one action. The policy's ``block``/``redact`` tiers are honored per
    finding; the explicit ``action=`` param is the enforcement applied to findings the policy
    leaves at **flag** tier. Precedence: any effective block → block; else any effective redact →
    redact (scrubs exactly those categories via ``acttrace.redact``); else flag. The reason records
    only category names + counts — never a raw value (acttrace counts only)."""
    from cendor.acttrace import Policy, redact, resolve_findings, scan

    def _reason(findings: list[Any]) -> str:
        cats = ", ".join(sorted({f.category for f in findings}))
        n = len(findings)
        return f"{name}: {n} categor{'y' if n == 1 else 'ies'} detected ({cats})"

    def check(payload: Any, ctx: Context) -> Verdict | None:
        findings = [f for f in scan(payload, scan_policy) if f.action != "allow"]
        if groups is not None:
            findings = [f for f in findings if f.group in groups]
        if categories is not None:
            findings = [f for f in findings if f.category in categories]
        if not findings:
            return None
        tiers = resolve_findings(findings)  # acttrace's own per-category resolution (guard's)
        promoted = tiers["flag"] if action in ("block", "redact") else []
        blocked = tiers["block"] + (promoted if action == "block" else [])
        to_redact = tiers["redact"] + (promoted if action == "redact" else [])
        if blocked:
            return Verdict("block", reason=_reason(blocked))
        if to_redact:
            # Scrub exactly the effective-redact categories (policy-redact tier + promoted flags).
            scrub = Policy(actions={f.category: "redact" for f in to_redact}, default="allow")
            cleaned, _ = redact(payload, scrub)
            return Verdict("redact", reason=_reason(to_redact), replacement=cleaned)
        return Verdict("flag", reason=_reason(findings))

    return Guardrail(
        name=name,
        stages=normalize_stages(stages),
        check=check,
        timeout=timeout,
        on_error=_resolve_on_error(action, on_error),
    )


def pii(
    policy: Any = None,
    *,
    action: str = "redact",
    stage: str | tuple[str, ...] = _DEFAULT_STAGES,
    name: str = "pii",
    timeout: float | None = None,
    on_error: str | None = None,
) -> Guardrail:
    """A guardrail over ``acttrace``'s full detector catalogue, governed by ``policy``.

    **Per-category actions are honored** (since 1.7.0, via ``acttrace.resolve_findings`` — the
    same resolution ``guard()`` applies): a category the ``policy`` resolves to ``block`` blocks
    and one it resolves to ``redact`` is scrubbed, regardless of ``action=``. The explicit
    ``action=`` param — ``"redact"`` (default), ``"block"``, or ``"flag"`` — is the enforcement
    applied to findings the policy leaves at **flag** tier. So
    ``pii(Policy.gdpr(), action="redact")`` still *blocks* a ``special_category`` finding (gdpr
    says block), and to purely observe use a policy whose actions are all ``flag``, not
    ``action="flag"``. ``policy`` defaults to :meth:`acttrace.Policy.default` (redacts secrets +
    emails, flags the rest); pass ``Policy.gdpr()`` / ``Policy.pci()`` / ``Policy.strict()`` for a
    wider net. Runs at every stage by default, so it also scans **tool outputs** — content
    ``guard()`` never sees. For secrets-only or high-entropy-only variants, use :func:`secrets` /
    :func:`entropy`.
    """
    from cendor.acttrace import Policy

    resolved = policy if policy is not None else Policy.default()
    return _bridge(
        name,
        stages=stage,
        action=action,
        scan_policy=resolved,
        timeout=timeout,
        on_error=on_error,
    )


def secrets(
    *,
    action: str = "redact",
    stage: str | tuple[str, ...] = _DEFAULT_STAGES,
    name: str = "secrets",
    timeout: float | None = None,
    on_error: str | None = None,
) -> Guardrail:
    """A guardrail scoped to ``acttrace``'s ``secret`` group — API keys, tokens, private keys, JWTs.
    ``action="redact"`` (default) scrubs them before the payload continues; ``"block"`` / ``"flag"``
    stop / record instead. A convenience wrapper over :func:`pii` with a secrets-only policy."""
    from cendor.acttrace import Policy

    scoped = Policy(actions={"secret": "redact" if action == "redact" else "flag"}, default="allow")
    return _bridge(
        name,
        stages=stage,
        action=action,
        scan_policy=scoped,
        groups={"secret"},
        timeout=timeout,
        on_error=on_error,
    )


def entropy(
    *,
    min_length: int = 24,
    min_entropy: float = 3.5,
    action: str = "flag",
    stage: str | tuple[str, ...] = _DEFAULT_STAGES,
    name: str = "entropy",
    timeout: float | None = None,
    on_error: str | None = None,
) -> Guardrail:
    """A guardrail for **opaque, high-entropy secrets** the anchored patterns miss (long random
    ids, base64 blobs). Enables ``acttrace``'s optional entropy detector (``min_length`` /
    ``min_entropy`` tune it) and gates on its ``high_entropy_secret`` category.

    Defaults to ``action="flag"`` because the entropy detector is **noisy** by nature — hashes and
    long identifiers look high-entropy too. Enabling it mutates ``acttrace``'s global detector
    registry (the documented way to turn entropy detection on); it is idempotent and re-tunable.
    """
    from cendor.acttrace import Policy, enable_entropy_detector

    enable_entropy_detector(min_length=min_length, min_entropy=min_entropy)  # idempotent
    scoped = Policy(
        actions={"high_entropy_secret": "redact" if action == "redact" else "flag"},
        default="allow",
    )
    return _bridge(
        name,
        stages=stage,
        action=action,
        scan_policy=scoped,
        categories={"high_entropy_secret"},
        timeout=timeout,
        on_error=on_error,
    )
