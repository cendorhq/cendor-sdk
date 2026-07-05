# cendor-sdk — a governed, provider-agnostic agent SDK (all-phase plan)

**Status:** proposed (not started)
**New repo:** `cendorhq/cendor-sdk` (separate from the monorepo) — ships `src/cendor/sdk/` only; bundles the published libraries as dependencies.
**Product role:** the **second door** — the simple, all-in-one governed agent SDK for teams who don't want to adopt another framework. The **libraries remain the primary** "beneath your framework" door.
**One line:** *the only agent SDK where cost budgets, tamper-evident audit, PII redaction, context governance, and record/replay testing are the **foundation**, not plugins — provider-agnostic and local-first.*

---

## 0. Why this exists (the strategic frame)

Cendor's libraries were designed to sit **beneath** other people's frameworks. Empirically that path has hard limits whenever Cendor doesn't own the inference loop (LangChain `with_raw_response` loses usage; streaming can crash; framework capture is recording-only; no run/agent correlation; embeddings invisible — see `plan/HARDENING_AND_LANGCHAIN_PLAN.md`, Appendix A).

`cendor-sdk` inverts the relationship: **we own the loop.** Because the SDK makes the model and tool calls itself, every one of those limitations dissolves — and the existing libraries (already ~70% of the hard work) plug in as first-class governance instead of best-effort observers.

**This is a second front door, not a replacement.** "Libraries beneath *your* framework" and "our whole governed SDK" coexist. The wedge is the same either way: production plumbing.

### Why owning the loop dissolves the limitations

| Beneath-frameworks limit | In `cendor-sdk` |
|---|---|
| `with_raw_response` → usage lost | Gone — we call the client directly; real response captured |
| Streaming context-manager crash | Gone — we own how the stream is returned |
| Enforcement impossible in frameworks (recording-only) | Full — budget/guard run *before* each model call |
| No run/agent identity; `trace_id` empty | First-class — SDK sets `trace()` per run/agent; correlation automatic |
| contextvars don't cross threads | We own task spawning → propagate context deliberately |
| Embeddings (RAG) not captured | We make the embedding call → capture it |
| Version-coupled fragility | No third-party framework in the path |

---

## 1. Goals & non-goals

### Goals
- A small, opinionated, **provider-agnostic** agent SDK (OpenAI, Anthropic, Gemini, Bedrock, Ollama — reusing `cendor-core`'s adapters).
- **Governance is the foundation:** budgets, audit, redaction, context assembly, and record/replay are wired in by construction, composable via the existing bus/interceptor/protocol seams.
- **Local-first**, no servers, no account (CLAUDE.md rule 4). Cloud/OTel export optional.
- **Sync and async** parity everywhere a model/tool call happens.
- Full type hints; `Decimal` money; tests hit no network; `import cendor.sdk` works.

### Non-goals (hard boundaries)
- **Not a feature-parity race** with LangGraph/OpenAI Agents SDK. Ship a thin core + the governance wedge; decline breadth-for-breadth's-sake.
- **No hosted runtime / server / distributed scheduler.** A library, like the OpenAI Agents SDK. Cross-process/distributed stays the user's job (durable sinks + their store).
- **No new heavy hard dependencies.** Hard-dep is `cendor-core` only (see §4). Provider SDKs and governance tools are optional extras.
- **Does not replace the libraries or the "beneath frameworks" story.**

---

## 2. Guiding principles (do not break)

1. **Cooperate through core (CLAUDE.md rule 2).** `cendor-sdk` hard-depends on **`cendor-core` only**. tokenguard/acttrace/contextkit/squeeze/cassette integrate through the **bus / interceptor / `Sink` / `Compressor` protocols** they already implement — they auto-activate when installed; the SDK does not need to import them for governance to work. Optional convenience wiring is behind extras + lazy import.
2. **Keep core tiny (rule 3).** The SDK lives *above* core. Add to core only genuinely shared primitives; build orchestration concerns in the SDK.
3. **Namespace safety (rule 1).** New package owns `src/cendor/sdk/` only; **never** create `src/cendor/__init__.py`. Run **namespace-guard** before every commit/build/release.
4. **Governed by default, escapable.** Every governance layer must be optional/removable — an ungoverned `run()` must still work with just `cendor-core`.

---

## 3. What already exists vs. what's genuinely new

**Reused as-is (the 70%)**
- `cendor-core`: `instrument()` (openai chat+responses / anthropic / bedrock / gemini / ollama), `LLMCall`/`ToolCall` on the **bus**, `instrument_tool`, `tokens`, `prices`, `otel`, the `Reroute` interceptor seam, `add_interceptor`, and the new **`trace()` / `current_trace_id()`** correlation hook.
- `tokenguard`: `budget()` (block/clamp/downgrade/raise), `track()`, `report`, sinks (`SQLiteSink`/`OTelSink`/`QueueSink`).
- `acttrace`: `AuditLog` (hash chain, `max_entries`, `path`), `decision()`, `human_oversight()`, `guard()`, `Policy`, `verify()`.
- `contextkit`: assemble-to-budget + `for_anthropic`/`for_gemini`/`for_bedrock` message adapters; emits `AssemblyReport` on the bus.
- `squeeze`: `compress()` (a `Compressor`), `store`.
- `cassette`: record/replay via the interceptor seam → deterministic agent tests.

**Genuinely new in `cendor-sdk` (the 30%)**
- **The agent loop** (single + multi-agent orchestration).
- **Provider response normalization** — extract *assistant content + tool calls + finish reason* from each provider's response shape (core normalizes *usage*, not content). Per provider: openai chat, openai responses, anthropic, gemini, bedrock, ollama.
- **Tool schema generation** — python function → JSON Schema → each provider's tool/function format (openai functions, anthropic `tools`, gemini function declarations, bedrock toolConfig).
- **Structured output** handling (tool-forced or JSON-schema response).
- **Orchestration patterns** — handoff, supervisor/router, sequential/parallel, sub-agents.

*(contextkit already covers outbound message *formatting*; the SDK adds inbound *parsing* and tool schemas.)*

---

## 4. Repo strategy, packaging & dependency design

### Repo: a separate `cendor-sdk` repo (decision)
`cendor-sdk` lives in its **own repo** (`cendorhq/cendor-sdk`), not the monorepo. Rationale: the libraries + site are launch-ready and stable; the SDK is a long-horizon, fast-churning product. Incubating it separately keeps the launch repo clean and lets the SDK iterate without touching the libraries' repo/CI.

- **`cendor-core` stays in the monorepo** with the libraries — core co-evolves tightly with them (*"grow core only as each tool needs it"*). The SDK consumes **published** core like any external user. (See §12 for why core is *not* split out — SemVer, not repo boundaries, prevents conflicts.)
- **Dev-time source override** for fast iteration against a local core without publishing every change:
  ```toml
  # cendor-sdk/pyproject.toml  (dev only; released build pins the PyPI version)
  [tool.uv.sources]
  cendor-core = { path = "../Cendor/packages/cendor-core", editable = true }
  ```
- **Carry namespace-guard into the new repo.** `cendor-sdk` is another distribution contributing to the PEP 420 `cendor` namespace. It must own `src/cendor/sdk/` **only** and never ship `src/cendor/__init__.py`. Add the guard check to its CI — rule 1 applies across repos, and this is the #1 way multi-repo namespace packages break.

### Packaging: batteries-included via *dependencies*, not vendoring
`cendor-sdk` is the "all-in-one" door: `pip install cendor-sdk` bundles the whole stack **by depending on the published libraries** and **re-exporting** their primitives, so the user installs once and imports only from `cendor.sdk`.

- **Bundle by dependency** — depends on `cendor-core`, `cendor-tokenguard`, `cendor-acttrace`, `cendor-contextkit`, `cendor-squeeze`, `cendor-cassette` (loose ranges `>=1.x,<2`). Provider SDKs stay optional extras (`[openai]`/`[anthropic]`/…); `[mcp]` in Phase 3.
- **Re-export a curated surface** — `from cendor.sdk import Agent, tool, run, budget, guard, Policy, AuditLog, track` (the governance objects are the *real* tokenguard/acttrace ones, re-exported). Feels standalone; one import namespace.
- **NEVER vendor.** Do not copy library code into the SDK. Each distribution owns **exactly one** `cendor.*` subpackage — `cendor-sdk` owns `cendor.sdk` only. Two distributions both providing e.g. `cendor.tokenguard` would collide the namespace and break every import (rule 1).
- **No version skew** — loose ranges mean a user who installs `cendor-sdk` *and* a library à la carte gets the same distributions at one compatible version.

### Layout (in the `cendor-sdk` repo)
```
cendor-sdk/
  pyproject.toml          # deps: cendor-core + tokenguard + acttrace + contextkit + squeeze +
                          #   cassette (>=1.x,<2). extras: [openai]/[anthropic]/[google]/[bedrock]/
                          #   [ollama] -> provider SDKs; [mcp] -> mcp client (Phase 3)
  src/cendor/sdk/         # the ONLY cendor.* subpackage this distribution ships
    __init__.py           # public API + re-exports (Agent, tool, run, budget, guard, Policy, ...)
    agent.py              # Agent, sub-agents, handoff targets
    tools.py              # tool() decorator, schema generation, per-provider tool formatting
    runner.py             # the loop: assemble -> call -> parse -> tools -> repeat; trace() scope
    providers.py          # response normalization (content+tool_calls) per provider
    orchestration.py      # handoff / supervisor / sequential / parallel (Phase 2)
    memory.py             # session history + optional local persistence (Phase 2)
    result.py             # Run/Step/Result data model
    mcp.py / a2a.py       # Phase 3
  tests/                  # cassette + respx, no network
  README.md               # killer metric + copy-paste governed-agent example + badge
  docs/sdk.md
  .github/workflows/      # CI incl. namespace check + ruff/mypy/pytest (reuse monorepo config)
```

**Dependency rule:** the SDK *bundles* all libraries (batteries-included) and re-exports them, but internally they still cooperate through core's seams — an advanced user can bypass governance and an ungoverned `run()` still works on core alone. Rule 2 stays intact: the SDK is a top-of-stack **consumer** (like the `cendor` umbrella meta-package), not a peer tool.

---

## 5. Public API surface (target shape)

```python
from cendor.sdk import Agent, tool, run, Runner, handoff

@tool
def search(query: str, top_k: int = 3) -> list[str]:
    """Search the KB."""            # schema derived from hints + docstring
    ...

planner = Agent(
    name="planner",
    model="claude-opus-4-8",        # any core-supported provider id
    instructions="Plan the research.",
    tools=[search],
    handoffs=["writer"],            # Phase 2
)
writer = Agent(name="writer", model="gpt-4o", instructions="Write the brief.")

# Ungoverned (core only):
result = run(planner, "Research X")            # sync
result = await run.aio(planner, "Research X")  # async

# Governed (compose via seams — nothing SDK-specific needed):
from cendor.tokenguard import budget, track
from cendor.acttrace import AuditLog, guard, Policy

log = AuditLog(system="research", risk_tier="high", path="audit.jsonl", max_entries=50_000)
with budget(usd=2.00, on_exceed="block"), guard(Policy.gdpr(), audit=log), track(feature="research"):
    result = run([planner, writer], "Research X and write a brief")   # multi-agent, Phase 2

result.output            # final answer / structured object
result.steps             # per-turn LLMCall/ToolCall records (correlated by trace_id)
result.usage, result.cost
```

**Data model** (`result.py`): `Run` (id == trace_id, agents, steps, output, usage, cost), `Step` (agent, LLMCall, tool ToolCalls, timing), `Result`. Steps *are* the `LLMCall`/`ToolCall` objects already on the bus — the SDK just correlates and returns them.

---

## 6. How one turn executes (the loop)

For each agent turn, `Runner`:
1. **Assemble** the prompt/context to the model's budget via **contextkit** (optional; falls back to raw messages). `AssemblyReport` → bus → audited.
2. **Format** messages + tool schemas for the target provider (contextkit adapters + SDK tool formatter).
3. **Call** the model through a `cendor-core`-**instrumented** client inside a `trace(run_id, agent=…)` scope → emits an `LLMCall` (usage/cost/reasoning) on the bus with the run's `trace_id`. Pre-call, the **budget/guard interceptors** fire (enforcement: block/clamp/downgrade, redact-before-send).
4. **Normalize** the response (SDK `providers.py`) → assistant content + tool calls + finish reason.
5. If tool calls: **execute** each (wrapped by `instrument_tool` → `ToolCall` on bus, same `trace_id`); optionally **squeeze**-compress large results; append results; **loop to 1**.
6. Else: finalize → structured output parse if requested → `Result`.

Budgets, audit, redaction, correlation, and record/replay all ride steps 3–5 through existing seams — **zero bespoke wiring**. `cassette.using(...)` around a run records/replays the whole trajectory deterministically.

---

## 7. Phases

### Phase 1 — Governed single agent (MVP) — *the wedge lands here*
**Deliverable:** `Agent`, `tool`/`Tool`, `run`/`Runner`, single ReAct-style loop, sync **and** async, over `cendor-core`. Provider-agnostic (all core providers). Structured output (table stakes). Streaming (returns core's now-context-manager-safe stream events). Basic session history (in-memory).
- Provider **response normalization** for openai (chat+responses), anthropic, gemini, bedrock, ollama.
- **Tool schema generation** + per-provider tool formatting.
- **Governance composes**: `budget()`, `guard(Policy)`, `AuditLog`, `track()`, `cassette.using()` all work against a `run()` with no SDK-specific glue, correlated by `trace()`.
- **Result** model with per-step `LLMCall`/`ToolCall`, usage, cost.
- Docs: `docs/sdk.md` + README (killer metric: "a governed agent in 10 lines"); a runnable, network-free example.
- Tests: cassette-recorded + respx-mocked single-agent runs across ≥2 providers; assert usage/cost/reasoning captured, budget blocks, guard redacts, audit chain verifies, replay is deterministic.
**Exit:** a governed single agent runs on OpenAI *and* Anthropic with budgets + audit + redaction + replay, no network in tests.

### Phase 2 — Multi-agent orchestration
**Deliverable:** the orchestration patterns.
- **Handoff** (agent transfers control to a named peer, OpenAI-Agents-style) via a synthetic transfer tool.
- **Supervisor/router** (a coordinator agent selects sub-agents), **sequential** and **parallel** pipelines.
- **Sub-agent correlation**: nested `trace()` (parent run id + per-agent child id) so a whole multi-agent trajectory is one correlated tree — the thing that was impossible beneath frameworks.
- **Per-agent budget/audit scoping**: `track(agent=…)`; per-agent `budget()`; audit `decision()` per agent step.
- **Memory/session**: conversation state across turns; optional **local** persistence (JSON/SQLite via a `Sink`), resumable within a process.
- Tests: multi-agent handoff + supervisor runs; assert one correlated `trace_id` tree, distinct agents distinguishable, per-agent budgets enforced, full audit of the trajectory.
**Exit:** a supervisor + 2 sub-agents run with per-agent budgets and a single verifiable audit trail; handoff works across providers.

### Phase 3 — Ecosystem & interop
**Deliverable:** make `cendor-sdk` agents first-class citizens elsewhere.
- **MCP client** (`[mcp]` extra): consume MCP tools/resources as `Tool`s.
- **A2A expose**: serve a `cendor-sdk` agent over the A2A protocol (local server helper — clearly optional, still no *required* server).
- **Publish as a custom-engine agent** (ties to earlier Copilot/Foundry analysis): a messaging-endpoint/adapter shim so a governed `cendor-sdk` agent can be surfaced in Microsoft 365 / Foundry; optionally feed the whole-run OTel span tree via `otel`.
- **Full-run OTel span tree**: emit `gen_ai.*` agent/tool/LLM spans for the run (uses `cendor-core.otel`), so the trajectory shows up in Foundry/Datadog/etc.
- **Human-in-the-loop**: pause/approve/resume hook wired to `acttrace.human_oversight()`.
- Tests: MCP tool round-trip (mocked), A2A serve+call (in-proc), OTel span tree assertions, HITL pause/resume + oversight entry in the chain.
**Exit:** an MCP tool works inside an agent; the agent is callable over A2A; a run emits a correlated OTel span tree; a HITL approval is recorded in the audit chain.

### Phase 4 — Production hardening & governed eval
**Deliverable:** the "safe for real workloads" layer + the testing wedge fully realized.
- **Resilience**: retries, timeouts, provider rate-limit/backoff, partial-failure handling — aligned to SDK behaviour (no failure modes the provider SDK lacks).
- **Resumable/checkpointed runs** (local): persist run state so a long agent can resume after restart (SDK-owned, still local-first).
- **Durable memory** stores (pluggable `Sink`-shaped backends; local by default).
- **Governed eval / regression harness**: cassette-backed replay of recorded agent trajectories as *tests* — assert output, tool sequence, and **cost/token ceilings** don't regress. (Seeds a possible future `cendor-eval`.)
- Tests: injected failures recover; a checkpointed run resumes; an eval suite catches a cost regression and a tool-sequence change.
**Exit:** a long-running agent survives a simulated restart; an eval suite gates cost + behaviour regressions in CI, offline.

---

## 8. Differentiation (why not "just another framework")

| | Provider lock | Cost budgets | Tamper-evident audit | PII redaction | Record/replay tests | Local-first |
|---|---|---|---|---|---|---|
| OpenAI Agents SDK | OpenAI-centric | ✗ | ✗ | ✗ | ✗ | lib |
| LangGraph | agnostic | DIY | DIY | DIY | DIY | lib |
| Anthropic Agent SDK | Anthropic-centric | ✗ | ✗ | ✗ | ✗ | lib |
| CrewAI / Pydantic AI / ADK | varies | ✗/DIY | ✗ | ✗ | ✗ | lib |
| **cendor-sdk** | **agnostic** | **built-in** | **built-in** | **built-in** | **built-in** | **yes** |

### Positioning: two doors, libraries first
The **libraries are the primary identity** — *"production plumbing beneath your framework"* — the differentiated wedge that works with everyone's stack. **`cendor-sdk` is the secondary, simpler door:** the batteries-included, governed agent SDK for teams who **don't want to adopt another framework** and just want a governed agent out of the box. The SDK is *"you don't need LangChain — or to wire the libraries — just use this,"* never *"our flagship framework competing with LangGraph."* This keeps the wedge sharp and sidesteps the red-ocean framework race.

Frame the choice by **audience/situation, not primacy** — the simpler door usually gets more traffic, so don't signal it's second-class or under-invest in it:

> **Already have a framework?** → compose the **libraries** beneath it.
> **Starting fresh / want it simple?** → **`cendor-sdk`**.

Site copy (two front doors):
- *Cendor libraries* (primary CTA) — "Production plumbing for your LLM app. Drop cost, governance, and testing beneath the framework you already use."
- *cendor-sdk* (secondary CTA) — "Don't want to pick a framework? A governed agent in 10 lines — budgets, audit, and PII redaction built in."

**Graduation funnel (shared primitives):** both doors expose the *same* primitives — `budget`, `guard`, `Policy`, `AuditLog`, `trace`. A team that starts on the SDK and later adopts a framework drops to the libraries underneath with **no concept rewrite and no churn-out**; movement between doors is continuous, not a migration.

Beachhead for the SDK = greenfield / regulated teams that want governance without assembly (ties to acttrace's EU AI Act *evidence* framing + tokenguard's cost governance). Compete on **governance + provider-agnosticism + simplicity**, not orchestration breadth.

---

## 9. Constitution reconciliation (CLAUDE.md)

- **Rule 1 (namespace):** `src/cendor/sdk/` only; never `src/cendor/__init__.py`. Since `cendor-sdk` is a **separate repo/distribution** in the PEP 420 `cendor` namespace, **carry the namespace-guard check into its CI** and run it each release — the top failure mode for multi-repo namespace packages.
- **Rule 2 (no tool→tool deps):** the SDK **bundles** all libraries (batteries-included) and re-exports them — permitted because it is a top-of-stack **consumer** (like the `cendor` umbrella meta-package), not a peer "tool"; the plumbing tools still cooperate only through core. **Update CLAUDE.md** to name `cendor-sdk` as the orchestration layer above the libraries (a small "What this is" amendment).
- **Rule 3 (tiny core):** orchestration lives in the SDK; only add to core if genuinely shared (candidate: none required — `trace()` already landed).
- **Rule 4 (local-first, no servers):** SDK is a library; A2A serve / HITL server helpers are strictly optional and opt-in.
- **Rule 5 (no empty packages):** ship Phase 1 with tests + README + a real `v0`.
- **Rule 6 (evidence, not guarantee):** unchanged — audit is evidence.
- **Positioning:** libraries stay the **primary** identity ("beneath frameworks"); `cendor-sdk` is the **secondary, simpler** all-in-one door for framework-less teams (see §8). Update root `README.md` and the site to present both doors, audience-framed.

---

## 10. Testing strategy
- **No network, ever.** respx-mock provider HTTP (the harness in `plan/scratch/`), **pin** provider/framework versions.
- **cassette** is dual-purpose: the SDK's replay engine *and* its test fixture format — record a trajectory once, replay in CI.
- Per phase: unit (loop, response normalization per provider, tool schema gen) + integration (governed run across ≥2 providers) + property (loop termination, budget monotonicity).
- Governance assertions: budget blocks/clamps/downgrades; guard redacts-before-send; audit chain `verify()`s; correlation `trace_id` tree; deterministic replay.
- `uv run pytest` green; `uv run ruff check . && uv run ruff format .`; namespace-guard clean.

## 11. Docs & site
- **`cendor-sdk` repo:** its own `README.md` (package-readme skill: killer metric + copy-paste governed-agent example + badge), `docs/sdk.md`, `CHANGELOG.md`, and CI (ruff/mypy/pytest + namespace check).
- **Monorepo (libraries):** update `docs/architecture.md` (name the SDK as the top-of-stack **second door**), `docs/index.md`, and root `README.md` to present the two doors — libraries primary, SDK secondary. Add `docs/versioning.md` (§12).
- **cendor-site:** new SDK page (`src/pages/libraries/sdk.astro` or a top-level route); a "Build a governed agent" quickstart; update `libraries/index.astro` + the landing + site README to present the two front doors, audience-framed (§8 copy). **Sequencing:** the site launches with the **libraries first**; add the SDK door **later**, when the SDK is ready to announce. (First check the docs-sync path in `cendor-site/scripts/`.)

## 12. Versioning & release
- New `cendor-sdk` (its own repo) starts at **0.1.0** (Phase 1), → **0.x** through Phases 2–3, → **1.0.0** when the loop + multi-agent + interop are stable and documented.
- **Core stays in the monorepo — do NOT split it out.** Conflicts between the libraries and the SDK are prevented by **SemVer + loose constraints, not repo boundaries**: keep `cendor-core` tiny and backward-compatible (additive minors, rare majors); every consumer depends on `cendor-core>=1.x,<2`. Then a user installing a library *and* the SDK resolves to one compatible core — no skew. Splitting core would only impose a cross-repo release dance on the libraries (which co-evolve with it) for zero conflict-prevention benefit.
- `cendor-core` bumps only if a shared primitive is genuinely needed (aim: none — `trace()` already landed); such a bump is a normal additive minor.
- **Write the contract down:** add `docs/versioning.md` in the monorepo stating the core SemVer policy + the `>=1.x,<2` convention, referenced by both the libraries and the SDK.
- Release builds pin published deps; dev uses the `uv` source override (§4).
- `cendor-sdk` is the second "door"; the `cendor` umbrella (all libs, no SDK) and à-la-carte libs are the first — see §8.

## 13. Risks & mitigations
- **Scope explosion** → thin opinionated core; governance wedge over feature parity; §1 non-goals are hard.
- **Positioning dilution** ("just another framework") → libraries are the **primary** identity; the SDK is the **secondary, simpler** door (§8). Frame by audience, not primacy — and don't under-invest in the SDK just because it's "second"; the simpler door often draws the most traffic.
- **Namespace collision across repos** → **never vendor** library code; one `cendor.*` subpackage per distribution (`cendor-sdk` ships `cendor.sdk` only); run the namespace-guard check in the SDK repo's CI.
- **Version skew (libs ↔ SDK)** → loose `>=1.x,<2` ranges + core SemVer discipline (§12); a user installing both resolves to one compatible core.
- **Cross-repo dev friction** → `uv` source override to a local core during development; pin the PyPI version for releases (§4).
- **Provider response-shape drift** → normalization isolated in `providers.py` with per-provider golden tests; ride core's provider abstraction.
- **Maintenance treadmill** → decline breadth; MCP/A2A give interop without re-implementing others' features.
- **Governance coupling creep** → the SDK bundles all libs, but internally they cooperate only through core's seams; an ungoverned `run()` must always work on core alone (Rule 2 holds — the SDK is a consumer, not a peer tool).

## 14. Success criteria
- **Phase 1:** a governed single agent (budget + audit + redaction + replay) runs on OpenAI *and* Anthropic; tests offline; ruff + namespace-guard clean; `import cendor.sdk` works.
- **Phase 2:** supervisor + 2 sub-agents, per-agent budgets, one verifiable correlated audit trail; handoff across providers.
- **Phase 3:** MCP tool inside an agent; agent callable over A2A; correlated OTel span tree; HITL approval recorded in the chain; publishable as a custom-engine agent.
- **Phase 4:** long-running agent resumes after a simulated restart; a cassette-backed eval suite gates cost + behaviour regressions in CI.
- **Throughout:** provider-agnostic, local-first, no network in tests, governance composable and removable.

---

## Appendix — minimal Phase-1 governed agent (target)

```python
from cendor.sdk import Agent, tool, run
from cendor.tokenguard import budget
from cendor.acttrace import AuditLog, guard, Policy

@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"

agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather],
              instructions="Answer using tools when helpful.")

log = AuditLog(system="support", risk_tier="limited", path="audit.jsonl")
with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
    result = run(agent, "What's the weather in Paris?")

print(result.output)                 # -> "It's sunny in Paris."
print(result.cost, result.usage)     # priced, budgeted
print([s.name for s in result.tool_steps])   # ["get_weather"]
# audit.jsonl: audit_open -> llm_call -> tool_call -> llm_call, hash-chained & verify()-able,
# all sharing one trace_id. Wrap in cassette.using("run.json") to replay it as a test.
```
