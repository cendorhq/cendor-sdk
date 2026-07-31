"""The Run/Step/Result data model (plan §5).

A ``Step`` wraps the actual ``LLMCall``/``ToolCall`` that ``cendor-core`` already emitted on the
bus — the SDK does not re-invent these records, it just *correlates* them (by ``trace_id``) and
returns them. ``Result`` (aliased ``Run``) is what ``run()`` hands back: the final output, the
ordered steps, and aggregate usage + Decimal cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cendor.core.types import LLMCall, Money, ToolCall, Usage, sum_usage
from cendor.guardrails import GuardrailDecision

#: The marker the loop puts in front of a failed tool's result before handing it to the model.
#: The string the MODEL sees is a deliberate contract (it is how the model learns to recover) and is
#: never changed — this constant just gives the one place that defines it a name.
TOOL_ERROR_PREFIX = "[error] "


def is_tool_error(result: object) -> bool:
    """Whether a tool result is the loop's own failure marker.

    The single definition of "this tool failed", shared by :attr:`Result.tool_errors` and the
    ``cendor.tool.outcome`` span attribute (``otel._tool_outcome``) so the two can never disagree.

    Honest limit: a tool whose *own* output begins with ``"[error] "`` is indistinguishable from a
    failure. That is not new — the span attribute has classified on this prefix since it shipped —
    and it is why the marker is a constant here rather than a literal in two files.
    """
    return isinstance(result, str) and result.startswith(TOOL_ERROR_PREFIX)


@dataclass(frozen=True)
class ToolError:
    """One tool execution that raised, in structured form.

    ``run()`` keeps the loop alive when a tool raises: the exception is turned into a
    ``"[error] <Type>: <message>"`` string, handed to the model, and the conversation continues.
    That
    is the right behaviour — but it left the *caller* with nothing to branch on except string
    matching. This is that failure, typed.

    ```python
    result = run(agent, "refund order 42")
    if result.tool_failed:
        for err in result.tool_errors:
            log.warning("tool %s raised %s: %s", err.tool, err.type, err.message)
    ```
    """

    tool: str
    """The tool that raised — or the name the model asked for, when no such tool exists."""

    type: str
    """The exception class name (``"ValueError"``), or ``"UnknownTool"`` when the model named a tool
    the agent does not have."""

    message: str
    """The exception's message, exactly as the model was shown it."""

    tool_call_id: str = ""
    """The provider's id for the call this failure answers, for correlation with the assistant turn
    that requested it. Empty only if the provider omitted one."""


_UNKNOWN_TOOL_MARKER = "unknown tool: "


def _parse_tool_error(content: str, tool: str, tool_call_id: str) -> ToolError:
    """Split the loop's own marker string back into its parts.

    Safe because this parses a string *this package wrote* two functions earlier
    (``runner._exec_tool_sync``: ``f"[error] {type(e).__name__}: {e}"``), not provider text.
    """
    body = content[len(TOOL_ERROR_PREFIX) :]
    if body.startswith(_UNKNOWN_TOOL_MARKER):
        return ToolError(
            tool=body[len(_UNKNOWN_TOOL_MARKER) :].strip() or tool,
            type="UnknownTool",
            message=body,
            tool_call_id=tool_call_id,
        )
    kind, sep, msg = body.partition(": ")
    if not sep:  # no separator — keep the whole body as the message rather than inventing a type
        return ToolError(tool=tool, type="", message=body, tool_call_id=tool_call_id)
    return ToolError(tool=tool, type=kind, message=msg, tool_call_id=tool_call_id)


@dataclass
class Step:
    """One correlated turn of a run: a single ``LLMCall`` (a model turn) or ``ToolCall`` (a tool
    execution). ``.call`` is the real bus event; ``.agent`` names the agent that produced it."""

    agent: str
    kind: str  # "llm" | "tool"
    call: LLMCall | ToolCall

    @property
    def name(self) -> str:
        """The model id for an LLM step, or the tool name for a tool step."""
        if isinstance(self.call, LLMCall):
            return self.call.model
        return self.call.name

    @property
    def trace_id(self) -> str:
        """The correlation id this step shares with every other step in its run."""
        return self.call.trace_id

    @property
    def usage(self) -> Usage | None:
        return self.call.usage if isinstance(self.call, LLMCall) else None

    @property
    def cost(self) -> Money | None:
        return self.call.cost if isinstance(self.call, LLMCall) else None


@dataclass
class Result:
    """The result of a run. ``run()`` returns this; ``Run`` is an alias for it (plan §5)."""

    output: Any
    """The final answer (a string) or the parsed structured object when ``output_type`` was set."""

    steps: list[Step] = field(default_factory=list)
    """Every ``LLMCall``/``ToolCall`` of the run, in order, correlated by one ``trace_id``."""

    trace_id: str = ""
    """The run id every step shares (set via ``cendor.core.trace``)."""

    conversation_id: str = ""
    """The conversation/session id this run belongs to, when it was run with a keyed session (G19).
    ``span_tree`` stamps it as ``gen_ai.conversation.id`` so a monitor groups a multi-turn thread.
    Empty when the run had no keyed session — a conversation id is never synthesized."""

    agents: list[str] = field(default_factory=list)
    """The agents that participated, in first-seen order (one, for a single-agent run)."""

    messages: list[dict] = field(default_factory=list)
    """The full conversation (canonical / OpenAI-shape messages), including tool results."""

    incomplete: bool = False
    """True if the run ended without a final answer — e.g. ``max_turns`` was hit with tool calls
    still pending. A finished, answered run is ``False``."""

    guardrail_decisions: list[GuardrailDecision] = field(default_factory=list)
    """Every guardrail trip/flag recorded during the run (redact/flag on the four stages, and any
    ``tool_call``/``tool_output`` block that returned a ``"[blocked …]"`` result to the model), in
    order — for post-hoc inspection without re-reading the audit file. A run that ended in a
    fail-closed ``block`` raised ``GuardrailTripped`` instead of returning a ``Result``; read that
    exception's ``.decisions`` for the blocking decision."""

    # --- convenience views ---------------------------------------------------------------------

    @property
    def llm_steps(self) -> list[Step]:
        """The model turns."""
        return [s for s in self.steps if s.kind == "llm"]

    @property
    def tool_steps(self) -> list[Step]:
        """The tool executions."""
        return [s for s in self.steps if s.kind == "tool"]

    @property
    def tool_errors(self) -> list[ToolError]:
        """Every tool execution that raised during this run, typed, in order.

        A raising tool does not stop the run — the loop hands the model
        ``"[error] <Type>: <message>"`` and keeps going — and it emits **no** ``ToolCall`` on the
        bus
        (core's tool wrapper does not catch), so a failed tool appears in neither :attr:`steps` nor
        :attr:`tool_steps`. Before this property the only machine-readable trace of a failure
        was that string prefix inside :attr:`messages`, which callers had to match by hand.

        Derived from :attr:`messages` rather than recorded separately, deliberately: the messages
        are what the model actually saw and what a checkpoint persists, so a **resumed** run
        reports its earlier tool failures too, and there is no second copy of the truth to drift.

        See :func:`is_tool_error` for the one honest limit.

        ```python
        result = run(agent, "refund order 42")
        assert not result.tool_failed          # or inspect result.tool_errors
        ```
        """
        errors: list[ToolError] = []
        for m in self.messages:
            if m.get("role") != "tool":
                continue
            content = m.get("content")
            if not is_tool_error(content):
                continue
            errors.append(
                _parse_tool_error(
                    str(content), str(m.get("name") or ""), str(m.get("tool_call_id") or "")
                )
            )
        return errors

    @property
    def tool_failed(self) -> bool:
        """True if any tool raised during the run. A guardrail *block* is not a failure — it is a
        decision, and lands in :attr:`guardrail_decisions` instead."""
        return bool(self.tool_errors)

    @property
    def final_message(self) -> dict | None:
        """The last assistant message, or ``None`` if the run produced none."""
        for m in reversed(self.messages):
            if m.get("role") == "assistant":
                return m
        return None

    @property
    def usage(self) -> Usage:
        """Aggregate token usage across every model turn of the run.

        Summed through core's field-complete ``sum_usage`` — a future ``Usage`` field can never
        silently vanish from this aggregate (it iterates the dataclass fields, not a hand list).
        """
        return sum_usage(s.usage for s in self.llm_steps if s.usage is not None)

    @property
    def cost(self) -> Money:
        """Aggregate cost across the run (priced model turns only; unpriced turns contribute 0)."""
        total = Money.zero()
        for s in self.llm_steps:
            if s.cost is not None:
                total = total + s.cost
        return total

    def __repr__(self) -> str:  # concise, useful in test output
        return (
            f"Result(output={self.output!r}, steps={len(self.steps)}, "
            f"cost={self.cost}, trace_id={self.trace_id!r})"
        )


# The plan's data model names the run record ``Run`` (id == trace_id, agents, steps, output, usage,
# cost). ``Result`` carries exactly that shape; expose both names.
Run = Result


# --------------------------------------------------------------------------- streaming events
#
# Yielded by ``run.stream`` / ``run.astream`` as a run progresses. The terminal ``RunComplete``
# carries the same ``Result`` a blocking ``run()`` would return.


@dataclass
class TextDelta:
    """A chunk of assistant text as it streams."""

    text: str


@dataclass
class ThinkingDelta:
    """A chunk of streamed **thinking / reasoning** text (GLR-12), yielded from ``run.stream`` only
    for providers that stream reasoning as it is produced (Ollama ``think`` models;
    OpenAI-compatible endpoints that stream ``reasoning_content``). Additive: providers that don't
    stream thinking emit none, and a consumer matching on the event type that doesn't handle
    ``ThinkingDelta`` is unaffected. Kept separate from :class:`TextDelta` (the visible answer) so a
    UI can render or hide reasoning independently."""

    text: str


@dataclass
class ToolCallEvent:
    """The model asked to call a tool (emitted before the tool runs)."""

    name: str
    arguments: dict
    id: str


@dataclass
class ToolResultEvent:
    """A tool finished; ``result`` is its stringified output."""

    name: str
    result: str


@dataclass
class RunComplete:
    """The terminal streaming event — carries the finished :class:`Result`."""

    result: Result


#: The union of events a streamed run yields.
StreamEvent = TextDelta | ThinkingDelta | ToolCallEvent | ToolResultEvent | RunComplete
