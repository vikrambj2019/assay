"""Opt-in paired live evaluation: neutral notes -> frozen plan -> clean/broken runs.

Each variant gets a fresh fixture store and browser at the same URL. Bug labels
and detection checks stay in the scoring process, never in planner context.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import traceback
from dataclasses import replace
from pathlib import Path
from threading import Thread

from werkzeug.serving import make_server

from core.budget import budget_from_depth
from core.check_report import write_check_html, write_results_json
from core.config import Config, load_dotenv
from core.context import CheckContext
from core.executor import run_plan
from core.plan import save_plan
from core.planner import AnthropicPlannerAdapter, run_planner
from core.policy import MutationPolicy
from core.schema import Verdict
from core.redact import make_redactor
from fixture.app.factory import create_app, FIXTURE_USERNAME, FIXTURE_PASSWORD
from fixture.eval.bugs import BUGS
from fixture.eval.live import run_live_eval
from fixture.eval.report import write_eval_report
from harness.scenario import BrowserScenarioAdapterFactory

# User requirements, not descriptions of the planted implementation defects.
REQUIREMENTS = {
    "login": "Log in with the supplied demo account. Successful login must end at /dashboard.",
    "record_creation": "Create a record named New Widget. After submission, Record saved must be visible.",
    "edit_persistence": "Open /records/1. Rename Widget A to Updated Widget. Updated Widget must be visible after saving and survive reload.",
    "form_validation": "Open /records. Submit the new-record form with an empty name. Name is required must be visible.",
    "navigation": "Open /records. Click Widget A's detail link. It must navigate to /records/1.",
    "calculation": "Open /records/1. The total should be correct; its calculation rule is unspecified.",
}


async def evaluate_fixture(cfg: Config, output: Path, run_count: int, model_version: str):
    if os.environ.get("ASSAY_EVAL_LIVE") != "1":
        raise RuntimeError("live evaluation is disabled; set ASSAY_EVAL_LIVE=1")
    server = make_server("127.0.0.1", 0, create_app())
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cfg = replace(cfg, app_url=url, login_url=url + "/login", allowed_origins=[url],
                  test_username="", test_password="", auth_state=None, storage_state=None,
                  allow_mutations=True, depth="low", headless=True)
    planner = AnthropicPlannerAdapter(model=cfg.model)
    plans = {}
    attempts = {}

    async def evaluate(spec, broken):
        # Reset both server state and browser state for every trial.
        server.set_app(create_app(bugs=[spec.bug_id] if broken else []))
        if not broken:
            attempts[spec.flow] = attempts.get(spec.flow, 0) + 1
        folder = output / spec.flow / str(attempts[spec.flow]) / ("broken" if broken else "clean")
        folder.mkdir(parents=True, exist_ok=True)
        budget = budget_from_depth("low", max_seconds=cfg.max_seconds,
                                   max_actions=cfg.max_actions, max_cost_usd=cfg.max_cost_usd)
        budget.start()
        try:
            if not broken:
                plans.pop(spec.flow, None)
                notes = (f"Application URL: {url}\nDemo login: {FIXTURE_USERNAME} / {FIXTURE_PASSWORD}\n"
                         + REQUIREMENTS[spec.flow])
                ctx = CheckContext(Path("requirements.md"), notes, None, None, None, None,
                                   [], [], [], [])
                plans[spec.flow] = run_planner(ctx, "low", planner)
            plan = plans[spec.flow]
            save_plan(plan, folder)
            result = await run_plan(plan, MutationPolicy(True),
                                   BrowserScenarioAdapterFactory(cfg, folder, budget), budget=budget)
            write_results_json(result, plan, folder, budget=budget)
            write_check_html(result, plan, folder, budget=budget)
            verdicts = {r.verdict for r in result.scenario_results}
            verdict = next((v for v in (Verdict.FAIL, Verdict.ERROR, Verdict.BLOCKED, Verdict.UNVERIFIED)
                            if v in verdicts), Verdict.PASS if verdicts == {Verdict.PASS} else Verdict.UNVERIFIED)
            return verdict, 0, budget.cost_usd()
        except Exception:
            (folder / "error.txt").write_text(make_redactor().scrub(traceback.format_exc()))
            # Infrastructure/planning failures are never credited as detections.
            return Verdict.ERROR, 0, budget.cost_usd()

    try:
        report = await run_live_eval(BUGS, evaluate, model=cfg.model,
                                    model_version=model_version, run_count=run_count,
                                    configuration={"depth": "low", "frozen_plan_per_pair": True,
                                                   "planning_cost_included": False})
        write_eval_report(report, output)
        return report
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("results/live-eval"))
    parser.add_argument("--model-version", required=True, help="Exact model/version used for this evaluation")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    load_dotenv()
    report = asyncio.run(evaluate_fixture(Config.from_env(), args.output, args.runs, args.model_version))
    print(f"Report: {args.output / 'eval_report.json'}")
    return int(bool(report.harness_error_count or report.false_positive_count or report.missed_count
                    or report.ambiguous_incorrectly_resolved_count))


if __name__ == "__main__":
    raise SystemExit(main())
