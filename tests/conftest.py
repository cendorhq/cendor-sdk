"""Shared test fixtures and provider-response builders.

Discipline (plan §10): **no network, ever.** ``respx`` mocks provider HTTP so the *real* openai /
anthropic SDKs parse real response objects — the same pattern as ``plan/scratch/``. Provider
versions are pinned by the lockfile.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

CHAT_URL = "https://api.openai.com/v1/chat/completions"
RESPONSES_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


# --------------------------------------------------------------------------- response builders


def openai_chat(content, *, prompt=50, completion=10, finish="stop", tool_calls=None, reasoning=0):
    """A Chat Completions JSON payload (parsed by the real openai SDK)."""
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage: dict = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if reasoning:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": usage,
    }


def openai_tool_call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def anthropic_message(text=None, *, input_t=50, output_t=10, stop="end_turn", tool_use=None):
    """An Anthropic Messages JSON payload (parsed by the real anthropic SDK)."""
    content: list = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool_use:
        content.append(
            {
                "type": "tool_use",
                "id": tool_use.get("id", "toolu_1"),
                "name": tool_use["name"],
                "input": tool_use["input"],
            }
        )
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-8",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": input_t, "output_tokens": output_t},
    }


def resp(payload):
    """Wrap a JSON payload in a 200 httpx.Response."""
    return httpx.Response(200, json=payload)


@pytest.fixture
def build():
    """A namespace of response builders + endpoint URLs for tests."""
    return SimpleNamespace(
        openai_chat=openai_chat,
        openai_tool_call=openai_tool_call,
        anthropic_message=anthropic_message,
        resp=resp,
        CHAT_URL=CHAT_URL,
        RESPONSES_URL=RESPONSES_URL,
        ANTHROPIC_URL=ANTHROPIC_URL,
    )


@pytest.fixture(autouse=True)
def _isolate():
    """Reset shared global state between tests: tokenguard records/budgets + the client cache."""
    import cendor.tokenguard as tg

    from cendor.sdk import providers

    tg.reset()
    providers._client_cache.clear()
    providers._placeholder_hints.clear()
    yield
    tg.reset()
    providers._client_cache.clear()
    providers._placeholder_hints.clear()
