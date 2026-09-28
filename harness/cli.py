"""`assay` (alias `agent`) command-line entrypoint.

  assay suite <file>   — run a YAML suite of goals (parallel + `needs:` deps)
  assay check [opts]   — collect context and run a pre-PR browser check

.env loading: load_dotenv() is called once here, before any subcommand runs,
so values in .env are visible to Config.from_env() without the caller having
to set them manually. Process environment variables already set always win.

Machine interface (`assay check --format json`):
  stdout carries exactly one JSON document (schema ``assay.check.summary``);
  every human-readable line goes to stderr. The same document is written to
  ``summary.json`` in the output directory. Exit codes are unchanged:
  0 pass, 1 confirmed failure, 2 incomplete / blocked / error / invalid input.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import shutil
import sys
from pathlib import Path


def _cmd_suite(args: argparse.Namespace) -> int:
    from core.suite import run_suite

    return asyncio.run(run_suite(args.file, on_event=lambda s: print(s, flush=True)))


def _cmd_check(args: argparse.Namespace) -> int:
    """Run a check; in JSON mode keep stdout clean for the summary document."""
    from core.check_summary import dumps_summary

    if args.format != "json":
        code, _ = _run_check(args)
        return code

    real_stdout = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        code, summary = _run_check(args)
    real_stdout.write(dumps_summary(summary) + "\n")
    real_stdout.flush()
    return code


def _fail(message: str, status: str = "invalid_input") -> tuple[int, dict]:
    from core.check_summary import error_summary

    print(f"error: {message}", file=sys.stderr)
    return 2, error_summary(message, status=status)


def _run_check(args: argparse.Namespace) -> tuple[int, dict]:
    from core.check_summary import (
        build_summary,
        file_sha256,
        format_plan_table,
        relabel_unselected,
        select_scenarios,
        write_summary_files,
    )
    from core.config import Config, _parse_url, legacy_env_warnings

    for warning in legacy_env_warnings():
        print(f"warning: {warning}", file=sys.stderr)

    # Build config from env (includes .env via load_dotenv called in main()).
    try:
        cfg = Config.from_env()
    except ValueError as e:
        return _fail(str(e))

    # CLI arguments override env / .env (highest priority in the precedence chain).
    if args.url:
        try:
            cfg.app_url = _parse_url(args.url, "--url")
        except ValueError as e:
            return _fail(str(e))
    if args.depth:
        cfg.depth = args.depth
    if args.record_video:
        cfg.record_video = True
    for flag, attr, value in (
        ("--max-seconds", "max_seconds", args.max_seconds),
        ("--max-actions", "max_actions", args.max_actions),
        ("--max-cost-usd", "max_cost_usd", args.max_cost_usd),
    ):
        if value is not None:
            if value <= 0:
                return _fail(f"{flag} must be a positive number")
            setattr(cfg, attr, value)

    if args.plan and args.plan_only:
        return _fail("--plan-only cannot be combined with --plan (the plan already exists)")
    if args.only and not args.plan:
        return _fail("--only requires --plan so the rerun uses the same saved expectations")

    # Validate all required inputs before any paid model call.
    try:
        cfg.validate_for_check()
    except ValueError as e:
        return _fail(str(e))

    out_dir = Path(args.output) if args.output else cfg.results_root / "check"

    # A generated plan is the contract for execution. If an output directory
    # already contains one, require an explicit --plan so a rerun cannot
    # silently replace the expectations with a newly sampled LLM plan.
    if args.notes and not args.plan_only and (out_dir / "plan.json").is_file():
        return _fail(
            f"{out_dir / 'plan.json'} already exists; use --plan {out_dir / 'plan.json'} "
            "to execute the frozen plan, or choose a new --output directory"
        )

    from core.budget import BudgetExhausted, budget_from_depth
    from core.plan import load_plan, save_plan

    ctx = None
    if args.plan:
        # ── Frozen plan: reuse saved expectations exactly ───────────────────
        source_path = Path(args.plan)
        try:
            plan = load_plan(source_path)
        except FileNotFoundError:
            return _fail(f"--plan {source_path}: file not found")
        except (ValueError, KeyError, TypeError) as e:
            return _fail(f"--plan {source_path}: not a valid plan ({e})")
        if args.readme or args.diff:
            print("warning: --readme/--diff are ignored with --plan; the saved plan is used as-is",
                  file=sys.stderr)
        if not args.depth:
            cfg.depth = plan.depth
        plan_path = out_dir / "plan.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() != plan_path.resolve():
            shutil.copyfile(source_path, plan_path)  # byte-identical copy keeps the hash
        plan_source = "loaded"
        print(f"\n  plan:   {plan_path} (loaded from {source_path}, {len(plan.scenarios)} scenario(s))")
        budget = budget_from_depth(
            cfg.depth, max_seconds=cfg.max_seconds,
            max_actions=cfg.max_actions, max_cost_usd=cfg.max_cost_usd,
        )
        budget.start()
    else:
        # ── Generate a new plan from notes / README / diff ──────────────────
        from core.context import collect_context

        notes = Path(args.notes)
        if not notes.is_file():
            return _fail(f"--notes {notes}: file not found")
        readme = Path(args.readme) if args.readme else None
        try:
            ctx = collect_context(notes, readme, args.diff, cfg)
        except (ValueError, FileNotFoundError) as e:
            return _fail(str(e))
        _print_context_summary(ctx)

        # Planning time is part of the run budget, including in --plan-only mode.
        budget = budget_from_depth(
            cfg.depth, max_seconds=cfg.max_seconds,
            max_actions=cfg.max_actions, max_cost_usd=cfg.max_cost_usd,
        )
        budget.start()

        from core.planner import AnthropicPlannerAdapter, PlanningError, run_planner

        if args.plan_only:
            print("\nNote: --plan-only mode — planning may incur model cost.")
        adapter = AnthropicPlannerAdapter(model=cfg.model)
        try:
            plan = run_planner(ctx, cfg.depth, adapter)
            budget.check()
        except BudgetExhausted as e:
            return _fail(f"planning exceeded the run budget: {e}", status="planning_failed")
        except PlanningError as e:
            return _fail(f"planning failed: {e}", status="planning_failed")

        plan_path = save_plan(plan, out_dir)
        plan_source = "generated"
        print(f"\n  plan:   {plan_path} ({len(plan.scenarios)} scenario(s))")
        if plan.coverage_suggestions:
            print(f"  suggestions: {len(plan.coverage_suggestions)} unselected idea(s)")

    if args.plan_only:
        summary = build_summary(
            code=0, run_result=None, plan=plan, plan_path=plan_path,
            plan_source=plan_source, out_dir=out_dir, base_url=cfg.app_url,
            budget=budget, planned_only=True,
        )
        write_summary_files(summary, out_dir)
        print()
        for line in format_plan_table(plan):
            print(line)
        if plan.coverage_suggestions:
            print(f"\n  Not selected at {plan.depth} depth: {len(plan.coverage_suggestions)} idea(s) (see summary.md)")
        print("\n(plan-only mode — no scenarios executed)")
        print(f"Run all:     assay check --plan {plan_path}")
        print(f"Run some:    assay check --plan {plan_path} --only <ID> [--only <ID> ...]")
        return 0, summary

    # ── --only: run a subset (plus prerequisites) of the saved plan ─────────
    exec_plan, selected, unselected = plan, None, []
    if args.only:
        try:
            exec_plan, selected, unselected = select_scenarios(plan, args.only)
        except ValueError as e:
            return _fail(str(e))
        print(f"  only:   {', '.join(selected)} ({len(unselected)} other scenario(s) not selected)")

    # ── Execute plan ─────────────────────────────────────────────────────────
    from core.check_report import exit_code, write_check_html, write_junit_xml, write_results_json
    from core.executor import run_plan
    from core.policy import MutationPolicy
    from core.schema import Verdict

    mutation_policy = MutationPolicy(allow_mutations=cfg.allow_mutations)

    from harness.scenario import BrowserScenarioAdapterFactory
    adapter_factory = BrowserScenarioAdapterFactory(cfg, out_dir, budget)

    run_result = asyncio.run(
        run_plan(exec_plan, mutation_policy, adapter_factory, budget=budget)
    )
    if unselected:
        relabel_unselected(run_result, unselected)

    plan_info = {"path": str(plan_path), "sha256": file_sha256(plan_path), "source": plan_source}
    selection = {"only": list(args.only), "selected": selected, "not_selected": unselected} if args.only else None

    # Write all report artifacts. Done unconditionally so partial results
    # are always available ("started runs always retain available results").
    json_path = write_results_json(run_result, plan, out_dir, ctx=ctx, budget=budget,
                                   plan_info=plan_info, selection=selection)
    html_path = write_check_html(run_result, plan, out_dir, ctx=ctx, budget=budget)
    junit_path = write_junit_xml(run_result, plan, out_dir)
    code = exit_code(run_result)
    summary = build_summary(
        code=code, run_result=run_result, plan=plan, plan_path=plan_path,
        plan_source=plan_source, out_dir=out_dir, base_url=cfg.app_url,
        selected=selected, unselected=unselected, budget=budget,
    )
    write_summary_files(summary, out_dir)

    print(f"  results: {json_path}")
    print(f"  report:  {html_path}")
    print(f"  junit:   {junit_path}")
    print(f"  summary: {out_dir / 'summary.md'}")

    if code == 0:
        print("\nresult: PASS" + (" (partial run: --only)" if unselected else ""))
    elif code == 1:
        print("\nresult: FAIL")
        for r in run_result.scenario_results:
            if r.verdict is Verdict.FAIL:
                print(f"  FAIL  {r.scenario_id} {r.scenario_title}: {r.reason}")
    else:
        print("\nresult: INCOMPLETE / ERROR")

    return code, summary


def _print_context_summary(ctx: "object") -> None:  # CheckContext
    from core.context import CheckContext
    assert isinstance(ctx, CheckContext)
    print(f"\n  notes:  {ctx.notes_path} ({len(ctx.notes_text):,} chars)")
    if ctx.readme_text is not None:
        print(f"  readme: {ctx.readme_path} ({len(ctx.readme_text):,} chars)")
    else:
        print("  readme: not found")
    if ctx.diff_ref is not None:
        diff_size = len(ctx.diff_text) if ctx.diff_text else 0
        print(f"  diff:   {ctx.diff_ref} ({diff_size:,} chars)")
        if ctx.untracked_files:
            sample = ", ".join(ctx.untracked_files[:5])
            suffix = "…" if len(ctx.untracked_files) > 5 else ""
            print(f"  untracked ({len(ctx.untracked_files)} files, not ingested): "
                  f"{sample}{suffix}")
    else:
        print("  diff:   not requested")
    if ctx.excluded:
        print(f"  excluded: {len(ctx.excluded)} file(s)")
        for ef in ctx.excluded[:5]:
            print(f"    - {ef.path}: {ef.reason}")
        if len(ctx.excluded) > 5:
            print(f"    … ({len(ctx.excluded) - 5} more)")
    if ctx.truncated:
        print(f"  truncated: {len(ctx.truncated)} item(s)")
        for tf in ctx.truncated:
            print(f"    - {tf.path}: {tf.original_chars:,} → {tf.kept_chars:,} chars")
    for w in ctx.warnings:
        print(f"  warning: {w}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assay",
        description="Natural-language browser testing agent with evidence-backed verdicts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── suite ──────────────────────────────────────────────────────────────
    p_suite = sub.add_parser("suite", help="run a YAML suite of goals")
    p_suite.add_argument("file", help="path to the suite YAML")
    p_suite.set_defaults(func=_cmd_suite)

    # ── check ──────────────────────────────────────────────────────────────
    p_check = sub.add_parser(
        "check", help="run a pre-PR browser check",
        description="Plan and run a bounded browser check. Exit codes: 0 pass, "
                    "1 confirmed failure, 2 incomplete/blocked/error/invalid input.",
    )
    source = p_check.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--notes", metavar="PATH",
        help="notes/changes file (Markdown) to plan from")
    source.add_argument(
        "--plan", metavar="PATH",
        help="rerun a saved plan.json exactly as written (no re-planning)")
    p_check.add_argument(
        "--only", action="append", default=[], metavar="SCENARIO_ID",
        help="with --plan: run only this scenario and its prerequisites (repeatable)")
    p_check.add_argument(
        "--readme", default=None, metavar="PATH",
        help="README file (default: README.md in working directory)")
    p_check.add_argument(
        "--diff", default=None, metavar="REF",
        help="git reference — include committed+staged+unstaged diff since merge-base")
    p_check.add_argument(
        "--depth", choices=["low", "medium", "high"], default=None,
        help="test depth preset — overrides ASSAY_DEPTH (default: medium; "
             "with --plan: the plan's depth)")
    p_check.add_argument(
        "--url", default=None, metavar="URL",
        help="application URL — overrides ASSAY_BASE_URL")
    p_check.add_argument(
        "--output", default=None, metavar="DIR",
        help="artifact directory (default: $ASSAY_RESULTS_DIR/check, i.e. results/check)")
    p_check.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="json: print one summary document to stdout and send all logs to stderr")
    p_check.add_argument(
        "--max-seconds", type=int, default=None, metavar="N",
        help="override maximum run time in seconds")
    p_check.add_argument(
        "--max-actions", type=int, default=None, metavar="N",
        help="override maximum action count")
    p_check.add_argument(
        "--max-cost-usd", type=float, default=None, metavar="F",
        help="override maximum cost in USD")
    p_check.add_argument(
        "--record-video", action="store_true", default=False,
        help="save a browser recording per scenario as <output>/<scenario-id>/video.webm "
             "(also ASSAY_RECORD_VIDEO=true); recordings may show sensitive data")
    p_check.add_argument(
        "--plan-only", action="store_true", default=False,
        help="generate and save plan.json without executing scenarios "
             "(note: planning may incur model cost)")
    p_check.set_defaults(func=_cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Load .env from the invocation directory before parsing args so that
    # Config.from_env() sees the values. --help is handled by argparse before
    # any subcommand runs, so it never triggers a model call or browser launch.
    from core.config import load_dotenv
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
