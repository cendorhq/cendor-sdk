"""A governed single agent — budget + audit + PII redaction + a tool call — running OFFLINE.

Everything here is real (the loop, budgets, the tamper-evident audit chain, redaction) except the
model call, which is served by a tiny stub client so the example needs no network and no API key.

In production you drop the ``client=`` argument and set ``OPENAI_API_KEY`` (or pass ``api_key=``):

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather])

Run it:  uv run python examples/single_agent.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, AuditLog, Policy, budget, guard, run, tool, verify


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny in {city}"


def _stub_client() -> object:
    """A stub OpenAI-shaped client: first turn asks for the tool, second turn answers."""
    turns = iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    id="call_1",
                                    type="function",
                                    function=SimpleNamespace(
                                        name="get_weather", arguments='{"city": "Paris"}'
                                    ),
                                )
                            ],
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=52, completion_tokens=12),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="It's sunny in Paris.", tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=88, completion_tokens=9),
            ),
        ]
    )

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(turns)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def main() -> None:
    audit_path = Path(tempfile.gettempdir()) / "cendor_sdk_example_audit.jsonl"
    agent = Agent(
        name="assistant",
        model="gpt-4o",
        tools=[get_weather],
        instructions="Answer using tools when helpful.",
        client=_stub_client(),  # offline stub — omit in production
    )

    log = AuditLog(system="support", risk_tier="limited", path=str(audit_path))
    with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
        result = run(agent, "What's the weather in Paris?", audit=log)
    log.detach()

    print("output    :", result.output)
    print("cost      :", result.cost)
    print("usage     :", result.usage)
    print("tool steps:", [s.name for s in result.tool_steps])
    print("trace_id  :", result.trace_id)

    ok, detail = verify(str(audit_path))
    print("audit ok  :", ok, "—", detail)
    print("audit file:", audit_path)


if __name__ == "__main__":
    main()
