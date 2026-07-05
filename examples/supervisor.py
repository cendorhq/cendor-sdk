"""Supervisor + 2 sub-agents with per-agent budgets and ONE verifiable audit trail — OFFLINE.

A coordinator routes to a researcher (which then hands to a writer). The whole trajectory is one
correlated tree (one parent trace id; a child id per agent) recorded on a single tamper-evident
audit chain — a decision per agent segment. In production, drop ``client=`` and set your API keys.

Run it:  uv run python examples/supervisor.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, AuditLog, supervisor, track, verify


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls" if tool_calls else "stop",
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=60, completion_tokens=14),
    )


def _transfer(target: str, call_id: str):
    return [
        SimpleNamespace(
            id=call_id,
            type="function",
            function=SimpleNamespace(name=f"transfer_to_{target}", arguments="{}"),
        )
    ]


def _stub(responses) -> object:
    it = iter(responses)

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def main() -> None:
    client = _stub(
        [
            _msg(tool_calls=_transfer("researcher", "c1")),  # coordinator -> researcher
            _msg(tool_calls=_transfer("writer", "c2")),  # researcher -> writer
            _msg(content="A governed brief on X."),  # writer answers
        ]
    )

    coordinator = Agent(name="coordinator", model="gpt-4o", instructions="Route.", client=client)
    researcher = Agent(
        name="researcher",
        model="gpt-4o",
        instructions="Research, then hand to the writer.",
        handoffs=["writer"],
        client=client,
        max_usd=0.50,
    )
    writer = Agent(name="writer", model="gpt-4o", instructions="Write it up.", client=client)

    audit_path = Path(tempfile.gettempdir()) / "cendor_sdk_supervisor_audit.jsonl"
    log = AuditLog(system="research-team", risk_tier="high", path=str(audit_path))
    with track(feature="research"):
        result = supervisor(coordinator, [researcher, writer], "Investigate X", audit=log)
    log.detach()

    print("output :", result.output)
    print("agents :", result.agents)
    print("one tree:", all(s.trace_id.startswith(result.trace_id) for s in result.steps))
    decisions = [e for e in log.entries if e.type == "decision"]
    print("decisions (one per agent):", len(decisions))
    ok, detail = verify(str(audit_path))
    print("audit ok:", ok, "—", detail)


if __name__ == "__main__":
    main()
