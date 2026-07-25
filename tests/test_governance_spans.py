"""Option C in the SDK (DR-2c): a blocked run's governance lands INSIDE the run — no audit object.

Core renders enforcement decisions flat for a libs-only app; inside an SDK run `live_spans` renders
the same events as children of the run root, so a telemetry user sees *why* a run stopped without
adopting the evidence library. When a real audit mirror is on the wire it wins (never two).

Offline via respx; observed through a real in-memory exporter installed as the global provider.
"""

from __future__ import annotations

import pytest
import respx
from cendor.core import bus
from cendor.core import otel as _co
from cendor.tokenguard import BudgetExceeded, budget

from cendor.sdk import Agent, run


@pytest.fixture
def global_otel():
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
    monkeypatch.delenv(_co.TELEMETRY_ENV, raising=False)
    _co._reset_governance_mirrors()
    yield
    _co._reset_governance_mirrors()
    bus._reset()


def _gov(exporter):
    return [s for s in exporter.get_finished_spans() if s.name.startswith("governance.")]


def test_a_blocked_run_shows_why_with_zero_governance_code(global_otel, build):
    """The acceptance case Option C exists for: no AuditLog anywhere, yet the block is visible."""
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("hello"))])
        with (
            pytest.raises(BudgetExceeded),
            budget(usd=0.0000001, on_exceed="block", name="tiny cap"),
        ):
            run(agent, "hi")

    spans = global_otel.get_finished_spans()
    gov = _gov(global_otel)
    assert [s.name for s in gov] == ["governance.budget_event"]
    a = gov[0].attributes
    assert a["cendor.gov.type"] == "budget_event"
    assert a["cendor.gov.action"] == "blocked"
    assert a["cendor.gov.budget"] == "tiny cap"
    assert a["cendor.gov.cap_usd"]
    assert "cendor.audit.type" not in a, "rule 6: no audit vocabulary on an ops span"
    # It is a CHILD of the run root, so any backend shows it inside the run it governed.
    root = next(s for s in spans if s.name == "agent.run")
    assert gov[0].parent is not None and gov[0].parent.span_id == root.context.span_id


def test_a_guardrail_block_renders_inline_on_the_run(global_otel, build):
    from cendor.sdk import rules

    agent = Agent(
        name="assistant",
        model="gpt-4o",
        guardrails=[rules.keyword_deny(["forbidden"], action="block")],
    )
    # noqa: B017 — GuardrailTripped surfaces through the run; the assertion here is the span shape
    with respx.mock, pytest.raises(Exception):  # noqa: B017
        run(agent, "a forbidden request")

    gov = _gov(global_otel)
    assert [s.name for s in gov] == ["governance.guardrail_decision"]
    a = gov[0].attributes
    assert a["cendor.gov.guardrail"] == "keyword_deny"
    assert a["cendor.gov.stage"] == "input"
    assert a["cendor.gov.action"] == "block"
    assert "cendor.gov.reason" not in a, "a rule's reason can carry payload text — never on a span"


def test_an_audit_mirror_wins_over_the_ops_spans(global_otel, build):
    """With governance code present, the chained `audit.*` spans are the rendering — not both."""
    from cendor.acttrace import AuditLog

    log = AuditLog(system="support")  # auto-attaches an OTelMirror (DR-2a) ⇒ core stands down
    agent = Agent(name="assistant", model="gpt-4o")
    try:
        with respx.mock:
            respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("hello"))])
            with (
                pytest.raises(BudgetExceeded),
                budget(usd=0.0000001, on_exceed="block", name="tiny cap"),
            ):
                run(agent, "hi", audit=log)
    finally:
        log.detach()

    names = [s.name for s in global_otel.get_finished_spans()]
    assert "audit.budget_event" in names, "the mirror rendered it"
    assert not any(n.startswith("governance.") for n in names), "and the ops path stood down"


def test_off_kills_the_inline_governance_too(global_otel, build, monkeypatch):
    monkeypatch.setenv(_co.TELEMETRY_ENV, "off")
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("hello"))])
        with (
            pytest.raises(BudgetExceeded),
            budget(usd=0.0000001, on_exceed="block", name="cap"),
        ):
            run(agent, "hi")
    assert global_otel.get_finished_spans() == ()
