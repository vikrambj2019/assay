"""Evaluation harness for the synthetic regression fixture.

Supports two modes:

Mocked (default, always runs in CI)
    Uses ``evaluate_assertion`` with ``FakeEvalAdapter`` objects configured
    from the ``BugSpec`` page states.  No browser, no LLM, fully deterministic.

Live (opt-in, requires credentials + running fixture app)
    Activated by setting ``BTA_EVAL_LIVE=1`` in the environment.  Runs actual
    ``bta check`` against a started fixture app instance.  Results MUST record
    model ID, configuration, and run count — percentages from a single run are
    never published.

Scoring
-------
For each BugSpec the harness produces an ``EvalScenarioResult``:

  detected        — broken_verdict is FAIL and clean_verdict is not FAIL.
  missed          — broken_verdict is PASS or UNVERIFIED (and check was defined).
  false_positive  — clean_verdict is FAIL (should never fail on clean app).
  harness_error   — either verdict is ERROR (infrastructure, not a missed bug).

The AMBIGUOUS-001 spec has no ``detection_check``; it is expected to remain
UNVERIFIED.  This is counted separately as ``ambiguous_unverified``.

Metrics are counts only.  Percentages are never computed here; the caller
may compute them from counts + sample_size only when sample_size > 1.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.assertions import evaluate_assertion
from core.plan import Assertion, AssertionKind, SourceRef
from core.schema import Verdict

if TYPE_CHECKING:
    from fixture.eval.bugs import BugSpec


# ── Fake adapter for mocked evaluation ───────────────────────────────────────

class _FakeEvalAdapter:
    """Minimal assertion-checker adapter backed by static page state."""

    def __init__(self, url: str, text: str, text_after_reload: str | None = None) -> None:
        self._url = url
        self._text = text
        self._text_after = text_after_reload if text_after_reload is not None else text
        self._reloaded = False

    async def current_url(self) -> str:
        return self._url

    async def page_text(self) -> str:
        return self._text_after if self._reloaded else self._text

    async def field_value(self, selector: str) -> str | None:
        return None

    async def is_element_visible(self, selector: str) -> bool:
        return False

    async def reload(self) -> None:
        self._reloaded = True


def _make_assertion(check: dict | None, bug_id: str) -> Assertion:
    src = SourceRef(kind="notes", path="fixture/eval/bugs.py", excerpt=bug_id)
    return Assertion(
        id=f"eval-{bug_id}",
        description=f"detection check for {bug_id}",
        kind=AssertionKind.REQUIRED,
        source=src,
        check=check,
    )


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class EvalScenarioResult:
    """Outcome of evaluating one BugSpec in mocked mode."""

    bug_id: str
    flow: str
    clean_verdict: Verdict
    broken_verdict: Verdict
    detected: bool          # bug triggered FAIL on broken app
    false_positive: bool    # FAIL appeared on clean app (problem)
    harness_error: bool     # ERROR on either side (infra, not missed bug)
    is_ambiguous: bool      # True for AMBIGUOUS-* specs


@dataclass
class EvalReport:
    """Aggregate evaluation results.

    Metrics are raw counts; percentages are omitted.  Any live-model run
    must populate ``model``, ``model_version``, and ``run_count`` before
    publishing results.

    For mocked runs, ``model`` and ``model_version`` are ``None`` and
    ``run_count`` is 1.
    """

    mode: str                          # "mocked" or "live"
    model: str | None                  # None for mocked runs
    model_version: str | None          # None for mocked runs
    run_count: int                     # number of independent runs aggregated
    sample_size: int                   # number of BugSpecs evaluated
    results: list[EvalScenarioResult]
    detected_count: int
    missed_count: int
    false_positive_count: int
    harness_error_count: int
    ambiguous_unverified_count: int    # AMBIGUOUS-* specs that remain UNVERIFIED (expected)
    ambiguous_incorrectly_resolved_count: int  # AMBIGUOUS-* specs that are PASS/FAIL (wrong)
    configuration: dict | None = None
    # Operational measurements.  These are raw observations, never inferred rates.
    runtime_seconds: float = 0.0
    intervention_count: int = 0
    cost_usd: float | None = 0.0
    consistency_matches: int = 0
    consistency_sample_size: int = 0


# ── Mocked evaluation ─────────────────────────────────────────────────────────

async def run_mocked_eval(bugs: "list[BugSpec]") -> EvalReport:
    """Run a fully deterministic evaluation against the given bug specs.

    No browser or LLM is used.  The ``_FakeEvalAdapter`` simulates the page
    state that a real browser would observe on the clean vs. broken app.

    Args:
        bugs: List of ``BugSpec`` objects (typically ``fixture.eval.bugs.BUGS``).

    Returns:
        ``EvalReport`` with per-bug verdicts and aggregate counts.
    """
    started = time.perf_counter()
    results: list[EvalScenarioResult] = []

    for spec in bugs:
        assertion = _make_assertion(spec.detection_check, spec.bug_id)
        is_ambiguous = spec.bug_id.startswith("AMBIGUOUS")

        clean_adapter = _FakeEvalAdapter(
            spec.clean_url,
            spec.clean_text,
            spec.clean_text_after_reload,
        )
        broken_adapter = _FakeEvalAdapter(
            spec.broken_url,
            spec.broken_text,
            spec.broken_text_after_reload,
        )

        clean_outcome  = await evaluate_assertion(assertion, clean_adapter)
        broken_outcome = await evaluate_assertion(assertion, broken_adapter)

        cv = clean_outcome.verdict
        bv = broken_outcome.verdict

        harness_error   = cv is Verdict.ERROR or bv is Verdict.ERROR
        false_positive  = cv is Verdict.FAIL
        detected        = (not is_ambiguous) and (not harness_error) and (bv is Verdict.FAIL) and (not false_positive)

        results.append(EvalScenarioResult(
            bug_id=spec.bug_id,
            flow=spec.flow,
            clean_verdict=cv,
            broken_verdict=bv,
            detected=detected,
            false_positive=false_positive,
            harness_error=harness_error,
            is_ambiguous=is_ambiguous,
        ))

    detected_count   = sum(1 for r in results if r.detected)
    missed_count     = sum(
        1 for r in results
        if not r.is_ambiguous and not r.harness_error and not r.detected and not r.false_positive
    )
    fp_count         = sum(1 for r in results if r.false_positive)
    err_count        = sum(1 for r in results if r.harness_error)
    amb_unverified   = sum(
        1 for r in results
        if r.is_ambiguous and r.broken_verdict is Verdict.UNVERIFIED
    )
    amb_wrong        = sum(
        1 for r in results
        if r.is_ambiguous and r.broken_verdict is not Verdict.UNVERIFIED
    )

    return EvalReport(
        mode="mocked",
        model=None,
        model_version=None,
        run_count=1,
        sample_size=len(results),
        results=results,
        detected_count=detected_count,
        missed_count=missed_count,
        false_positive_count=fp_count,
        harness_error_count=err_count,
        ambiguous_unverified_count=amb_unverified,
        ambiguous_incorrectly_resolved_count=amb_wrong,
        runtime_seconds=time.perf_counter() - started,
        intervention_count=0,
        cost_usd=0.0,
        # A mocked run is deterministic; record the observation explicitly so
        # consumers do not mistake run_count=1 for a reliability claim.
        consistency_matches=len(results),
        consistency_sample_size=len(results),
    )
