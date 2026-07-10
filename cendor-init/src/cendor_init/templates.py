"""Vendored assistant-rules templates + the offline versions snapshot.

These are VENDORED copies of section 3 ("Wire up your AI assistant") of the docs source of truth,
``cendor-libs/docs/for-ai-assistants.md``. That page is the single source — do not fork its wording;
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
budget`). Provider SDKs are optional (extras / peer deps). Deterministic guardrails don't stop novel
attacks; acttrace is evidence, not a guarantee. Full reference: https://cendor.ai/docs/for-ai-assistants
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
Python is a PEP 420 namespace. Provider SDKs are optional. Deterministic guardrails don't stop novel
attacks; acttrace is evidence, not a guarantee. Full reference: https://cendor.ai/docs/for-ai-assistants
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
Python is a PEP 420 namespace. Provider SDKs are optional. Deterministic guardrails don't stop novel
attacks; acttrace is evidence, not a guarantee. Full reference: https://cendor.ai/docs/for-ai-assistants
"""

SENTINEL = "cendor.ai/docs/for-ai-assistants"
"""Appears in every template — used to recognise a file we previously wrote."""
