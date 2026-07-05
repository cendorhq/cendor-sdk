# Examples

Runnable, **network-free** examples for `cendor-sdk`. Each uses a fake/stub client (or a recorded
cassette) so it runs offline with no API keys — the same discipline as the test suite.

| Example | Phase | Shows |
|---|---|---|
| [single_agent.py](single_agent.py) | 1 | A governed single agent — budget + audit + redaction + a tool call, offline. |
| [handoff.py](handoff.py) | 2 | A planner hands the conversation to a writer via a transfer tool. |
| [supervisor.py](supervisor.py) | 2 | A coordinator routes to 2 sub-agents; per-agent budgets; one verifiable audit trail. |

Run one with:

```bash
uv run python examples/single_agent.py
```
