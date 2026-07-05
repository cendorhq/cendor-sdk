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
| [sdk.md](sdk.md) | Quickstart, the agent loop, tools, structured output, sessions, governance, testing. |
| [multi-agent.md](multi-agent.md) | Handoff, supervisor/router, sequential & parallel pipelines. |
| [interop.md](interop.md) | MCP tools, A2A, Foundry/Copilot, OTel span tree, human-in-the-loop. |
| [hardening.md](hardening.md) | Retries, timeouts, checkpointed/resumable runs, durable memory. |
| [eval.md](eval.md) | Cassette-backed governed eval & regression harness. |

## Design principles (do not break)

1. **Cooperate through core.** The SDK hard-depends on `cendor-core` only; the governance tools
   integrate through core's bus / interceptor / `Sink` / `Compressor` seams.
2. **Governed by default, escapable.** Every governance layer is optional — an ungoverned `run()`
   works with just `cendor-core`.
3. **Namespace safety.** This distribution ships `cendor.sdk` **only** — never `src/cendor/__init__.py`.
4. **Local-first, no servers.** Cloud / OTel export is always optional and opt-in.

See [the full plan](../plan/CENDOR_SDK_PLAN.md) for the complete design and roadmap.
