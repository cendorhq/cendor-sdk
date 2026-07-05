# Production hardening

> Phase 4. The "safe for real workloads" layer: retries with backoff, checkpointed/resumable runs,
> and durable local memory — all local-first, no new failure modes the provider SDK lacks.

## Retries & backoff

Pass a `RetryPolicy` to retry **transient** model-call failures (timeouts, connection errors, rate
limits, 5xx) with exponential backoff. Governance decisions (`BudgetExceeded`, `PolicyViolation`)
are **never** retried — they're terminal by design.

```python
from cendor.sdk import Agent, run, RetryPolicy

agent = Agent(name="assistant", model="gpt-4o", instructions="…")
result = run(agent, "…", retry=RetryPolicy(max_attempts=5, backoff_base=0.5))
```

`RetryPolicy` fields: `max_attempts`, `backoff_base`, `backoff_factor`, `max_backoff`,
`should_retry` (a predicate — defaults to `default_is_transient`), and `sleep` (injectable, so tests
run instantly). Only the *successful* attempt emits an `LLMCall`, so usage/cost aren't double-counted.

## Checkpointed & resumable runs

Pass `checkpoint=` (a path or a `Checkpointer`) and the run persists its conversation after each
turn. If the process crashes or restarts, calling `run` again with the same checkpoint **resumes**
from the saved state — completed tools are in the saved messages and are **not** re-executed.

```python
from cendor.sdk import Agent, run

agent = Agent(name="assistant", model="gpt-4o", tools=[...], instructions="…")

# First attempt crashes mid-run; the checkpoint holds the completed turns.
try:
    run(agent, "a long task", checkpoint="run.ckpt.json")
except Exception:
    ...

# Later — same checkpoint — resumes where it left off (no re-running earlier tools):
result = run(agent, "a long task", checkpoint="run.ckpt.json")
```

The checkpoint is a local JSON file written atomically (temp + replace). A finished run marks the
checkpoint `done`, so a subsequent call starts fresh.

## Durable memory

`Session` gives in-memory memory with local JSON `save`/`load`. For durable, multi-conversation
persistence use `SQLiteSessionStore` — a single local file, no server.

```python
from cendor.sdk import Agent, run, SQLiteSessionStore

store = SQLiteSessionStore("sessions.db")
session = store.load("user-42")          # empty Session if unknown
run(agent, "hi, I'm Alice", session=session)
store.save("user-42", session)           # durable across restarts

# next process:
session = store.load("user-42")          # remembers Alice
```

## What's *not* here (by design)

No hosted runtime, server, or distributed scheduler (plan §1 non-goals). Cross-process /
distributed execution stays your job — point `SQLiteSessionStore`/checkpoints at your shared storage
if you need it. The SDK is a library, like the OpenAI Agents SDK.
