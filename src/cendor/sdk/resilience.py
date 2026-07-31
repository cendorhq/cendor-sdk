"""Resilience: retries with exponential backoff around the model call.

A small, provider-agnostic retry policy applied to the model call in the loop. It retries only
*transient* failures (timeouts, connection errors, rate limits, 5xx) and **never** retries a
governance decision — ``BudgetExceeded`` / ``PolicyViolation`` are terminal by design. Backoff is
exponential with a cap; the sleep function is injectable so tests run instantly.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from cendor.acttrace import PolicyViolation
from cendor.tokenguard import BudgetExceeded

_TRANSIENT_NAME_HINTS = (
    "timeout",
    "connection",
    "ratelimit",
    "apiconnection",
    "internalserver",
    "serviceunavailable",
    "overloaded",
    "apistatus",
    "temporarilyunavailable",
)
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
#: Governance decisions are terminal — matched by ``isinstance`` on the real library classes (a
#: name-string match would silently turn never-retry into retry if a lib renamed its exception).
_NEVER_RETRY: tuple[type[BaseException], ...] = (BudgetExceeded, PolicyViolation)


def default_is_transient(exc: BaseException) -> bool:
    """Heuristic: is ``exc`` a transient provider error worth retrying?

    Governance exceptions (``BudgetExceeded``/``PolicyViolation``) are never transient — checked by
    ``isinstance`` on the real library classes. Otherwise a call is retried if it carries a
    retryable HTTP status or its type name looks transient — the name heuristic applies **only** to
    transient hints, using duck typing so no provider SDK needs importing.
    """
    if isinstance(exc, _NEVER_RETRY):
        return False
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if isinstance(status, int) and status in _TRANSIENT_STATUS:
        return True
    name = type(exc).__name__.lower()
    return any(hint in name for hint in _TRANSIENT_NAME_HINTS)


@dataclass
class RetryPolicy:
    """How to retry a transient model call."""

    max_attempts: int = 3
    backoff_base: float = 0.2
    backoff_factor: float = 2.0
    max_backoff: float = 10.0
    should_retry: Callable[[BaseException], bool] = field(default=default_is_transient)
    sleep: Callable[[float], None] = field(default=time.sleep)

    def delay(self, attempt: int) -> float:
        """Backoff for a 0-indexed retry attempt (capped)."""
        return min(self.backoff_base * (self.backoff_factor**attempt), self.max_backoff)


#: An OpenAI-shaped 400 that names both the rejected parameter and its replacement, e.g.
#: ``Unsupported parameter: 'max_tokens' is not supported with this model. Use
#: 'max_completion_tokens' instead.`` Reasoning-family deployments (gpt-5*, o*) answer this to a
#: Chat Completions call carrying ``max_tokens``.
_PARAM_SWAP_RE = re.compile(
    r"'(?P<old>[A-Za-z0-9_]+)'\s+is not supported[^.]*\."
    r"\s*Use\s+'(?P<new>[A-Za-z0-9_]+)'\s+instead",
    re.IGNORECASE,
)


def _param_swap(exc: BaseException, kwargs: dict) -> dict | None:
    """A repaired copy of ``kwargs`` when the provider named a parameter *and* its replacement.

    On **Azure/Foundry this cannot be predicted from the model id**: the id a call carries is the
    *deployment* name, which the user chose — ``"my-chat"`` says nothing about whether the model
    behind it is a reasoning family. So a name-based rule (like
    :func:`~cendor.sdk.providers._openai_supports_temperature`) is structurally unable to solve it,
    and the provider's own error message is the only reliable signal. Measured 2026-07-31 against a
    live Foundry deployment: ``Agent(max_tokens=…)`` with ``provider="azure"`` 400'd outright.

    Deliberately narrow — it repairs only when the message names both sides, the old key is
    actually in ``kwargs``, and the new key is not already set. Returns ``None`` otherwise, so
    every other failure raises exactly as before.
    """
    m = _PARAM_SWAP_RE.search(str(exc))
    if m is None:
        return None
    old, new = m.group("old"), m.group("new")
    if old not in kwargs or new in kwargs:
        return None
    repaired = dict(kwargs)
    repaired[new] = repaired.pop(old)
    return repaired


def call_with_retry(fn: Callable[..., object], kwargs: dict, retry: RetryPolicy | None) -> object:
    """Call ``fn(**kwargs)``, retrying transient failures per ``retry`` (no retry if ``None``).

    Independently of ``retry``, one **parameter-swap repair** is allowed: if the provider rejects a
    request by naming the unsupported parameter *and* its replacement, the call is re-issued once
    with the rename applied (see :func:`_param_swap`). It is not a transient retry — it is the only
    way to be right about an Azure deployment name.
    """
    if retry is None:
        try:
            return fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - repair once if the provider named the swap
            repaired = _param_swap(exc, kwargs)
            if repaired is None:
                raise
            return fn(**repaired)
    attempt = 0
    swapped = False
    while True:
        try:
            return fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - classify, then re-raise non-transient/exhausted
            if not swapped:
                repaired = _param_swap(exc, kwargs)
                if repaired is not None:
                    kwargs, swapped = repaired, True
                    continue  # a rename, not a transient failure: no backoff, no attempt spent
            attempt += 1
            if attempt >= retry.max_attempts or not retry.should_retry(exc):
                raise
            retry.sleep(retry.delay(attempt - 1))


async def acall_with_retry(
    fn: Callable[..., object], kwargs: dict, retry: RetryPolicy | None
) -> object:
    """Async counterpart of :func:`call_with_retry` (awaits ``fn`` and sleeps with ``asyncio``)."""
    if retry is None:
        try:
            return await fn(**kwargs)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - repair once if the provider named the swap
            repaired = _param_swap(exc, kwargs)
            if repaired is None:
                raise
            return await fn(**repaired)  # type: ignore[misc]
    attempt = 0
    swapped = False
    while True:
        try:
            return await fn(**kwargs)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - classify, then re-raise non-transient/exhausted
            if not swapped:
                repaired = _param_swap(exc, kwargs)
                if repaired is not None:
                    kwargs, swapped = repaired, True
                    continue  # a rename, not a transient failure: no backoff, no attempt spent
            attempt += 1
            if attempt >= retry.max_attempts or not retry.should_retry(exc):
                raise
            await asyncio.sleep(retry.delay(attempt - 1))
