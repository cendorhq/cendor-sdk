"""Governed eval / regression harness (plan §7 Phase 4) — the testing wedge fully realized.

Replay recorded agent trajectories (cassettes) as **tests** and assert they don't regress: the
output, the **tool sequence**, and **cost/token ceilings**. Because cassette replay re-emits the
recorded usage, cost and tokens are real on replay — so an eval suite gates *behaviour and spend*
regressions in CI, offline. Seeds a possible future ``cendor-eval``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .agent import Agent
from .runner import run


@dataclass
class EvalCase:
    """One recorded trajectory + its expectations."""

    name: str
    input: Any
    cassette: str
    expect_output: str | None = None
    expect_contains: str | None = None
    expect_tools: list[str] | None = None
    max_usd: float | None = None
    max_tokens: int | None = None


@dataclass
class EvalResult:
    """The outcome of evaluating one case."""

    name: str
    passed: bool
    failures: list[str]
    output: Any
    cost_usd: Decimal
    tokens: int
    tools: list[str]


@dataclass
class EvalReport:
    """The outcome of an eval suite."""

    results: list[EvalResult] = field(default_factory=list)

    @property
    def passed(self) -> list[EvalResult]:
        return [r for r in self.results if r.passed]

    @property
    def failed(self) -> list[EvalResult]:
        return [r for r in self.results if not r.passed]

    @property
    def ok(self) -> bool:
        return not self.failed

    def assert_ok(self) -> None:
        """Raise ``AssertionError`` listing every failure (use this in a CI test)."""
        if self.failed:
            lines = [f"  - {r.name}: {'; '.join(r.failures)}" for r in self.failed]
            raise AssertionError(
                f"{len(self.failed)}/{len(self.results)} eval case(s) failed:\n" + "\n".join(lines)
            )

    def __str__(self) -> str:
        head = f"eval: {len(self.passed)}/{len(self.results)} passed"
        rows = [
            f"  {'PASS' if r.passed else 'FAIL'} {r.name}  "
            f"(${r.cost_usd}, {r.tokens} tok, tools={r.tools})"
            + ("" if r.passed else "  <- " + "; ".join(r.failures))
            for r in self.results
        ]
        return head + "\n" + "\n".join(rows)


def _evaluate_one(agent: Agent, case: EvalCase) -> EvalResult:
    from cendor import cassette

    with cassette.using(case.cassette, mode="replay"):
        result = run(agent, case.input)

    tools = [s.name for s in result.tool_steps]
    cost = result.cost.amount
    tokens = result.usage.total_tokens
    failures: list[str] = []

    if case.expect_output is not None and str(result.output) != case.expect_output:
        failures.append(f"output {str(result.output)!r} != {case.expect_output!r}")
    if case.expect_contains is not None and case.expect_contains not in str(result.output):
        failures.append(f"output missing {case.expect_contains!r}")
    if case.expect_tools is not None and tools != case.expect_tools:
        failures.append(f"tool sequence {tools} != {case.expect_tools}")
    if case.max_usd is not None and cost > Decimal(str(case.max_usd)):
        failures.append(f"cost ${cost} > ${case.max_usd} ceiling (regression)")
    if case.max_tokens is not None and tokens > case.max_tokens:
        failures.append(f"tokens {tokens} > {case.max_tokens} ceiling (regression)")

    return EvalResult(
        name=case.name,
        passed=not failures,
        failures=failures,
        output=result.output,
        cost_usd=cost,
        tokens=tokens,
        tools=tools,
    )


def evaluate(agent: Agent, cases: list[EvalCase]) -> EvalReport:
    """Replay each case's cassette against ``agent`` and check output/tools/cost/token ceilings."""
    return EvalReport(results=[_evaluate_one(agent, case) for case in cases])
