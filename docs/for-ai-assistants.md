# cendor-sdk for AI coding assistants

You told an assistant *"build me a governed agent"* and it reached for `cendor-sdk`. The SDK gets the
same AI-assistant treatment as the libraries — the correct call-shapes are taught inline and
on demand, so your assistant writes `Agent`, `run`, `budget`, and `guard` right the first time. This
page is a short pointer; the canonical call-shape reference lives with the libraries (the SDK
re-exports their objects, so the shapes are the same).

## Four ways to make your assistant fluent

- **Type Teach ships inside the package.** `cendor-sdk` (Python) and `@cendor/sdk` (TypeScript) carry
  an inline `@example` and a correct-shape signature on every public symbol — `Agent`, `tool`, `run`,
  `budget`, `guard`, `Policy`, `AuditLog`. Your editor's language server (and any agent-mode
  assistant that reads diagnostics) is handed the right shape as you type, and the wrong shape is a
  compile error whose message states the right one. No setup — it's in the install.
- **Rules files** — paste a short cheatsheet into your repo so your assistant reads the correct
  shapes on every edit. The SDK row (`Agent(name=…, model=…, guardrails=[…], max_usd=0.5)`;
  `run(agent, "hi")`) is already in every block. See [Rules files](/docs/assistant-rules).
- **MCP server** (agent mode) — connect the read-only [Cendor MCP server](/docs/assistant-mcp) and
  your assistant can look up SDK pages live, e.g. `get_page("sdk/agents")` or `get_page("sdk/governance")`,
  plus the shared `get_api` / `example` call-shape tools. Remote `mcp.cendor.ai` or local
  `npx @cendor/mcp` / `uvx cendor-mcp`.
- **One command** — `npx @cendor/init` / `uvx cendor-init` detects the SDK in your project and wires
  the rules files (and, with `--mcp`, the connect config); its `doctor` static-checks your wiring for
  CI. See [init CLI & doctor](/docs/assistant-init).

## The call-shape reference is shared

The SDK's governance primitives (`budget`, `track`, `Policy`, `AuditLog`, `trace`, …) *are* the
library objects, re-exported as identities; `guard` is acttrace's enforcement in the SDK's scope
form, `rules` is the SDK's superset module, and the eval harness, `register_model_price`, and
session stores are SDK-owned. The shapes are shared, so the canonical trap table and
CI-typechecked examples live on one page for both doors:
**[For AI assistants](/docs/for-ai-assistants)**. Don't duplicate it here; point your assistant at
that URL (or the [full docs bundle](https://cendor.ai/llms-full.txt), which includes every SDK page).

For the SDK-specific surfaces, the docs themselves are the reference:
[Agents & the loop](agents.md), [Governance](governance.md), [Guardrails](guardrails.md), and the
[FAQ](faq.md).

## SDK-specific traps

The canonical trap table above covers the shared library primitives. These are the traps that bite
**only when you use the SDK door** — where the SDK re-exports something (or deliberately doesn't), or
where a language ships the SDK feature and the other lags. Same four-column shape for muscle memory.
Every row is verified against the current source in both languages.

| Task | Python (`cendor.sdk`) | TypeScript (`@cendor/sdk`) | The trap |
|---|---|---|---|
| A governed agent | `Agent(name=…, model=…, guardrails=[…], max_usd=0.5)`; `run(agent, "hi")` | `new Agent({ name, model, guardrails: [...], maxUsd: 0.5 })`; `run(agent, 'hi')` | No `budget=` on `Agent` — the per-agent cap is `max_usd`/`maxUsd`. TS is `maxUsd` and `baseURL` (capital URL); process-wide caps use tokenguard's `budget()`. |
| Session store | `SQLiteSessionStore(path)` — capital `SQLite` | `new SqliteSessionStore(path)` — `Sqlite` | Casing differs across languages, and it lives in `cendor.sdk`, not `cassette`. TS also has `new MemorySessionStore()` (no-arg, in-memory, TS-only); Python uses a plain `Session`. |
| Record / replay | `from cendor import cassette` | `import { using } from '@cendor/cassette'` | **cassette is NOT re-exported by the SDK** — import it from the umbrella, never `cendor.sdk.cassette`. It surfaces in the SDK only via the eval harness. |
| PII / secrets in the loop | `rules.pii()` / `rules.secrets()` / `rules.entropy()` | `rules.pii(undefined, {…})` / `rules.secrets({…})` | These are **acttrace detectors bridged** into the Gate — they gate **all four stages incl. `tool_output`**, which the process-global `guard()` never sees. Not `cendor.guardrails` rules. |
| LLM-judge intent / adherence | `from cendor.sdk import judge, rules`; `judge.task_adherence(respond)` / `judge.intent_prompt(i, mode="deny")` | `judge.taskAdherence(respond)` / `judge.intentPrompt(i, 'deny')` | `judge.*` build a check (a policy string or a verdict fn), **not** a guardrail — wire via `rules.llm_judge(check, stage=…)`. |
| Spotlight (wrap untrusted) | `rules.spotlight(...)` — on the SDK `rules` | `rules.spotlight({...})` — on the SDK `rules` (since 0.10.0) | Both SDKs re-export the **full** library rule catalogue — `spotlight`, the detection-tier adapters (`language`, `classifier`, `openaiModeration`, …), and the similarity checks all ride `rules`. Only the helpers (`payloadText`, `NORMALIZATIONS`) stay library-only in TS. |
| Red-team a gate | `from cendor.guardrails import load_corpus, run_redteam` | `import { loadCorpus, runRedteam } from '@cendor/guardrails'` | `redteam` / `load_corpus` live in `cendor.guardrails`, deliberately **not** SDK re-exports — cendor vends no attack data. |
| Python-only SDK gates | `Agent(reask_on_output_trip=2, stream_check_window=200)` | *Python-first — the TS port lands later* | Bounded re-ask on an output block + streaming output-window checks are Python-first in `cendor-sdk`; see the [parity matrix](/docs/languages). |
| HF / Azure model ids | `Agent(model="…", provider="huggingface")` | `new Agent({ model, provider: 'huggingface' })` | Hub ids & Azure deployment names aren't prefix-inferable — always pass `provider=`. Provider SDKs are extras (Py) / peers (TS). |

These SDK rows are also folded into the machine-served canonical table, so an agent-mode assistant on
the [MCP server](/docs/assistant-mcp) gets them from `get_api` too.

## Honest limits

- These aids teach call **shapes**, never performance numbers — every benchmark-backed claim lives in
  the libraries' [Benchmarks](/docs/benchmarks); `acttrace` (the SDK's audit layer) produces
  *evidence*, not a compliance guarantee.
- Type Teach and the rules files are only as current as your installed version / the day you pasted.
  For a live lookup use the MCP server (agent mode); if a shape disagrees with your editor's hover,
  trust the editor — it's reading the version you actually have.
- Parity is documented, not version-coupled. Where the TypeScript SDK differs from Python (e.g. it
  ships OpenAI + Anthropic first-class), the [Languages & parity](/docs/languages) matrix is the
  source of truth.
