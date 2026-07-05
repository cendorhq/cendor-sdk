# Multi-agent orchestration

> Phase 2. Handoff, supervisor/router, sequential & parallel — with the correlation that was
> *impossible beneath frameworks*: a whole multi-agent trajectory as **one governed, correlated
> tree**.

Every multi-agent run gets one parent `run_id`. Each agent segment runs under a nested child trace
id (`{run_id}:{agent}#i`, a `trace_id` that starts with the parent), so:

- `result.trace_id` is the parent id; every `step.trace_id` starts with it → one tree.
- each `step.agent` names the agent that produced it → distinct agents are distinguishable.
- per-agent governance rides the same seams: each segment is wrapped in `track(agent=…)` and, when
  the agent sets `max_usd`, a per-agent `budget(...)`; and each segment opens its own audit
  `decision()` on a shared `AuditLog` — **one verifiable chain**.

## Handoff

An agent transfers control to a named peer via a synthetic `transfer_to_<peer>` tool. The
conversation is canonical (provider-agnostic), so **handoff works across providers** — an OpenAI
planner can hand off to an Anthropic writer with no rewrite.

```python
from cendor.sdk import Agent, run

writer = Agent(name="writer", model="claude-opus-4-8", instructions="Write the brief.")
planner = Agent(
    name="planner", model="gpt-4o",
    instructions="Plan, then hand off to the writer.",
    handoffs=["writer"],          # or handoffs=[handoff("writer")]
)

# A list means a handoff team: the first agent is the entry point, the rest are reachable peers.
result = run([planner, writer], "Research X and write a brief")
print(result.output)              # the writer's final answer
print(result.agents)              # ["planner", "writer"]
```

## Supervisor / router

A coordinator agent routes to sub-agents by handoff:

```python
from cendor.sdk import Agent, supervisor, AuditLog

coordinator = Agent(name="coordinator", model="gpt-4o", instructions="Route to a specialist.")
researcher  = Agent(name="researcher",  model="gpt-4o", instructions="Do the research.")
writer      = Agent(name="writer",      model="claude-opus-4-8", instructions="Write it up.")

log = AuditLog(system="research-team", risk_tier="high", path="team.jsonl")
result = supervisor(coordinator, [researcher, writer], "Investigate X and write it up", audit=log)

# One correlated audit trail: a decision per agent segment, every llm_call/tool_call chained.
```

## Sequential & parallel pipelines

```python
from cendor.sdk import sequential, parallel, parallel_async

# Pipe each agent's output into the next.
result = sequential([drafter, editor, factchecker], "Write about X")
print(result.output)                     # the last agent's output

# Fan out over the same input; result.output is {agent_name: output}.
result = parallel([summarizer_a, summarizer_b], "Summarize this document")
print(result.output)                     # {"summarizer_a": ..., "summarizer_b": ...}

# Real concurrency (async):
result = await parallel_async([a, b, c], "Same task, three takes")
```

## Per-agent budgets & attribution

```python
from cendor.sdk import Agent, run, report

# Cap a single agent's spend; the orchestrator enforces it around that agent's segment.
expensive = Agent(name="deep", model="claude-opus-4-8", instructions="Think hard.", max_usd=0.50)
cheap     = Agent(name="fast", model="gpt-4o-mini",     instructions="Be quick.")

run([cheap, expensive], "...")

# Spend is auto-attributed by agent (track(agent=...)):
report(group_by=["agent"]).assert_under(usd=1.00, agent="deep")
```

## Session memory (in-memory + local persistence)

```python
from cendor.sdk import Session, run

session = Session()
run(agent, "My name is Alice.", session=session)
run(agent, "What's my name?", session=session)   # remembers within the process

session.save("chat.json")                         # local-first, no server
resumed = Session.load("chat.json")               # resume later
```

## Correlation model at a glance

| Concept | Where it lives |
|---|---|
| Parent run id | `result.trace_id` |
| Per-agent child id | `step.trace_id` (starts with the parent) |
| Which agent produced a step | `step.agent` |
| Audit correlation | one `decision()` per agent segment on the shared `AuditLog` |
| Spend attribution | `track(agent=…)` per segment → `report(group_by=["agent"])` |
