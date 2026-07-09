"""Guardrail wiring for the agent loop — evaluate the four stages at the runner's seams.

Thin adapters over ``cendor.guardrails``: build the :class:`~cendor.guardrails.Context`, evaluate
(sync or async), and translate the verdict into the loop's control flow —

* **input** (pre-spend) and **output**: a ``block`` **raises** ``GuardrailTripped`` (fail-closed);
  a ``redact`` rewrites the payload in place / returns the cleaned value; a ``flag`` records.
* **tool_call** / **tool_output**: a ``block`` returns a ``"[blocked by <name>] <reason>"`` tool
  result so the loop continues without the side effect (mirrors ``hitl``'s ``"[denied]"``); a
  ``redact`` rewrites the arguments / result; a ``flag`` records.

Every trip/flag emits a ``GuardrailDecision`` on the bus, so an attached ``AuditLog`` chains it —
correlated with the run's decision because gating runs inside the runner's ``_decision`` scope.
The same decisions are also **collected** (via :func:`collecting`) so ``Result.guardrail_decisions``
can surface them post-hoc without re-reading the audit file. Each guardrail's ``timeout`` /
``on_error`` policy is honoured by ``cendor.guardrails.evaluate`` itself — the SDK inherits it for
free. No import from acttrace here; guardrails imports only ``cendor.core`` (constitution rule 2).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from cendor.guardrails import Context, GuardrailDecision, GuardrailTripped
from cendor.guardrails import evaluate as _evaluate
from cendor.guardrails import evaluate_async as _evaluate_async

#: The valid guardrail execution modes. ``blocking`` (default) runs input-stage guardrails before
#: the first model call (a block is pre-spend, ``$0``). ``parallel`` overlaps them with the first
#: model call for lower latency on the pass path — only worth it for slow tier-3/4 checks (an LLM
#: judge, a hosted rail), and async-only. See :func:`effective_mode`.
MODES: tuple[str, ...] = ("blocking", "parallel")


def effective(agent: Any, override: Any) -> list:
    """The guardrails in force for a run: the per-run ``override`` if given, else the agent's."""
    if override is not None:
        return list(override)
    return list(getattr(agent, "guardrails", []) or [])


def effective_mode(agent: Any, override: str | None) -> str:
    """The guardrail execution mode for a run: the per-run ``override`` if given, else the agent's
    ``guardrail_mode`` (default ``"blocking"``). Validated against :data:`MODES`."""
    mode = override if override is not None else getattr(agent, "guardrail_mode", "blocking")
    if mode not in MODES:
        raise ValueError(f"unknown guardrail_mode {mode!r}; must be one of {MODES}")
    return mode


def _has(guardrails: list, stage: str) -> bool:
    return any(stage in g.stages for g in guardrails)


def _blocked_message(exc: GuardrailTripped) -> str:
    d = exc.decisions[-1]
    msg = f"[blocked by {d.guardrail}]"
    return f"{msg} {d.reason}" if d.reason else msg


# --------------------------------------------------------------------------- decision collection

#: The list collecting this run's guardrail decisions (``contextvars`` — concurrency-correct for
#: overlapping async runs), or ``None`` outside a :func:`collecting` scope.
_collected: ContextVar[list[GuardrailDecision] | None] = ContextVar(
    "cendor_sdk_guardrail_decisions", default=None
)


@contextmanager
def collecting() -> Iterator[list[GuardrailDecision]]:
    """Collect every guardrail decision recorded during the block — the source for
    ``Result.guardrail_decisions``. Nesting is safe; the outer collector is restored on exit."""
    box: list[GuardrailDecision] = []
    token = _collected.set(box)
    try:
        yield box
    finally:
        _collected.reset(token)


def _record(decisions: list[GuardrailDecision]) -> None:
    box = _collected.get()
    if box is not None and decisions:
        box.extend(decisions)


def snapshot() -> list[GuardrailDecision]:
    """A copy of the decisions collected so far in the active :func:`collecting` scope (``[]`` if
    none is active). Read at every ``Result`` construction site."""
    box = _collected.get()
    return list(box) if box is not None else []


# --------------------------------------------------------------------------- input (pre-spend)


def gate_input_sync(guardrails: list, agent: Any, messages: list[dict], run_id: str) -> None:
    """Gate the outgoing messages before the first model call. Block raises; redact rewrites
    ``messages`` in place so every turn (and the persisted session) sees the cleaned content."""
    if not _has(guardrails, "input"):
        return
    ctx = Context(stage="input", agent=agent.name, trace_id=run_id)
    cleaned, decs = _evaluate(
        guardrails, "input", messages, ctx
    )  # raises GuardrailTripped on block
    _record(decs)
    _apply_message_redaction(messages, cleaned)


async def gate_input_async(guardrails: list, agent: Any, messages: list[dict], run_id: str) -> None:
    if not _has(guardrails, "input"):
        return
    ctx = Context(stage="input", agent=agent.name, trace_id=run_id)
    cleaned, decs = await _evaluate_async(guardrails, "input", messages, ctx)
    _record(decs)
    _apply_message_redaction(messages, cleaned)


def start_input_gate_async(
    guardrails: list, agent: Any, messages: list[dict], run_id: str
) -> asyncio.Task | None:
    """Parallel mode: start the input-stage evaluation as a task so it runs **concurrently with the
    first model call**, and return it (or ``None`` when there are no input guardrails).

    The caller ``await``s the returned task after issuing the first model call; a block surfaces as
    ``GuardrailTripped`` there. Unlike blocking mode this does **not** rewrite ``messages`` before
    the call (the call is already in flight), so parallel mode is for block/flag input checks, not
    redaction — documented in docs/guardrails.md. On the pass path the check's latency is hidden
    behind the model call; on a block the model call may already have completed (and been billed),
    where blocking mode guarantees ``$0``.
    """
    if not _has(guardrails, "input"):
        return None
    ctx = Context(stage="input", agent=agent.name, trace_id=run_id)

    async def _run() -> None:
        _cleaned, decs = await _evaluate_async(guardrails, "input", list(messages), ctx)
        _record(decs)

    return asyncio.ensure_future(_run())


def _apply_message_redaction(messages: list[dict], cleaned: Any) -> None:
    if cleaned is not messages and isinstance(cleaned, list):
        messages[:] = cleaned


# ----------------------------------------------------------------------- tool_call / tool_output


def gate_tool_call_sync(guardrails: list, agent: Any, tc: Any, run_id: str) -> str | None:
    """Gate a tool call. Returns a ``"[blocked …]"`` result string to short-circuit the tool, or
    ``None`` to proceed (after rewriting ``tc.arguments`` for a redact)."""
    if not _has(guardrails, "tool_call"):
        return None
    ctx = Context(
        stage="tool_call", agent=agent.name, tool=tc.name, tool_args=tc.arguments, trace_id=run_id
    )
    try:
        cleaned, decs = _evaluate(guardrails, "tool_call", tc.arguments, ctx)
    except GuardrailTripped as exc:
        _record(exc.decisions)
        return _blocked_message(exc)
    _record(decs)
    tc.arguments = cleaned
    return None


async def gate_tool_call_async(guardrails: list, agent: Any, tc: Any, run_id: str) -> str | None:
    if not _has(guardrails, "tool_call"):
        return None
    ctx = Context(
        stage="tool_call", agent=agent.name, tool=tc.name, tool_args=tc.arguments, trace_id=run_id
    )
    try:
        cleaned, decs = await _evaluate_async(guardrails, "tool_call", tc.arguments, ctx)
    except GuardrailTripped as exc:
        _record(exc.decisions)
        return _blocked_message(exc)
    _record(decs)
    tc.arguments = cleaned
    return None


def gate_tool_output_sync(guardrails: list, agent: Any, tc: Any, result: str, run_id: str) -> str:
    """Gate a tool result before the model sees it. Block replaces it with a ``"[blocked …]"``
    message; redact substitutes the cleaned text; flag records and passes it through."""
    if not _has(guardrails, "tool_output"):
        return result
    ctx = Context(stage="tool_output", agent=agent.name, tool=tc.name, trace_id=run_id)
    try:
        cleaned, decs = _evaluate(guardrails, "tool_output", result, ctx)
    except GuardrailTripped as exc:
        _record(exc.decisions)
        return _blocked_message(exc)
    _record(decs)
    return cleaned if isinstance(cleaned, str) else result


async def gate_tool_output_async(
    guardrails: list, agent: Any, tc: Any, result: str, run_id: str
) -> str:
    if not _has(guardrails, "tool_output"):
        return result
    ctx = Context(stage="tool_output", agent=agent.name, tool=tc.name, trace_id=run_id)
    try:
        cleaned, decs = await _evaluate_async(guardrails, "tool_output", result, ctx)
    except GuardrailTripped as exc:
        _record(exc.decisions)
        return _blocked_message(exc)
    _record(decs)
    return cleaned if isinstance(cleaned, str) else result


# --------------------------------------------------------------------------- output


def gate_output_sync(guardrails: list, agent: Any, output: Any, run_id: str) -> Any:
    """Gate the model's final answer. Block raises (post-generation, before the Result is returned);
    redact returns the cleaned text; flag records. No-op when there is no final output."""
    if output is None or not _has(guardrails, "output"):
        return output
    ctx = Context(stage="output", agent=agent.name, trace_id=run_id)
    cleaned, decs = _evaluate(guardrails, "output", output, ctx)  # raises GuardrailTripped on block
    _record(decs)
    return cleaned


async def gate_output_async(guardrails: list, agent: Any, output: Any, run_id: str) -> Any:
    if output is None or not _has(guardrails, "output"):
        return output
    ctx = Context(stage="output", agent=agent.name, trace_id=run_id)
    cleaned, decs = await _evaluate_async(guardrails, "output", output, ctx)
    _record(decs)
    return cleaned
