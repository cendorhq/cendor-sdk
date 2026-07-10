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

The SDK's primitives *are* the library objects, re-exported — so the canonical trap table and
CI-typechecked examples live on one page for both doors:
**[For AI assistants](/docs/for-ai-assistants)**. Don't duplicate it here; point your assistant at
that URL (or the [full docs bundle](https://cendor.ai/llms-full.txt), which includes every SDK page).

For the SDK-specific surfaces, the docs themselves are the reference:
[Agents & the loop](agents.md), [Governance](governance.md), [Guardrails](guardrails.md), and the
[FAQ](faq.md).

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
