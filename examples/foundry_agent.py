"""Governed agents on **Microsoft Foundry** (cloud) and **Foundry Local** (on-device), OFFLINE.

Microsoft Foundry was formerly called Azure AI Foundry. Microsoft's guidance is to reach Foundry
deployments with the **standard ``openai`` SDK** (not the legacy ``AzureOpenAI`` client) pointed at
the Foundry ``/openai/v1/`` endpoint — ``azure-ai-inference`` (the Azure AI Inference beta SDK,
the ``/models`` route) is deprecated and retires on 26 August 2026, per Microsoft's Foundry Models
endpoints page. cendor-sdk wraps that as ``provider="azure"`` — so the governed loop, budgets,
audit, and redaction all ride the same seams as any other provider.

Everything here is real except the model call, which is a tiny OpenAI-shaped stub so the example
runs with no network and no keys. In production you drop ``client=`` and configure the endpoint:

    # Microsoft Foundry (cloud) — Chat Completions; `model` is your DEPLOYMENT name:
    agent = Agent(name="foundry", model="my-gpt4o-deployment", provider="azure",
                  base_url="https://my-resource.openai.azure.com",   # or AZURE_OPENAI_ENDPOINT
                  api_key="<resource-key>")                          # or AZURE_OPENAI_API_KEY

    # ...OpenAI-family deployment via the Responses API instead:
    agent = Agent(..., provider="azure_responses")

    # Microsoft Entra ID (keyless) — pass a bearer-token provider, or bring your own client:
    from openai import OpenAI
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    token = get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default")
    client = OpenAI(base_url="https://my-resource.openai.azure.com/openai/v1/", api_key=token)
    agent = Agent(name="foundry", model="my-gpt4o-deployment", provider="azure", client=client)

    # Foundry Local (on-device) — OpenAI-compatible local server; `model` is the resolved model id:
    from foundry_local import FoundryLocalManager
    mgr = FoundryLocalManager("qwen2.5-0.5b")               # starts the service, loads the model
    agent = Agent(name="local", model=mgr.get_model_info("qwen2.5-0.5b").id,
                  provider="foundry_local", base_url=mgr.endpoint, api_key=mgr.api_key)

Run it:  uv run python examples/foundry_agent.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, AuditLog, Policy, budget, guard, run, tool, verify


@tool
def get_release_notes(version: str) -> str:
    """Look up the highlights for a release version."""
    return f"{version}: faster cold starts, governed Foundry providers."


def _stub_openai_client() -> object:
    """An OpenAI-shaped stub (Azure Foundry & Foundry Local speak it): tool call, then answer."""
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
                                        name="get_release_notes",
                                        arguments='{"version": "1.1.0"}',
                                    ),
                                )
                            ],
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=61, completion_tokens=14),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content="1.1.0 adds governed Foundry providers.", tool_calls=None
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=95, completion_tokens=11),
            ),
        ]
    )

    class Completions:
        def create(self, **kwargs: object) -> object:
            return next(turns)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def main() -> None:
    audit_path = Path(tempfile.gettempdir()) / "cendor_sdk_foundry_audit.jsonl"

    # provider="azure" is the OpenAI provider with Foundry-aware construction. Offline here via a
    # stub client; in production pass base_url + api_key (see the docstring) and drop client=.
    agent = Agent(
        name="foundry",
        model="my-gpt4o-deployment",  # your Foundry DEPLOYMENT name (not the model name)
        provider="azure",
        tools=[get_release_notes],
        instructions="Answer using tools when helpful.",
        client=_stub_openai_client(),  # offline stub — omit in production
    )

    log = AuditLog(system="release-bot", risk_tier="limited", path=str(audit_path))
    with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
        result = run(agent, "What's new in 1.1.0?", audit=log)
    log.detach()

    print("provider  :", agent.provider_impl.name)
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
