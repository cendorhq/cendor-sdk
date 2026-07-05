"""Embeddings — governed, captured embedding calls (the RAG plumbing from the plan's §0).

``embed(model, inputs)`` calls the provider's embeddings endpoint, returns the vectors, and emits a
governed ``LLMCall`` on ``cendor-core``'s bus — so the call's tokens + cost land in the *same* audit
/ attribution / cost tree as chat calls (RAG embeddings were invisible beneath frameworks; owning
the call makes them first-class). Correlate them by wrapping in ``trace(...)`` like any run.

Note: this *captures* (records) the embedding call. Pre-call USD *blocking* of embeddings would need
core-level embeddings interception; use a ``tokens=`` budget or register a price to bound spend.

OpenAI-family providers (``openai`` / ``azure`` / ``foundry_local``) share ``embeddings.create``;
for others, call the provider's embedding client directly.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from cendor.core import bus, current_trace_id, prices
from cendor.core.types import LLMCall, Usage

from .providers import OpenAIChatProvider, get_provider, resolve_provider


def _texts(inputs: str | list[str]) -> list[str]:
    return [inputs] if isinstance(inputs, str) else list(inputs)


def _config(api_key: str | None, base_url: str | None) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if api_key:
        cfg["api_key"] = api_key
    if base_url:
        cfg["base_url"] = base_url
    return cfg


def _resolve(model: str, provider: str | None) -> OpenAIChatProvider:
    try:
        prov = resolve_provider(model, provider)
    except ValueError:
        prov = get_provider("openai")  # embedding ids rarely prefix-infer; default to OpenAI shape
    if not isinstance(prov, OpenAIChatProvider):
        raise NotImplementedError(
            f"embed() supports OpenAI-family providers (openai/azure/foundry_local); got "
            f"{prov.name!r}. Call that provider's embeddings client directly."
        )
    return prov


def _vectors(resp: Any) -> list[list[float]]:
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data", [])
    out: list[list[float]] = []
    for d in data or []:
        emb = getattr(d, "embedding", None)
        if emb is None and isinstance(d, dict):
            emb = d.get("embedding")
        out.append(list(emb) if emb is not None else [])
    return out


def _emit(model: str, provider: str, resp: Any, start: float) -> None:
    u = getattr(resp, "usage", None)
    inp = 0
    if u is not None:
        inp = getattr(u, "prompt_tokens", None) or getattr(u, "total_tokens", 0) or 0
    elif isinstance(resp, dict):
        inp = (resp.get("usage") or {}).get("prompt_tokens", 0) or 0
    call = LLMCall(
        id=uuid.uuid4().hex,
        provider=provider,
        model=model,
        messages=[],
        trace_id=current_trace_id(),
        ts=datetime.now(UTC),
    )
    call.latency_ms = (time.perf_counter() - start) * 1000.0
    call.usage = Usage(input_tokens=int(inp), output_tokens=0)
    try:
        call.cost = prices.estimate(model, int(inp), 0)
        call.metadata["cost_estimated"] = True
    except KeyError:
        call.cost = None
    call.metadata["embedding"] = True
    bus.emit(call)


def embed(
    model: str,
    inputs: str | list[str],
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
) -> list[list[float]]:
    """Embed text(s); return one vector per input and emit a governed ``LLMCall`` on the bus."""
    prov = _resolve(model, provider)
    client = prov.client(async_=False, config=_config(api_key, base_url))
    kwargs: dict[str, Any] = {"model": model, "input": _texts(inputs)}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    start = time.perf_counter()
    resp = client.embeddings.create(**kwargs)
    _emit(model, prov.name, resp, start)
    return _vectors(resp)


async def aembed(
    model: str,
    inputs: str | list[str],
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
) -> list[list[float]]:
    """Async counterpart of :func:`embed`."""
    prov = _resolve(model, provider)
    client = prov.client(async_=True, config=_config(api_key, base_url))
    kwargs: dict[str, Any] = {"model": model, "input": _texts(inputs)}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    start = time.perf_counter()
    resp = await client.embeddings.create(**kwargs)
    _emit(model, prov.name, resp, start)
    return _vectors(resp)
