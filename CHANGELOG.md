# Changelog

All notable changes to `cendor-sdk` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.19.0] — 2026-07-25
**A blocked run now shows *why* — inline on the run, with no audit object** (Option C, DR-2c; see
`cendor-core` 1.13.0).

`live_spans` renders `cendor-tokenguard`'s budget events and `cendor-guardrails`' decisions as
`governance.*` **children of the `agent.run` root**, using core's `cendor.gov.*` vocabulary. So an app
that writes zero governance code still sees the budget that stopped a run and the guardrail that
tripped, in the trace, next to the steps they governed.

### Added
- `governance.budget_event` / `governance.guardrail_decision` children on the live run tree.

### Unchanged, deliberately
- **The audit mirror wins**: with an `AuditLog` in play the chained `audit.*` spans are the rendering
  and these stand down — never two renderings of one decision.
- **No `audit.*` vocabulary and no `reason` string** on these spans (rule 6: a rule's reason can carry
  input-derived text — the audit chain keeps it, the default-on span does not).
- `CENDOR_TELEMETRY=off` disables them with everything else.

### Changed
- Floors: `cendor-core>=1.13`, `cendor-acttrace>=1.12`.

## [1.18.0] — 2026-07-25
**A governed run is now visible with zero telemetry code** (see `cendor-core` 1.12.0 for the switch).

⚠️ **Default-behaviour change.** If your app configures an OpenTelemetry provider
(`configure_azure_monitor()`, a plain `set_tracer_provider`, an OTLP endpoint pointed at Cendor
Monitor…) and you upgrade, `run()` opens the run scope itself: you get the `agent.run` root with its
steps as children, the usage/cost rollups, `gen_ai.conversation.id` from your `session`, and — because
the root is the active span — **governance correlated to the run**. Previously this needed
`with live_spans(...):` around every run.

### Added
- **Automatic run scope** on `run()`, `run.aio()`, `run.stream()` and `run.astream()`. It opens only
  when `CENDOR_TELEMETRY` isn't `off`, a provider is configured, and **no explicit `live_spans()`
  scope is open** — an explicit scope always wins, so there is never a second root. Without
  OpenTelemetry installed nothing happens at all.
- The conversation id comes from `session.id` (the id *you* chose); `cendor.run.label` stays empty
  unless you pass one — a label is a human-authored tag and the SDK invents no identity.

### Changed
- Floors: `cendor-core>=1.12` (the switch), `cendor-tokenguard>=1.6` (the spend tap),
  `cendor-acttrace>=1.11` (the mirror auto-attach). Together they are what makes a run's trajectory,
  spend **and** governance land in your backend from a zero-telemetry-code app.
- The scope predicate is failure-safe: any error resolving it (e.g. an older `cendor-core`) is treated
  as "no scope" rather than raised into your run.

## [1.17.0] — 2026-07-24
Findings-closure wave — a fix for streamed-async checkpointing and multi-agent pipeline-shape
governance parity with the TypeScript SDK. Backwards-compatible (new keyword-only options preserve
today's behaviour); no `cendor-core` changes.

### Fixed
- **`run.astream(checkpoint=…)` now persists (it was accepted but not applied).** The async
  streaming entry point took a `checkpoint=` argument and its docstring promised the same S13
  semantics as `run.stream`, but the argument was never forwarded to `stream_agent_async` /
  `stream_agents_async` — so streamed-async checkpointing was silently a no-op. It now saves per turn
  (single agent) / per turn + segment (team) and done-resumes to a lone terminal `RunComplete`,
  matching `run.stream` and the TypeScript twin. Covered by a red-first async test (the twin of the
  sync S13 tests).

### Added
- **Pipeline-shape governance parity (TS-observed).** `sequential` / `parallel` / `parallel_async`
  now accept the honoured per-run surface — `retry`, `on_step`, and `guardrails` (a per-run override
  of each agent's list; decisions collected into `Result.guardrail_decisions`) — in addition to
  `audit` / `max_turns`. `supervisor` gains `session`, `checkpoint`, `on_step`, `guardrails`, and
  `retry`, delegating to `run_agents` exactly as the TypeScript `supervisor` rides `runAgents`
  (`run_agents` / `run_agents_async` also forward `retry` now). This mirrors the TypeScript behaviour
  1:1.

### Honest limits
- **`session` / `checkpoint` are team-only; `guardrail_mode` is single-agent-only.** The pipeline
  shapes (`sequential` / `parallel` / `parallel_async`) pipe or fan out over independent per-agent
  inputs — there is no single conversation to persist or resume — so they don't take `session` /
  `checkpoint`; use a handoff team (`run([...])`) or `supervisor` for that. `guardrail_mode`
  (`"parallel"`) applies only to single-agent runs; team and pipeline shapes always gate in blocking
  mode. Both languages behave the same (see [`multi-agent.md`](docs/multi-agent.md)).

## [1.16.0] — 2026-07-24
SDK telemetry wave — structural signals for RAG, memory, orchestration, checkpoints, tools, and MCP
become first-class `cendor.sdk` spans, so an OTel backend (or Cendor Monitor) renders each as its own
domain. **Zero core changes** — the new signals ride `cendor-core`'s bus and the existing
`live_spans()` scope; content rules are unchanged (labels/ids/counts only, never message bodies).

### Added
- **RAG spans (`rag.assemble` / `rag.compress`):** an always-on retriever's `context_budget` assembly
  (contextkit `AssemblyReport`) and squeeze compression (`CompressionEvent`) now surface as `cendor.sdk`
  child spans inside a `live_spans()` run — budget/used, blocks kept vs dropped, token deltas, technique.
- **Memory spans (`memory.load` / `memory.save`):** a run that reads/writes a `Session` emits a span
  carrying the session id, turn count, and byte size.
- **Orchestration handoff spans (`orchestration.handoff`):** each `transfer_to_<peer>` handoff emits an
  edge (from-agent → to-agent, segment, transfer tool) so a monitor reconstructs the multi-agent graph
  from rows rather than parsing trace-id families.
- **Checkpoint spans (`checkpoint.save` / `checkpoint.resume`):** a `Checkpointer` write and a resume
  decision (finished or unfinished) emit spans carrying the run id, done flag, and turn count.
- **Tool source + outcome:** every `execute_tool` span now carries `cendor.tool.source`
  (`local` | `mcp`, + `cendor.tool.mcp.server`/`transport`) and `cendor.tool.outcome`
  (`ok` | `error` | `blocked`). A `tool_call` guardrail **block** — which runs no tool and so emits no
  `ToolCall` — now emits a dedicated `execute_tool {name}` span with `outcome="blocked"` +
  `cendor.tool.blocked_by` (the guardrail name; never the reason text).
- **MCP server attribution:** `load_mcp_tools(session, server=…, transport=…)` tags each tool's spans
  with the MCP server + transport and emits `mcp.connect` / `mcp.list_tools` lifecycle spans, so a
  monitor attributes tool calls per server. The labels are optional and non-secret.

### Honest limits
- The new domain telemetry rides the **live** path (`live_spans`); the post-hoc `span_tree` continues
  to render the LLM/tool tree from `Result` (and now also stamps tool source/outcome).
- The tool-source registry is process-global; a tool-name collision across two MCP servers is
  last-writer-wins.

## [1.15.0] — 2026-07-23
Phase-S follow-up — the parity items deferred from 1.14 land, both languages, on the same
`cendor-core` 1.10 / `cendor-tokenguard` 1.5 shelf (no new floor).

### Added
- **Streamed + team `conversation.id` (S6):** `run.stream`/`run.astream` and `run_agents`/`run_agents_async` now stamp `Result.conversation_id` from a keyed session and enter the conversation scope, so a monitor groups multi-turn streamed and multi-agent runs — previously single-agent-blocking only.
- **Streamed checkpoints (S13):** `run.stream`/`run.astream` accept `checkpoint=` (a path or `Checkpointer`) — per-turn saves for a single agent, per-turn + per-segment for a team. A finished checkpoint replays a lone terminal `RunComplete` (no model call, no re-yielded deltas); an unfinished resume continues from the saved messages without re-emitting prior deltas.
- **Bedrock forced-`toolChoice` structured output (S14):** a **tool-less** Bedrock agent with `output_type` now forces a synthetic schema-shaped tool (`toolConfig.toolChoice`), and the provider unwraps its input into the final answer — stronger than the JSON nudge. Gated to tool-less agents (a forced choice can't coexist with real tools on Converse); with tools present it falls back to the nudge (documented).

### Changed
- **Streamed-run RAG attributed (S11):** the four stream generators now prepare messages **inside** the run scope (collector + `trace` + per-agent budget), so an always-on retriever's embed call is attributed to the run rather than firing before it opened — parity with the blocking `run()` (GLR-4). No API change.

## [1.14.0] — 2026-07-23
Provider-capabilities wave on the new `cendor-core` 1.10 / `cendor-tokenguard` 1.5 shelf.

### Added
- **Anthropic incremental streaming + ThinkingDelta (S1/S2):** `run.stream`/`run.astream` on an Anthropic agent now emit text incrementally (`text_delta` → `TextDelta`), stream extended thinking (`thinking_delta` → `ThinkingDelta`), and reassemble tool calls from `input_json_delta` fragments (keyed by content-block index). Previously Anthropic fell back to one whole-response delta.
- **Native Anthropic structured output (S14):** `output_type`/`json_mode` on supported Anthropic families now sends `output_config.format` json_schema (normalized to `additionalProperties: false`) — schema-enforced, stronger than a prompt nudge. Older models degrade to the JSON-instruction path; Bedrock keeps the nudge (forced-`toolChoice` remains a documented honest limit).
- **Ollama + Bedrock images (S15):** a multimodal user turn with data-URL images translates to Ollama's `message.images` (base64) and Bedrock Converse image blocks (raw bytes). Remote http(s) image URLs stay unsupported (no fetching) — documented.

### Changed
- **Bedrock async no longer blocks the loop (S5):** `run.aio()` offloads boto3's blocking `converse` to a worker thread (`asyncio.to_thread`); contextvars propagate so the run's governance scope still attaches. Requires `cendor-core >= 1.10` / `cendor-tokenguard >= 1.5`.

### Honest limits (tracked for a follow-up, not in this release)
- Streamed/multi-agent `conversation.id` grouping, guardrails re-ask / stream-window in the TS port, streamed-run checkpoints, Bedrock forced-`toolChoice` structured output, and the finer TS span-attribute set.

## [1.13.0] — 2026-07-22
Wire the SDK onto the `cendor-core` ambient seam, harden `live_spans`, isolate streaming contexts, and add streamed thinking deltas.

### Fixed
- **Stream context isolation (GLR-9):** the streaming runners (`run.stream` / `run.astream`, single- and multi-agent) no longer leak the run's scopes (trace / attribution / budget / audit / collector) into the consumer between deltas. A consumer's own instrumented call made between two streamed events is no longer misattributed to the run (it was appended to `Result.steps`, counted against the run's budget, chained under its audit decision, and rendered as a live span). Sync streams advance inside a copied context; async streams relay through a producer task + queue (the design the TypeScript port always had). One behavior note: ambient scopes are captured **when the stream is created**, not re-read live per advance.

### Added
- **`ThinkingDelta` (GLR-12):** a new additive `run.stream` event surfacing streamed reasoning text for providers that stream it (Ollama `think` models; OpenAI-compatible `reasoning_content`), kept separate from `TextDelta`. Additive — a consumer that doesn't handle it is unaffected.

### Changed
- **`live_spans` (GLR-2/3):** reads the agent + conversation id from the event's stamped metadata (so they survive an out-of-scope streamed delivery), and renders only the observed run family — a concurrent run sharing the process bus no longer pollutes its steps / rollups / children.
- **RAG in scope (GLR-4):** for a fresh (non-resumed) `run()`, a retriever's embedding call is now attributed to the run (a collected step, trace-stamped, budgeted) instead of firing before the run opened.
- Requires `cendor-core >= 1.9`, `cendor-tokenguard >= 1.4`, `cendor-acttrace >= 1.10`, `cendor-cassette >= 1.1` (the ambient-seam shelf).

## [1.12.0] — 2026-07-21
Emission truth on governed journeys — TTFT, estimated-usage, and run.agents on the SDK span paths (Monitor v5 G-V4-1/2/3). Additive; a monitor renders these, nothing changes for a run that doesn't stream or governs no agents.

### Added
- **TTFT inside a governed journey (G-V4-1).** `span_tree` and `live_spans` now stamp `cendor.ttft_ms` on a streamed `chat` span (recovered on the first chunk by `instrument()`), so time-to-first-token shows on real SDK workloads — previously only a bare libs-only streamed call carried it.
- **Estimated-usage provenance (G-V4-3).** When a streamed call reported no usage and the token count was recovered by offline estimate, the `chat` span carries `cendor.usage_estimated="true"` — truth = the product, so a monitor shows those tokens as *est.* rather than exact. Stamped only when set.
- **Run agents on the live root (G-V4-2).** `live_spans` accumulates the participating agents and stamps `cendor.run.agents` on the `agent.run` root at close, reaching parity with the post-hoc `span_tree` (so a monitor's Agents view fills for live-streamed runs too).

## [1.11.0] — 2026-07-20
Opt-in content on the run spans + auto session grouping — the SDK half of the Cendor journey console (Monitor v3). Backward-compatible (additive); content stays OFF unless you opt in with `cendor.core.otel.capture_content()`.

### Added
- **Opt-in content on `span_tree` / `live_spans` (G17/G18).** When content capture is on (core 1.7's `otel.capture_content()`), each `chat` step span carries `gen_ai.input.messages`, `gen_ai.output.messages` (incl. parsed **thinking** parts), and `gen_ai.system_instructions` (extracted from the request kwargs for Anthropic/Responses/Gemini/Bedrock, where the system prompt isn't a message); each `execute_tool` span carries `cendor.tool.arguments` / `cendor.tool.result`. All masked + byte-capped by the core config, and **never** on `audit.*` spans (rule 6). Off by default.
- **Auto conversation id (G19).** `run(session=…)` propagates the session's key as `gen_ai.conversation.id` on the run span — `SQLiteSessionStore.load(id)` now stamps `Session.id`, and `Session(id=…)` works for the in-memory case. `live_spans` reads it live; `span_tree` reads it from `Result.conversation_id` (new field). An explicit `conversation_id=` argument still wins; a conversation id is **never synthesized** (semconv).
- **Cassette replay flag on run spans (G22).** A replayed `chat` step carries `cendor.replayed=true`.
- **G20 co-existence.** `live_spans` signals core's opt-in bus→span emitter to stand down while it owns the spans (no double emission).

### Changed
- Dependency floor `cendor-core >= 1.7` (content-capture helpers).

## [1.10.0] — 2026-07-20
Richer run spans for the monitor: agent-named, step-numbered call spans, a live tree at parity with the post-hoc one, and an opt-in run label. Backward-compatible (additive). Dependency floors bumped to the V2 emission wave (`cendor-tokenguard >= 1.3`, `cendor-acttrace >= 1.7`, `cendor-guardrails >= 1.6`).

### Added
- **Run label (`live_spans(label=…)` / `span_tree(result, label=…)`)** → `cendor.run.label` on the root `agent.run` span, so a monitor can show what a run was *for*. It is a deliberate, short, non-sensitive string you choose — **never derived from the prompt** (prompts/tool values stay off spans).
- **Agent name + step index on every call span.** `chat`/`execute_tool` spans now carry `gen_ai.agent.name` (which agent made the call) and a 1-based `cendor.step` ordinal, in both `span_tree` and `live_spans`.
- **`live_spans` parity with `span_tree`.** The live root now carries `cendor.run.id` / `cendor.trace_id` (learned from the run's correlation id) and, at close, the run's `gen_ai.usage.input_tokens` / `output_tokens` and `cendor.run.cost_usd` rollups.

### Changed
- The per-agent `max_usd` ceiling opened by the SDK is now **named** (`agent:<name> max_usd`), so a block by an agent's cap is attributable in a monitor (via `tokenguard 1.3`'s `budget(name=)`).

## [1.9.0] — 2026-07-20
`gen_ai.conversation.id` grouping for multi-turn runs. Backward-compatible (additive, opt-in).

### Added
- **`live_spans(..., conversation_id=…)`** and **`span_tree(result, ..., conversation_id=…)`** now
  accept an optional conversation/session id (e.g. your `SQLiteSessionStore` key). When given, the
  root `agent.run` span carries it as the OpenTelemetry `gen_ai.conversation.id` semantic-convention
  attribute, so a backend can group the runs of one multi-turn conversation. Omitted by default (no
  key leaks when you aren't grouping). Libs-only users can pass any `gen_ai.*` attribute — including
  `gen_ai.conversation.id` — through `cendor.core.otel.span(...)`'s existing attributes argument.
  See https://cendor.ai/docs/observability

## [1.8.0] — 2026-07-19
OpenTelemetry observability export, re-exported from the governance libraries. Backward-compatible.

### Added
- **`OTelMirror`** (from `cendor-acttrace` ≥ 1.6) and **`BudgetEvent`** (from `cendor-tokenguard` ≥ 1.2) are now re-exported on the SDK surface. Attach `AuditLog(system, mirror=OTelMirror())` to stream the governed loop's audit trail — decisions, guardrail actions, `budget_event`s, human oversight — to any OpenTelemetry backend as an operational copy (the hash-chained file stays the sole `verify()` evidence). Pre-flight budget actions (`blocked`/`downgraded`/`clamped`) ride the bus as `BudgetEvent` and are chained + mirrored. Pairs with the existing `cendor.sdk.otel` span tree. See https://cendor.ai/docs/observability

### Changed
- Dependency floors: `cendor-acttrace>=1.6`, `cendor-tokenguard>=1.2`.

## [1.7.2] — 2026-07-19
Gemini multi-turn tool loops (gemini-3.x). No API changes; a pure bug-fix release.

### Fixed
- **Gemini 3.x tool loops no longer 400 on the replay turn.** gemini-3.x returns a `thought_signature` alongside each `function_call` that must be echoed back on the next turn; without it the API rejects the replayed call (`Function call is missing a thought_signature in functionCall parts`). `ToolInvocation` now carries `thought_signature`, the Gemini adapter captures it on parse, and the canonical→Gemini translation re-emits it. Other providers are unaffected (the field is `None`).

## [1.7.1] — 2026-07-18
Provider-adapter fixes surfaced by live black-box testing (Gemini, Ollama, structured output). No API changes; a pure bug-fix release.

### Fixed
- **Gemini tool calls no longer 400.** `Tool.to_gemini()` stamped `additionalProperties` (and other JSON-Schema keys) into the function declaration, which google-genai rejects with `400 INVALID_ARGUMENT` — so every tool-equipped Gemini agent failed. The declaration schema is now sanitized to the subset Gemini's `Schema` proto accepts (recursively).
- **`run.aio()` works on Gemini.** The async path wrapped the *sync* `client.models.generate_content` and then awaited its non-awaitable result (`TypeError: object GenerateContentResponse can't be used in 'await'`). Async runs now target google-genai's async-native `client.aio.models.generate_content`. The async-detection shim is also hardened to never crash on a sync-only client (it runs it blocking instead), so a future sync-only provider degrades gracefully.
- **Ollama tool loops survive the replay turn.** Canonical history stores tool-call `function.arguments` as a JSON string; the ollama client requires a dict and rejected the string (pydantic `dict_type` / server 400 `Value looks like object, but can't find closing '}' symbol`). `OllamaProvider.build_kwargs` now re-hydrates arguments to dicts for the ollama wire.
- **Structured output tolerates fenced/wrapped JSON.** `output_type=` parsing did a bare `json.loads` and silently returned the raw string when a provider without a native JSON-schema mode (Anthropic/Ollama/HF) wrapped its JSON in a ```` ```json ```` fence or prose — breaking the declared type. Parsing now strips the fence / extracts the first balanced JSON value first.

## [1.7.0] — 2026-07-14
The SDK inherits the libraries — verified. `guard` is now the identical acttrace object, embeddings are governed pre-flight, the pii bridge honors per-category actions, and a new parity/identity test suite pins every re-export so drift fails the build. Floors: `cendor-core>=1.6`, `cendor-acttrace>=1.5`.

### Changed
- **`guard` is the identical `cendor.acttrace.guard` object** (`cendor.sdk.guard is cendor.acttrace.guard`). acttrace 1.5.0's return is dual-shape (a raw interceptor that is also a context manager), so the SDK's context-manager wrapper is deleted. Existing `with guard(policy, audit=…, on_block=…):` code keeps working unchanged; `PolicyViolation` (the default block exception) is now exported from `cendor.sdk` so you can catch it without a lib import.
- **`embed()`/`aembed()` are governed pre-flight.** The embeddings call now rides the instrumented client and core (≥ 1.6) captures it — so a keyless `budget(usd=…, on_exceed="block")` refuses an over-budget embed *before* it fires and a `guard(...)` can redact the text before the provider sees it. The SDK's hand-built emit shim is deleted (no double emission; `metadata["embedding"]` and trace correlation unchanged).
- **Behavior fix — `rules.pii`/`secrets`/`entropy` honor per-category policy actions** (via acttrace's new `resolve_findings`, the same resolution `guard()` applies). A category the policy resolves to `block` now blocks and a `redact` category is scrubbed **regardless of `action=`**; the explicit `action=` param applies only to findings the policy leaves at flag tier. Concretely: `pii(Policy.gdpr(), action="redact")` now *blocks* a `special_category` finding instead of merely scrubbing it. To purely observe, use an all-flag policy — not `action="flag"`.
- **`Result.usage` aggregates through core's field-complete `Usage` arithmetic** (`sum_usage`) — a future `Usage` field can no longer silently vanish from the aggregate.
- **`register_model_price` writes through core's contractual `prices._register` hook** — registrations now survive `prices.refresh()` (they used to be dropped with the table swap).
- **Never-retry is `isinstance`-matched** on the real `BudgetExceeded`/`PolicyViolation` classes (a lib exception rename can no longer silently turn never-retry into retry).

### Added
- **New re-exports:** `PolicyViolation` (acttrace); `GuardrailDecision`, `Verdict` (guardrails — the types of `Result.guardrail_decisions`); `LLMCall`, `ToolCall`, `Usage`, `Money` (core — typing parity with `@cendor/sdk`); `downgrades`, `clamps` (tokenguard — see what a pre-flight downgrade/clamp rerouted).
- **`EvalCase(normalizer=…)`** — forwarded to cassette's replay matching, so prompts embedding timestamps/uuids still replay.
- **`ContextBudgetFallback` bus event** — a failed `context_budget` assembly still falls back silently to raw messages, but now emits a diagnostic on core's bus (`from cendor.sdk.runner import ContextBudgetFallback`): silent but observable.
- **SDK↔lib parity/identity test suite** (`tests/test_lib_parity.py`): `is`-pins every documented re-export, diffs `sdk.rules` against the library catalogue with a reviewed exclusion allowlist, pins the lib signatures the SDK forwards (a new lib kwarg fails the build instead of lagging silently), and carries the shim-expiry harness for future workarounds.

## [1.6.2] — 2026-07-13
Provider-auth hardening: the first-run "where's my key?" paper cuts now fail loud and actionable.
No change for correct code.

### Changed
- A live provider call that fails to authenticate **while the keyless placeholder is in play** now
  raises `MissingAPIKeyError` (exported from `cendor.sdk`) naming the exact env var to set
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, `HF_TOKEN`) and linking the docs,
  instead of the provider's bare 401. It never fires when a real key or a pre-built `client=` was
  supplied, on non-auth errors, or on keyless offline flows (cassette replay, pre-flight blocks).
- **Bedrock**: passing `api_key=` now raises a clear error (Bedrock authenticates via the AWS
  credential chain, not an API key); `base_url=` maps to boto3's `endpoint_url`.
- **Ollama**: `base_url=` maps to the ollama client's `host=` (it previously collided with the
  underlying httpx client and crashed); passing `api_key=` raises a clear "local, no key" error.

## [1.6.1] — 2026-07-11
AI-assistant onboarding: inline Type Teach ships inside the package, plus the bundled
integration guide. No runtime behavior change for correct code.

### Added
- Inline `@example` + correct-shape signatures on the public API (`Agent`, `run`, `SQLiteSessionStore`,
  re-exported `rules`/`judge`), so your editor's language server — and agent-mode assistants — is handed
  the right call as you type. Includes the `SQLiteSessionStore` ↔ `SqliteSessionStore` casing note.
- `INTEGRATION.md` is now bundled in the installed package — a one-screen "call Cendor correctly"
  guide. Full trap sheet: https://cendor.ai/docs/for-ai-assistants

## [1.6.0] — 2026-07-10
V04 re-exports (plan-guardrails-v04). Additive and backward-compatible. Requires
`cendor-guardrails>=1.4`.

### Added
- **`rules.intent` + `rules.custom_category`** re-exported on the SDK's `rules` surface — the pre-LLM
  intent gate (embedding / BYO-classifier backends) and the semantic category-by-example check land
  in one import for `Agent(guardrails=[…])`. Bring your own `embed`/`classify` (or
  `cendor.guardrails.embeddings.local_embedder` via the `[embeddings]` extra).
- **`presets` + `policy_schema`** re-exported at the SDK top level (`from cendor.sdk import presets,
  policy_schema`): the curated `presets.prompt_injection()` starter and the shipped policy JSON
  Schema (with `load_policy(..., validate=True)`).
- **G1 matching options ride along** — `rules.keyword_deny(..., match="word", normalize=(…))` is
  available through the re-exported rule. No runner change; these are library capabilities surfaced
  through the SDK façade. No new claim (all capability-neutral; catch-rate/accuracy gates stay shut).

## [1.5.0] — 2026-07-09
V03 Tier-A wiring (plan-guardrails-v03). Additive and backward-compatible. Requires
`cendor-guardrails>=1.3`.

### Added
- **`task_adherence` at the `tool_call` stage (A3)** — the runner now threads the run's originating
  user turn into `Context.instruction` on the tool-call gate, so a BYO-judge alignment check
  (`judge.task_adherence(respond)`, reused from `cendor-guardrails`) can compare a proposed tool call
  against the user's intent. Wire it via `rules.llm_judge(check, stage="tool_call", action="flag")`
  (advisory + fail-open by default; `action="block"` short-circuits the tool). The judge call rides
  your instrumented client, so **its own spend is budgeted + audited**. No adherence-rate claim.
- **`task_adherence` re-exported** at the SDK top level (`from cendor.sdk import task_adherence`,
  alongside `judge` / `rules`).
- **A1/A2 ride along** from `cendor-guardrails` 1.3: `rules.spotlight` is re-exported through the SDK
  `rules` surface, and the reserved annotation-parity metadata keys (`detected` / `filtered` /
  `redacted` / `severity` / …) now land on the SDK's `guardrail_decision` audit entries with no change
  to the runner or acttrace.

## [1.4.0] — Unreleased
Guardrails maturity (plan-guardrails-v02). Additive and backward-compatible. Requires
`cendor-guardrails>=1.2` and `cendor-acttrace>=1.4`. (Folds the never-released 1.3.0 — Waves 1 & 2
below — into a single minor with Wave 3; if the waves ship separately, 1.3.0 = Waves 1–2 and
1.4.0 = Wave 3.)

### Added (Wave 4 — bounded re-ask, streaming checks)
- **`Agent(reask_on_output_trip=N)`** — when an **output**-stage guardrail *blocks* the final answer,
  re-ask the model to revise it up to `N` times instead of raising (default `0` = raise immediately).
  Each re-ask is a full model call — its cost lands in tokenguard/acttrace like any other; bounded by
  the cap and `max_turns`; if every re-ask still trips, the block raises (fail-closed). Applies to
  `run` / `run.aio` and per-segment orchestration (non-streaming). The re-asked block is still
  recorded on the audit chain and `Result.guardrail_decisions`.
- **`Agent(stream_check_window=N)`** — on `run.stream`, also evaluate the output guardrails on the
  **buffered** text every `N` characters, so a block fires earlier in the stream (default `0` = final
  text only). Already-yielded deltas can't be unshown, so this narrows the window, it doesn't close it
  (redact mid-stream isn't applied) — single-agent `run.stream` only.

### Added (Wave 3 — hosted rails, config-as-data, grounding)
- **Hosted rails via `rules`** — `rules.bedrock_guardrail` / `rules.azure_content_safety` /
  `rules.model_armor` re-exported from `cendor-guardrails`, usable in `Agent(guardrails=[…])` at any
  of the four stages. The client is bring-your-own (duck-typed, metered by the vendor); every verdict
  still lands as a local `guardrail_decision` in the run's audit chain ("cloud check, local
  evidence"). Also re-exported: `rules.groundedness` / `rules.denied_topics` (bring-your-own
  embedding fn) for RAG-hallucination and off-topic gating.
- **`load_policy()` / `LoadedPolicy`** re-exported at the SDK top level — declare deterministic
  guardrails in a versioned JSON/YAML file and pass the result straight to `Agent(guardrails=…)`.
  The policy's content hash + version are stamped into every decision, so the run's audit chain
  proves which policy was active.

### Added (Wave 1 — PII bridge, execution model, decision inspection)
- **`rules.pii()` / `rules.secrets()` / `rules.entropy()`** — PII/secret guardrails bridged from
  `acttrace`'s detector catalogue (the SDK may import libraries; the tool→tool ban only constrains
  the libraries). They gate **all four stages by default — including `tool_output`**, which the
  process-global `guard()` never sees. `rules` is now the SDK's own superset module (the
  deterministic `cendor.guardrails` rules re-exported + the three bridged ones). No catch-rate claim
  — coverage is exactly acttrace's catalogue.
- **`Agent(guardrail_mode=…)`** + per-run `run(agent, input, guardrail_mode="parallel")` — overlap
  input-stage guardrails with the first model call (OpenAI-parity, async only) for lower latency on
  the pass path; default `"blocking"` stays pre-spend. Honest limit: parallel mode can bill a call a
  later trip blocks, and does not apply input redaction before the call.
- **`Result.guardrail_decisions`** — every trip/flag recorded during a run, for post-hoc inspection
  without re-reading the audit file (populated for `run` / `run.aio` / `run.stream` / teams).
- **`judge` re-exported** from `cendor.sdk` — the `cendor.guardrails.judge` helpers (verdict prompt +
  strict-JSON parsing) for building `rules.llm_judge` checks; the judge call rides an instrumented
  client, so its own spend is budgeted + audited. Per-guardrail `timeout` / `on_error` (from
  `cendor-guardrails` 1.1) flow through `rules.custom` / `rules.llm_judge`.

### Added (Wave 2 — detection-tier adapters via rules)
- The SDK's `rules` superset now also re-exports the opt-in detection-tier adapters from
  `cendor-guardrails` 1.1: `rules.classifier` (BYO local model), `rules.prompt_guard` (prompt-injection
  classifier adapter, `[promptguard]` extra — no jailbreak-detection claim), `rules.language`
  (language-switch guard), and `rules.openai_moderation` (OpenAI's free moderation endpoint). See the
  guardrails "Threat model" for what each tier does and doesn't stop.

## [1.2.0] — 2026-07-09

### Added
- **`Agent(guardrails=[…])` — a deterministic gate wired at all four stages of the loop.** Attach
  `cendor.guardrails` rules (re-exported: `Guardrail`, `guardrail`, `GuardrailTripped`, `rules`) to
  gate `input` (pre-spend), `tool_call`, `tool_output`, and `output`. A `block` at the input/output
  stage **raises `GuardrailTripped`** (fail-closed — an input block refuses before the model is ever
  called, `$0` spent); a `block` at the tool stages returns a `"[blocked by <name>] <reason>"` tool
  result so the loop continues without the side effect (mirroring `require_approval`'s `"[denied]"`).
  `redact` rewrites the outgoing messages / tool result / output; `flag` records and proceeds. Every
  decision emits on the bus, so an attached `AuditLog` chains it as a `guardrail_decision` entry —
  correlated with the run's decision, no extra wiring. Works on the sync, async, streaming, and
  multi-agent paths; each agent in a team gates with its own list.
- **Per-run override — `run(agent, input, guardrails=[…])`** (and `run.aio`) replaces the agent's
  list for that run; `guardrails=[]` disables gating for the run. For a team, the override applies
  to every segment. This is per-agent/per-run scoped — unlike the process-global `guard()`, which
  stays the acttrace-policy context manager (the two are distinct).
- Depends on `cendor-guardrails>=1.0,<2` (joins the bundled stack).

## [1.1.1] — 2026-07-08

### Fixed
- **`Agent(max_usd=…)` is now enforced on a single-agent `run()`**, not only in orchestrated/
  multi-agent runs. The per-agent USD cap was previously applied only inside the orchestrator's
  per-segment scope, so a plain `run(agent, …)` billed with no ceiling. The single-agent run and
  stream paths (sync + async) now wrap the same pre-flight `budget(on_exceed="block")` scope the
  orchestrator uses, so an over-budget call is refused before it is sent — identical semantics to a
  multi-agent segment.
- **Resuming an already-completed checkpoint no longer re-runs the model or its tools.** Resuming a
  run whose checkpoint is marked `done` now short-circuits and returns the stored result
  (`steps == []`, zero model calls, zero tool calls) instead of rebuilding from the original input and
  replaying the whole loop. Applies to single- and multi-agent runs, sync and async.

## [1.1.0] — 2026-07-05

### Added
- **Live progress hook** — `run(agent, input, on_step=cb)` (and `run.aio`) calls `cb(step)` with each
  `Step` **live** as the run advances, complementing the post-hoc `Result.steps`. Works for single-
  and multi-agent runs (each `Step` carries its agent's name); a raised callback never breaks a run.
- **Opt-in prompt caching** — `Agent(cache=True)` enables explicit provider prompt caching. For
  Anthropic it puts a `cache_control` breakpoint on the system prompt + tool schema (the stable
  prefix); cache-read/write tokens flow through `cendor-core`'s usage extraction + pricing, so
  `Result.usage`/`cost` reflect the savings. A no-op for providers that cache automatically (OpenAI).
- **Multi-agent streaming** — `run.stream([...])` / `run.astream([...])` now stream a handoff run
  (previously raised `TypeError`): events from each active agent, switching on a `transfer_to_<peer>`
  call, with one terminal `RunComplete` carrying the aggregate `Result`.
- **Live OTel spans** — `cendor.sdk.otel.live_spans()` context manager emits `gen_ai` spans as a run
  progresses (the streaming counterpart to the post-hoc `span_tree()`): a root `agent.run` span
  brackets the block and a child `chat`/`execute_tool` span is emitted as each call completes (start
  backdated by `latency_ms` for accurate duration). No-op without OpenTelemetry.
- `__version__` is now derived from installed package metadata (single source of truth: `pyproject`).

## [1.0.1] — 2026-07-05

### Changed
- **Packaging & docs only — no code changes.** Split the README into a repo landing (`README.md`)
  and a clean PyPI project description (`README-pypi.md`, now the `[project.readme]`), so the PyPI
  page is a self-contained product description with working links. Removed references to the
  internal `plan/` design docs from the shipped README / docs / CHANGELOG, and untracked `plan/`
  (kept local, gitignored) so it never ships. The `cendor.sdk` package code is identical to 1.0.0.

## [1.0.0] — 2026-07-05

First public release on PyPI — the governed agent loop, multi-agent orchestration, ecosystem
interop, and production hardening + governed eval, consolidated with the gap-analysis remediation,
retrieval (RAG), and rolling memory. Requires `cendor-core>=1.3` (Hugging Face detection).

### Fixed
- **Tool calling on Gemini & Bedrock** (P0) — `build_kwargs` dropped assistant `tool_calls` and all
  `tool` results, so multi-turn tool loops silently broke on those providers. Added
  `_canonical_to_gemini` / `_canonical_to_bedrock` (functionCall/functionResponse; toolUse/toolResult),
  mirroring the Anthropic translator, with round-trip tests.

### Added — gap-analysis remediation
- **Provider param passthrough** — `Agent.extra` merges arbitrary request kwargs (`tool_choice`,
  `reasoning_effort`, `top_p`, `stop`, `seed`, `response_format`, `extra_body`, …) into every call.
- **o-series temperature guard** — the OpenAI provider omits `temperature` for `o1`/`o3`/`o4` models
  (which reject it) instead of erroring.
- **Robust structured output** — an `output_type` now derives a JSON Schema used via each provider's
  native structured-output feature: OpenAI `json_schema`, Ollama `format`, Gemini `response_schema`;
  Anthropic/Bedrock/Responses embed the schema in the JSON nudge. Dataclass / Pydantic / dict.
- **Streaming** — `run.stream(agent, input)` / `run.astream(...)` yield `TextDelta` / `ToolCallEvent`
  / `ToolResultEvent` and a terminal `RunComplete(result)`. Native reassembly for the OpenAI family +
  Ollama (tool-call deltas included); whole-response fallback for the rest. Single-agent.
- **Multimodal input** — message `content` may be a parts list (`text` + `image_url`); OpenAI-family
  passes it through, Anthropic/Gemini translate to image blocks (base64/url), Bedrock keeps the text.
- **Unpriced-model cost governance** — `register_model_price(model, input=…, output=…)` registers a
  rate so cost/USD budgets bind on HF/Azure-deployment/Foundry-Local/custom ids; `configure`
  (`on_unpriced="raise"`) re-exported.
- **Embeddings** — `embed(model, inputs)` / `aembed(...)` return vectors and emit a governed
  `LLMCall` (tokens/cost/audit captured) for OpenAI-family providers — RAG calls become first-class.
- **RAG seam** — `VectorIndex` (a dependency-free in-memory cosine index over `embed()`) +
  `Agent(retriever=…)`, which injects retrieved context as a system message before the call
  ("always-on" RAG). Bring your own embedder via `embedder=`, or plug a real vector DB as a
  retriever. The SDK governs retrieval; it does not store your vectors.
- **Summarizing memory** — `SummarizingSession(model=… | summarizer=…, max_messages=…,
  keep_recent=…)` folds old turns into a durable summary note when a conversation grows, keeping
  recent turns verbatim so memory stays bounded but the gist persists (beyond `context_budget`
  trimming). `llm_summarizer(model)` builds a governed summarizer; pass any callable for offline
  summaries.
- **Multi-agent checkpointing** — `run([...], checkpoint=…)` persists the trajectory per turn/segment
  and resumes a crashed team run.
- **MCP prompts** — `load_mcp_prompts(session)` + `get_mcp_prompt(session, name, args)` (renders to
  canonical messages); `load_mcp_resources` now exported too.
- **Concurrent tool execution** — a turn's multiple tool calls run via `asyncio.gather` in async runs.
- **`max_turns` signal** — `Result.incomplete` is `True` when a run ends with no final answer.
- **Nested tool params** — tool schema generation expands nested dataclass / Pydantic parameters.
- **Richer OTel spans** — `span_tree` now records latency, finish reason, reasoning tokens, and tool
  argument names.
- **Eval judge** — `EvalCase.judge` adds a pluggable semantic / LLM-judge scorer.

### Added
- **Hugging Face provider** (`provider="huggingface"` / `"hf"`, extra `[huggingface]`) — wraps
  `huggingface_hub.InferenceClient.chat_completion` (OpenAI-shaped response, so request formatting
  and normalization are reused). Token via `api_key=` or `HF_TOKEN`/`HUGGINGFACEHUB_API_TOKEN`;
  `base_url=` targets a dedicated Inference Endpoint; `HF_PROVIDER` routes through a specific
  inference provider. `cendor-core`'s `instrument` now detects `chat_completion` structurally and
  attributes the `LLMCall` to `huggingface`, so budgets/guard/audit apply.
- **Azure AI Foundry provider** (`provider="azure"` / `"azure_openai"` / `"foundry"`, extra
  `[azure]`) — connects to Foundry deployments via the **standard `openai` SDK** on the
  `/openai/v1/` endpoint, per Microsoft's current guidance (the `AzureOpenAI` client and
  `azure-ai-inference` are being retired). `model` is the Foundry **deployment name**; `base_url`
  (or `AZURE_OPENAI_ENDPOINT`) is normalized to the v1 route; `api_key` falls back to
  `AZURE_OPENAI_API_KEY`/`AZURE_INFERENCE_CREDENTIAL`. Entra ID via a bearer-token `api_key` or a
  bring-your-own `client=`. See `docs/sdk.md` → *Connecting to Hugging Face & Azure AI Foundry*.
- **Azure Foundry Responses API** (`provider="azure_responses"` / `"foundry_responses"`) — drives
  the OpenAI Responses API over a Foundry endpoint for OpenAI-family deployments; same Foundry-aware
  construction as `provider="azure"` (Chat Completions).
- **Foundry Local provider** (`provider="foundry_local"` / `"foundry-local"`, extra
  `[foundry-local]`) — Microsoft's on-device runtime over its local OpenAI-compatible REST server
  (the local counterpart to Ollama). Endpoint via `base_url=` or `FOUNDRY_LOCAL_ENDPOINT` (e.g.
  `foundry_local.FoundryLocalManager(alias).endpoint`); no key required. `model` is the resolved
  Foundry Local model id.
- **Examples**: `examples/foundry_agent.py` (Azure Foundry + Foundry Local) and
  `examples/huggingface_agent.py`, both offline.

### Consolidation & docs (Phases 1–4)

Consolidates Phases 1–4 into a stable, documented release (plan §12): the governed agent loop,
multi-agent orchestration, ecosystem interop, and production hardening + governed eval — complete,
provider-agnostic, local-first, and tested offline.

- Full docs pass: `docs/index.md` links every page and lists the public API surface; the two-door
  framing (libraries primary, SDK secondary) is consistent across the README and docs.
- Marked `Development Status :: 5 - Production/Stable`.
- Verification: `uv run pytest` green (91 tests, no network), `ruff check`/`ruff format` clean,
  mypy clean, namespace-guard clean, `import cendor.sdk` works.

## [0.4.0] — Phase 4: Production hardening & governed eval

The "safe for real workloads" layer plus the testing wedge fully realized.

### Added
- **Retries & backoff** — `RetryPolicy` retries transient model-call failures (timeouts,
  connection errors, rate limits, 5xx) with exponential backoff; governance decisions
  (`BudgetExceeded`/`PolicyViolation`) are never retried. `run(..., retry=RetryPolicy(...))`.
- **Checkpointed / resumable runs** — `run(..., checkpoint="run.ckpt.json")` persists the
  conversation after each turn; re-running with the same checkpoint resumes without re-executing
  completed tools. Backed by `Checkpointer` (atomic local JSON).
- **Durable memory** — `SQLiteSessionStore` persists many named conversations locally (no server);
  `Session.save`/`Session.load` for JSON.
- **Governed eval / regression harness** — `evaluate(agent, [EvalCase(...)])` replays recorded
  cassettes as tests and asserts output, tool sequence, and cost/token ceilings; cost/tokens are
  real on replay. `EvalReport.assert_ok()` fails CI on a behaviour *or* spend regression.
- **Docs/examples**: `docs/hardening.md`, `docs/eval.md`; `examples/eval_suite.py`.
- **Tests** (+9): transient failure recovers, retry gives up / doesn't retry non-transient, a
  checkpointed run resumes after a simulated crash, SQLite durable memory round-trips, and an eval
  suite catches a cost regression + a tool-sequence change.

## [0.3.0] — Phase 3: Ecosystem & interop

Governed `cendor-sdk` agents become first-class citizens elsewhere — all optional and local-first.

### Added
- **MCP client** (`[mcp]` extra) — `load_mcp_tools(session)` turns an MCP server's tools into
  governed `Tool`s (duck-typed against `mcp.ClientSession`; async, use with `run.aio`).
  `load_mcp_resources(session)` reads resources.
- **A2A** — `A2AServer` serves an agent over the Agent-to-Agent protocol (JSON-RPC `message/send` +
  agent card); `A2AClient` calls it **in-process**; `a2a.serve(...)` is an optional stdlib HTTP
  server. Replies carry governance metadata (trace id, cost).
- **Foundry / Copilot** — `FoundryAdapter` publishes an agent as a Microsoft 365 / Foundry
  custom-engine agent over the Bot Framework Activity protocol (`on_activity`, `manifest`).
- **OpenTelemetry span tree** (`[otel]` extra) — `span_tree(result)` emits a `gen_ai.*` span tree
  (root `agent.run` → per-agent → per model call / tool) mirroring the correlated `Result`; a
  no-op when OTel isn't installed.
- **Human-in-the-loop** — `require_approval(tool, approver=…)` gates a tool behind approval and
  records the verdict via `decision.human_oversight(...)` on the run's audit chain; rejection blocks
  the tool. The runner now exposes the active audit `decision` via a contextvar for this wiring.
- **Docs/examples**: `docs/interop.md`; `examples/mcp_agent.py`.
- **Tests** (+7): MCP round-trip (mocked session), A2A serve+call (in-proc), Foundry adapter, OTel
  span-tree assertions (in-memory exporter), HITL approval + rejection recorded in a verified chain.

## [0.2.0] — Phase 2: Multi-agent orchestration

Orchestration patterns land, with the correlation that was impossible beneath frameworks: a whole
multi-agent trajectory is one governed, correlated tree on one verifiable audit chain.

### Added
- **Handoff** — an agent transfers control to a named peer via a synthetic `transfer_to_<peer>`
  tool; the canonical conversation carries across the switch, so **handoff works across providers**.
- **Supervisor / router** — `supervisor(coordinator, [sub_agents], ...)`; a coordinator routes to
  sub-agents by handoff. `run([entry, *peers], input)` is the handoff-team shortcut.
- **Sequential & parallel** — `sequential([...])` pipes each agent's output into the next;
  `parallel([...])` / `parallel_async([...])` fan out over the same input (`{agent: output}`).
- **Nested trace correlation** — one parent `run_id`; each agent segment runs under a child trace
  id (`{run_id}:{agent}#i`), so every `Step` carries its agent name and a `trace_id` that starts
  with the parent — one correlated tree.
- **Per-agent governance** — each segment is wrapped in `track(agent=…)` (spend attribution) and,
  when the agent sets `max_usd`, a per-agent `budget(...)`; each segment opens its own audit
  `decision()` on a shared `AuditLog` — one verifiable chain, distinct agents distinguishable.
- **Session persistence** — `Session.save(path)` / `Session.load(path)` (local JSON), resumable.
- **Refactor** — the per-agent loop is extracted (`run_agent_sync`/`run_agent_async`) so the
  single-agent `Runner` and the orchestrator share exactly one loop.
- **Docs/examples**: `docs/multi-agent.md`; `examples/handoff.py`, `examples/supervisor.py`.
- **Tests**: supervisor + 2 sub-agents with a correlated audit trail; per-agent budgets enforced;
  handoff across OpenAI↔Anthropic; sequential/parallel pipelines; session persistence.

## [0.1.0] — Phase 1: Governed single agent

The wedge lands: a governed single agent runs on OpenAI **and** Anthropic, with budgets, audit,
redaction, and deterministic replay — provider-agnostic, local-first, no network in tests.

### Added
- **`Agent`** — a small, opinionated agent bound to any core-supported provider id, with tools,
  instructions, structured output, and a bounded ReAct loop.
- **`tool` / `Tool`** — decorator that generates a JSON Schema from a function's type hints +
  docstring, and formats it per provider (OpenAI functions, Anthropic tools, Gemini function
  declarations, Bedrock toolConfig). Sync **and** async tools; every call emits a `ToolCall`.
- **`run` / `Runner`** — the single-agent loop, **sync (`run`) and async (`run.aio`)**: assemble →
  format → call (inside `trace(run_id)`) → normalize → tools → repeat → finalize.
- **Provider response normalization** (`providers.py`) — extract assistant content + tool calls +
  finish reason for OpenAI (Chat Completions + Responses), Anthropic, Gemini, Bedrock, and Ollama.
- **Structured output** — `output_type` as a dataclass or JSON-schema dict, parsed from the final
  message.
- **In-memory `Session`** — conversation memory across `run()` calls.
- **`Result` / `Run` / `Step`** — the data model; steps are the actual bus `LLMCall`/`ToolCall`
  records, correlated by one `trace_id`, with aggregate `usage` and Decimal `cost`.
- **Governance re-exports** — `budget`, `track`, `report` (tokenguard); `AuditLog`, `Policy`,
  `guard` (acttrace, with `guard` wrapped as a context manager); all composing through core's seams
  with zero SDK glue. An ungoverned `run()` still works on `cendor-core` alone.
- **Docs**: `README.md` (two doors + killer metric), `docs/index.md`, `docs/sdk.md`;
  `examples/single_agent.py`.
- **Tests**: governed single-agent runs across OpenAI and Anthropic (respx-mocked, no network);
  usage/cost/reasoning captured, budget blocks, guard redacts, audit chain `verify()`s, `trace_id`
  correlates, and cassette replay is deterministic.
