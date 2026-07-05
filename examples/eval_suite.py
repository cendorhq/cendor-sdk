"""A governed eval suite that gates behaviour AND spend — OFFLINE, deterministic, free.

Record a trajectory once (here with a stub client), then replay it as a regression test that asserts
the output, the tool sequence, and a cost ceiling. In CI this catches a cost regression or a
tool-sequence change without ever hitting the network.

Run it:  uv run python examples/eval_suite.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from cendor.core import instrument

from cendor import cassette
from cendor.sdk import Agent, EvalCase, evaluate, run, tool


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"


def _tool_turn():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="c1",
                            type="function",
                            function=SimpleNamespace(
                                name="get_weather", arguments='{"city": "Paris"}'
                            ),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=12),
    )


def _final_turn():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="It's sunny in Paris.", tool_calls=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=9),
    )


def _stub(responses) -> object:
    it = iter(responses)

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def main() -> None:
    path = str(Path(tempfile.gettempdir()) / "cendor_sdk_eval_weather.json")

    # 1) Record the trajectory once (offline stub).
    rec_agent = Agent(
        name="assistant",
        model="gpt-4o",
        tools=[get_weather],
        instructions="Use tools.",
        client=_stub([_tool_turn(), _final_turn()]),
    )
    with cassette.using(path, mode="record"):
        run(rec_agent, "What's the weather in Paris?")

    # 2) Evaluate against the recorded cassette (no network; cost/tokens are real on replay).
    eval_agent = Agent(
        name="assistant",
        model="gpt-4o",
        tools=[get_weather],
        instructions="Use tools.",
        client=_stub([]),  # never called on replay — the cassette short-circuits
    )
    report = evaluate(
        eval_agent,
        [
            EvalCase(
                name="happy-path",
                input="What's the weather in Paris?",
                cassette=path,
                expect_output="It's sunny in Paris.",
                expect_tools=["get_weather"],
                max_usd=1.0,
            ),
            EvalCase(
                name="cost-ceiling (deliberately too low)",
                input="What's the weather in Paris?",
                cassette=path,
                max_usd=0.00000001,
            ),
        ],
    )

    print(report)
    print("\nsuite ok:", report.ok)


if __name__ == "__main__":
    main()
