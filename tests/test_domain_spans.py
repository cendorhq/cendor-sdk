"""E-wave SDK domain telemetry: RAG / memory / orchestration / checkpoints / tools / MCP spans.

The SDK emits ``cendor.sdk`` spans for its structural signals so a monitor (or any OTel backend)
renders them as first-class domains. Run-scoped signals ride ``cendor-core``'s bus and become child
spans under an active ``live_spans`` root; setup-time MCP lifecycle emits standalone spans. Offline
via respx; the OTel in-memory exporter captures spans (mirrors ``test_live_spans.py``).
"""

from __future__ import annotations

import asyncio

import pytest
import respx
from cendor.core import bus

from cendor.sdk import Agent, run, tool
from cendor.sdk import _telemetry as _tel
from cendor.sdk.otel import live_spans


def _mem_tracer():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


@pytest.fixture(autouse=True)
def _reset_domain_registries():
    """The tool-source registry + mcp-seen set are process-global (dev-tool convenience); clear
    them around each test so a tool name / server tagged in one test can't leak into the next."""
    _tel._TOOL_SOURCES.clear()
    _tel._MCP_SEEN.clear()
    yield
    _tel._TOOL_SOURCES.clear()
    _tel._MCP_SEEN.clear()


@tool
def get_weather(city: str) -> str:
    """Weather for a city."""
    return f"Sunny in {city}"


def _by_name(spans, name):
    return [s for s in spans if s.name == name]


# --------------------------------------------------------------------------- memory


def test_memory_load_and_save_spans(build):
    from cendor.sdk import Session

    tracer, exporter = _mem_tracer()
    mem = Session(id="chat-7")
    mem.add({"role": "user", "content": "earlier"})
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("Hi again."))])
        with live_spans(tracer=tracer):
            run(agent, "hello", session=mem)

    spans = exporter.get_finished_spans()
    load = _by_name(spans, "memory.load")
    save = _by_name(spans, "memory.save")
    assert load and save, [s.name for s in spans]
    assert load[0].attributes["cendor.memory.op"] == "load"
    assert load[0].attributes["cendor.memory.session_id"] == "chat-7"
    assert load[0].attributes["gen_ai.conversation.id"] == "chat-7"
    assert load[0].attributes["cendor.sdk.kind"] == "memory.load"
    # load saw the 1 pre-run turn; save saw the fuller written-back conversation.
    assert load[0].attributes["cendor.memory.turns"] == 1
    assert save[0].attributes["cendor.memory.turns"] >= load[0].attributes["cendor.memory.turns"]
    assert save[0].parent is not None  # nested under agent.run


# --------------------------------------------------------------------------- checkpoints


def test_checkpoint_save_then_resume_spans(build, tmp_path):
    path = str(tmp_path / "run.json")
    agent = Agent(name="assistant", model="gpt-4o")

    # First run: writes a finished checkpoint → checkpoint.save spans.
    tracer, exporter = _mem_tracer()
    with respx.mock:
        respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("Done."))])
        with live_spans(tracer=tracer):
            run(agent, "do it", checkpoint=path)
    saves = _by_name(exporter.get_finished_spans(), "checkpoint.save")
    assert saves, [s.name for s in exporter.get_finished_spans()]
    assert saves[-1].attributes["cendor.checkpoint.op"] == "save"
    assert saves[-1].attributes["cendor.checkpoint.done"] is True

    # Second run of the SAME call: the finished checkpoint resumes → checkpoint.resume, no model.
    tracer2, exporter2 = _mem_tracer()
    with live_spans(tracer=tracer2):
        run(agent, "do it", checkpoint=path)  # no respx mock — must not touch the model
    resumes = _by_name(exporter2.get_finished_spans(), "checkpoint.resume")
    assert resumes, [s.name for s in exporter2.get_finished_spans()]
    assert resumes[0].attributes["cendor.checkpoint.op"] == "resume"
    assert resumes[0].attributes["cendor.checkpoint.done"] is True


# --------------------------------------------------------------------------- tools (source/outcome)


def test_tool_span_carries_source_and_outcome(build):
    tracer, exporter = _mem_tracer()
    # Pretend get_weather came from an MCP server so its span is attributed.
    _tel.register_tool_source("get_weather", "mcp", server="weather-mcp", transport="stdio")
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
                build.resp(build.openai_chat("Sunny.")),
            ]
        )
        with live_spans(tracer=tracer):
            run(agent, "weather?")

    tool_span = next(s for s in exporter.get_finished_spans() if s.name.startswith("execute_tool "))
    assert tool_span.attributes["cendor.tool.source"] == "mcp"
    assert tool_span.attributes["cendor.tool.mcp.server"] == "weather-mcp"
    assert tool_span.attributes["cendor.tool.mcp.transport"] == "stdio"
    assert tool_span.attributes["cendor.tool.outcome"] == "ok"


def test_blocked_tool_emits_blocked_span():
    """A ``tool_call`` guardrail block produces no ToolCall on the bus, so the SDK emits a
    ``ToolGate`` that renders an ``execute_tool`` span with ``outcome="blocked"``."""
    tracer, exporter = _mem_tracer()
    with live_spans(tracer=tracer):
        bus.emit(
            _tel.ToolGate(
                name="delete_everything",
                blocked_by="no-destructive-ops",
                trace_id="run-abc",
                agent="assistant",
            )
        )
    blocked = next(
        s for s in exporter.get_finished_spans() if s.name == "execute_tool delete_everything"
    )
    assert blocked.attributes["cendor.tool.outcome"] == "blocked"
    assert blocked.attributes["cendor.tool.blocked_by"] == "no-destructive-ops"
    assert blocked.attributes["cendor.tool.source"] == "local"
    assert blocked.attributes["gen_ai.agent.name"] == "assistant"
    assert blocked.attributes["cendor.trace_id"] == "run-abc"


# --------------------------------------------------------------------------- orchestration handoff


def test_orchestration_handoff_span():
    tracer, exporter = _mem_tracer()
    with live_spans(tracer=tracer):
        bus.emit(
            _tel.OrchestrationEdge(
                from_agent="planner",
                to_agent="writer",
                segment=0,
                transfer_tool="transfer_to_writer",
                trace_id="run-xyz",
            )
        )
    edge = next(s for s in exporter.get_finished_spans() if s.name == "orchestration.handoff")
    assert edge.attributes["cendor.orch.from_agent"] == "planner"
    assert edge.attributes["cendor.orch.to_agent"] == "writer"
    assert edge.attributes["cendor.orch.segment"] == 0
    assert edge.attributes["cendor.orch.transfer_tool"] == "transfer_to_writer"
    # Learned the run family from the edge → the root span is identified.
    root = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert root.attributes["cendor.run.id"] == "run-xyz"


# --------------------------------------------------------------------------- RAG


def test_rag_assemble_and_compress_spans():
    """RAG rides the library bus events (contextkit ``AssemblyReport`` / squeeze
    ``CompressionEvent``); the subscriber dispatches by class name, so faithful stand-ins with the
    real field shapes exercise the attribute extraction offline."""
    tracer, exporter = _mem_tracer()

    class _Decision:
        def __init__(self, action, before, after):
            self.action = action
            self.tokens_before = before
            self.tokens_after = after

    class AssemblyReport:  # noqa: N801 - name matched by the subscriber's dispatch
        budget = 1000
        used = 640
        reserved_output = 256
        model = "gpt-4o"
        trace_id = "run-rag"
        decisions = [_Decision("kept", 400, 400), _Decision("dropped", 300, 0)]

    class CompressionEvent:  # noqa: N801
        technique = "extractive"
        tokens_before = 800
        tokens_after = 200
        ratio = 0.25
        store_kind = "memory"
        kind = "text"
        trace_id = "run-rag"

    with live_spans(tracer=tracer):
        bus.emit(AssemblyReport())
        bus.emit(CompressionEvent())

    spans = exporter.get_finished_spans()
    asm = next(s for s in spans if s.name == "rag.assemble")
    assert asm.attributes["cendor.rag.budget"] == 1000
    assert asm.attributes["cendor.rag.kept"] == 1
    assert asm.attributes["cendor.rag.dropped"] == 1
    assert asm.attributes["cendor.rag.tokens_before"] == 700
    comp = next(s for s in spans if s.name == "rag.compress")
    assert comp.attributes["cendor.rag.technique"] == "extractive"
    assert comp.attributes["cendor.rag.tokens_after"] == 200


def test_rag_assemble_end_to_end_via_context_budget(build):
    """An agent with ``context_budget`` runs contextkit assembly, which emits an ``AssemblyReport``
    on the bus → a ``rag.assemble`` span, with no synthetic events."""
    tracer, exporter = _mem_tracer()
    agent = Agent(name="assistant", model="gpt-4o", context_budget=2000)
    with respx.mock:
        respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("ok"))])
        with live_spans(tracer=tracer):
            run(agent, "summarize this")
    assert _by_name(exporter.get_finished_spans(), "rag.assemble"), [
        s.name for s in exporter.get_finished_spans()
    ]


# --------------------------------------------------------------------------- MCP lifecycle


def test_mcp_lifecycle_spans_and_source_registration():
    """``load_mcp_tools(server=…, transport=…)`` emits ``mcp.connect`` + ``mcp.list_tools`` and
    registers each wrapped tool's source as ``mcp``. Uses the global tracer (setup-time, not a run
    child), so wire the exporter into whatever provider is active."""
    from opentelemetry import trace as ot
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from cendor.sdk.mcp import load_mcp_tools

    exporter = InMemorySpanExporter()
    current = ot.get_tracer_provider()
    if isinstance(current, TracerProvider):
        current.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        ot.set_tracer_provider(provider)

    class _FakeSession:
        async def list_tools(self):
            return [
                {"name": "search", "description": "Search", "inputSchema": {"type": "object"}},
                {"name": "fetch", "description": "Fetch", "inputSchema": {"type": "object"}},
            ]

        async def call_tool(self, name, args):
            return {"content": [{"text": "ok"}]}

    tools = asyncio.run(load_mcp_tools(_FakeSession(), server="github", transport="stdio"))
    assert {t.name for t in tools} == {"search", "fetch"}
    assert _tel.tool_source("search") == {"source": "mcp", "server": "github", "transport": "stdio"}

    spans = exporter.get_finished_spans()
    connect = _by_name(spans, "mcp.connect")
    listing = _by_name(spans, "mcp.list_tools")
    assert connect and listing, [s.name for s in spans]
    assert connect[0].attributes["cendor.mcp.server"] == "github"
    assert connect[0].attributes["cendor.mcp.transport"] == "stdio"
    assert listing[0].attributes["cendor.mcp.tool_count"] == 2
