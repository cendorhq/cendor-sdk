# The cendor-sdk quickstart & reference

> **A governed agent in 10 lines.** `Agent`, `tool`, `run`, the loop, and how governance composes —
> then providers (OpenAI · Anthropic · Gemini · Bedrock · Ollama · Hugging Face · Azure AI Foundry ·
> Foundry Local), streaming, structured output, sessions & memory, retrieval (RAG), and embeddings.

## Install

```bash
pip install "cendor-sdk[openai,anthropic]"
```

Provider SDKs are optional extras (`[openai]`, `[anthropic]`, `[google]`, `[bedrock]`, `[ollama]`,
`[huggingface]`, `[azure]`). The install bundles the whole Cendor stack by dependency — import only
from `cendor.sdk`.

## A governed agent in 10 lines

```python
from cendor.sdk import Agent, tool, run, budget, guard, Policy, AuditLog

@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""      # schema is derived from type hints + docstring
    return f"Sunny in {city}"

agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather],
              instructions="Answer using tools when helpful.")

log = AuditLog(system="support", risk_tier="limited", path="audit.jsonl")
with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
    result = run(agent, "What's the weather in Paris?", audit=log)

print(result.output)                        # the final answer
print(result.cost, result.usage)            # Decimal money, real token usage
print([s.name for s in result.tool_steps])  # ["get_weather"]
```

## Ungoverned — core only

Governance is opt-in. Drop the `with` block and it runs bare on `cendor-core`:

```python
from cendor.sdk import Agent, run

agent = Agent(name="a", model="gpt-4o", instructions="Be brief.")
result = run(agent, "Hello")               # sync
result = await run.aio(agent, "Hello")     # async — same signature
```

## `Agent`

```python
Agent(
    name: str,
    model: str,                    # any core-supported provider id: "gpt-4o", "claude-opus-4-8", ...
    instructions: str = "",        # the system prompt
    tools: list = [],              # @tool-decorated callables or Tool objects
    provider: str | None = None,   # override provider inference from the model id
    output_type: type | dict | None = None,  # structured output (dataclass / JSON schema)
    max_turns: int = 8,            # ReAct loop bound (termination guarantee)
    context_budget: int | None = None,  # assemble history to this token budget via contextkit
    temperature: float | None = None,
    max_tokens: int | None = None,
)
```

Provider is inferred from the model id (`gpt-*`/`o*` → OpenAI, `claude-*` → Anthropic,
`gemini-*` → Google, …); pass `provider=` to override. Hugging Face (`provider="huggingface"`) and
Azure AI Foundry (`provider="azure"`) are **not** prefix-inferable — Hub ids and Foundry deployment
names are arbitrary — so pass `provider=` explicitly for those (see
[Connecting to Hugging Face & Azure AI Foundry](#connecting-to-hugging-face--azure-ai-foundry)).

`api_key` / `base_url` / `client` are also accepted: `api_key` falls back to the provider's env var,
`base_url` targets a gateway or self-hosted/Azure endpoint, and `client` lets you pass a pre-built
SDK client (it's instrumented on adoption, so budgets/guard/audit still apply).

## `tool` / `Tool`

`@tool` turns a plain function into a `Tool`: the JSON Schema is generated from the function's type
hints, and the description from its docstring. Sync and async functions both work.

```python
from cendor.sdk import tool

@tool
def search(query: str, top_k: int = 3) -> list[str]:
    """Search the knowledge base."""
    ...

@tool(name="lookup")
async def fetch(url: str) -> str:
    """Fetch a URL."""
    ...
```

Each tool executes through `cendor-core`'s `instrument_tool`, so every invocation emits a `ToolCall`
on the bus (correlated by `trace_id`, recorded by the audit chain, replayable by cassette).

The generated schema is formatted per provider automatically — OpenAI `functions`, Anthropic
`tools`, Gemini function declarations, Bedrock `toolConfig`.

## `run` / `Runner`

```python
run(agent, input, *, session=None, audit=None, max_turns=None) -> Result   # sync
await run.aio(agent, input, *, session=None, audit=None, max_turns=None)    # async
```

- `input` is a string or a list of messages.
- `session` — a `Session` for multi-turn memory (see below).
- `audit` — an `AuditLog`; when passed, each agent step is wrapped in an acttrace `decision()` so
  the `llm_call`/`tool_call` entries are correlated in the chain by `decision_id` and the run's
  `trace_id`.

### The loop

Per turn `Runner`: assemble context (optional `contextkit`) → format for the provider → **call**
inside `trace(run_id)` (usage/cost captured; budget/guard fire pre-call) → **normalize** the
response → run any tool calls (`ToolCall` per call) → loop, or finalize into a `Result`.

## `Result`

```python
result.output        # final answer (str) or the parsed structured object
result.steps         # list[Step] — one per LLMCall/ToolCall, in order, correlated by trace_id
result.llm_steps     # the model turns
result.tool_steps    # the tool executions
result.usage         # aggregate Usage across the run
result.cost          # aggregate Money (Decimal) across the run
result.trace_id      # the run id every step shares
result.messages      # the full conversation (canonical/OpenAI-shape messages)
```

Each `Step` wraps the actual `LLMCall`/`ToolCall` from the bus (`.call`), with `.agent`, `.kind`
(`"llm"`/`"tool"`), and `.name` (model id or tool name).

## Structured output

```python
from dataclasses import dataclass

@dataclass
class Weather:
    city: str
    conditions: str

agent = Agent(name="w", model="gpt-4o", instructions="Report weather.", output_type=Weather)
result = run(agent, "Weather in Paris?")
assert isinstance(result.output, Weather)
```

`output_type` accepts a dataclass (parsed from JSON) or a raw JSON-schema `dict`. The runner asks
the provider for JSON and parses the final message into the requested type.

## Sessions (in-memory)

```python
from cendor.sdk import Session

session = Session()
run(agent, "My name is Alice.", session=session)
result = run(agent, "What's my name?", session=session)   # remembers within the process
```

`Session` keeps the conversation across `run()` calls. Durable/local persistence lands in Phase 2.

## Governance composes through seams

Everything below is the **real** tokenguard/acttrace API, re-exported from `cendor.sdk` for
one-import convenience. Nothing here is SDK-specific glue — it all rides `cendor-core`'s seams.

### Budgets — `budget`

```python
from cendor.sdk import budget

with budget(usd=0.25, on_exceed="block"):     # pre-flight: the over-budget call never runs
    run(agent, "...")

with budget(usd=1.00, on_exceed="raise"):     # post-flight: raises after the cap is crossed
    run(agent, "...")

with budget(usd=0.50, on_exceed="downgrade", downgrade={"gpt-4o": "gpt-4o-mini"}):
    run(agent, "...")                          # reroutes to the cheaper model
```

`BudgetExceeded` is raised on block/raise. Budgets stack (innermost cap enforced first).

### Attribution — `track`

```python
from cendor.sdk import track, report        # report re-exported from tokenguard

with track(feature="support", user_id="alice"):
    run(agent, "...")

report(group_by=["feature"]).assert_under(usd=0.05, feature="support")
```

### Audit + redaction — `AuditLog`, `guard`, `Policy`

```python
from cendor.sdk import AuditLog, guard, Policy
from cendor.acttrace import verify          # the chain verifier

log = AuditLog(system="support", risk_tier="high", path="audit.jsonl")
with guard(Policy.gdpr(), audit=log):        # redacts PII *before* the provider sees it
    run(agent, "email me at alice@example.com", audit=log)

ok, detail = verify("audit.jsonl")           # tamper-evident hash chain
assert ok
```

`AuditLog` auto-subscribes to the bus and records every `llm_call`/`tool_call`/`context_assembly`
with zero wiring. `guard()` (the SDK's context-manager wrapper around `acttrace.guard`) installs a
pre-call interceptor that redacts / blocks / flags per the `Policy`. `Policy.default()`,
`Policy.gdpr()`, `Policy.pci()`, `Policy.strict()` are built in.

## Testing — record once, replay forever

```python
from cendor import cassette

with cassette.using("tests/fixtures/run.json"):   # records on first run, replays after
    result = run(agent, "What's the weather in Paris?")
```

Replay is deterministic and offline — the same trajectory every time, no network, no keys. This is
the same `cassette` that powers the SDK's own test suite (with `respx` mocking provider HTTP).

## Provider support

| Provider | Client construction | Response normalization | Tool formatting |
|---|---|---|---|
| OpenAI (Chat Completions) | ✅ | ✅ | ✅ functions |
| OpenAI (Responses API) | ✅ | ✅ | ✅ functions |
| Anthropic (Messages) | ✅ | ✅ | ✅ tools |
| Google Gemini | via `[google]` | ✅ | ✅ function declarations |
| AWS Bedrock | via `[bedrock]` | ✅ | ✅ toolConfig |
| Ollama (local) | via `[ollama]` | ✅ | ✅ functions |
| Hugging Face | via `[huggingface]` | ✅ (OpenAI-shape) | ✅ functions |
| Azure AI Foundry (Chat + Responses) | via `[azure]` | ✅ (OpenAI-shape) | ✅ functions |
| Foundry Local (on-device) | via `[foundry-local]` | ✅ (OpenAI-shape) | ✅ functions |

Normalization is isolated in `providers.py` with per-provider fixtures, so provider response-shape
drift is contained.

## Connecting to Hugging Face & Azure AI Foundry

Both speak the OpenAI **Chat Completions shape**, so their providers reuse the OpenAI request
formatting and response parsing — only client construction differs. `cendor-core` detects each
structurally, so cost/usage capture and the `budget`/`guard`/audit seams apply unchanged.

### Hugging Face

Uses `huggingface_hub.InferenceClient.chat_completion` (the response is OpenAI-shaped; the call is
attributed to `huggingface`, not `openai`). The `model` is a Hub id or an Inference Endpoint URL.

```python
from cendor.sdk import Agent, run

# Serverless Inference API — token from api_key= or the HF_TOKEN / HUGGINGFACEHUB_API_TOKEN env var
agent = Agent(
    name="hf",
    model="meta-llama/Llama-3.1-8B-Instruct",
    provider="huggingface",        # required — Hub ids aren't prefix-inferable
    api_key="hf_...",              # optional; falls back to HF_TOKEN
)
result = run(agent, "Summarize the plot of Hamlet in two lines.")

# A dedicated Inference Endpoint (or a third-party provider) — point base_url at it:
agent = Agent(name="hf", model="tgi", provider="huggingface",
              base_url="https://<your-endpoint>.endpoints.huggingface.cloud")
```

Route through a specific inference provider (e.g. `together`, `fireworks-ai`) with the `HF_PROVIDER`
env var. Install with `pip install "cendor-sdk[huggingface]"`.

### Azure AI Foundry

Microsoft's current guidance — the `AzureOpenAI` client and the `azure-ai-inference` package are
being retired (`azure-ai-inference` on **2026-05-30**) — is to consume Foundry deployments with the
**standard `openai` SDK** pointed at the Foundry `/openai/v1/` endpoint. So `provider="azure"` *is*
the OpenAI provider with Foundry-aware construction. Two rules matter:

1. **`model` is your deployment name**, not the underlying model name (Azure keys on deployment).
2. **`base_url` is the Foundry endpoint** — `/openai/v1/` is appended for you. Also read from
   `AZURE_OPENAI_ENDPOINT`. `api-version` is not needed (the v1 GA API infers it).

```python
from cendor.sdk import Agent, run, budget

# Azure OpenAI models:      https://<res>.openai.azure.com
# Foundry Models (DeepSeek, # https://<res>.services.ai.azure.com   ← both work, /openai/v1/ added
#   Grok, Llama, …):
agent = Agent(
    name="foundry",
    model="my-gpt4o-deployment",       # your Foundry DEPLOYMENT name
    provider="azure",
    base_url="https://my-resource.openai.azure.com",   # or AZURE_OPENAI_ENDPOINT
    api_key="<resource-key>",          # or AZURE_OPENAI_API_KEY / AZURE_INFERENCE_CREDENTIAL
)

with budget(usd=0.10, on_exceed="block"):
    result = run(agent, "Draft a one-line release note.")
```

**Microsoft Entra ID (keyless):** pass a bearer-token provider as `api_key` (the v1 client refreshes
it), or build the client yourself and hand it to `Agent(client=...)`:

```python
from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token = get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default")
client = OpenAI(base_url="https://my-resource.openai.azure.com/openai/v1/", api_key=token)
agent = Agent(name="foundry", model="my-gpt4o-deployment", provider="azure", client=client)
```

For an OpenAI-family deployment (`gpt-*`, `o*`) you can drive the **Responses API** instead of Chat
Completions with `provider="azure_responses"` — same Foundry-aware construction, Responses
semantics. Keep `provider="azure"` (Chat Completions) for the broadest reach across non-OpenAI
Foundry models (DeepSeek, Grok, Llama, …).

Install with `pip install "cendor-sdk[azure]"` (just pulls `openai`).

### Foundry Local (on-device)

Microsoft **Foundry Local** runs a model on the device and exposes an OpenAI-compatible REST server
— the local counterpart to Ollama. `provider="foundry_local"` is the OpenAI Chat provider pointed at
that local endpoint; no key is needed. `model` is the resolved Foundry Local model id.

```python
from cendor.sdk import Agent, run
from foundry_local import FoundryLocalManager

# FoundryLocalManager starts the service, downloads/loads the model, and exposes the endpoint.
mgr = FoundryLocalManager("qwen2.5-0.5b")           # a catalog alias
agent = Agent(
    name="local",
    model=mgr.get_model_info("qwen2.5-0.5b").id,     # the resolved model id, not the alias
    provider="foundry_local",
    base_url=mgr.endpoint,                            # or set FOUNDRY_LOCAL_ENDPOINT
    api_key=mgr.api_key,                              # not required locally; defaults to "none"
)
result = run(agent, "Give me one tip for faster cold starts.")
```

If you already run the service yourself, just pass `base_url=` (or `FOUNDRY_LOCAL_ENDPOINT`) and skip
the manager. Install with `pip install "cendor-sdk[foundry-local]"`.

## Streaming

`run.stream` (sync) / `run.astream` (async) yield events as the run progresses; the terminal event
carries the same `Result` a blocking `run()` returns. Native token-by-token reassembly for the
OpenAI family + Ollama (tool-call deltas included); other providers fall back to a whole-response
delta. Single-agent.

```python
from cendor.sdk import Agent, run, TextDelta, ToolCallEvent, RunComplete

agent = Agent(name="a", model="gpt-4o", instructions="Be brief.")
for event in run.stream(agent, "Tell me a joke"):
    if isinstance(event, TextDelta):
        print(event.text, end="", flush=True)
    elif isinstance(event, ToolCallEvent):
        print(f"\n[calling {event.name}({event.arguments})]")
    elif isinstance(event, RunComplete):
        print("\ncost:", event.result.cost)
```

## Provider params & reasoning — `Agent.extra`

`Agent.extra` merges arbitrary request kwargs into every model call — the passthrough for anything
the SDK doesn't model first-class: `tool_choice`, `reasoning_effort`, `top_p`, `stop`, `seed`,
`response_format`, `extra_body`. It's merged at the top level (OpenAI/Anthropic/Ollama/HF/Azure
shape). o-series models (`o1`/`o3`/`o4`) that reject `temperature` have it omitted automatically.

```python
agent = Agent(name="a", model="o3-mini", extra={"reasoning_effort": "high"})
picky = Agent(name="p", model="gpt-4o", tools=[...], extra={"tool_choice": "required"})
```

## Structured output (schema-constrained)

An `output_type` (dataclass / Pydantic / JSON-schema dict) is turned into a JSON Schema and sent via
each provider's native structured-output feature — OpenAI `json_schema`, Ollama `format`, Gemini
`response_schema`; Anthropic/Bedrock embed the schema in the JSON instruction. Far more reliable than
a bare "respond with JSON".

## Multimodal input

A message's `content` may be a parts list (OpenAI shape). OpenAI-family passes it through natively;
Anthropic and Gemini translate images to their block formats (base64 or URL); Bedrock keeps the text.

```python
run(agent, [{"role": "user", "content": [
    {"type": "text", "text": "What's in this image?"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}},
]}])
```

## Embeddings (RAG)

`embed(model, inputs)` / `aembed(...)` return one vector per input and emit a governed `LLMCall`
(tokens + cost captured on the bus, correlated by `trace`) for OpenAI-family providers.

```python
from cendor.sdk import embed, trace
with trace("index-build"):
    vectors = embed("text-embedding-3-small", ["hello", "world"], provider="openai")
```

## Pricing unpriced models

Model ids absent from the bundled price table (HF Hub ids, Azure/Foundry deployment names, custom
gateways) cost `$0`, so a USD `budget(...)` can't bind. Register a rate so cost + budgets work:

```python
from cendor.sdk import register_model_price, budget
register_model_price("my-gpt4o-deployment", input=2.50, output=10.00)  # USD per 1M tokens
with budget(usd=0.10, on_exceed="block"):
    run(agent, "...")
```

Or use a `tokens=` cap, or `configure(on_unpriced="raise")` to reject unpriced calls under
`on_exceed="block"`.

## Resumable multi-agent runs & completion signal

`run([entry, peer, ...], input, checkpoint="team.ckpt.json")` persists the trajectory per
turn/segment and resumes a crashed team run. `Result.incomplete` is `True` when a run ended without a
final answer (e.g. `max_turns` hit mid tool-loop).

## Retrieval (RAG)

The SDK governs retrieval; it is not a vector database. Two paths:

**1. Always-on RAG via `Agent(retriever=...)`.** A `retriever` is any `query -> list[str]` callable.
`VectorIndex` is a dependency-free in-memory cosine index built on the governed `embed()` (fine for
small corpora, demos, tests); for scale, plug your own store (pgvector/Pinecone/Chroma) as a
retriever. Retrieved passages are injected as a system message before the model call.

```python
from cendor.sdk import Agent, run, VectorIndex

kb = VectorIndex(model="text-embedding-3-small", provider="openai")   # or embedder=<your fn>
kb.add(["Refunds within 30 days.", "Support hours are 9-5 UTC."])
agent = Agent(name="rag", model="gpt-4o", retriever=kb.as_retriever(k=3),
              instructions="Answer only from the provided context.")
run(agent, "What's the refund window?")   # relevant passages retrieved + injected, embed governed
```

**2. Agentic RAG via a tool.** Let the model decide when to retrieve — expose your store as a `@tool`
(the retrieval then shows up as a governed `ToolCall`):

```python
@tool
def search_kb(query: str, top_k: int = 5) -> list[str]:
    """Retrieve relevant passages."""
    return [h.text for h in kb.search(query, k=top_k)]
agent = Agent(name="rag", model="gpt-4o", tools=[search_kb])
```

## Memory — how an agent remembers its context

Three layers, all local-first and composable:

**Working / conversation memory — `Session`.** Pass the same `Session` to successive `run()` calls and
the agent remembers prior turns (the canonical conversation is carried in, and the run writes it
back). Optional JSON persistence.

```python
from cendor.sdk import Session

session = Session()
run(agent, "My name is Alice.", session=session)
run(agent, "What's my name?", session=session)   # -> knows "Alice"
session.save("chat.json"); Session.load("chat.json")   # optional local persistence
```

**Durable memory across processes — `SQLiteSessionStore`.** Many named conversations in one local
SQLite file (no server):

```python
from cendor.sdk import SQLiteSessionStore
store = SQLiteSessionStore("sessions.db")
session = store.load("user-42")     # empty Session if unknown
run(agent, "hi", session=session)
store.save("user-42", session)      # survives restarts
```

**Fitting memory to the window — `context_budget`.** Long histories overflow the context window;
set `Agent(context_budget=8000)` and the loop assembles the conversation to that token budget via
`contextkit` (with `squeeze` compression when installed), emitting an audited `AssemblyReport`. This
is *how the agent keeps remembering* without the prompt growing unbounded.

**Crash recovery — `checkpoint`.** `run(agent, ..., checkpoint="run.ckpt.json")` (and
`run([...], checkpoint=…)` for teams) persists the conversation each turn so a crashed run resumes
without re-doing completed work.

**Rolling summarization — `SummarizingSession`.** `context_budget` *trims* to fit the window;
`SummarizingSession` instead *folds* old turns into a durable summary note (keeping recent turns
verbatim), so memory stays bounded without losing the gist of a long conversation.

```python
from cendor.sdk import SummarizingSession, run

mem = SummarizingSession(model="gpt-4o-mini", max_messages=20, keep_recent=8)
for msg in conversation:
    run(agent, msg, session=mem)   # old turns fold into a memory note; recent turns stay verbatim
```

The summarizer is pluggable — `model=` builds a governed one-shot summarizer (`llm_summarizer`), or
pass your own `summarizer=` callable for offline/extractive summaries. It triggers automatically
after each run and its model call rides the same governance seams.

**Long-term / semantic memory.** For remembering facts *across* sessions, back a `VectorIndex` (or
your vector DB) and attach it as the agent's `retriever` — past facts are retrieved by relevance and
injected, the same way as RAG documents. So: conversation memory = `Session` / `SummarizingSession`;
semantic memory = retrieval.
