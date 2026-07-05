"""MCP prompts (#13): list prompts and render one to canonical messages, via a fake session."""

from __future__ import annotations

from types import SimpleNamespace

from cendor.sdk import get_mcp_prompt, load_mcp_prompts


class _FakeSession:
    async def list_prompts(self):
        return SimpleNamespace(
            prompts=[SimpleNamespace(name="greet", description="Greet someone", arguments=[])]
        )

    async def get_prompt(self, name, arguments):
        who = arguments.get("who", "")
        return SimpleNamespace(
            messages=[
                SimpleNamespace(role="user", content=SimpleNamespace(type="text", text=f"Hi {who}"))
            ]
        )


async def test_load_mcp_prompts_and_render():
    session = _FakeSession()
    prompts = await load_mcp_prompts(session)
    assert "greet" in prompts
    assert prompts["greet"]["description"] == "Greet someone"

    messages = await get_mcp_prompt(session, "greet", {"who": "Al"})
    assert messages[0]["role"] == "user"
    assert "Hi Al" in messages[0]["content"]
