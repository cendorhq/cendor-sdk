# cendor-sdk

**A governed agent in 10 lines — cost budgets, tamper-evident audit, and PII redaction built in.**

![version](https://img.shields.io/badge/version-1.0.1-blue) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-Apache_2.0-blue) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) ![types: mypy](https://img.shields.io/badge/types-mypy-blue)

*provider-agnostic · local-first · offline by default · sync **and** async*

> The only agent SDK where **cost budgets, tamper-evident audit, PII redaction, context governance,
> and record/replay testing are the *foundation*, not plugins.**

`cendor-sdk` **owns the agent loop**, so every governance concern that is best-effort *beneath* a
framework becomes first-class here: usage is never lost, budgets enforce *before* the model call,
PII is redacted *before* send, and the whole run correlates under one `trace_id`. It's the simple,
batteries-included door into the [Cendor](https://github.com/cendorhq/Cendor) stack — you don't need
to pick a framework or wire the libraries. (Already have a framework? Compose the libraries beneath
it: `pip install cendor`.)

## Install

```bash
pip install "cendor-sdk[openai,anthropic]"     # provider SDKs are optional extras
pip install "cendor-sdk[all]"                  # every provider + interop, batteries included
```

The install bundles the whole Cendor stack (`cendor-core`, `tokenguard`, `acttrace`, `contextkit`,
`squeeze`, `cassette`) by dependency — you install once and import only from `cendor.sdk`. Provider
SDKs stay optional extras: `[openai]`, `[anthropic]`, `[google]`, `[bedrock]`, `[ollama]`,
`[huggingface]`, `[azure]`, `[foundry-local]`, plus `[mcp]` and `[otel]`.

## A governed agent in 10 lines

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
| **Azure AI Foundry** | deployments via the OpenAI v1 endpoint (Chat + Responses) | `[azure]` |
| **Foundry Local** | on-device, OpenAI-compatible | `[foundry-local]` |

## More in the box

Everything a real agent needs — all governed through the same seams:

- **Streaming** — `run.stream` / `run.astream` yield text deltas + tool events (native for the OpenAI family + Ollama).
- **Structured output** — a dataclass / Pydantic / JSON-schema `output_type` uses each provider's native schema mode.
- **Reasoning & control** — `Agent.extra` passes `tool_choice`, `reasoning_effort`, `top_p`, `stop`, …; o-series `temperature` is handled for you.
- **RAG** — `VectorIndex` + `Agent(retriever=…)` inject governed retrieval, or expose your store as a `@tool`.
- **Memory** — `Session` (conversation), `SummarizingSession` (rolling summary), `SQLiteSessionStore` (durable), `context_budget` (fit the window).
- **Embeddings** — `embed()` / `aembed()` capture RAG calls on the same cost/audit tree.
- **Cost governance for any model** — `register_model_price(...)` so budgets bind on custom / deployment-named ids.

## Docs

- [Quickstart & reference](https://github.com/cendorhq/cendor-sdk/blob/main/docs/sdk.md)
- [Multi-agent orchestration](https://github.com/cendorhq/cendor-sdk/blob/main/docs/multi-agent.md)
- [Interop — MCP, A2A, Foundry, OTel, HITL](https://github.com/cendorhq/cendor-sdk/blob/main/docs/interop.md)
- [Production hardening](https://github.com/cendorhq/cendor-sdk/blob/main/docs/hardening.md) · [Governed eval](https://github.com/cendorhq/cendor-sdk/blob/main/docs/eval.md)
- [Runnable, network-free examples](https://github.com/cendorhq/cendor-sdk/tree/main/examples)
- [Changelog](https://github.com/cendorhq/cendor-sdk/blob/main/CHANGELOG.md)

## License

Apache-2.0.
