"""Tests for the planner and depth policies (Task 11).

All pure unit tests — no browser, no real LLM, no network, no git.
FakeAdapter injects pre-canned JSON responses so every code path is
exercised without touching the Anthropic API.
"""

from __future__ import annotations

import json
import inspect
from pathlib import Path

import pytest

from core.context import CheckContext
from core.plan import SCHEMA_VERSION, Plan
from core.planner import (
    AnthropicPlannerAdapter,
    DEPTH_POLICIES,
    DepthPolicy,
    PlanningError,
    run_planner,
)
from core.planner import _build_system_prompt, _build_user_message


def test_anthropic_adapter_default_budget_handles_check_rich_plans():
    """The default response budget must fit medium plans with checks."""
    default = inspect.signature(AnthropicPlannerAdapter.__init__).parameters[
        "max_tokens"
    ].default
    assert default == 8192


def test_anthropic_adapter_skips_thinking_blocks(monkeypatch):
    """Extended-thinking content must not crash planner response extraction."""
    class Thinking:
        pass

    class Text:
        text = '{"plan": "ok"}'

    class Messages:
        def create(self, **kwargs):
            return type("Response", (), {"content": [Thinking(), Text()]})()

    adapter = object.__new__(AnthropicPlannerAdapter)
    adapter._client = type("Client", (), {"messages": Messages()})()
    adapter._model = "test"
    adapter._max_tokens = 10
    assert adapter.complete("system", "user") == '{"plan": "ok"}'


def test_anthropic_adapter_requires_text_block():
    class Thinking:
        pass

    class Messages:
        def create(self, **kwargs):
            return type("Response", (), {"content": [Thinking()]})()

    adapter = object.__new__(AnthropicPlannerAdapter)
    adapter._client = type("Client", (), {"messages": Messages()})()
    adapter._model = "test"
    adapter._max_tokens = 10
    with pytest.raises(ValueError, match="No text block"):
        adapter.complete("system", "user")


def test_prompt_teaches_deterministic_check_types_and_notes_path():
    policy = DEPTH_POLICIES["medium"]
    system = _build_system_prompt(policy, "medium")
    ctx = _make_context()
    user = _build_user_message(ctx, policy, "medium")
    assert '"check": {"type": "text_visible"' in system
    assert '"type": "url_contains"' in system
    assert '"type": "persistence"' in system
    assert "Do not use element_visible, element_hidden, or field_value" in system
    assert "Keep persistence verification in the same scenario" in system
    assert "Schedule the primary end-to-end booking" in system
    assert "Deterministic checks run after the entire scenario goal completes" in system
    assert "Use 'changes.md' as the source path" in user
    assert "not the example '<notes-filename>' or 'changes.md'" in user


def test_prompt_uses_actual_notes_filename():
    policy = DEPTH_POLICIES["low"]
    ctx = _make_context()
    ctx.notes_path = Path("driftline-booking-flow.md")
    user = _build_user_message(ctx, policy, "low")
    assert "Use 'driftline-booking-flow.md' as the source path" in user


def test_ungrounded_selector_checks_are_removed_before_validation():
    data = json.loads(_plan_json(1))
    data["scenarios"][0]["assertions"][0]["check"] = {
        "type": "element_visible", "selector": ".invented-selector"
    }
    adapter = FakeAdapter([json.dumps(data)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.scenarios[0].assertions[0].check is None


# ── Helpers ───────────────────────────────────────────────────────────────────

_SENTINEL = object()  # distinguishes "caller passed None" from "caller didn't pass"


class FakeAdapter:
    """Injectable adapter that returns pre-canned responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []  # (system, user) per call

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise RuntimeError("FakeAdapter: no more responses")
        return self.responses.pop(0)


def _make_context(
    notes: str = "## Change\n- Improved login flow\n",
    readme: str | None = None,
    diff: str | None = None,
    diff_ref: str | None = None,
) -> CheckContext:
    return CheckContext(
        notes_path=Path("changes.md"),
        notes_text=notes,
        readme_path=Path("README.md") if readme is not None else None,
        readme_text=readme,
        diff_ref=diff_ref,
        diff_text=diff,
        untracked_files=[],
        excluded=[],
        truncated=[],
        warnings=[],
    )


def _plan_json(
    n: int = 1,
    depth: str = "medium",
    *,
    mutations: bool = False,
    suggestions: list[str] | None = None,
    assertion_kind: str = "required",
    assertion_source: object = _SENTINEL,
) -> str:
    """Build a minimal valid plan JSON string with *n* scenarios."""
    src: dict | None
    if assertion_source is _SENTINEL:
        # default: include source (required for kind="required")
        src = {
            "kind": "notes",
            "path": "changes.md",
            "excerpt": "Improved login flow",
        }
    else:
        src = assertion_source  # type: ignore[assignment]
    scenarios = []
    for i in range(1, n + 1):
        scenarios.append({
            "id": f"s-{i:03d}",
            "title": f"Scenario {i}",
            "goal": f"Verify feature {i}",
            "prerequisites": [],
            "assertions": [{
                "id": f"a-{i:03d}",
                "description": "Behavior is correct",
                "kind": assertion_kind,
                "source": src,
            }],
            "requires_mutations": mutations,
            "reason": "Changed behavior",
            "source": {
                "kind": "notes",
                "path": "changes.md",
                "excerpt": "Improved login flow",
            },
            "skip": False,
        })
    return json.dumps({
        "version": "1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "depth": depth,
        "scenarios": scenarios,
        "coverage_suggestions": suggestions or [],
    })


# ── Depth policy values ───────────────────────────────────────────────────────

def test_all_three_depth_presets_defined():
    assert set(DEPTH_POLICIES.keys()) == {"low", "medium", "high"}


def test_depth_policy_low_values():
    p = DEPTH_POLICIES["low"]
    assert p.max_scenarios == 3
    assert p.max_actions == 40
    assert p.max_seconds == 180


def test_depth_policy_medium_values():
    p = DEPTH_POLICIES["medium"]
    assert p.max_scenarios == 8
    assert p.max_actions == 200
    assert p.max_seconds == 900


def test_depth_policy_high_values():
    p = DEPTH_POLICIES["high"]
    assert p.max_scenarios == 15
    assert p.max_actions == 300
    assert p.max_seconds == 1800


def test_depth_policy_emphasis_strings_not_empty():
    for name, policy in DEPTH_POLICIES.items():
        assert policy.emphasis, f"{name} has empty emphasis"


# ── Happy path ────────────────────────────────────────────────────────────────

def test_run_planner_returns_plan():
    adapter = FakeAdapter([_plan_json(1, "medium")])
    plan = run_planner(_make_context(), "medium", adapter)
    assert isinstance(plan, Plan)


def test_run_planner_sets_correct_depth():
    adapter = FakeAdapter([_plan_json(1, "low")])
    plan = run_planner(_make_context(), "low", adapter)
    assert plan.depth == "low"


def test_run_planner_enforces_schema_version():
    adapter = FakeAdapter([_plan_json(1)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.version == SCHEMA_VERSION


def test_adapter_called_once_on_success():
    adapter = FakeAdapter([_plan_json(1)])
    run_planner(_make_context(), "medium", adapter)
    assert len(adapter.calls) == 1


def test_no_scenarios_lost_within_cap():
    adapter = FakeAdapter([_plan_json(3, "medium")])
    plan = run_planner(_make_context(), "medium", adapter)
    assert len(plan.scenarios) == 3


# ── Prompt content ────────────────────────────────────────────────────────────

def test_system_prompt_contains_depth_name():
    adapter = FakeAdapter([_plan_json(1, "low")])
    run_planner(_make_context(), "low", adapter)
    system, _ = adapter.calls[0]
    assert "low" in system


def test_system_prompt_contains_max_scenarios():
    adapter = FakeAdapter([_plan_json(1, "low")])
    run_planner(_make_context(), "low", adapter)
    system, _ = adapter.calls[0]
    assert "3" in system   # max_scenarios for low


def test_user_message_contains_notes_text():
    ctx = _make_context(notes="## My special change\nImproved login flow\n")
    adapter = FakeAdapter([_plan_json(1)])
    run_planner(ctx, "medium", adapter)
    _, user = adapter.calls[0]
    assert "My special change" in user


def test_user_message_contains_readme_when_present():
    ctx = _make_context(readme="# App\nAuth via OAuth2\n")
    adapter = FakeAdapter([_plan_json(1)])
    run_planner(ctx, "medium", adapter)
    _, user = adapter.calls[0]
    assert "OAuth2" in user


def test_user_message_notes_readme_absent_when_none():
    ctx = _make_context(readme=None)
    adapter = FakeAdapter([_plan_json(1)])
    run_planner(ctx, "medium", adapter)
    _, user = adapter.calls[0]
    assert "not available" in user


def test_user_message_contains_diff_when_present():
    ctx = _make_context(diff="+def new_feature(): pass\n", diff_ref="main")
    adapter = FakeAdapter([_plan_json(1)])
    run_planner(ctx, "medium", adapter)
    _, user = adapter.calls[0]
    assert "new_feature" in user


def test_user_message_diff_absent_when_none():
    ctx = _make_context(diff=None)
    adapter = FakeAdapter([_plan_json(1)])
    run_planner(ctx, "medium", adapter)
    _, user = adapter.calls[0]
    assert "not requested" in user


# ── Depth cap enforcement ─────────────────────────────────────────────────────

def test_depth_cap_low_trims_to_3():
    adapter = FakeAdapter([_plan_json(5, "low")])
    plan = run_planner(_make_context(), "low", adapter)
    assert len(plan.scenarios) == 3


def test_depth_cap_medium_trims_to_8():
    adapter = FakeAdapter([_plan_json(10, "medium")])
    plan = run_planner(_make_context(), "medium", adapter)
    assert len(plan.scenarios) == 8


def test_depth_cap_high_trims_to_15():
    adapter = FakeAdapter([_plan_json(20, "high")])
    plan = run_planner(_make_context(), "high", adapter)
    assert len(plan.scenarios) == 15


def test_excess_scenarios_become_coverage_suggestions():
    adapter = FakeAdapter([_plan_json(5, "low")])
    plan = run_planner(_make_context(), "low", adapter)
    # scenarios 4 and 5 trimmed → should be in suggestions
    assert len(plan.coverage_suggestions) == 2


def test_within_cap_no_suggestions_added():
    adapter = FakeAdapter([_plan_json(2, "medium")])
    plan = run_planner(_make_context(), "medium", adapter)
    assert len(plan.coverage_suggestions) == 0


def test_existing_suggestions_preserved_after_cap():
    adapter = FakeAdapter([_plan_json(5, "low", suggestions=["Existing idea"])])
    plan = run_planner(_make_context(), "low", adapter)
    assert "Existing idea" in plan.coverage_suggestions
    # 1 existing + 2 excess scenarios
    assert len(plan.coverage_suggestions) == 3


def test_depth_override_even_when_llm_returns_wrong_depth():
    """The planner enforces depth=medium regardless of what the LLM said."""
    adapter = FakeAdapter([_plan_json(1, "high")])   # LLM claims "high"
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.depth == "medium"


# ── Repair mechanism ──────────────────────────────────────────────────────────

def test_malformed_json_triggers_one_repair():
    adapter = FakeAdapter(["not valid json !!!", _plan_json(1)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert len(adapter.calls) == 2
    assert isinstance(plan, Plan)


def test_repair_user_message_references_the_error():
    adapter = FakeAdapter(["invalid json!!!", _plan_json(1)])
    run_planner(_make_context(), "medium", adapter)
    _, repair_user = adapter.calls[1]
    # Repair message must describe the failure so the model can fix it
    assert "failed" in repair_user.lower() or "error" in repair_user.lower()


def test_both_attempts_fail_raises_planning_error():
    adapter = FakeAdapter(["bad json 1", "bad json 2"])
    with pytest.raises(PlanningError):
        run_planner(_make_context(), "medium", adapter)


def test_planning_error_message_includes_first_error():
    adapter = FakeAdapter(["bad json 1", "bad json 2"])
    with pytest.raises(PlanningError, match="First error"):
        run_planner(_make_context(), "medium", adapter)


def test_planning_error_message_includes_repair_error():
    adapter = FakeAdapter(["bad json 1", "bad json 2"])
    with pytest.raises(PlanningError, match="Repair error"):
        run_planner(_make_context(), "medium", adapter)


def test_exactly_one_repair_attempt_made():
    adapter = FakeAdapter(["bad json", "still bad"])
    with pytest.raises(PlanningError):
        run_planner(_make_context(), "medium", adapter)
    assert len(adapter.calls) == 2


def test_no_repair_when_first_attempt_succeeds():
    adapter = FakeAdapter([_plan_json(1)])
    run_planner(_make_context(), "medium", adapter)
    assert len(adapter.calls) == 1


def test_validation_failure_triggers_repair():
    """A plan that parses as JSON but fails validate_plan also triggers repair."""
    bad_plan = json.loads(_plan_json(1))
    # Remove source from a required assertion → validate_plan raises
    bad_plan["scenarios"][0]["assertions"][0]["source"] = None
    adapter = FakeAdapter([json.dumps(bad_plan), _plan_json(1)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert len(adapter.calls) == 2
    assert isinstance(plan, Plan)


def test_markdown_fences_stripped():
    fenced = "```json\n" + _plan_json(1) + "\n```"
    adapter = FakeAdapter([fenced])
    plan = run_planner(_make_context(), "medium", adapter)
    assert isinstance(plan, Plan)


def test_plain_backtick_fences_stripped():
    fenced = "```\n" + _plan_json(1) + "\n```"
    adapter = FakeAdapter([fenced])
    plan = run_planner(_make_context(), "medium", adapter)
    assert isinstance(plan, Plan)


# ── Requirement traceability ──────────────────────────────────────────────────

def test_required_assertion_with_source_passes():
    adapter = FakeAdapter([_plan_json(1)])
    plan = run_planner(_make_context(), "medium", adapter)
    a = plan.scenarios[0].assertions[0]
    assert a.kind.value == "required"
    assert a.source is not None


def test_assumption_assertion_without_source_is_valid():
    adapter = FakeAdapter([_plan_json(1, assertion_kind="assumption",
                                      assertion_source=None)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.scenarios[0].assertions[0].source is None


def test_exploratory_assertion_without_source_is_valid():
    adapter = FakeAdapter([_plan_json(1, assertion_kind="exploratory",
                                      assertion_source=None)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.scenarios[0].assertions[0].source is None


def test_required_assertion_without_source_triggers_repair():
    """REQUIRED+no source fails validate_plan; planner must repair or fail."""
    bad = json.loads(_plan_json(1))
    bad["scenarios"][0]["assertions"][0]["kind"] = "required"
    bad["scenarios"][0]["assertions"][0]["source"] = None
    # First call returns bad plan, second returns valid
    adapter = FakeAdapter([json.dumps(bad), _plan_json(1)])
    plan = run_planner(_make_context(), "medium", adapter)
    # After repair, result is valid
    assert plan.scenarios[0].assertions[0].source is not None


def test_source_kind_notes_preserved():
    adapter = FakeAdapter([_plan_json(1)])
    plan = run_planner(_make_context(), "medium", adapter)
    src = plan.scenarios[0].assertions[0].source
    assert src.kind == "notes"
    assert src.path == "changes.md"
    assert src.excerpt == "Improved login flow"


def test_source_kind_diff_preserved():
    diff_src = {"kind": "diff", "path": "git diff main",
                "excerpt": "+def login(): pass"}
    adapter = FakeAdapter([_plan_json(1, assertion_source=diff_src)])
    plan = run_planner(_make_context(diff=diff_src["excerpt"]), "medium", adapter)
    assert plan.scenarios[0].assertions[0].source.kind == "diff"


def test_source_kind_readme_preserved():
    readme_src = {"kind": "readme", "path": "README.md",
                  "excerpt": "## Auth\nOAuth2 flow"}
    adapter = FakeAdapter([_plan_json(1, assertion_source=readme_src)])
    plan = run_planner(_make_context(readme=readme_src["excerpt"]), "medium", adapter)
    assert plan.scenarios[0].assertions[0].source.kind == "readme"


def test_coverage_suggestions_passed_through():
    adapter = FakeAdapter([_plan_json(1, suggestions=["Test reset password",
                                                       "Test empty state"])])
    plan = run_planner(_make_context(), "medium", adapter)
    assert "Test reset password" in plan.coverage_suggestions
    assert "Test empty state" in plan.coverage_suggestions


# ── Mutation labeling ─────────────────────────────────────────────────────────

def test_requires_mutations_true_preserved():
    adapter = FakeAdapter([_plan_json(1, mutations=True)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.scenarios[0].requires_mutations is True


def test_requires_mutations_false_preserved():
    adapter = FakeAdapter([_plan_json(1, mutations=False)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.scenarios[0].requires_mutations is False


def test_mixed_mutation_scenarios():
    data = json.loads(_plan_json(3))
    data["scenarios"][0]["requires_mutations"] = True
    data["scenarios"][1]["requires_mutations"] = False
    data["scenarios"][2]["requires_mutations"] = True
    adapter = FakeAdapter([json.dumps(data)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.scenarios[0].requires_mutations is True
    assert plan.scenarios[1].requires_mutations is False
    assert plan.scenarios[2].requires_mutations is True


# ── Ambiguous requirements preserved as assumptions ───────────────────────────

def test_ambiguous_requirement_uses_assumption_kind():
    """Assertions without a clear requirement basis should be assumption, not required."""
    data = json.loads(_plan_json(1))
    data["scenarios"][0]["assertions"][0]["kind"] = "assumption"
    data["scenarios"][0]["assertions"][0]["source"] = None
    data["scenarios"][0]["assertions"][0]["description"] = "Layout looks correct"
    adapter = FakeAdapter([json.dumps(data)])
    plan = run_planner(_make_context(), "medium", adapter)
    a = plan.scenarios[0].assertions[0]
    assert a.kind.value == "assumption"
    assert a.source is None          # ambiguous — no source traceable


def test_assumption_cannot_masquerade_as_required():
    """Supplying kind=required without source is caught by validate_plan."""
    bad = json.loads(_plan_json(1))
    bad["scenarios"][0]["assertions"][0]["kind"] = "required"
    bad["scenarios"][0]["assertions"][0]["source"] = None
    # Both attempts fail → PlanningError, not silent pass
    adapter = FakeAdapter([json.dumps(bad), json.dumps(bad)])
    with pytest.raises(PlanningError):
        run_planner(_make_context(), "medium", adapter)


# ── Prerequisites preserved ───────────────────────────────────────────────────

def test_prerequisites_preserved():
    data = json.loads(_plan_json(2))
    data["scenarios"][1]["prerequisites"] = ["s-001"]
    adapter = FakeAdapter([json.dumps(data)])
    plan = run_planner(_make_context(), "medium", adapter)
    assert plan.scenarios[1].prerequisites == ["s-001"]


# ── CLI: --plan-only flag ─────────────────────────────────────────────────────

def test_plan_only_flag_exists_in_check_parser():
    from harness.cli import build_parser
    parser = build_parser()
    for action in parser._actions:
        if hasattr(action, "_name_parser_map"):
            check_parser = action._name_parser_map.get("check")
            if check_parser:
                args = check_parser.parse_args(["--notes", "x.md", "--plan-only"])
                assert args.plan_only is True
                return
    pytest.fail("'check' subparser not found")


def test_plan_only_defaults_to_false():
    from harness.cli import build_parser
    parser = build_parser()
    for action in parser._actions:
        if hasattr(action, "_name_parser_map"):
            check_parser = action._name_parser_map.get("check")
            if check_parser:
                args = check_parser.parse_args(["--notes", "x.md"])
                assert args.plan_only is False
                return
    pytest.fail("'check' subparser not found")


def test_repair_preserves_context_and_failed_output():
    ctx = _make_context(readme="README_MARKER", diff="DIFF_MARKER")
    bad = '{"scenarios": INVALID_JSON}'
    adapter = FakeAdapter([bad, _plan_json()])
    run_planner(ctx, "low", adapter)
    repair = adapter.calls[1][1]
    for text in (ctx.notes_text, "README_MARKER", "DIFF_MARKER", bad):
        assert text in repair


def test_invented_source_cannot_be_accepted_after_repair():
    data = json.loads(_plan_json())
    data["scenarios"][0]["assertions"][0]["source"]["excerpt"] = "invented requirement"
    adapter = FakeAdapter([json.dumps(data), json.dumps(data)])
    with pytest.raises(PlanningError, match="source excerpt not found"):
        run_planner(_make_context(), "low", adapter)
