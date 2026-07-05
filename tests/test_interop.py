"""Ecosystem & interop: MCP tools, A2A serve/call, Foundry adapter, OTel span tree, HITL (§7 P3).

All offline: the MCP session is a fake (duck-typed), A2A/Foundry run in-process, and OTel uses the
in-memory exporter.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import respx
from cendor.acttrace import verify

from cendor.sdk import (
    A2AClient,
    A2AServer,
    Agent,
    AuditLog,
    FoundryAdapter,
    load_mcp_tools,
    run,
    span_tree,
    tool,
)
from cendor.sdk.hitl import require_approval


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"


# --------------------------------------------------------------------------- MCP


class FakeMCPSession:
    """A duck-typed stand-in for mcp.ClientSession."""

    def __init__(self) -> None:
        self.calls: list = []

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
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=f"KB result for {arguments['query']}")]
        )


async def test_mcp_tool_round_trip(build):
    session = FakeMCPSession()
    tools = await load_mcp_tools(session)
    assert tools[0].name == "search_kb"
    assert tools[0].parameters["required"] == ["query"]

    agent = Agent(name="a", model="gpt-4o", tools=tools, instructions="Use tools.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[build.openai_tool_call("search_kb", {"query": "refunds"})],
                    )
                ),
                build.resp(build.openai_chat("Refunds take 5 days.")),
            ]
        )
        result = await run.aio(agent, "How do refunds work?")

    assert result.output == "Refunds take 5 days."
    assert session.calls == [("search_kb", {"query": "refunds"})]
    assert [s.name for s in result.tool_steps] == ["search_kb"]
    assert result.tool_steps[0].call.result == "KB result for refunds"


# --------------------------------------------------------------------------- A2A


def test_a2a_serve_and_call_in_proc(build):
    agent = Agent(name="greeter", model="gpt-4o", tools=[get_weather], instructions="Greet.")
    client = A2AClient(A2AServer(agent))

    card = client.card()
    assert card["name"] == "greeter"
    assert any(skill["name"] == "get_weather" for skill in card["skills"])

    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("Hello from the agent."))
        )
        reply = client.send("hi")
    assert reply == "Hello from the agent."

    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("Hi again.")))
        full = client.send_full("hi")
    assert full["role"] == "agent"
    assert full["metadata"]["trace_id"]
    assert Decimal(full["metadata"]["cost_usd"]) >= 0


# --------------------------------------------------------------------------- Foundry / Copilot


def test_foundry_custom_engine_adapter(build):
    agent = Agent(name="assistant", model="gpt-4o", instructions="Help.")
    adapter = FoundryAdapter(agent)
    assert adapter.manifest()["type"] == "custom-engine"

    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=build.resp(build.openai_chat("Sure, I can help."))
        )
        activity = {
            "type": "message",
            "text": "help me",
            "id": "a1",
            "from": {"id": "user"},
            "conversation": {"id": "c1"},
        }
        reply = adapter.on_activity(activity)

    assert reply is not None
    assert reply["type"] == "message"
    assert reply["text"] == "Sure, I can help."
    assert reply["channelData"]["cendor"]["trace_id"]
    # a non-message activity is acked with None
    assert adapter.on_activity({"type": "conversationUpdate"}) is None


# --------------------------------------------------------------------------- OTel span tree


def test_otel_span_tree(build):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="Use tools.")
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
        result = run(agent, "weather in Paris?")

    assert span_tree(result, tracer=tracer) is True
    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "agent.run" in names
    assert any(n.startswith("chat ") for n in names)
    assert any(n.startswith("execute_tool ") for n in names)

    # correlated tree: root has no parent; child spans do
    root = next(s for s in spans if s.name == "agent.run")
    assert root.parent is None
    chat = next(s for s in spans if s.name.startswith("chat "))
    assert chat.parent is not None
    assert chat.attributes["gen_ai.request.model"] == "gpt-4o"
    assert chat.attributes["gen_ai.system"] == "openai"


def test_span_tree_noop_without_otel(build):
    """span_tree never raises; returns True when OTel is present (as in dev), else False."""
    agent = Agent(name="a", model="gpt-4o", instructions="hi")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("hi")))
        result = run(agent, "hi")
    assert span_tree(result) is True


# --------------------------------------------------------------------------- HITL

_refunds: list = []


@tool
def issue_refund(order_id: int, amount: int) -> str:
    """Issue a refund for an order."""
    _refunds.append((order_id, amount))
    return f"Refunded ${amount} for order {order_id}."


def test_hitl_approval_recorded_in_audit_chain(build, tmp_path):
    _refunds.clear()
    approvals: list = []

    def approver(name, args):
        approvals.append((name, args))
        return True, "within policy"

    guarded = require_approval(issue_refund, approver=approver, reviewer="ops@bank")
    agent = Agent(name="support", model="gpt-4o", tools=[guarded], instructions="Use tools.")
    log = AuditLog(system="support", risk_tier="high", path=str(tmp_path / "audit.jsonl"))
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[
                            build.openai_tool_call("issue_refund", {"order_id": 42, "amount": 50})
                        ],
                    )
                ),
                build.resp(build.openai_chat("Your refund is on its way.")),
            ]
        )
        result = run(agent, "Refund order 42 for $50", audit=log)
    log.detach()

    assert approvals == [("issue_refund", {"order_id": 42, "amount": 50})]
    assert _refunds == [(42, 50)]  # approved -> the real tool ran
    assert result.output == "Your refund is on its way."

    oversight = [e for e in log.entries if e.type == "human_oversight"]
    assert len(oversight) == 1
    assert oversight[0].payload["action"] == "approved"
    assert oversight[0].payload["reviewer"] == "ops@bank"
    ok, detail = verify(str(tmp_path / "audit.jsonl"))
    assert ok, detail


def test_hitl_rejection_blocks_the_tool(build, tmp_path):
    _refunds.clear()
    guarded = require_approval(issue_refund, approver=lambda n, a: (False, "amount too large"))
    agent = Agent(name="support", model="gpt-4o", tools=[guarded], instructions="Use tools.")
    log = AuditLog(system="support", path=str(tmp_path / "audit.jsonl"))
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(
                    build.openai_chat(
                        None,
                        finish="tool_calls",
                        tool_calls=[
                            build.openai_tool_call("issue_refund", {"order_id": 7, "amount": 9999})
                        ],
                    )
                ),
                build.resp(build.openai_chat("I couldn't process that refund.")),
            ]
        )
        result = run(agent, "Refund order 7 for $9999", audit=log)
    log.detach()

    assert _refunds == []  # rejected -> the real tool never ran
    assert "couldn't" in result.output
    oversight = [e for e in log.entries if e.type == "human_oversight"]
    assert len(oversight) == 1 and oversight[0].payload["action"] == "rejected"
