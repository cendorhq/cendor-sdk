"""GLR-9 (Bug B) red->green: the Python stream generators must NOT leak the run's scopes into the
consumer between deltas — a consumer's own instrumented call made between two streamed events is not
attributed to the run (no trace id, not a step). Plus GLR-12: ThinkingDelta streaming."""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import current_trace_id, instrument

from cendor.sdk import Agent, RunComplete, TextDelta, ThinkingDelta, run


def _text_chunk(content=None, finish=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish
            )
        ],
        usage=usage,
    )


def _reasoning_chunk(text):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=None, reasoning_content=text, tool_calls=None),
                finish_reason=None,
            )
        ],
        usage=None,
    )


_USAGE = SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7)


def _stream_client(chunks):
    it = iter([iter(chunks)])

    class Completions:
        def create(self, **kwargs):
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def _plain_client():
    """A non-streamed instrumented client for the consumer's own call between deltas."""

    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None))],
                usage=_USAGE,
            )

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_consumer_call_between_deltas_is_not_attributed_to_the_run():
    run_client = _stream_client(
        [_text_chunk("Hel"), _text_chunk("lo"), _text_chunk(None, finish="stop", usage=_USAGE)]
    )
    agent = Agent(name="a", model="gpt-4o", instructions="x", client=run_client)
    consumer = _plain_client()
    seen_trace = None
    done = None
    for ev in run.stream(agent, "hi"):
        if isinstance(ev, TextDelta) and seen_trace is None:
            # We are OUTSIDE the run's scopes here (GLR-9). The run's trace scope must not have
            # leaked into our context, and our own call must not join the run.
            seen_trace = current_trace_id()
            consumer.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": "x"}]
            )
        if isinstance(ev, RunComplete):
            done = ev
    assert seen_trace == ""  # RED before the fix: the run id leaked into the consumer's context
    assert done is not None
    # Only the run's own model call is a step — the consumer's call is not collected.
    assert len(done.result.llm_steps) == 1


def test_thinking_delta_streamed_separately_from_text():
    client = _stream_client(
        [
            _reasoning_chunk("let me "),
            _reasoning_chunk("think"),
            _text_chunk("The answer."),
            _text_chunk(None, finish="stop", usage=_USAGE),
        ]
    )
    agent = Agent(name="a", model="gpt-4o", instructions="x", client=client)
    events = list(run.stream(agent, "hi"))
    thinking = "".join(e.text for e in events if isinstance(e, ThinkingDelta))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert thinking == "let me think"  # GLR-12: reasoning surfaced separately
    assert text == "The answer."


def test_no_thinking_delta_without_reasoning():
    client = _stream_client([_text_chunk("hi"), _text_chunk(None, finish="stop", usage=_USAGE)])
    agent = Agent(name="a", model="gpt-4o", instructions="x", client=client)
    events = list(run.stream(agent, "hi"))
    assert not any(isinstance(e, ThinkingDelta) for e in events)
