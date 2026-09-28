"""Machine-readable run summary for `assay check`.

The summary is the stable contract for coding agents and CI:

* printed to stdout with ``--format json`` (and nothing else is printed there);
* always written to ``summary.json`` next to the other artifacts;
* rendered as ``summary.md`` for pasting into a pull-request description.

It is intentionally small enough to sit in an agent's context. Full detail
stays in ``results.json`` and ``report.html``.

Schema ``assay.check.summary`` version 1. Fields are only ever added within a
major version; a breaking change bumps ``schema_version``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.plan import Plan
from core.redact import make_redactor
from core.schema import Verdict

if TYPE_CHECKING:
    from core.budget import RunBudget
    from core.executor import RunResult

SUMMARY_SCHEMA = "assay.check.summary"
SUMMARY_VERSION = "1"

EXIT_MEANINGS = {
    0: "all selected scenarios passed and the run completed",
    1: "at least one confirmed application failure",
    2: "no confirmed failure, but the run is incomplete, blocked, errored, or its input was invalid",
}

# Guidance an agent can act on without re-reading the docs.
ATTENTION_HINTS = {
    Verdict.ERROR: "Harness or infrastructure problem. Do not change application code for this; report it.",
    Verdict.BLOCKED: "A prerequisite failed or policy prevented execution. Resolve the prerequisite or ask the user.",
    Verdict.UNVERIFIED: "Ambiguous requirement, unsupported check, or exhausted budget. Ask a human; never report it as passing.",
}

NOT_SELECTED_REASON = "not selected for this run (--only)"


def tool_version() -> str:
    try:
        from importlib.metadata import version

        return version("assay")
    except Exception:  # noqa: BLE001 - metadata unavailable in some checkouts
        return "unknown"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── --only selection ──────────────────────────────────────────────────────────

def select_scenarios(plan: Plan, only: list[str]) -> tuple[Plan, list[str], list[str]]:
    """Restrict execution to *only* plus every transitive prerequisite.

    Returns ``(execution_plan, selected_ids, unselected_ids)``. The execution
    plan is an in-memory copy in which unselected scenarios are marked
    ``skip=True``; expectations of selected scenarios are never changed, and
    the saved plan file is never modified.

    Raises ValueError for unknown scenario IDs.
    """
    lookup = {s.id: s for s in plan.scenarios}
    unknown = [sid for sid in only if sid not in lookup]
    if unknown:
        known = ", ".join(lookup) or "(none)"
        raise ValueError(f"--only: unknown scenario id(s) {', '.join(unknown)}; plan contains {known}")

    selected: set[str] = set()

    def visit(sid: str) -> None:
        if sid in selected:
            return
        selected.add(sid)
        for dep in lookup[sid].prerequisites:
            visit(dep)

    for sid in only:
        visit(sid)

    scenarios = [s if s.id in selected else replace(s, skip=True) for s in plan.scenarios]
    ordered_selected = [s.id for s in plan.scenarios if s.id in selected]
    unselected = [s.id for s in plan.scenarios if s.id not in selected]
    return replace(plan, scenarios=scenarios), ordered_selected, unselected


def relabel_unselected(run_result: "RunResult", unselected: list[str]) -> None:
    """Give scenarios skipped by --only an explicit reason in every artifact."""
    names = set(unselected)
    for r in run_result.scenario_results:
        if r.scenario_id in names and r.verdict is Verdict.SKIPPED:
            r.reason = NOT_SELECTED_REASON


# ── Plan overview (what an agent shows the user before running) ──────────────

def plan_overview(plan: Plan) -> list[dict[str, Any]]:
    """One compact row per scenario, in plan order."""
    rows = []
    for s in plan.scenarios:
        rows.append({
            "id": s.id,
            "title": s.title,
            "requires_mutations": s.requires_mutations,
            "prerequisites": list(s.prerequisites),
            "assertions": len(s.assertions),
            "source": s.source.kind if s.source else None,
        })
    return rows


def format_plan_table(plan: Plan) -> list[str]:
    """Human-readable scenario table for text mode."""
    rows = plan_overview(plan)
    if not rows:
        return ["  (no scenarios)"]
    id_w = max(len("ID"), *(len(r["id"]) for r in rows))
    title_w = min(60, max(len("Scenario"), *(len(r["title"]) for r in rows)))
    lines = [f"  {'ID':<{id_w}}  {'Scenario':<{title_w}}  Changes data  Depends on",
             f"  {'-' * id_w}  {'-' * title_w}  ------------  ----------"]
    for r in rows:
        title = r["title"] if len(r["title"]) <= title_w else r["title"][: title_w - 1] + "…"
        deps = ", ".join(r["prerequisites"]) or "-"
        lines.append(f"  {r['id']:<{id_w}}  {title:<{title_w}}  {'yes' if r['requires_mutations'] else 'no':<12}  {deps}")
    return lines


# ── Summary construction ──────────────────────────────────────────────────────

def _video(out_dir: Path, scenario_id: str) -> "str | None":
    path = out_dir / scenario_id / "video.webm"
    return str(path) if path.is_file() else None


def _status_for(code: int) -> str:
    return {0: "pass", 1: "fail"}.get(code, "incomplete")


def _artifact_map(out_dir: Path, names: list[str]) -> dict[str, str]:
    keys = {
        "plan.json": "plan",
        "results.json": "results",
        "report.html": "report",
        "junit.xml": "junit",
        "summary.json": "summary_json",
        "summary.md": "summary_md",
    }
    return {keys[n]: str(out_dir / n) for n in names}


def _rerun_commands(plan_path: Path, out_dir: Path, failed_ids: list[str]) -> dict[str, str]:
    base = f"assay check --plan {plan_path} --format json --output {out_dir}"
    cmds = {"all": base}
    if failed_ids:
        cmds["failed"] = base + "".join(f" --only {sid}" for sid in failed_ids)
    return cmds


def build_summary(
    *,
    code: int,
    run_result: "RunResult | None",
    plan: Plan,
    plan_path: Path,
    plan_source: str,
    out_dir: Path,
    base_url: str,
    selected: list[str] | None = None,
    unselected: list[str] | None = None,
    budget: "RunBudget | None" = None,
    planned_only: bool = False,
) -> dict[str, Any]:
    """Build the summary document for a planned or executed run."""
    plan_lookup = {s.id: s for s in plan.scenarios}
    unselected = unselected or []

    doc: dict[str, Any] = {
        "schema": SUMMARY_SCHEMA,
        "schema_version": SUMMARY_VERSION,
        "tool": {"name": "assay", "version": tool_version()},
        "status": "planned" if planned_only else _status_for(code),
        "exit_code": code,
        "exit_meaning": "plan written; no scenarios executed" if planned_only else EXIT_MEANINGS[code],
        "run_id": run_result.run_id if run_result else None,
        "complete": run_result.complete if run_result else False,
        "scope": "partial" if unselected else "full",
        "selected": selected if unselected else None,
        "depth": plan.depth,
        "base_url": str(base_url),
        "plan": {
            "path": str(plan_path),
            "sha256": file_sha256(plan_path),
            "source": plan_source,
            "scenario_count": len(plan.scenarios),
        },
        "counts": {},
        "failures": [],
        "needs_attention": [],
        "artifacts": {},
        "rerun": {},
        "budget": budget.summary() if budget is not None else None,
    }

    if planned_only or run_result is None:
        doc["scenarios"] = plan_overview(plan)
        doc["coverage_suggestions"] = list(plan.coverage_suggestions)
        doc["artifacts"] = _artifact_map(out_dir, ["plan.json", "summary.json", "summary.md"])
        doc["rerun"] = _rerun_commands(plan_path, out_dir, [])
        return doc

    counted = [r for r in run_result.scenario_results if r.scenario_id not in set(unselected)]
    counts = {v.value: sum(1 for r in counted if r.verdict is v) for v in Verdict}
    doc["counts"] = {k: n for k, n in counts.items() if n} | {"total": len(counted)}

    failed_ids: list[str] = []
    for r in run_result.scenario_results:
        scenario = plan_lookup.get(r.scenario_id)
        if r.verdict is Verdict.FAIL:
            failed_ids.append(r.scenario_id)
            assertions = []
            if scenario is not None:
                for a in scenario.assertions:
                    assertions.append({
                        "id": a.id,
                        "expected": a.description,
                        "kind": a.kind.value,
                        "check": a.check,
                        "observed": r.assertion_evidence.get(a.id),
                    })
            doc["failures"].append({
                "scenario_id": r.scenario_id,
                "title": r.scenario_title,
                "reason": r.reason,
                "assertions": assertions,
                "video": _video(out_dir, r.scenario_id),
            })
        elif r.verdict in ATTENTION_HINTS and r.scenario_id not in unselected:
            doc["needs_attention"].append({
                "scenario_id": r.scenario_id,
                "title": r.scenario_title,
                "verdict": r.verdict.value,
                "reason": r.reason,
                "hint": ATTENTION_HINTS[r.verdict],
                "video": _video(out_dir, r.scenario_id),
            })

    doc["scenarios"] = [
        {"id": r.scenario_id, "title": r.scenario_title, "verdict": r.verdict.value,
         "assertion_results": [asdict(o) for o in r.assertion_outcomes],
         "video": _video(out_dir, r.scenario_id)}
        for r in run_result.scenario_results
    ]
    doc["artifacts"] = _artifact_map(
        out_dir, ["plan.json", "results.json", "report.html", "junit.xml", "summary.json", "summary.md"]
    )
    doc["rerun"] = _rerun_commands(plan_path, out_dir, failed_ids)
    return doc


def error_summary(message: str, status: str = "invalid_input", code: int = 2) -> dict[str, Any]:
    """Summary for runs that stop before a plan exists (bad input, planner failure)."""
    return {
        "schema": SUMMARY_SCHEMA,
        "schema_version": SUMMARY_VERSION,
        "tool": {"name": "assay", "version": tool_version()},
        "status": status,
        "exit_code": code,
        "exit_meaning": EXIT_MEANINGS[code],
        "error": message,
    }


def dumps_summary(doc: dict[str, Any]) -> str:
    return make_redactor().scrub(json.dumps(doc, indent=2, ensure_ascii=False))


# ── Files ─────────────────────────────────────────────────────────────────────

def write_summary_files(doc: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(dumps_summary(doc) + "\n", encoding="utf-8")
    (out_dir / "summary.md").write_text(make_redactor().scrub(render_markdown(doc)), encoding="utf-8")


def _md(text: Any) -> str:
    """Keep application text from breaking Markdown tables or injecting HTML."""
    s = "" if text is None else str(text)
    return s.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def render_markdown(doc: dict[str, Any]) -> str:
    """Render a PR-ready summary. Advisory wording is deliberate."""
    status = doc["status"].upper()
    lines = [f"### Browser check (assay): {status}", ""]

    if doc["status"] == "planned":
        lines += [
            f"Plan written with {doc['plan']['scenario_count']} scenario(s) at `{doc['depth']}` depth; nothing was executed.",
            "",
            "| ID | Scenario | Changes data | Depends on |",
            "|---|---|---|---|",
        ]
        for r in doc.get("scenarios") or []:
            lines.append(
                f"| `{r['id']}` | {_md(r['title'])} | {'yes' if r['requires_mutations'] else 'no'} | "
                f"{', '.join(r['prerequisites']) or '-'} |"
            )
        if doc.get("coverage_suggestions"):
            lines += ["", f"Not selected at this depth ({len(doc['coverage_suggestions'])}):"]
            lines += [f"- {_md(c)}" for c in doc["coverage_suggestions"]]
        lines.append("")
    else:
        counts = doc.get("counts") or {}
        cells = [f"{k} {v}" for k, v in counts.items() if k != "total"]
        lines.append(f"{counts.get('total', 0)} scenario(s) at `{doc['depth']}` depth: " + (", ".join(cells) or "none"))
        if not doc.get("complete"):
            lines.append("")
            lines.append("**Run incomplete.** Some required scenarios have no definitive result.")
        if doc.get("scope") == "partial":
            lines.append("")
            lines.append(
                "**Partial run:** only " + ", ".join(f"`{s}`" for s in doc.get("selected") or [])
                + " were executed. Run the full plan before relying on this result."
            )
        lines.append("")

    if doc.get("failures"):
        lines += ["#### Failures", ""]
        for f in doc["failures"]:
            lines.append(f"- **{_md(f['title'])}** (`{f['scenario_id']}`): {_md(f['reason'])}")
            if f.get("video"):
                lines.append(f"  - recording: `{f['video']}`")
            for a in f["assertions"]:
                if a.get("observed") is not None:
                    lines.append(f"  - expected: {_md(a['expected'])}; observed: {_md(a['observed'])}")
        lines.append("")

    if doc.get("needs_attention"):
        lines += ["#### Needs attention", "", "| Scenario | Status | Reason |", "|---|---|---|"]
        for n in doc["needs_attention"]:
            lines.append(f"| {_md(n['title'])} | {n['verdict']} | {_md(n['reason'])} |")
        lines.append("")

    lines.append(
        f"Plan `{Path(doc['plan']['path']).name}` sha256 `{doc['plan']['sha256'][:12]}` "
        f"({doc['plan']['source']}). Full report: `{doc['artifacts'].get('report', 'n/a')}`."
    )
    lines += ["", "_Advisory pre-PR browser check. It does not prove exhaustive coverage._", ""]
    return "\n".join(lines)
