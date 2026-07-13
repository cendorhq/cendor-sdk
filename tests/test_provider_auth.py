"""Provider-auth hardening (PLAN-SDK-KEYS-PHASE2 N1–N3) — respx-mocked, no network.

N1: a live 401 while the keyless *placeholder* is in play becomes a :class:`MissingAPIKeyError`
naming the env var to set — and never fires with a real key, on non-auth errors, or on keyless
success. N2/N3: Bedrock/Ollama reject ``api_key=`` with a clear error and map ``base_url=`` to the
right client kwarg (``endpoint_url`` / ``host``) — asserted against fake, provider-shaped clients.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx
import pytest
import respx

from cendor.sdk import Agent, MissingAPIKeyError, run
from cendor.sdk import providers as P

_ALL_KEY_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "HF_TOKEN",
    "HUGGINGFACEHUB_API_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "AZURE_INFERENCE_CREDENTIAL",
    "AZURE_AI_API_KEY",
)


@pytest.fixture
def no_keys(monkeypatch):
    """A clean env with every provider key unset (so the placeholder path is exercised)."""
    for var in _ALL_KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def _error_body(kind: str = "invalid_api_key") -> dict:
    return {"error": {"message": "bad key", "type": kind, "code": kind}}


# --------------------------------------------------------------------------- N1: placeholder hint


def test_placeholder_401_raises_hint(build, no_keys):
    agent = Agent(name="a", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=httpx.Response(401, json=_error_body()))
        with pytest.raises(MissingAPIKeyError) as ei:
            run(agent, "hi")
    msg = str(ei.value)
    assert "OPENAI_API_KEY" in msg
    assert "api_key=" in msg
    assert "https://cendor.ai/docs/sdk/providers#api-keys--credentials" in msg
    # original provider error is chained (not swallowed)
    assert ei.value.__cause__ is not None


async def test_placeholder_401_raises_hint_async(build, no_keys):
    agent = Agent(name="a", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=httpx.Response(401, json=_error_body()))
        with pytest.raises(MissingAPIKeyError) as ei:
            await run.aio(agent, "hi")
    assert "OPENAI_API_KEY" in str(ei.value)


def test_anthropic_placeholder_401_hint(build, no_keys):
    agent = Agent(name="a", model="claude-sonnet-5")
    with respx.mock:
        respx.post(build.ANTHROPIC_URL).mock(
            return_value=httpx.Response(
                401,
                json={"type": "error", "error": {"type": "authentication_error", "message": "x"}},
            )
        )
        with pytest.raises(MissingAPIKeyError) as ei:
            run(agent, "hi")
    assert "ANTHROPIC_API_KEY" in str(ei.value)


def test_real_key_is_not_converted(build, monkeypatch):
    """With a real key set the placeholder never engages, so the provider's own error surfaces."""
    import openai

    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-looking-key")
    agent = Agent(name="a", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=httpx.Response(401, json=_error_body()))
        with pytest.raises(openai.AuthenticationError):
            run(agent, "hi")


def test_explicit_api_key_is_not_converted(build, no_keys):
    """An explicit (wrong) key is a real key — no placeholder, so no hint."""
    import openai

    agent = Agent(name="a", model="gpt-4o", api_key="sk-explicit-but-wrong")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=httpx.Response(401, json=_error_body()))
        with pytest.raises(openai.AuthenticationError):
            run(agent, "hi")


def test_non_auth_error_not_converted(build, no_keys):
    """A non-auth failure (400) is re-raised as the provider's own error, placeholder or not."""
    import openai

    agent = Agent(name="a", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            return_value=httpx.Response(400, json=_error_body("invalid_request_error"))
        )
        with pytest.raises(openai.BadRequestError):
            run(agent, "hi")


def test_keyless_success_still_works(build, no_keys):
    """Keyless offline flow: a mocked 200 completes without any hint (placeholder still works)."""
    agent = Agent(name="a", model="gpt-4o")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("ok")))
        result = run(agent, "hi")
    assert result.output == "ok"


# --------------------------------------------------------------------------- placeholder detection


def test_uses_placeholder_matrix(no_keys):
    assert P.OpenAIChatProvider()._uses_placeholder({}) is True
    assert P.AnthropicProvider()._uses_placeholder({}) is True
    assert P.AzureFoundryProvider()._uses_placeholder({}) is True
    assert P.HuggingFaceProvider()._uses_placeholder({}) is True
    # explicit key ⇒ not a placeholder
    assert P.OpenAIChatProvider()._uses_placeholder({"api_key": "k"}) is False
    # keyless providers never use the (auth) placeholder ⇒ never hint
    assert P.BedrockProvider()._uses_placeholder({}) is False
    assert P.OllamaProvider()._uses_placeholder({}) is False
    assert P.GeminiProvider()._uses_placeholder({}) is False
    assert P.FoundryLocalProvider()._uses_placeholder({}) is False


def test_env_key_suppresses_placeholder(monkeypatch, no_keys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert P.OpenAIChatProvider()._uses_placeholder({}) is False
    monkeypatch.setenv("HUGGINGFACEHUB_API_TOKEN", "hf-x")  # HF reads either var
    assert P.HuggingFaceProvider()._uses_placeholder({}) is False


# --------------------------------------------------------------------------- N2: Bedrock


def test_bedrock_api_key_rejected():
    with pytest.raises(ValueError, match="AWS credential chain"):
        P.BedrockProvider()._raw_client(False, {"api_key": "k"})


def test_bedrock_base_url_maps_to_endpoint_url(monkeypatch):
    calls: dict = {}

    def fake_client(service, **kwargs):
        calls["service"] = service
        calls["kwargs"] = kwargs
        return SimpleNamespace(converse=lambda **_: None)

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=fake_client))
    P.BedrockProvider()._raw_client(False, {"base_url": "http://gw:4000"})
    assert calls["service"] == "bedrock-runtime"
    assert calls["kwargs"] == {"endpoint_url": "http://gw:4000"}


# --------------------------------------------------------------------------- N3: Ollama


def test_ollama_api_key_rejected():
    with pytest.raises(ValueError, match="local and needs no API key"):
        P.OllamaProvider()._raw_client(False, {"api_key": "k"})


def test_ollama_base_url_maps_to_host(monkeypatch):
    seen: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["sync"] = kwargs

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            seen["async"] = kwargs

    monkeypatch.setitem(
        sys.modules, "ollama", SimpleNamespace(Client=FakeClient, AsyncClient=FakeAsyncClient)
    )
    P.OllamaProvider()._raw_client(False, {"base_url": "http://host:11434"})
    assert seen["sync"] == {"host": "http://host:11434"}
    P.OllamaProvider()._raw_client(True, {"base_url": "http://host:11434"})
    assert seen["async"] == {"host": "http://host:11434"}
