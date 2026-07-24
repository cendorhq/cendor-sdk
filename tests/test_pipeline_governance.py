"""G2 (F2): pipeline-shape governance parity with the TS observed behavior (Phase-0 truth table).

``sequential`` / ``parallel`` / ``parallel_async`` gain the *honored* RunOptions surface
(``retry``, ``on_step``, ``guardrails`` + ``Result.guardrail_decisions`` collection); ``supervisor``
delegates to ``run_agents`` with the full surface (``session`` / ``checkpoint`` / ``on_step`` /
``guardrails`` / ``retry``). Per the truth table, ``session`` / ``checkpoint`` are deliberately NOT
accepted by the pipe shapes (TS ignores them there) — that residual is documented, not replicated.
Offline via stub clients (mirrors ``test_gaps_followup.py``)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cendor.core import instrument

from cendor.sdk import (
    Agent,
    GuardrailTripped,
    parallel,
    parallel_async,
    rules,
    sequential,
    supervisor,
)


def _answer(content: str, finish: str = "stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish, message=SimpleNamespace(content=content, tool_calls=None)
            )
        ],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
    )


def _client(text: str):
    return instrument(
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: _answer(text)))
        )
    )


def _aclient(text: str):
    async def create(**k):
        return _answer(text)

    return instrument(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )


def _raising_client():
    def create(**kwargs):
        raise AssertionError("the model must not be called on a done-resume")

    return instrument(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )


class _Sess:
    def __init__(self, id: str) -> None:
        self.id = id
        self._m: list[dict] = []

    def snapshot(self) -> list[dict]:
        return list(self._m)

    def replace(self, msgs: list[dict]) -> None:
        self._m = list(msgs)


# --------------------------------------------------------------------------- sequential


def test_sequential_collects_guardrail_decisions():
    first = Agent(name="first", model="gpt-4o", client=_client("a draft"))
    second = Agent(name="second", model="gpt-4o", client=_client("the final brief"))
    result = sequential(
        [first, second],
        "watchword please",
        guardrails=[rules.keyword_deny(["watchword"], action="flag")],
    )
    assert result.output == "the final brief"
    # BEFORE this wave result.guardrail_decisions was always [] for pipe shapes.
    assert any(d.action == "flag" for d in result.guardrail_decisions)


def test_sequential_guardrail_block_raises_pre_spend():
    first = Agent(name="first", model="gpt-4o", client=_raising_client())
    with pytest.raises(GuardrailTripped):
        sequential(
            [first],
            "a forbidden request",
            guardrails=[rules.keyword_deny(["forbidden"], action="block")],
        )


def test_sequential_on_step_fires():
    steps: list = []
    first = Agent(name="first", model="gpt-4o", client=_client("a"))
    second = Agent(name="second", model="gpt-4o", client=_client("b"))
    result = sequential([first, second], "start", on_step=steps.append)
    assert len(steps) == len(result.steps) >= 2  # one llm step per agent, streamed to the hook


# ------------------------------------------------------------------- parallel / parallel_async


def test_parallel_collects_guardrail_decisions():
    a = Agent(name="a", model="gpt-4o", client=_client("out-a"))
    b = Agent(name="b", model="gpt-4o", client=_client("out-b"))
    result = parallel(
        [a, b], "watchword here", guardrails=[rules.keyword_deny(["watchword"], action="flag")]
    )
    assert set(result.output.keys()) == {"a", "b"}
    # both agents see the same flagged input → two flag decisions collected
    assert sum(1 for d in result.guardrail_decisions if d.action == "flag") == 2


async def test_parallel_async_collects_guardrail_decisions():
    a = Agent(name="a", model="gpt-4o", client=_aclient("out-1"))
    b = Agent(name="b", model="gpt-4o", client=_aclient("out-2"))
    result = await parallel_async(
        [a, b], "watchword here", guardrails=[rules.keyword_deny(["watchword"], action="flag")]
    )
    assert set(result.output.keys()) == {"a", "b"}
    assert sum(1 for d in result.guardrail_decisions if d.action == "flag") == 2


# ------------------------------------------------------------------- supervisor (full surface)


def test_supervisor_stamps_conversation_id_and_persists_session():
    sess = _Sess("router-9")
    coordinator = Agent(name="coordinator", model="gpt-4o", client=_client("routed answer"))
    sub = Agent(name="sub", model="gpt-4o", client=_client("unused"))
    result = supervisor(coordinator, [sub], "route this", session=sess)
    assert result.conversation_id == "router-9"  # session honored (via run_agents)
    assert len(sess.snapshot()) > 0  # session.replace persisted the conversation


def test_supervisor_collects_guardrail_decisions():
    coordinator = Agent(name="coordinator", model="gpt-4o", client=_client("done"))
    sub = Agent(name="sub", model="gpt-4o", client=_client("unused"))
    result = supervisor(
        coordinator,
        [sub],
        "watchword",
        guardrails=[rules.keyword_deny(["watchword"], action="flag")],
    )
    assert any(d.action == "flag" for d in result.guardrail_decisions)


def test_supervisor_checkpoint_done_resume(tmp_path):
    ckpt = tmp_path / "team.json"
    coordinator = Agent(name="coordinator", model="gpt-4o", client=_client("supervised out"))
    sub = Agent(name="sub", model="gpt-4o", client=_client("unused"))
    result = supervisor(coordinator, [sub], "go", checkpoint=str(ckpt))
    assert result.output == "supervised out"
    state = json.loads(ckpt.read_text())
    assert state["done"] is True

    # A done-resume must not call any model.
    coordinator2 = Agent(name="coordinator", model="gpt-4o", client=_raising_client())
    sub2 = Agent(name="sub", model="gpt-4o", client=_raising_client())
    resumed = supervisor(coordinator2, [sub2], "go", checkpoint=str(ckpt))
    assert resumed.output == "supervised out"
