"""Multi-agent runs are checkpointed & resumable (P1-7). Offline via a stub client."""

from __future__ import annotations

import json
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, run


def _stub(answer: str):
    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=answer, tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
            )

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_multi_agent_checkpoint_written(tmp_path):
    a = Agent(name="a", model="gpt-4o", client=_stub("done"))
    ckpt = tmp_path / "team.ckpt.json"
    result = run([a], "hi", checkpoint=str(ckpt))  # a list → orchestration path
    assert result.output == "done"
    state = json.loads(ckpt.read_text())
    assert state["done"] is True
    assert state["output"] == "done"


def test_multi_agent_checkpoint_resumes(tmp_path):
    ckpt = tmp_path / "team.ckpt.json"
    ckpt.write_text(
        json.dumps(
            {
                "run_id": "r1",
                "messages": [{"role": "user", "content": "ORIGINAL"}],
                "active": "a",
                "seen": ["a"],
                "seg": 0,
                "done": False,
            }
        )
    )
    a = Agent(name="a", model="gpt-4o", client=_stub("resumed"))
    # The new input must be ignored — the run resumes from the saved (unfinished) checkpoint.
    result = run([a], "IGNORED-NEW-INPUT", checkpoint=str(ckpt))
    assert any(m.get("content") == "ORIGINAL" for m in result.messages)
    assert not any(m.get("content") == "IGNORED-NEW-INPUT" for m in result.messages)
    assert result.output == "resumed"
