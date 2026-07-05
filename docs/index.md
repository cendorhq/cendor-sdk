# cendor-sdk documentation

**A governed, provider-agnostic agent SDK — the second door into Cendor.**

Cendor is *production plumbing for LLM applications*. There are two front doors:

- **The libraries** (primary) — *"production plumbing beneath your framework."* Already using
  LangChain / LlamaIndex / a provider SDK? Compose the libraries underneath it: `pip install cendor`.
- **`cendor-sdk`** (this project) — *"a governed agent in 10 lines."* Starting fresh or want it
  simple? Don't pick a framework or wire the libraries — just use the SDK: `pip install cendor-sdk`.

Both doors expose the **same primitives** (`budget`, `guard`, `Policy`, `AuditLog`, `trace`), so
moving between them is continuous, never a migration.

## Pages

| Page | What it covers |
|---|---|
| [sdk.md](sdk.md) | Quickstart, the agent loop, tools, structured output, sessions, governance, testing — plus providers, streaming, RAG, memory, and embeddings. |
| [multi-agent.md](multi-agent.md) | Handoff, supervisor/router, sequential & parallel pipelines. |
| [interop.md](interop.md) | MCP tools, A2A, Foundry/Copilot, OTel span tree, human-in-the-loop. |
| [hardening.md](hardening.md) | Retries, timeouts, checkpointed/resumable runs, durable memory. |
| [eval.md](eval.md) | Cassette-backed governed eval & regression harness. |

## Public API surface

Everything below is importable from `cendor.sdk`.

| Group | Names |
|---|---|
| Agent & loop | `Agent`, `tool`, `Tool`, `run` (`.aio` / `.stream` / `.astream`), `Runner` |
| Result model | `Result`, `Run`, `Step`, `ParsedResponse`, `ToolInvocation` |
| Streaming events | `StreamEvent`, `TextDelta`, `ToolCallEvent`, `ToolResultEvent`, `RunComplete` |
| Orchestration (P2) | `handoff`, `Handoff`, `sequential`, `parallel`, `parallel_async`, `supervisor` |
| Memory | `Session`, `SummarizingSession`, `llm_summarizer`, `SQLiteSessionStore` |
| Retrieval & embeddings | `embed`, `aembed`, `VectorIndex`, `Hit` |
| Governance (re-exported) | `budget`, `track`, `report`, `configure`, `register_model_price`, `BudgetExceeded`, `guard`, `Policy`, `AuditLog`, `verify` |
| Correlation | `trace`, `current_trace_id` |
| Interop (P3) | `load_mcp_tools`, `load_mcp_prompts`, `get_mcp_prompt`, `load_mcp_resources`, `A2AServer`, `A2AClient`, `FoundryAdapter`, `span_tree`, `require_approval` |
| Hardening & eval (P4) | `RetryPolicy`, `Checkpointer`, `evaluate`, `EvalCase`, `EvalReport`, `EvalResult` |

The governance objects (`budget`/`track`/`report` from tokenguard; `Policy`/`AuditLog`/`verify`
from acttrace) are the **real** library objects, re-exported — so a team can start on the SDK and
later drop to the libraries with no concept rewrite. `guard` is the SDK's context-manager wrapper
over `acttrace.guard`. Extras: `[openai]`, `[anthropic]`, `[google]`, `[bedrock]`, `[ollama]`,
`[huggingface]`, `[azure]`, `[foundry-local]`, `[mcp]`, `[otel]`, and `[all]`.

## Design principles (do not break)

1. **Cooperate through core.** The SDK hard-depends on `cendor-core` only; the governance tools
   integrate through core's bus / interceptor / `Sink` / `Compressor` seams.
2. **Governed by default, escapable.** Every governance layer is optional — an ungoverned `run()`
   works with just `cendor-core`.
3. **Namespace safety.** This distribution ships `cendor.sdk` **only** — never `src/cendor/__init__.py`.
4. **Local-first, no servers.** Cloud / OTel export is always optional and opt-in.

See the [CHANGELOG](../CHANGELOG.md) for the release history.
