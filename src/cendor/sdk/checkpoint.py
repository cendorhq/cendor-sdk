"""Checkpointed / resumable runs — local-first.

A ``Checkpointer`` persists a run's conversation to a local JSON file after each turn, so a long
agent can resume after a crash or restart without re-doing completed work (already-run tools are in
the saved messages and are not re-executed). Local by default; no server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Checkpointer:
    """Persist and restore run state to a local JSON file."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        """The saved state (``{run_id, messages, done, output}``), or ``None`` if absent/bad."""
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, state: dict[str, Any]) -> None:
        """Atomically write the run state (temp file + replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path)

    def resumable_messages(self) -> list[dict] | None:
        """Saved messages to resume from, or ``None`` if there's no unfinished checkpoint."""
        state = self.load()
        if state and not state.get("done"):
            return list(state.get("messages") or [])
        return None

    def finished(self) -> dict[str, Any] | None:
        """The full saved state iff it is a finished (``done``) run, else ``None``.

        Callers early-return a completed ``Result`` from this stored ``{output, messages}`` — so
        resuming an already-finished run re-invokes neither the model nor any tool.
        """
        state = self.load()
        if state and state.get("done"):
            return state
        return None

    def clear(self) -> None:
        """Delete the checkpoint file (e.g. after a successful, finished run)."""
        try:
            self.path.unlink()
        except OSError:
            pass


def _as_checkpointer(value: Any) -> Checkpointer | None:
    if value is None or isinstance(value, Checkpointer):
        return value
    return Checkpointer(str(value))
