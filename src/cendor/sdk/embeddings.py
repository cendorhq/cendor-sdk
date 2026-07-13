"""Embeddings — governed, captured embedding calls (the RAG plumbing from the plan's §0).

``embed(model, inputs)`` calls the provider's embeddings endpoint through the **instrumented**
client, so ``cendor-core`` (≥ 1.6) captures it like any chat call: an ``LLMCall`` with
``metadata["embedding"] = True`` lands on the bus with real usage + cost, and the **pre-flight
interceptor pass applies** — a keyless ``budget(usd=…, on_exceed="block")`` refuses an over-budget
embed *before* it fires, and a ``guard(...)`` can redact the text before the provider sees it.
Tokens + cost land in the *same* audit / attribution / cost tree as chat calls. Correlate them by
wrapping in ``trace(...)`` like any run. The SDK owns the *feature* (it is the caller); the
*capture* is core's — there is no hand-built emit path here anymore (deleted in 1.7.0).

OpenAI-family providers (``openai`` / ``azure`` / ``foundry_local``) share ``embeddings.create``;
for others, call the provider's embedding client directly.
"""

from __future__ import annotations

from typing import Any

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


def embed(
    model: str,
    inputs: str | list[str],
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
) -> list[list[float]]:
    """Embed text(s); return one vector per input. The call rides the instrumented client, so
    core emits the governed ``LLMCall`` (``metadata["embedding"] = True``) and pre-flight
    budgets/guards apply."""
    prov = _resolve(model, provider)
    client = prov.client(async_=False, config=_config(api_key, base_url))
    kwargs: dict[str, Any] = {"model": model, "input": _texts(inputs)}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    resp = client.embeddings.create(**kwargs)
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
    resp = await client.embeddings.create(**kwargs)
    return _vectors(resp)
