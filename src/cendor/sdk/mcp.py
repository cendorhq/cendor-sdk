"""MCP client: consume Model Context Protocol tools as ``cendor.sdk`` ``Tool``s (plan §7 Phase 3).

The integration is duck-typed against an MCP *client session* — any object exposing async
``list_tools()`` and ``call_tool(name, arguments)`` (the shape of ``mcp.ClientSession``). That keeps
the ``[mcp]`` package an optional extra and makes the wiring testable offline with a fake session.
Each MCP tool becomes a governed ``Tool`` (its schema comes from the server), so it flows through
the same loop, bus, audit, and budget as any other tool. MCP is async, so the wrapped tools are
async — use them with ``run.aio(...)``.

Connect to a real server with the ``[mcp]`` extra:

    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        tools = await load_mcp_tools(session)
        agent = Agent(name="a", model="gpt-4o", tools=tools)
        result = await run.aio(agent, "...")
"""

from __future__ import annotations

from typing import Any

from .providers import _get
from .tools import Tool


def _mcp_result_text(result: Any) -> str:
    """Extract text from an MCP ``CallToolResult`` (``.content`` is a list of content parts)."""
    content = _get(result, "content")
    if content is None:
        return _get(result, "text") or str(result)
    parts: list[str] = []
    for item in content if isinstance(content, list) else [content]:
        text = _get(item, "text")
        if text is not None:
            parts.append(str(text))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts) if parts else str(content)


def _wrap_mcp_tool(session: Any, spec: Any) -> Tool:
    name = _get(spec, "name") or "tool"
    description = _get(spec, "description") or ""
    schema = (
        _get(spec, "inputSchema")
        or _get(spec, "input_schema")
        or {
            "type": "object",
            "properties": {},
        }
    )

    async def call(**kwargs: Any) -> str:
        result = await session.call_tool(name, kwargs)
        return _mcp_result_text(result)

    return Tool(name=name, description=description, parameters=schema, func=call, is_async=True)


async def load_mcp_tools(session: Any) -> list[Tool]:
    """List an MCP session's tools and return them as governed ``Tool``s.

    ``session`` is any object with async ``list_tools()`` (returning something with a ``.tools``
    list, or a list directly) and ``call_tool(name, arguments)``.
    """
    listing = await session.list_tools()
    specs = _get(listing, "tools")
    if specs is None:
        specs = listing if isinstance(listing, list) else []
    return [_wrap_mcp_tool(session, spec) for spec in specs]


async def load_mcp_prompts(session: Any) -> dict[str, Any]:
    """List an MCP session's prompt templates as ``{name: {description, arguments}}`` (empty if the
    server exposes none). Fetch a rendered prompt with :func:`get_mcp_prompt`."""
    if not hasattr(session, "list_prompts"):
        return {}
    listing = await session.list_prompts()
    prompts = _get(listing, "prompts") or (listing if isinstance(listing, list) else [])
    out: dict[str, Any] = {}
    for p in prompts:
        name = _get(p, "name")
        if name is None:
            continue
        out[str(name)] = {
            "description": _get(p, "description") or "",
            "arguments": _get(p, "arguments") or [],
        }
    return out


async def get_mcp_prompt(session: Any, name: str, arguments: dict | None = None) -> list[dict]:
    """Render an MCP prompt to canonical (OpenAI-shape) messages you can pass straight to ``run``.

    Maps each MCP prompt message to ``{"role", "content"}`` (roles other than ``assistant`` become
    ``user``). Returns ``[]`` if the server has no ``get_prompt``.
    """
    if not hasattr(session, "get_prompt"):
        return []
    result = await session.get_prompt(name, arguments or {})
    out: list[dict] = []
    for m in _get(result, "messages") or []:
        role = _get(m, "role") or "user"
        out.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": _mcp_result_text(_get(m, "content")),
            }
        )
    return out


async def load_mcp_resources(session: Any) -> dict[str, Any]:
    """Read an MCP session's resources into ``{uri: contents}`` (best-effort; empty if absent)."""
    if not hasattr(session, "list_resources"):
        return {}
    listing = await session.list_resources()
    resources = _get(listing, "resources") or (listing if isinstance(listing, list) else [])
    out: dict[str, Any] = {}
    for res in resources:
        uri = _get(res, "uri")
        if uri is None:
            continue
        try:
            contents = await session.read_resource(uri)
            out[str(uri)] = _mcp_result_text(contents)
        except Exception:  # noqa: BLE001 - a single unreadable resource must not abort the batch
            continue
    return out
