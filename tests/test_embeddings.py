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
