"""Per-agent governance wiring the SDK owns: ``_scope``.

``cendor.sdk.guard`` needs no wrapper anymore: since ``cendor-acttrace`` 1.5.0, ``guard()``'s
return is **dual-shape** (a plain interceptor that is also a context manager installing/removing
itself on core's seam), so the SDK re-exports the identical library object —
``cendor.sdk.guard is cendor.acttrace.guard``. The wrapper that used to live here is gone.

``_scope`` is the shared per-agent governance wrapper (``track`` + optional ``max_usd`` budget).
It lives in this leaf module — which imports nothing from :mod:`runner`/:mod:`orchestration` — so
both the single-agent :class:`~cendor.sdk.runner.Runner` and the multi-agent orchestrator can wrap
each agent identically without a circular import.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import Agent


@contextmanager
def _scope(agent: Agent) -> Any:
    """Per-agent governance: attribute spend to the agent + enforce its ``max_usd`` cap if set.

    Wraps one agent's turns in ``track(agent=…)`` and, when the agent sets ``max_usd``, a
    pre-flight ``budget(on_exceed="block")`` ceiling (blocks the over-budget call *before* it is
    sent). Shared by the single-agent ``Runner`` and the multi-agent orchestrator so the cap
    applies identically on every path.
    """
    from cendor.tokenguard import budget, track

    with ExitStack() as stack:
        stack.enter_context(track(agent=agent.name))
        if agent.max_usd is not None:
            stack.enter_context(budget(usd=agent.max_usd, on_exceed="block"))
        yield
