"""Tool schema generation from type hints + docstring, and tool execution emitting ToolCalls."""

from __future__ import annotations

from typing import Literal

from cendor.core import bus
from cendor.core.types import ToolCall

from cendor.sdk import Tool, tool


def test_schema_from_type_hints_and_docstring():
    @tool
    def search(query: str, top_k: int = 3, deep: bool = False) -> list[str]:
        """Search the knowledge base.

        Args:
            query: the search string.
            top_k: how many results.
        """
        return []

    assert isinstance(search, Tool)
    assert search.name == "search"
    assert search.description == "Search the knowledge base."
    props = search.parameters["properties"]
    assert props["query"] == {"type": "string", "description": "the search string."}
    assert props["top_k"]["type"] == "integer"
    assert props["deep"]["type"] == "boolean"
    # only params without defaults are required
    assert search.parameters["required"] == ["query"]


def test_schema_optionals_lists_and_literals():
    @tool
    def f(tags: list[str], mode: Literal["fast", "slow"] = "fast", note: str | None = None) -> str:
        """Do a thing."""
        return "ok"

    props = f.parameters["properties"]
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}
    assert props["mode"]["enum"] == ["fast", "slow"]
    assert props["mode"]["type"] == "string"
    # str | None resolves to the inner type; not required (has default)
    assert props["note"]["type"] == "string"
    assert f.parameters["required"] == ["tags"]


def test_named_tool_decorator():
    @tool(name="lookup")
    def f(x: int) -> int:
        """Look up."""
        return x

    assert f.name == "lookup"


def test_tool_execution_emits_toolcall():
    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    seen: list = []
    bus.subscribe(seen.append)
    try:
        result = add.invoke({"a": 2, "b": 3})
    finally:
        bus.unsubscribe(seen.append)
    assert result == 5
    calls = [e for e in seen if isinstance(e, ToolCall)]
    assert calls and calls[-1].name == "add"
    assert calls[-1].result == 5


async def test_async_tool_execution():
    @tool
    async def fetch(url: str) -> str:
        """Fetch a URL."""
        return f"body of {url}"

    assert fetch.is_async
    result = await fetch.ainvoke({"url": "http://x"})
    assert result == "body of http://x"
