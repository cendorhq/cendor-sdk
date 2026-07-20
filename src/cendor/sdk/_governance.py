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
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import Agent

#: The agent currently executing a turn (set by :func:`_scope`). Read by ``otel.live_spans`` to
#: stamp ``gen_ai.agent.name`` on each child span at emit time — robust regardless of bus fan-out
#: order (the event is emitted synchronously inside the active agent's scope). Empty when no run.
_active_agent: ContextVar[str] = ContextVar("cendor_sdk_active_agent", default="")


def current_agent() -> str:
    """The name of the agent currently executing a turn, or ``""`` outside a run."""
    return _active_agent.get()


@contextmanager
def _scope(agent: Agent) -> Any:
    """Per-agent governance: attribute spend to the agent + enforce its ``max_usd`` cap if set.

    Wraps one agent's turns in ``track(agent=…)`` and, when the agent sets ``max_usd``, a
    pre-flight ``budget(on_exceed="block")`` ceiling (blocks the over-budget call *before* it is
    sent). Shared by the single-agent ``Runner`` and the multi-agent orchestrator so the cap
    applies identically on every path. Also stamps the ambient current-agent contextvar so the OTel
    span tree can name which agent made each call.
    """
    from cendor.tokenguard import budget, track

    with ExitStack() as stack:
        stack.enter_context(track(agent=agent.name))
        token = _active_agent.set(agent.name)
        stack.callback(_active_agent.reset, token)
        if agent.max_usd is not None:
            # Name the per-agent ceiling so a block by an agent's max_usd is identifiable in a
            # monitor (which budget blocked what) — the tokenguard 1.3 budget(name=) hook (G10).
            stack.enter_context(
                budget(
                    usd=agent.max_usd,
                    on_exceed="block",
                    name=f"agent:{agent.name} max_usd",
                    description=f"per-agent USD ceiling for {agent.name}",
                )
            )
        yield
