"""Provider response normalization — the SDK's 30% (plan §3). Unit tests over dict/namespace
fixtures for every provider shape, so response-shape drift is caught here in isolation."""

from __future__ import annotations

from cendor.sdk.providers import (
    AnthropicProvider,
    BedrockProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIChatProvider,
    OpenAIResponsesProvider,
    ToolInvocation,
    _canonical_to_anthropic,
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


def test_infer_and_resolve_provider():
    assert infer_provider("gpt-4o") == "openai"
    assert infer_provider("claude-opus-4-8") == "anthropic"
    assert infer_provider("gemini-2.0-flash") == "google"
    assert resolve_provider("gpt-4o").name == "openai"
    assert resolve_provider("anything", provider="anthropic").name == "anthropic"
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
