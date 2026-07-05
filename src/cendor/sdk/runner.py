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

    # --- sync ----------------------------------------------------------------------------------

    def run(self, input: Any) -> Result:
        agent = self.agent
        provider = agent.provider_impl
        client = (
            provider.adopt(agent.client, async_=False)
            if agent.client is not None
            else provider.client(async_=False, config=agent.config())
        )
        create = provider.create_method(client)
        messages = _prepare_messages(agent, input, self.session)
        json_mode = agent.output_type is not None
        run_id = uuid.uuid4().hex
        output: Any = None

        with (
            _collector(run_id) as events,
            trace(run_id),
            _decision(self.audit, agent, input, run_id),
        ):
            for _turn in range(self.max_turns):
                wire = _assemble(agent, messages)
                kwargs = provider.build_kwargs(
                    agent.model,
                    wire,
                    agent.toolset,
                    agent.instructions,
                    json_mode=json_mode,
                    temperature=agent.temperature,
                    max_tokens=agent.max_tokens,
                )
                response = create(**kwargs)
                parsed = provider.parse(response)
                messages.append(assistant_message(parsed.content, parsed.tool_calls))
                if parsed.tool_calls:
                    for tc in parsed.tool_calls:
                        result = self._exec_tool_sync(tc)
                        messages.append(tool_result_message(tc.id, tc.name, result))
                    continue
                output = parsed.content
                break

        return self._finalize(run_id, output, events, messages)

    def _exec_tool_sync(self, tc: Any) -> str:
        tool = self.agent.get_tool(tc.name)
        if tool is None:
            return f"[error] unknown tool: {tc.name}"
        try:
            return _stringify_result(_resolve_sync(tool.invoke(tc.arguments)))
        except Exception as e:  # noqa: BLE001 - surface tool errors to the model, keep the loop alive
            return f"[error] {type(e).__name__}: {e}"

    # --- async ---------------------------------------------------------------------------------

    async def run_async(self, input: Any) -> Result:
        agent = self.agent
        provider = agent.provider_impl
        client = (
            provider.adopt(agent.client, async_=True)
            if agent.client is not None
            else provider.client(async_=True, config=agent.config())
        )
        create = provider.create_method(client)
        messages = _prepare_messages(agent, input, self.session)
        json_mode = agent.output_type is not None
        run_id = uuid.uuid4().hex
        output: Any = None

        with (
            _collector(run_id) as events,
            trace(run_id),
            _decision(self.audit, agent, input, run_id),
        ):
            for _turn in range(self.max_turns):
                wire = _assemble(agent, messages)
                kwargs = provider.build_kwargs(
                    agent.model,
                    wire,
                    agent.toolset,
                    agent.instructions,
                    json_mode=json_mode,
                    temperature=agent.temperature,
                    max_tokens=agent.max_tokens,
                )
                response = await create(**kwargs)
                parsed = provider.parse(response)
                messages.append(assistant_message(parsed.content, parsed.tool_calls))
                if parsed.tool_calls:
                    for tc in parsed.tool_calls:
                        result = await self._exec_tool_async(tc)
                        messages.append(tool_result_message(tc.id, tc.name, result))
                    continue
                output = parsed.content
                break

        return self._finalize(run_id, output, events, messages)

    async def _exec_tool_async(self, tc: Any) -> str:
        tool = self.agent.get_tool(tc.name)
        if tool is None:
            return f"[error] unknown tool: {tc.name}"
        try:
            return _stringify_result(await tool.ainvoke(tc.arguments))
        except Exception as e:  # noqa: BLE001 - surface tool errors to the model, keep the loop alive
            return f"[error] {type(e).__name__}: {e}"

    # --- finalize ------------------------------------------------------------------------------

    def _finalize(self, run_id: str, output: Any, events: list, messages: list[dict]) -> Result:
        parsed_output = _parse_output(output, self.agent.output_type)
        steps = [_step(self.agent.name, ev) for ev in events]
        if self.session is not None:
            self.session.replace(messages)
        return Result(
            output=parsed_output,
            steps=steps,
            trace_id=run_id,
            agents=[self.agent.name],
            messages=messages,
        )


# --------------------------------------------------------------------------- run() entrypoint


class _Run:
    """The ``run`` callable: ``run(agent, input)`` (sync) and ``run.aio(agent, input)`` (async)."""

    def __call__(
        self,
        agent: Agent,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
    ) -> Result:
        return Runner(agent, session=session, audit=audit, max_turns=max_turns).run(input)

    async def aio(
        self,
        agent: Agent,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
    ) -> Result:
        return await Runner(agent, session=session, audit=audit, max_turns=max_turns).run_async(
            input
        )


run = _Run()
