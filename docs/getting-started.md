# Getting started

Install the SDK, run one governed agent, and learn where each concept lives. Ten minutes,
one API key (or none — the [ungoverned example](#4-run-it-ungoverned--core-only) works offline
with a recorded cassette).

**Using an AI coding assistant?** Cendor's types teach the correct call-shape inline (on hover and
completion), so Copilot / Claude / Cursor get it right as you type — and there's a trap-sheet you can
paste into your assistant: [For AI assistants](for-ai-assistants.md).

**Fastest start.** `npx @cendor/init` (Node) or `uvx cendor-init` (Python) wires Cendor **and** your
AI assistant in one step — it detects your project, writes the correct assistant rules files, can add
the MCP config, and scaffolds a working `instrument()` call. Offline, no key. A companion
`… doctor` catches wiring mistakes before they bite. See [For AI assistants](for-ai-assistants.md).

## 1. Install

<!-- tabs: lang -->
<!-- tab: Python -->

```bash
pip install "cendor-sdk[openai,anthropic]"
# Using uv? Same names, same extras: `uv add` instead of `pip install`.
```

Provider SDKs are optional extras — `[openai]`, `[anthropic]`, `[google]`, `[bedrock]`,
`[ollama]`, `[huggingface]`, `[azure]`, `[foundry-local]`, plus `[mcp]`, `[otel]`, and `[all]`.
The install pulls in the seven libraries the SDK is built on — [cendor-core](/docs/core) (the
`instrument()` seam + event bus), [contextkit](/docs/contextkit), [squeeze](/docs/squeeze),
[tokenguard](/docs/tokenguard), [cendor-guardrails](/docs/guardrails), [cassette](/docs/cassette),
and [acttrace](/docs/acttrace) — by dependency; import only from `cendor.sdk`.

<!-- tab: TypeScript -->

```bash
npm i @cendor/sdk openai
```

Provider SDKs are peer dependencies — add `openai` and/or `@anthropic-ai/sdk` for the providers
you call. ESM-only; Node LTS first, edge runtimes supported. Everything imports from
`@cendor/sdk`.

<!-- /tabs -->

## 2. Bring one env var

The SDK builds the provider client for you — so you don't set a *Cendor* key, you set the
**provider's own** env var. For OpenAI (used in the quickstart below) that's `OPENAI_API_KEY`:

```bash
export OPENAI_API_KEY="sk-..."         # macOS / Linux
```

```powershell
$env:OPENAI_API_KEY = "sk-..."         # Windows PowerShell
```

Prefer to pass it in code? Use `Agent(api_key=…)` / `new Agent({ apiKey: … })`, or hand over a
pre-built client with `Agent(client=…)`. Every other provider reads its own standard variable
(`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, the AWS credential chain for Bedrock, …) — the full table is
in [API keys & credentials](providers.md#api-keys--credentials). The SDK doesn't read `.env` files;
load them yourself. **No key yet?** The [ungoverned](#4-run-it-ungoverned--core-only) and
[testable](#5-make-it-testable) examples below run offline from a recorded cassette — no credentials
needed.

## 3. A governed agent in 10 lines

One agent, one tool, and all four governance layers — budget cap, PII guard, audit chain, and a
cost/usage receipt:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent, tool, run, budget, guard, Policy, AuditLog

@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""      # schema derived from type hints + docstring
    return f"Sunny in {city}"

agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather],
              instructions="Answer using tools when helpful.")

log = AuditLog(system="support", risk_tier="limited", path="audit.jsonl")
with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
    result = run(agent, "What's the weather in Paris?", audit=log)

print(result.output)                        # the final answer
print(result.cost, result.usage)            # Decimal money, real token usage
print([s.name for s in result.tool_steps])  # ["get_weather"]
```

<!-- tab: TypeScript -->

```ts
import { Agent, tool, run, withBudget, guard, Policy, AuditLog } from '@cendor/sdk';
import { z } from 'zod';

const getWeather = tool(({ city }) => `Sunny in ${city}`, {
  name: 'get_weather',
  description: 'Current weather for a city',
  parameters: z.object({ city: z.string() }),   // TS has no runtime type hints — zod is the schema
});

const agent = new Agent({ name: 'assistant', model: 'gpt-4o', tools: [getWeather],
                          instructions: 'Answer using tools when helpful.' });

const audit = new AuditLog('support', { riskTier: 'limited', path: 'audit.jsonl' });
const result = await withBudget({ usd: 0.25, onExceed: 'block' }, () =>
  guard({ policy: Policy.default(), audit }, () =>
    run(agent, "What's the weather in Paris?", { audit })));

console.log(result.output);                          // the final answer
console.log(result.cost?.toString(), result.usage);  // decimal money, real token usage
console.log(result.toolSteps.map((s) => s.name));    // ["get_weather"]
```

<!-- /tabs -->

What each line buys you (and the library it comes from):

- `@tool` / `tool(...)` — the JSON Schema comes from type hints + docstring (Python) or a zod 4
  schema (TS); it's formatted per provider automatically.
- `budget(usd=0.25, on_exceed="block")` — a **pre-flight** cap: the over-budget call never runs
  (from [tokenguard](/docs/tokenguard)).
- `guard(Policy.default(), ...)` — PII is redacted **before** the provider sees it (from
  [acttrace](/docs/acttrace)).
- `AuditLog(...)` — every model and tool call lands in a tamper-evident hash chain
  (`verify("audit.jsonl")` checks it offline) (from [acttrace](/docs/acttrace)).
- `result.cost` — decimal money, never a float, aggregated across the whole run (priced by
  [tokenguard](/docs/tokenguard)).

Not shown here but one argument away: `Agent(context_budget=…)` fits history to a token budget via
[contextkit](/docs/contextkit)/[squeeze](/docs/squeeze), and `Agent(guardrails=[…])` gates the loop
with [cendor-guardrails](/docs/guardrails).

## 4. Run it ungoverned — core only

Every governance layer is optional. Drop the wrappers and it's a bare loop on `cendor-core`:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent, run

agent = Agent(name="a", model="gpt-4o", instructions="Be brief.")
result = run(agent, "Hello")               # sync
result = await run.aio(agent, "Hello")     # async — same signature
```

<!-- tab: TypeScript -->

```ts
import { Agent, run } from '@cendor/sdk';

const agent = new Agent({ name: 'a', model: 'gpt-4o', instructions: 'Be brief.' });
const result = await run(agent, 'Hello');   // TS is async throughout
```

<!-- /tabs -->

## 5. Make it testable

Record the run once; replay it offline forever — deterministic, no network, no keys. Cost and
tokens are **real on replay**, so tests can assert spend too:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor import cassette

with cassette.using("tests/fixtures/run.json"):   # records on first run, replays after
    result = run(agent, "What's the weather in Paris?")
```

<!-- tab: TypeScript -->

```ts
import { using } from '@cendor/cassette';

const result = await using('tests/fixtures/run.json', () =>   // records once, replays after
  run(agent, "What's the weather in Paris?"));
```

<!-- /tabs -->

This is the foundation of the [eval harness](eval.md), which turns recorded trajectories into CI
regression tests.

## 6. Where each concept lives

| I want to… | Go to | Library underneath |
|---|---|---|
| Understand `Agent`, `tool`, `run`, `Result` | [Agents & the loop](agents.md) | [cendor-core](/docs/core) |
| Cap spend, attribute cost | [Governance](governance.md) | [tokenguard](/docs/tokenguard) |
| Audit, redact PII | [Governance](governance.md) | [acttrace](/docs/acttrace) |
| Block / redact / flag at four stages | [Guardrails](guardrails.md) | [cendor-guardrails](/docs/guardrails) |
| Fit history to a token budget | [Agents & the loop](agents.md#core-concepts) | [contextkit](/docs/contextkit) + [squeeze](/docs/squeeze) |
| Make the agent remember across turns/processes | [Memory & sessions](memory.md) | [contextkit](/docs/contextkit) |
| Give the agent my documents | [Retrieval (RAG)](rag.md) | [contextkit](/docs/contextkit) |
| Use more than one agent | [Multi-agent](multi-agent.md) | — (SDK orchestration) |
| Connect Gemini / Bedrock / Ollama / Hugging Face / Azure | [Providers](providers.md) | [cendor-core](/docs/core) |
| Consume MCP tools, serve A2A, emit OTel spans | [Ecosystem & interop](interop.md) | [cendor-core](/docs/core) |
| Survive crashes, retries, long runs | [Production hardening](hardening.md) | — (SDK) |
| Record once / replay; gate regressions in CI | [Eval & regression testing](eval.md) | [cassette](/docs/cassette) |

The full map — every SDK symbol, the library that powers it, and where it plugs into the loop —
is on [Architecture](architecture.md).

> **Coming from the libraries?** `budget`, `track`, `Policy`, `AuditLog`, and `trace` are the
> identical library objects here, re-exported for one-import convenience — and `guard` is
> acttrace's policy enforcement in the SDK's scope form. Nothing to relearn. The reverse also
> holds: see [FAQ → libraries or SDK](faq.md).
