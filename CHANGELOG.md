# Changelog

All notable changes to `cendor-sdk` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-07-05

First public release on PyPI — the governed agent loop, multi-agent orchestration, ecosystem
interop, and production hardening + governed eval, consolidated with the gap-analysis remediation,
retrieval (RAG), and rolling memory. Requires `cendor-core>=1.3` (Hugging Face detection).

### Fixed
- **Tool calling on Gemini & Bedrock** (P0) — `build_kwargs` dropped assistant `tool_calls` and all
  `tool` results, so multi-turn tool loops silently broke on those providers. Added
  `_canonical_to_gemini` / `_canonical_to_bedrock` (functionCall/functionResponse; toolUse/toolResult),
  mirroring the Anthropic translator, with round-trip tests.

### Added — gap-analysis remediation (see `plan/SDK_FIT_GAP_ANALYSIS.md`)
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
