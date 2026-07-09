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
No import from acttrace here; guardrails imports only ``cendor.core`` (constitution rule 2).
"""

from __future__ import annotations

from typing import Any

from cendor.guardrails import Context, GuardrailTripped
from cendor.guardrails import evaluate as _evaluate
from cendor.guardrails import evaluate_async as _evaluate_async


def effective(agent: Any, override: Any) -> list:
    """The guardrails in force for a run: the per-run ``override`` if given, else the agent's."""
    if override is not None:
        return list(override)
    return list(getattr(agent, "guardrails", []) or [])


def _has(guardrails: list, stage: str) -> bool:
    return any(stage in g.stages for g in guardrails)


def _blocked_message(exc: GuardrailTripped) -> str:
    d = exc.decisions[-1]
    msg = f"[blocked by {d.guardrail}]"
    return f"{msg} {d.reason}" if d.reason else msg


# --------------------------------------------------------------------------- input (pre-spend)


def gate_input_sync(guardrails: list, agent: Any, messages: list[dict], run_id: str) -> None:
    """Gate the outgoing messages before the first model call. Block raises; redact rewrites
    ``messages`` in place so every turn (and the persisted session) sees the cleaned content."""
    if not _has(guardrails, "input"):
        return
    ctx = Context(stage="input", agent=agent.name, trace_id=run_id)
    cleaned, _ = _evaluate(guardrails, "input", messages, ctx)  # raises GuardrailTripped on block
    _apply_message_redaction(messages, cleaned)


async def gate_input_async(guardrails: list, agent: Any, messages: list[dict], run_id: str) -> None:
    if not _has(guardrails, "input"):
        return
    ctx = Context(stage="input", agent=agent.name, trace_id=run_id)
    cleaned, _ = await _evaluate_async(guardrails, "input", messages, ctx)
    _apply_message_redaction(messages, cleaned)


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
        cleaned, _ = _evaluate(guardrails, "tool_call", tc.arguments, ctx)
    except GuardrailTripped as exc:
        return _blocked_message(exc)
    tc.arguments = cleaned
    return None


async def gate_tool_call_async(guardrails: list, agent: Any, tc: Any, run_id: str) -> str | None:
    if not _has(guardrails, "tool_call"):
        return None
    ctx = Context(
        stage="tool_call", agent=agent.name, tool=tc.name, tool_args=tc.arguments, trace_id=run_id
    )
    try:
        cleaned, _ = await _evaluate_async(guardrails, "tool_call", tc.arguments, ctx)
    except GuardrailTripped as exc:
        return _blocked_message(exc)
    tc.arguments = cleaned
    return None


def gate_tool_output_sync(guardrails: list, agent: Any, tc: Any, result: str, run_id: str) -> str:
    """Gate a tool result before the model sees it. Block replaces it with a ``"[blocked …]"``
    message; redact substitutes the cleaned text; flag records and passes it through."""
    if not _has(guardrails, "tool_output"):
        return result
    ctx = Context(stage="tool_output", agent=agent.name, tool=tc.name, trace_id=run_id)
    try:
        cleaned, _ = _evaluate(guardrails, "tool_output", result, ctx)
    except GuardrailTripped as exc:
        return _blocked_message(exc)
    return cleaned if isinstance(cleaned, str) else result


async def gate_tool_output_async(
    guardrails: list, agent: Any, tc: Any, result: str, run_id: str
) -> str:
    if not _has(guardrails, "tool_output"):
        return result
    ctx = Context(stage="tool_output", agent=agent.name, tool=tc.name, trace_id=run_id)
    try:
        cleaned, _ = await _evaluate_async(guardrails, "tool_output", result, ctx)
    except GuardrailTripped as exc:
        return _blocked_message(exc)
    return cleaned if isinstance(cleaned, str) else result


# --------------------------------------------------------------------------- output


def gate_output_sync(guardrails: list, agent: Any, output: Any, run_id: str) -> Any:
    """Gate the model's final answer. Block raises (post-generation, before the Result is returned);
    redact returns the cleaned text; flag records. No-op when there is no final output."""
    if output is None or not _has(guardrails, "output"):
        return output
    ctx = Context(stage="output", agent=agent.name, trace_id=run_id)
    cleaned, _ = _evaluate(guardrails, "output", output, ctx)  # raises GuardrailTripped on block
    return cleaned


async def gate_output_async(guardrails: list, agent: Any, output: Any, run_id: str) -> Any:
    if output is None or not _has(guardrails, "output"):
        return output
    ctx = Context(stage="output", agent=agent.name, trace_id=run_id)
    cleaned, _ = await _evaluate_async(guardrails, "output", output, ctx)
    return cleaned
