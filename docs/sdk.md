# The cendor-sdk quickstart & reference

> **A governed agent in 10 lines.** This page covers the Phase 1 surface: `Agent`, `tool`, `run`,
> the loop, provider normalization, structured output, sessions, and how governance composes.

## Install

```bash
pip install "cendor-sdk[openai,anthropic]"
```

Provider SDKs are optional extras (`[openai]`, `[anthropic]`, `[google]`, `[bedrock]`, `[ollama]`).
The install bundles the whole Cendor stack by dependency — import only from `cendor.sdk`.

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
`gemini-*` → Google, …); pass `provider=` to override.

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
| Ollama | via `[ollama]` | ✅ | ✅ functions |

Normalization is isolated in `providers.py` with per-provider fixtures, so provider response-shape
drift is contained.
