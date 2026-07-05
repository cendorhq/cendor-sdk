"""Multi-agent streaming: ``run.stream([...])`` / ``run.astream([...])`` stream a handoff run —
events from each active agent, switching on a ``transfer_to_<peer>`` call, with one terminal
``RunComplete`` carrying the aggregate Result. Previously this raised ``TypeError``. Offline via
stub OpenAI-shaped stream clients."""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, RunComplete, TextDelta, ToolCallEvent, handoff, run

_USAGE = SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7)


def _text_chunk(content=None, finish=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish
            )
        ],
        usage=usage,
    )


def _tool_chunk(idx, *, id=None, name=None, args=None, finish=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=idx, id=id, function=SimpleNamespace(name=name, arguments=args)
                        )
                    ],
                ),
                finish_reason=finish,
            )
        ],
        usage=usage,
    )


def _client(turns):
    it = iter(turns)

    class Completions:
        def create(self, **kwargs):
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def _planner() -> Agent:
    # one turn: hand off to the writer
    return Agent(
        name="planner",
        model="gpt-4o",
        handoffs=[handoff("writer")],
        client=_client(
            [
                iter(
                    [
                        _tool_chunk(0, id="t1", name="transfer_to_writer", args="{}"),
                        _text_chunk(None, finish="tool_calls", usage=_USAGE),
                    ]
                )
            ]
        ),
    )


def _writer() -> Agent:
    return Agent(
        name="writer",
        model="gpt-4o",
        client=_client(
            [
                iter(
                    [
                        _text_chunk("All "),
                        _text_chunk("done."),
                        _text_chunk(None, finish="stop", usage=_USAGE),
                    ]
                )
            ]
        ),
    )


def _assert_handoff_stream(events: list) -> None:
    assert any(isinstance(e, ToolCallEvent) and e.name == "transfer_to_writer" for e in events)
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "All done."  # the writer's streamed answer
    done = events[-1]
    assert isinstance(done, RunComplete)
    assert done.result.output == "All done."
    assert done.result.agents == ["planner", "writer"]  # both segments in the aggregate
    assert {s.agent for s in done.result.steps} == {"planner", "writer"}


def test_stream_multi_agent_handoff():
    # Previously raised TypeError; now streams the handoff run to completion.
    events = list(run.stream([_planner(), _writer()], "plan then write"))
    _assert_handoff_stream(events)


def test_stream_single_agent_still_works_via_list_dispatch():
    # A one-agent list still streams (no handoff, one segment) — the dispatch is uniform.
    writer = _writer()
    events = list(run.stream([writer], "just write"))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "All done."
    assert isinstance(events[-1], RunComplete)
    assert events[-1].result.agents == ["writer"]
