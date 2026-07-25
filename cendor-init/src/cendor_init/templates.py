"""Vendored assistant-rules templates + the offline versions snapshot.

These are VENDORED copies of the copy-paste rules blocks from the docs source of truth,
``cendor-libs/docs/assistant-rules.md``. That page is the single source — do not fork its wording;
when it changes, re-copy the blocks here (see the cendorhq root CLAUDE.md release-sync list). Kept in
lock-step with the TypeScript twin (``@cendor/init``'s ``templates/*.md``).
"""

from __future__ import annotations

# GitHub Copilot repo-wide instructions (.github/copilot-instructions.md).
COPILOT = """# Using Cendor (cendor.* / @cendor/*) correctly

Cendor is offline-first plumbing for LLM apps — Python `cendor.*` (PyPI), TypeScript `@cendor/*`
(npm), Apache-2.0. Wrap the provider client **once** with `instrument()`; budgets, gating, testing,
and audit all plug into one event bus. Every public symbol ships an inline `@example` + a
correct-shape type — trust the editor's hover/completion over a guess.

Which library, and the one call that matters (Python shown; TS mirrors it in camelCase — see traps):
- Cap / attribute spend → **tokenguard**: `@budget(usd=0.5, on_exceed="raise")`, `track(...)`, `report()`
- Fit a prompt to a token budget → **contextkit**: `Context(budget_tokens=8000, model="gpt-4o").assemble()`
- Losslessly shrink a payload → **squeeze**: `small, handle = compress(x, kind="auto")`
- Block / redact unsafe input+output → **guardrails**: `rules.keyword_deny([...], action="block")`
- Record once, replay offline in tests → **cassette**: `@cassette.use("tests/x.json")`
- PII/secret detection + tamper-evident audit → **acttrace**: `AuditLog(system="support", risk_tier="limited")`
- Token count / price / instrument → **core**: `instrument(OpenAI())`, `tokens.count(msgs, model="gpt-4o")`
- A whole governed agent loop → **cendor-sdk**: `Agent(name=…, model=…, guardrails=[…], max_usd=0.5)`; `run(agent, "hi")`

Call shapes that are easy to get wrong:
- `instrument()` wraps the client **once**, not per call.
- TS `budget` is **curried**: `budget(cfg)(fn)` — never `budget(cfg, fn)`. Python `budget(...)` takes keyword args and is both a decorator and a context-manager.
- `prices.estimate` — Python positional `prices.estimate(model, input_tokens, output_tokens=200)`; TS options object `prices.estimate(model, inputTokens, { outputTokens: 200 })`.
- Money is `Decimal` / `decimal.js`, never `float` / `number`.
- `Context.assemble()` is sync in Python (`aassemble()` for async), async in TS (`await`).
- Guardrail actions are `block | redact | flag` (no `warn`); PII/secrets are acttrace detectors, not guardrail rules.
- Session store lives in the SDK, casing differs: Python `SQLiteSessionStore`, TS `SqliteSessionStore`.
- TS tokenguard sinks live at the `@cendor/tokenguard/sinks` subpath.
- Python is a PEP 420 namespace — `from cendor.tokenguard import budget`; no top-level `cendor` module.
- Provider SDKs are optional (Python extras, TS peer deps) — install only what you call.
- SDK provider keys: the SDK builds the client, so use the provider's standard env var (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …) or `Agent(api_key=…)` / a pre-built `client=`. There is no Cendor key config.
- Telemetry: **do not write any**. With OpenTelemetry installed and a provider configured in the app, Cendor emits `gen_ai.*` call spans, spend counters, an `agent.run` tree per SDK `run()`, and `governance.*` decisions on its own (core ≥ 1.13 / 0.15, sdk ≥ 1.19 / 0.22). Never add `use_span_emitter()` / `use_sink(OTelSink())` / `live_spans()` unless the user asks for explicit control — and never invent an endpoint, key or exporter: Cendor has none, it emits into the app's own provider.
- The off switch is the env var `CENDOR_TELEMETRY=off` (process-wide, no code change); `CENDOR_DEBUG_TELEMETRY=1` prints one line saying whether a provider was detected. With no provider (or no OTel installed) everything is a silent no-op — that is correct, not a bug.
- `AuditLog(system=…)` is the *governance* line; its OTel mirror auto-attaches (pass `mirror=False` to opt out). The mirror is an **operational copy** — `verify()` runs on the hash-chained file. The automatic `governance.*` spans are operational signals too, never "an audit trail".

Honest limits: deterministic guardrails don't stop novel adversarial attacks; acttrace produces
*evidence*, not a compliance guarantee. Full reference: https://cendor.ai/docs/for-ai-assistants
"""

# Cursor project rule — a whole file, frontmatter included (.cursor/rules/cendor.mdc).
CURSOR = """---
description: How to call Cendor (cendor.* / @cendor/*) correctly
globs: ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js"]
alwaysApply: false
---
Cendor is offline-first plumbing for LLM apps — Python `cendor.*`, TypeScript `@cendor/*`. Wrap the
provider client **once** with `instrument()`; budgets, gating, testing, and audit plug into one bus.
Every public symbol ships an inline `@example` — trust the editor's hover over a guess.

Which library, and the one call that matters (Python; TS mirrors it in camelCase — see traps):
- Cap / attribute spend → **tokenguard**: `@budget(usd=0.5, on_exceed="raise")`, `track(...)`, `report()`
- Fit a prompt to a token budget → **contextkit**: `Context(budget_tokens=8000, model="gpt-4o").assemble()`
- Losslessly shrink a payload → **squeeze**: `small, handle = compress(x, kind="auto")`
- Block / redact unsafe input+output → **guardrails**: `rules.keyword_deny([...], action="block")`
- Record once, replay offline → **cassette**: `@cassette.use("tests/x.json")`
- PII/secret detection + tamper-evident audit → **acttrace**: `AuditLog(system="support", risk_tier="limited")`
- Token count / price / instrument → **core**: `instrument(OpenAI())`, `tokens.count(msgs, model="gpt-4o")`
- A governed agent loop → **cendor-sdk**: `Agent(name=…, model=…, guardrails=[…], max_usd=0.5)`; `run(agent, "hi")`

Traps: `instrument()` once, not per call. TS `budget` is curried — `budget(cfg)(fn)`, never
`budget(cfg, fn)`. `prices.estimate` is positional in Python (`output_tokens=…`) but takes a
`{ outputTokens }` object in TS. Money is `Decimal`/`decimal.js`, never `float`/`number`.
`Context.assemble()` is sync in Python (`aassemble()` async), `await` in TS. Guardrail actions are
`block | redact | flag` (no `warn`); PII/secrets are acttrace detectors, not guardrail rules. Session
store is in the SDK, casing differs (`SQLiteSessionStore` / `SqliteSessionStore`). TS tokenguard
sinks: `@cendor/tokenguard/sinks`. Python is a PEP 420 namespace (`from cendor.tokenguard import
budget`). Provider SDKs are optional (extras / peer deps). SDK provider keys: the provider's standard
env var (`OPENAI_API_KEY`) or `Agent(api_key=…)`, never a Cendor key config. Telemetry: write none — with an
OpenTelemetry provider configured in the app, Cendor emits call spans, spend counters, an `agent.run`
tree per `run()`, and `governance.*` decisions by itself (core ≥ 1.13/0.15, sdk ≥ 1.19/0.22); never add
`use_span_emitter()`/`use_sink(OTelSink())`/`live_spans()` unless explicit control is asked for, and
never invent an endpoint or key (Cendor has none). Off switch: `CENDOR_TELEMETRY=off`; diagnose with
`CENDOR_DEBUG_TELEMETRY=1`. `AuditLog(system=…)` auto-attaches its OTel mirror (`mirror=False` opts
out) — the mirror and the `governance.*` spans are operational copies; `verify()` runs on the file. Deterministic guardrails
don't stop novel attacks; acttrace is evidence, not a guarantee. Full reference:
https://cendor.ai/docs/for-ai-assistants
"""

# The cross-tool AGENTS.md section (also reused for Windsurf's .windsurf/rules).
AGENTS = """## Cendor (cendor.* / @cendor/*)

Offline-first plumbing for LLM apps. Wrap the provider client **once** with `instrument()`; budgets,
gating, testing, and audit plug into one bus. Every symbol ships an inline `@example` — trust the
editor's hover over a guess.

Which library, and the one call that matters (Python; TS mirrors it in camelCase — see traps):
- Cap / attribute spend → **tokenguard**: `@budget(usd=0.5, on_exceed="raise")`, `track(...)`, `report()`
- Fit a prompt to a token budget → **contextkit**: `Context(budget_tokens=8000, model="gpt-4o").assemble()`
- Losslessly shrink a payload → **squeeze**: `small, handle = compress(x, kind="auto")`
- Block / redact unsafe input+output → **guardrails**: `rules.keyword_deny([...], action="block")`
- Record once, replay offline → **cassette**: `@cassette.use("tests/x.json")`
- PII/secret detection + tamper-evident audit → **acttrace**: `AuditLog(system="support", risk_tier="limited")`
- Token count / price / instrument → **core**: `instrument(OpenAI())`, `tokens.count(msgs, model="gpt-4o")`
- A governed agent loop → **cendor-sdk**: `Agent(name=…, model=…, guardrails=[…], max_usd=0.5)`; `run(agent, "hi")`

Traps: `instrument()` once, not per call. TS `budget` is curried — `budget(cfg)(fn)`, never
`budget(cfg, fn)`. `prices.estimate` is positional in Python, `{ outputTokens }` object in TS. Money
is `Decimal`/`decimal.js`, never `float`/`number`. `Context.assemble()` is sync in Python
(`aassemble()` async), `await` in TS. Guardrail actions `block | redact | flag` (no `warn`);
PII/secrets are acttrace detectors, not guardrail rules. Session store is in the SDK, casing differs
(`SQLiteSessionStore` / `SqliteSessionStore`). TS tokenguard sinks: `@cendor/tokenguard/sinks`.
Python is a PEP 420 namespace. Provider SDKs are optional. SDK provider keys: the provider's standard
env var (`OPENAI_API_KEY`) or `Agent(api_key=…)`, never a Cendor key config. Telemetry: write none — with an
OpenTelemetry provider configured in the app, Cendor emits call spans, spend counters, an `agent.run`
tree per `run()`, and `governance.*` decisions by itself (core ≥ 1.13/0.15, sdk ≥ 1.19/0.22); never add
`use_span_emitter()`/`use_sink(OTelSink())`/`live_spans()` unless explicit control is asked for, and
never invent an endpoint or key (Cendor has none). Off switch: `CENDOR_TELEMETRY=off`; diagnose with
`CENDOR_DEBUG_TELEMETRY=1`. `AuditLog(system=…)` auto-attaches its OTel mirror (`mirror=False` opts
out) — the mirror and the `governance.*` spans are operational copies; `verify()` runs on the file. Deterministic guardrails
don't stop novel attacks; acttrace is evidence, not a guarantee. Full reference:
https://cendor.ai/docs/for-ai-assistants
"""

# Claude Code CLAUDE.md section.
CLAUDE = """## Calling Cendor (cendor.* / @cendor/*)

Offline-first plumbing for LLM apps. Wrap the provider client **once** with `instrument()`; budgets,
gating, testing, and audit plug into one bus. Every symbol ships an inline `@example` — prefer the
editor's hover to a guess.

Which library, and the one call that matters (Python; TS mirrors it in camelCase — see traps):
- Cap / attribute spend → **tokenguard**: `@budget(usd=0.5, on_exceed="raise")`, `track(...)`, `report()`
- Fit a prompt to a token budget → **contextkit**: `Context(budget_tokens=8000, model="gpt-4o").assemble()`
- Losslessly shrink a payload → **squeeze**: `small, handle = compress(x, kind="auto")`
- Block / redact unsafe input+output → **guardrails**: `rules.keyword_deny([...], action="block")`
- Record once, replay offline → **cassette**: `@cassette.use("tests/x.json")`
- PII/secret detection + tamper-evident audit → **acttrace**: `AuditLog(system="support", risk_tier="limited")`
- Token count / price / instrument → **core**: `instrument(OpenAI())`, `tokens.count(msgs, model="gpt-4o")`
- A governed agent loop → **cendor-sdk**: `Agent(name=…, model=…, guardrails=[…], max_usd=0.5)`; `run(agent, "hi")`

Traps: `instrument()` once, not per call. TS `budget` is curried — `budget(cfg)(fn)`, never
`budget(cfg, fn)`. `prices.estimate` is positional in Python, `{ outputTokens }` object in TS. Money
is `Decimal`/`decimal.js`, never `float`/`number`. `Context.assemble()` is sync in Python
(`aassemble()` async), `await` in TS. Guardrail actions `block | redact | flag` (no `warn`);
PII/secrets are acttrace detectors, not guardrail rules. Session store is in the SDK, casing differs
(`SQLiteSessionStore` / `SqliteSessionStore`). TS tokenguard sinks: `@cendor/tokenguard/sinks`.
Python is a PEP 420 namespace. Provider SDKs are optional. SDK provider keys: the provider's standard
env var (`OPENAI_API_KEY`) or `Agent(api_key=…)`, never a Cendor key config. Telemetry: write none — with an
OpenTelemetry provider configured in the app, Cendor emits call spans, spend counters, an `agent.run`
tree per `run()`, and `governance.*` decisions by itself (core ≥ 1.13/0.15, sdk ≥ 1.19/0.22); never add
`use_span_emitter()`/`use_sink(OTelSink())`/`live_spans()` unless explicit control is asked for, and
never invent an endpoint or key (Cendor has none). Off switch: `CENDOR_TELEMETRY=off`; diagnose with
`CENDOR_DEBUG_TELEMETRY=1`. `AuditLog(system=…)` auto-attaches its OTel mirror (`mirror=False` opts
out) — the mirror and the `governance.*` spans are operational copies; `verify()` runs on the file. Deterministic guardrails
don't stop novel attacks; acttrace is evidence, not a guarantee. Full reference:
https://cendor.ai/docs/for-ai-assistants
"""

SENTINEL = "cendor.ai/docs/for-ai-assistants"
"""Appears in every template — used to recognise a file we previously wrote."""
