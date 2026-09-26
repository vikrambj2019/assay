"""`agent` / `bta` command-line entrypoint.

  bta suite <file>   — run a YAML suite of goals (parallel + `needs:` deps)
  bta check [opts]   — collect context and run a pre-PR browser check

.env loading: load_dotenv() is called once here, before any subcommand runs,
so values in .env are visible to Config.from_env() without the caller having
to set them manually. Process environment variables already set always win.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _cmd_suite(args: argparse.Namespace) -> int:
    from core.suite import run_suite

    return asyncio.run(run_suite(args.file, on_event=lambda s: print(s, flush=True)))


def _cmd_check(args: argparse.Namespace) -> int:
    from core.config import Config, _parse_url
    from core.context import collect_context

    # Build config from env (includes .env via load_dotenv called in main()).
    try:
        cfg = Config.from_env()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # CLI arguments override env / .env (highest priority in the precedence chain).
    if args.url:
        try:
            cfg.app_url = _parse_url(args.url, "--url")
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    if args.depth:
        cfg.depth = args.depth
    if args.max_seconds is not None:
        if args.max_seconds <= 0:
            print("error: --max-seconds must be a positive integer", file=sys.stderr)
            return 2
        cfg.max_seconds = args.max_seconds
    if args.max_actions is not None:
        if args.max_actions <= 0:
            print("error: --max-actions must be a positive integer", file=sys.stderr)
            return 2
        cfg.max_actions = args.max_actions
    if args.max_cost_usd is not None:
        if args.max_cost_usd <= 0:
            print("error: --max-cost-usd must be a positive number", file=sys.stderr)
            return 2
        cfg.max_cost_usd = args.max_cost_usd

    # Validate all required inputs before any paid model call.
    try:
        cfg.validate_for_check()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    notes = Path(args.notes)
    if not notes.is_file():
        print(f"error: --notes {notes}: file not found", file=sys.stderr)
        return 2

    readme = Path(args.readme) if args.readme else None

    # Collect context (notes, README, git diff).
    try:
        ctx = collect_context(notes, readme, args.diff, cfg)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Report context provenance to the user.
    _print_context_summary(ctx)

    # Planning time is part of the run budget, including in --plan-only mode.
    from core.budget import BudgetExhausted, budget_from_depth
    budget = budget_from_depth(
        cfg.depth,
        max_seconds=cfg.max_seconds,
        max_actions=cfg.max_actions,
        max_cost_usd=cfg.max_cost_usd,
    )
    budget.start()

    # Run planner.
    from core.plan import save_plan
    from core.planner import AnthropicPlannerAdapter, PlanningError, run_planner

    if args.plan_only:
        print("\nNote: --plan-only mode — planning may incur model cost.")

    adapter = AnthropicPlannerAdapter(model=cfg.model)
    try:
        plan = run_planner(ctx, cfg.depth, adapter)
        budget.check()
    except BudgetExhausted as e:
        print(f"error: planning exceeded the run budget: {e}", file=sys.stderr)
        return 2
    except PlanningError as e:
        print(f"error: planning failed: {e}", file=sys.stderr)
        return 2

    out_dir = cfg.results_root / "check"
    plan_path = save_plan(plan, out_dir)
    print(f"\n  plan:   {plan_path} ({len(plan.scenarios)} scenario(s))")
    if plan.coverage_suggestions:
        print(f"  suggestions: {len(plan.coverage_suggestions)} unselected idea(s)")

    if args.plan_only:
        print("(plan-only mode — no scenarios executed)")
        return 0

    # ── Execute plan ──────────────────────────────────────────────────────────
    from core.check_report import exit_code, write_check_html, write_junit_xml, write_results_json
    from core.executor import run_plan
    from core.policy import MutationPolicy

    mutation_policy = MutationPolicy(allow_mutations=cfg.allow_mutations)

    from harness.scenario import BrowserScenarioAdapterFactory
    adapter_factory = BrowserScenarioAdapterFactory(cfg, out_dir, budget)

    run_result = asyncio.run(
        run_plan(plan, mutation_policy, adapter_factory, budget=budget)
    )

    # Write all three report artifacts.  Done unconditionally so partial results
    # are always available ("started runs always retain available results").
    json_path  = write_results_json(run_result, plan, out_dir, ctx=ctx, budget=budget)
    html_path  = write_check_html(run_result, plan, out_dir, ctx=ctx, budget=budget)
    junit_path = write_junit_xml(run_result, plan, out_dir)
    print(f"  results: {json_path}")
    print(f"  report:  {html_path}")
    print(f"  junit:   {junit_path}")

    code = exit_code(run_result)
    if code == 0:
        print("\nresult: PASS")
    elif code == 1:
        print("\nresult: FAIL")
        for r in run_result.scenario_results:
            from core.schema import Verdict
            if r.verdict is Verdict.FAIL:
                print(f"  FAIL  {r.scenario_title}: {r.reason}")
    else:
        print("\nresult: INCOMPLETE / ERROR")

    return code


def _print_context_summary(ctx: "object") -> None:  # CheckContext
    from core.context import CheckContext
    assert isinstance(ctx, CheckContext)
    print(f"\n  notes:  {ctx.notes_path} ({len(ctx.notes_text):,} chars)")
    if ctx.readme_text is not None:
        print(f"  readme: {ctx.readme_path} ({len(ctx.readme_text):,} chars)")
    else:
        print(f"  readme: not found")
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
        prog="bta",
        description="Natural-language browser testing agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── suite ──────────────────────────────────────────────────────────────
    p_suite = sub.add_parser("suite", help="run a YAML suite of goals")
    p_suite.add_argument("file", help="path to the suite YAML")
    p_suite.set_defaults(func=_cmd_suite)

    # ── check ──────────────────────────────────────────────────────────────
    p_check = sub.add_parser("check", help="run a pre-PR browser check")
    p_check.add_argument(
        "--notes", required=True, metavar="PATH",
        help="required: notes/changes file (Markdown)")
    p_check.add_argument(
        "--readme", default=None, metavar="PATH",
        help="README file (default: README.md in working directory)")
    p_check.add_argument(
        "--diff", default=None, metavar="REF",
        help="git reference — include committed+staged+unstaged diff since merge-base")
    p_check.add_argument(
        "--depth", choices=["low", "medium", "high"], default=None,
        help="test depth preset — overrides BTA_DEPTH (default: medium)")
    p_check.add_argument(
        "--url", default=None, metavar="URL",
        help="application URL — overrides BTA_BASE_URL")
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
