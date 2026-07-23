"""Phase S SDK wave: Anthropic incremental streaming + ThinkingDelta (S1/S2), native structured
output (S14), Ollama/Bedrock images (S15), Bedrock async-via-thread (S5). Offline, no network."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, RunComplete, TextDelta, ThinkingDelta, ToolCallEvent, run
from cendor.sdk.providers import (
    AnthropicProvider,
    BedrockProvider,
    _bedrock_content,
    _canonical_to_bedrock,
    _ollama_message,
)

# --- S1/S2: Anthropic streaming ---------------------------------------------------------------


def _anthropic_client(events):
    class Messages:
        def create(self, **kwargs):
            return iter(events)

    return instrument(SimpleNamespace(messages=Messages()))


def _cbd_text(text, index=0):
    return SimpleNamespace(
        type="content_block_delta", index=index, delta=SimpleNamespace(type="text_delta", text=text)
    )


def _cbd_thinking(text, index=0):
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="thinking_delta", thinking=text),
    )


def _cbd_json(partial, index=0):
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="input_json_delta", partial_json=partial),
    )


def test_s1_anthropic_streaming_text_and_usage():
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=0)
            ),
        ),
        SimpleNamespace(
            type="content_block_start", index=0, content_block=SimpleNamespace(type="text")
        ),
        _cbd_text("Hello "),
        _cbd_text("world"),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=5),
        ),
        SimpleNamespace(type="message_stop"),
    ]
    agent = Agent(
        name="a", model="claude-opus-4-8", instructions="x", client=_anthropic_client(events)
    )
    out = list(run.stream(agent, "hi"))
    text = "".join(e.text for e in out if isinstance(e, TextDelta))
    assert text == "Hello world"  # incremental text deltas
    done = out[-1]
    assert isinstance(done, RunComplete)
    assert done.result.output == "Hello world"


def test_s2_anthropic_streaming_thinking_delta():
    events = [
        SimpleNamespace(
            type="message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=10))
        ),
        SimpleNamespace(
            type="content_block_start", index=0, content_block=SimpleNamespace(type="thinking")
        ),
        _cbd_thinking("let me reason "),
        _cbd_thinking("about this"),
        SimpleNamespace(
            type="content_block_start", index=1, content_block=SimpleNamespace(type="text")
        ),
        _cbd_text("answer", index=1),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=5),
        ),
    ]
    agent = Agent(
        name="a", model="claude-opus-4-8", instructions="x", client=_anthropic_client(events)
    )
    out = list(run.stream(agent, "hi"))
    thinking = "".join(e.text for e in out if isinstance(e, ThinkingDelta))
    text = "".join(e.text for e in out if isinstance(e, TextDelta))
    assert thinking == "let me reason about this"  # S2 ThinkingDelta stream
    assert text == "answer"  # thinking not folded into visible content


def test_s1_anthropic_streaming_tool_call_input_json():
    @__import__("cendor.sdk", fromlist=["tool"]).tool
    def get_weather(city: str) -> str:
        """weather"""
        return f"Sunny in {city}"

    turn1 = [
        SimpleNamespace(
            type="message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=10))
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="get_weather"),
        ),
        _cbd_json('{"city":', index=0),
        _cbd_json(' "Paris"}', index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(output_tokens=5),
        ),
    ]
    turn2 = [
        SimpleNamespace(
            type="message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=12))
        ),
        SimpleNamespace(
            type="content_block_start", index=0, content_block=SimpleNamespace(type="text")
        ),
        _cbd_text("It's sunny."),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=3),
        ),
    ]
    turns = iter([iter(turn1), iter(turn2)])

    class Messages:
        def create(self, **kwargs):
            return next(turns)

    client = instrument(SimpleNamespace(messages=Messages()))
    agent = Agent(name="a", model="claude-opus-4-8", tools=[get_weather], client=client)
    out = list(run.stream(agent, "weather?"))
    calls = [e for e in out if isinstance(e, ToolCallEvent)]
    assert calls[0].name == "get_weather"
    assert calls[0].arguments == {"city": "Paris"}  # input_json_delta fragments reassembled
    assert out[-1].result.output == "It's sunny."


# --- S14: native structured output (gated) ----------------------------------------------------


def test_s14_anthropic_native_structured_output_supported_model():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    kwargs = AnthropicProvider().build_kwargs(
        "claude-opus-4-8",
        [{"role": "user", "content": "x"}],
        [],
        "sys",
        json_mode=True,
        output_schema=schema,
    )
    assert "output_config" in kwargs
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False  # normalized
    assert "single JSON object" not in (kwargs.get("system") or "")  # no instruction nudge


def test_s14_anthropic_degrades_on_old_model():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    kwargs = AnthropicProvider().build_kwargs(
        "claude-3-5-sonnet-20240620",
        [{"role": "user", "content": "x"}],
        [],
        "sys",
        json_mode=True,
        output_schema=schema,
    )
    assert "output_config" not in kwargs  # degrades to the instruction path
    assert "JSON object" in kwargs["system"]


# --- S15: images -------------------------------------------------------------------------------


def test_s15_ollama_images():
    data = base64.b64encode(b"\x89PNG fake").decode()
    content = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
    ]
    mapped = _ollama_message({"role": "user", "content": content})
    assert mapped["content"] == "what is this?"
    assert mapped["images"] == [data]  # raw base64, no data: prefix


def test_s15_bedrock_image_blocks():
    raw = b"\x89PNG fake bytes"
    data = base64.b64encode(raw).decode()
    content = [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}},
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/x.png"},
        },  # remote -> dropped
    ]
    blocks = _bedrock_content(content)
    assert blocks[0] == {"text": "describe"}
    assert blocks[1]["image"]["format"] == "jpeg"
    assert blocks[1]["image"]["source"]["bytes"] == raw  # decoded raw bytes
    assert len(blocks) == 2  # remote URL dropped


def test_s15_bedrock_full_message_translation():
    data = base64.b64encode(b"img").decode()
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}
            ],
        }
    ]
    wire = _canonical_to_bedrock(msgs)
    assert wire[0]["role"] == "user"
    assert wire[0]["content"][0]["image"]["format"] == "png"


# --- S5: Bedrock async via thread --------------------------------------------------------------


def test_s5_bedrock_flagged_async_via_thread():
    assert BedrockProvider()._async_via_thread is True
