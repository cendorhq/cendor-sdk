"""Session memory — conversation state carried across ``run()`` calls.

Phase 1 ships an in-memory ``Session``. Phase 2 adds optional local persistence (JSON/SQLite via a
``Sink``); Phase 4 adds durable, resumable stores. The in-memory shape is the stable base the later
phases build on.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Session:
    """In-memory conversation memory.

    Pass the same ``Session`` to successive ``run()`` calls and the agent remembers the prior turns
    (within the process). The stored messages are canonical (OpenAI-shape).
    """

    messages: list[dict] = field(default_factory=list)

    def add(self, message: dict) -> None:
        """Append one message."""
        self.messages.append(message)

    def extend(self, messages: list[dict]) -> None:
        """Append several messages."""
        self.messages.extend(messages)

    def snapshot(self) -> list[dict]:
        """A shallow copy of the current messages (safe to mutate)."""
        return list(self.messages)

    def replace(self, messages: list[dict]) -> None:
        """Replace the stored conversation wholesale (used by the runner to write back a run)."""
        self.messages = list(messages)

    def clear(self) -> None:
        """Forget the conversation."""
        self.messages.clear()

    def __len__(self) -> int:
        return len(self.messages)
