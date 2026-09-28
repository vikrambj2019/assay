"""Report writers for `assay check` runs.

Produces three artifacts from one canonical RunResult:

  results.json  — versioned, machine-readable summary (plan + outcomes + budget)
  report.html   — self-contained HTML with evidence, escaped application text
  junit.xml     — JUnit-compatible XML understood by most CI systems

All writers accept an optional ``CheckContext`` (for input-source provenance)
and an optional ``RunBudget`` (for budget/usage summary).  Omitting them
produces valid but incomplete output — useful when writing partial results
during error cleanup.

Exit-code contract
------------------
``exit_code(run_result)`` returns:
  0  — all non-skipped scenarios PASS, run is complete, no errors/blocks.
  1  — at least one confirmed FAIL.
  2  — no confirmed FAIL but incomplete, ERROR, BLOCKED, or UNVERIFIED
       scenarios remain (the run cannot be treated as clean).

"Invalid inputs fail cleanly without requiring a report directory" means
the callers of these writers must not be reached when config validation fails.
"Started runs always retain available results" means callers must invoke
these writers even when execution throws — the partial results are still
valid artifacts.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from core.plan import AssertionKind, Plan
from core.run import ScenarioResult
from core.executor import RunResult
from core.schema import Verdict
from core.redact import make_redactor

if TYPE_CHECKING:
    from core.budget import RunBudget
    from core.context import CheckContext


RESULTS_VERSION = "1"


# ── Exit code ─────────────────────────────────────────────────────────────────

def exit_code(run_result: RunResult) -> int:
    """Derive the process exit code from a completed run result.

    Priority:
      1  — at least one confirmed FAIL (app misbehaved).
      2  — no FAIL but run is incomplete, or any scenario is ERROR / BLOCKED.
      0  — all non-SKIPPED scenarios PASS, run is complete.
    """
    verdicts = {r.verdict for r in run_result.scenario_results}
    if Verdict.FAIL in verdicts:
        return 1
    if not run_result.complete:
        return 2
    if Verdict.ERROR in verdicts or Verdict.BLOCKED in verdicts:
        return 2
    return 0


# ── results.json ──────────────────────────────────────────────────────────────

def _stage_json(stage, out_dir: Path) -> dict:
    row = asdict(stage)
    for action in row["action_records"]:
        shot = action["screenshot"]
        if shot is not None:
            try:
                action["screenshot"] = str(Path(shot).resolve().relative_to(out_dir.resolve()))
            except ValueError:
                action["screenshot"] = None
    return row


def write_results_json(
    run_result: RunResult,
    plan: Plan,
    out_dir: Path,
    ctx: "CheckContext | None" = None,
    budget: "RunBudget | None" = None,
    plan_info: "dict | None" = None,
    selection: "dict | None" = None,
) -> Path:
    """Write ``results.json`` to *out_dir* and return the path.

    ``plan_info`` records which plan file was executed (path, sha256, and
    whether it was generated or loaded with ``--plan``). ``selection`` records
    an ``--only`` subset. Both fields are additive within version 1.

    The file is versioned so consumers can detect format changes.  Application
    text in ``reason`` fields is included verbatim (consumers should escape
    for their output context).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    verdicts = [r.verdict for r in run_result.scenario_results]
    summary = {v.value: verdicts.count(v) for v in Verdict if verdicts.count(v) > 0}
    summary["total"] = len(verdicts)

    # Per-scenario output with assertion-kind annotation from the plan.
    assertion_kinds = _assertion_kind_map(plan)
    scenario_rows = []
    for r in run_result.scenario_results:
        row: dict = {
            "scenario_id": r.scenario_id,
            "title": r.scenario_title,
            "verdict": r.verdict.value,
            "reason": r.reason,
            "assertions_checked": r.assertions_checked,
            "assertion_evidence": dict(r.assertion_evidence),
            "assertion_results": [asdict(o) for o in r.assertion_outcomes],
            "stages": [_stage_json(stage, out_dir) for stage in r.stages],
        }
        video = scenario_video(out_dir, r.scenario_id)
        if video:
            row["video"] = video
        if r.assertions_checked:
            row["assertion_kinds"] = {
                aid: assertion_kinds.get(aid, "unknown")
                for aid in r.assertions_checked
            }
        scenario_rows.append(row)

    doc: dict = {
        "version": RESULTS_VERSION,
        "run_id": run_result.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "complete": run_result.complete,
        "exit_code": exit_code(run_result),
        "depth": plan.depth,
        "plan_created_at": plan.created_at,
        "plan_scenario_count": len(plan.scenarios),
        "coverage_suggestions": list(plan.coverage_suggestions),
        "context": _context_summary(ctx),
        "budget": budget.summary() if budget is not None else None,
        "scenarios": scenario_rows,
        "summary": summary,
    }
    if plan_info is not None:
        doc["plan"] = plan_info
    if selection is not None:
        doc["selection"] = selection

    path = out_dir / "results.json"
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    path.write_text(make_redactor().scrub(text), encoding="utf-8")
    return path


# ── report.html ───────────────────────────────────────────────────────────────

_COLOR = {
    Verdict.PASS:       "#1a7f37",
    Verdict.FAIL:       "#cf222e",
    Verdict.ERROR:      "#bf8700",
    Verdict.BLOCKED:    "#6e7781",
    Verdict.SKIPPED:    "#6e7781",
    Verdict.UNVERIFIED: "#9a6700",
}

_CSS = """
body{font:14px/1.6 system-ui,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}
header{padding:20px 28px;background:#fff;border-bottom:1px solid #d0d7de}
h1{margin:0 0 6px;font-size:20px}h2{font-size:15px;margin:18px 0 8px}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;color:#fff;
       font-weight:600;font-size:12px;white-space:nowrap}
.meta{color:#6e7781;font-size:12px;margin:4px 0}
main{padding:20px 28px;max-width:960px}
.card{background:#fff;border:1px solid #d0d7de;border-radius:8px;
      margin:0 0 14px;overflow:hidden}
.card-head{display:flex;align-items:center;gap:10px;padding:10px 16px;
           border-bottom:1px solid #eaeef2}
.card-head .title{font-weight:600;flex:1}
.card-body{padding:12px 16px;font-size:13px}
.reason{margin:0 0 8px;color:#57606a}
.goal{margin:0 0 8px;font-style:italic;color:#1f2328}
.assertions{margin:0 0 8px;padding-left:18px;color:#57606a}
.ctx{background:#fff;border:1px solid #d0d7de;border-radius:8px;
     padding:12px 16px;margin:0 0 18px;font-size:13px}
.ctx dt{font-weight:600;color:#1f2328;float:left;min-width:110px}
.ctx dd{margin:0 0 4px 110px;color:#57606a}
.suggestions{padding-left:18px;color:#57606a;font-size:13px}
.budget{background:#fff8ed;border:1px solid #d0a000;border-radius:8px;
        padding:10px 16px;margin:0 0 18px;font-size:13px}
.rec{width:100%;max-width:880px;border:1px solid #d0d7de;border-radius:6px;margin:8px 0 0}
.pill{display:inline-block;padding:1px 7px;border-radius:8px;font-size:11px;
      font-weight:600;border:1px solid currentColor;margin-right:4px}
"""

def write_check_html(
    run_result: RunResult,
    plan: Plan,
    out_dir: Path,
    ctx: "CheckContext | None" = None,
    budget: "RunBudget | None" = None,
) -> Path:
    """Write self-contained ``report.html`` to *out_dir* and return the path.

    All application-sourced text is HTML-escaped.  No external resources.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    code = exit_code(run_result)
    code_label = {0: "PASS", 1: "FAIL", 2: "INCOMPLETE/ERROR"}[code]
    code_color = {0: "#1a7f37", 1: "#cf222e", 2: "#bf8700"}[code]

    # ── header ──────────────────────────────────────────────────────────────
    counts_html = " ".join(
        f"<span class='badge' style='background:{_COLOR[v]}'>"
        f"{escape(v.value)} {sum(1 for r in run_result.scenario_results if r.verdict is v)}"
        f"</span>"
        for v in Verdict
        if any(r.verdict is v for r in run_result.scenario_results)
    )
    complete_label = "complete" if run_result.complete else "incomplete"
    header_html = (
        f"<header>"
        f"<h1>assay check &mdash; "
        f"<span style='color:{code_color}'>{escape(code_label)}</span></h1>"
        f"<div style='margin:6px 0'>{counts_html}</div>"
        f"<div class='meta'>run&nbsp;{escape(run_result.run_id)} &bull; "
        f"{escape(plan.depth)}&nbsp;depth &bull; "
        f"{len(plan.scenarios)}&nbsp;scenario(s) &bull; "
        f"{escape(complete_label)}</div>"
        f"</header>"
    )

    # ── context section ──────────────────────────────────────────────────────
    ctx_html = _context_html(ctx)

    # ── budget section ───────────────────────────────────────────────────────
    budget_html = _budget_html(budget)

    # ── scenario cards ───────────────────────────────────────────────────────
    assertion_kinds = _assertion_kind_map(plan)
    plan_lookup = {s.id: s for s in plan.scenarios}
    cards_html = "\n".join(
        _scenario_card(r, plan_lookup.get(r.scenario_id), assertion_kinds,
                       scenario_video(out_dir, r.scenario_id), out_dir)
        for r in run_result.scenario_results
    )

    # ── coverage suggestions ─────────────────────────────────────────────────
    suggestions_html = ""
    if plan.coverage_suggestions:
        items = "".join(
            f"<li>{escape(s)}</li>" for s in plan.coverage_suggestions
        )
        suggestions_html = (
            f"<section><h2>Coverage suggestions "
            f"({len(plan.coverage_suggestions)} unselected idea(s))</h2>"
            f"<ul class='suggestions'>{items}</ul></section>"
        )

    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>assay check — {escape(code_label)}</title>"
        f"<style>{_CSS}</style></head>"
        f"<body>{header_html}"
        f"<main>{ctx_html}{budget_html}"
        f"<h2>Scenarios ({len(run_result.scenario_results)})</h2>"
        f"{cards_html}"
        f"{suggestions_html}</main></body></html>"
    )

    path = out_dir / "report.html"
    path.write_text(make_redactor().scrub(doc), encoding="utf-8")
    return path


def _evidence_link(path: str | None, label: str) -> str:
    if not path or Path(path).is_absolute() or ".." in Path(path).parts or ":" in path:
        return ""
    return f' <a href="{escape(path, quote=True)}">{escape(label)}</a>'


def _scenario_card(
    r: ScenarioResult,
    scenario,  # Scenario | None from the plan
    assertion_kinds: dict[str, str],
    video: "str | None" = None,
    out_dir: Path | None = None,
) -> str:
    color = _COLOR[r.verdict]
    badge = f"<span class='badge' style='background:{color}'>{escape(r.verdict.value)}</span>"

    goal_html = ""
    assertions_html = ""
    if scenario is not None:
        goal_html = f"<p class='goal'>Goal: {escape(scenario.goal)}</p>"
        if scenario.assertions:
            items = "".join(
                f"<li>{escape(a.id)}: {escape(a.description)} "
                f"<span class='pill' style='color:{_kind_color(a.kind)}'>"
                f"{escape(a.kind.value)}</span></li>"
                for a in scenario.assertions
            )
            assertions_html = f"<ul class='assertions'>{items}</ul>"

    reason_html = f"<p class='reason'>{escape(r.reason)}</p>" if r.reason else ""
    evidence_html = ""
    if r.assertion_evidence:
        items = "".join(
            f"<li>{escape(aid)}: {escape(value)}</li>"
            for aid, value in r.assertion_evidence.items()
        )
        evidence_html = f"<ul class='evidence'><strong>Evidence</strong>{items}</ul>"
    if r.assertion_outcomes:
        items = []
        for outcome in r.assertion_outcomes:
            items.append(
                f"<li>{escape(outcome.assertion_id)}: <strong>{outcome.verdict.value}</strong> "
                f"({escape(outcome.evaluator)}) — {escape(outcome.reason)} "
                f"<br>Observed: {escape(outcome.evidence)} <br>URL: {escape(outcome.url)}"
                + _evidence_link(outcome.screenshot, "Assertion screenshot") + "</li>"
            )
        evidence_html += "<ul class='assertion-results'>" + "".join(items) + "</ul>"
    for stage in r.stages:
        evidence_html += (
            f"<details><summary>{escape(stage.text)}: {stage.verdict.value}</summary>"
            f"<p>{escape(stage.reason)}</p><pre>{escape(chr(10).join(stage.evidence))}</pre>"
            + "".join(
                f"<p>Action {a['action_id']}: {escape(a['detail'])}"
                + _evidence_link(a['screenshot'], "Action screenshot") + "</p>"
                for a in _stage_json(stage, out_dir or Path('.'))['action_records']
            )
            + "</details>"
        )
    video_html = ""
    if video:
        src = escape(video, quote=True)
        video_html = (
            f"<video class='rec' controls preload='metadata' src='{src}'></video>"
            f"<p class='meta'><a href='{src}'>Download recording</a></p>"
        )

    return (
        f"<div class='card'>"
        f"<div class='card-head'>{badge}"
        f"<span class='title'>{escape(r.scenario_title)}</span>"
        f"<span style='color:#6e7781;font-size:12px'>{escape(r.scenario_id)}</span>"
        f"</div>"
        f"<div class='card-body'>{goal_html}{reason_html}{assertions_html}{evidence_html}{video_html}</div>"
        f"</div>"
    )


def _kind_color(kind: AssertionKind) -> str:
    return {"required": "#cf222e", "assumption": "#9a6700", "exploratory": "#1a7f37"}.get(
        kind.value, "#6e7781"
    )


def _context_html(ctx: "CheckContext | None") -> str:
    if ctx is None:
        return ""
    rows = [f"<dt>Notes</dt><dd>{escape(str(ctx.notes_path))}</dd>"]
    if ctx.readme_text is not None:
        rows.append(f"<dt>README</dt><dd>{escape(str(ctx.readme_path))}</dd>")
    if ctx.diff_ref is not None:
        diff_size = len(ctx.diff_text) if ctx.diff_text else 0
        rows.append(
            f"<dt>Diff</dt><dd>{escape(ctx.diff_ref)} "
            f"({diff_size:,}&nbsp;chars)</dd>"
        )
    if ctx.excluded:
        rows.append(
            f"<dt>Excluded</dt><dd>{len(ctx.excluded)} file(s) "
            f"(secrets/binary)</dd>"
        )
    if ctx.truncated:
        rows.append(f"<dt>Truncated</dt><dd>{len(ctx.truncated)} file(s)</dd>")
    for w in ctx.warnings:
        rows.append(f"<dt>Warning</dt><dd>{escape(w)}</dd>")
    inner = "".join(rows)
    return f"<section><h2>Input sources</h2><dl class='ctx'>{inner}</dl></section>"


def _budget_html(budget: "RunBudget | None") -> str:
    if budget is None:
        return ""
    s = budget.summary()
    lim = s["limits"]
    parts = [
        f"Elapsed: {s['elapsed_seconds']}s",
        f"Actions: {s['actions']}",
        f"Cost: ${s['cost_usd']:.4f}",
    ]
    if lim["max_seconds"] is not None:
        parts[0] += f" / {lim['max_seconds']}s"
    if lim["max_actions"] is not None:
        parts[1] += f" / {lim['max_actions']}"
    if lim["max_cost_usd"] is not None:
        parts[2] += f" / ${lim['max_cost_usd']:.2f}"
    inner = " &bull; ".join(escape(p) for p in parts)
    return f"<div class='budget'><strong>Budget:</strong> {inner}</div>"


# ── junit.xml ─────────────────────────────────────────────────────────────────

def write_junit_xml(
    run_result: RunResult,
    plan: Plan,
    out_dir: Path,
) -> Path:
    """Write ``junit.xml`` to *out_dir* and return the path.

    Each scenario becomes a ``<testcase>``.  Verdict mapping:
      PASS                → plain testcase (no child element)
      FAIL                → <failure>
      ERROR               → <error>
      BLOCKED/UNVERIFIED  → <skipped> (message preserves the distinct status)
      SKIPPED             → <skipped>

    The ``classname`` attribute includes the verdict so CI dashboards that
    bucket by class can distinguish BLOCKED from SKIPPED from UNVERIFIED.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    results = run_result.scenario_results
    n_fail = sum(1 for r in results if r.verdict is Verdict.FAIL)
    n_err  = sum(1 for r in results if r.verdict is Verdict.ERROR)
    n_skip = sum(
        1 for r in results
        if r.verdict in (Verdict.SKIPPED, Verdict.BLOCKED, Verdict.UNVERIFIED)
    )

    suite_attrs = {
        "name":     "assay check",
        "tests":    str(len(results)),
        "failures": str(n_fail),
        "errors":   str(n_err),
        "skipped":  str(n_skip),
    }
    suites = ET.Element("testsuites", suite_attrs)
    suite  = ET.SubElement(suites, "testsuite", suite_attrs)

    for r in results:
        classname = f"assay.check.{r.verdict.value.lower()}"
        safe = make_redactor()
        tc = ET.SubElement(suite, "testcase", {
            "classname": classname,
            "name":      safe.scrub(r.scenario_title),
            "time":      "0",
        })
        if r.verdict is Verdict.FAIL:
            reason = safe.scrub(r.reason)
            ET.SubElement(tc, "failure", {"message": reason}).text = reason
        elif r.verdict is Verdict.ERROR:
            reason = safe.scrub(r.reason)
            ET.SubElement(tc, "error", {"message": reason}).text = reason
        elif r.verdict in (Verdict.SKIPPED, Verdict.BLOCKED, Verdict.UNVERIFIED):
            msg = safe.scrub(f"[{r.verdict.value}] {r.reason}")
            ET.SubElement(tc, "skipped", {"message": msg})

    tree = ET.ElementTree(suites)
    ET.indent(tree, space="  ")
    path = out_dir / "junit.xml"
    with path.open("wb") as fh:
        fh.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
        tree.write(fh, encoding="utf-8", xml_declaration=False)
    return path


# ── Internal helpers ──────────────────────────────────────────────────────────

def scenario_video(out_dir: Path, scenario_id: str) -> "str | None":
    """Relative path of a scenario's recorded video, or None when not recorded."""
    rel = Path(scenario_id) / "video.webm"
    return rel.as_posix() if (out_dir / rel).is_file() else None


def _assertion_kind_map(plan: Plan) -> dict[str, str]:
    """Map assertion ID → kind value string for annotation in reports."""
    kinds: dict[str, str] = {}
    for scenario in plan.scenarios:
        for assertion in scenario.assertions:
            kinds[assertion.id] = assertion.kind.value
    return kinds


def _context_summary(ctx: "CheckContext | None") -> dict:
    if ctx is None:
        return {}
    return {
        "notes_path": str(ctx.notes_path),
        "readme_path": str(ctx.readme_path) if ctx.readme_path else None,
        "diff_ref": ctx.diff_ref,
        "excluded_count": len(ctx.excluded),
        "excluded": [{"path": e.path, "reason": e.reason} for e in ctx.excluded],
        "truncated_count": len(ctx.truncated),
        "truncated": [
            {"path": t.path, "original_chars": t.original_chars, "kept_chars": t.kept_chars}
            for t in ctx.truncated
        ],
        "untracked_files": list(ctx.untracked_files),
        "warnings": list(ctx.warnings),
    }
