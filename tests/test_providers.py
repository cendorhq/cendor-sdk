"""Provider response normalization — the SDK's 30% (plan §3). Unit tests over dict/namespace
fixtures for every provider shape, so response-shape drift is caught here in isolation."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from cendor.sdk.providers import (
    AnthropicProvider,
    AzureFoundryProvider,
    AzureFoundryResponsesProvider,
    BedrockProvider,
    FoundryLocalProvider,
    GeminiProvider,
    HuggingFaceProvider,
    OllamaProvider,
    OpenAIChatProvider,
    OpenAIResponsesProvider,
    ToolInvocation,
    _azure_foundry_base_url,
    _canonical_to_anthropic,
    _canonical_to_bedrock,
    _canonical_to_gemini,
    _foundry_local_base_url,
    assistant_message,
    get_provider,
    infer_provider,
    resolve_provider,
    tool_result_message,
)
from cendor.sdk.tools import tool


def test_openai_chat_normalization_text():
    p = OpenAIChatProvider()
    resp = {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Hi"}}],
    }
    parsed = p.parse(resp)
    assert parsed.content == "Hi"
    assert parsed.tool_calls == []
    assert parsed.finish_reason == "stop"


def test_openai_chat_normalization_tool_calls():
    p = OpenAIChatProvider()
    resp = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_9",
                            "type": "function",
                            "function": {"name": "search", "arguments": '{"q": "cats"}'},
                        }
                    ],
                },
            }
        ]
    }
    parsed = p.parse(resp)
    assert parsed.content is None
    assert len(parsed.tool_calls) == 1
    tc = parsed.tool_calls[0]
    assert (tc.id, tc.name, tc.arguments) == ("call_9", "search", {"q": "cats"})


def test_openai_responses_normalization():
    p = OpenAIResponsesProvider()
    resp = {
        "status": "completed",
        "output_text": "The answer.",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "The answer."}]},
            {"type": "function_call", "call_id": "fc_1", "name": "lookup", "arguments": '{"x": 1}'},
        ],
    }
    parsed = p.parse(resp)
    assert parsed.content == "The answer."
    assert parsed.tool_calls[0].name == "lookup"
    assert parsed.tool_calls[0].arguments == {"x": 1}


def test_anthropic_normalization():
    p = AnthropicProvider()
    resp = {
        "content": [
            {"type": "text", "text": "Let me check. "},
            {"type": "tool_use", "id": "toolu_2", "name": "weather", "input": {"city": "Paris"}},
        ],
        "stop_reason": "tool_use",
    }
    parsed = p.parse(resp)
    assert parsed.content == "Let me check. "
    assert parsed.tool_calls[0].id == "toolu_2"
    assert parsed.tool_calls[0].arguments == {"city": "Paris"}
    assert parsed.finish_reason == "tool_use"


def test_gemini_normalization():
    p = GeminiProvider()
    resp = {
        "candidates": [
            {
                "finish_reason": "STOP",
                "content": {
                    "parts": [
                        {"text": "Sunny"},
                        {"function_call": {"name": "weather", "args": {"city": "Paris"}}},
                    ]
                },
            }
        ]
    }
    parsed = p.parse(resp)
    assert "Sunny" in (parsed.content or "")
    assert parsed.tool_calls[0].name == "weather"
    assert parsed.tool_calls[0].arguments == {"city": "Paris"}


def test_bedrock_normalization():
    p = BedrockProvider()
    resp = {
        "output": {
            "message": {
                "content": [
                    {"text": "Sunny."},
                    {
                        "toolUse": {
                            "toolUseId": "tu_1",
                            "name": "weather",
                            "input": {"city": "Paris"},
                        }
                    },
                ]
            }
        },
        "stopReason": "tool_use",
    }
    parsed = p.parse(resp)
    assert parsed.content == "Sunny."
    assert parsed.tool_calls[0].id == "tu_1"
    assert parsed.finish_reason == "tool_use"


def test_ollama_normalization():
    p = OllamaProvider()
    resp = {
        "message": {
            "role": "assistant",
            "content": "Hi",
            "tool_calls": [{"function": {"name": "weather", "arguments": {"city": "Paris"}}}],
        },
        "done": True,
        "done_reason": "stop",
    }
    parsed = p.parse(resp)
    assert parsed.content == "Hi"
    assert parsed.tool_calls[0].name == "weather"
    assert parsed.finish_reason == "stop"


def test_huggingface_normalization_reuses_openai_shape():
    """HF chat_completion returns the OpenAI Chat shape; the HF provider parses it identically."""
    p = HuggingFaceProvider()
    resp = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Hi from HF",
                    "tool_calls": [
                        {
                            "id": "call_hf",
                            "type": "function",
                            "function": {"name": "weather", "arguments": {"city": "Paris"}},
                        }
                    ],
                },
            }
        ]
    }
    parsed = p.parse(resp)
    assert parsed.content == "Hi from HF"
    assert parsed.finish_reason == "stop"
    assert parsed.tool_calls[0].name == "weather"
    # arguments arrive as a dict from HF; _loads_args passes dicts through unchanged.
    assert parsed.tool_calls[0].arguments == {"city": "Paris"}


def test_core_attributes_huggingface_client():
    """cendor-core detects InferenceClient.chat_completion and attributes the LLMCall to HF."""
    from cendor.core import bus
    from cendor.core.instrument import instrument
    from cendor.core.types import LLMCall

    class _FakeHFClient:
        def chat_completion(self, *, model, messages, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="Hi", tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
            )

    events: list = []

    def on_event(ev):
        if isinstance(ev, LLMCall):
            events.append(ev)

    bus.subscribe(on_event)
    try:
        client = instrument(_FakeHFClient())
        client.chat_completion(
            model="meta-llama/Llama-3.1-8B-Instruct",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        bus.unsubscribe(on_event)

    assert events, "no LLMCall was emitted for the HF client"
    call = events[-1]
    assert call.provider == "huggingface"
    assert call.model == "meta-llama/Llama-3.1-8B-Instruct"
    assert call.usage is not None
    assert call.usage.input_tokens == 11
    assert call.usage.output_tokens == 3


def test_azure_foundry_base_url_normalization():
    # Bare Azure OpenAI host → /openai/v1/ appended.
    assert (
        _azure_foundry_base_url({"base_url": "https://myres.openai.azure.com"})
        == "https://myres.openai.azure.com/openai/v1/"
    )
    # Bare Foundry Models (services) host → /openai/v1/ appended.
    assert (
        _azure_foundry_base_url({"base_url": "https://myres.services.ai.azure.com"})
        == "https://myres.services.ai.azure.com/openai/v1/"
    )
    # Already a v1 endpoint → preserved (normalized trailing slash).
    assert (
        _azure_foundry_base_url({"base_url": "https://myres.openai.azure.com/openai/v1/"})
        == "https://myres.openai.azure.com/openai/v1/"
    )
    # Legacy azure-ai-inference /models route → preserved.
    assert (
        _azure_foundry_base_url({"base_url": "https://myres.services.ai.azure.com/models"})
        == "https://myres.services.ai.azure.com/models/"
    )


def test_azure_foundry_base_url_from_env(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://envres.openai.azure.com")
    assert _azure_foundry_base_url({}) == "https://envres.openai.azure.com/openai/v1/"
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    assert _azure_foundry_base_url({}) is None


def test_foundry_local_base_url_normalization():
    # Bare local host → /v1/ appended.
    assert _foundry_local_base_url({"base_url": "http://localhost:5273"}) == (
        "http://localhost:5273/v1/"
    )
    # Endpoint already ending in /v1 (e.g. FoundryLocalManager.endpoint) → preserved.
    assert _foundry_local_base_url({"base_url": "http://localhost:5273/v1"}) == (
        "http://localhost:5273/v1/"
    )


def test_foundry_local_requires_endpoint(monkeypatch):
    monkeypatch.delenv("FOUNDRY_LOCAL_ENDPOINT", raising=False)
    assert _foundry_local_base_url({}) is None
    # No endpoint anywhere → a clear, actionable error rather than silently hitting api.openai.com.
    try:
        FoundryLocalProvider()._raw_client(False, {})
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Foundry Local needs an endpoint" in str(e)


def test_azure_responses_reuses_responses_parse():
    """provider='azure_responses' drives the Responses API; parse mirrors the OpenAI one."""
    p = AzureFoundryResponsesProvider()
    resp = {
        "status": "completed",
        "output_text": "Done.",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Done."}]},
            {"type": "function_call", "call_id": "fc_9", "name": "lookup", "arguments": '{"a": 2}'},
        ],
    }
    parsed = p.parse(resp)
    assert parsed.content == "Done."
    assert parsed.tool_calls[0].name == "lookup"
    assert parsed.tool_calls[0].arguments == {"a": 2}


def test_new_provider_registry_and_aliases():
    assert isinstance(get_provider("huggingface"), HuggingFaceProvider)
    assert isinstance(get_provider("hf"), HuggingFaceProvider)
    assert isinstance(get_provider("azure"), AzureFoundryProvider)
    assert isinstance(get_provider("azure_openai"), AzureFoundryProvider)
    assert isinstance(get_provider("foundry"), AzureFoundryProvider)
    assert isinstance(get_provider("azure_responses"), AzureFoundryResponsesProvider)
    assert isinstance(get_provider("foundry_responses"), AzureFoundryResponsesProvider)
    assert isinstance(get_provider("foundry_local"), FoundryLocalProvider)
    assert isinstance(get_provider("foundry-local"), FoundryLocalProvider)
    # Chat-shape providers subclass the OpenAI Chat provider; the Responses variant subclasses the
    # OpenAI Responses provider — distinct provider names, reused request/response shapes.
    assert isinstance(get_provider("huggingface"), OpenAIChatProvider)
    assert isinstance(get_provider("foundry_local"), OpenAIChatProvider)
    assert isinstance(get_provider("azure_responses"), OpenAIResponsesProvider)
    assert get_provider("huggingface").name == "huggingface"
    assert get_provider("azure").name == "azure"
    assert get_provider("foundry_local").name == "foundry_local"
    assert get_provider("azure_responses").name == "azure_responses"
    # HF binds to chat_completion; Azure/Foundry-Local keep the OpenAI chat.completions.create path;
    # the Responses variant keeps responses.create.
    assert HuggingFaceProvider._create_path == ("chat_completion",)
    assert AzureFoundryProvider._create_path == ("chat", "completions", "create")
    assert FoundryLocalProvider._create_path == ("chat", "completions", "create")
    assert AzureFoundryResponsesProvider._create_path == ("responses", "create")


def test_huggingface_and_azure_tool_formatting():
    @tool
    def search(query: str) -> list[str]:
        """Search the KB."""
        return []

    # Both reuse the OpenAI function-tool shape.
    assert get_provider("huggingface").format_tools([search])[0]["type"] == "function"
    assert get_provider("azure").format_tools([search])[0]["function"]["name"] == "search"


def test_openai_temperature_guarded_for_o_series():
    """o-series reasoning models reject `temperature` — the OpenAI provider must omit it."""
    p = OpenAIChatProvider()
    assert "temperature" in p.build_kwargs("gpt-4o", [], [], "", temperature=0.5)
    assert "temperature" not in p.build_kwargs("o3-mini", [], [], "", temperature=0.5)
    assert "temperature" not in p.build_kwargs("o1", [], [], "", temperature=0.5)


def test_openai_output_schema_uses_native_json_schema():
    p = OpenAIChatProvider()
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    k = p.build_kwargs("gpt-4o", [], [], "", json_mode=True, output_schema=schema)
    assert k["response_format"]["type"] == "json_schema"
    assert k["response_format"]["json_schema"]["schema"] == schema
    # No schema → fall back to json_object.
    k2 = p.build_kwargs("gpt-4o", [], [], "", json_mode=True)
    assert k2["response_format"] == {"type": "json_object"}


def test_ollama_and_gemini_native_structured_output():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    ok = OllamaProvider().build_kwargs("llama3.1", [], [], "", json_mode=True, output_schema=schema)
    assert ok["format"] == schema  # Ollama takes the schema as its format constraint
    gk = GeminiProvider().build_kwargs(
        "gemini-2.0-flash", [], [], "", json_mode=True, output_schema=schema
    )
    assert gk["config"]["response_mime_type"] == "application/json"
    assert gk["config"]["response_schema"] == schema


@dataclass
class _Address:  # module-level so annotations resolve under `from __future__ import annotations`
    city: str
    postcode: str


def test_tool_schema_expands_nested_dataclass():
    """A tool taking a dataclass/pydantic param expands into a proper object schema (not bare)."""

    @tool
    def register(name: str, address: _Address) -> str:
        """Register a user."""
        return "ok"

    addr = register.parameters["properties"]["address"]
    assert addr["type"] == "object"
    assert set(addr["properties"]) == {"city", "postcode"}
    assert addr["required"] == ["city", "postcode"]


def test_register_model_price_enables_costing():
    """A registered price makes unpriced ids (HF / Azure / Foundry-Local / custom) cost > $0."""
    from decimal import Decimal

    from cendor.core import prices

    from cendor.sdk.pricing import register_model_price

    register_model_price("cendor-test-deployment-xyz", input=2.50, output=10.00)  # USD / 1M tokens
    cost = prices.estimate("cendor-test-deployment-xyz", 1_000_000, 1_000_000)
    assert cost.amount == Decimal("12.5")  # 2.50 input + 10.00 output


def test_infer_and_resolve_provider():
    assert infer_provider("gpt-4o") == "openai"
    assert infer_provider("claude-opus-4-8") == "anthropic"
    assert infer_provider("gemini-2.0-flash") == "google"
    assert resolve_provider("gpt-4o").name == "openai"
    assert resolve_provider("anything", provider="anthropic").name == "anthropic"
    # HF ids and Foundry deployment names aren't prefix-inferable — they require an explicit
    # provider= (deployment names are arbitrary; Hub ids collide with nothing safe to guess).
    assert resolve_provider("meta-llama/Llama-3.1-8B-Instruct", provider="huggingface").name == (
        "huggingface"
    )
    assert resolve_provider("my-gpt4o-deployment", provider="azure").name == "azure"
    try:
        infer_provider("totally-unknown-model")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_canonical_to_anthropic_tool_round_trip():
    """A tool call + result in canonical (OpenAI) shape becomes Anthropic tool_use/tool_result."""
    messages = [
        {"role": "user", "content": "weather?"},
        assistant_message(
            None, [ToolInvocation(id="tu_1", name="weather", arguments={"city": "Paris"})]
        ),
        tool_result_message("tu_1", "weather", "Sunny in Paris"),
    ]
    wire = _canonical_to_anthropic(messages)
    assert wire[0] == {"role": "user", "content": "weather?"}
    # assistant turn has a tool_use block
    assert wire[1]["role"] == "assistant"
    assert any(b["type"] == "tool_use" and b["name"] == "weather" for b in wire[1]["content"])
    # tool result folded into a following user turn as tool_result
    assert wire[2]["role"] == "user"
    assert wire[2]["content"][0]["type"] == "tool_result"
    assert wire[2]["content"][0]["tool_use_id"] == "tu_1"


def _tool_history() -> list[dict]:
    """A canonical tool conversation: user -> assistant(tool_call) -> tool(result)."""
    return [
        {"role": "user", "content": "weather in Paris?"},
        assistant_message(
            None, [ToolInvocation(id="tc1", name="get_weather", arguments={"city": "Paris"})]
        ),
        tool_result_message("tc1", "get_weather", "Sunny, 24C"),
    ]


def test_canonical_to_gemini_tool_round_trip():
    """Gemini contents must preserve the tool call + result (function_call/function_response)."""
    contents = _canonical_to_gemini(_tool_history())
    assert contents[0] == {"role": "user", "parts": [{"text": "weather in Paris?"}]}
    # assistant tool-call turn -> a model turn carrying a function_call part
    model_turn = contents[1]
    assert model_turn["role"] == "model"
    fc = next(p["function_call"] for p in model_turn["parts"] if "function_call" in p)
    assert fc["name"] == "get_weather"
    assert fc["args"] == {"city": "Paris"}
    # tool result -> a following user turn carrying a function_response part
    resp_turn = contents[2]
    assert resp_turn["role"] == "user"
    fr = next(p["function_response"] for p in resp_turn["parts"] if "function_response" in p)
    assert fr["name"] == "get_weather"
    assert "Sunny" in fr["response"]["result"]


def test_canonical_to_bedrock_tool_round_trip():
    """Bedrock messages must preserve the toolUse (call) and toolResult (result) blocks."""
    wire = _canonical_to_bedrock(_tool_history())
    assert wire[0] == {"role": "user", "content": [{"text": "weather in Paris?"}]}
    assistant = wire[1]
    assert assistant["role"] == "assistant"
    tu = next(b["toolUse"] for b in assistant["content"] if "toolUse" in b)
    assert (tu["toolUseId"], tu["name"], tu["input"]) == ("tc1", "get_weather", {"city": "Paris"})
    result = wire[2]
    assert result["role"] == "user"
    tr = next(b["toolResult"] for b in result["content"] if "toolResult" in b)
    assert tr["toolUseId"] == "tc1"
    assert "Sunny" in tr["content"][0]["text"]


def test_gemini_bedrock_build_kwargs_carry_tool_history():
    """Regression for the P0 bug: build_kwargs must not drop tool calls/results (both providers)."""
    hist = _tool_history()
    gk = GeminiProvider().build_kwargs("gemini-2.0-flash", hist, [], "")
    assert any("function_call" in str(c) for c in gk["contents"])
    assert any("Sunny" in str(c) for c in gk["contents"])
    bk = BedrockProvider().build_kwargs("meta.llama", hist, [], "")
    assert any("toolUse" in str(m) for m in bk["messages"])
    assert any("Sunny" in str(m) for m in bk["messages"])


def test_multimodal_content_translation():
    """A multimodal user turn (text + images) maps to each provider's image blocks."""
    data_url = "data:image/png;base64,QUJD"
    msg = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
            ],
        }
    ]
    # Anthropic: text block + base64 image + url image
    blocks = _canonical_to_anthropic(msg)[0]["content"]
    assert {"type": "text", "text": "what is this?"} in blocks
    assert any(
        b["type"] == "image"
        and b["source"]["type"] == "base64"
        and b["source"]["media_type"] == "image/png"
        and b["source"]["data"] == "QUJD"
        for b in blocks
    )
    assert any(b["type"] == "image" and b["source"].get("type") == "url" for b in blocks)
    # Gemini: text part + inline_data + file_data
    parts = _canonical_to_gemini(msg)[0]["parts"]
    assert {"text": "what is this?"} in parts
    assert any("inline_data" in p and p["inline_data"]["mime_type"] == "image/png" for p in parts)
    assert any("file_data" in p for p in parts)
    # Bedrock: text kept (image bytes out of scope there)
    assert _canonical_to_bedrock(msg)[0]["content"][0]["text"] == "what is this?"


def test_openai_multimodal_passthrough():
    """OpenAI-family passes multimodal content parts through unchanged (native support)."""
    content = [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "https://x"}},
    ]
    k = OpenAIChatProvider().build_kwargs("gpt-4o", [{"role": "user", "content": content}], [], "")
    assert k["messages"][-1]["content"] == content


def test_per_provider_tool_formatting():
    @tool
    def search(query: str, top_k: int = 3) -> list[str]:
        """Search the KB."""
        return []

    assert get_provider("openai").format_tools([search])[0]["type"] == "function"
    assert get_provider("anthropic").format_tools([search])[0]["name"] == "search"
    gemini = get_provider("google").format_tools([search])
    assert gemini[0]["function_declarations"][0]["name"] == "search"
    bedrock = get_provider("bedrock").format_tools([search])
    assert bedrock["tools"][0]["toolSpec"]["name"] == "search"


def test_register_model_price_survives_prices_refresh(monkeypatch):
    # 1.7.0 writes through core's contractual prices._register — a refresh() no longer drops it.
    import contextlib
    import io
    import json
    from decimal import Decimal

    from cendor.core import prices

    from cendor.sdk.pricing import register_model_price

    prices._reset()
    try:
        register_model_price("cendor-refresh-survivor", input=1.00, output=2.00)  # per 1M
        payload = json.dumps(
            {"_updated": "2099-01-01", "models": {"gpt-4o": {"input": 0.001, "output": 0}}}
        )

        @contextlib.contextmanager
        def fake_urlopen(url, timeout=5.0):
            yield io.BytesIO(payload.encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        assert prices.refresh() is True
        cost = prices.estimate("cendor-refresh-survivor", 1_000_000, 0)
        assert cost.amount == Decimal("1")  # the registration survived the table swap
    finally:
        prices._reset()
