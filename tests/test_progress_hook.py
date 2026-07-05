"""The live progress hook: ``run(..., on_step=cb)`` fires per Step as the run progresses,
matching the post-hoc ``Result.steps`` — for single- and multi-agent runs, and a raised callback
never breaks the run. All offline against stub clients."""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, run, tool
from cendor.sdk.result import Step


@tool
def get_weather(city: str) -> str:
    """Weather for a city."""
    return f"Sunny in {city}"


def _tool_then_answer(answer: str = "It's sunny in Paris.") -> object:
    """OpenAI-shaped stub: turn 1 calls get_weather, turn 2 answers. Fresh per call."""
    turns = iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="call_1",
                                    type="function",
                                    function=SimpleNamespace(
                                        name="get_weather", arguments='{"city": "Paris"}'
                                    ),
                                )
                            ],
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=answer, tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=2),
            ),
        ]
    )

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(turns)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_on_step_fires_live_in_order_and_matches_result():
    seen: list[Step] = []
    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], client=_tool_then_answer())
    result = run(agent, "What's the weather in Paris?", on_step=seen.append)

    # Every step was delivered live, as a Step, in order: model → tool → model.
    assert all(isinstance(s, Step) for s in seen)
    assert [(s.kind, s.name) for s in seen] == [
        ("llm", "gpt-4o"),
        ("tool", "get_weather"),
        ("llm", "gpt-4o"),
    ]
    # …and the live stream is exactly what the finished Result reports.
    assert [(s.kind, s.name) for s in seen] == [(s.kind, s.name) for s in result.steps]


def test_on_step_callback_error_never_breaks_the_run():
    def boom(_step: Step) -> None:
        raise ValueError("a bad progress hook must not break the run")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], client=_tool_then_answer())
    result = run(agent, "weather?", on_step=boom)  # must not raise
    assert result.output == "It's sunny in Paris."


def test_on_step_fires_across_a_multi_agent_handoff():
    from cendor.sdk import handoff

    # planner hands off to writer; each agent sees its own stub. The hook must fire for both
    # segments (steps carry each agent's name), live.
    planner = Agent(
        name="planner",
        model="gpt-4o",
        handoffs=[handoff("writer")],
        client=_handoff_then(_transfer_call("writer")),
    )
    writer = Agent(name="writer", model="gpt-4o", client=_answer_only("Done."))

    seen: list[Step] = []
    result = run([planner, writer], "plan and write", on_step=seen.append)

    agents_seen = {s.agent for s in seen}
    assert "planner" in agents_seen and "writer" in agents_seen
    assert [(s.agent, s.kind) for s in seen] == [(s.agent, s.kind) for s in result.steps]


# --- helpers for the multi-agent case ---------------------------------------------------------


def _transfer_call(target: str) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="t1",
                            type="function",
                            function=SimpleNamespace(name=f"transfer_to_{target}", arguments="{}"),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2),
    )


def _handoff_then(transfer: object) -> object:
    turns = iter([transfer])

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(turns)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def _answer_only(answer: str) -> object:
    turns = iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=answer, tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2),
            )
        ]
    )

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(turns)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
