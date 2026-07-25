"""The SDK's automatic run scope (DR-1 / W1.4): a governed run is visible with zero telemetry code.

`run()` opens the existing `live_spans` machinery itself when telemetry is on, the app has
configured an OpenTelemetry provider, and no explicit scope is open. An explicit
`with live_spans(...)` still wins (one root, never two); `CENDOR_TELEMETRY=off` disables it; with
OpenTelemetry absent nothing happens at all.

Offline via respx; observed through a real in-memory span exporter installed as the GLOBAL
provider — which is the point: the app configures OTel normally, writing no Cendor telemetry code.
"""

from __future__ import annotations

import pytest
import respx
from cendor.core import bus
from cendor.core.otel import TELEMETRY_ENV

from cendor.sdk import Agent, Session, run, tool
from cendor.sdk.otel import live_spans


@pytest.fixture
def global_otel():
    """Install an in-memory tracer provider as the global one (what an app's OTel setup does)."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.util._once import Once

    def _reset():
        trace._TRACER_PROVIDER = None
        trace._TRACER_PROVIDER_SET_ONCE = Once()

    _reset()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        provider.shutdown()
        _reset()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(TELEMETRY_ENV, raising=False)
    yield
    bus._reset()


@tool
def get_weather(city: str) -> str:
    """Weather for a city."""
    return f"Sunny in {city}"


def _one_call(build):
    respx.post(build.CHAT_URL).mock(side_effect=[build.resp(build.openai_chat("hello"))])


def test_zero_code_run_produces_a_root_and_children(global_otel, build):
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        result = run(agent, "hi")
    assert result.output == "hello"
    spans = global_otel.get_finished_spans()
    names = [s.name for s in spans]
    assert "agent.run" in names, "the run root opened itself"
    assert "chat gpt-4o" in names, "and the step is a child of it"
    root = next(s for s in spans if s.name == "agent.run")
    chat = next(s for s in spans if s.name == "chat gpt-4o")
    assert chat.parent is not None and chat.parent.span_id == root.context.span_id
    assert root.attributes["gen_ai.operation.name"] == "agent"
    assert root.attributes["gen_ai.usage.input_tokens"] > 0
    assert root.attributes["cendor.run.cost_usd"]
    assert "cendor.run.label" not in root.attributes, "a label is human-authored — never invented"


def test_the_session_id_becomes_the_conversation_id(global_otel, build):
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        run(agent, "hi", session=Session(id="chat-42"))
    root = next(s for s in global_otel.get_finished_spans() if s.name == "agent.run")
    assert root.attributes["gen_ai.conversation.id"] == "chat-42"


def test_an_explicit_scope_still_wins_one_root(global_otel, build):
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        with live_spans(label="refund triage"):
            run(agent, "hi")
    roots = [s for s in global_otel.get_finished_spans() if s.name == "agent.run"]
    assert len(roots) == 1, "the user's scope owns the run — the SDK must not nest a second root"
    assert roots[0].attributes["cendor.run.label"] == "refund triage"


def test_off_produces_nothing(global_otel, build, monkeypatch):
    monkeypatch.setenv(TELEMETRY_ENV, "off")
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        run(agent, "hi")
    assert global_otel.get_finished_spans() == ()


def test_no_provider_configured_produces_nothing(build):
    """The predicate is honest: nothing is emitted until the app configures OTel."""
    from opentelemetry import trace
    from opentelemetry.util._once import Once

    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        result = run(agent, "hi")  # must not raise
    assert result.output == "hello"


def test_governance_correlates_to_the_auto_scope(global_otel, build):
    """The whole point of the run root: audit spans land inside the run's trace."""
    from cendor.acttrace import AuditLog

    log = AuditLog(system="support")  # its mirror auto-attaches too (DR-2a)
    agent = Agent(name="assistant", model="gpt-4o")
    try:
        with respx.mock:
            _one_call(build)
            run(agent, "hi", audit=log)
    finally:
        log.detach()
    spans = global_otel.get_finished_spans()
    root = next(s for s in spans if s.name == "agent.run")
    audit = [s for s in spans if s.name.startswith("audit.")]
    assert audit, "governance reached the wire with no mirror line"
    linked = [s for s in audit if s.context.trace_id == root.context.trace_id]
    assert linked, "and at least one entry shares the run's trace (correlated in any backend)"


def test_a_throwing_run_still_closes_the_auto_scope(global_otel, build):
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(side_effect=RuntimeError("boom"))
        # The provider SDK wraps the transport failure, so catch broadly on purpose — what this test
        # asserts is the scope's teardown, not which error type surfaces.
        with pytest.raises(Exception):  # noqa: B017
            run(agent, "hi")
    # The scope closed (its root ended) and the latch was released, so a later libs-only call is not
    # silently suppressed.
    from cendor.core import otel as _co

    assert _co.live_spans_active() is False
    assert any(s.name == "agent.run" for s in global_otel.get_finished_spans())


async def test_the_async_path_also_auto_scopes(global_otel, build):
    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        await run.aio(agent, "hi")
    assert any(s.name == "agent.run" for s in global_otel.get_finished_spans())


def test_the_sync_stream_path_auto_scopes_exactly_once(global_otel, build):
    from cendor.sdk.result import RunComplete

    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        events = list(run.stream(agent, "hi"))
    assert isinstance(events[-1], RunComplete)
    spans = global_otel.get_finished_spans()
    assert [s.name for s in spans].count("agent.run") == 1
    # No double render: the streamed call appears once, as the run's child (not also as a flat
    # span).
    assert [s.name for s in spans].count("chat gpt-4o") == 1


async def test_the_async_stream_path_auto_scopes_exactly_once(global_otel, build):
    from cendor.sdk.result import RunComplete

    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        events = [ev async for ev in run.astream(agent, "hi")]
    assert isinstance(events[-1], RunComplete)
    names = [s.name for s in global_otel.get_finished_spans()]
    assert names.count("agent.run") == 1
    assert names.count("chat gpt-4o") == 1


# ------------------------------------------------------------------- GLR-3: no cross-run adoption
# These use clients with REAL latency so the runs genuinely overlap. With an instant stub each run
# finishes before the next starts and the bus never interleaves — which is why the defect below
# survived the wave: every acceptance probe was sequential.


class _SlowOpenAI:
    """An openai-shaped async client whose completion takes ``delay`` seconds."""

    def __init__(self, content: str, delay: float, model: str) -> None:
        self._content, self._delay, self._model = content, delay, model
        self.chat = self
        self.completions = self

    async def create(self, **kwargs: object) -> object:
        import asyncio
        from types import SimpleNamespace

        await asyncio.sleep(self._delay)
        return SimpleNamespace(
            id="c1",
            model=self._model,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._content, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


def _slow_agent(model: str, content: str, delay: float) -> Agent:
    return Agent(
        name="assistant", model=model, client=_SlowOpenAI(content, delay, model), provider="openai"
    )


async def test_two_overlapping_runs_never_adopt_each_others_calls(global_otel):
    """Both roots used to learn whichever run emitted first: one call rendered twice (once under
    each root), the other lost, and both roots stamped with one ``cendor.run.id``."""
    import asyncio

    await asyncio.gather(
        run.aio(_slow_agent("gpt-4o-mini", "a", 0.05), "hi"),
        run.aio(_slow_agent("gpt-4.1-mini", "b", 0.01), "hi"),
    )
    spans = global_otel.get_finished_spans()
    roots = [s for s in spans if s.name == "agent.run"]
    assert len(roots) == 2
    by_run_id = {str(s.attributes.get("cendor.run.id") or ""): s.context.span_id for s in roots}
    assert len(by_run_id) == 2 and all(by_run_id), "each root learned its OWN run id"
    chats = [s for s in spans if s.name.startswith("chat ")]
    assert sorted(s.name for s in chats) == ["chat gpt-4.1-mini", "chat gpt-4o-mini"]
    for c in chats:  # every call sits under the root of the run that made it
        assert c.parent is not None
        assert c.parent.span_id == by_run_id[str(c.attributes["cendor.trace_id"])]


def test_a_stream_in_flight_does_not_silence_a_concurrent_run(global_otel, build):
    """The stream's scope lives in the producer's copied context, so the consumer's context stays
    clean and a concurrent run still opens its own scope."""
    import asyncio
    from types import SimpleNamespace

    from cendor.core import otel as _co

    agent = Agent(name="assistant", model="gpt-4o")
    with respx.mock:
        _one_call(build)
        stream = run.stream(agent, "hi")
        next(iter(stream))  # the stream's scope is open
        assert _co.live_spans_active() is False, "the latch must not leak into the consumer"

        def _instant(**kwargs: object) -> object:
            return SimpleNamespace(
                id="c1",
                model="gpt-4.1-mini",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="b", tool_calls=None), finish_reason="stop"
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        other = Agent(
            name="b",
            model="gpt-4.1-mini",
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=_instant))
            ),
            provider="openai",
        )
        run(other, "hi")
        for _ev in stream:
            pass
    assert asyncio  # (import kept meaningful for the async sibling above)
    spans = global_otel.get_finished_spans()
    assert [s.name for s in spans].count("agent.run") == 2
    assert sorted(s.name for s in spans if s.name.startswith("chat ")) == [
        "chat gpt-4.1-mini",
        "chat gpt-4o",
    ]


def test_a_run_less_libs_call_is_not_adopted_into_a_run(global_otel):
    """A call with no run id belongs to no run: core's flat emitter renders it, the run scope must
    not (it used to become the run's step 1, putting a foreign call's cost inside the run)."""
    import asyncio

    from cendor.core import bus
    from cendor.core.types import LLMCall, Usage

    async def main():
        async def _foreign():
            await asyncio.sleep(0.005)
            bus.emit(
                LLMCall(
                    id="x1",
                    provider="openai",
                    model="libs-only-model",
                    messages=[],
                    usage=Usage(10, 5),
                )
            )

        await asyncio.gather(run.aio(_slow_agent("gpt-4o-mini", "a", 0.025), "hi"), _foreign())

    asyncio.run(main())
    spans = global_otel.get_finished_spans()
    root = next(s for s in spans if s.name == "agent.run")
    children = [
        s for s in spans if s.parent is not None and s.parent.span_id == root.context.span_id
    ]
    assert [c.name for c in children] == ["chat gpt-4o-mini"]
