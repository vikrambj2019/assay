"""Sequential scenario executor for `bta check` runs.

Runs each scenario in the plan in topological (dependency) order.
For each scenario:
  1. Pre-execution gate (Task 12): SKIPPED / BLOCKED checks run first.
  2. Goal execution: the injectable ScenarioAdapter runs the scenario's
     goal in the browser (navigates, interacts with the application).
  3. Assertion checks: every assertion in the scenario is evaluated
     deterministically where possible; semantic ones become UNVERIFIED.
  4. Scenario verdict: derived from assertion outcomes (see _scenario_verdict).

The executor itself never silently weakens expectations — expected outcomes
are fixed at plan-load time and cannot change during execution.  A failed
prerequisite always blocks its dependents; explicit completion requires all
required assertions to have results.

For unit tests, inject a ``FakeScenarioAdapter`` so goal execution returns
a pre-configured page state without a real browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.assertions import AssertionOutcome, evaluate_assertion
from core.budget import BudgetExhausted, RunBudget
from core.plan import Assertion, AssertionKind, Plan, Scenario, validate_plan
from core.policy import MutationPolicy
from core.run import (
    ScenarioResult,
    execution_order,
    generate_run_id,
    pre_check_scenario,
)
from core.schema import Verdict


# ── Adapter protocol ──────────────────────────────────────────────────────────

class ScenarioAdapter(Protocol):
    """Injectable interface combining goal execution and assertion checking.

    ``run_goal`` drives the browser to accomplish the scenario's goal.
    The assertion-checking methods read the resulting browser state.

    Implementations:
      - Real: wraps a Playwright page + agent loop.
      - Test: ``FakeScenarioAdapter`` with pre-configured page state.
    """

    async def run_goal(self, scenario: Scenario) -> None:
        """Execute the scenario's NL goal.  Raise on infrastructure failure."""
        ...

    async def current_url(self) -> str: ...
    async def page_text(self) -> str: ...
    async def field_value(self, selector: str) -> "str | None": ...
    async def is_element_visible(self, selector: str) -> bool: ...
    async def reload(self) -> None: ...


class ScenarioAdapterFactory(Protocol):
    """Creates a fresh ScenarioAdapter for each scenario execution."""

    async def create(self, scenario: Scenario) -> ScenarioAdapter:
        """Return an adapter ready to run *scenario*."""
        ...


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    """Aggregate result of executing all scenarios in a plan."""

    run_id: str
    scenario_results: list[ScenarioResult]
    complete: bool  # True when every non-skipped required scenario has a result


# ── Public entry point ────────────────────────────────────────────────────────

async def run_plan(
    plan: Plan,
    mutation_policy: MutationPolicy,
    adapter_factory: ScenarioAdapterFactory,
    run_id: str | None = None,
    budget: RunBudget | None = None,
) -> RunResult:
    """Execute *plan* sequentially in dependency order.

    Each scenario is either:
    - Gated (SKIPPED / BLOCKED) by ``pre_check_scenario`` before touching the browser.
    - Executed: goal is run, then all assertions are evaluated.

    The run_id is included in every ScenarioResult for artifact correlation.
    Partial results are preserved even when a later scenario errors.

    When *budget* is provided it is checked before each scenario.  If the budget
    is exhausted mid-run, the current and all remaining required scenarios are
    recorded as UNVERIFIED with the budget reason; the run is then marked
    incomplete (``complete=False``).

    Args:
        plan:            Validated plan (not mutated).
        mutation_policy: Whether mutation scenarios are permitted.
        adapter_factory: Provides a ScenarioAdapter per scenario.
        run_id:          Optional pre-generated ID; one is minted when absent.
        budget:          Optional RunBudget; when absent no budget is enforced.

    Returns:
        RunResult with all per-scenario results and a completeness flag.
    """
    if run_id is None:
        run_id = generate_run_id()

    validate_plan(plan)

    order = execution_order(plan)
    completed: dict[str, Verdict] = {}
    results: list[ScenarioResult] = []
    budget_reason: str | None = None

    for scenario in order:
        # If budget was already exhausted, mark remaining scenarios UNVERIFIED.
        if budget_reason is not None:
            result = ScenarioResult(
                scenario_id=scenario.id,
                scenario_title=scenario.title,
                verdict=Verdict.UNVERIFIED,
                reason=budget_reason,
                run_id=run_id,
            )
            completed[scenario.id] = result.verdict
            results.append(result)
            continue

        # Check budget before starting this scenario.
        if budget is not None:
            try:
                budget.check()
            except BudgetExhausted as exc:
                budget_reason = str(exc)
                result = ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_title=scenario.title,
                    verdict=Verdict.UNVERIFIED,
                    reason=budget_reason,
                    run_id=run_id,
                )
                completed[scenario.id] = result.verdict
                results.append(result)
                continue

        gate = pre_check_scenario(scenario, mutation_policy, completed)
        if gate is not None:
            verdict, reason = gate
            result = ScenarioResult(
                scenario_id=scenario.id,
                scenario_title=scenario.title,
                verdict=verdict,
                reason=reason,
                run_id=run_id,
            )
        else:
            try:
                result = await _execute_scenario(scenario, run_id, adapter_factory)
            except BudgetExhausted as exc:
                budget_reason = str(exc)
                result = ScenarioResult(
                    scenario_id=scenario.id,
                    scenario_title=scenario.title,
                    verdict=Verdict.UNVERIFIED,
                    reason=budget_reason,
                    run_id=run_id,
                )

        completed[scenario.id] = result.verdict
        results.append(result)

    # A run is complete when every non-skipped scenario has a definitive result
    # (i.e., no UNVERIFIED required assertions remain).
    non_skipped = [r for r in results if r.verdict is not Verdict.SKIPPED]
    complete = (
        budget_reason is None
        and all(r.verdict is not Verdict.UNVERIFIED for r in non_skipped)
    )

    return RunResult(run_id=run_id, scenario_results=results, complete=complete)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _execute_scenario(
    scenario: Scenario,
    run_id: str,
    factory: ScenarioAdapterFactory,
) -> ScenarioResult:
    """Run one scenario: goal → assertions → verdict."""
    try:
        adapter = await factory.create(scenario)
    except Exception as exc:  # noqa: BLE001
        return ScenarioResult(
            scenario_id=scenario.id,
            scenario_title=scenario.title,
            verdict=Verdict.ERROR,
            reason=f"failed to create scenario adapter: {exc}",
            run_id=run_id,
        )

    try:
        await adapter.run_goal(scenario)
    except BudgetExhausted:
        close = getattr(adapter, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
        raise  # propagate to run_plan for uniform handling
    except Exception as exc:  # noqa: BLE001
        close = getattr(adapter, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
        return ScenarioResult(
            scenario_id=scenario.id,
            scenario_title=scenario.title,
            verdict=Verdict.ERROR,
            reason=f"scenario goal execution failed: {exc}",
            run_id=run_id,
        )

    close_error: Exception | None = None
    outcomes = await _check_assertions(scenario, adapter)
    close = getattr(adapter, "close", None)
    if close is not None:
        try:
            await close()
        except Exception as exc:  # noqa: BLE001
            close_error = exc
    if close_error is not None:
        return ScenarioResult(
            scenario_id=scenario.id,
            scenario_title=scenario.title,
            verdict=Verdict.ERROR,
            reason=f"scenario cleanup failed: {close_error}",
            run_id=run_id,
        )
    verdict, reason = _scenario_verdict(outcomes, scenario.assertions)

    return ScenarioResult(
        scenario_id=scenario.id,
        scenario_title=scenario.title,
        verdict=verdict,
        reason=reason,
        run_id=run_id,
        assertions_checked=[o.assertion_id for o in outcomes],
        assertion_evidence={
            o.assertion_id: (o.evidence or o.reason)
            for o in outcomes
            if o.evidence or o.reason
        },
    )


async def _check_assertions(
    scenario: Scenario,
    adapter: ScenarioAdapter,
) -> list[AssertionOutcome]:
    """Evaluate every assertion in *scenario* against the current browser state."""
    outcomes: list[AssertionOutcome] = []
    for assertion in scenario.assertions:
        outcome = await evaluate_assertion(assertion, adapter)
        outcomes.append(outcome)
    return outcomes


def _scenario_verdict(
    outcomes: list[AssertionOutcome],
    assertions: list[Assertion],
) -> tuple[Verdict, str]:
    """Derive the scenario verdict from its assertion outcomes.

    Priority (highest first):
      FAIL   — any assertion fails (observed mismatch)
      ERROR  — any assertion could not be evaluated (infrastructure gap)
      UNVERIFIED — any REQUIRED assertion is unverified (can't confirm outcome)
      PASS   — all assertions have results and none failed

    ASSUMPTION / EXPLORATORY assertions that are UNVERIFIED do not block a
    PASS verdict; only REQUIRED unverified assertions do.
    """
    if not outcomes:
        return Verdict.UNVERIFIED, "no assertions were evaluated"

    # FAIL takes precedence over everything
    for o in outcomes:
        if o.verdict is Verdict.FAIL:
            return Verdict.FAIL, o.reason

    # ERROR next
    for o in outcomes:
        if o.verdict is Verdict.ERROR:
            return Verdict.ERROR, o.reason

    # UNVERIFIED on REQUIRED assertions blocks PASS
    for o, a in zip(outcomes, assertions):
        if o.verdict is Verdict.UNVERIFIED and a.kind is AssertionKind.REQUIRED:
            return Verdict.UNVERIFIED, o.reason

    # All assertions accounted for; assumption/exploratory UNVERIFIED is noted
    unverified = [o for o in outcomes if o.verdict is Verdict.UNVERIFIED]
    if unverified:
        # Non-required UNVERIFIED — still report it but don't fail the scenario
        note = "; ".join(o.assertion_id for o in unverified)
        return Verdict.PASS, (
            f"{len(outcomes) - len(unverified)} assertion(s) passed; "
            f"{len(unverified)} non-required assertion(s) unverified: {note}"
        )

    return Verdict.PASS, f"all {len(outcomes)} assertion(s) passed"
