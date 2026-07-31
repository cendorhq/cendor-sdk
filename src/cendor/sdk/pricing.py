"""Register per-model prices so cost estimation + USD budgets bind on models absent from core's
bundled price table.

Model ids that aren't in the snapshot — Hugging Face Hub ids, Azure / Foundry **deployment** names,
Foundry Local ids, or any custom / self-hosted gateway — have no price, so ``cendor-core`` estimates
their calls at ``$0`` and a USD ``budget(...)`` cannot enforce a cap on them (tokenguard warns once
per unpriced model under ``on_exceed="block"``). Four ways to close that gap:

* ``register_model_price(...)`` — give the model a rate; cost reporting **and** USD budgets work.
* ``register_deployment(name, like="gpt-4o")`` — for the common Azure/Foundry case where you don't
  have a rate card to hand but you do know which model the deployment serves.
* a ``tokens=`` cap on ``budget(...)`` — bounds spend without needing a price.
* ``configure(on_unpriced="raise")`` (re-exported from tokenguard) — reject unpriced calls under
  ``on_exceed="block"`` instead of letting them through as ``$0``.

Both registration helpers are thin re-exports of ``cendor.core.prices`` — a **libraries-door** user
needs only ``cendor-core``, not this distribution.
"""

from __future__ import annotations

from decimal import Decimal

from cendor.core import prices as _prices

_PER: dict[str, Decimal] = {"1M": Decimal(1_000_000), "1K": Decimal(1_000), "token": Decimal(1)}


def register_model_price(
    model: str,
    *,
    input: float | str,
    output: float | str = 0,
    cached: float | str | None = None,
    cache_write: float | str | None = None,
    per: str = "1M",
) -> dict[str, Decimal]:
    """Register a model's rates in ``cendor-core``'s price table so its calls are costed.

    Rates are given per 1M tokens by default (``per="1M"``; also ``"1K"`` or ``"token"``) and stored
    as exact per-token ``Decimal``. After registering, ``result.cost`` is non-zero for the model and
    USD ``budget(...)`` caps enforce against it. The registration **survives** ``prices.refresh()``.

    Since ``cendor-core`` 1.15.0 this is a convenience re-export:
    ``cendor.core.prices.register_model_price`` is public and identical, so a libraries-door user
    needs no SDK. (Before 1.15.0 the SDK was the only supported entry point in Python, and this
    docstring said so — it wrote through core's private ``prices._register`` hook.)

    ```python
    from cendor.sdk import Agent, run, budget
    from cendor.sdk.pricing import register_model_price

    register_model_price("my-gpt4o-deployment", input=2.50, output=10.00)  # USD per 1M tokens
    with budget(usd=0.10, on_exceed="block"):
        run(Agent(name="a", model="my-gpt4o-deployment", provider="azure", ...), "hi")
    ```

    Args:
        model: The exact model id the agent uses (deployment name / Hub id / local id).
        input: Input (prompt) price.
        output: Output (completion) price.
        cached: Optional cache-read price (defaults to the input rate when absent).
        cache_write: Optional cache-write price (Anthropic-style).
        per: Unit the prices are expressed in — ``"1M"`` (default), ``"1K"``, or ``"token"``.

    Returns:
        The stored per-token rate dict.
    """
    if per not in _PER:
        raise ValueError(f"per must be one of {sorted(_PER)}, got {per!r}")
    divisor = _PER[per]
    rates: dict[str, Decimal] = {
        "input": Decimal(str(input)) / divisor,
        "output": Decimal(str(output)) / divisor,
    }
    if cached is not None:
        rates["cached"] = Decimal(str(cached)) / divisor
    if cache_write is not None:
        rates["cache_write"] = Decimal(str(cache_write)) / divisor
    _prices._register(model, rates)  # the contractual write hook — survives prices.refresh()
    return rates


def register_deployment(deployment: str, *, like: str) -> dict[str, Decimal]:
    """Price an Azure/Foundry **deployment name** like the model it serves.

    A re-export of :func:`cendor.core.prices.register_deployment` — see that function for the full
    contract. The short version: on Azure the id a call reports is the deployment name *you* chose,
    so it is in no price table and its cost is ``None``; this maps it onto a base model's rates
    explicitly. Copy-at-registration, survives ``refresh()``, raises if ``like`` is unknown.

    ```python
    from cendor.sdk import Agent, run, budget
    from cendor.sdk.pricing import register_deployment

    register_deployment("prod-gpt4o-eastus", like="gpt-4o")
    with budget(usd=0.10, on_exceed="block"):      # now binds — the deployment has a price
        run(Agent(name="a", model="prod-gpt4o-eastus", provider="azure"), "hi")
    ```

    Args:
        deployment: The exact id calls report — your deployment name.
        like: A model id already in the price table whose rates it should use.

    Returns:
        The stored per-token rate dict.

    Raises:
        cendor.core.prices.UnknownModelError: If ``like`` is not in the active table.
    """
    return _prices.register_deployment(deployment, like=like)
