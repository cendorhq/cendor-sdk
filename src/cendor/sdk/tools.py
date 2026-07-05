"""``tool()`` / ``Tool`` — turn a Python function into a governed, provider-formatted tool.

The JSON Schema is generated from the function's type hints; the description from its docstring
(with a best-effort Google-style ``Args:`` parse for per-parameter descriptions). Every tool
executes through ``cendor-core``'s ``instrument_tool`` so each call emits a ``ToolCall`` on the bus
(correlated by ``trace_id``, recorded by the audit chain, replayable by cassette).

Per-provider formatting (OpenAI functions, Anthropic ``tools``, Gemini function declarations,
Bedrock ``toolConfig``) lives here too, so ``providers.py`` only has to ask for the right shape.
"""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Union, get_args, get_origin

from cendor.core import instrument_tool

_JSON_PRIMITIVES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _schema_for_annotation(annotation: Any) -> dict:
    """Map a Python type annotation to a JSON Schema fragment (best-effort, offline)."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}  # unconstrained
    if annotation in _JSON_PRIMITIVES:
        return {"type": _JSON_PRIMITIVES[annotation]}

    origin = get_origin(annotation)

    # Optional[X] / X | None / Union[...]
    if origin is Union or origin is types.UnionType:
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return _schema_for_annotation(members[0])
        return {"anyOf": [_schema_for_annotation(a) for a in members]}

    if origin is Literal:
        values = list(get_args(annotation))
        schema: dict[str, Any] = {"enum": values}
        if values and all(isinstance(v, str) for v in values):
            schema["type"] = "string"
        return schema

    if origin in (list, set, tuple, frozenset):
        args = get_args(annotation)
        item = _schema_for_annotation(args[0]) if args else {}
        return {"type": "array", "items": item or {}}

    if origin is dict:
        return {"type": "object"}

    if annotation in (list, set, tuple):
        return {"type": "array"}
    if annotation is dict:
        return {"type": "object"}

    # Unknown / complex — leave unconstrained rather than emit an invalid schema.
    return {}


def _parse_arg_docs(doc: str) -> dict[str, str]:
    """Best-effort Google-style ``Args:`` parse → {param: description}. Never raises."""
    out: dict[str, str] = {}
    lines = doc.splitlines()
    in_args = False
    current: str | None = None
    for raw in lines:
        line = raw.strip()
        header = line.rstrip(":").lower()
        if header in ("args", "arguments", "parameters"):
            in_args = True
            continue
        if in_args:
            if header in ("returns", "return", "raises", "yields", "examples", "example", "note"):
                break
            if ":" in line and not line.startswith(" "):
                name, _, desc = line.partition(":")
                name = name.split("(")[0].strip()  # "city (str): ..." -> "city"
                if name:
                    out[name] = desc.strip()
                    current = name
            elif current and line:
                out[current] = (out[current] + " " + line).strip()
    return out


def _build_schema(func: Callable) -> tuple[str, dict]:
    """Return ``(description, parameters_json_schema)`` for a function."""
    doc = inspect.getdoc(func) or ""
    description = doc.split("\n\n")[0].strip() if doc else (func.__name__.replace("_", " "))
    arg_docs = _parse_arg_docs(doc)

    try:
        hints = typing.get_type_hints(func)
    except Exception:  # noqa: BLE001 - unresolved annotations must not break tool creation
        hints = getattr(func, "__annotations__", {})

    sig = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, param.annotation)
        pschema = _schema_for_annotation(annotation)
        if pname in arg_docs:
            pschema = {**pschema, "description": arg_docs[pname]}
        properties[pname] = pschema
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return description, schema


@dataclass
class Tool:
    """A callable tool with a generated JSON Schema and per-provider formatting.

    Construct via the :func:`tool` decorator rather than directly.
    """

    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    is_async: bool

    def __post_init__(self) -> None:
        # Wrap once with instrument_tool so every execution emits a ToolCall on the bus.
        self._runner: Callable[..., Any] = instrument_tool(self.name)(self.func)

    def invoke(self, arguments: dict[str, Any]) -> Any:
        """Execute the tool synchronously with a dict of arguments."""
        return self._runner(**arguments)

    async def ainvoke(self, arguments: dict[str, Any]) -> Any:
        """Execute the tool, awaiting it if it is a coroutine function."""
        result = self._runner(**arguments)
        if inspect.isawaitable(result):
            return await result
        return result

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the underlying function directly (also emits a ToolCall)."""
        return self._runner(*args, **kwargs)

    # --- per-provider formatting ----------------------------------------------------------------

    def to_openai(self) -> dict:
        """OpenAI Chat Completions / Responses function-tool shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict:
        """Anthropic ``tools`` shape."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_gemini(self) -> dict:
        """A single Gemini function declaration (providers wrap these in a declarations list)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_bedrock(self) -> dict:
        """A single Bedrock Converse ``toolSpec`` (providers wrap a list in ``toolConfig``)."""
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {"json": self.parameters},
            }
        }


def tool(func: Callable[..., Any] | None = None, *, name: str | None = None) -> Any:
    """Decorate a function as a :class:`Tool`.

    Usable bare (``@tool``) or with a name (``@tool(name="lookup")``). The parameter schema is
    generated from type hints; the description from the docstring.

    ```python
    @tool
    def search(query: str, top_k: int = 3) -> list[str]:
        '''Search the knowledge base.'''
        ...
    ```
    """

    def make(fn: Callable[..., Any]) -> Tool:
        description, parameters = _build_schema(fn)
        return Tool(
            name=name or fn.__name__,
            description=description,
            parameters=parameters,
            func=fn,
            is_async=inspect.iscoroutinefunction(fn),
        )

    if func is not None:
        return make(func)
    return make


def as_tool(obj: Tool | Callable[..., Any]) -> Tool:
    """Coerce a ``Tool`` or plain callable into a ``Tool`` (idempotent)."""
    if isinstance(obj, Tool):
        return obj
    return tool(obj)
