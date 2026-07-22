"""cendor.sdk — a governed, provider-agnostic agent SDK.

*The second door into Cendor:* the simple, all-in-one governed agent SDK. Cost budgets,
tamper-evident audit, PII redaction, context governance, and record/replay testing are the
**foundation**, not plugins — composed through ``cendor-core``'s bus / interceptor / ``Sink`` /
``Compressor`` seams, correlated by ``trace()``, with zero SDK-specific glue. An ungoverned
``run()`` works on ``cendor-core`` alone.

```python
from cendor.sdk import Agent, tool, run, budget, guard, Policy, AuditLog

@tool
def get_weather(city: str) -> str:
    "Current weather for a city."
    return f"Sunny in {city}"

agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather])
log = AuditLog(system="support", path="audit.jsonl")
with budget(usd=0.25, on_exceed="block"), guard(Policy.default(), audit=log):
    result = run(agent, "What's the weather in Paris?", audit=log)
print(result.output, result.cost)
```
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

# --- audit + redaction (cendor-acttrace, re-exported) -------------------------------------------
# `guard` is the IDENTICAL acttrace object (since cendor-acttrace 1.5.0 its return is dual-shape:
# a plain interceptor that is also a context manager) — `cendor.sdk.guard is cendor.acttrace.guard`.
from cendor.acttrace import AuditLog, OTelMirror, Policy, PolicyViolation, guard, verify

# --- correlation + event types (cendor-core) ----------------------------------------------------
# `LLMCall`/`ToolCall`/`Usage`/`Money` are re-exported for isinstance checks and typing over
# `Result.steps[].call` (TS parity — @cendor/sdk has exported them since 0.4).
from cendor.core import LLMCall, Money, ToolCall, Usage, current_trace_id, trace

# --- guardrails gate (cendor-guardrails, re-exported) -------------------------------------------
# `Guardrail`/`guardrail`/`GuardrailTripped`/`judge`/`rules` are the one-import convenience for
# Agent(guardrails=[…]). NOTE: `evaluate` here is the SDK's eval harness, not guardrails.evaluate.
# `rules` is the SDK's own superset module (`.rules`): the deterministic cendor.guardrails rules
# PLUS `pii`/`secrets`/`entropy` bridged from acttrace's catalogue (SDK may import libs).
# `GuardrailDecision`/`Verdict` are re-exported for typing over `Result.guardrail_decisions`.
from cendor.guardrails import (
    Guardrail,
    GuardrailDecision,
    GuardrailTripped,
    LoadedPolicy,
    Verdict,
    guardrail,
    judge,
    load_policy,
    policy_schema,
    presets,
)
from cendor.guardrails.judge import task_adherence

# --- budgets + attribution (cendor-tokenguard, re-exported) -------------------------------------
# `downgrades()`/`clamps()` expose what a pre-flight `on_exceed="downgrade"`/token clamp rerouted.
from cendor.tokenguard import (
    BudgetEvent,
    BudgetExceeded,
    budget,
    clamps,
    configure,
    downgrades,
    report,
    track,
)

from . import rules

# --- the SDK -----------------------------------------------------------------------------------
from .a2a import A2AClient, A2AServer
from .agent import Agent
from .checkpoint import Checkpointer
from .embeddings import aembed, embed
from .eval import EvalCase, EvalReport, EvalResult, evaluate
from .foundry import FoundryAdapter
from .hitl import require_approval
from .mcp import get_mcp_prompt, load_mcp_prompts, load_mcp_resources, load_mcp_tools
from .memory import (
    Session,
    SQLiteSessionStore,
    SqliteSessionStore,
    SummarizingSession,
    llm_summarizer,
)
from .orchestration import (
    Handoff,
    handoff,
    parallel,
    parallel_async,
    sequential,
    supervisor,
)
from .otel import live_spans, span_tree
from .pricing import register_model_price
from .providers import MissingAPIKeyError, ParsedResponse, ToolInvocation
from .rag import Hit, VectorIndex
from .resilience import RetryPolicy
from .result import (
    Result,
    RunComplete,
    Step,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from .result import Run as Run
from .runner import Runner, run
from .tools import Tool, tool

# Derive the version from installed package metadata (the single source of truth is pyproject.toml),
# so it can never drift from the published distribution. Falls back only in a source tree with no
# installed metadata.
try:
    __version__ = version("cendor-sdk")
except PackageNotFoundError:  # pragma: no cover - source tree without installed metadata
    __version__ = "0.0.0+unknown"

__all__ = [
    # agent + loop
    "Agent",
    "tool",
    "Tool",
    "run",
    "Runner",
    "Session",
    # embeddings + RAG
    "embed",
    "aembed",
    "VectorIndex",
    "Hit",
    # orchestration
    "handoff",
    "Handoff",
    "sequential",
    "parallel",
    "parallel_async",
    "supervisor",
    # interop
    "load_mcp_tools",
    "load_mcp_prompts",
    "get_mcp_prompt",
    "load_mcp_resources",
    "A2AServer",
    "A2AClient",
    "FoundryAdapter",
    "span_tree",
    "live_spans",
    "require_approval",
    # hardening + eval
    "RetryPolicy",
    "Checkpointer",
    "SQLiteSessionStore",
    "SqliteSessionStore",  # deprecated casing alias (TS spelling) — canonical is SQLiteSessionStore
    "SummarizingSession",
    "llm_summarizer",
    "evaluate",
    "EvalCase",
    "EvalReport",
    "EvalResult",
    # result model
    "Result",
    "Run",
    "Step",
    "ParsedResponse",
    "ToolInvocation",
    "MissingAPIKeyError",
    # streaming events (run.stream / run.astream)
    "StreamEvent",
    "TextDelta",
    "ThinkingDelta",
    "ToolCallEvent",
    "ToolResultEvent",
    "RunComplete",
    # governance (the identical tokenguard/acttrace objects, re-exported — incl. `guard`, whose
    # dual-shape return since acttrace 1.5.0 makes the scope form a library feature;
    # `register_model_price` is SDK-owned)
    "budget",
    "track",
    "report",
    "configure",
    "downgrades",
    "clamps",
    "register_model_price",
    "BudgetExceeded",
    "BudgetEvent",
    "guard",
    "PolicyViolation",
    "Policy",
    "AuditLog",
    "OTelMirror",
    "verify",
    # guardrails gate (cendor-guardrails objects, re-exported; `rules` is the SDK's superset
    # module — see the note at the top of this file)
    "Guardrail",
    "guardrail",
    "GuardrailTripped",
    "GuardrailDecision",
    "Verdict",
    "judge",
    "task_adherence",
    "load_policy",
    "LoadedPolicy",
    "policy_schema",
    "presets",
    "rules",
    # correlation + event types (cendor-core, re-exported)
    "trace",
    "current_trace_id",
    "LLMCall",
    "ToolCall",
    "Usage",
    "Money",
    "__version__",
]
