"""Streaming — run.stream yields text deltas, tool events, and a terminal RunComplete (P1-3).

Offline via stub OpenAI-shaped stream clients (no network); asserts text reassembly, tool-call
delta accumulation across fragments, and that the final Result matches a blocking run.
"""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import (
    Agent,
    RunComplete,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
    run,
    tool,
)


def _text_chunk(content=None, finish=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish
            )
        ],
        usage=usage,
    )


def _tool_chunk(idx, *, id=None, name=None, args=None, finish=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=idx,
                            id=id,
                            function=SimpleNamespace(name=name, arguments=args),
                        )
                    ],
                ),
                finish_reason=finish,
            )
        ],
        usage=usage_of(finish),
    )


def usage_of(finish):
    if finish:
        return SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7)
    return None


def _client(turns):
    it = iter(turns)

    class Completions:
        def create(self, **kwargs):
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_stream_text_and_complete():
    client = _client(
        [
            iter(
                [
                    _text_chunk("Hello "),
                    _text_chunk("world"),
                    _text_chunk(
                        None,
                        finish="stop",
                        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
                    ),
                ]
            )
        ]
    )
    agent = Agent(name="a", model="gpt-4o", instructions="x", client=client)
    events = list(run.stream(agent, "hi"))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "Hello world"
    done = events[-1]
    assert isinstance(done, RunComplete)
    assert done.result.output == "Hello world"
    assert done.result.trace_id  # correlated like a blocking run
    assert [s.name for s in done.result.llm_steps] == ["gpt-4o"]


def test_stream_tool_call_reassembled_across_fragments():
    @tool
    def get_weather(city: str) -> str:
        """weather"""
        return f"Sunny in {city}"

    client = _client(
        [
            # turn 1: a tool call streamed as argument fragments
            iter(
                [
                    _tool_chunk(0, id="call_1", name="get_weather", args='{"city":'),
                    _tool_chunk(0, args=' "Paris"}'),
                    _text_chunk(
                        None,
                        finish="tool_calls",
                        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
                    ),
                ]
            ),
            # turn 2: the streamed answer
            iter(
                [
                    _text_chunk("It's "),
                    _text_chunk("sunny."),
                    _text_chunk(
                        None,
                        finish="stop",
                        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3),
                    ),
                ]
            ),
        ]
    )
    agent = Agent(name="a", model="gpt-4o", tools=[get_weather], client=client)
    events = list(run.stream(agent, "weather in Paris?"))

    calls = [e for e in events if isinstance(e, ToolCallEvent)]
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert calls[0].name == "get_weather"
    assert calls[0].arguments == {"city": "Paris"}  # fragments reassembled + parsed
    assert "Sunny in Paris" in results[0].result
    done = events[-1]
    assert isinstance(done, RunComplete)
    assert done.result.output == "It's sunny."
    assert [s.name for s in done.result.tool_steps] == ["get_weather"]
