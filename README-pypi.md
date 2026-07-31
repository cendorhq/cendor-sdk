# cendor-sdk

**A governed agent in 10 lines — cost budgets, tamper-evident audit, and PII redaction built in.**

![PyPI](https://img.shields.io/pypi/v/cendor-sdk) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-Apache_2.0-blue) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) ![types: mypy](https://img.shields.io/badge/types-mypy-blue)

*provider-agnostic · local-first · offline by default · sync **and** async*

> A thin, provider-agnostic agent SDK where **cost budgets, tamper-evident audit, PII redaction,
> context governance, and record/replay testing are the *foundation*, not a plugin.**

`cendor-sdk` **owns the agent loop**, so every governance concern that is best-effort *beneath* a
framework becomes first-class here: usage is never lost, budgets enforce *before* the model call,
PII is redacted *before* send, and the whole run correlates under one `trace_id`. It's the simple,
batteries-included door into the [Cendor](https://github.com/cendorhq/cendor-libs) stack — you don't need
to pick a framework or wire the libraries. (Already have a framework? Compose the libraries beneath
it: `pip install cendor-libs`.)

## Install

```bash
pip install "cendor-sdk[openai,anthropic]"     # provider SDKs are optional extras
pip install "cendor-sdk[all]"                  # every provider + interop, batteries included
# Using uv? Same names, same extras: `uv add` instead of `pip install`.
```

The install bundles the whole Cendor stack — all seven libraries (`cendor-core`, `tokenguard`,
`guardrails`, `acttrace`, `contextkit`, `squeeze`, `cassette`) — by dependency, so you install once
and import only from `cendor.sdk`. Provider SDKs stay optional extras: `[openai]`, `[anthropic]`,
`[google]`, `[bedrock]`, `[ollama]`, `[huggingface]`, `[azure]`, `[foundry-local]`, plus `[mcp]` and
`[otel]`.

Using an AI coding assistant? `uvx cendor-init` (Python) / `npx @cendor/init` (TS) wires it up — or
point it at [cendor.ai/docs/for-ai-assistants](https://cendor.ai/docs/for-ai-assistants).

## A governed agent in 10 lines

**Auth:** `OPENAI_API_KEY` from your environment (or `Agent(api_key=…)`, or a pre-built `client=`).
The SDK builds the provider client for you — there's no Cendor-specific key. Full table:
[docs/providers](https://cendor.ai/docs/sdk/providers#api-keys--credentials).

```python
from cendor.sdk import Agent, tool, run, budget, guard, Policy, AuditLog

@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"

agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather],
              instructions="Answer using tools when helpful.")

log = AuditLog(system="support", risk_tier="limited", path="audit.jsonl")
with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
    result = run(agent, "What's the weather in Paris?", audit=log)

print(result.output)                        # -> "It's sunny in Paris."
print(result.cost, result.usage)            # priced in Decimal, budgeted
print([s.name for s in result.tool_steps])  # -> ["get_weather"]
# audit.jsonl: audit_open -> decision -> llm_call -> tool_call -> llm_call, hash-chained &
# verify()-able, all correlated by one trace_id. Wrap in cassette.using("run.json") to replay it.
```

**Ungoverned still works — on `cendor-core` alone.** Every governance layer is optional and
removable; drop the `with` block and `run(agent, ...)` runs bare:

```python
from cendor.sdk import Agent, run
result = run(Agent(name="a", model="gpt-4o", instructions="Be brief."), "Hi")
result = await run.aio(agent, "Hi")   # same call, async
```

> `run.aio` is natively async for OpenAI (Chat + Responses — and the Microsoft Foundry (formerly
> Azure AI Foundry) / Foundry Local paths that use the same client), Anthropic, Google Gemini
> (google-genai's `aio.models.generate_content`), Ollama, and Hugging Face. Bedrock's boto3
> `converse` is blocking, so `run.aio` offloads it to a worker thread (`asyncio.to_thread`) — the
> event loop keeps running, and the run's governance scope still attaches.

## Why it's different

| | Provider lock | Cost budgets | Tamper-evident audit | PII redaction | Record/replay tests | Local-first |
|---|---|---|---|---|---|---|
| OpenAI Agents SDK | OpenAI-centric | ✗ | ✗ | ✗ | ✗ | lib |
| LangGraph | agnostic | DIY | DIY | DIY | DIY | lib |
| Anthropic Agent SDK | Anthropic-centric | ✗ | ✗ | ✗ | ✗ | lib |
| CrewAI / Pydantic AI / ADK | varies | ✗/DIY | ✗ | ✗ | ✗ | lib |
| **cendor-sdk** | **agnostic** | **built-in** | **built-in** | **built-in** | **built-in** | **yes** |

Governance is composed through Cendor's existing **bus / interceptor / `Sink` / `Compressor`**
seams, correlated by `trace()` — **zero SDK-specific glue**. Budgets, audit, redaction, and
record/replay all ride the agent loop through those seams, so removing any one is just not entering
its context.

## Multi-agent, one correlated tree

Handoff, supervisor/router, and sequential/parallel pipelines — with the correlation that was
*impossible beneath frameworks*. A whole multi-agent run is one governed, `trace_id`-correlated
tree, on one verifiable audit chain. Handoff even works **across providers**:

```python
from cendor.sdk import Agent, run

writer  = Agent(name="writer",  model="claude-opus-4-8", instructions="Write the brief.")
planner = Agent(name="planner", model="gpt-4o", instructions="Plan, then hand off.",
                handoffs=["writer"])

result = run([planner, writer], "Research X and write a brief")   # OpenAI -> Anthropic handoff
print(result.agents)     # ["planner", "writer"]
```

## Every major provider — one canonical loop

The provider is inferred from the model id (override with `provider=`). History is held in one
canonical shape, so a run can **hand off between providers** without rewriting it.

| Provider | Models | Extra |
|---|---|---|
| **OpenAI** | Chat Completions + Responses API | `[openai]` |
| **Anthropic** | Messages API | `[anthropic]` |
| **Google Gemini** | `google-genai` | `[google]` |
| **AWS Bedrock** | Converse API | `[bedrock]` |
| **Ollama** | local models | `[ollama]` |
| **Hugging Face** | Inference / endpoints | `[huggingface]` |
| **Microsoft Foundry** | deployments via the OpenAI v1 endpoint (Chat + Responses) | `[azure]` |
| **Foundry Local** | on-device, OpenAI-compatible | `[foundry-local]` |

## More in the box

Everything a real agent needs — all governed through the same seams:

- **Streaming** — `run.stream` / `run.astream` yield text deltas + tool events. Incremental token-level deltas on the OpenAI Chat family (including Microsoft Foundry, Foundry Local, and Hugging Face), Anthropic, and Ollama; OpenAI *Responses*, Gemini, and Bedrock yield the whole response as one delta.
- **Structured output** — a dataclass / Pydantic / JSON-schema `output_type` uses each provider's native schema mode.
- **Reasoning & control** — `Agent.extra` passes `tool_choice`, `reasoning_effort`, `top_p`, `stop`, …; o-series `temperature` is handled for you.
- **RAG** — `VectorIndex` + `Agent(retriever=…)` inject governed retrieval, or expose your store as a `@tool`.
- **Memory** — `Session` (conversation), `SummarizingSession` (rolling summary), `SQLiteSessionStore` (durable), `context_budget` (fit the window).
- **Embeddings** — `embed()` / `aembed()` capture RAG calls on the same cost/audit tree.
- **Cost governance for any model** — `register_model_price(...)` so budgets bind on custom / deployment-named ids.
- **Interop** — MCP tools, A2A server/client, a Foundry/Copilot adapter, and human-in-the-loop approvals on the same audit chain.
- **Production hardening** — retry policies, and checkpointed/resumable runs so a crashed run continues where it stopped.
- **Observability, zero telemetry code** — configure any OpenTelemetry provider and `run()` emits an `agent.run` span tree with usage/cost rollups and governance correlated to the run; `CENDOR_TELEMETRY=off` switches it off. Cendor has no endpoint or key — it emits into **your** backend.
- **Agent identity** — `Agent(id="reg-42")` rides the semconv `gen_ai.agent.id` on every span and governance row, so a budget block says **which** agent it stopped; no id means the attribute is omitted, never fabricated.

## Scope & honest limits

- **`on_exceed="raise"` overshoots by one call** — it's post-flight. For a true ceiling use `"block"`.
- **Unpriced models record `$0`,** so a USD cap can't bind on them — `register_model_price(...)` or use a token cap.
- **`guard` redacts what its detectors find** — regex/pattern detectors plus Presidio NER (an optional extra). See [acttrace](https://cendor.ai/docs/acttrace) for coverage.
- **`guard` / interceptors are process-global** — they register on the single in-process bus, so install policy once at startup rather than toggling per request.
- **Evidence, not compliance.** The audit chain supports a compliance case; it doesn't make one, and it isn't legal advice.

## Docs

Rendered, searchable, with a page-wide Python / TypeScript toggle at
[cendor.ai/docs/sdk](https://cendor.ai/docs/sdk/getting-started) — the same markdown also renders on
GitHub.

- [Quickstart & reference](https://cendor.ai/docs/sdk/getting-started) · [Architecture — the libraries inside the SDK](https://cendor.ai/docs/sdk/architecture)
- [Agents & the loop](https://cendor.ai/docs/sdk/agents) · [Multi-agent orchestration](https://cendor.ai/docs/sdk/multi-agent)
- [Governance](https://cendor.ai/docs/sdk/governance) · [Guardrails](https://cendor.ai/docs/sdk/guardrails) · [Observability](https://cendor.ai/docs/sdk/observability)
- [Providers](https://cendor.ai/docs/sdk/providers) · [Interop — MCP, A2A, Foundry, OTel, HITL](https://cendor.ai/docs/sdk/interop)
- [Production hardening](https://cendor.ai/docs/sdk/hardening) · [Governed eval](https://cendor.ai/docs/sdk/eval) · [FAQ](https://cendor.ai/docs/sdk/faq)
- [Runnable, network-free examples](https://github.com/cendorhq/cendor-sdk/tree/main/examples) · [Changelog](https://github.com/cendorhq/cendor-sdk/blob/main/CHANGELOG.md)

## License

Apache-2.0.
