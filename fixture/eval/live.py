"""Opt-in live evaluation adapter.

The harness deliberately does not manufacture credentials or start a model from
unit tests.  A trusted runner supplies an async ``evaluate`` callback that runs
the real browser agent against one fixture variant and returns a verdict plus
usage metadata.  The environment gate prevents accidental paid runs.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

from core.schema import Verdict
from fixture.eval.bugs import BugSpec
from fixture.eval.harness import EvalReport, EvalScenarioResult

LiveOutcome = tuple[Verdict, int, float]
LiveEvaluator = Callable[[BugSpec, bool], Awaitable[LiveOutcome]]


async def run_live_eval(
    bugs: list[BugSpec],
    evaluate: LiveEvaluator,
    *,
    model: str,
    model_version: str,
    configuration: dict | None = None,
    run_count: int = 1,
) -> EvalReport:
    """Score credentialed real-model runs supplied by *evaluate*.

    ``evaluate(spec, broken)`` must return ``(verdict, interventions, cost)``.
    Live execution is opt-in through ``ASSAY_EVAL_LIVE=1`` and requires explicit
    model metadata.  Ground-truth labels remain in this process and are never
    passed to the evaluator callback.
    """
    if os.environ.get("ASSAY_EVAL_LIVE") != "1":
        raise RuntimeError("live evaluation is disabled; set ASSAY_EVAL_LIVE=1")
    if run_count < 1:
        raise ValueError("run_count must be at least 1")
    if not model or not model_version:
        raise ValueError("model and model_version are required for live reports")

    started = time.perf_counter()
    all_results: list[EvalScenarioResult] = []
    interventions = 0
    cost = 0.0
    for _ in range(run_count):
        for spec in bugs:
            clean, ci, cc = await evaluate(spec, False)
            broken, bi, bc = await evaluate(spec, True)
            interventions += ci + bi
            cost += cc + bc
            ambiguous = spec.bug_id.startswith("AMBIGUOUS")
            error = clean is Verdict.ERROR or broken is Verdict.ERROR
            false_positive = clean is Verdict.FAIL
            detected = (not ambiguous and not error and broken is Verdict.FAIL
                        and not false_positive)
            all_results.append(EvalScenarioResult(
                bug_id=spec.bug_id, flow=spec.flow,
                clean_verdict=clean, broken_verdict=broken,
                detected=detected, false_positive=false_positive,
                harness_error=error, is_ambiguous=ambiguous,
            ))

    detected_count = sum(r.detected for r in all_results)
    false_positive_count = sum(r.false_positive for r in all_results)
    harness_error_count = sum(r.harness_error for r in all_results)
    ambiguous_unverified = sum(
        r.is_ambiguous and r.broken_verdict is Verdict.UNVERIFIED for r in all_results
    )
    ambiguous_wrong = sum(
        r.is_ambiguous and r.broken_verdict is not Verdict.UNVERIFIED for r in all_results
    )
    missed_count = sum(
        not r.is_ambiguous and not r.harness_error and not r.detected
        and not r.false_positive for r in all_results
    )
    # A consistency match means the verdict pair matches the first run for the
    # same bug. This is a count, with its denominator published explicitly.
    consistency_matches = 0
    consistency_size = 0
    if run_count > 1:
        first = {(r.bug_id, r.clean_verdict, r.broken_verdict): r for r in all_results[:len(bugs)]}
        for r in all_results[len(bugs):]:
            consistency_size += 1
            consistency_matches += (r.clean_verdict, r.broken_verdict) == next(
                (k[1:] for k in first if k[0] == r.bug_id), (None, None)
            )
    return EvalReport(
        mode="live", model=model, model_version=model_version,
        configuration=configuration or {}, run_count=run_count,
        sample_size=len(bugs) * run_count, results=all_results,
        detected_count=detected_count, missed_count=missed_count,
        false_positive_count=false_positive_count,
        harness_error_count=harness_error_count,
        ambiguous_unverified_count=ambiguous_unverified,
        ambiguous_incorrectly_resolved_count=ambiguous_wrong,
        runtime_seconds=time.perf_counter() - started,
        intervention_count=interventions, cost_usd=cost,
        consistency_matches=consistency_matches,
        consistency_sample_size=consistency_size,
    )
