"""Provider abstraction: client construction, outbound formatting, inbound normalization.

``cendor-core`` normalizes *usage/cost*; the SDK adds what core deliberately leaves out — parsing
each provider's response *shape* into assistant **content + tool calls + finish reason**, and
formatting the outbound request (messages, tools, system) per provider. The conversation is held in
one **canonical (OpenAI-shape)** format so a run can hand off between providers without rewriting
history; each provider translates canonical → its wire format at call time.

Normalization is implemented for OpenAI (Chat Completions + Responses), Anthropic, Gemini, Bedrock,
Ollama, Hugging Face, Azure AI Foundry (Chat Completions + Responses), and Foundry Local. Client
construction ships for OpenAI + Anthropic (Phase 1); the others construct behind lazy imports via
their extras.

Hugging Face (``huggingface_hub``) and Azure AI Foundry both speak the OpenAI Chat Completions
*shape*, so their providers subclass :class:`OpenAIChatProvider` and only override client
construction. For Foundry this is deliberate and future-proof: Microsoft's current guidance is to
consume Foundry deployments with the **standard** ``openai`` SDK pointed at the ``/openai/v1/``
endpoint — the ``AzureOpenAI`` client and the ``azure-ai-inference`` package are being retired — so
"connect to Foundry" is just the OpenAI provider with a Foundry ``base_url`` and the *deployment
name* as the model id.
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


#: OpenAI reasoning-model families that reject a ``temperature`` param (calls 400 if it's sent).
_NO_TEMPERATURE_PREFIXES: tuple[str, ...] = ("o1", "o3", "o4")


def _openai_supports_temperature(model: str) -> bool:
    """Whether an OpenAI model accepts ``temperature`` (o-series reasoning models reject it)."""
    return not model.lower().startswith(_NO_TEMPERATURE_PREFIXES)


def _json_instruction(output_schema: dict | None) -> str:
    """A system-prompt nudge to emit JSON — carrying the schema when one is known (more reliable
    than a bare 'respond with JSON', and the only structured-output lever on providers without a
    native JSON-schema mode)."""
    if output_schema:
        return "\n\nRespond with ONLY a single JSON object matching this schema:\n" + json.dumps(
            output_schema
        )
    return "\n\nRespond with only a single JSON object."


# --------------------------------------------------------------------------- multimodal content
#
# The canonical content shape for multimodal input is OpenAI's content-parts list:
#   [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:|https"}}]
# OpenAI/Azure/Foundry-Local/HF pass this through unchanged; the translators below map it onto
# Anthropic / Gemini blocks. (Bedrock keeps the text; image bytes are out of scope there for now.)


def _part_text(part: Any) -> str | None:
    if isinstance(part, dict) and part.get("type") == "text":
        return str(part.get("text", ""))
    return None


def _image_url(part: Any) -> str | None:
    if isinstance(part, dict) and part.get("type") == "image_url":
        u = part.get("image_url")
        return u.get("url") if isinstance(u, dict) else (u if isinstance(u, str) else None)
    return None


def _parse_data_url(url: str) -> tuple[str, str]:
    """``data:image/png;base64,XXXX`` -> ``("image/png", "XXXX")``; best-effort."""
    try:
        header, data = url.split(",", 1)
        media_type = header[len("data:") :].split(";", 1)[0] or "image/png"
        return media_type, data
    except ValueError:
        return "image/png", ""


def _text_of_content(content: Any) -> str:
    """Join the text parts of a (possibly multimodal) content value; scalars pass through."""
    if isinstance(content, list):
        return "".join(t for p in content if (t := _part_text(p)) is not None)
    return _stringify(content)


def _anthropic_content(content: Any) -> Any:
    """Canonical content -> Anthropic content (a string, or a list of text/image blocks)."""
    if not isinstance(content, list):
        return _stringify(content)
    blocks: list[dict] = []
    for p in content:
        text = _part_text(p)
        if text is not None:
            blocks.append({"type": "text", "text": text})
            continue
        url = _image_url(p)
        if url and url.startswith("data:"):
            media_type, data = _parse_data_url(url)
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                }
            )
        elif url:
            blocks.append({"type": "image", "source": {"type": "url", "url": url}})
    return blocks or ""


def _gemini_parts(content: Any) -> list[dict]:
    """Canonical content -> Gemini ``parts`` (text + inline_data/file_data for images)."""
    if not isinstance(content, list):
        return [{"text": _stringify(content)}]
    parts: list[dict] = []
    for p in content:
        text = _part_text(p)
        if text is not None:
            parts.append({"text": text})
            continue
        url = _image_url(p)
        if url and url.startswith("data:"):
            media_type, data = _parse_data_url(url)
            parts.append({"inline_data": {"mime_type": media_type, "data": data}})
        elif url:
            parts.append({"file_data": {"file_uri": url}})
    return parts or [{"text": ""}]


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
        output_schema: dict | None = None,
    ) -> dict:
        raise NotImplementedError

    # --- inbound --------------------------------------------------------------------------------

    def parse(self, response: Any) -> ParsedResponse:
        raise NotImplementedError

    @staticmethod
    def wants_more_tools(parsed: ParsedResponse) -> bool:
        return bool(parsed.tool_calls)

    # --- streaming ------------------------------------------------------------------------------
    #: Whether this provider reassembles a streamed response. False → the runner falls back to a
    #: non-streamed call and yields the whole text as one delta (correct, just not incremental).
    supports_stream: bool = False

    def stream_text(self, chunk: Any) -> str:
        """The text delta carried by one streamed chunk (``""`` if none)."""
        return ""

    def parse_stream(self, chunks: list) -> ParsedResponse:
        """Reassemble a full :class:`ParsedResponse` (content + tool calls) from streamed chunks."""
        raise NotImplementedError


class OpenAIChatProvider(Provider):
    """OpenAI Chat Completions."""

    name = "openai"
    _create_path: tuple[str, ...] = ("chat", "completions", "create")

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
        output_schema: dict | None = None,
    ) -> dict:
        wire: list[dict] = []
        sys = instructions
        if json_mode and not output_schema:  # native json_schema below is stronger than a nudge
            sys = (sys + "\n\nRespond with a single JSON object.").strip()
        if sys:
            wire.append({"role": "system", "content": sys})
        wire.extend(messages)
        kwargs: dict[str, Any] = {"model": model, "messages": wire}
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
        if json_mode:
            if output_schema:  # schema-constrained output (more reliable than json_object)
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": output_schema, "strict": False},
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}
        if temperature is not None and _openai_supports_temperature(model):
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

    supports_stream = True

    def stream_text(self, chunk: Any) -> str:
        choice = _first(_get(chunk, "choices"))
        return str(_get(_get(choice, "delta"), "content", "") or "")

    def parse_stream(self, chunks: list) -> ParsedResponse:
        """Accumulate Chat Completions deltas: content is concatenated; tool calls are reassembled
        per ``index`` (id/name captured, argument fragments joined)."""
        content_parts: list[str] = []
        acc: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        for ch in chunks:
            choice = _first(_get(ch, "choices"))
            if choice is None:
                continue
            delta = _get(choice, "delta")
            txt = _get(delta, "content")
            if txt:
                content_parts.append(str(txt))
            for tc in _get(delta, "tool_calls") or []:
                idx = _get(tc, "index", 0) or 0
                slot = acc.setdefault(idx, {"id": None, "name": "", "args": ""})
                if _get(tc, "id"):
                    slot["id"] = _get(tc, "id")
                fn = _get(tc, "function")
                if _get(fn, "name"):
                    slot["name"] = _get(fn, "name")
                if _get(fn, "arguments"):
                    slot["args"] += str(_get(fn, "arguments"))
            if _get(choice, "finish_reason"):
                finish = _get(choice, "finish_reason")
        tool_calls = [
            ToolInvocation(
                id=slot["id"] or f"call_{uuid.uuid4().hex[:8]}",
                name=slot["name"] or "",
                arguments=_loads_args(slot["args"]),
            )
            for _, slot in sorted(acc.items())
        ]
        return ParsedResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            raw=chunks,
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
        output_schema: dict | None = None,
    ) -> dict:
        kwargs: dict[str, Any] = {"model": model, "input": list(messages)}
        sys = instructions
        if json_mode:
            sys = (sys + _json_instruction(output_schema)).strip()
        if sys:
            kwargs["instructions"] = sys
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
        if temperature is not None and _openai_supports_temperature(model):
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
        output_schema: dict | None = None,
    ) -> dict:
        system = instructions
        if json_mode:
            system = (system + _json_instruction(output_schema)).strip()
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
        else:  # user / system-as-user fallback (supports multimodal content parts)
            out.append({"role": "user", "content": _anthropic_content(m.get("content"))})
    flush_results()
    return out


def _canonical_to_gemini(messages: list[dict]) -> list[dict]:
    """Translate canonical (OpenAI-shape) history to Gemini ``contents``.

    Assistant tool calls become ``function_call`` parts; tool results become ``function_response``
    parts folded into a following ``user`` turn (Gemini has no dedicated tool role). Without this,
    a multi-turn tool conversation would lose its tool calls/results and the loop would stall.
    """
    out: list[dict] = []
    pending: list[dict] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "parts": list(pending)})
            pending.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            pending.append(
                {
                    "function_response": {
                        "name": m.get("name", ""),
                        "response": {"result": _stringify(m.get("content"))},
                    }
                }
            )
            continue
        flush()
        if role == "assistant":
            parts: list[dict] = []
            if m.get("content"):
                parts.append({"text": _stringify(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                parts.append(
                    {
                        "function_call": {
                            "name": fn.get("name", ""),
                            "args": _loads_args(fn.get("arguments")),
                        }
                    }
                )
            out.append({"role": "model", "parts": parts or [{"text": ""}]})
        else:  # user / system-as-user fallback (supports multimodal content parts)
            out.append({"role": "user", "parts": _gemini_parts(m.get("content"))})
    flush()
    return out


def _canonical_to_bedrock(messages: list[dict]) -> list[dict]:
    """Translate canonical (OpenAI-shape) history to Bedrock Converse ``messages``.

    Assistant tool calls become ``toolUse`` blocks; tool results become ``toolResult`` blocks folded
    into a following ``user`` turn (consecutive results merge). Without this, Converse loses the
    tool exchange and the agent loop cannot complete on Bedrock.
    """
    out: list[dict] = []
    pending: list[dict] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            pending.append(
                {
                    "toolResult": {
                        "toolUseId": m.get("tool_call_id", ""),
                        "content": [{"text": _stringify(m.get("content"))}],
                    }
                }
            )
            continue
        flush()
        if role == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"text": _stringify(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": _loads_args(fn.get("arguments")),
                        }
                    }
                )
            out.append({"role": "assistant", "content": blocks or [{"text": ""}]})
        else:  # user / system-as-user fallback (multimodal: text parts kept)
            out.append({"role": "user", "content": [{"text": _text_of_content(m.get("content"))}]})
    flush()
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
        output_schema: dict | None = None,
    ) -> dict:
        contents = _canonical_to_gemini(messages)
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
        elif json_mode:  # Gemini can't combine function tools with a forced JSON schema
            config["response_mime_type"] = "application/json"
            if output_schema:
                config["response_schema"] = output_schema
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
        output_schema: dict | None = None,
    ) -> dict:
        wire = _canonical_to_bedrock(messages)
        system_text = instructions
        if json_mode:
            system_text = (system_text + _json_instruction(output_schema)).strip()
        kwargs: dict[str, Any] = {"modelId": model, "messages": wire}
        if system_text:
            kwargs["system"] = [{"text": system_text}]
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
        output_schema: dict | None = None,
    ) -> dict:
        wire: list[dict] = []
        if instructions:
            wire.append({"role": "system", "content": instructions})
        wire.extend(messages)
        kwargs: dict[str, Any] = {"model": model, "messages": wire}
        formatted = self.format_tools(tools)
        if formatted:
            kwargs["tools"] = formatted
        if json_mode:  # Ollama accepts a JSON schema (or "json") as the format constraint
            kwargs["format"] = output_schema if output_schema else "json"
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

    supports_stream = True

    def stream_text(self, chunk: Any) -> str:
        return str(_get(_get(chunk, "message"), "content", "") or "")

    def parse_stream(self, chunks: list) -> ParsedResponse:
        """Accumulate Ollama chat chunks: content deltas concatenated; tool calls arrive whole on
        a chunk's ``message.tool_calls`` (usually the final one)."""
        content_parts: list[str] = []
        tool_calls: list[ToolInvocation] = []
        finish: str | None = None
        for ch in chunks:
            message = _get(ch, "message")
            txt = _get(message, "content")
            if txt:
                content_parts.append(str(txt))
            for tc in _get(message, "tool_calls") or []:
                fn = _get(tc, "function")
                tool_calls.append(
                    ToolInvocation(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        name=_get(fn, "name") or "",
                        arguments=_loads_args(_get(fn, "arguments")),
                    )
                )
            if _get(ch, "done_reason") or _get(ch, "done"):
                finish = _get(ch, "done_reason") or "stop"
        return ParsedResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            raw=chunks,
        )


class HuggingFaceProvider(OpenAIChatProvider):
    """Hugging Face Inference (``huggingface_hub.InferenceClient``).

    ``InferenceClient.chat_completion`` returns an OpenAI-shaped ``ChatCompletionOutput`` (choices /
    message / tool_calls / usage), so this reuses :class:`OpenAIChatProvider`'s request formatting
    and response parsing verbatim — only the client and the create path differ. ``cendor-core``
    detects ``chat_completion`` structurally and attributes the ``LLMCall`` to ``huggingface``.

    The ``model`` is a Hub model id (``"meta-llama/Llama-3.1-8B-Instruct"``) or an Inference
    Endpoint URL. Point at a dedicated endpoint / third-party provider with ``base_url=``, and route
    through a specific inference provider with the ``HF_PROVIDER`` env var (e.g. ``together``).
    """

    name = "huggingface"
    _create_path: tuple[str, ...] = ("chat_completion",)

    def _raw_client(self, async_: bool, config: dict) -> Any:
        from huggingface_hub import AsyncInferenceClient, InferenceClient  # type: ignore

        token = (
            config.get("api_key")
            or os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        )
        kwargs: dict[str, Any] = {"token": token}
        if config.get("base_url"):
            kwargs["base_url"] = config["base_url"]
        provider = os.environ.get("HF_PROVIDER")
        if provider:
            kwargs["provider"] = provider
        cls = AsyncInferenceClient if async_ else InferenceClient
        return cls(**kwargs)


def _azure_foundry_base_url(config: dict) -> str | None:
    """Resolve (and normalize) the Azure AI Foundry OpenAI-v1 ``base_url``.

    Accepts an explicit ``base_url`` or the ``AZURE_OPENAI_ENDPOINT`` / ``AZURE_OPENAI_BASE_URL`` /
    ``AZURE_AI_ENDPOINT`` env vars — a bare Foundry host (``https://<res>.openai.azure.com`` or
    ``https://<res>.services.ai.azure.com``) gets the ``/openai/v1/`` route appended. An endpoint
    that already carries a path (``/openai/v1`` or the legacy ``/models``) is left as-is.
    """
    raw = (
        config.get("base_url")
        or os.environ.get("AZURE_OPENAI_BASE_URL")
        or os.environ.get("AZURE_OPENAI_ENDPOINT")
        or os.environ.get("AZURE_AI_ENDPOINT")
    )
    if not raw:
        return None
    raw = raw.rstrip("/")
    if "/openai/v1" in raw or raw.endswith("/models"):
        return raw + "/"
    if raw.endswith((".openai.azure.com", ".services.ai.azure.com")) or (
        ".cognitiveservices.azure.com" in raw
    ):
        return raw + "/openai/v1/"
    return raw + "/"


class AzureFoundryProvider(OpenAIChatProvider):
    """Azure AI Foundry models via the OpenAI-compatible ``/openai/v1/`` endpoint.

    Microsoft's current guidance (the ``AzureOpenAI`` client and ``azure-ai-inference`` are being
    retired) is to consume Foundry deployments with the **standard** ``openai`` SDK pointed at the
    Foundry v1 endpoint. So this is :class:`OpenAIChatProvider` with Foundry-aware construction:

    * ``model`` is your Foundry **deployment name** (Azure keys on deployment, not model, name).
    * ``base_url`` is the Foundry endpoint — either ``https://<res>.openai.azure.com`` (Azure OpenAI
      models) or ``https://<res>.services.ai.azure.com`` (Foundry Models incl. DeepSeek, Grok,
      Llama, …); ``/openai/v1/`` is appended for you. Also read from ``AZURE_OPENAI_ENDPOINT``.
    * ``api_key`` is your resource key (``AZURE_OPENAI_API_KEY`` / ``AZURE_INFERENCE_CREDENTIAL``).
      For Microsoft Entra ID, pass a bearer-token provider as ``api_key`` (the v1 client
      refreshes it), or build the client yourself and pass ``client=`` on the Agent.

    ``api-version`` is not needed — the v1 GA API infers it. Detected by ``cendor-core`` as OpenAI
    (it *is* the ``openai`` SDK), so budgets/guard/audit ride the same seams.
    """

    name = "azure"

    def _raw_client(self, async_: bool, config: dict) -> Any:
        import openai

        base_url = _azure_foundry_base_url(config)
        api_key = (
            config.get("api_key")
            or os.environ.get("AZURE_OPENAI_API_KEY")
            or os.environ.get("AZURE_INFERENCE_CREDENTIAL")
            or os.environ.get("AZURE_AI_API_KEY")
            or "azure-cendor-sdk-placeholder"
        )
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        cls = openai.AsyncOpenAI if async_ else openai.OpenAI
        return cls(**kwargs)


class AzureFoundryResponsesProvider(OpenAIResponsesProvider):
    """Azure AI Foundry via the OpenAI **Responses** API (``responses.create``).

    Same Foundry-aware client construction as :class:`AzureFoundryProvider`, but drives the
    Responses API instead of Chat Completions — the primary surface for OpenAI-family Foundry
    deployments (``gpt-*``, ``o*``). Use ``provider="azure_responses"`` when your deployment is an
    OpenAI model and you want Responses semantics; keep ``provider="azure"`` (Chat Completions) for
    the broadest coverage across non-OpenAI Foundry models (DeepSeek, Grok, Llama, …).
    """

    name = "azure_responses"

    def _raw_client(self, async_: bool, config: dict) -> Any:
        return AzureFoundryProvider()._raw_client(async_, config)


def _foundry_local_base_url(config: dict) -> str | None:
    """Resolve (and normalize to ``/v1/``) the Foundry Local OpenAI-compatible endpoint.

    Accepts an explicit ``base_url`` or the ``FOUNDRY_LOCAL_ENDPOINT`` env var — typically the
    ``FoundryLocalManager(alias).endpoint`` of the running on-device service. A bare host gets
    ``/v1/`` appended; an endpoint that already ends in ``/v1`` is preserved.
    """
    raw = config.get("base_url") or os.environ.get("FOUNDRY_LOCAL_ENDPOINT")
    if not raw:
        return None
    raw = raw.rstrip("/")
    return (raw + "/") if raw.endswith("/v1") else (raw + "/v1/")


class FoundryLocalProvider(OpenAIChatProvider):
    """Microsoft **Foundry Local** — on-device models over the local OpenAI-compatible REST server.

    The local counterpart to Ollama: Foundry Local runs a model on the device and exposes an
    OpenAI-compatible endpoint, so this is :class:`OpenAIChatProvider` pointed at that local URL. No
    key is needed (``api_key`` defaults to ``"none"``). Two ways to supply the endpoint:

    * Run the service yourself and pass ``base_url=`` (or set ``FOUNDRY_LOCAL_ENDPOINT``).
    * Bootstrap it with ``foundry_local.FoundryLocalManager(alias)`` — its ``.endpoint`` /
      ``.api_key`` and the resolved model id (``.get_model_info(alias).id``). For the fully managed
      path, build that ``openai`` client and hand it to ``Agent(client=...)``.

    ``model`` is the concrete Foundry Local model id (not the catalog alias). Detected by
    ``cendor-core`` as OpenAI (it *is* an OpenAI-compatible endpoint), so governance rides the same
    seams.
    """

    name = "foundry_local"

    def _raw_client(self, async_: bool, config: dict) -> Any:
        import openai

        base_url = _foundry_local_base_url(config)
        if not base_url:
            raise ValueError(
                "Foundry Local needs an endpoint: pass base_url=... on the Agent or set "
                "FOUNDRY_LOCAL_ENDPOINT (e.g. foundry_local.FoundryLocalManager(alias).endpoint). "
                "See docs/sdk.md → Connecting to Hugging Face & Azure AI Foundry."
            )
        api_key = config.get("api_key") or os.environ.get("FOUNDRY_LOCAL_API_KEY") or "none"
        cls = openai.AsyncOpenAI if async_ else openai.OpenAI
        return cls(api_key=api_key, base_url=base_url)


# --------------------------------------------------------------------------- registry

_PROVIDERS: dict[str, Provider] = {
    "openai": OpenAIChatProvider(),
    "openai_responses": OpenAIResponsesProvider(),
    "anthropic": AnthropicProvider(),
    "google": GeminiProvider(),
    "gemini": GeminiProvider(),
    "bedrock": BedrockProvider(),
    "ollama": OllamaProvider(),
    "huggingface": HuggingFaceProvider(),
    "hf": HuggingFaceProvider(),
    "azure": AzureFoundryProvider(),
    "azure_openai": AzureFoundryProvider(),
    "foundry": AzureFoundryProvider(),
    "azure_responses": AzureFoundryResponsesProvider(),
    "foundry_responses": AzureFoundryResponsesProvider(),
    "foundry_local": FoundryLocalProvider(),
    "foundry-local": FoundryLocalProvider(),
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
