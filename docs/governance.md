# Governance

Cap what a run may spend, attribute every cent, keep tamper-evident evidence, and redact PII
before it leaves the process — each one is a single wrapper around `run()`. Everything on this
page is the **real** [tokenguard](/docs/tokenguard) / [acttrace](/docs/acttrace) API — the objects
re-exported from `cendor.sdk` for one-import convenience, with `guard` as acttrace's policy
enforcement in the SDK's scope form; it all rides
[`cendor-core`](/docs/core)'s seams, so it applies to any instrumented call, not just SDK runs.

## Quickstart

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import Agent, run, budget, guard, Policy, AuditLog

agent = Agent(name="support", model="gpt-4o", instructions="Help politely.")

log = AuditLog(system="support", risk_tier="limited", path="audit.jsonl")
with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
    result = run(agent, "Why was I charged twice?", audit=log)
```

<!-- tab: TypeScript -->

```ts
import { Agent, run, withBudget, guard, Policy, AuditLog } from '@cendor/sdk';

const agent = new Agent({ name: 'support', model: 'gpt-4o', instructions: 'Help politely.' });

const audit = new AuditLog('support', { riskTier: 'limited', path: 'audit.jsonl' });
const result = await withBudget({ usd: 0.25, onExceed: 'block' }, () =>
  guard({ policy: Policy.default(), audit }, () =>
    run(agent, 'Why was I charged twice?', { audit })));
```

<!-- /tabs -->

## Core concepts

### Budgets

A pre-flight budget stops an over-budget call **before it runs**; a post-flight one trips after.
Pick by intent (the full decision guide is in
[tokenguard → hard cap vs runaway guard](/docs/tokenguard#core-concepts)):

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import budget

with budget(usd=0.25, on_exceed="block"):     # pre-flight: the over-budget call never runs
    run(agent, "...")

with budget(usd=1.00, on_exceed="raise"):     # post-flight: raises after the cap is crossed
    run(agent, "...")

with budget(usd=0.50, on_exceed="downgrade", downgrade={"gpt-4o": "gpt-4o-mini"}):
    run(agent, "...")                          # reroutes to the cheaper model
```

<!-- tab: TypeScript -->

```ts
import { withBudget } from '@cendor/sdk';

await withBudget({ usd: 0.25, onExceed: 'block' },   // pre-flight: never runs over budget
  () => run(agent, '...'));

await withBudget({ usd: 1.00, onExceed: 'raise' },   // post-flight: raises after the cap
  () => run(agent, '...'));

await withBudget({ usd: 0.50, onExceed: 'downgrade', downgrade: { 'gpt-4o': 'gpt-4o-mini' } },
  () => run(agent, '...'));                          // reroutes to the cheaper model
```

<!-- /tabs -->

`BudgetExceeded` is raised on block/raise. Budgets stack — the innermost cap is enforced first.
`Agent(max_usd=...)` caps that agent's spend pre-flight (`block` semantics) on **every** run — a
plain `run(agent, ...)` as well as a single agent's segment in a multi-agent team
([Multi-agent → per-agent budgets](multi-agent.md#per-agent-budgets--attribution)).

### Attribution

Tag ambient spend by feature, user, session — anything — and read it back grouped, or assert it
in a test:

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import track, report

with track(feature="support", user_id="alice"):
    run(agent, "...")

report(group_by=["feature"]).assert_under(usd=0.05, feature="support")
```

<!-- tab: TypeScript -->

```ts
import { track, report } from '@cendor/sdk';

await track({ feature: 'support', userId: 'alice' }, () => run(agent, '...'));

report(['feature']).assertUnder(0.05, { feature: 'support' });
```

<!-- /tabs -->

### Audit + redaction

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import AuditLog, guard, Policy, verify

log = AuditLog(system="support", risk_tier="high", path="audit.jsonl")
with guard(Policy.gdpr(), audit=log):        # redacts PII *before* the provider sees it
    run(agent, "email me at alice@example.com", audit=log)

ok, detail = verify("audit.jsonl")           # tamper-evident hash chain, checked offline
assert ok
```

<!-- tab: TypeScript -->

```ts
import { AuditLog, guard, Policy, verify } from '@cendor/sdk';

const audit = new AuditLog('support', { riskTier: 'high', path: 'audit.jsonl' });
await guard({ policy: Policy.gdpr(), audit }, () =>   // redacts PII *before* the provider sees it
  run(agent, 'email me at alice@example.com', { audit }));

const [ok, detail] = verify('audit.jsonl');   // tamper-evident hash chain, checked offline
```

<!-- /tabs -->

- `AuditLog` auto-subscribes to the bus and records every `llm_call` / `tool_call` /
  `context_assembly` with zero wiring. Editing any past entry breaks every entry after it —
  that's the hash chain, and `verify()` re-walks it offline.
- `guard()` installs a pre-call interceptor that redacts, blocks, or flags per the `Policy`.
  `Policy.default()`, `Policy.gdpr()`, `Policy.pci()`, `Policy.strict()` are built in; the
  detector catalogue and custom policies live in [acttrace](/docs/acttrace).
- Human approvals can join the same chain — see
  [Interop → human-in-the-loop](interop.md#human-in-the-loop--approvals-in-the-audit-chain).

### Testing — record once, replay forever

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor import cassette

with cassette.using("tests/fixtures/run.json"):   # records on first run, replays after
    result = run(agent, "What's the weather in Paris?")
```

<!-- tab: TypeScript -->

```ts
import { using } from '@cendor/cassette';

const result = await using('tests/fixtures/run.json', () =>   // records once, replays after
  run(agent, "What's the weather in Paris?"));
```

<!-- /tabs -->

Record/replay is the [cassette](/docs/cassette) library — the SDK's [eval harness](eval.md) drives
it, but **cassette is not re-exported by the SDK**: import it from the umbrella as
`from cendor import cassette` (Python) / `@cendor/cassette` (TypeScript), as the snippet above does.
Replay is deterministic and offline — the same trajectory every time, no network, no keys — and
replayed calls re-emit their recorded usage, so **cost and tokens are real on replay**. That's what
makes spend a testable property; the eval harness builds directly on it.

## Plugs into the stack

The SDK adds no governance machinery of its own. Each wrapper attaches to a
[`cendor-core`](/docs/core) seam, which is why the same line works under the SDK loop, under a
bare instrumented client, and in both languages:

| Wrapper | Seam | Moment |
|---|---|---|
| `budget(on_exceed="block"/"downgrade")` | pre-flight interceptor | before the call runs |
| `budget(on_exceed="raise")` | bus subscriber | after usage lands |
| `track` / `report` | bus subscriber | after usage lands |
| `guard(Policy...)` | pre-flight interceptor | before the request leaves |
| `AuditLog` | bus subscriber | every event, appended to the chain |
| [`Agent(guardrails=[…])`](guardrails.md) | in-loop gate (the [cendor-guardrails](/docs/guardrails) Gate) | four stages: input / tool_call / tool_output / output |
| `cassette` | subscriber (record) + interceptor (replay) | around the call |

## Honest limits

- **`on_exceed="raise"` overshoots by one call** — it's post-flight. For a true ceiling use
  `"block"` (details in [tokenguard → honest limits](/docs/tokenguard#honest-limits)).
- **Unpriced models record `$0`,** so a USD cap can't bind on them — register a rate or use a
  token cap ([Providers → pricing custom models](providers.md#pricing-unpriced-models)).
- **`guard` redacts what its detectors find.** Regex/pattern detectors plus (Python-only)
  Presidio NER — see [acttrace](/docs/acttrace) for coverage and the
  [parity matrix](/docs/languages) for the language split.
- **`guard`/interceptors are process-global.** They register on the single in-process event bus,
  not per-call or per-agent, so in a concurrent server they scope around your whole app rather than
  one request — install policy once at startup, don't toggle it per request. For **per-agent,
  per-run** gating (keyword / regex / URL / length / schema, at four stages) use
  [`Agent(guardrails=[…])`](guardrails.md) instead — the [cendor-guardrails](/docs/guardrails) Gate,
  scoped to the agent and overridable per run, recording to the same audit chain.
- **Evidence, not compliance.** The audit chain supports a compliance case; it doesn't make one.
