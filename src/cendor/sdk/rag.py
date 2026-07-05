"""RAG seam — a thin, dependency-free retrieval layer over the governed :func:`embed`.

The SDK's job is to make retrieval *governed*, not to be a vector database. So this provides:

* :class:`VectorIndex` — a tiny in-memory cosine index built on :func:`cendor.sdk.embed` (so every
  embedding call is captured on the bus). Great for small corpora, demos, and tests; for production
  scale, plug your own store (pgvector / Pinecone / Chroma / …) and expose it as a retriever.
* :meth:`VectorIndex.as_retriever` — a ``query -> list[str]`` callable you hand to
  ``Agent(retriever=...)`` so retrieved context is injected into the governed loop automatically.

Bring your own embedder (local sentence-transformers, etc.) via ``embedder=`` — otherwise the
governed provider ``embed()`` is used, and its tokens/cost/audit ride the same seams as chat calls.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .embeddings import embed


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class Hit:
    """One retrieval result: the passage text, its cosine ``score``, and any ``metadata``."""

    text: str
    score: float
    metadata: dict = field(default_factory=dict)


class VectorIndex:
    """An in-memory cosine similarity index over embedded texts (no external dependency).

    ```python
    from cendor.sdk import Agent, run, VectorIndex

    kb = VectorIndex(model="text-embedding-3-small", provider="openai")
    kb.add(["Refunds within 30 days.", "Support hours are 9-5 UTC."])
    agent = Agent(name="rag", model="gpt-4o", retriever=kb.as_retriever(k=3),
                  instructions="Answer only from the provided context.")
    run(agent, "What's the refund window?")   # relevant passages are retrieved + injected
    ```
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        provider: str | None = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        embedder: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self.model = model
        self._embed: Callable[[list[str]], list[list[float]]] = embedder or (
            lambda texts: embed(model, texts, provider=provider, api_key=api_key, base_url=base_url)
        )
        self._items: list[tuple[str, list[float], dict]] = []

    def add(self, texts: list[str], metadatas: list[dict] | None = None) -> None:
        """Embed and index ``texts`` (with optional per-text ``metadatas``)."""
        texts = list(texts)
        if not texts:
            return
        metas = metadatas or [{} for _ in texts]
        vectors = self._embed(texts)
        for text, vector, meta in zip(texts, vectors, metas, strict=True):
            self._items.append((text, list(vector), dict(meta)))

    def search(self, query: str, k: int = 5) -> list[Hit]:
        """Return the ``k`` most similar indexed passages to ``query`` (highest cosine first)."""
        if not self._items:
            return []
        qvec = self._embed([query])[0]
        scored = [
            Hit(text=text, score=_cosine(qvec, vector), metadata=meta)
            for text, vector, meta in self._items
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[: max(k, 0)]

    def as_retriever(self, k: int = 5) -> Callable[[str], list[str]]:
        """A ``query -> list[str]`` retriever (top-``k`` passages) for ``Agent(retriever=...)``."""

        def retrieve(query: str) -> list[str]:
            return [hit.text for hit in self.search(query, k=k)]

        return retrieve

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"VectorIndex(model={self.model!r}, size={len(self._items)})"


def format_context(chunks: list[Any]) -> str:
    """Render retrieved chunks into a single context block for injection into the prompt."""
    return "Relevant context:\n\n" + "\n\n---\n\n".join(str(c) for c in chunks)
