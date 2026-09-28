"""Sequential scenario execution skeleton for `assay check` runs.

Provides:
- ``generate_run_id``         — collision-resistant run identifier included in
                                every result record so artifacts are traceable.
- ``ScenarioResult``          — one scenario's execution outcome.
- ``execution_order``         — topological sort of scenarios by prerequisites.
- ``pre_check_scenario``      — pre-execution policy gate (skip / mutation /
                                blocked-prerequisite); returns (Verdict, reason)
                                when the scenario must not run, None when ok.

The full scenario executor (browser launch, assertion checking) is in Task 13.
This module handles only the sequential ordering and pre-execution gating so
policy decisions are unit-testable without a browser.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from core.plan import Plan, Scenario
from core.policy import MutationPolicy
from core.schema import Verdict, StepLog
from core.assertions import AssertionOutcome


# ── Run identity ──────────────────────────────────────────────────────────────

def generate_run_id() -> str:
    """Return a UUID4-based run identifier.

    Included in every ScenarioResult so log lines, artifacts, and result
    records from the same run can be correlated even when multiple runs
    share an output directory.
    """
    return str(uuid.uuid4())


# ── Per-scenario result ───────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    """Execution outcome for one scenario in a check run.

    ``run_id`` ties every result to its originating run.  Future fields
    (started_at, completed_at, action_records, evidence) are added in
    Task 13 when the executor is implemented.
    """

    scenario_id: str
    scenario_title: str
    verdict: Verdict
    reason: str
    run_id: str
    assertions_checked: list[str] = field(default_factory=list)
    assertion_evidence: dict[str, str] = field(default_factory=dict)
    assertion_outcomes: list[AssertionOutcome] = field(default_factory=list)
    stages: list[StepLog] = field(default_factory=list)


# ── Topological ordering ──────────────────────────────────────────────────────

def execution_order(plan: Plan) -> list[Scenario]:
    """Return scenarios sorted so every prerequisite precedes its dependents.

    ``validate_plan`` guarantees no cycles and no unknown IDs, so this DFS
    visit will always terminate.  The relative order of independent scenarios
    is preserved (first-seen wins, matching plan order).
    """
    lookup = {s.id: s for s in plan.scenarios}
    visited: set[str] = set()
    result: list[Scenario] = []

    def visit(sid: str) -> None:
        if sid in visited:
            return
        for dep in lookup[sid].prerequisites:
            visit(dep)
        visited.add(sid)
        result.append(lookup[sid])

    for sid in lookup:
        visit(sid)

    return result


# ── Pre-execution gate ────────────────────────────────────────────────────────

def pre_check_scenario(
    scenario: Scenario,
    mutation_policy: MutationPolicy,
    completed: dict[str, Verdict],
) -> tuple[Verdict, str] | None:
    """Decide whether *scenario* should be blocked before execution.

    Checks (in order):
    1. ``skip=True``                      → SKIPPED (user-requested, not a shortcut)
    2. ``requires_mutations`` + policy    → BLOCKED  (mutation policy)
    3. Any prerequisite non-PASS          → BLOCKED  (dependency failure)

    Returns:
        ``(Verdict, reason)`` when the scenario must not run.
        ``None``              when it is safe to proceed.

    Args:
        scenario:        The scenario to check.
        mutation_policy: Resolved mutation policy for this run.
        completed:       Map of already-executed scenario IDs to their verdicts.
    """
    # 1. Explicit skip
    if scenario.skip:
        return Verdict.SKIPPED, "scenario marked skip=true — excluded by the user"

    # 2. Mutation policy gate
    allowed, reason = mutation_policy.check_scenario(scenario)
    if not allowed:
        return Verdict.BLOCKED, reason

    # 3. Prerequisite results
    for dep_id in scenario.prerequisites:
        dep_verdict = completed.get(dep_id)
        if dep_verdict is None:
            # Should not happen after execution_order(), but guard anyway
            return Verdict.BLOCKED, (
                f"prerequisite {dep_id!r} has not been executed yet"
            )
        if dep_verdict is not Verdict.PASS:
            return Verdict.BLOCKED, (
                f"prerequisite {dep_id!r} did not pass "
                f"(status: {dep_verdict.value})"
            )

    return None  # scenario may proceed
