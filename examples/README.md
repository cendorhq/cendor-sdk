# Examples

Runnable, **network-free** examples for `cendor-sdk`. Each uses a fake/stub client (or a recorded
cassette) so it runs offline with no API keys — the same discipline as the test suite.

| Example | Shows |
|---|---|
| [single_agent.py](single_agent.py) | A governed single agent — budget + audit + redaction + a tool call, offline. |
| [handoff.py](handoff.py) | A planner hands the conversation to a writer via a transfer tool. |
| [supervisor.py](supervisor.py) | A coordinator routes to 2 sub-agents; per-agent budgets; one verifiable audit trail. |
| [mcp_agent.py](mcp_agent.py) | Consume an MCP tool, emit an OTel span tree, and serve the agent over A2A. |
| [eval_suite.py](eval_suite.py) | Record a trajectory, then replay it as a regression test gating output + cost. |
| [foundry_agent.py](foundry_agent.py) | Governed agent on Azure AI Foundry (cloud) + Foundry Local (on-device), offline. |
| [huggingface_agent.py](huggingface_agent.py) | Governed agent on Hugging Face Inference — `chat_completion` attributed to `huggingface`. |

Run one with:

```bash
uv run python examples/single_agent.py
```
