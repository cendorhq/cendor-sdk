"""Opt-in incremental output checking on run.stream (Wave 4).

With `stream_check_window`, the output guardrails are evaluated on the buffered text every N chars,
so a block fires *earlier* in the stream (deltas already shown can't be unshown — this narrows the
window, it doesn't close it). Off by default = final-text only. Fake stream client, no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from cendor.core import instrument

from cendor.sdk import Agent, GuardrailTripped, TextDelta, rules, run


def _chunk(content=None, finish=None):
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2) if finish else None
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish
            )
        ],
        usage=usage,
    )


def _stream_client(chunks):
    class Completions:
        def create(self, **kwargs):
            return iter(chunks)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


_CHUNKS = [
    _chunk("a perfectly safe intro "),
    _chunk("now classified content appears"),
    _chunk("this trailing text should never stream"),
    _chunk(None, finish="stop"),
]


def _agent(client, **kw):
    return Agent(
        name="a",
        model="gpt-4o",
        instructions="x",
        client=client,
        guardrails=[rules.keyword_deny(["classified"], stage="output", action="block")],
        **kw,
    )


def test_stream_window_blocks_mid_stream():
    agent = _agent(_stream_client(_CHUNKS), stream_check_window=10)
    seen: list[str] = []
    with pytest.raises(GuardrailTripped):
        for ev in run.stream(agent, "go"):
            if isinstance(ev, TextDelta):
                seen.append(ev.text)
    joined = "".join(seen)
    assert "safe intro" in joined  # early deltas were shown
    assert "trailing text should never stream" not in joined  # blocked before the 3rd chunk


def test_stream_without_window_streams_all_then_blocks_post_hoc():
    agent = _agent(_stream_client(_CHUNKS))  # stream_check_window defaults to 0
    seen: list[str] = []
    with pytest.raises(GuardrailTripped):
        for ev in run.stream(agent, "go"):
            if isinstance(ev, TextDelta):
                seen.append(ev.text)
    joined = "".join(seen)
    # the whole thing streamed (post-hoc block) — the trailing chunk WAS shown
    assert "trailing text should never stream" in joined


# The async path (`run.astream` → `gate_stream_partial_async`) is a line-for-line mirror of the sync
# path exercised above; the async-streaming client shape is covered by the SDK's streaming tests.
