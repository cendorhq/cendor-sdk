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

# --- audit + redaction (cendor-acttrace, re-exported) -------------------------------------------
from cendor.acttrace import AuditLog, Policy, verify

# --- correlation (cendor-core) ------------------------------------------------------------------
from cendor.core import current_trace_id, trace

# --- budgets + attribution (cendor-tokenguard, re-exported) -------------------------------------
from cendor.tokenguard import BudgetExceeded, budget, report, track

from ._governance import guard

# --- the SDK -----------------------------------------------------------------------------------
from .a2a import A2AClient, A2AServer
from .agent import Agent
from .checkpoint import Checkpointer
from .eval import EvalCase, EvalReport, EvalResult, evaluate
from .foundry import FoundryAdapter
from .hitl import require_approval
from .mcp import load_mcp_tools
from .memory import Session, SQLiteSessionStore
from .orchestration import (
    Handoff,
    handoff,
    parallel,
    parallel_async,
    sequential,
    supervisor,
)
from .otel import span_tree
from .providers import ParsedResponse, ToolInvocation
from .resilience import RetryPolicy
from .result import Result, Run, Step
from .runner import Runner, run
from .tools import Tool, tool

__version__ = "1.0.0"

__all__ = [
    # agent + loop
    "Agent",
    "tool",
    "Tool",
    "run",
    "Runner",
    "Session",
    # orchestration (Phase 2)
    "handoff",
    "Handoff",
    "sequential",
    "parallel",
    "parallel_async",
    "supervisor",
    # interop (Phase 3)
    "load_mcp_tools",
    "A2AServer",
    "A2AClient",
    "FoundryAdapter",
    "span_tree",
    "require_approval",
    # hardening + eval (Phase 4)
    "RetryPolicy",
    "Checkpointer",
    "SQLiteSessionStore",
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
    # governance (the real tokenguard/acttrace objects, re-exported)
    "budget",
    "track",
    "report",
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
