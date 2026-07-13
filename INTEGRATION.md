# Integrating Cendor (read me — or hand me to your AI assistant)

You installed a `cendor-*` (PyPI) / `@cendor/*` (npm) package. Cendor is **offline-first plumbing for
LLM apps** — context, cost, guardrails, testing, audit — that sits *beneath* your agent framework.
Local-first, no servers, Apache-2.0. Here's the 30-second version so you (or the assistant writing
this code) call it correctly.

## Instrument once

Wrap your provider client a **single** time. Every Cendor library then plugs into one shared event
bus — nothing monkey-patches your client, nothing imports another library.

```python
# Python  (cendor.*)
from cendor.core import instrument
client = instrument(OpenAI())          # idempotent · additive · sync/async/streaming
```

```ts
// TypeScript  (@cendor/*)
import { instrument } from '@cendor/core';
const client = instrument(new OpenAI());   // idempotent · additive · sync/async/streaming
```

## Which library for which job

`tokenguard` cap/attribute spend · `contextkit` fit a prompt to a token budget · `squeeze` losslessly
shrink a payload · `guardrails` block/redact unsafe input+output · `cassette` record once, replay
offline · `acttrace` PII/secret detection + tamper-evident audit · `core` token count / price /
`instrument()`. A whole governed agent loop → `cendor-sdk` / `@cendor/sdk`.

## Building an agent? (`cendor-sdk` / `@cendor/sdk`)

The second door: the SDK gives you the whole governed agent loop — `Agent`, `tool`, `run` — with the
same governance one import away.

```python
# Python  (cendor.sdk)
from cendor.sdk import Agent, run, budget

agent = Agent(name="assistant", model="gpt-4o", instructions="Be helpful.", max_usd=0.5)
with budget(usd=0.10, on_exceed="block"):        # pre-flight cap
    result = run(agent, "Summarize the standup.")
print(result.output, result.cost)                # the answer + Decimal money
```

```ts
// TypeScript  (@cendor/sdk)
import { Agent, run, withBudget } from '@cendor/sdk';

const agent = new Agent({ name: 'assistant', model: 'gpt-4o', instructions: 'Be helpful.', maxUsd: 0.5 });
const result = await withBudget({ usd: 0.10, onExceed: 'block' }, () =>   // pre-flight cap
  run(agent, 'Summarize the standup.'));
console.log(result.output, result.cost?.toString());  // the answer + decimal money
```

The per-agent cost cap is **`max_usd`/`maxUsd`**, not a `budget=` field; the SQLite session store
casing differs (`SQLiteSessionStore` in Python, `SqliteSessionStore` in TS); `cassette` is imported
from the umbrella (`from cendor import cassette`), not the SDK. **Auth:** the SDK builds the provider
client, so use the provider's standard env var (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …) or
`Agent(api_key=…)` / a pre-built `client=` — nothing Cendor-specific. Full guide:
<https://cendor.ai/docs/sdk>.

## The three traps most likely to bite

1. **TS `budget` is curried:** `budget(cfg)(fn)` — never `budget(cfg, fn)`. Python `budget(...)` is a
   decorator *and* context-manager taking keyword args: `@budget(usd=0.5, on_exceed="raise")`.
2. **`prices.estimate` differs by language:** Python is positional
   `prices.estimate(model, input_tokens, output_tokens=200)`; TS takes an options object
   `prices.estimate(model, inputTokens, { outputTokens: 200 })`. Money is `Decimal` / `decimal.js`,
   never `float` / `number`.
3. **Python is a PEP 420 namespace:** import from the flat path (`from cendor.tokenguard import
   budget`); there is no top-level `cendor` module object. In TypeScript, tokenguard sinks live at the
   `@cendor/tokenguard/sinks` subpath, not the package root.

Every public symbol also ships an inline `@example` and a correct-shape type signature, so your editor
(and any agent-mode assistant reading diagnostics) is handed the right call as you type — the wrong
shape is a compile error whose message states the right one.

## More

- **Full call-shape trap sheet + copy-paste assistant rules:** <https://cendor.ai/docs/for-ai-assistants>
- **MCP server for agent-mode assistants:** `mcp.cendor.ai`
- **Docs:** <https://cendor.ai/docs>

Honest limits: deterministic guardrails don't stop novel adversarial attacks, and `acttrace` produces
*evidence* to support compliance — never a compliance guarantee.
