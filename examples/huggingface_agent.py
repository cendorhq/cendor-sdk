"""A governed agent on **Hugging Face** Inference, running OFFLINE.

cendor-sdk wraps ``huggingface_hub.InferenceClient.chat_completion`` (an OpenAI-shaped response),
and cendor-core detects it structurally so the ``LLMCall`` is attributed to ``huggingface`` and the
budget / audit / redaction seams apply. Everything here is real except the model call, served by a
tiny stub client exposing ``chat_completion`` so the example needs no network and no token.

In production you drop ``client=`` and set a token (``HF_TOKEN``) or pass ``api_key=``:

    agent = Agent(name="hf", model="meta-llama/Llama-3.1-8B-Instruct", provider="huggingface")

    # A dedicated Inference Endpoint (or third-party provider via the HF_PROVIDER env var):
    agent = Agent(name="hf", model="tgi", provider="huggingface",
                  base_url="https://<your-endpoint>.endpoints.huggingface.cloud")

Run it:  uv run python examples/huggingface_agent.py
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


def _stub_hf_client() -> object:
    """A stub Hugging Face InferenceClient: chat_completion returns an OpenAI-shaped output.

    cendor-core recognizes ``chat_completion`` and attributes the call to ``huggingface`` (not
    ``openai``). First turn asks for the tool; second turn answers.
    """
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
                                        name="get_weather", arguments={"city": "Paris"}
                                    ),
                                )
                            ],
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=57, completion_tokens=13),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="It's sunny in Paris.", tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=90, completion_tokens=9),
            ),
        ]
    )

    class _StubHF:
        def chat_completion(self, **kwargs: object) -> object:
            return next(turns)

    return instrument(_StubHF())


def main() -> None:
    audit_path = Path(tempfile.gettempdir()) / "cendor_sdk_hf_audit.jsonl"
    agent = Agent(
        name="hf",
        model="meta-llama/Llama-3.1-8B-Instruct",
        provider="huggingface",  # required — Hub ids aren't prefix-inferable
        tools=[get_weather],
        instructions="Answer using tools when helpful.",
        client=_stub_hf_client(),  # offline stub — omit in production
    )

    log = AuditLog(system="support", risk_tier="limited", path=str(audit_path))
    with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
        result = run(agent, "What's the weather in Paris?", audit=log)
    log.detach()

    print("provider  :", agent.provider_impl.name)
    print("output    :", result.output)
    print("cost      :", result.cost)  # None for HF ids (not in the price table) — usage is real
    print("usage     :", result.usage)
    print("tool steps:", [s.name for s in result.tool_steps])
    print("llm steps :", [s.call.provider for s in result.llm_steps])  # -> ['huggingface', ...]
    print("trace_id  :", result.trace_id)

    ok, detail = verify(str(audit_path))
    print("audit ok  :", ok, "—", detail)


if __name__ == "__main__":
    main()
