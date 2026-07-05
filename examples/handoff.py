"""Handoff: a planner hands the conversation to a writer — running OFFLINE (stub client).

The planner calls a synthetic ``transfer_to_writer`` tool; control (and the whole conversation)
transfers to the writer, whose answer becomes the result. In production, drop ``client=`` and set
your API keys — handoff also works *across providers* (OpenAI planner -> Anthropic writer).

Run it:  uv run python examples/handoff.py
"""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, run


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls" if tool_calls else "stop",
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=40, completion_tokens=12),
    )


def _stub(responses) -> object:
    it = iter(responses)

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def main() -> None:
    transfer = [
        SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(
                name="transfer_to_writer", arguments='{"reason": "ready to write"}'
            ),
        )
    ]
    client = _stub([_msg(tool_calls=transfer), _msg(content="Here is the finished brief.")])

    writer = Agent(name="writer", model="gpt-4o", instructions="Write the brief.", client=client)
    planner = Agent(
        name="planner",
        model="gpt-4o",
        instructions="Plan, then hand off to the writer.",
        handoffs=["writer"],
        client=client,
    )

    result = run([planner, writer], "Research X and write a brief")

    print("output :", result.output)
    print("agents :", result.agents)
    print("trace  :", result.trace_id)
    print("steps  :", [(s.agent, s.kind, s.name) for s in result.steps])


if __name__ == "__main__":
    main()
