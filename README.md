# cendor-sdk

**A governed agent in 10 lines — cost budgets, tamper-evident audit, and PII redaction built in.**

![version](https://img.shields.io/badge/version-1.0.0-blue) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-Apache_2.0-blue) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) ![types: mypy](https://img.shields.io/badge/types-mypy-blue) ![status: stable](https://img.shields.io/badge/status-stable-brightgreen)

*provider-agnostic · local-first · offline by default · sync **and** async*

> The only agent SDK where **cost budgets, tamper-evident audit, PII redaction, context governance,
> and record/replay testing are the *foundation*, not plugins.**

---

## Two doors into Cendor

Cendor is *production plumbing for LLM applications*. There are two ways in — pick by your situation,
not by primacy:

| | When | What you get |
|---|---|---|
| 🥇 **The libraries** (primary) | *You already have a framework* (LangChain, LlamaIndex, the provider SDKs). | Drop cost, governance, and testing **beneath** the framework you already use. `pip install cendor`. |
| 🚪 **`cendor-sdk`** (this repo) | *Starting fresh / want it simple.* | A batteries-included, **governed agent SDK** — you don't need to pick a framework *or* wire the libraries. `pip install cendor-sdk`. |

Both doors expose the **same primitives** — `budget`, `guard`, `Policy`, `AuditLog`, `trace`. A team
that starts on the SDK and later adopts a framework drops to the libraries underneath with **no
concept rewrite**. Movement between doors is continuous, not a migration.

`cendor-sdk` **owns the agent loop**, so every governance concern that is best-effort *beneath* a
framework becomes first-class here: usage is never lost, budgets enforce *before* the model call,
PII is redacted *before* send, and the whole run correlates under one `trace_id`.

---

## Install

```bash
pip install "cendor-sdk[openai,anthropic]"     # provider SDKs are optional extras
```

The install bundles the whole Cendor stack (`cendor-core`, `tokenguard`, `acttrace`, `contextkit`,
`squeeze`, `cassette`) by dependency — you install once and import only from `cendor.sdk`.

## The killer example — a governed agent in 10 lines

```python
from cendor.sdk import Agent, tool, run, budget, guard, Policy, AuditLog

@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"

agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather],
              instructions="Answer using tools when helpful.")

log = AuditLog(system="support", risk_tier="limited", path="audit.jsonl")
with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
    result = run(agent, "What's the weather in Paris?", audit=log)

print(result.output)                        # -> "It's sunny in Paris."
print(result.cost, result.usage)            # priced in Decimal, budgeted
print([s.name for s in result.tool_steps])  # -> ["get_weather"]
# audit.jsonl: audit_open -> decision -> llm_call -> tool_call -> llm_call, hash-chained &
# verify()-able, all correlated by one trace_id. Wrap in cassette.using("run.json") to replay it.
```

**Ungoverned still works — on `cendor-core` alone.** Every governance layer is optional and
removable; drop the `with` block and `run(agent, ...)` runs bare:

```python
from cendor.sdk import Agent, run
result = run(Agent(name="a", model="gpt-4o", instructions="Be brief."), "Hi")
result = await run.aio(agent, "Hi")   # same call, async
```

---

## Why it's different

| | Provider lock | Cost budgets | Tamper-evident audit | PII redaction | Record/replay tests | Local-first |
|---|---|---|---|---|---|---|
| OpenAI Agents SDK | OpenAI-centric | ✗ | ✗ | ✗ | ✗ | lib |
| LangGraph | agnostic | DIY | DIY | DIY | DIY | lib |
| Anthropic Agent SDK | Anthropic-centric | ✗ | ✗ | ✗ | ✗ | lib |
| CrewAI / Pydantic AI / ADK | varies | ✗/DIY | ✗ | ✗ | ✗ | lib |
| **cendor-sdk** | **agnostic** | **built-in** | **built-in** | **built-in** | **built-in** | **yes** |

Governance is composed through Cendor's existing **bus / interceptor / `Sink` / `Compressor`**
seams, correlated by `trace()` — **zero SDK-specific glue**. Budgets, audit, redaction, and
record/replay all ride the agent loop through those seams, so removing any one is just not entering
its context.

## How one turn executes

`Runner`, for each agent turn:

1. **Assemble** context to the model's budget via `contextkit` (optional; falls back to raw messages) — emits an `AssemblyReport` that the audit chain records.
2. **Format** messages + tool schemas for the target provider.
3. **Call** the model through a `cendor-core`-instrumented client inside `trace(run_id)` → emits an `LLMCall` (usage/cost/reasoning). Pre-call, budget/guard interceptors fire (block / clamp / downgrade, redact-before-send).
4. **Normalize** the response → assistant content + tool calls + finish reason.
5. **Execute** any tool calls (each emits a `ToolCall`, same `trace_id`); append results; loop.
6. Else **finalize** → structured-output parse → `Result`.

## Multi-agent, one correlated tree

Handoff, supervisor/router, and sequential/parallel pipelines — with the correlation that was
*impossible beneath frameworks*. A whole multi-agent run is one governed, `trace_id`-correlated
tree, on one verifiable audit chain. Handoff even works **across providers**:

```python
from cendor.sdk import Agent, run

writer  = Agent(name="writer",  model="claude-opus-4-8", instructions="Write the brief.")
planner = Agent(name="planner", model="gpt-4o", instructions="Plan, then hand off.",
                handoffs=["writer"])

result = run([planner, writer], "Research X and write a brief")   # OpenAI ➝ Anthropic handoff
print(result.agents)     # ["planner", "writer"]
```

See [docs/multi-agent.md](docs/multi-agent.md).

## Status — v1.0.0 (stable)

All four phases are shipped, tested offline, and documented.

| Phase | Scope | State |
|---|---|---|
| **1** | Governed single agent (loop, providers, tools, structured output, session, governance) | ✅ shipped |
| **2** | Multi-agent orchestration (handoff, supervisor, sequential/parallel) | ✅ shipped |
| **3** | Ecosystem & interop (MCP, A2A, Foundry, OTel span tree, HITL) | ✅ shipped |
| **4** | Production hardening (retries, checkpoints, durable memory) & governed eval | ✅ shipped |

## Docs

- [docs/index.md](docs/index.md) — start here
- [docs/sdk.md](docs/sdk.md) — the SDK quickstart & reference
- [docs/multi-agent.md](docs/multi-agent.md) — handoff, supervisor, sequential/parallel
- [docs/interop.md](docs/interop.md) — MCP, A2A, Foundry/Copilot, OTel, human-in-the-loop
- [docs/hardening.md](docs/hardening.md) — retries, checkpointed/resumable runs, durable memory
- [docs/eval.md](docs/eval.md) — cassette-backed governed eval & regression testing
- [CHANGELOG.md](CHANGELOG.md)
- [examples/](examples/) — runnable, network-free examples
- [plan/CENDOR_SDK_PLAN.md](plan/CENDOR_SDK_PLAN.md) — the full design

## License

Apache-2.0.
