"""Record once, replay forever — cassette makes an agent run deterministic and offline (plan §10).

On replay there is no ``respx`` mock installed: if the run tried a real HTTP call it would fail, so
a passing replay proves the trajectory was served entirely from the cassette (via core's interceptor
seam), with the tool body skipped too."""

from __future__ import annotations

import respx

from cendor import cassette
from cendor.sdk import Agent, run, tool

_side_effects = {"tool_calls": 0}


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    _side_effects["tool_calls"] += 1
    return f"Sunny in {city}"


def _agent():
    return Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="Use tools.")


def test_record_then_replay_is_deterministic(build, tmp_path):
    path = str(tmp_path / "run.json")
    agent = _agent()

    # Record: real (mocked) calls run once; cassette captures the whole trajectory.
    _side_effects["tool_calls"] = 0
    with respx.mock as mock:
        route = mock.post(build.CHAT_URL).mock(
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
            recorded = run(agent, "What's the weather in Paris?")
    assert route.call_count == 2
    assert _side_effects["tool_calls"] == 1
    assert recorded.output == "It's sunny in Paris."

    # Replay: NO respx mock. Served from the cassette; tool body is skipped.
    _side_effects["tool_calls"] = 0
    with cassette.using(path, mode="replay"):
        replayed = run(agent, "What's the weather in Paris?")
    assert replayed.output == "It's sunny in Paris."
    assert [s.name for s in replayed.tool_steps] == ["get_weather"]
    assert _side_effects["tool_calls"] == 0  # the real tool never ran on replay
