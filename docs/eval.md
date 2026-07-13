# Eval & regression testing

Replay recorded agent trajectories as **tests**, and assert they don't regress — output, tool
sequence, and cost/token ceilings — offline, deterministic, and free. Because
[cassette](governance.md#testing--record-once-replay-forever) replay re-emits each recorded
call's usage, **cost and tokens are real on replay**: an eval suite gates *behaviour and spend*
in CI, with no network and no keys.

## Quickstart

### 1. Record once

Record a trajectory (in a test with the provider mocked, or against the real API once):

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor import cassette
from cendor.sdk import run

with cassette.using("evals/weather.json", mode="record"):
    run(agent, "What's the weather in Paris?")
```

<!-- tab: TypeScript -->

```ts
import { using } from '@cendor/cassette';
import { run } from '@cendor/sdk';

await using('evals/weather.json', { mode: 'record' }, () =>
  run(agent, "What's the weather in Paris?"));
```

<!-- /tabs -->

### 2. Assert it doesn't regress

<!-- tabs: lang -->
<!-- tab: Python -->

```python
from cendor.sdk import evaluate, EvalCase

cases = [
    EvalCase(
        name="weather-happy-path",
        input="What's the weather in Paris?",
        cassette="evals/weather.json",
        expect_output="It's sunny in Paris.",   # or expect_contains="sunny"
        expect_tools=["get_weather"],           # the exact tool sequence
        max_usd=0.01,                            # cost ceiling
        max_tokens=2000,                         # token ceiling
    ),
]

report = evaluate(agent, cases)
report.assert_ok()     # raises AssertionError listing any regressions — use this in a CI test
```

<!-- tab: TypeScript -->

```ts
import { evaluate, type EvalCase } from '@cendor/sdk';

const cases: EvalCase[] = [
  {
    name: 'weather-happy-path',
    input: "What's the weather in Paris?",
    cassette: 'evals/weather.json',
    expectOutput: "It's sunny in Paris.",   // or expectContains: 'sunny'
    expectTools: ['get_weather'],           // the exact tool sequence
    maxUsd: 0.01,                            // cost ceiling
    maxTokens: 2000,                         // token ceiling
  },
];

const report = await evaluate(agent, cases);
report.assertOk();     // throws listing any regressions — use this in a CI test
```

<!-- /tabs -->

Each `EvalResult` records the actual `output`, `cost_usd`, `tokens`, and `tools`, plus its
`failures`. `EvalReport` exposes `.passed`, `.failed`, `.ok`, `assert_ok()`, and a readable
`str()`.

## Core concepts

### What it catches

| Regression | Field | Failure |
|---|---|---|
| The agent stops calling a tool (or calls a new one) | `expect_tools` | `tool sequence [...] != [...]` |
| The answer changes | `expect_output` / `expect_contains` | `output ... != ...` |
| A change makes a run more expensive | `max_usd` | `cost $X > $ceiling (regression)` |
| A change inflates token usage | `max_tokens` | `tokens N > ceiling (regression)` |

**Volatile prompts replay too** (since 1.7.0 / 0.10.0): `EvalCase(normalizer=…)` /
`{ normalizer }` is forwarded to cassette's replay matching — normalize timestamps, uuids, or
"today's date" out of the request before hashing, so a prompt that embeds them still matches its
recording. Same signature as [cassette's own `normalizer`](/docs/cassette#core-concepts).

### A CI test

<!-- tabs: lang -->
<!-- tab: Python -->

```python
def test_agent_does_not_regress():
    from cendor.sdk import Agent, evaluate, EvalCase
    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="…")
    evaluate(agent, load_cases("evals/")).assert_ok()
```

<!-- tab: TypeScript -->

```ts
test('agent does not regress', async () => {
  const agent = new Agent({ name: 'assistant', model: 'gpt-4o',
                            tools: [getWeather], instructions: '…' });
  (await evaluate(agent, loadCases('evals/'))).assertOk();
});
```

<!-- /tabs -->

Run it with `pytest` / `vitest` — offline and deterministic, and it fails the build on a
behaviour *or* cost regression. That last part is the point: spend is normally invisible in CI because every
real call costs money and returns something different; on replay it's just another assertion.

## Plugs into the stack

An `EvalCase` is a [cassette](/docs/cassette) plus expectations — record fixtures wherever your
tests already record them, and keep them in the repo like any fixture. Tool sequences come from
[`result.tool_steps`](agents.md#result--the-receipt); ceilings read the same decimal cost the
[budget](governance.md#budgets) machinery enforces at runtime.

## Honest limits

- **A replay tests the agent against a frozen provider.** A regression *in the provider itself*
  (a model update changing behaviour) won't show until you re-record — schedule an occasional
  live re-record if that matters to you.
- **`expect_output` is exact by default.** For phrasing that legitimately varies, use
  `expect_contains` — or assert on tools and cost, which are stable.
- **The harness evaluates trajectories, not truth.** It catches *changes*; whether the recorded
  behaviour was ever *good* is your judgment when you record the fixture.
