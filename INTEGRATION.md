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
