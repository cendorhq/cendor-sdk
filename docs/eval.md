# Governed eval & regression testing

> Phase 4. The testing wedge fully realized: replay recorded agent trajectories as **tests** and
> assert they don't regress — output, **tool sequence**, and **cost/token ceilings** — offline,
> deterministic, and free.

Because `cassette` replay re-emits each recorded call's usage, **cost and tokens are real on
replay**. So an eval suite gates *behaviour and spend* regressions in CI, with no network and no
keys — the thing that's impossible when every run hits a paid, non-deterministic API.

## Record once

Record a trajectory (in a test, with `respx` mocking the provider; or against the real API once):

```python
from cendor import cassette
from cendor.sdk import run

with cassette.using("evals/weather.json", mode="record"):
    run(agent, "What's the weather in Paris?")
```

## Assert it doesn't regress

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

Each `EvalResult` records the actual `output`, `cost_usd`, `tokens`, and `tools`, plus the list of
`failures`. `EvalReport` exposes `.passed`, `.failed`, `.ok`, `assert_ok()`, and a readable `str()`.

## What it catches

| Regression | Field | Failure |
|---|---|---|
| The agent stops calling a tool (or calls a new one) | `expect_tools` | `tool sequence [...] != [...]` |
| The answer changes | `expect_output` / `expect_contains` | `output ... != ...` |
| A change makes a run more expensive | `max_usd` | `cost $X > $ceiling (regression)` |
| A change inflates token usage | `max_tokens` | `tokens N > ceiling (regression)` |

## A CI test

```python
def test_agent_does_not_regress():
    from cendor.sdk import Agent, evaluate, EvalCase
    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], instructions="…")
    evaluate(agent, load_cases("evals/")).assert_ok()
```

Run it with `pytest` — offline, deterministic, and it fails the build on a behaviour *or* cost
regression. This seeds a possible future `cendor-eval`.
