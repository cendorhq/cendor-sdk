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


#: The conversation id of the run in flight (set by the runner from the session key, G19). Read by
#: ``otel.live_spans`` to stamp ``gen_ai.conversation.id`` on the root span. Empty when no run, or
#: when the run's session carries no id (semconv: never synthesize one).
_active_conversation: ContextVar[str] = ContextVar("cendor_sdk_active_conversation", default="")


def current_conversation() -> str:
    """The conversation id of the run in flight (from the session key), or ``""``."""
    return _active_conversation.get()


def _sdk_ambient(_event: Any) -> dict[str, Any] | None:
    """GLR-2 ambient provider: stamp the active agent + conversation id onto every LLMCall/ToolCall
    at construction (the caller's synchronous frame), so ``live_spans`` (and any subscriber) reads
    them from the event even when it is delivered outside the run scope — a stream finalized after
    the scope exits, or a consumer call after a Python stream generator leaked+restored scopes.
    Non-empty keys only; core's never-overwrite seam keeps any explicit value."""
    out: dict[str, Any] = {}
    agent = _active_agent.get()
    if agent:
        out["agent"] = agent
    conversation = _active_conversation.get()
    if conversation:
        out["conversation_id"] = conversation
    return out or None


# Register once at module load (idempotent). Importing cendor.sdk wires this.
from cendor.core import add_ambient_provider as _add_ambient_provider  # noqa: E402

_add_ambient_provider(_sdk_ambient)


@contextmanager
def _conversation_scope(session: Any) -> Any:
    """Set the ambient conversation id from a session's key for the duration of a run (G19). No-op
    when there is no session or it has no id — a conversation id is never synthesized."""
    cid = getattr(session, "id", None) if session is not None else None
    if not cid:
        yield
        return
    token = _active_conversation.set(str(cid))
    try:
        yield
    finally:
        _active_conversation.reset(token)


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
