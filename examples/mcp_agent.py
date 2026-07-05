"""An agent that consumes an MCP tool, emits an OTel span tree, and is exposed over A2A — OFFLINE.

Everything real except the model call (a stub) and the MCP server (a fake, duck-typed session).
In production: connect a real MCP server via the ``[mcp]`` extra, drop ``client=``, set your keys.

Run it:  uv run python examples/mcp_agent.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cendor.sdk import A2AClient, A2AServer, Agent, load_mcp_tools, run, span_tree, tool


class FakeMCPSession:
    """Stands in for mcp.ClientSession (async list_tools / call_tool)."""

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="search_kb",
                    description="Search the knowledge base.",
                    inputSchema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                )
            ]
        )

    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="KB: refunds take 5 days")]
        )


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls" if tool_calls else "stop",
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=45, completion_tokens=12),
    )


def _stub(responses, *, async_: bool = False) -> object:
    """A raw OpenAI-shaped stub. ``async_=True`` gives an async ``create`` for ``run.aio``."""
    it = iter(responses)

    if async_:

        class Completions:
            async def create(self, **kwargs: object) -> object:
                return next(it)

    else:

        class Completions:
            def create(self, **kwargs: object) -> object:
                return next(it)

    return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


async def mcp_demo() -> None:
    """Consume an MCP tool inside a governed async run, then emit a full-run OTel span tree."""
    tools = await load_mcp_tools(FakeMCPSession())
    tool_call = [
        SimpleNamespace(
            id="c1",
            type="function",
            function=SimpleNamespace(name="search_kb", arguments='{"query": "refunds"}'),
        )
    ]
    client = _stub(
        [_msg(tool_calls=tool_call), _msg(content="Refunds take 5 business days.")], async_=True
    )
    agent = Agent(
        name="support", model="gpt-4o", tools=tools, instructions="Use the KB.", client=client
    )

    result = await run.aio(agent, "How long do refunds take?")
    print("MCP-backed answer:", result.output)
    print("tool steps       :", [s.name for s in result.tool_steps])
    print("otel span tree   :", span_tree(result))  # no-op unless OpenTelemetry is configured


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"


def a2a_demo() -> None:
    """Expose a governed agent over A2A and call it in-process."""
    client = _stub([_msg(content="It's sunny in Paris.")])
    agent = Agent(
        name="greeter", model="gpt-4o", tools=[get_weather], instructions="Greet.", client=client
    )
    a2a = A2AClient(A2AServer(agent))
    card = a2a.card()
    print("A2A card         :", card["name"], "| skills:", [s["name"] for s in card["skills"]])
    print("A2A reply        :", a2a.send("weather in Paris?"))


if __name__ == "__main__":
    asyncio.run(mcp_demo())
    a2a_demo()
