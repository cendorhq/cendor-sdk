"""Multi-agent runs are checkpointed & resumable (P1-7). Offline via a stub client."""

from __future__ import annotations

import json
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, run


def _stub(answer: str, calls: dict | None = None):
    class Completions:
        def create(self, **kwargs):
            if calls is not None:
                calls["n"] = calls.get("n", 0) + 1
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


def _finished_team_ckpt(ckpt):
    """A finished (done) multi-agent checkpoint with a stored output."""
    ckpt.write_text(
        json.dumps(
            {
                "run_id": "r1",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "final answer"},
                ],
                "active": "a",
                "seen": ["a"],
                "seg": 1,
                "done": True,
                "output": "final answer",
            }
        )
    )


def test_multi_agent_finished_checkpoint_does_not_rerun(tmp_path):
    """Resuming a DONE multi-agent checkpoint returns the stored output — 0 model calls."""
    ckpt = tmp_path / "team.ckpt.json"
    _finished_team_ckpt(ckpt)
    calls: dict = {"n": 0}
    a = Agent(name="a", model="gpt-4o", client=_stub("RERAN", calls))
    result = run([a], "IGNORED", checkpoint=str(ckpt))
    assert result.output == "final answer"  # stored output, not a re-run
    assert calls["n"] == 0  # model was not called
    assert result.steps == []  # no bus events on a resume
    assert result.agents == ["a"]


async def test_multi_agent_finished_checkpoint_does_not_rerun_async(tmp_path):
    """Same short-circuit on the async multi-agent path."""
    ckpt = tmp_path / "team.ckpt.json"
    _finished_team_ckpt(ckpt)
    calls: dict = {"n": 0}
    a = Agent(name="a", model="gpt-4o", client=_stub("RERAN", calls))
    result = await run.aio([a], "IGNORED", checkpoint=str(ckpt))
    assert result.output == "final answer"
    assert calls["n"] == 0
    assert result.steps == []
