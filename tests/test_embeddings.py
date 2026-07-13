"""Embeddings — governed, captured embedding calls (P1-8). Offline via respx."""

from __future__ import annotations

import httpx
import respx
from cendor.core import bus, trace
from cendor.core.types import LLMCall

from cendor.sdk import embed

EMBED_URL = "https://api.openai.com/v1/embeddings"


def _payload():
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
    }


def test_embed_returns_vectors_and_captures_usage():
    events: list = []

    def on(e):
        if isinstance(e, LLMCall):
            events.append(e)

    bus.subscribe(on)
    try:
        with respx.mock:
            respx.post(EMBED_URL).mock(return_value=httpx.Response(200, json=_payload()))
            with trace("emb-run"):
                vecs = embed("text-embedding-3-small", ["hello"], provider="openai")
    finally:
        bus.unsubscribe(on)

    assert vecs == [[0.1, 0.2, 0.3]]
    # the embedding call was captured on the bus, correlated + flagged
    assert events, "no LLMCall emitted for the embedding call"
    call = events[-1]
    assert call.metadata.get("embedding") is True
    assert call.usage.input_tokens == 4
    assert call.trace_id == "emb-run"


def test_embed_emits_exactly_once_no_shim_double_emission():
    # 1.7.0 deleted the SDK's hand-built emit path; core's instrument() is the only emitter now.
    events: list = []

    def on(e):
        if isinstance(e, LLMCall):
            events.append(e)

    bus.subscribe(on)
    try:
        with respx.mock:
            respx.post(EMBED_URL).mock(return_value=httpx.Response(200, json=_payload()))
            embed("text-embedding-3-small", "hello", provider="openai")
    finally:
        bus.unsubscribe(on)

    assert len(events) == 1  # exactly one LLMCall — no double emission
    call = events[0]
    assert call.metadata.get("embedding") is True
    assert call.metadata.get("cost_estimated") is True
    # golden: $0.02/1M * 4 tokens = 0.00000008 (compare by Decimal value, not repr)
    from decimal import Decimal

    assert call.cost is not None and call.cost.amount == Decimal("0.00000008")


def test_embed_preflight_budget_blocks_before_the_provider_call():
    # Pre-flight governance now applies to embeddings: a keyless USD budget refuses the call
    # BEFORE it fires (the respx route would 500 if hit — it must never be).
    import pytest
    from cendor.tokenguard import BudgetExceeded, budget, reset

    reset()
    try:
        with respx.mock:
            route = respx.post(EMBED_URL).mock(return_value=httpx.Response(500))
            with budget(usd=0.0000000000001, on_exceed="block"):
                with pytest.raises(BudgetExceeded):
                    embed(
                        "text-embedding-3-small",
                        "hello world this is a longer text",
                        provider="openai",
                    )
            assert route.call_count == 0  # blocked pre-flight; the provider was never called
    finally:
        reset()
