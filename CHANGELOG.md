# Changelog

All notable changes to `cendor-sdk` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
