"""The agent loop: assemble → call → normalize → tools → repeat → finalize (plan §6).

``run(agent, input)`` is sync; ``run.aio(agent, input)`` is async — same signature. The loop runs
inside a ``cendor.core.trace(run_id)`` scope, so every ``LLMCall``/``ToolCall`` it emits shares one
``trace_id``; the SDK collects those bus events (in order) and returns them as ``Result.steps``.
Governance (``budget``/``guard``/``track``/``AuditLog``) rides the same seams with no extra wiring —
an ungoverned ``run()`` works on ``cendor-core`` alone.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import is_dataclass
from typing import TYPE_CHECKING, Any

from cendor.core import bus, trace
from cendor.core.types import LLMCall, ToolCall

from .providers import assistant_message, tool_result_message
from .resilience import RetryPolicy, acall_with_retry, call_with_retry
from .result import Result, RunComplete, Step, TextDelta, ToolCallEvent, ToolResultEvent

if TYPE_CHECKING:
    from .agent import Agent
    from .memory import Session

#: The acttrace ``Decision`` handle for the run currently executing in this context (or ``None``).
#: Human-in-the-loop tools (``cendor.sdk.hitl``) read it to record ``human_oversight`` on the same
#: audit chain and decision the run is already correlated by. Set by :func:`_decision`.
_active_decision: ContextVar[Any] = ContextVar("cendor_sdk_active_decision", default=None)


# --------------------------------------------------------------------------- helpers


@contextmanager
def _collector(run_id: str, *, agent_name: str = "", on_step: Any = None) -> Any:
    """Subscribe a bus collector for this run; yields the (ordered) list of its LLM/tool events.

    If ``on_step`` is set, it is called with each event's :class:`Step` **live**, as the event is
    emitted on the bus (after each model turn / tool call completes) — the public progress hook.
    A raised callback is swallowed: a bad progress hook must never break a run.
    """
    events: list[LLMCall | ToolCall] = []

    def on_bus(ev: Any) -> None:
        if isinstance(ev, (LLMCall, ToolCall)) and getattr(ev, "trace_id", "") == run_id:
            events.append(ev)
            if on_step is not None:
                try:
                    on_step(_step(agent_name, ev))
                except Exception:  # noqa: BLE001 - a progress hook must never break a run
                    pass

    bus.subscribe(on_bus)
    try:
        yield events
    finally:
        bus.unsubscribe(on_bus)


@contextmanager
def _decision(audit: Any, agent: Agent, input: Any, run_id: str) -> Any:
    """Open an acttrace ``decision()`` for the run when an ``AuditLog`` is provided.

    Correlates the auto-captured ``llm_call``/``tool_call`` entries in the chain by ``decision_id``
    (and records the ``trace_id`` bridge). No-op when ``audit`` is ``None`` — governance stays
    optional."""
    if audit is None:
        yield None
        return
    with audit.decision(input=_safe_input(input), actor=agent.name) as d:
        token = _active_decision.set(d)
        try:
            d.record(agent=agent.name, model=agent.model, trace_id=run_id)
        except Exception:  # noqa: BLE001 - recording is best-effort; never break a run
            pass
        try:
            yield d
        finally:
            _active_decision.reset(token)


def _safe_input(input: Any) -> Any:
    if isinstance(input, (str, dict, list)):
        return input
    return repr(input)


def _prepare_messages(agent: Agent, input: Any, session: Session | None) -> list[dict]:
    messages: list[dict] = list(session.snapshot()) if session is not None else []
    if isinstance(input, str):
        messages.append({"role": "user", "content": input})
    elif isinstance(input, dict):
        messages.append(input)
    elif isinstance(input, list):
        messages.extend(input)
    else:
        messages.append({"role": "user", "content": str(input)})
    if getattr(agent, "retriever", None) is not None:
        _inject_retrieved_context(agent, messages)
    return messages


def _inject_retrieved_context(agent: Agent, messages: list[dict]) -> None:
    """Retrieve context for the latest user query and insert it as a system message before it.

    "Always-on" RAG: runs once per run, governed (the retriever's embed call rides the bus). No-op
    if there's no user turn or the retriever returns nothing."""
    from .providers import _text_of_content
    from .rag import format_context

    idx = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None
    )
    if idx is None:
        return
    raw = messages[idx].get("content")
    query = raw if isinstance(raw, str) else _text_of_content(raw)
    chunks = agent.retriever(query)
    if chunks:
        messages.insert(idx, {"role": "system", "content": format_context(chunks)})


def _assemble(agent: Agent, messages: list[dict]) -> list[dict]:
    """Optional context assembly to a token budget via contextkit (emits an audited AssemblyReport).
    Falls back to the raw messages if unset or if assembly can't handle the shape."""
    if not agent.context_budget:
        return messages
    try:
        from cendor.contextkit import Block, Context

        ctx = Context(
            budget_tokens=agent.context_budget,
            model=agent.model,
            reserve_output=agent.max_tokens or 512,
        )
        ctx.add(Block(messages=messages))
        return ctx.assemble()
    except Exception:  # noqa: BLE001 - assembly is best-effort; degrade to raw messages
        return messages


def _stringify_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _schema_from_output_type(output_type: Any) -> dict | None:
    """Derive a JSON Schema from an ``output_type`` for provider-native structured output.

    A dict is taken as a schema as-is; a Pydantic model via ``model_json_schema()``; a dataclass by
    mapping its fields. ``None`` when no schema can be derived (falls back to a JSON nudge)."""
    if output_type is None:
        return None
    if isinstance(output_type, dict):
        return output_type
    if hasattr(output_type, "model_json_schema"):  # pydantic
        try:
            return output_type.model_json_schema()
        except Exception:  # noqa: BLE001 - schema derivation is best-effort
            return None
    if is_dataclass(output_type):
        import dataclasses
        import typing

        from .tools import _schema_for_annotation

        try:
            hints = typing.get_type_hints(output_type)
        except Exception:  # noqa: BLE001
            hints = {}
        props: dict[str, Any] = {}
        required: list[str] = []
        for f in dataclasses.fields(output_type):
            props[f.name] = _schema_for_annotation(hints.get(f.name, f.type))
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                required.append(f.name)
        schema: dict[str, Any] = {
            "type": "object",
            "properties": props,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema
    return None


def _parse_output(content: Any, output_type: Any) -> Any:
    if output_type is None or content is None:
        return content
    data: Any = content
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return content  # provider returned prose; hand it back unparsed
    if isinstance(output_type, dict):  # a raw JSON schema → return the parsed dict
        return data
    ctor: Any = output_type
    if hasattr(ctor, "model_validate"):  # pydantic
        return ctor.model_validate(data)
    if is_dataclass(output_type) and isinstance(data, dict):
        return ctor(**data)
    try:
        return ctor(**data) if isinstance(data, dict) else ctor(data)
    except Exception:  # noqa: BLE001 - fall back to the parsed data rather than crash
        return data


def _step(agent_name: str, ev: LLMCall | ToolCall) -> Step:
    return Step(agent=agent_name, kind="llm" if isinstance(ev, LLMCall) else "tool", call=ev)


def _resolve_sync(value: Any) -> Any:
    """Run an awaitable to completion from a sync context (async tool in a sync run)."""
    if not inspect.isawaitable(value):
        return value

    async def _await() -> Any:
        return await value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await())
    raise RuntimeError(
        "an async tool was invoked from a synchronous run inside a running event loop; "
        "use `await run.aio(...)` instead"
    )


# ------------------------------------------------------------------------- reusable per-agent loop
#
# One agent's turns, sharing a run_id. The orchestrator (orchestration.py) composes these with
# nested child trace ids and per-agent governance; the single-agent Runner calls them once.

ToolResolver = Any  # Callable[[str], Tool | None]


def _client_for(agent: Agent, async_: bool) -> Any:
    provider = agent.provider_impl
    if agent.client is not None:
        return provider.adopt(agent.client, async_=async_)
    return provider.client(async_=async_, config=agent.config())


def run_agent_sync(
    agent: Agent,
    messages: list[dict],
    run_id: str,
    *,
    audit: Any = None,
    max_turns: int | None = None,
    tools: list | None = None,
    resolve: ToolResolver = None,
    handoff_targets: dict[str, str] | None = None,
    retry: RetryPolicy | None = None,
    on_turn: Any = None,
    on_step: Any = None,
) -> tuple[Any, list[Step], str | None]:
    """Run one agent's turns over ``messages`` (mutated in place).

    Returns ``(output, steps, switched)`` — ``switched`` is a handoff target name if the agent
    transferred control, else ``None``. ``retry`` retries transient model calls; ``on_turn`` (if
    set) is called with ``messages`` after each turn — the checkpoint hook; ``on_step`` (if set) is
    called with each :class:`Step` live as it completes — the progress hook.
    """
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=False))
    tools = agent.toolset if tools is None else tools
    resolve = agent.get_tool if resolve is None else resolve
    handoff_targets = handoff_targets or {}
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    output: Any = None
    switched: str | None = None

    with (
        _collector(run_id, agent_name=agent.name, on_step=on_step) as events,
        trace(run_id),
        _decision(audit, agent, messages, run_id),
    ):
        for _turn in range(turns):
            wire = _assemble(agent, messages)
            kwargs = provider.build_kwargs(
                agent.model,
                wire,
                tools,
                agent.instructions,
                json_mode=json_mode,
                temperature=agent.temperature,
                max_tokens=agent.max_tokens,
                output_schema=_schema_from_output_type(agent.output_type),
            )
            if agent.extra:
                kwargs.update(agent.extra)  # provider-param passthrough (tool_choice, reasoning, …)
            if agent.cache:
                kwargs = provider.apply_cache(kwargs)
            parsed = provider.parse(call_with_retry(create, kwargs, retry))
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                for tc in parsed.tool_calls:
                    result = _exec_tool_sync(resolve, tc)
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    if tc.name in handoff_targets:
                        switched = handoff_targets[tc.name]
                if on_turn is not None:
                    on_turn(messages)
                if switched:
                    break
                continue
            output = parsed.content
            if on_turn is not None:
                on_turn(messages)
            break

    return output, [_step(agent.name, e) for e in events], switched


async def run_agent_async(
    agent: Agent,
    messages: list[dict],
    run_id: str,
    *,
    audit: Any = None,
    max_turns: int | None = None,
    tools: list | None = None,
    resolve: ToolResolver = None,
    handoff_targets: dict[str, str] | None = None,
    retry: RetryPolicy | None = None,
    on_turn: Any = None,
    on_step: Any = None,
) -> tuple[Any, list[Step], str | None]:
    """Async counterpart of :func:`run_agent_sync`."""
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=True))
    tools = agent.toolset if tools is None else tools
    resolve = agent.get_tool if resolve is None else resolve
    handoff_targets = handoff_targets or {}
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    output: Any = None
    switched: str | None = None

    with (
        _collector(run_id, agent_name=agent.name, on_step=on_step) as events,
        trace(run_id),
        _decision(audit, agent, messages, run_id),
    ):
        for _turn in range(turns):
            wire = _assemble(agent, messages)
            kwargs = provider.build_kwargs(
                agent.model,
                wire,
                tools,
                agent.instructions,
                json_mode=json_mode,
                temperature=agent.temperature,
                max_tokens=agent.max_tokens,
                output_schema=_schema_from_output_type(agent.output_type),
            )
            if agent.extra:
                kwargs.update(agent.extra)  # provider-param passthrough (tool_choice, reasoning, …)
            if agent.cache:
                kwargs = provider.apply_cache(kwargs)
            parsed = provider.parse(await acall_with_retry(create, kwargs, retry))
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                # Execute a turn's tool calls concurrently; append results in request order.
                results = await asyncio.gather(
                    *(_exec_tool_async(resolve, tc) for tc in parsed.tool_calls)
                )
                for tc, result in zip(parsed.tool_calls, results, strict=True):
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    if tc.name in handoff_targets:
                        switched = handoff_targets[tc.name]
                if on_turn is not None:
                    on_turn(messages)
                if switched:
                    break
                continue
            output = parsed.content
            if on_turn is not None:
                on_turn(messages)
            break

    return output, [_step(agent.name, e) for e in events], switched


def _exec_tool_sync(resolve: ToolResolver, tc: Any) -> str:
    tool = resolve(tc.name)
    if tool is None:
        return f"[error] unknown tool: {tc.name}"
    try:
        return _stringify_result(_resolve_sync(tool.invoke(tc.arguments)))
    except Exception as e:  # noqa: BLE001 - surface tool errors to the model, keep the loop alive
        return f"[error] {type(e).__name__}: {e}"


async def _exec_tool_async(resolve: ToolResolver, tc: Any) -> str:
    tool = resolve(tc.name)
    if tool is None:
        return f"[error] unknown tool: {tc.name}"
    try:
        return _stringify_result(await tool.ainvoke(tc.arguments))
    except Exception as e:  # noqa: BLE001 - surface tool errors to the model, keep the loop alive
        return f"[error] {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- Runner


class Runner:
    """Drives one agent's loop. ``run`` (sync) / ``run_async`` (async).

    ``retry`` retries transient model calls; ``checkpoint`` (a path or ``Checkpointer``) persists
    the conversation after each turn so a crashed run resumes without re-doing completed work.
    """

    def __init__(
        self,
        agent: Agent,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
        retry: RetryPolicy | None = None,
        checkpoint: Any = None,
        on_step: Any = None,
    ) -> None:
        from .checkpoint import _as_checkpointer

        self.agent = agent
        self.session = session
        self.audit = audit
        self.max_turns = max_turns or agent.max_turns
        self.retry = retry
        self.checkpoint = _as_checkpointer(checkpoint)
        self.on_step = on_step

    def _start(self, input: Any) -> tuple[list[dict], str, Any]:
        """Resolve starting messages (resuming from a checkpoint if one is unfinished)."""
        resume = self.checkpoint.resumable_messages() if self.checkpoint else None
        if resume is not None:
            messages = resume
        else:
            messages = _prepare_messages(self.agent, input, self.session)
        run_id = uuid.uuid4().hex

        def on_turn(msgs: list[dict]) -> None:
            if self.checkpoint is not None:
                self.checkpoint.save({"run_id": run_id, "messages": msgs, "done": False})

        return messages, run_id, (on_turn if self.checkpoint is not None else None)

    def _finish(self, run_id: str, output: Any, steps: list, messages: list[dict]) -> Result:
        if self.session is not None:
            self.session.replace(messages)
        if self.checkpoint is not None:
            self.checkpoint.save(
                {"run_id": run_id, "messages": messages, "done": True, "output": output}
            )
        return Result(
            output=_parse_output(output, self.agent.output_type),
            steps=steps,
            trace_id=run_id,
            agents=[self.agent.name],
            messages=messages,
            incomplete=output is None,  # no final answer (e.g. max_turns hit mid tool loop)
        )

    def run(self, input: Any) -> Result:
        messages, run_id, on_turn = self._start(input)
        output, steps, _ = run_agent_sync(
            self.agent,
            messages,
            run_id,
            audit=self.audit,
            max_turns=self.max_turns,
            retry=self.retry,
            on_turn=on_turn,
            on_step=self.on_step,
        )
        return self._finish(run_id, output, steps, messages)

    async def run_async(self, input: Any) -> Result:
        messages, run_id, on_turn = self._start(input)
        output, steps, _ = await run_agent_async(
            self.agent,
            messages,
            run_id,
            audit=self.audit,
            max_turns=self.max_turns,
            retry=self.retry,
            on_turn=on_turn,
            on_step=self.on_step,
        )
        return self._finish(run_id, output, steps, messages)


# --------------------------------------------------------------------------- streaming


def stream_agent_sync(
    agent: Agent,
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
) -> Any:
    """Yield streaming events for a single agent run (``TextDelta`` / ``ToolCallEvent`` /
    ``ToolResultEvent`` / terminal ``RunComplete``). Providers that reassemble a stream emit text
    incrementally; the rest fall back to a whole-response delta. Same governance seams as ``run()``.
    """
    messages = _prepare_messages(agent, input, session)
    run_id = uuid.uuid4().hex
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=False))
    tools = agent.toolset
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    output: Any = None

    with _collector(run_id) as events, trace(run_id), _decision(audit, agent, messages, run_id):
        for _turn in range(turns):
            wire = _assemble(agent, messages)
            kwargs = provider.build_kwargs(
                agent.model,
                wire,
                tools,
                agent.instructions,
                json_mode=json_mode,
                temperature=agent.temperature,
                max_tokens=agent.max_tokens,
                output_schema=_schema_from_output_type(agent.output_type),
            )
            if agent.extra:
                kwargs.update(agent.extra)
            if agent.cache:
                kwargs = provider.apply_cache(kwargs)
            if provider.supports_stream:
                chunks: list = []
                for chunk in create(**{**kwargs, "stream": True}):
                    chunks.append(chunk)
                    delta = provider.stream_text(chunk)
                    if delta:
                        yield TextDelta(delta)
                parsed = provider.parse_stream(chunks)
            else:  # provider has no reassembler → one whole-response delta
                parsed = provider.parse(create(**kwargs))
                if parsed.content:
                    yield TextDelta(parsed.content)
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                for tc in parsed.tool_calls:
                    yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                    result = _exec_tool_sync(agent.get_tool, tc)
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    yield ToolResultEvent(tc.name, result)
                continue
            output = parsed.content
            break

    if session is not None:
        session.replace(messages)
    steps = [_step(agent.name, e) for e in events]
    yield RunComplete(
        Result(
            output=_parse_output(output, agent.output_type),
            steps=steps,
            trace_id=run_id,
            agents=[agent.name],
            messages=messages,
            incomplete=output is None,
        )
    )


async def stream_agent_async(
    agent: Agent,
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
) -> Any:
    """Async counterpart of :func:`stream_agent_sync` (``async for`` over the same events)."""
    messages = _prepare_messages(agent, input, session)
    run_id = uuid.uuid4().hex
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=True))
    tools = agent.toolset
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    output: Any = None

    with _collector(run_id) as events, trace(run_id), _decision(audit, agent, messages, run_id):
        for _turn in range(turns):
            wire = _assemble(agent, messages)
            kwargs = provider.build_kwargs(
                agent.model,
                wire,
                tools,
                agent.instructions,
                json_mode=json_mode,
                temperature=agent.temperature,
                max_tokens=agent.max_tokens,
                output_schema=_schema_from_output_type(agent.output_type),
            )
            if agent.extra:
                kwargs.update(agent.extra)
            if agent.cache:
                kwargs = provider.apply_cache(kwargs)
            if provider.supports_stream:
                chunks = []
                stream = await create(**{**kwargs, "stream": True})
                async for chunk in stream:
                    chunks.append(chunk)
                    delta = provider.stream_text(chunk)
                    if delta:
                        yield TextDelta(delta)
                parsed = provider.parse_stream(chunks)
            else:
                parsed = provider.parse(await create(**kwargs))
                if parsed.content:
                    yield TextDelta(parsed.content)
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                for tc in parsed.tool_calls:
                    yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                    result = await _exec_tool_async(agent.get_tool, tc)
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    yield ToolResultEvent(tc.name, result)
                continue
            output = parsed.content
            break

    if session is not None:
        session.replace(messages)
    steps = [_step(agent.name, e) for e in events]
    yield RunComplete(
        Result(
            output=_parse_output(output, agent.output_type),
            steps=steps,
            trace_id=run_id,
            agents=[agent.name],
            messages=messages,
            incomplete=output is None,
        )
    )


def stream_agents_sync(
    agents: list[Agent],
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
) -> Any:
    """Stream a **multi-agent** (handoff) run as events; one terminal ``RunComplete`` carries the
    aggregate ``Result``. Mirrors :func:`run_agents`' segment loop, streaming each active agent's
    turns and switching when it calls a ``transfer_to_<peer>`` tool."""
    from .orchestration import _effective, _scope

    registry = {a.name: a for a in agents}
    parent = uuid.uuid4().hex
    messages = _prepare_messages(agents[0], input, session)
    active = agents[0]
    seen: list[str] = []
    steps: list = []
    output: Any = None
    for seg in range(2 * len(registry) + 2):
        if active.name not in seen:
            seen.append(active.name)
        child = f"{parent}:{active.name}#{seg}"
        tools, tool_map, transfer_map = _effective(active, registry)
        provider = active.provider_impl
        create = provider.create_method(_client_for(active, async_=False))
        json_mode = active.output_type is not None
        turns = max_turns or active.max_turns
        switched: str | None = None
        with (
            _scope(active),
            _collector(child, agent_name=active.name) as events,
            trace(child),
            _decision(audit, active, messages, child),
        ):
            for _turn in range(turns):
                wire = _assemble(active, messages)
                kwargs = provider.build_kwargs(
                    active.model,
                    wire,
                    tools,
                    active.instructions,
                    json_mode=json_mode,
                    temperature=active.temperature,
                    max_tokens=active.max_tokens,
                    output_schema=_schema_from_output_type(active.output_type),
                )
                if active.extra:
                    kwargs.update(active.extra)
                if active.cache:
                    kwargs = provider.apply_cache(kwargs)
                if provider.supports_stream:
                    chunks: list = []
                    for chunk in create(**{**kwargs, "stream": True}):
                        chunks.append(chunk)
                        delta = provider.stream_text(chunk)
                        if delta:
                            yield TextDelta(delta)
                    parsed = provider.parse_stream(chunks)
                else:
                    parsed = provider.parse(create(**kwargs))
                    if parsed.content:
                        yield TextDelta(parsed.content)
                messages.append(assistant_message(parsed.content, parsed.tool_calls))
                if parsed.tool_calls:
                    for tc in parsed.tool_calls:
                        yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                        result = _exec_tool_sync(tool_map.get, tc)
                        messages.append(tool_result_message(tc.id, tc.name, result))
                        yield ToolResultEvent(tc.name, result)
                        if tc.name in transfer_map:
                            switched = transfer_map[tc.name]
                    if switched:
                        break
                    continue
                output = parsed.content
                break
        steps.extend(_step(active.name, e) for e in events)
        if switched and switched in registry:
            active = registry[switched]
            continue
        break

    if session is not None:
        session.replace(messages)
    yield RunComplete(
        Result(
            output=_parse_output(output, active.output_type),
            steps=steps,
            trace_id=parent,
            agents=seen,
            messages=messages,
            incomplete=output is None,
        )
    )


async def stream_agents_async(
    agents: list[Agent],
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
) -> Any:
    """Async counterpart of :func:`stream_agents_sync` (``async for`` over the events)."""
    from .orchestration import _effective, _scope

    registry = {a.name: a for a in agents}
    parent = uuid.uuid4().hex
    messages = _prepare_messages(agents[0], input, session)
    active = agents[0]
    seen: list[str] = []
    steps: list = []
    output: Any = None
    for seg in range(2 * len(registry) + 2):
        if active.name not in seen:
            seen.append(active.name)
        child = f"{parent}:{active.name}#{seg}"
        tools, tool_map, transfer_map = _effective(active, registry)
        provider = active.provider_impl
        create = provider.create_method(_client_for(active, async_=True))
        json_mode = active.output_type is not None
        turns = max_turns or active.max_turns
        switched: str | None = None
        with (
            _scope(active),
            _collector(child, agent_name=active.name) as events,
            trace(child),
            _decision(audit, active, messages, child),
        ):
            for _turn in range(turns):
                wire = _assemble(active, messages)
                kwargs = provider.build_kwargs(
                    active.model,
                    wire,
                    tools,
                    active.instructions,
                    json_mode=json_mode,
                    temperature=active.temperature,
                    max_tokens=active.max_tokens,
                    output_schema=_schema_from_output_type(active.output_type),
                )
                if active.extra:
                    kwargs.update(active.extra)
                if active.cache:
                    kwargs = provider.apply_cache(kwargs)
                if provider.supports_stream:
                    chunks = []
                    stream = await create(**{**kwargs, "stream": True})
                    async for chunk in stream:
                        chunks.append(chunk)
                        delta = provider.stream_text(chunk)
                        if delta:
                            yield TextDelta(delta)
                    parsed = provider.parse_stream(chunks)
                else:
                    parsed = provider.parse(await create(**kwargs))
                    if parsed.content:
                        yield TextDelta(parsed.content)
                messages.append(assistant_message(parsed.content, parsed.tool_calls))
                if parsed.tool_calls:
                    for tc in parsed.tool_calls:
                        yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                        result = await _exec_tool_async(tool_map.get, tc)
                        messages.append(tool_result_message(tc.id, tc.name, result))
                        yield ToolResultEvent(tc.name, result)
                        if tc.name in transfer_map:
                            switched = transfer_map[tc.name]
                    if switched:
                        break
                    continue
                output = parsed.content
                break
        steps.extend(_step(active.name, e) for e in events)
        if switched and switched in registry:
            active = registry[switched]
            continue
        break

    if session is not None:
        session.replace(messages)
    yield RunComplete(
        Result(
            output=_parse_output(output, active.output_type),
            steps=steps,
            trace_id=parent,
            agents=seen,
            messages=messages,
            incomplete=output is None,
        )
    )


# --------------------------------------------------------------------------- run() entrypoint


class _Run:
    """The ``run`` callable.

    - ``run(agent, input)`` — a single agent (sync); ``run.aio(...)`` async.
    - ``run([entry, peer, ...], input)`` — multi-agent: the first agent is the entry point and the
      rest are reachable by handoff (plan §5). Dispatches to :mod:`cendor.sdk.orchestration`.
    """

    def __call__(
        self,
        agent: Any,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
        retry: RetryPolicy | None = None,
        checkpoint: Any = None,
        on_step: Any = None,
    ) -> Result:
        """``on_step`` (if set) is called with each :class:`~cendor.sdk.result.Step` live as the
        run progresses — a public progress hook, complementing the post-hoc ``Result.steps``."""
        if isinstance(agent, (list, tuple)):
            from .orchestration import run_agents

            return run_agents(
                list(agent),
                input,
                audit=audit,
                max_turns=max_turns,
                session=session,
                checkpoint=checkpoint,
                on_step=on_step,
            )
        return Runner(
            agent,
            session=session,
            audit=audit,
            max_turns=max_turns,
            retry=retry,
            checkpoint=checkpoint,
            on_step=on_step,
        ).run(input)

    async def aio(
        self,
        agent: Any,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
        retry: RetryPolicy | None = None,
        checkpoint: Any = None,
        on_step: Any = None,
    ) -> Result:
        """Async counterpart of :meth:`__call__`; ``on_step`` fires per :class:`Step` live."""
        if isinstance(agent, (list, tuple)):
            from .orchestration import run_agents_async

            return await run_agents_async(
                list(agent),
                input,
                audit=audit,
                max_turns=max_turns,
                session=session,
                checkpoint=checkpoint,
                on_step=on_step,
            )
        return await Runner(
            agent,
            session=session,
            audit=audit,
            max_turns=max_turns,
            retry=retry,
            checkpoint=checkpoint,
            on_step=on_step,
        ).run_async(input)

    def stream(
        self,
        agent: Any,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
    ) -> Any:
        """Stream a run as events (sync generator). Terminal event is ``RunComplete`` carrying the
        ``Result``. A single agent streams its turns; a list streams a multi-agent handoff run,
        switching active agents on a ``transfer_to_<peer>`` call — one terminal ``RunComplete``."""
        if isinstance(agent, (list, tuple)):
            return stream_agents_sync(
                list(agent), input, session=session, audit=audit, max_turns=max_turns
            )
        return stream_agent_sync(agent, input, session=session, audit=audit, max_turns=max_turns)

    def astream(
        self,
        agent: Any,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
    ) -> Any:
        """Async counterpart of :meth:`stream` (``async for`` over the events); a list streams a
        multi-agent handoff run."""
        if isinstance(agent, (list, tuple)):
            return stream_agents_async(
                list(agent), input, session=session, audit=audit, max_turns=max_turns
            )
        return stream_agent_async(agent, input, session=session, audit=audit, max_turns=max_turns)


run = _Run()
