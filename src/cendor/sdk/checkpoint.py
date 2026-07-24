"""Checkpointed / resumable runs — local-first.

A ``Checkpointer`` persists a run's conversation to a local JSON file after each turn, so a long
agent can resume after a crash or restart without re-doing completed work (already-run tools are in
the saved messages and are not re-executed). Local by default; no server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import _telemetry as _tel


class Checkpointer:
    """Persist and restore run state to a local JSON file.

    Pass a path (or a ``Checkpointer``) as ``run(..., checkpoint=…)`` to make a run resumable: the
    conversation is saved after every turn, so re-running the same call after a crash picks up where
    it left off and already-run tools are not re-executed. A finished run returns its stored
    ``Result`` without touching the model.

    ```python
    from cendor.sdk import Agent, run
    agent = Agent(name="researcher", model="gpt-4o")
    result = run(agent, "Draft the Q3 report.", checkpoint="run.db")  # resumes on re-run
    ```
    """

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
        # E-wave: checkpoint.save span (correlated by the run id carried in the state itself).
        _tel.emit_checkpoint(
            "save",
            str(state.get("run_id") or ""),
            done=bool(state.get("done")),
            turns=len(state.get("messages") or []),
            segment=state.get("seg"),
        )

    def resumable_messages(self) -> list[dict] | None:
        """Saved messages to resume from, or ``None`` if there's no unfinished checkpoint."""
        state = self.load()
        if state and not state.get("done"):
            msgs = list(state.get("messages") or [])
            _tel.emit_checkpoint(  # E-wave: checkpoint.resume span (unfinished — continue the run)
                "resume",
                str(state.get("run_id") or ""),
                done=False,
                turns=len(msgs),
                segment=state.get("seg"),
            )
            return msgs
        return None

    def finished(self) -> dict[str, Any] | None:
        """The full saved state iff it is a finished (``done``) run, else ``None``.

        Callers early-return a completed ``Result`` from this stored ``{output, messages}`` — so
        resuming an already-finished run re-invokes neither the model nor any tool.
        """
        state = self.load()
        if state and state.get("done"):
            _tel.emit_checkpoint(  # E-wave: checkpoint.resume span (finished — no model call)
                "resume",
                str(state.get("run_id") or ""),
                done=True,
                turns=len(state.get("messages") or []),
                segment=state.get("seg"),
            )
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
