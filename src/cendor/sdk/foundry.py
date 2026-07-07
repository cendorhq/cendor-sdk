"""Publish a governed agent as a custom-engine agent for Microsoft 365 / Foundry.

A dependency-free adapter over the Bot Framework **Activity** protocol — the messaging surface a
custom-engine agent exposes to Copilot / Teams / Azure AI Foundry. ``FoundryAdapter.on_activity``
takes an inbound ``message`` Activity, runs the governed agent, and returns an outbound Activity
(carrying governance metadata: trace id + cost). You wire this into your web endpoint of choice;
the governance (budgets/audit/redaction) rides the same seams as any other run.
"""

from __future__ import annotations

from typing import Any

from .agent import Agent
from .runner import run


class FoundryAdapter:
    """Adapt a ``cendor.sdk`` agent to the Bot Framework Activity protocol (custom-engine agent)."""

    def __init__(self, agent: Agent, *, audit: Any = None) -> None:
        self.agent = agent
        self.audit = audit

    def on_activity(self, activity: dict) -> dict | None:
        """Handle one inbound Activity, returning the outbound reply Activity (or ``None``).

        Non-``message`` activities (e.g. ``conversationUpdate``) return ``None`` — the endpoint
        simply acks them.
        """
        if activity.get("type") != "message":
            return None
        text = activity.get("text", "")
        result = run(self.agent, text, audit=self.audit)
        return {
            "type": "message",
            "text": str(result.output),
            "from": {"id": self.agent.name, "name": self.agent.name},
            "recipient": activity.get("from"),
            "conversation": activity.get("conversation"),
            "replyToId": activity.get("id"),
            "channelData": {
                "cendor": {
                    "trace_id": result.trace_id,
                    "cost_usd": str(result.cost.amount),
                    "agents": result.agents,
                }
            },
        }

    def manifest(self) -> dict:
        """A minimal custom-engine agent manifest (name/description) for registration."""
        return {
            "name": self.agent.name,
            "description": self.agent.instructions or f"The {self.agent.name} agent.",
            "type": "custom-engine",
            "model": self.agent.model,
        }
