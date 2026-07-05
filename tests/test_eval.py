"""Governed eval / regression harness: cassette-backed replay gates behaviour + spend (plan §7 P4).

A recorded trajectory is replayed as a test; the suite catches a cost-ceiling regression and a
tool-sequence change — offline, deterministic, free.
"""

from __future__ import annotations

import pytest
import respx

from cendor import cassette
from cendor.sdk import Agent, EvalCase, evaluate, run, tool


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"


def _agent():
    return Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="Use tools.")


def _record_cassette(build, path):
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("get_weather", {"city": "Paris"})],
                    )
                ),
                build.resp(build.openai_chat("It's sunny in Paris.")),
            ]
        )
        with cassette.using(path, mode="record"):
            run(_agent(), "What's the weather in Paris?")


def test_eval_suite_gates_behaviour_and_cost(build, tmp_path):
    path = str(tmp_path / "weather.json")
    _record_cassette(build, path)

    cases = [
        EvalCase(
            name="happy-path",
            input="What's the weather in Paris?",
            cassette=path,
            expect_output="It's sunny in Paris.",
            expect_tools=["get_weather"],
            max_usd=1.0,
            max_tokens=100_000,
        ),
        EvalCase(
            name="cost-regression",  # ceiling absurdly low -> must fail
            input="What's the weather in Paris?",
            cassette=path,
            max_usd=0.00000001,
        ),
        EvalCase(
            name="tool-sequence-change",  # expects a different tool -> must fail
            input="What's the weather in Paris?",
            cassette=path,
            expect_tools=["search"],
        ),
    ]

    report = evaluate(_agent(), cases)

    # the happy path passes; the two regressions are caught
    assert report.results[0].passed
    assert report.results[0].tools == ["get_weather"]
    assert report.results[0].cost_usd > 0  # cost is real on replay (recorded usage re-emitted)

    cost_case = next(r for r in report.results if r.name == "cost-regression")
    assert not cost_case.passed and "cost" in cost_case.failures[0]

    tool_case = next(r for r in report.results if r.name == "tool-sequence-change")
    assert not tool_case.passed and "tool sequence" in tool_case.failures[0]

    assert len(report.failed) == 2
    assert not report.ok
    with pytest.raises(AssertionError):
        report.assert_ok()


def test_eval_all_pass(build, tmp_path):
    path = str(tmp_path / "weather.json")
    _record_cassette(build, path)
    report = evaluate(
        _agent(),
        [
            EvalCase(
                name="ok",
                input="What's the weather in Paris?",
                cassette=path,
                expect_contains="sunny",
                expect_tools=["get_weather"],
                max_usd=1.0,
            )
        ],
    )
    assert report.ok
    report.assert_ok()  # does not raise
