"""G1 (F1): ``run.astream`` streamed-async checkpointing. The async twin of the S13 sync tests in
``test_gaps_followup.py`` — ``run.astream(..., checkpoint=path)`` must persist the conversation per
turn and a finished checkpoint must done-resume without re-calling the model. Before the fix,
``astream`` accepted + documented ``checkpoint`` but never forwarded it to ``stream_agent_async`` /
``stream_agents_async`` — a silent no-op. Offline (stub clients), mirrors the S13 sync coverage.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, RunComplete, run


def _chunk(content=None, finish=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish
            )
        ],
        usage=usage,
    )


async def _stream_answer(text: str):
    """One streamed turn (async iterator): a text delta + a terminal usage chunk (OpenAI-shape)."""
    yield _chunk(text)
    yield _chunk(None, finish="stop", usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2))


def _astream_client(turns):
    """A stub OpenAI-shaped async client whose ``await create(stream=True)`` returns each queued
    async-iterable turn (the async path does ``stream = await create(...)`` then ``async for``)."""
    it = iter(turns)

    class Completions:
        async def create(self, **kwargs):
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def _raising_async_client():
    class Completions:
        async def create(self, **kwargs):
            raise AssertionError("the model must not be called on a done-resume (S13/G1)")

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


async def _collect(aiter):
    return [ev async for ev in aiter]


# --------------------------------------------------------------------------- single agent (G1)


async def test_astream_writes_a_finished_checkpoint(tmp_path):
    ckpt = tmp_path / "run.json"
    agent = Agent(name="a", model="gpt-4o", client=_astream_client([_stream_answer("hello")]))
    events = await _collect(run.astream(agent, "hi", checkpoint=str(ckpt)))
    done = events[-1]
    assert isinstance(done, RunComplete)
    assert done.result.output == "hello"
    # BEFORE the fix this file never existed — astream dropped checkpoint= on the floor.
    state = json.loads(ckpt.read_text())
    assert state["done"] is True and state["output"] == "hello"


async def test_astream_done_resume_replays_without_calling_the_model(tmp_path):
    ckpt = tmp_path / "run.json"
    agent = Agent(name="a", model="gpt-4o", client=_astream_client([_stream_answer("first")]))
    await _collect(run.astream(agent, "hi", checkpoint=str(ckpt)))  # complete + persist
    # Re-run with a client that raises if the model is called — done-resume must short-circuit.
    agent2 = Agent(name="a", model="gpt-4o", client=_raising_async_client())
    events = await _collect(run.astream(agent2, "hi", checkpoint=str(ckpt)))
    assert len(events) == 1 and isinstance(events[0], RunComplete)  # lone terminal event, no deltas
    assert events[0].result.output == "first"


async def test_astream_settled_resume_replays_without_calling_the_model(tmp_path):
    """A stream save lands the answering turn with done=False before the done save (the crash
    window) — that settled shape must short-circuit exactly like a done-resume: a lone RunComplete,
    no model call, no re-yielded deltas."""
    ckpt = tmp_path / "run.json"
    agent = Agent(name="a", model="gpt-4o", client=_astream_client([_stream_answer("first")]))
    await _collect(run.astream(agent, "hi", checkpoint=str(ckpt)))  # complete + persist
    state = json.loads(ckpt.read_text())
    state["done"] = False  # simulate the crash between the per-turn save and the done save
    ckpt.write_text(json.dumps(state))
    agent2 = Agent(name="a", model="gpt-4o", client=_raising_async_client())
    events = await _collect(run.astream(agent2, "hi", checkpoint=str(ckpt)))
    assert len(events) == 1 and isinstance(events[0], RunComplete)
    assert events[0].result.output == "first"


# --------------------------------------------------------------------------- team (G1, multi-agent)


async def test_astream_team_done_resume_replays_without_calling_the_model(tmp_path):
    ckpt = tmp_path / "team.json"
    a = Agent(name="a", model="gpt-4o", client=_astream_client([_stream_answer("team-out")]))
    b = Agent(name="b", model="gpt-4o", client=_astream_client([_stream_answer("unused")]))
    await _collect(run.astream([a, b], "go", checkpoint=str(ckpt)))
    state = json.loads(ckpt.read_text())
    assert state["done"] is True  # the team stream persisted a finished checkpoint
    # A done-resume of the team stream must not call any model.
    a2 = Agent(name="a", model="gpt-4o", client=_raising_async_client())
    b2 = Agent(name="b", model="gpt-4o", client=_raising_async_client())
    events = await _collect(run.astream([a2, b2], "go", checkpoint=str(ckpt)))
    assert len(events) == 1 and isinstance(events[0], RunComplete)
    assert events[0].result.output == "team-out"
