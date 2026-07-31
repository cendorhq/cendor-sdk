"""S7 — a caller can tell a tool failed without matching strings; the model still sees the string.

MEASURED MECHANISM (``plan/evidence-gapclose-2026-07-31/s7_probe_tool_failure_visibility.py``):
a tool that raises emits **zero** ``ToolCall`` events on the bus (``cendor.core`` does not catch
around the tool), so it appears in neither ``Result.steps`` nor ``Result.tool_steps``, and no
``execute_tool`` span is rendered for it. ``result.incomplete`` stays ``False`` — the run
"succeeded".
The only machine-readable trace of the failure was the ``"[error] …"`` prefix inside a tool message.

``Result.tool_errors`` closes that. The string handed to the MODEL is a deliberate contract and is
byte-identical — asserted below, because a "fix" that changed it would silently change how every
model recovers from a tool failure.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from cendor.core import bus
from cendor.core.types import ToolCall

from cendor.sdk import Agent, ToolError, run, tool
from cendor.sdk.result import Result, is_tool_error


@pytest.fixture(autouse=True)
def _clean_bus():
    bus._reset()
    yield
    bus._reset()


def _stub(*turns: Any) -> Any:
    """A client that returns the given parsed turns in order."""

    class Completions:
        def __init__(self) -> None:
            self.n = 0

        def create(self, **kwargs: Any) -> Any:
            turn = turns[min(self.n, len(turns) - 1)]
            self.n += 1
            return turn

    return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


def _tool_call_turn(name: str, args: str, call_id: str = "call_0") -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=call_id,
                            type="function",
                            function=SimpleNamespace(name=name, arguments=args),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        model="gpt-4o",
    )


def _answer_turn(text: str) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=6),
        model="gpt-4o",
    )


@tool
def explode(x: int) -> str:
    """Always fails."""
    raise ValueError(f"boom on {x}")


@tool
def fine(x: int) -> str:
    """Always works."""
    return f"ok {x}"


def test_a_raising_tool_is_reported_as_a_typed_tool_error() -> None:
    agent = Agent(
        name="probe",
        model="gpt-4o",
        instructions="Use tools.",
        tools=[explode],
        client=_stub(_tool_call_turn("explode", '{"x": 1}'), _answer_turn("could not")),
    )
    result = run(agent, "please explode")

    assert result.tool_failed is True
    assert result.tool_errors == [
        ToolError(tool="explode", type="ValueError", message="boom on 1", tool_call_id="call_0")
    ]


def test_the_string_the_model_sees_is_unchanged() -> None:
    """NEGATIVE CONTROL on the contract: the model-facing text is byte-identical to before."""
    agent = Agent(
        name="probe",
        model="gpt-4o",
        instructions="Use tools.",
        tools=[explode],
        client=_stub(_tool_call_turn("explode", '{"x": 7}'), _answer_turn("could not")),
    )
    result = run(agent, "please explode")
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert [m["content"] for m in tool_msgs] == ["[error] ValueError: boom on 7"]


def test_an_unknown_tool_is_typed_as_UnknownTool() -> None:
    agent = Agent(
        name="probe",
        model="gpt-4o",
        instructions="Use tools.",
        tools=[fine],
        client=_stub(_tool_call_turn("nope", "{}"), _answer_turn("no such tool")),
    )
    result = run(agent, "call nope")
    assert [(e.tool, e.type) for e in result.tool_errors] == [("nope", "UnknownTool")]


# --- NEGATIVE CONTROL: a successful run must report NOTHING. ------------------------------------
def test_a_successful_tool_run_has_no_tool_errors() -> None:
    agent = Agent(
        name="probe",
        model="gpt-4o",
        instructions="Use tools.",
        tools=[fine],
        client=_stub(_tool_call_turn("fine", '{"x": 3}'), _answer_turn("done")),
    )
    result = run(agent, "please work")
    assert result.tool_failed is False
    assert result.tool_errors == []
    assert len(result.tool_steps) == 1  # a SUCCEEDING tool does emit a ToolCall


def test_the_measured_baseline_still_holds_a_failed_tool_emits_no_toolcall() -> None:
    """Pins the mechanism this feature exists for, so a future core change is noticed HERE.

    If core ever starts emitting a ``ToolCall`` for a raising tool, this test goes red — and that is
    the moment to reconsider whether ``tool_errors`` should read steps instead of messages.
    """
    seen: list = []
    bus.subscribe(seen.append)
    agent = Agent(
        name="probe",
        model="gpt-4o",
        instructions="Use tools.",
        tools=[explode],
        client=_stub(_tool_call_turn("explode", '{"x": 1}'), _answer_turn("could not")),
    )
    result = run(agent, "please explode")
    assert [e for e in seen if isinstance(e, ToolCall)] == []
    assert result.tool_steps == []
    assert result.incomplete is False  # the run itself did not fail
    assert result.tool_errors  # …but the failure is now visible


# --- NEGATIVE CONTROL: a guardrail BLOCK is a decision, never a tool error. ---------------------
def test_a_guardrail_block_string_is_not_a_tool_error() -> None:
    blocked = Result(
        output="x",
        messages=[
            {"role": "tool", "tool_call_id": "c1", "name": "refund", "content": "[blocked] policy"},
        ],
    )
    assert blocked.tool_failed is False
    assert blocked.tool_errors == []


# --- The shared classifier: one definition for the span label and the Result view. --------------
def test_is_tool_error_classifies_only_the_marker() -> None:
    assert is_tool_error("[error] ValueError: x") is True
    assert is_tool_error("[blocked] policy") is False
    assert is_tool_error("all good") is False
    assert is_tool_error(None) is False
    assert is_tool_error(17) is False
    assert is_tool_error({"error": True}) is False


def test_the_span_attribute_and_the_result_view_share_one_definition() -> None:
    """`align, don't duplicate` — otel._tool_outcome must classify through is_tool_error."""
    from cendor.sdk.otel import _tool_outcome

    assert _tool_outcome(SimpleNamespace(result="[error] ValueError: x")) == "error"
    assert _tool_outcome(SimpleNamespace(result="[blocked] policy")) == "ok"
    assert _tool_outcome(SimpleNamespace(result="fine")) == "ok"
    assert _tool_outcome(SimpleNamespace(result=None)) == "ok"


# --- Parsing edge cases in the marker the SDK itself writes. ------------------------------------
def test_a_message_with_no_type_separator_keeps_the_whole_body() -> None:
    r = Result(
        output="x",
        messages=[{"role": "tool", "tool_call_id": "c", "name": "t", "content": "[error] bare"}],
    )
    assert r.tool_errors == [ToolError(tool="t", type="", message="bare", tool_call_id="c")]


def test_a_colon_inside_the_message_is_not_re_split() -> None:
    r = Result(
        output="x",
        messages=[
            {
                "role": "tool",
                "tool_call_id": "c",
                "name": "t",
                "content": "[error] HTTPError: 404: not found",
            }
        ],
    )
    assert r.tool_errors == [
        ToolError(tool="t", type="HTTPError", message="404: not found", tool_call_id="c")
    ]


def test_multiple_failures_are_reported_in_order() -> None:
    r = Result(
        output="x",
        messages=[
            {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "[error] A: one"},
            {"role": "assistant", "content": "hm"},
            {"role": "tool", "tool_call_id": "b", "name": "t2", "content": "ok"},
            {"role": "tool", "tool_call_id": "c", "name": "t3", "content": "[error] B: two"},
        ],
    )
    assert [(e.tool, e.type, e.message) for e in r.tool_errors] == [
        ("t1", "A", "one"),
        ("t3", "B", "two"),
    ]


def test_tool_errors_survive_a_resumed_run_because_they_come_from_the_messages() -> None:
    """A checkpoint persists ``messages``, so a resumed Result reports the earlier failure too."""
    r = Result(
        output="final",
        messages=[
            {"role": "user", "content": "go"},
            {"role": "tool", "tool_call_id": "c0", "name": "explode", "content": "[error] E: old"},
            {"role": "assistant", "content": "final"},
        ],
    )
    assert [e.message for e in r.tool_errors] == ["old"]
