"""The `context_budget` best-effort fallback is silent but OBSERVABLE on the bus (1.7.0).

When per-turn assembly fails, `_assemble` degrades to the raw messages (unchanged, deliberate)
AND emits a `ContextBudgetFallback` diagnostic on cendor-core's bus. Unknown event types are
ignored by the stock subscribers (bus-events spec) — pinned here against the real ones.
No network.
"""

from __future__ import annotations

from cendor.core import bus

from cendor.sdk.agent import Agent
from cendor.sdk.runner import ContextBudgetFallback, _assemble


def _agent(**kw) -> Agent:
    return Agent(name="t", model="gpt-4o", instructions="x", **kw)


def test_fallback_emits_diagnostic_event_and_returns_raw_messages(monkeypatch):
    events: list = []
    bus.subscribe(events.append)
    try:
        import cendor.contextkit as ck

        def boom(*a, **k):
            raise RuntimeError("assembly exploded")

        monkeypatch.setattr(ck, "Context", boom)
        msgs = [{"role": "user", "content": "hi"}]
        out = _assemble(_agent(context_budget=8000), msgs)
    finally:
        bus.unsubscribe(events.append)

    assert out is msgs  # unchanged fallback behavior: degrade to raw messages
    fallbacks = [e for e in events if isinstance(e, ContextBudgetFallback)]
    assert len(fallbacks) == 1
    ev = fallbacks[0]
    assert ev.agent == "t" and ev.budget_tokens == 8000 and ev.error == "RuntimeError"


def test_no_event_when_assembly_succeeds_or_budget_unset():
    events: list = []
    bus.subscribe(events.append)
    try:
        msgs = [{"role": "user", "content": "hi"}]
        _assemble(_agent(), msgs)  # no context_budget -> no assembly, no event
        _assemble(_agent(context_budget=8000), msgs)  # real assembly succeeds -> no event
    finally:
        bus.unsubscribe(events.append)
    assert not [e for e in events if isinstance(e, ContextBudgetFallback)]


def test_stock_subscribers_tolerate_the_unknown_event(tmp_path):
    # tokenguard's bus subscriber and acttrace's AuditLog._on_event isinstance-guard their event
    # types — emitting the SDK diagnostic must never break them (bus-events spec).
    import cendor.tokenguard as tokenguard
    from cendor.acttrace import AuditLog

    tokenguard.reset()
    log = AuditLog(system="t", path=str(tmp_path / "a.jsonl"))
    try:
        bus.emit(ContextBudgetFallback(agent="t", budget_tokens=1, error="X"))
        # tokenguard report unchanged; the audit chain gained no entry for the unknown event
        assert not [e for e in log.entries if "ContextBudgetFallback" in str(e.payload)]
    finally:
        log.detach()
        tokenguard.reset()
