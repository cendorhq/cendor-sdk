"""Loop extras: max_turns incompleteness signal (#12) and parallel async tool execution (#9)."""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, run, tool


@tool
def noop() -> str:
    """A no-op tool."""
    return "ok"


def _tool_call(id):
    return SimpleNamespace(
        id=id, type="function", function=SimpleNamespace(name="noop", arguments="{}")
    )


def _sync_client_always_tool():
    def resp():
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(content=None, tool_calls=[_tool_call("c1")]),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    class Completions:
        def create(self, **kwargs):
            return resp()

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_incomplete_flag_on_max_turns():
    """A run that never finalizes (always calls a tool) hits max_turns → result.incomplete."""
    agent = Agent(name="a", model="gpt-4o", tools=[noop], client=_sync_client_always_tool())
    result = run(agent, "go", max_turns=2)
    assert result.incomplete is True
    assert result.output is None


def _async_client_two_tools_then_answer():
    turns = iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None, tool_calls=[_tool_call("c1"), _tool_call("c2")]
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="done", tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            ),
        ]
    )

    class Completions:
        async def create(self, **kwargs):
            return next(turns)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


async def test_parallel_async_tools_all_execute():
    """Two tool calls in one turn run concurrently (via gather) and both results are recorded."""
    client = _async_client_two_tools_then_answer()
    agent = Agent(name="a", model="gpt-4o", tools=[noop], client=client)
    result = await run.aio(agent, "go")
    assert result.output == "done"
    assert [s.name for s in result.tool_steps] == ["noop", "noop"]
    assert result.incomplete is False
