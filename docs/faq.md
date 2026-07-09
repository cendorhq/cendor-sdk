# FAQ

## Libraries or SDK — which door do I take?

Pick by what you already have:

- **You have a framework** (LangChain, LlamaIndex) or call a provider SDK directly → use the
  [libraries](/docs) underneath it. You keep your loop; Cendor adds budgets, audit, redaction,
  record/replay beneath it via one `instrument()` wrap (or the LangChain callback handler).
- **You're starting fresh**, or want an agent without picking a framework → use the SDK.
  `Agent` + `tool` + `run`, with every governance layer one import away.
- **You're not sure** → start with the SDK; it's the shorter path to a working governed agent,
  and moving down to the libraries later is continuous (next answer).

## Can I use both at the same time?

Yes — they're the same objects. `budget`, `track`, `guard`, `Policy`, `AuditLog`, and `trace`
re-exported from `cendor.sdk` **are** the tokenguard/acttrace originals, so a `budget()` opened
around an SDK `run()` also caps a bare instrumented client call in the same scope, and one
`AuditLog` records both. A common mix: the SDK runs the agent loop, while a separately
`instrument()`-ed client handles non-agent calls (embeddings, one-shot completions) — one budget,
one audit chain, one `report()`.

## Does the SDK lock me in?

No. `result.messages` is the canonical (OpenAI-shape) conversation; tools are plain functions;
governance objects are library objects. Dropping the SDK means keeping the libraries and writing
your own loop — the concepts transfer one-to-one because they were never SDK concepts.

## Is this another LangChain?

It's deliberately less. No chains, no graph DSL, no vector store, no prompt templates — a bounded
ReAct loop with tools, sessions, and governance built in. If you want a rich orchestration
vocabulary, use a framework — and run the [libraries](/docs) beneath it; that's what they're for.

## Which languages are supported?

Python (`pip install cendor-sdk`) and TypeScript/JavaScript (`npm i @cendor/sdk`). Same API
shapes (`snake_case` ↔ `camelCase`), same defaults, same error names; artifacts (cassettes,
audit chains) are byte-for-byte interoperable. All ten provider paths ship in TypeScript — the
full split (and what stays Python-only) is in the [parity matrix](/docs/languages).

## Why did my `budget(usd=...)` not stop a run?

Almost always one of two things:

1. **The model is unpriced** — Hub ids, Azure deployment names, and custom gateways aren't in
   the bundled price table, so calls record `$0`. Register a rate
   ([Providers → pricing unpriced models](providers.md#pricing-unpriced-models)) or use a
   `tokens=` cap.
2. **The mode is post-flight** — `on_exceed="raise"` trips *after* the breaching call returns.
   For a hard ceiling use `"block"` ([Governance → budgets](governance.md#budgets)).

## How do I block or redact unsafe input / output?

Attach [`Agent(guardrails=[…])`](guardrails.md): deterministic checks (`keyword_deny`, `regex_rule`,
`url_allowlist` / `url_deny`, `length_bounds`, `json_schema`, `custom`) at four stages — `input`
(pre-spend), `tool_call`, `tool_output`, `output`. `block` fails closed (an input block raises before
the model is called — `$0`), `redact` rewrites the payload, `flag` records; every decision lands on
the audit chain. Override per run with `run(agent, input, guardrails=[…])`. The checks are
offline/deterministic, so they don't catch a novel jailbreak — add a `rules.llm_judge` (your model
call) for open-ended risk. For PII/secrets, `rules.pii()` / `rules.secrets()` / `rules.entropy()`
bridge `acttrace`'s detector catalogue as guardrails at all four stages (including `tool_output`);
`guard(Policy…)` remains the process-global option.

## How do I test an agent without an API key?

Record once, replay forever: wrap the run in
[`cassette.using(...)`](governance.md#testing--record-once-replay-forever) — first run records,
every run after replays offline with real usage/cost re-emitted. The
[eval harness](eval.md) turns those recordings into CI regression tests.

## Can agents on different providers work together?

Yes — the conversation is canonical, so an OpenAI planner can hand off to an Anthropic writer
with no translation ([Multi-agent](multi-agent.md)). Provider handoff is a model-id change, not
an architecture change.

## Does it work in notebooks / scripts / servers?

All three. In Python `run()` is sync, with `run.aio` / `run.astream` for servers and notebooks with
running loops; in TypeScript everything is async (`run` / `run.stream`). Nothing spawns background
services; state lives in the process (and in whatever session/checkpoint files you ask for).

## Where do the numbers on the website come from?

Every performance and behaviour claim is measured by the reproducible, offline benchmark suite —
see [/benchmarks](/benchmarks) and the library docs'
[benchmark methodology](/docs/benchmarks).
