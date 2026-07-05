"""Human-in-the-loop: gate a tool behind human approval, recorded in the audit chain (plan §7 P3).

``acttrace`` records *that* oversight happened; the pause/approve/resume is the application's job.
``require_approval`` wraps a tool so that, before it runs, an ``approver`` callback is consulted
(the pause) and the verdict is written to the run's audit chain via ``decision.human_oversight()`` —
tied to the same ``decision`` the run is already correlated by. On approval the real tool runs; on
rejection a denial is returned to the model. The ``approver`` is where a real deployment blocks on a
reviewer (a prompt, a queue, a webhook); here it is a plain callable so it stays testable and local.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .runner import _active_decision, _resolve_sync
from .tools import Tool, as_tool

#: An approver takes (tool_name, arguments) and returns ``bool`` or ``(bool, note)``.
Approver = Callable[[str, dict], "bool | tuple[bool, str]"]


def _verdict(result: Any) -> tuple[bool, str]:
    if isinstance(result, tuple):
        return bool(result[0]), str(result[1]) if len(result) > 1 else ""
    return bool(result), ""


def require_approval(
    tool: Any,
    *,
    approver: Approver,
    reviewer: str = "human",
) -> Tool:
    """Wrap ``tool`` so each call pauses for human approval, recorded via ``human_oversight``.

    ```python
    from cendor.sdk import Agent, run, AuditLog
    from cendor.sdk.hitl import require_approval

    refund_tool = require_approval(issue_refund, approver=lambda name, args: args["amount"] < 100)
    agent = Agent(name="support", model="gpt-4o", tools=[refund_tool])
    log = AuditLog(system="support", path="audit.jsonl")
    run(agent, "Refund order 42", audit=log)   # audit.jsonl gets a human_oversight entry
    ```
    """
    inner = as_tool(tool)

    def guarded(**kwargs: Any) -> Any:
        approved, note = _verdict(approver(inner.name, dict(kwargs)))
        decision = _active_decision.get()
        if decision is not None:
            try:
                decision.human_oversight(
                    reviewer=reviewer,
                    action="approved" if approved else "rejected",
                    note=note or inner.name,
                )
            except Exception:  # noqa: BLE001 - oversight recording is best-effort
                pass
        if not approved:
            return f"[denied] human oversight rejected '{inner.name}'" + (
                f": {note}" if note else ""
            )
        # Call the raw function (the guarded wrapper is itself instrumented, so one ToolCall is
        # emitted — not two). Resolve an async tool's coroutine from this sync path.
        return _resolve_sync(inner.func(**kwargs))

    return Tool(
        name=inner.name,
        description=inner.description,
        parameters=inner.parameters,
        func=guarded,
        is_async=False,
    )


def always_approve(name: str, arguments: dict) -> bool:
    """An approver that approves everything (useful as a default / in tests)."""
    return True


def always_reject(name: str, arguments: dict) -> tuple[bool, str]:
    """An approver that rejects everything."""
    return False, "auto-rejected"
