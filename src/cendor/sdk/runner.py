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
import re
import uuid
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import is_dataclass
from typing import TYPE_CHECKING, Any

from cendor.core import bus, current_trace_id, trace
from cendor.core.types import LLMCall, ToolCall

from . import _guardrails as _gr
from . import _telemetry as _tel
from ._governance import _conversation_scope, _scope
from ._governance import current_agent as _current_agent
from .providers import assistant_message, tool_result_message
from .resilience import RetryPolicy, acall_with_retry, call_with_retry
from .result import (
    Result,
    RunComplete,
    Step,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    ToolResultEvent,
)

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
            # G13a: stamp which agent made the call into the event's free metadata dict (core types
            # untouched). setdefault so a more-specific value set upstream wins; current_agent()
            # is correct even in multi-agent orchestration (each turn runs under its own _scope).
            meta = getattr(ev, "metadata", None)
            if isinstance(meta, dict):
                meta.setdefault("agent", _current_agent() or agent_name)
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
    if session is not None:  # E-wave: memory.load (single-agent path runs inside the trace scope)
        _tel.emit_memory("load", session, current_trace_id() or "")
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


class ContextBudgetFallback:
    """Diagnostic bus event: ``context_budget`` assembly failed and the turn fell back to raw
    messages. Emitted on ``cendor-core``'s bus so the deliberate best-effort fallback is *silent
    but observable* — subscribe and alert on it if a fallback matters to you. Unknown event types
    are ignored by the stock subscribers (bus-events spec), so emitting this is side-effect-free.
    """

    def __init__(self, agent: str, budget_tokens: int, error: str) -> None:
        self.agent = agent
        self.budget_tokens = budget_tokens
        self.error = error


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
    except Exception as exc:  # noqa: BLE001 - assembly is best-effort; degrade to raw messages
        try:  # make the silent fallback observable on the bus (never let the emit itself break it)
            bus.emit(
                ContextBudgetFallback(
                    agent=agent.name,
                    budget_tokens=int(agent.context_budget),
                    error=type(exc).__name__,
                )
            )
        except Exception:  # noqa: BLE001, S110 - diagnostics must never break the run
            pass
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


_NO_JSON = object()  # sentinel: no JSON value could be recovered from the text

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _loose_json_parse(text: str) -> Any:
    """Recover a JSON value from model output that may be fenced or wrapped in prose.

    Providers without a native JSON-schema mode (Anthropic, Ollama-without-format, HF) very commonly
    wrap structured output in a ```json fence, so a bare ``json.loads`` returned the raw string and
    silently broke the declared ``output_type``. Tries: whole string → fenced block → first balanced
    ``{…}``/``[…]``. Returns :data:`_NO_JSON` when nothing parseable is found.
    """
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = _FENCE_RE.search(s)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = next((i for i, ch in enumerate(s) if ch in "{["), -1)
    if start >= 0:
        opener = s[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except json.JSONDecodeError:
                        return _NO_JSON
    return _NO_JSON


def _parse_output(content: Any, output_type: Any) -> Any:
    if output_type is None or content is None:
        return content
    data: Any = content
    if isinstance(content, str):
        parsed = _loose_json_parse(content)
        if parsed is _NO_JSON:
            return content  # provider returned prose; hand it back unparsed
        data = parsed
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
    guardrails: list | None = None,
    prepare: Any = None,
) -> tuple[Any, list[Step], str | None]:
    """Run one agent's turns over ``messages`` (mutated in place).

    ``prepare`` (GLR-4): when set, a zero-arg callable that produces the starting messages, run
    **inside** the collector + ``trace(run_id)`` scope, so a retriever's embed call is attributed
    to the run (a collected step, trace-stamped, budgeted) rather than firing before the run opened.
    Its result is appended into ``messages`` (kept empty by the caller for a fresh run).

    Returns ``(output, steps, switched)`` — ``switched`` is a handoff target name if the agent
    transferred control, else ``None``. ``retry`` retries transient model calls; ``on_turn`` (if
    set) is called with ``messages`` after each turn — the checkpoint hook; ``on_step`` (if set) is
    called with each :class:`Step` live as it completes — the progress hook. ``guardrails`` (if set)
    overrides the agent's own list for this run (see :mod:`cendor.sdk._guardrails`).
    """
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=False))
    tools = agent.toolset if tools is None else tools
    resolve = agent.get_tool if resolve is None else resolve
    handoff_targets = handoff_targets or {}
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    gl = _gr.effective(agent, guardrails)
    output: Any = None
    switched: str | None = None

    with ExitStack() as _stack:
        events = _stack.enter_context(_collector(run_id, agent_name=agent.name, on_step=on_step))
        _stack.enter_context(trace(run_id))
        if (
            prepare is not None
        ):  # GLR-4: RAG embed attributed (collected + trace-stamped + budgeted)
            messages.extend(prepare())
        _stack.enter_context(_decision(audit, agent, messages, run_id))
        _gr.gate_input_sync(gl, agent, messages, run_id)  # pre-spend: block raises, redact rewrites
        reasks_left = _gr.effective_reasks(agent, None)
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
                instruction = _gr.originating_instruction(messages)
                for tc in parsed.tool_calls:
                    result = _exec_tool_sync(resolve, tc, gl, agent, run_id, instruction)
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    if tc.name in handoff_targets:
                        switched = handoff_targets[tc.name]
                if on_turn is not None:
                    on_turn(messages)
                if switched:
                    break
                continue
            try:
                output = _gr.gate_output_sync(gl, agent, parsed.content, run_id)  # block raises
            except _gr.GuardrailTripped as exc:  # opt-in bounded re-ask on an output block
                reasks_left, corrective = _gr.reask_step(exc, reasks_left)
                if corrective is None:
                    raise
                messages.append(corrective)
                if on_turn is not None:
                    on_turn(messages)
                continue
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
    guardrails: list | None = None,
    guardrail_mode: str | None = None,
    prepare: Any = None,
) -> tuple[Any, list[Step], str | None]:
    """Async counterpart of :func:`run_agent_sync` (see ``prepare`` there — GLR-4).

    With ``guardrail_mode="parallel"`` the input-stage guardrails run **concurrently with the first
    model call** instead of before it (OpenAI-parity): their latency hides behind the call on the
    pass path. A block still surfaces as ``GuardrailTripped``, but the in-flight call may already
    have completed (and been billed) — the default ``"blocking"`` mode guarantees ``$0`` on a block
    and is the only mode that applies input *redaction* before the call.
    """
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=True), async_=True)
    tools = agent.toolset if tools is None else tools
    resolve = agent.get_tool if resolve is None else resolve
    handoff_targets = handoff_targets or {}
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    gl = _gr.effective(agent, guardrails)
    mode = _gr.effective_mode(agent, guardrail_mode)
    output: Any = None
    switched: str | None = None

    with ExitStack() as _stack:
        events = _stack.enter_context(_collector(run_id, agent_name=agent.name, on_step=on_step))
        _stack.enter_context(trace(run_id))
        if (
            prepare is not None
        ):  # GLR-4: RAG embed attributed (collected + trace-stamped + budgeted)
            messages.extend(prepare())
        _stack.enter_context(_decision(audit, agent, messages, run_id))
        gate_task = None
        if mode == "parallel":  # overlap the input gate with the first model call
            gate_task = _gr.start_input_gate_async(gl, agent, messages, run_id)
        else:
            await _gr.gate_input_async(gl, agent, messages, run_id)  # pre-spend: block / redact
        reasks_left = _gr.effective_reasks(agent, None)
        try:
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
                    kwargs.update(
                        agent.extra
                    )  # provider-param passthrough (tool_choice, reasoning)
                if agent.cache:
                    kwargs = provider.apply_cache(kwargs)
                parsed = provider.parse(await acall_with_retry(create, kwargs, retry))
                if gate_task is not None:  # parallel mode: join the input gate (block raises here)
                    await gate_task
                    gate_task = None
                messages.append(assistant_message(parsed.content, parsed.tool_calls))
                if parsed.tool_calls:
                    # Execute a turn's tool calls concurrently; append results in request order.
                    instruction = _gr.originating_instruction(messages)
                    results = await asyncio.gather(
                        *(
                            _exec_tool_async(resolve, tc, gl, agent, run_id, instruction)
                            for tc in parsed.tool_calls
                        )
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
                try:
                    output = await _gr.gate_output_async(gl, agent, parsed.content, run_id)  # block
                except _gr.GuardrailTripped as exc:  # opt-in bounded re-ask on an output block
                    reasks_left, corrective = _gr.reask_step(exc, reasks_left)
                    if corrective is None:
                        raise
                    messages.append(corrective)
                    if on_turn is not None:
                        on_turn(messages)
                    continue
                if on_turn is not None:
                    on_turn(messages)
                break
        finally:
            # Parallel mode: if the loop exited before joining the input gate (e.g. the first model
            # call raised), don't leak a pending task — cancel and drain its result/exception.
            if gate_task is not None:
                gate_task.cancel()
                try:
                    await gate_task
                except BaseException:  # noqa: BLE001 - the primary error already propagates
                    pass

    return output, [_step(agent.name, e) for e in events], switched


def _exec_tool_sync(
    resolve: ToolResolver,
    tc: Any,
    guardrails: list | None = None,
    agent: Any = None,
    run_id: str = "",
    instruction: str = "",
) -> str:
    gl = guardrails or []
    blocked = _gr.gate_tool_call_sync(gl, agent, tc, run_id, instruction) if gl else None
    if blocked is not None:  # tool_call guardrail blocked — return to the model, don't run the tool
        return blocked
    tool = resolve(tc.name)
    if tool is None:
        return f"[error] unknown tool: {tc.name}"
    try:
        result = _stringify_result(_resolve_sync(tool.invoke(tc.arguments)))
    except Exception as e:  # noqa: BLE001 - surface tool errors to the model, keep the loop alive
        return f"[error] {type(e).__name__}: {e}"
    return _gr.gate_tool_output_sync(gl, agent, tc, result, run_id) if gl else result


async def _exec_tool_async(
    resolve: ToolResolver,
    tc: Any,
    guardrails: list | None = None,
    agent: Any = None,
    run_id: str = "",
    instruction: str = "",
) -> str:
    gl = guardrails or []
    blocked = await _gr.gate_tool_call_async(gl, agent, tc, run_id, instruction) if gl else None
    if blocked is not None:
        return blocked
    tool = resolve(tc.name)
    if tool is None:
        return f"[error] unknown tool: {tc.name}"
    try:
        result = _stringify_result(await tool.ainvoke(tc.arguments))
    except Exception as e:  # noqa: BLE001 - surface tool errors to the model, keep the loop alive
        return f"[error] {type(e).__name__}: {e}"
    return await _gr.gate_tool_output_async(gl, agent, tc, result, run_id) if gl else result


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
        guardrails: list | None = None,
        guardrail_mode: str | None = None,
    ) -> None:
        from .checkpoint import _as_checkpointer

        self.agent = agent
        self.session = session
        self.audit = audit
        self.max_turns = max_turns or agent.max_turns
        self.retry = retry
        self.checkpoint = _as_checkpointer(checkpoint)
        self.on_step = on_step
        self.guardrails = guardrails  # per-run override; None ⇒ use the agent's own list
        self.guardrail_mode = guardrail_mode  # per-run override; None ⇒ use the agent's own mode

    def _start(self, input: Any) -> tuple[list[dict], str, Any, Any]:
        """Resolve starting messages (resuming from a checkpoint if one is unfinished).

        GLR-4: for a fresh run, messages are prepared **inside** the run scopes (so a retriever's
        embed is attributed) — return an empty list to fill + a ``prepare`` thunk; a resume already
        carries its messages (``prepare`` is ``None``)."""
        resume = self.checkpoint.resumable_messages() if self.checkpoint else None
        if resume is not None:
            messages: list[dict] = resume
            prepare = None
        else:
            messages = []
            prepare = lambda: _prepare_messages(self.agent, input, self.session)  # noqa: E731
        run_id = uuid.uuid4().hex

        def on_turn(msgs: list[dict]) -> None:
            if self.checkpoint is not None:
                self.checkpoint.save({"run_id": run_id, "messages": msgs, "done": False})

        return messages, run_id, (on_turn if self.checkpoint is not None else None), prepare

    def _resumed_result(self, state: dict) -> Result:
        """Reconstruct a completed :class:`Result` from a finished (``done``) checkpoint.

        No run is minted and the loop is never entered, so the model and tools are not re-invoked;
        ``steps`` is empty (there are no bus events on a resume). The stored ``output`` is parsed
        to the agent's ``output_type`` for typed results.
        """
        output = state.get("output")
        return Result(
            output=_parse_output(output, self.agent.output_type),
            steps=[],
            trace_id=state.get("run_id") or "",
            conversation_id=getattr(self.session, "id", None) or "",
            agents=[self.agent.name],
            messages=list(state.get("messages") or []),
            incomplete=output is None,
            guardrail_decisions=_gr.snapshot(),  # empty on a resume (the loop never ran)
        )

    def _finish(self, run_id: str, output: Any, steps: list, messages: list[dict]) -> Result:
        if self.session is not None:
            self.session.replace(messages)
            _tel.emit_memory("save", self.session, run_id)  # E-wave: memory.save span
        if self.checkpoint is not None:
            self.checkpoint.save(
                {"run_id": run_id, "messages": messages, "done": True, "output": output}
            )
        return Result(
            output=_parse_output(output, self.agent.output_type),
            steps=steps,
            trace_id=run_id,
            conversation_id=getattr(self.session, "id", None) or "",
            agents=[self.agent.name],
            messages=messages,
            incomplete=output is None,  # no final answer (e.g. max_turns hit mid tool loop)
            guardrail_decisions=_gr.snapshot(),
        )

    def run(self, input: Any) -> Result:
        done = self.checkpoint.finished() if self.checkpoint else None
        if done is not None:  # already finished — return the stored Result, don't re-run the loop
            return self._resumed_result(done)
        messages, run_id, on_turn, prepare = self._start(input)
        with _gr.collecting():  # collect decisions for Result.guardrail_decisions
            with _conversation_scope(self.session):  # G19: propagate the session key to live_spans
                with _scope(self.agent):  # attribute spend + enforce agent.max_usd (pre-flight)
                    output, steps, _ = run_agent_sync(
                        self.agent,
                        messages,
                        run_id,
                        audit=self.audit,
                        max_turns=self.max_turns,
                        retry=self.retry,
                        on_turn=on_turn,
                        on_step=self.on_step,
                        guardrails=self.guardrails,
                        prepare=prepare,
                    )
            return self._finish(run_id, output, steps, messages)

    async def run_async(self, input: Any) -> Result:
        done = self.checkpoint.finished() if self.checkpoint else None
        if done is not None:  # already finished — return the stored Result, don't re-run the loop
            return self._resumed_result(done)
        messages, run_id, on_turn, prepare = self._start(input)
        with _gr.collecting():  # collect decisions for Result.guardrail_decisions
            with _conversation_scope(self.session):  # G19: propagate the session key to live_spans
                with _scope(self.agent):  # attribute spend + enforce agent.max_usd (pre-flight)
                    output, steps, _ = await run_agent_async(
                        self.agent,
                        messages,
                        run_id,
                        audit=self.audit,
                        max_turns=self.max_turns,
                        retry=self.retry,
                        on_turn=on_turn,
                        on_step=self.on_step,
                        guardrails=self.guardrails,
                        guardrail_mode=self.guardrail_mode,
                        prepare=prepare,
                    )
            return self._finish(run_id, output, steps, messages)


# --------------------------------------------------------------------------- streaming


def _stream_resumed_result(agent: Agent, state: dict, session: Session | None) -> Result:
    """S13: reconstruct a completed :class:`Result` from a finished (``done``) streamed checkpoint.

    Mirrors :meth:`Runner._resumed_result` — no run is minted and no turn is streamed, so the model
    and tools are not re-invoked and no prior deltas are re-yielded (S13-D). ``steps`` is empty (no
    bus events on a resume); the stored ``output`` is parsed to the agent's ``output_type``."""
    output = state.get("output")
    return Result(
        output=_parse_output(output, agent.output_type),
        steps=[],
        trace_id=state.get("run_id") or "",
        conversation_id=getattr(session, "id", None) or "",
        agents=list(state.get("seen") or [agent.name]),
        messages=list(state.get("messages") or []),
        incomplete=output is None,
        guardrail_decisions=[],  # empty on a resume (the loop never ran)
    )


def stream_agent_sync(
    agent: Agent,
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
    checkpoint: Any = None,
) -> Any:
    """Yield streaming events for a single agent run (``TextDelta`` / ``ToolCallEvent`` /
    ``ToolResultEvent`` / terminal ``RunComplete``). Providers that reassemble a stream emit text
    incrementally; the rest fall back to a whole-response delta. Same governance seams as ``run()``.

    ``checkpoint`` (S13): a path or ``Checkpointer`` persists the conversation after each turn so a
    crashed stream resumes without re-doing completed work. A finished checkpoint yields a lone
    terminal ``RunComplete`` (no model call, no re-yielded deltas); an unfinished one resumes from
    the saved messages (prior deltas are **not** re-emitted — S13-D)."""
    from .checkpoint import _as_checkpointer

    ckpt = _as_checkpointer(checkpoint)
    if ckpt is not None:
        done = ckpt.finished()
        if done is not None:  # already finished — replay the stored Result, don't re-stream
            yield RunComplete(_stream_resumed_result(agent, done, session))
            return
    resume = ckpt.resumable_messages() if ckpt is not None else None
    run_id = uuid.uuid4().hex  # fresh id even on resume (single-agent parity with Runner._start)
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=False))
    tools = agent.toolset
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    gl = _gr.effective(agent, None)
    output: Any = None
    messages: list[dict] = resume if resume is not None else []

    def _save(done: bool) -> None:  # S13: per-turn checkpoint at each turn boundary
        if ckpt is not None:
            ckpt.save({"run_id": run_id, "messages": messages, "done": done, "output": output})

    # S11: mirror run_agent_sync's ExitStack ordering — prepare INSIDE collector+trace+scope so a
    # retriever's embed is attributed (collected + trace-stamped + budgeted), _decision after it.
    with ExitStack() as _stack:
        _stack.enter_context(_scope(agent))  # attribute spend + enforce agent.max_usd (pre-flight)
        _stack.enter_context(_conversation_scope(session))  # S6: session key → live_spans (G19)
        events = _stack.enter_context(_collector(run_id))
        _stack.enter_context(trace(run_id))
        if resume is None:  # GLR-4/S11: RAG embed attributed to the run
            messages.extend(_prepare_messages(agent, input, session))
        _stack.enter_context(_decision(audit, agent, messages, run_id))
        run_decisions = _stack.enter_context(_gr.collecting())  # for Result.guardrail_decisions
        _gr.gate_input_sync(gl, agent, messages, run_id)  # pre-spend: block raises, redact rewrites
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
                window = _gr.stream_window(agent)  # opt-in incremental output check (0 = off)
                buffered, checked = "", 0
                for chunk in create(**{**kwargs, "stream": True}):
                    chunks.append(chunk)
                    thinking = provider.stream_thinking(chunk)  # GLR-12: reasoning, if streamed
                    if thinking:
                        yield ThinkingDelta(thinking)
                    delta = provider.stream_text(chunk)
                    if delta:
                        yield TextDelta(delta)
                        if window:
                            buffered += delta
                            if len(buffered) - checked >= window:
                                # a block raises mid-stream; already-yielded deltas can't be unshown
                                _gr.gate_stream_partial_sync(gl, agent, buffered, run_id)
                                checked = len(buffered)
                parsed = provider.parse_stream(chunks)
            else:  # provider has no reassembler → one whole-response delta
                parsed = provider.parse(create(**kwargs))
                if parsed.content:
                    yield TextDelta(parsed.content)
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                instruction = _gr.originating_instruction(messages)
                for tc in parsed.tool_calls:
                    yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                    result = _exec_tool_sync(agent.get_tool, tc, gl, agent, run_id, instruction)
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    yield ToolResultEvent(tc.name, result)
                _save(False)  # S13: checkpoint after the tool turn
                continue
            # output stage runs after the deltas already streamed — a block raises here (post-hoc)
            output = _gr.gate_output_sync(gl, agent, parsed.content, run_id)
            _save(False)  # S13: checkpoint after the answering turn (before the run scope unwinds)
            break

    if session is not None:
        session.replace(messages)
        _tel.emit_memory("save", session, run_id)  # E-wave: memory.save span
    _save(True)  # S13: final done save
    steps = [_step(agent.name, e) for e in events]
    yield RunComplete(
        Result(
            output=_parse_output(output, agent.output_type),
            steps=steps,
            trace_id=run_id,
            conversation_id=getattr(session, "id", None) or "",  # S6
            agents=[agent.name],
            messages=messages,
            incomplete=output is None,
            guardrail_decisions=list(run_decisions),
        )
    )


async def stream_agent_async(
    agent: Agent,
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
    checkpoint: Any = None,
) -> Any:
    """Async counterpart of :func:`stream_agent_sync` (``async for`` over the same events); same
    ``checkpoint`` (S13) semantics."""
    from .checkpoint import _as_checkpointer

    ckpt = _as_checkpointer(checkpoint)
    if ckpt is not None:
        done = ckpt.finished()
        if done is not None:  # already finished — replay the stored Result, don't re-stream
            yield RunComplete(_stream_resumed_result(agent, done, session))
            return
    resume = ckpt.resumable_messages() if ckpt is not None else None
    run_id = uuid.uuid4().hex
    provider = agent.provider_impl
    create = provider.create_method(_client_for(agent, async_=True), async_=True)
    tools = agent.toolset
    json_mode = agent.output_type is not None
    turns = max_turns or agent.max_turns
    gl = _gr.effective(agent, None)
    output: Any = None
    messages: list[dict] = resume if resume is not None else []

    def _save(done: bool) -> None:  # S13: per-turn checkpoint at each turn boundary
        if ckpt is not None:
            ckpt.save({"run_id": run_id, "messages": messages, "done": done, "output": output})

    with ExitStack() as _stack:
        _stack.enter_context(_scope(agent))  # attribute spend + enforce agent.max_usd (pre-flight)
        _stack.enter_context(_conversation_scope(session))  # S6: session key → live_spans (G19)
        events = _stack.enter_context(_collector(run_id))
        _stack.enter_context(trace(run_id))
        if resume is None:  # GLR-4/S11: RAG embed attributed to the run
            messages.extend(_prepare_messages(agent, input, session))
        _stack.enter_context(_decision(audit, agent, messages, run_id))
        run_decisions = _stack.enter_context(_gr.collecting())  # for Result.guardrail_decisions
        await _gr.gate_input_async(gl, agent, messages, run_id)  # pre-spend: block raises / redact
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
                window = _gr.stream_window(agent)  # opt-in incremental output check (0 = off)
                buffered, checked = "", 0
                stream = await create(**{**kwargs, "stream": True})
                async for chunk in stream:
                    chunks.append(chunk)
                    thinking = provider.stream_thinking(chunk)  # GLR-12: reasoning, if streamed
                    if thinking:
                        yield ThinkingDelta(thinking)
                    delta = provider.stream_text(chunk)
                    if delta:
                        yield TextDelta(delta)
                        if window:
                            buffered += delta
                            if len(buffered) - checked >= window:
                                # a block raises mid-stream; shown deltas can't be unshown
                                await _gr.gate_stream_partial_async(gl, agent, buffered, run_id)
                                checked = len(buffered)
                parsed = provider.parse_stream(chunks)
            else:
                parsed = provider.parse(await create(**kwargs))
                if parsed.content:
                    yield TextDelta(parsed.content)
            messages.append(assistant_message(parsed.content, parsed.tool_calls))
            if parsed.tool_calls:
                instruction = _gr.originating_instruction(messages)
                for tc in parsed.tool_calls:
                    yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                    result = await _exec_tool_async(
                        agent.get_tool, tc, gl, agent, run_id, instruction
                    )
                    messages.append(tool_result_message(tc.id, tc.name, result))
                    yield ToolResultEvent(tc.name, result)
                _save(False)  # S13: checkpoint after the tool turn
                continue
            # output stage runs after the deltas already streamed — a block raises here (post-hoc)
            output = await _gr.gate_output_async(gl, agent, parsed.content, run_id)
            _save(False)  # S13: checkpoint after the answering turn (before the scope unwinds)
            break

    if session is not None:
        session.replace(messages)
        _tel.emit_memory("save", session, run_id)  # E-wave: memory.save span
    _save(True)  # S13: final done save
    steps = [_step(agent.name, e) for e in events]
    yield RunComplete(
        Result(
            output=_parse_output(output, agent.output_type),
            steps=steps,
            trace_id=run_id,
            conversation_id=getattr(session, "id", None) or "",  # S6
            agents=[agent.name],
            messages=messages,
            incomplete=output is None,
            guardrail_decisions=list(run_decisions),
        )
    )


def stream_agents_sync(
    agents: list[Agent],
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
    checkpoint: Any = None,
) -> Any:
    """Stream a **multi-agent** (handoff) run as events; one terminal ``RunComplete`` carries the
    aggregate ``Result``. Mirrors :func:`run_agents`' segment loop, streaming each active agent's
    turns and switching when it calls a ``transfer_to_<peer>`` tool.

    ``checkpoint`` (S13): per-turn **and** per-segment saves persist ``{active, seen, seg}`` so a
    crashed team stream resumes at the segment/turn it left off (prior deltas are not re-emitted —
    S13-D); a finished checkpoint yields a lone terminal ``RunComplete``."""
    from .checkpoint import _as_checkpointer
    from .orchestration import _effective

    registry = {a.name: a for a in agents}
    ckpt = _as_checkpointer(checkpoint)
    if ckpt is not None:
        done = ckpt.finished()
        if done is not None:  # already finished — replay the stored Result, don't re-stream
            final = registry.get(done.get("active") or "", agents[0])
            yield RunComplete(_stream_resumed_result(final, done, session))
            return
    state = ckpt.load() if ckpt is not None else None
    if state is not None and not state.get("done"):  # S13: resume an unfinished team stream
        parent = state.get("run_id") or uuid.uuid4().hex
        messages: list[dict] = list(state.get("messages") or [])
        active = registry.get(state.get("active") or "", agents[0])
        seen: list[str] = list(state.get("seen") or [])
        start_seg = int(state.get("seg", 0))
        prepared = True  # a resumed conversation already carries its prepared messages
    else:
        parent = uuid.uuid4().hex
        messages = []
        active = agents[0]
        seen = []
        start_seg = 0
        prepared = False
    steps: list = []
    output: Any = None
    run_decisions: list = []  # accumulated across segments for Result.guardrail_decisions
    max_segments = 2 * len(registry) + 2
    seg = start_seg

    def _save(done: bool, seg_no: int, active_name: str) -> None:  # S13
        if ckpt is not None:
            ckpt.save(
                {
                    "run_id": parent,
                    "messages": messages,
                    "active": active_name,
                    "seen": seen,
                    "seg": seg_no,
                    "done": done,
                    "output": output,
                }
            )

    with _conversation_scope(session):  # S6: session key → live_spans (G19), whole team run
        for seg in range(start_seg, max_segments):
            if active.name not in seen:
                seen.append(active.name)
            child = f"{parent}:{active.name}#{seg}"
            tools, tool_map, transfer_map = _effective(active, registry)
            provider = active.provider_impl
            create = provider.create_method(_client_for(active, async_=False))
            json_mode = active.output_type is not None
            turns = max_turns or active.max_turns
            gl = _gr.effective(active, None)
            switched: str | None = None
            with ExitStack() as _stack:
                _stack.enter_context(_scope(active))
                events = _stack.enter_context(_collector(child, agent_name=active.name))
                _stack.enter_context(trace(child))
                if not prepared:  # GLR-4/S11: prepare inside the first segment's scope
                    messages.extend(_prepare_messages(agents[0], input, session))
                    prepared = True
                _stack.enter_context(_decision(audit, active, messages, child))
                seg_decisions = _stack.enter_context(_gr.collecting())  # accumulated below
                _gr.gate_input_sync(gl, active, messages, child)  # pre-spend per segment
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
                            thinking = provider.stream_thinking(chunk)  # GLR-12: reasoning
                            if thinking:
                                yield ThinkingDelta(thinking)
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
                        instruction = _gr.originating_instruction(messages)
                        for tc in parsed.tool_calls:
                            yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                            result = _exec_tool_sync(
                                tool_map.get, tc, gl, active, child, instruction
                            )
                            messages.append(tool_result_message(tc.id, tc.name, result))
                            yield ToolResultEvent(tc.name, result)
                            if tc.name in transfer_map:
                                switched = transfer_map[tc.name]
                        _save(False, seg, active.name)  # S13: checkpoint after the tool turn
                        if switched:
                            break
                        continue
                    output = _gr.gate_output_sync(gl, active, parsed.content, child)
                    _save(False, seg, active.name)  # S13: checkpoint after the answering turn
                    break
            steps.extend(_step(active.name, e) for e in events)
            run_decisions.extend(seg_decisions)
            if switched and switched in registry:
                _tel.emit_handoff(active.name, switched, seg, f"transfer_to_{switched}", parent)
                active = registry[switched]
                _save(False, seg + 1, active.name)  # S13: checkpoint the handoff
                continue
            break

    if session is not None:
        session.replace(messages)
        _tel.emit_memory("save", session, parent)  # E-wave: memory.save span
    _save(True, seg, active.name)  # S13: final done save
    yield RunComplete(
        Result(
            output=_parse_output(output, active.output_type),
            steps=steps,
            trace_id=parent,
            conversation_id=getattr(session, "id", None) or "",  # S6
            agents=seen,
            messages=messages,
            incomplete=output is None,
            guardrail_decisions=list(run_decisions),
        )
    )


async def stream_agents_async(
    agents: list[Agent],
    input: Any,
    *,
    session: Session | None = None,
    audit: Any = None,
    max_turns: int | None = None,
    checkpoint: Any = None,
) -> Any:
    """Async counterpart of :func:`stream_agents_sync` (``async for`` over the events); same
    per-segment ``checkpoint`` (S13) semantics."""
    from .checkpoint import _as_checkpointer
    from .orchestration import _effective

    registry = {a.name: a for a in agents}
    ckpt = _as_checkpointer(checkpoint)
    if ckpt is not None:
        done = ckpt.finished()
        if done is not None:  # already finished — replay the stored Result, don't re-stream
            final = registry.get(done.get("active") or "", agents[0])
            yield RunComplete(_stream_resumed_result(final, done, session))
            return
    state = ckpt.load() if ckpt is not None else None
    if state is not None and not state.get("done"):  # S13: resume an unfinished team stream
        parent = state.get("run_id") or uuid.uuid4().hex
        messages: list[dict] = list(state.get("messages") or [])
        active = registry.get(state.get("active") or "", agents[0])
        seen: list[str] = list(state.get("seen") or [])
        start_seg = int(state.get("seg", 0))
        prepared = True
    else:
        parent = uuid.uuid4().hex
        messages = []
        active = agents[0]
        seen = []
        start_seg = 0
        prepared = False
    steps: list = []
    output: Any = None
    run_decisions: list = []  # accumulated across segments for Result.guardrail_decisions
    max_segments = 2 * len(registry) + 2
    seg = start_seg

    def _save(done: bool, seg_no: int, active_name: str) -> None:  # S13
        if ckpt is not None:
            ckpt.save(
                {
                    "run_id": parent,
                    "messages": messages,
                    "active": active_name,
                    "seen": seen,
                    "seg": seg_no,
                    "done": done,
                    "output": output,
                }
            )

    with _conversation_scope(session):  # S6: session key → live_spans (G19), whole team run
        for seg in range(start_seg, max_segments):
            if active.name not in seen:
                seen.append(active.name)
            child = f"{parent}:{active.name}#{seg}"
            tools, tool_map, transfer_map = _effective(active, registry)
            provider = active.provider_impl
            create = provider.create_method(_client_for(active, async_=True), async_=True)
            json_mode = active.output_type is not None
            turns = max_turns or active.max_turns
            gl = _gr.effective(active, None)
            switched: str | None = None
            with ExitStack() as _stack:
                _stack.enter_context(_scope(active))
                events = _stack.enter_context(_collector(child, agent_name=active.name))
                _stack.enter_context(trace(child))
                if not prepared:  # GLR-4/S11: prepare inside the first segment's scope
                    messages.extend(_prepare_messages(agents[0], input, session))
                    prepared = True
                _stack.enter_context(_decision(audit, active, messages, child))
                seg_decisions = _stack.enter_context(_gr.collecting())  # accumulated below
                await _gr.gate_input_async(gl, active, messages, child)  # pre-spend per segment
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
                            thinking = provider.stream_thinking(chunk)  # GLR-12: reasoning
                            if thinking:
                                yield ThinkingDelta(thinking)
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
                        instruction = _gr.originating_instruction(messages)
                        for tc in parsed.tool_calls:
                            yield ToolCallEvent(tc.name, tc.arguments, tc.id)
                            result = await _exec_tool_async(
                                tool_map.get, tc, gl, active, child, instruction
                            )
                            messages.append(tool_result_message(tc.id, tc.name, result))
                            yield ToolResultEvent(tc.name, result)
                            if tc.name in transfer_map:
                                switched = transfer_map[tc.name]
                        _save(False, seg, active.name)  # S13: checkpoint after the tool turn
                        if switched:
                            break
                        continue
                    output = await _gr.gate_output_async(gl, active, parsed.content, child)
                    _save(False, seg, active.name)  # S13: checkpoint after the answering turn
                    break
            steps.extend(_step(active.name, e) for e in events)
            run_decisions.extend(seg_decisions)
            if switched and switched in registry:
                _tel.emit_handoff(active.name, switched, seg, f"transfer_to_{switched}", parent)
                active = registry[switched]
                _save(False, seg + 1, active.name)  # S13: checkpoint the handoff
                continue
            break

    if session is not None:
        session.replace(messages)
        _tel.emit_memory("save", session, parent)  # E-wave: memory.save span
    _save(True, seg, active.name)  # S13: final done save
    yield RunComplete(
        Result(
            output=_parse_output(output, active.output_type),
            steps=steps,
            trace_id=parent,
            conversation_id=getattr(session, "id", None) or "",  # S6
            agents=seen,
            messages=messages,
            incomplete=output is None,
            guardrail_decisions=list(run_decisions),
        )
    )


# ------------------------------------------------------------------- stream isolation (GLR-9)


def _drive_sync(ctx: Any, gen: Any) -> Any:
    """GLR-9: advance a scoped stream generator inside a **copied context**, yielding each event out
    in the consumer's own context. The run scopes (`trace`/`track`/budget/audit/collector) that the
    inner generator enters in its body therefore live in ``ctx``, never leaking into the consumer
    between deltas — so a consumer's own instrumented call between two events is NOT attributed to
    the run. Abandonment closes the inner generator inside ``ctx``, so its scopes unwind there."""
    try:
        while True:
            try:
                ev = ctx.run(next, gen)
            except StopIteration:
                return
            yield ev
    finally:
        ctx.run(gen.close)


async def _drive_async(agen: Any) -> Any:
    """GLR-9 (async): run the scoped async generator on a background ``asyncio.Task`` (whose context
    is a ``copy_context()`` snapshot taken at task creation — so user-opened cassette/track/OTel
    scopes propagate IN), relaying its events through a queue that the public async generator drains
    **outside** every run scope. The scoped body's contextvar sets stay confined to the producer
    task; the consumer never sees them between deltas. Mirrors the TS producer+queue design."""
    import asyncio

    queue: asyncio.Queue = asyncio.Queue()

    async def _producer() -> None:
        try:
            async for ev in agen:
                await queue.put(("item", ev))
        except BaseException as exc:  # noqa: BLE001 - relay to the consumer, don't crash the task
            await queue.put(("error", exc))
        else:
            await queue.put(("done", None))

    task = asyncio.ensure_future(_producer())  # Task creation snapshots the current context
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "item":
                yield payload
            elif kind == "error":
                raise payload
            else:  # "done"
                return
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001 - cancellation / producer teardown is best-effort
            pass


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
        guardrails: list | None = None,
        guardrail_mode: str | None = None,
    ) -> Result:
        """Run ``agent`` on ``input`` and return a :class:`Result` (sync).

        ```python
        from cendor.sdk import Agent, run
        agent = Agent(name="assistant", model="gpt-4o")
        result = run(agent, "Summarize the meeting notes.")
        print(result.output)
        ```

        Async is ``await run.aio(agent, input)`` (same signature). Streaming is ``run.stream(...)``
        (sync generator) and ``run.astream(...)`` (async iterator) — the async-stream method is
        ``run.astream``, **not** ``run.stream.aio``.

        ``on_step`` (if set) is called with each :class:`~cendor.sdk.result.Step` live as the
        run progresses — a public progress hook, complementing the post-hoc ``Result.steps``.
        ``guardrails`` overrides the agent's own list for this run (for a team, it replaces every
        segment's list); omit it to use each agent's ``Agent(guardrails=[…])``. ``guardrail_mode``
        (``"blocking"`` / ``"parallel"``) is honoured on the async path (``run.aio``); a sync run
        always blocks."""
        if isinstance(agent, (list, tuple)):
            from .orchestration import run_agents

            with _gr.collecting():  # run_agents builds its Result inside this scope
                return run_agents(
                    list(agent),
                    input,
                    audit=audit,
                    max_turns=max_turns,
                    session=session,
                    checkpoint=checkpoint,
                    on_step=on_step,
                    guardrails=guardrails,
                )
        return Runner(
            agent,
            session=session,
            audit=audit,
            max_turns=max_turns,
            retry=retry,
            checkpoint=checkpoint,
            on_step=on_step,
            guardrails=guardrails,
            guardrail_mode=guardrail_mode,
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
        guardrails: list | None = None,
        guardrail_mode: str | None = None,
    ) -> Result:
        """Async counterpart of :meth:`__call__`; ``on_step`` fires per :class:`Step` live.
        ``guardrails`` overrides the agent's own list for this run; ``guardrail_mode``
        (``"blocking"`` / ``"parallel"``) overrides the agent's mode (single-agent runs)."""
        if isinstance(agent, (list, tuple)):
            from .orchestration import run_agents_async

            with _gr.collecting():  # run_agents_async builds its Result inside this scope
                return await run_agents_async(
                    list(agent),
                    input,
                    audit=audit,
                    max_turns=max_turns,
                    session=session,
                    checkpoint=checkpoint,
                    on_step=on_step,
                    guardrails=guardrails,
                )
        return await Runner(
            agent,
            session=session,
            audit=audit,
            max_turns=max_turns,
            retry=retry,
            checkpoint=checkpoint,
            on_step=on_step,
            guardrails=guardrails,
            guardrail_mode=guardrail_mode,
        ).run_async(input)

    def stream(
        self,
        agent: Any,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
        checkpoint: Any = None,
    ) -> Any:
        """Stream a run as events (sync generator). Terminal event is ``RunComplete`` carrying the
        ``Result``. A single agent streams its turns; a list streams a multi-agent handoff run,
        switching active agents on a ``transfer_to_<peer>`` call — one terminal ``RunComplete``.

        ``checkpoint`` (S13, a path or ``Checkpointer``) persists the conversation as the stream
        progresses so a crashed stream resumes without re-doing completed work (prior deltas are not
        re-emitted); a finished checkpoint yields a lone terminal ``RunComplete``."""
        # GLR-9: copy the caller's context ONCE, here at stream() call time (so user-opened
        # cassette/track/OTel scopes propagate in), then drive the scoped generator inside it.
        ctx = copy_context()
        if isinstance(agent, (list, tuple)):
            gen = stream_agents_sync(
                list(agent),
                input,
                session=session,
                audit=audit,
                max_turns=max_turns,
                checkpoint=checkpoint,
            )
        else:
            gen = stream_agent_sync(
                agent,
                input,
                session=session,
                audit=audit,
                max_turns=max_turns,
                checkpoint=checkpoint,
            )
        return _drive_sync(ctx, gen)

    def astream(
        self,
        agent: Any,
        input: Any,
        *,
        session: Session | None = None,
        audit: Any = None,
        max_turns: int | None = None,
        checkpoint: Any = None,
    ) -> Any:
        """Async counterpart of :meth:`stream` (``async for`` over the events); a list streams a
        multi-agent handoff run. Same ``checkpoint`` (S13) semantics."""
        # GLR-9: relay the scoped async generator through a producer Task + queue, so its run scopes
        # stay confined to the task and never leak into the consumer between deltas.
        if isinstance(agent, (list, tuple)):
            agen = stream_agents_async(
                list(agent), input, session=session, audit=audit, max_turns=max_turns
            )
        else:
            agen = stream_agent_async(
                agent, input, session=session, audit=audit, max_turns=max_turns
            )
        return _drive_async(agen)


run = _Run()
