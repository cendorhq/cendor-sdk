# Changelog

All notable changes to `cendor-sdk` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
