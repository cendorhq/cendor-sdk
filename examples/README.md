# Examples

Runnable, **network-free** examples for `cendor-sdk`. Each uses a fake/stub client (or a recorded
cassette) so it runs offline with no API keys — the same discipline as the test suite.

| Example | Phase | Shows |
|---|---|---|
| [single_agent.py](single_agent.py) | 1 | A governed single agent — budget + audit + redaction + a tool call, offline. |

Run one with:

```bash
uv run python examples/single_agent.py
```
