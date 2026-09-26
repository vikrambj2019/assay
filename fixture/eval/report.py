"""Evaluation report writer for the regression fixture.

Writes ``eval_report.json`` containing the full evaluation results.

Design constraints:
- Raw counts only; no percentages are computed or stored.
- Live-model results MUST include model, model_version, and run_count.
- Harness errors are recorded separately from missed bugs — a run that
  ERROR'd on a check is not the same as a missed detection.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fixture.eval.harness import EvalReport

REPORT_VERSION = "1"


def write_eval_report(report: "EvalReport", out_dir: Path) -> Path:
    """Write ``eval_report.json`` to *out_dir* and return the path.

    The file is versioned.  Counts are included as-is; callers that compute
    percentages from these numbers must also include ``sample_size`` and
    ``run_count`` in any published result.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    scenario_rows = [
        {
            "bug_id": r.bug_id,
            "flow": r.flow,
            "clean_verdict": r.clean_verdict.value,
            "broken_verdict": r.broken_verdict.value,
            "detected": r.detected,
            "false_positive": r.false_positive,
            "harness_error": r.harness_error,
            "is_ambiguous": r.is_ambiguous,
        }
        for r in report.results
    ]

    doc = {
        "version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": report.mode,
        "model": report.model,
        "model_version": report.model_version,
        "configuration": report.configuration,
        "run_count": report.run_count,
        "sample_size": report.sample_size,
        # Counts — never divide these to produce percentages without also
        # including sample_size and run_count in the published result.
        "detected_count": report.detected_count,
        "missed_count": report.missed_count,
        "false_positive_count": report.false_positive_count,
        "harness_error_count": report.harness_error_count,
        "ambiguous_unverified_count": report.ambiguous_unverified_count,
        "ambiguous_incorrectly_resolved_count": report.ambiguous_incorrectly_resolved_count,
        "runtime_seconds": report.runtime_seconds,
        "intervention_count": report.intervention_count,
        "cost_usd": report.cost_usd,
        "consistency_matches": report.consistency_matches,
        "consistency_sample_size": report.consistency_sample_size,
        "scenarios": scenario_rows,
    }

    path = out_dir / "eval_report.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
