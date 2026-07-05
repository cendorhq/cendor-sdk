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
from cendor.acttrace import AuditLog, Policy, verify

# --- correlation (cendor-core) ------------------------------------------------------------------
from cendor.core import current_trace_id, trace

# --- budgets + attribution (cendor-tokenguard, re-exported) -------------------------------------
from cendor.tokenguard import BudgetExceeded, budget, configure, report, track

from ._governance import guard

# --- the SDK -----------------------------------------------------------------------------------
from .a2a import A2AClient, A2AServer
from .agent import Agent
from .checkpoint import Checkpointer
from .embeddings import aembed, embed
from .eval import EvalCase, EvalReport, EvalResult, evaluate
from .foundry import FoundryAdapter
from .hitl import require_approval
from .mcp import get_mcp_prompt, load_mcp_prompts, load_mcp_resources, load_mcp_tools
from .memory import Session, SQLiteSessionStore, SummarizingSession, llm_summarizer
from .orchestration import (
    Handoff,
    handoff,
    parallel,
    parallel_async,
    sequential,
    supervisor,
)
from .otel import span_tree
from .pricing import register_model_price
from .providers import ParsedResponse, ToolInvocation
from .rag import Hit, VectorIndex
from .resilience import RetryPolicy
from .result import (
    Result,
    RunComplete,
    Step,
    StreamEvent,
    TextDelta,
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
    # orchestration (Phase 2)
    "handoff",
    "Handoff",
    "sequential",
    "parallel",
    "parallel_async",
    "supervisor",
    # interop (Phase 3)
    "load_mcp_tools",
    "load_mcp_prompts",
    "get_mcp_prompt",
    "load_mcp_resources",
    "A2AServer",
    "A2AClient",
    "FoundryAdapter",
    "span_tree",
    "require_approval",
    # hardening + eval (Phase 4)
    "RetryPolicy",
    "Checkpointer",
    "SQLiteSessionStore",
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
    # streaming events (run.stream / run.astream)
    "StreamEvent",
    "TextDelta",
    "ToolCallEvent",
    "ToolResultEvent",
    "RunComplete",
    # governance (the real tokenguard/acttrace objects, re-exported)
    "budget",
    "track",
    "report",
    "configure",
    "register_model_price",
    "BudgetExceeded",
    "guard",
    "Policy",
    "AuditLog",
    "verify",
    # correlation
    "trace",
    "current_trace_id",
    "__version__",
]
