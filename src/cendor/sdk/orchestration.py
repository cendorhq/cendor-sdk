"""Multi-agent orchestration: handoff, supervisor/router, sequential & parallel (plan §7 Phase 2).

The correlation that was *impossible beneath frameworks* is first-class here. A multi-agent run
gets one parent ``run_id``; each agent segment runs under a nested child trace id
(``{run_id}:{agent}#i``, a ``trace_id`` starting with the parent) — so the whole run is one **tree**
every ``Step`` carries its agent's name and its child ``trace_id``. Per-agent governance rides the
same seams: each segment is wrapped in ``track(agent=…)`` and, when the agent sets ``max_usd``, a
per-agent ``budget(...)``; and each segment opens its own audit ``decision()`` on a shared
``AuditLog`` — one verifiable chain, distinct agents distinguishable.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any

from .agent import Agent
from .result import Result
from .runner import _parse_output, _prepare_messages, run_agent_async, run_agent_sync
from .tools import Tool


@dataclass
class Handoff:
    """A declared handoff target — an agent name (or an ``Agent``)."""

    target: str


def handoff(agent_or_name: Agent | str) -> Handoff:
    """Declare a handoff target for an ``Agent(handoffs=[...])`` list.

    ```python
    planner = Agent(name="planner", model="gpt-4o", handoffs=[handoff("writer")])
    ```
    """
    name = agent_or_name.name if isinstance(agent_or_name, Agent) else str(agent_or_name)
    return Handoff(name)


def _handoff_name(h: Any) -> str:
    if isinstance(h, Handoff):
        return h.target
    if isinstance(h, Agent):
        return h.name
    return str(h)


def _transfer_tool(target: str) -> Tool:
    """A synthetic tool the model calls to hand control to a peer agent."""

    def transfer(reason: str = "") -> str:
        return f"Transferred to {target}."

    return Tool(
        name=f"transfer_to_{target}",
        description=f"Hand off the conversation to the '{target}' agent when it is better suited.",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "why you are handing off"}},
            "additionalProperties": False,
        },
        func=transfer,
        is_async=False,
    )


def _effective(agent: Agent, registry: dict[str, Agent]) -> tuple[list[Tool], dict, dict[str, str]]:
    """Agent tools + synthetic transfer tools for its reachable handoff peers."""
    tools: list[Tool] = list(agent.toolset)
    transfer_map: dict[str, str] = {}
    for h in agent.handoffs:
        name = _handoff_name(h)
        if name in registry and name != agent.name:
            tt = _transfer_tool(name)
            tools.append(tt)
            transfer_map[tt.name] = name
    tool_map = {t.name: t for t in tools}
    return tools, tool_map, transfer_map


@contextmanager
def _scope(agent: Agent) -> Any:
    """Per-agent governance: attribute spend to the agent + enforce its ``max_usd`` cap if set."""
    from cendor.tokenguard import budget, track

    with ExitStack() as stack:
        stack.enter_context(track(agent=agent.name))
        if agent.max_usd is not None:
            stack.enter_context(budget(usd=agent.max_usd, on_exceed="block"))
        yield


def _user_message(value: Any) -> dict:
    return {"role": "user", "content": value if isinstance(value, str) else str(value)}


# --------------------------------------------------------------------------- handoff / supervisor


def _resume_state(ckpt: Any, agents: list[Agent], input: Any, session: Any) -> tuple:
    """Resolve (parent, messages, active, seen, start_seg) from an unfinished checkpoint."""
    registry = {a.name: a for a in agents}
    state = ckpt.load() if ckpt else None
    if state and not state.get("done"):
        return (
            state.get("run_id") or uuid.uuid4().hex,
            list(state.get("messages") or []),
            registry.get(state.get("active"), agents[0]),
            list(state.get("seen") or []),
            int(state.get("seg", 0)),
        )
    return uuid.uuid4().hex, _prepare_messages(agents[0], input, session), agents[0], [], 0


def run_agents(
    agents: list[Agent],
    input: Any,
    *,
    audit: Any = None,
    max_turns: int | None = None,
    session: Any = None,
    checkpoint: Any = None,
) -> Result:
    """Run a handoff/supervised team: ``agents[0]`` is the entry; peers are reached by handoff.

    Control transfers when an agent calls a ``transfer_to_<peer>`` tool; the conversation (canonical
    provider-agnostic history) carries across the switch — so handoff works *across providers*.
    ``checkpoint`` (a path or ``Checkpointer``) persists the trajectory after each turn/segment so a
    crashed multi-agent run resumes without re-doing completed work.
    """
    from .checkpoint import _as_checkpointer

    ckpt = _as_checkpointer(checkpoint)
    registry = {a.name: a for a in agents}
    parent, messages, active, seen, start_seg = _resume_state(ckpt, agents, input, session)
    steps: list = []
    output: Any = None
    max_segments = 2 * len(registry) + 2
    seg = start_seg

    def _save(done: bool, seg_no: int, active_name: str, out: Any = None) -> None:
        if ckpt is not None:
            ckpt.save(
                {
                    "run_id": parent,
                    "messages": messages,
                    "active": active_name,
                    "seen": seen,
                    "seg": seg_no,
                    "done": done,
                    "output": out,
                }
            )

    for seg in range(start_seg, max_segments):
        if active.name not in seen:
            seen.append(active.name)
        child = f"{parent}:{active.name}#{seg}"
        tools, tool_map, transfer_map = _effective(active, registry)
        on_turn = (lambda _m, _s=seg, _a=active.name: _save(False, _s, _a)) if ckpt else None
        with _scope(active):
            output, seg_steps, switched = run_agent_sync(
                active,
                messages,
                child,
                audit=audit,
                max_turns=max_turns,
                tools=tools,
                resolve=tool_map.get,
                handoff_targets=transfer_map,
                on_turn=on_turn,
            )
        steps.extend(seg_steps)
        if switched and switched in registry:
            active = registry[switched]
            _save(False, seg + 1, active.name)
            continue
        break

    if session is not None:
        session.replace(messages)
    _save(True, seg, active.name, output)
    return Result(
        output=_parse_output(output, active.output_type),
        steps=steps,
        trace_id=parent,
        agents=seen,
        messages=messages,
        incomplete=output is None,
    )


async def run_agents_async(
    agents: list[Agent],
    input: Any,
    *,
    audit: Any = None,
    max_turns: int | None = None,
    session: Any = None,
    checkpoint: Any = None,
) -> Result:
    """Async counterpart of :func:`run_agents` (same segment/turn checkpointing)."""
    from .checkpoint import _as_checkpointer

    ckpt = _as_checkpointer(checkpoint)
    registry = {a.name: a for a in agents}
    parent, messages, active, seen, start_seg = _resume_state(ckpt, agents, input, session)
    steps: list = []
    output: Any = None
    max_segments = 2 * len(registry) + 2
    seg = start_seg

    def _save(done: bool, seg_no: int, active_name: str, out: Any = None) -> None:
        if ckpt is not None:
            ckpt.save(
                {
                    "run_id": parent,
                    "messages": messages,
                    "active": active_name,
                    "seen": seen,
                    "seg": seg_no,
                    "done": done,
                    "output": out,
                }
            )

    for seg in range(start_seg, max_segments):
        if active.name not in seen:
            seen.append(active.name)
        child = f"{parent}:{active.name}#{seg}"
        tools, tool_map, transfer_map = _effective(active, registry)
        on_turn = (lambda _m, _s=seg, _a=active.name: _save(False, _s, _a)) if ckpt else None
        with _scope(active):
            output, seg_steps, switched = await run_agent_async(
                active,
                messages,
                child,
                audit=audit,
                max_turns=max_turns,
                tools=tools,
                resolve=tool_map.get,
                handoff_targets=transfer_map,
                on_turn=on_turn,
            )
        steps.extend(seg_steps)
        if switched and switched in registry:
            active = registry[switched]
            _save(False, seg + 1, active.name)
            continue
        break

    if session is not None:
        session.replace(messages)
    _save(True, seg, active.name, output)
    return Result(
        output=_parse_output(output, active.output_type),
        steps=steps,
        trace_id=parent,
        agents=seen,
        messages=messages,
        incomplete=output is None,
    )


def supervisor(
    coordinator: Agent,
    agents: list[Agent],
    input: Any,
    *,
    audit: Any = None,
    max_turns: int | None = None,
) -> Result:
    """A coordinator agent that routes to sub-agents via handoff (router pattern)."""
    existing = [_handoff_name(h) for h in coordinator.handoffs]
    names = [a.name for a in agents]
    coordinator.handoffs = list(dict.fromkeys([*existing, *names]))
    return run_agents([coordinator, *agents], input, audit=audit, max_turns=max_turns)


# --------------------------------------------------------------------------- sequential / parallel


def sequential(
    agents: list[Agent], input: Any, *, audit: Any = None, max_turns: int | None = None
) -> Result:
    """Run agents in order, piping each agent's output as the next agent's input."""
    parent = uuid.uuid4().hex
    steps: list = []
    seen: list[str] = []
    messages_all: list[dict] = []
    current: Any = input
    output: Any = None
    for i, agent in enumerate(agents):
        seen.append(agent.name)
        child = f"{parent}:{agent.name}#{i}"
        msgs = [_user_message(current)]
        with _scope(agent):
            output, seg_steps, _ = run_agent_sync(
                agent, msgs, child, audit=audit, max_turns=max_turns
            )
        steps.extend(seg_steps)
        messages_all.extend(msgs)
        current = output
    return Result(output=output, steps=steps, trace_id=parent, agents=seen, messages=messages_all)


def parallel(
    agents: list[Agent], input: Any, *, audit: Any = None, max_turns: int | None = None
) -> Result:
    """Run agents independently on the same input; ``result.output`` is ``{agent_name: output}``.

    Sync execution is sequential; use :func:`parallel_async` for real concurrency.
    """
    parent = uuid.uuid4().hex
    steps: list = []
    outputs: dict[str, Any] = {}
    for i, agent in enumerate(agents):
        child = f"{parent}:{agent.name}#{i}"
        msgs = [_user_message(input)]
        with _scope(agent):
            out, seg_steps, _ = run_agent_sync(agent, msgs, child, audit=audit, max_turns=max_turns)
        steps.extend(seg_steps)
        outputs[agent.name] = out
    return Result(
        output=outputs, steps=steps, trace_id=parent, agents=[a.name for a in agents], messages=[]
    )


async def parallel_async(
    agents: list[Agent], input: Any, *, audit: Any = None, max_turns: int | None = None
) -> Result:
    """Run agents concurrently on the same input (real fan-out); output is ``{name: output}``."""
    parent = uuid.uuid4().hex

    async def _one(i: int, agent: Agent) -> tuple[str, Any, list]:
        child = f"{parent}:{agent.name}#{i}"
        msgs = [_user_message(input)]
        with _scope(agent):
            out, seg_steps, _ = await run_agent_async(
                agent, msgs, child, audit=audit, max_turns=max_turns
            )
        return agent.name, out, seg_steps

    results = await asyncio.gather(*(_one(i, a) for i, a in enumerate(agents)))
    steps: list = []
    outputs: dict[str, Any] = {}
    for name, out, seg_steps in results:
        outputs[name] = out
        steps.extend(seg_steps)
    return Result(
        output=outputs, steps=steps, trace_id=parent, agents=[a.name for a in agents], messages=[]
    )
