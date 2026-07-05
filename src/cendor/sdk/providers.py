"""Provider abstraction: client construction, outbound formatting, inbound normalization.

``cendor-core`` normalizes *usage/cost*; the SDK adds what core deliberately leaves out — parsing
each provider's response *shape* into assistant **content + tool calls + finish reason**, and
formatting the outbound request (messages, tools, system) per provider. The conversation is held in
one **canonical (OpenAI-shape)** format so a run can hand off between providers without rewriting
history; each provider translates canonical → its wire format at call time.

Normalization is implemented for OpenAI (Chat Completions + Responses), Anthropic, Gemini, Bedrock,
and Ollama. Client construction ships for OpenAI + Anthropic (Phase 1); the others construct behind
lazy imports via their extras.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cendor.core import instrument

if TYPE_CHECKING:
    from .tools import Tool


# --------------------------------------------------------------------------- normalized response


@dataclass
class ToolInvocation:
    """A tool call the model asked for: an id, the tool name, and parsed arguments."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedResponse:
    """The SDK-normalized view of one model response, provider-agnostic."""

    content: str | None
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    finish_reason: str | None = None
    raw: Any = None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Duck-typed attribute/key access — works on SDK objects and dict/namespace recordings."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _first(seq: Any) -> Any:
    if not seq:
        return None
    try:
        return seq[0]
    except (TypeError, KeyError, IndexError):
        return None


def _loads_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {}
    return {}


# --------------------------------------------------------------------------- canonical messages
#
# The runner keeps history in this shape (OpenAI Chat Completions). The system prompt is NOT stored
# here — it is passed to build_kwargs as ``instructions`` so provider switches stay clean.


def assistant_message(content: str | None, tool_calls: list[ToolInvocation]) -> dict:
    """Canonical assistant turn (optionally with tool calls)."""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in tool_calls
        ]
    return msg


def tool_result_message(tool_call_id: str, name: str, content: str) -> dict:
    """Canonical tool-result turn."""
    return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}


def _stringify(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, default=str)


# --------------------------------------------------------------------------- providers

_client_cache: dict[tuple, Any] = {}


def _ensure_async_detectable(client: Any, path: tuple[str, ...]) -> None:
    """Make an async ``create`` method visible to ``inspect.iscoroutinefunction``.

    The openai/anthropic v2 SDKs expose ``create`` as a *method wrapper* for which
    ``iscoroutinefunction`` returns ``False`` — so ``cendor-core``'s ``instrument`` would treat an
    async client as sync and capture no usage. Wrapping the terminal method in a genuine ``async
    def`` (before instrumenting) restores detection, so usage/cost are captured on async runs too.
    """
    if not path:
        return
    owner: Any = client
    for attr in path[:-1]:
        owner = getattr(owner, attr, None)
        if owner is None:
            return
    name = path[-1]
    orig = getattr(owner, name, None)
    if orig is None or getattr(orig, "_cendor_wrapped", False):
        return
    if inspect.iscoroutinefunction(orig):
        return

    @functools.wraps(orig)
    async def _acreate(*args: Any, **kwargs: Any) -> Any:
        return await orig(*args, **kwargs)

    try:
        setattr(owner, name, _acreate)
    except (AttributeError, TypeError):  # pragma: no cover - client forbids setattr
        pass


class Provider:
    """Base provider: construct/instrument a client, format the request, normalize the response."""

    name: str = ""
    #: dotted path from the client to the create method, e.g. ("chat", "completions", "create").
    _create_path: tuple[str, ...] = ()

    # --- client construction --------------------------------------------------------------------

    def _raw_client(self, async_: bool, config: dict) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError(f"client construction not available for provider {self.name!r}")

    def adopt(self, client: Any, async_: bool) -> Any:
        """Prepare a client for the loop: ensure async detection, then instrument (idempotent)."""
        if async_:
            _ensure_async_detectable(client, self._create_path)
        return instrument(client)

    def client(self, async_: bool = False, config: dict | None = None) -> Any:
        """A cached, adopted (async-detectable + instrumented) client for this provider."""
        config = config or {}
        key = (self.name, async_, config.get("api_key"), config.get("base_url"))
        client = _client_cache.get(key)
        if client is None:
            client = self.adopt(self._raw_client(async_, config), async_)
            _client_cache[key] = client
        return client

    def create_method(self, client: Any) -> Callable[..., Any]:
        """The instrumented create method on ``client`` for this provider."""
        target: Any = client
        for attr in self._create_path:
            target = getattr(target, attr)
        return target

    # --- outbound -------------------------------------------------------------------------------

    def format_tools(self, tools: list[Tool]) -> Any:
        raise NotImplementedError

    def build_kwargs(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool],
        instructions: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        raise NotImplementedError

    # --- inbound --------------------------------------------------------------------------------

    def parse(self, response: Any) -> ParsedResponse:
        raise NotImplementedError

    @staticmethod
    def wants_more_tools(parsed: ParsedResponse) -> bool:
        return bool(parsed.tool_calls)


class OpenAIChatProvider(Provider):
    """OpenAI Chat Completions."""

    name = "openai"
    _create_path = ("chat", "completions", "create")

    def _raw_client(self, async_: bool, config: dict) -> Any:
        import openai

        api_key = (
            config.get("api_key") or os.environ.get("OPENAI_API_KEY") or "sk-cendor-sdk-placeholder"
        )
        kwargs: dict[str, Any] = {"api_key": api_key}
        if config.get("base_url"):
            kwargs["base_url"] = config["base_url"]
        cls = openai.AsyncOpenAI if async_ else openai.OpenAI
        return cls(**kwargs)

    def format_tools(self, tools: list[Tool]) -> Any:
        return [t.to_openai() for t in tools] or None

    def build_kwargs(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool],
        instructions: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        wire: list[dict] = []
        sys = instructions
        if json_mode:
            sys = (sys + "\n\nRespond with a single JSON object.").strip()
        if sys:
            wire.append({"role": "system", "content": sys})
        wire.extend(messages)
        kwargs: dict[str, Any] = {"model": model, "messages": wire}
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return kwargs

    def parse(self, response: Any) -> ParsedResponse:
        choice = _first(_get(response, "choices"))
        message = _get(choice, "message")
        content = _get(message, "content")
        tool_calls: list[ToolInvocation] = []
        for tc in _get(message, "tool_calls") or []:
            fn = _get(tc, "function")
            tool_calls.append(
                ToolInvocation(
                    id=_get(tc, "id") or f"call_{uuid.uuid4().hex[:8]}",
                    name=_get(fn, "name") or "",
                    arguments=_loads_args(_get(fn, "arguments")),
                )
            )
        return ParsedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=_get(choice, "finish_reason"),
            raw=response,
        )


class OpenAIResponsesProvider(Provider):
    """OpenAI Responses API (input=/output=)."""

    name = "openai_responses"
    _create_path = ("responses", "create")

    def _raw_client(self, async_: bool, config: dict) -> Any:
        return OpenAIChatProvider()._raw_client(async_, config)

    def format_tools(self, tools: list[Tool]) -> Any:
        # Responses API uses a flattened function-tool shape.
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ] or None

    def build_kwargs(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool],
        instructions: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        kwargs: dict[str, Any] = {"model": model, "input": list(messages)}
        if instructions:
            kwargs["instructions"] = instructions
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        return kwargs

    def parse(self, response: Any) -> ParsedResponse:
        content = _get(response, "output_text")
        tool_calls: list[ToolInvocation] = []
        text_parts: list[str] = []
        for item in _get(response, "output") or []:
            itype = _get(item, "type")
            if itype == "function_call":
                tool_calls.append(
                    ToolInvocation(
                        id=(
                            _get(item, "call_id")
                            or _get(item, "id")
                            or f"call_{uuid.uuid4().hex[:8]}"
                        ),
                        name=_get(item, "name") or "",
                        arguments=_loads_args(_get(item, "arguments")),
                    )
                )
            elif itype == "message":
                for part in _get(item, "content") or []:
                    if _get(part, "type") in ("output_text", "text"):
                        text_parts.append(str(_get(part, "text") or ""))
        if content is None and text_parts:
            content = "".join(text_parts)
        return ParsedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=_get(response, "status"),
            raw=response,
        )


class AnthropicProvider(Provider):
    """Anthropic Messages API."""

    name = "anthropic"
    _create_path = ("messages", "create")

    def _raw_client(self, async_: bool, config: dict) -> Any:
        import anthropic

        api_key = (
            config.get("api_key")
            or os.environ.get("ANTHROPIC_API_KEY")
            or "sk-ant-cendor-sdk-placeholder"
        )
        kwargs: dict[str, Any] = {"api_key": api_key}
        if config.get("base_url"):
            kwargs["base_url"] = config["base_url"]
        cls = anthropic.AsyncAnthropic if async_ else anthropic.Anthropic
        return cls(**kwargs)

    def format_tools(self, tools: list[Tool]) -> Any:
        return [t.to_anthropic() for t in tools] or None

    def build_kwargs(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool],
        instructions: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        system = instructions
        if json_mode:
            system = (system + "\n\nRespond with only a single JSON object.").strip()
        wire = _canonical_to_anthropic(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": wire,
            "max_tokens": max_tokens or 1024,  # Anthropic requires max_tokens
        }
        if system:
            kwargs["system"] = system
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
        if temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs

    def parse(self, response: Any) -> ParsedResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolInvocation] = []
        for block in _get(response, "content") or []:
            btype = _get(block, "type")
            if btype == "text":
                text_parts.append(str(_get(block, "text") or ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolInvocation(
                        id=_get(block, "id") or f"call_{uuid.uuid4().hex[:8]}",
                        name=_get(block, "name") or "",
                        arguments=dict(_get(block, "input") or {}),
                    )
                )
        return ParsedResponse(
            content="".join(text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=_get(response, "stop_reason"),
            raw=response,
        )


def _canonical_to_anthropic(messages: list[dict]) -> list[dict]:
    """Translate canonical (OpenAI-shape) history to Anthropic message blocks.

    Assistant tool calls become ``tool_use`` content blocks; tool results become ``tool_result``
    blocks folded into a single following user turn (consecutive tool results merge).
    """
    out: list[dict] = []
    pending_results: list[dict] = []

    def flush_results() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": _stringify(m.get("content")),
                }
            )
            continue
        flush_results()
        if role == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": _stringify(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": _loads_args(fn.get("arguments")),
                    }
                )
            out.append({"role": "assistant", "content": blocks if blocks else ""})
        else:  # user / system-as-user fallback
            out.append({"role": "user", "content": _stringify(m.get("content"))})
    flush_results()
    return out


class GeminiProvider(Provider):
    """Google Gemini (google-genai) — normalization + formatting (client via [google])."""

    name = "google"
    _create_path = ("models", "generate_content")

    def _raw_client(self, async_: bool, config: dict) -> Any:
        from google import genai  # type: ignore

        api_key = config.get("api_key") or os.environ.get("GOOGLE_API_KEY")
        return genai.Client(api_key=api_key)

    def format_tools(self, tools: list[Tool]) -> Any:
        if not tools:
            return None
        return [{"function_declarations": [t.to_gemini() for t in tools]}]

    def build_kwargs(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool],
        instructions: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        contents = [
            {
                "role": "model" if m.get("role") == "assistant" else "user",
                "parts": [{"text": _stringify(m.get("content"))}],
            }
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        config: dict[str, Any] = {}
        if instructions:
            config["system_instruction"] = instructions
        if temperature is not None:
            config["temperature"] = temperature
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        formatted = self.format_tools(tools)
        if formatted:
            config["tools"] = formatted
        kwargs: dict[str, Any] = {"model": model, "contents": contents}
        if config:
            kwargs["config"] = config
        return kwargs

    def parse(self, response: Any) -> ParsedResponse:
        cand = _first(_get(response, "candidates"))
        parts = _get(_get(cand, "content"), "parts") or []
        text_parts: list[str] = []
        tool_calls: list[ToolInvocation] = []
        for p in parts:
            if _get(p, "text"):
                text_parts.append(str(_get(p, "text")))
            fc = _get(p, "function_call")
            if fc:
                tool_calls.append(
                    ToolInvocation(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name=_get(fc, "name") or "",
                        arguments=dict(_get(fc, "args") or {}),
                    )
                )
        content = _get(response, "text")
        if content is None and text_parts:
            content = "".join(text_parts)
        return ParsedResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=_get(cand, "finish_reason"),
            raw=response,
        )


class BedrockProvider(Provider):
    """AWS Bedrock Converse — normalization + formatting (client via [bedrock])."""

    name = "bedrock"
    _create_path = ("converse",)

    def _raw_client(self, async_: bool, config: dict) -> Any:
        import boto3  # type: ignore

        return boto3.client("bedrock-runtime", **config)

    def format_tools(self, tools: list[Tool]) -> Any:
        if not tools:
            return None
        return {"tools": [t.to_bedrock() for t in tools]}

    def build_kwargs(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool],
        instructions: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        wire = [
            {
                "role": "assistant" if m.get("role") == "assistant" else "user",
                "content": [{"text": _stringify(m.get("content"))}],
            }
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        kwargs: dict[str, Any] = {"modelId": model, "messages": wire}
        if instructions:
            kwargs["system"] = [{"text": instructions}]
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["toolConfig"] = formatted
        inference: dict[str, Any] = {}
        if temperature is not None:
            inference["temperature"] = temperature
        if max_tokens is not None:
            inference["maxTokens"] = max_tokens
        if inference:
            kwargs["inferenceConfig"] = inference
        return kwargs

    def parse(self, response: Any) -> ParsedResponse:
        message = _get(_get(response, "output"), "message")
        text_parts: list[str] = []
        tool_calls: list[ToolInvocation] = []
        for block in _get(message, "content") or []:
            if _get(block, "text"):
                text_parts.append(str(_get(block, "text")))
            tu = _get(block, "toolUse")
            if tu:
                tool_calls.append(
                    ToolInvocation(
                        id=_get(tu, "toolUseId") or f"call_{uuid.uuid4().hex[:8]}",
                        name=_get(tu, "name") or "",
                        arguments=dict(_get(tu, "input") or {}),
                    )
                )
        return ParsedResponse(
            content="".join(text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=_get(response, "stopReason"),
            raw=response,
        )


class OllamaProvider(Provider):
    """Ollama chat — normalization + formatting (client via [ollama])."""

    name = "ollama"
    _create_path = ("chat",)

    def _raw_client(self, async_: bool, config: dict) -> Any:
        import ollama  # type: ignore

        cls = ollama.AsyncClient if async_ else ollama.Client
        return cls(**config)

    def format_tools(self, tools: list[Tool]) -> Any:
        return [t.to_openai() for t in tools] or None

    def build_kwargs(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool],
        instructions: str,
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        wire: list[dict] = []
        if instructions:
            wire.append({"role": "system", "content": instructions})
        wire.extend(messages)
        kwargs: dict[str, Any] = {"model": model, "messages": wire}
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
        if json_mode:
            kwargs["format"] = "json"
        return kwargs

    def parse(self, response: Any) -> ParsedResponse:
        message = _get(response, "message")
        tool_calls: list[ToolInvocation] = []
        for tc in _get(message, "tool_calls") or []:
            fn = _get(tc, "function")
            tool_calls.append(
                ToolInvocation(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=_get(fn, "name") or "",
                    arguments=_loads_args(_get(fn, "arguments")),
                )
            )
        done = _get(response, "done")
        return ParsedResponse(
            content=_get(message, "content"),
            tool_calls=tool_calls,
            finish_reason=_get(response, "done_reason") or ("stop" if done else None),
            raw=response,
        )


# --------------------------------------------------------------------------- registry

_PROVIDERS: dict[str, Provider] = {
    "openai": OpenAIChatProvider(),
    "openai_responses": OpenAIResponsesProvider(),
    "anthropic": AnthropicProvider(),
    "google": GeminiProvider(),
    "gemini": GeminiProvider(),
    "bedrock": BedrockProvider(),
    "ollama": OllamaProvider(),
}

#: (prefix, provider-name) — first match wins; longer/more-specific prefixes first.
_MODEL_PREFIXES: list[tuple[str, str]] = [
    ("gpt-", "openai"),
    ("chatgpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("bedrock/", "bedrock"),
    ("anthropic.", "bedrock"),
    ("amazon.", "bedrock"),
    ("meta.", "bedrock"),
    ("mistral.", "bedrock"),
    ("cohere.", "bedrock"),
    ("llama", "ollama"),
    ("qwen", "ollama"),
    ("mistral", "ollama"),
    ("phi", "ollama"),
    ("gemma", "ollama"),
]


def infer_provider(model: str) -> str:
    """Infer the provider name from a model id. Raises ``ValueError`` if unknown."""
    m = model.lower()
    for prefix, name in _MODEL_PREFIXES:
        if m.startswith(prefix):
            return name
    raise ValueError(
        f"cannot infer a provider for model {model!r}; pass provider=... on the Agent "
        f"(one of: {', '.join(sorted(set(_PROVIDERS)))})"
    )


def get_provider(name: str) -> Provider:
    """Look up a provider by name. Raises ``ValueError`` if unknown."""
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"unknown provider {name!r}; known: {', '.join(sorted(_PROVIDERS))}"
        ) from None


def resolve_provider(model: str, provider: str | None = None) -> Provider:
    """Resolve a provider for an agent: explicit ``provider`` wins, else infer from the model."""
    return get_provider(provider) if provider else get_provider(infer_provider(model))
