"""Session memory — conversation state carried across ``run()`` calls.

Phase 1 ships an in-memory ``Session``. Phase 2 adds optional local persistence (JSON/SQLite via a
``Sink``); Phase 4 adds durable, resumable stores. The in-memory shape is the stable base the later
phases build on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Session:
    """In-memory conversation memory, with optional local JSON persistence.

    Pass the same ``Session`` to successive ``run()`` calls and the agent remembers the prior turns
    (within the process). The stored messages are canonical (OpenAI-shape). ``save``/``load`` give
    resumable, local-first persistence (no server); Phase 4 adds durable ``Sink``-shaped stores.
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

    # --- optional local persistence (JSON) ------------------------------------------------------

    def save(self, path: str) -> None:
        """Persist the conversation to a local JSON file (creates parent dirs)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"messages": self.messages}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> Session:
        """Load a conversation from a local JSON file (empty session if the file is absent)."""
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(messages=list(data.get("messages", [])))

    def __len__(self) -> int:
        return len(self.messages)


class SQLiteSessionStore:
    """A durable, local session store (Phase 4): conversations keyed by id in SQLite.

    Local-first — a single file on disk, no server. Use it to persist and resume many named
    conversations across processes.

    ```python
    store = SQLiteSessionStore("sessions.db")
    session = store.load("user-42")          # empty Session if unknown
    run(agent, "hi", session=session)
    store.save("user-42", session)           # durable across restarts
    ```
    """

    def __init__(self, path: str) -> None:
        import sqlite3

        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, messages TEXT NOT NULL)"
        )
        self._conn.commit()

    def save(self, session_id: str, session: Session) -> None:
        """Persist a session's messages under ``session_id`` (upsert)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (id, messages) VALUES (?, ?)",
            (session_id, json.dumps(session.messages)),
        )
        self._conn.commit()

    def load(self, session_id: str) -> Session:
        """Load a session by id (an empty ``Session`` if unknown)."""
        row = self._conn.execute(
            "SELECT messages FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return Session()
        return Session(messages=list(json.loads(row[0])))

    def ids(self) -> list[str]:
        """All stored session ids."""
        return [r[0] for r in self._conn.execute("SELECT id FROM sessions").fetchall()]

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
