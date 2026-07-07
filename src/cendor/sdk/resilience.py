"""Resilience: retries with exponential backoff around the model call.

A small, provider-agnostic retry policy applied to the model call in the loop. It retries only
*transient* failures (timeouts, connection errors, rate limits, 5xx) and **never** retries a
governance decision — ``BudgetExceeded`` / ``PolicyViolation`` are terminal by design. Backoff is
exponential with a cap; the sleep function is injectable so tests run instantly.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

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
_NEVER_RETRY = {"budgetexceeded", "policyviolation"}


def default_is_transient(exc: BaseException) -> bool:
    """Heuristic: is ``exc`` a transient provider error worth retrying?

    Governance exceptions (``BudgetExceeded``/``PolicyViolation``) are never transient. Otherwise a
    call is retried if it carries a retryable HTTP status or its type name looks transient — using
    duck typing so no provider SDK needs importing.
    """
    name = type(exc).__name__.lower()
    if name in _NEVER_RETRY:
        return False
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if isinstance(status, int) and status in _TRANSIENT_STATUS:
        return True
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


def call_with_retry(fn: Callable[..., object], kwargs: dict, retry: RetryPolicy | None) -> object:
    """Call ``fn(**kwargs)``, retrying transient failures per ``retry`` (no retry if ``None``)."""
    if retry is None:
        return fn(**kwargs)
    attempt = 0
    while True:
        try:
            return fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - classify, then re-raise non-transient/exhausted
            attempt += 1
            if attempt >= retry.max_attempts or not retry.should_retry(exc):
                raise
            retry.sleep(retry.delay(attempt - 1))


async def acall_with_retry(
    fn: Callable[..., object], kwargs: dict, retry: RetryPolicy | None
) -> object:
    """Async counterpart of :func:`call_with_retry` (awaits ``fn`` and sleeps with ``asyncio``)."""
    if retry is None:
        return await fn(**kwargs)  # type: ignore[misc]
    attempt = 0
    while True:
        try:
            return await fn(**kwargs)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - classify, then re-raise non-transient/exhausted
            attempt += 1
            if attempt >= retry.max_attempts or not retry.should_retry(exc):
                raise
            await asyncio.sleep(retry.delay(attempt - 1))
