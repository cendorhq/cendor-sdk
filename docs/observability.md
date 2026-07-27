# Observability — export your agent runs (or watch them in Cendor Monitor)

A governed `run` is the richest thing Cendor emits: a full **`gen_ai` span tree** — a root
`agent.run`, a child per agent segment, a grandchild per model call and tool execution, with tokens,
cost, latency, and (opt-in) content. Because it's **standard OpenTelemetry** — one wire, no
Cendor-specific exporter, never a Cendor endpoint — where it goes is your choice: **your own backend**
(Azure Monitor, CloudWatch, Datadog, Langfuse, any OTLP) for production — or **Cendor Monitor**, a
free, self-hosted **journey view**, when you want to *see* a run in one screen. Same wire either
way; your own backend stays the documented production default.

> The full backend catalogue, the emitter reference, and content-capture details live on the
> libraries' [Observability guide](/docs/observability). This page is the SDK's short path to it.

## Quickstart

Configure an OpenTelemetry pipeline once (in your app), then wrap a run — `live_spans()` streams the
trajectory as it happens; `span_tree(result)` emits it after the fact.

<!-- tabs: lang -->
<!-- tab: Python -->

```python
# export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector:4317   (or http://localhost:4318 for Cendor Monitor)
from cendor.sdk import run
from cendor.sdk.otel import live_spans

with live_spans():                       # the whole agent.run span tree -> your backend
    result = run(agent, "Refund order 42")
```

<!-- tab: TypeScript -->

```ts
import { run, spanTree, liveSpans } from '@cendor/sdk';

const live = liveSpans();                // stream the run's spans -> your backend
try {
  const result = await run(agent, 'Refund order 42');
  spanTree(result);                      // or emit the whole tree post-hoc
} finally {
  live.close();
}
```

<!-- /tabs -->

## Sessions group a conversation

Pass `run(session=…)` (a `Session`, an `SqliteSessionStore` key, or any conversation id) and the SDK
auto-stamps `gen_ai.conversation.id` on the run root — so a backend groups every run of one multi-turn
conversation. That grouping is exactly what Cendor Monitor drills as **Agents → Sessions → Runs**.
Add a human-authored `label=` (never derived from the prompt) to stamp `cendor.run.label` so a
monitor can show *what a run was for*.

Streamed calls carry `cendor.ttft_ms` (time-to-first-token) on the `chat` span, and — when the
provider reported no usage on the stream so the tokens were recovered by an offline estimate —
`cendor.usage_estimated="true"`, so a monitor shows those counts as *est.* rather than exact (truth =
the product). The run root carries `cendor.run.agents` so a monitor's Agents view fills for
live-streamed runs too.

## Governance links back to the run

Inside `live_spans()` / `liveSpans` (`@cendor/sdk` ≥ 1.12 / 0.17), the run's root span is the active
context span for the whole run, so every governance event your run produces — a budget block, a
guardrail verdict, a tool call, an audit `decision` — correlates to that run's trace (`@cendor/acttrace`
stamps `cendor.audit.otel_trace_id`, and the `audit.*` mirror spans nest in the run's trace). A run
built post-hoc from `span_tree` instead still links by `cendor.audit.run_id` (Cendor's ambient run
id). So a trace-aware tool such as Cendor Monitor can open a run and show every verdict on the exact
step it governed — see [Correlate audit entries with your traces](/docs/observability#correlate-audit-entries-with-your-traces).

## Structural signals — RAG, memory, orchestration, tools, MCP

Beyond the `chat`/`execute_tool` tree, the SDK emits a `cendor.sdk` span for each of its **structural**
signals, so a backend renders them as first-class domains (Cendor Monitor gives each its own view).
These are automatic — they ride the same `live_spans()` / `liveSpans` scope, opt-in and content-free
(ids, labels, and counts only; never message bodies). Available in `cendor-sdk` ≥ 1.16 / `@cendor/sdk`
≥ 0.21.

| Span | Emitted when | Carries |
|---|---|---|
| `rag.assemble` | `context_budget` assembly runs | budget/used, blocks kept vs dropped, token deltas |
| `rag.compress` | squeeze compresses a block | technique, tokens before/after, ratio |
| `memory.load` / `memory.save` | a run reads / writes a `Session` | session id, turn count, byte size |
| `orchestration.handoff` | one agent hands off to another | from-agent, to-agent, segment, transfer tool |
| `checkpoint.save` / `checkpoint.resume` | a `Checkpointer` writes / a run resumes | run id, done flag, turn count |
| `execute_tool …` (now) | any tool call | `cendor.tool.source` (`local`\|`mcp`), `cendor.tool.outcome` (`ok`\|`error`\|`blocked`) |

A tool the `tool_call` guardrail **blocks** never runs, so it emits no ordinary tool span — the SDK
emits a dedicated `execute_tool {name}` span with `cendor.tool.outcome="blocked"` and the guardrail
name in `cendor.tool.blocked_by` (never the reason text), so a blocked call is still visible.

**MCP server attribution.** Pass the (non-secret) server name + transport to `load_mcp_tools` and every
tool from that server is tagged `cendor.tool.source="mcp"` with the server on its spans, plus
`mcp.connect` / `mcp.list_tools` lifecycle spans so a monitor can attribute calls per server:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent
from cendor.sdk.mcp import load_mcp_tools

# `session` is your connected MCP client session. server/transport are non-secret labels.
tools = await load_mcp_tools(session, server="github", transport="stdio")
agent = Agent(name="assistant", model="gpt-4o", tools=tools)
```

<!-- tab: TypeScript -->

```ts
import { Agent, loadMcpTools } from '@cendor/sdk';

// `session` is your connected MCP client (e.g. @modelcontextprotocol/sdk's Client — duck-typed here).
declare const session: {
  listTools(): Promise<unknown>;
  callTool(name: string, args: Record<string, unknown>): Promise<unknown>;
};

// server/transport are non-secret attribution labels — tools' spans get cendor.tool.source="mcp".
const tools = await loadMcpTools(session, { server: 'github', transport: 'stdio' });
const agent = new Agent({ name: 'assistant', model: 'gpt-4o', tools });
```

<!-- /tabs -->

## Watch it in Cendor Monitor

[**Cendor Monitor**](/docs/monitor) is the optional, self-hosted monitor that renders these SDK runs
as a **run journey**: the whole conversation with tokens, cost, latency, and TTFT per step, and the
exact step where a budget block, guardrail verdict, or compression fired — inline. One `docker run`,
on your own infrastructure:

```bash
docker run --rm -p 3000:3000 -p 4317:4317 -p 4318:4318 ghcr.io/cendorhq/cendor-monitor:0.14.2
# then, in your app:  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   → open http://localhost:3000
```

It's optional dev tooling (like [`cendor-mcp`](/docs/assistant-mcp)); no part of the SDK depends on
it, and your own OTel backend stays the production default. Full page: [Cendor Monitor](/docs/monitor)
· [cendor.ai/monitor](/monitor).

## Honest limits

- **Content is opt-in, off by default.** Prompts/responses/thinking/tool values ride the wire only
  after you call `otel.capture_content()` (see the [libraries guide](/docs/observability#content-capture--opt-in-off-by-default)).
  A monitor never enables it for you.
- **The monitor is an operational copy — not the evidence.** `verify()` runs on the tamper-evident
  audit **file** on your app host, never on telemetry or on what a monitor shows.
- **Cendor never operates a telemetry endpoint.** You configure the pipeline; Cendor exports into the
  global OTel provider you own. Local-first, no server required to run the SDK itself.
