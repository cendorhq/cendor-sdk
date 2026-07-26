"""`Agent(id=…)` → `gen_ai.agent.id`, and the actor on every governance row (W4 / S4).

Measured 2026-07-26 (`plan/REPORT-MONITOR-DOORS-FITGAP-2026-07-26.md`):

* `gen_ai.agent.id` was **never emitted and never stored** — an agent was a string-only label, so two
  agents sharing a name across apps collided and a rename lost the history.
* `governance_events.agent` was populated on **13 of 386** rows, i.e. "which agent was blocked" was
  answerable only by inferring it from step ordering. On a governance product that is the attribute
  most worth having.

Rails: the id is emitted **only when the app gave one** — never hashed, never a placeholder (D3), and
core still carries no identity of its own. No network: a fake provider client.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from cendor.sdk import Agent, run
from cendor.sdk.otel import live_spans, span_tree


@pytest.fixture
def otel_traces():
    """An in-memory tracer provider installed as the global one — what an app's OTel setup does."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.util._once import Once

    def _reset():
        trace._TRACER_PROVIDER = None
        trace._TRACER_PROVIDER_SET_ONCE = Once()

    _reset()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        provider.shutdown()
        _reset()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import cendor.tokenguard as tokenguard
    from cendor.core import bus

    bus._reset()
    tokenguard.reset()
    yield
    bus._reset()
    tokenguard.reset()


def _client(reply: str = "ok"):
    """A fake OpenAI-shaped client: one answer, real usage, no tools."""

    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=reply, tool_calls=None))],
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
            )

    return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


def _agent(**kw):
    return Agent(name="refund-bot", model="gpt-4o-mini", client=_client(), **kw)


def _attrs(exporter, name_prefix: str) -> list[dict]:
    return [
        dict(s.attributes or {})
        for s in exporter.get_finished_spans()
        if s.name.startswith(name_prefix)
    ]


def test_live_spans_stamps_gen_ai_agent_id_when_the_app_gave_one(otel_traces):
    with live_spans():
        run(_agent(id="agent-7"), "hi")
    chats = _attrs(otel_traces, "chat ")
    assert chats, "no chat child span"
    assert all(a["gen_ai.agent.name"] == "refund-bot" for a in chats)
    assert all(a["gen_ai.agent.id"] == "agent-7" for a in chats)


def test_no_id_means_the_attribute_is_OMITTED_not_invented(otel_traces):
    """D3, stated as a test: absent identity stays absent. No hash of the name, no placeholder."""
    with live_spans():
        run(_agent(), "hi")
    chats = _attrs(otel_traces, "chat ")
    assert chats
    assert all(a["gen_ai.agent.name"] == "refund-bot" for a in chats)
    assert all("gen_ai.agent.id" not in a for a in chats), (
        f"an id was invented for an agent that has none: {chats}"
    )


def test_span_tree_carries_the_id_post_hoc(otel_traces):
    """The post-hoc path has only the `Result` — the id rode in on the call's metadata."""
    result = run(_agent(id="agent-7"), "hi")
    assert span_tree(result) is True
    for prefix in ("agent ", "chat "):
        rows = _attrs(otel_traces, prefix)
        assert rows, f"no {prefix.strip()} span"
        assert all(a.get("gen_ai.agent.id") == "agent-7" for a in rows), prefix


def test_the_id_does_not_shift_positional_arguments():
    """`Agent("support", "gpt-4o", "You are…")` is a documented shape — `id` is appended, not inserted."""
    a = Agent("support", "gpt-4o", "You are helpful.")
    assert a.name == "support"
    assert a.model == "gpt-4o"
    assert a.instructions == "You are helpful."
    assert a.id is None


# --------------------------------------------------------------------- S4: the actor on governance


def test_governance_ops_spans_name_the_acting_agent(otel_traces):
    """A budget block has no agent field of its own, so it used to be an anonymous row. It now reads
    the ambient actor through core's registry (`governance.*`, Option C — no AuditLog attached)."""
    from decimal import Decimal

    from cendor.tokenguard import BudgetExceeded, budget

    with live_spans(), budget(usd=Decimal("0.00000001"), on_exceed="block", name="tiny"):
        with pytest.raises(BudgetExceeded):
            run(_agent(id="agent-7"), "hi")
    gov = _attrs(otel_traces, "governance.")
    assert gov, "no governance span for a blocked run"
    assert any(a.get("cendor.gov.agent") == "refund-bot" for a in gov), (
        f"the block did not name the agent it stopped: {gov}"
    )
    assert any(a.get("cendor.gov.agent_id") == "agent-7" for a in gov), gov
    # rail 14: an agent name is app-supplied configuration, but a guardrail's REASON can paraphrase
    # the payload — it must still never reach a default-on span.
    assert all("cendor.gov.reason" not in a for a in gov)


def test_audit_mirror_spans_name_the_acting_agent(otel_traces):
    """The other half of S4: with an AuditLog attached the mirror wins, so IT must carry the actor —
    including on the entry types that have no agent field (llm_call, budget_event, decision*)."""
    from cendor.acttrace import AuditLog, OTelMirror

    log = AuditLog(system="refunds", risk_tier="high", mirror=OTelMirror())
    try:
        with live_spans():
            run(_agent(id="agent-7"), "hi", audit=log)
    finally:
        log.detach()
    audit = _attrs(otel_traces, "audit.")
    assert audit, "no audit mirror spans"
    named = [a for a in audit if a.get("cendor.audit.agent") == "refund-bot"]
    assert named, f"no mirrored entry named the agent: {[a.get('cendor.audit.type') for a in audit]}"
    assert any(a.get("cendor.audit.agent_id") == "agent-7" for a in audit)
    # An llm_call entry has no agent field in its payload at all — it is exactly the case the
    # measured 13/386 was missing.
    calls = [a for a in audit if a.get("cendor.audit.type") == "llm_call"]
    if calls:
        assert all(a.get("cendor.audit.agent") == "refund-bot" for a in calls)
