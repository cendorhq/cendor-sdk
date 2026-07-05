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
from dataclasses import is_dataclass
from typing import TYPE_CHECKING, Any

from cendor.core import bus, trace
from cendor.core.types import LLMCall, ToolCall

from .providers import assistant_message, tool_result_message
from .result import Result, Step

if TYPE_CHECKING:
    from .agent import Agent
    from .memory import Session


# --------------------------------------------------------------------------- helpers


@contextmanager
def _collector(run_id: str) -> Any:
    """Subscribe a bus collector for this run; yields the (ordered) list of its LLM/tool events."""
    events: list[LLMCall | ToolCall] = []

    def on_event(ev: Any) -> None:
        if isinstance(ev, (LLMCall, ToolCall)) and getattr(ev, "trace_id", "") == run_id:
            events.append(ev)

    bus.subscribe(on_event)
    try:
        yield events
    finally:
        bus.unsubscribe(on_event)


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
        try:
            d.record(agent=agent.name, model=agent.model, trace_id=run_id)
        except Exception:  # noqa: BLE001 - recording is best-effort; never break a run
            pass
        yield d


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
    return messages


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
) -> tuple[Any, list[Step], str | None]:
    """Run one agent's turns over ``messages`` (mutated in place).

    Returns ``(output, steps, switched)`` where ``switched`` is a handoff target name if the agent
    transferred control, else ``None``.
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
            )
            parsed = provider.parse(create(**kwargs))
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                for tc in parsed.tool_calls:
                    result = _exec_tool_sync(resolve, tc)
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    if tc.name in handoff_targets:
                        switched = handoff_targets[tc.name]
                if switched:
                    break
                continue
            output = parsed.content
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
            )
            parsed = provider.parse(await create(**kwargs))
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                for tc in parsed.tool_calls:
                    result = await _exec_tool_async(resolve, tc)
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    if tc.name in handoff_targets:
                        switched = handoff_targets[tc.name]
                if switched:
                    break
                continue
            output = parsed.content
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
    """Drives one agent's loop. ``run`` (sync) / ``run_async`` (async)."""

    def __init__(
        self,
        agent: Agent,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
    ) -> None:
        self.agent = agent
        self.session = session
        self.audit = audit
        self.max_turns = max_turns or agent.max_turns

    def run(self, input: Any) -> Result:
        messages = _prepare_messages(self.agent, input, self.session)
        run_id = uuid.uuid4().hex
        output, steps, _ = run_agent_sync(
            self.agent, messages, run_id, audit=self.audit, max_turns=self.max_turns
        )
        if self.session is not None:
            self.session.replace(messages)
        return Result(
            output=_parse_output(output, self.agent.output_type),
            steps=steps,
            trace_id=run_id,
            agents=[self.agent.name],
            messages=messages,
        )

    async def run_async(self, input: Any) -> Result:
        messages = _prepare_messages(self.agent, input, self.session)
        run_id = uuid.uuid4().hex
        output, steps, _ = await run_agent_async(
            self.agent, messages, run_id, audit=self.audit, max_turns=self.max_turns
        )
        if self.session is not None:
            self.session.replace(messages)
        return Result(
            output=_parse_output(output, self.agent.output_type),
            steps=steps,
            trace_id=run_id,
            agents=[self.agent.name],
            messages=messages,
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
    ) -> Result:
        if isinstance(agent, (list, tuple)):
            from .orchestration import run_agents

            return run_agents(list(agent), input, audit=audit, max_turns=max_turns)
        return Runner(agent, session=session, audit=audit, max_turns=max_turns).run(input)

    async def aio(
        self,
        agent: Any,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
    ) -> Result:
        if isinstance(agent, (list, tuple)):
            from .orchestration import run_agents_async

            return await run_agents_async(list(agent), input, audit=audit, max_turns=max_turns)
        return await Runner(agent, session=session, audit=audit, max_turns=max_turns).run_async(
            input
        )


run = _Run()
