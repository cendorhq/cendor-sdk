"""RAG seam — VectorIndex ranking + the Agent(retriever=...) injection hook (offline)."""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, VectorIndex, run

# A deterministic bag-of-words embedder so ranking is testable without a network.
_VOCAB = ["refund", "support", "hours", "policy", "window", "days", "shipping"]


def _fake_embedder(texts):
    return [[1.0 if word in t.lower() else 0.0 for word in _VOCAB] for t in texts]


def test_vector_index_ranks_by_similarity():
    idx = VectorIndex(embedder=_fake_embedder)
    idx.add(
        ["Refunds are available within 30 days.", "Support hours are 9-5 UTC."],
        metadatas=[{"id": "refund"}, {"id": "support"}],
    )
    assert len(idx) == 2
    hits = idx.search("what is the refund window in days", k=1)
    assert len(hits) == 1
    assert "Refund" in hits[0].text
    assert hits[0].metadata["id"] == "refund"
    assert hits[0].score > 0


def _stub_echo_client():
    """A stub that echoes back whatever system 'context' it received, so we can assert injection."""

    class Completions:
        def create(self, **kwargs):
            sys_ctx = " ".join(m["content"] for m in kwargs["messages"] if m["role"] == "system")
            answer = "SAW_CONTEXT" if "Refunds" in sys_ctx else "NO_CONTEXT"
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=answer, tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1),
            )

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_agent_retriever_injects_context():
    idx = VectorIndex(embedder=_fake_embedder)
    idx.add(["Refunds are available within 30 days."])
    agent = Agent(
        name="rag",
        model="gpt-4o",
        instructions="Answer from context.",
        retriever=idx.as_retriever(k=1),
        client=_stub_echo_client(),
    )
    result = run(agent, "refund window?")
    assert result.output == "SAW_CONTEXT"  # retrieved passage was injected as a system message
    # the injected context is part of the recorded conversation
    assert any(
        m.get("role") == "system" and "Refunds" in m.get("content", "") for m in result.messages
    )
