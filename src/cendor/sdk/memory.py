"""Session memory — conversation state carried across ``run()`` calls.

An in-memory ``Session`` carries turns within a process; ``save``/``load`` add optional local JSON
persistence, and ``SQLiteSessionStore`` gives a durable, resumable store keyed by conversation id.
All local-first — no server, a single file on disk.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Session:
    """In-memory conversation memory, with optional local JSON persistence.

    Pass the same ``Session`` to successive ``run()`` calls and the agent remembers the prior turns
    (within the process). The stored messages are canonical (OpenAI-shape). ``save``/``load`` give
    resumable, local-first persistence (no server); ``SQLiteSessionStore`` is the durable variant.

    ```python
    from cendor.sdk import Agent, Session, run
    mem = Session()
    run(agent, "My name is Ada.", session=mem)
    run(agent, "What's my name?", session=mem)   # remembers "Ada"
    ```
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


_MEMORY_PREFIX = "Conversation summary so far:\n"

#: A summarizer folds old turns into a note: ``summarizer(old_messages, prior_summary) -> summary``.
Summarizer = Callable[[list[dict], "str | None"], str]


def _render_messages(messages: list[dict]) -> str:
    """Flatten canonical messages to plain text for a summarizer (tool calls noted inline)."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content")
        if isinstance(content, list):  # multimodal → keep the text parts
            content = " ".join(
                str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("text")
            )
        text = str(content or "")
        for tc in m.get("tool_calls") or []:
            text += f" [called {tc.get('function', {}).get('name', '')}]"
        lines.append(f"{role}: {text}".strip())
    return "\n".join(lines)


def llm_summarizer(
    model: str,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 512,
) -> Summarizer:
    """Build a governed summarizer that folds old turns into a concise note via one model call.

    The summarization is itself a governed ``run`` (its ``LLMCall`` rides the bus — cost/audit
    apply). Use a cheap model here (e.g. ``gpt-4o-mini``). Synchronous; for ``run.aio`` this runs
    between turns, so keep it fast or pass your own async-friendly summarizer.

    ```python
    from cendor.sdk import SummarizingSession, llm_summarizer
    mem = SummarizingSession(summarizer=llm_summarizer("gpt-4o-mini"), max_messages=20)
    ```
    """

    def summarize(old: list[dict], prior: str | None) -> str:
        from .agent import Agent
        from .runner import run

        parts = []
        if prior:
            parts.append(f"Summary so far:\n{prior}\n")
        parts.append("Additional conversation to fold in:\n" + _render_messages(old))
        prompt = "\n".join(parts) + (
            "\n\nReturn an updated, concise summary preserving durable facts, decisions, names, "
            "and open threads. Prose only."
        )
        agent = Agent(
            name="memory-summarizer",
            model=model,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            instructions="You maintain a running summary of a conversation as durable memory.",
            max_tokens=max_tokens,
        )
        return str(run(agent, prompt).output or "")

    return summarize


class SummarizingSession(Session):
    """A ``Session`` that folds old turns into a durable summary note when it grows too long.

    Keeps the last ``keep_recent`` messages verbatim; older turns are summarized into a single
    system "memory" message (merged with any prior summary), so the conversation stays bounded but
    the gist persists — beyond what ``context_budget`` (token-budget trimming) alone retains.
    Summarization triggers automatically after each run (when the runner writes the session back).

    ```python
    from cendor.sdk import SummarizingSession, run
    mem = SummarizingSession(model="gpt-4o-mini", max_messages=20, keep_recent=8)
    run(agent, "…", session=mem)   # long chats auto-summarize; recent turns stay verbatim
    ```

    Pass a custom ``summarizer`` callable instead of ``model`` for offline/extractive summaries.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        summarizer: Summarizer | None = None,
        max_messages: int = 20,
        keep_recent: int = 8,
        messages: list[dict] | None = None,
    ) -> None:
        super().__init__(messages=list(messages or []))
        if summarizer is None and model is None:
            raise ValueError("SummarizingSession needs a summarizer= callable or a model=")
        self._summarizer: Summarizer = summarizer or llm_summarizer(model)  # type: ignore[arg-type]
        self.max_messages = max_messages
        self.keep_recent = keep_recent

    def replace(self, messages: list[dict]) -> None:
        super().replace(messages)
        self._maybe_summarize()

    def _maybe_summarize(self) -> None:
        if len(self.messages) <= self.max_messages:
            return
        body = self.messages
        prior: str | None = None
        head = body[0] if body else None
        if (
            head
            and head.get("role") == "system"
            and str(head.get("content", "")).startswith(_MEMORY_PREFIX)
        ):
            prior = str(head["content"])[len(_MEMORY_PREFIX) :]
            body = body[1:]
        keep = max(self.keep_recent, 0)
        old = body[:-keep] if keep else body
        recent = body[-keep:] if keep else []
        if not old:
            return
        summary = self._summarizer(old, prior)
        note = {"role": "system", "content": _MEMORY_PREFIX + summary}
        self.messages = [note, *recent]


class SQLiteSessionStore:
    """A durable, local session store: conversations keyed by id in SQLite.

    Local-first — a single file on disk, no server. Use it to persist and resume many named
    conversations across processes.

    ```python
    store = SQLiteSessionStore("sessions.db")
    session = store.load("user-42")          # empty Session if unknown
    run(agent, "hi", session=session)
    store.save("user-42", session)           # durable across restarts
    ```

    The canonical Python spelling is ``SQLiteSessionStore``. The TypeScript port names it
    ``SqliteSessionStore`` (lowercase ``qlite``); that spelling is also accepted here as a
    deprecated alias, so the wrong casing resolves-and-teaches instead of raising ``ImportError``.
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


# deprecated casing alias — canonical is SQLiteSessionStore (TS uses SqliteSessionStore)
SqliteSessionStore = SQLiteSessionStore
