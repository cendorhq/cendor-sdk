"""Thin governance wiring the SDK owns: ``guard`` (a context manager) and ``_scope``.

``acttrace.guard()`` returns a bare pre-call interceptor (you install it on core's interceptor
seam yourself). The SDK exposes ``guard`` as a **context manager** so it reads like ``budget()`` /
``track()`` and composes in one ``with`` line — the whole reason it's in the re-export surface
(plan §4). It installs the acttrace interceptor for the duration and removes it on exit; the actual
redact/block/flag logic and the audit recording are 100% acttrace's, riding core's seam.

``_scope`` is the shared per-agent governance wrapper (``track`` + optional ``max_usd`` budget).
It lives in this leaf module — which imports nothing from :mod:`runner`/:mod:`orchestration` — so
both the single-agent :class:`~cendor.sdk.runner.Runner` and the multi-agent orchestrator can wrap
each agent identically without a circular import.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any

from cendor.core.instrument import add_interceptor, remove_interceptor

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


@contextmanager
def guard(policy: Any = None, *, audit: Any = None, on_block: Any = None) -> Any:
    """Install an ``acttrace`` policy guard on core's interceptor seam for the block's duration.

    Redacts PII **before** the provider sees it, blocks disallowed content, and flags the rest —
    per the ``Policy``. When ``audit`` is given, each action is recorded on the hash-chained log.

    ```python
    from cendor.sdk import guard, Policy, AuditLog
    log = AuditLog(system="support", path="audit.jsonl")
    with guard(Policy.gdpr(), audit=log):
        run(agent, "email me at alice@example.com", audit=log)
    ```
    """
    from cendor.acttrace import guard as _acttrace_guard

    if on_block is not None:
        interceptor = _acttrace_guard(policy, audit=audit, on_block=on_block)
    else:
        interceptor = _acttrace_guard(policy, audit=audit)
    add_interceptor(interceptor)
    try:
        yield interceptor
    finally:
        remove_interceptor(interceptor)
