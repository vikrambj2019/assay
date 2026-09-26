"""Tests for the test-plan schema (Task 10).

All pure unit tests — no browser, no LLM, no network, no git.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.plan import (
    SCHEMA_VERSION,
    Assertion,
    AssertionKind,
    Plan,
    Scenario,
    SourceRef,
    load_plan,
    make_plan,
    save_plan,
    validate_plan,
)


# ── Factories for minimal valid objects ───────────────────────────────────────

def _source(kind: str = "notes", path: str = "changes.md",
            excerpt: str = "Login updated") -> SourceRef:
    return SourceRef(kind=kind, path=path, excerpt=excerpt)


def _assertion(id: str = "a-001", kind: AssertionKind = AssertionKind.REQUIRED,
               with_source: bool = True) -> Assertion:
    return Assertion(
        id=id,
        description="User sees the dashboard",
        kind=kind,
        source=_source() if with_source else None,
    )


def _scenario(
    id: str = "s-001",
    title: str = "Login",
    prerequisites: list[str] | None = None,
    assertions: list[Assertion] | None = None,
    skip: bool = False,
) -> Scenario:
    return Scenario(
        id=id,
        title=title,
        goal=f"Log in as the test user",
        prerequisites=prerequisites or [],
        assertions=assertions or [_assertion()],
        requires_mutations=False,
        reason="Core flow",
        source=_source(),
        skip=skip,
    )


def _valid_plan(scenarios: list[Scenario] | None = None) -> Plan:
    return make_plan(
        depth="medium",
        scenarios=scenarios or [_scenario()],
        coverage_suggestions=["Test invalid credentials"],
    )


# ── AssertionKind ─────────────────────────────────────────────────────────────

def test_assertion_kind_values():
    assert AssertionKind.REQUIRED.value == "required"
    assert AssertionKind.ASSUMPTION.value == "assumption"
    assert AssertionKind.EXPLORATORY.value == "exploratory"


def test_assertion_kind_from_string():
    assert AssertionKind("required") is AssertionKind.REQUIRED
    assert AssertionKind("assumption") is AssertionKind.ASSUMPTION
    assert AssertionKind("exploratory") is AssertionKind.EXPLORATORY


# ── SourceRef round-trip ──────────────────────────────────────────────────────

def test_source_ref_round_trip():
    s = SourceRef(kind="notes", path="changes.md", excerpt="Login flow updated")
    assert SourceRef.from_dict(s.to_dict()) == s


@pytest.mark.parametrize("kind", ["notes", "readme", "diff"])
def test_source_ref_accepts_all_context_kinds(kind):
    s = SourceRef(kind=kind, path="somefile", excerpt="text")
    rt = SourceRef.from_dict(s.to_dict())
    assert rt.kind == kind


def test_source_ref_diff_path():
    """diff SourceRef can carry a git diff reference as the path."""
    s = SourceRef(kind="diff", path="git diff main", excerpt="+def new_func(): pass")
    assert SourceRef.from_dict(s.to_dict()).path == "git diff main"


# ── Assertion round-trip ──────────────────────────────────────────────────────

def test_assertion_required_with_source_round_trips():
    a = Assertion(
        id="a-001",
        description="Dashboard is visible",
        kind=AssertionKind.REQUIRED,
        source=_source(),
    )
    rt = Assertion.from_dict(a.to_dict())
    assert rt.id == a.id
    assert rt.kind is AssertionKind.REQUIRED
    assert rt.source is not None
    assert rt.source.excerpt == a.source.excerpt


def test_assertion_assumption_without_source_round_trips():
    a = Assertion(
        id="a-002",
        description="Error message is styled correctly",
        kind=AssertionKind.ASSUMPTION,
        source=None,
    )
    rt = Assertion.from_dict(a.to_dict())
    assert rt.kind is AssertionKind.ASSUMPTION
    assert rt.source is None


def test_assertion_exploratory_without_source_round_trips():
    a = Assertion(
        id="a-003",
        description="Pagination works",
        kind=AssertionKind.EXPLORATORY,
        source=None,
    )
    rt = Assertion.from_dict(a.to_dict())
    assert rt.kind is AssertionKind.EXPLORATORY
    assert rt.source is None


# ── Scenario round-trip ───────────────────────────────────────────────────────

def test_scenario_round_trip():
    s = _scenario(id="s-001", prerequisites=["s-000"],
                  assertions=[_assertion("a-001"), _assertion("a-002", AssertionKind.ASSUMPTION, False)])
    rt = Scenario.from_dict(s.to_dict())
    assert rt.id == s.id
    assert rt.prerequisites == ["s-000"]
    assert len(rt.assertions) == 2
    assert rt.assertions[1].kind is AssertionKind.ASSUMPTION


def test_scenario_skip_field_round_trips():
    s = _scenario(skip=True)
    rt = Scenario.from_dict(s.to_dict())
    assert rt.skip is True


def test_scenario_requires_mutations_round_trips():
    s = Scenario(id="s-m", title="Create item", goal="Create a record",
                 prerequisites=[], assertions=[_assertion()],
                 requires_mutations=True, reason="Mutation test", source=None)
    rt = Scenario.from_dict(s.to_dict())
    assert rt.requires_mutations is True


def test_scenario_source_ref_round_trips():
    s = _scenario()
    assert s.source is not None
    rt = Scenario.from_dict(s.to_dict())
    assert rt.source is not None
    assert rt.source.path == "changes.md"


# ── Plan round-trip ───────────────────────────────────────────────────────────

def test_plan_round_trip():
    plan = _valid_plan()
    rt = Plan.from_dict(plan.to_dict())
    assert rt.version == SCHEMA_VERSION
    assert rt.depth == "medium"
    assert len(rt.scenarios) == 1
    assert rt.coverage_suggestions == ["Test invalid credentials"]


def test_make_plan_sets_version_and_timestamp():
    plan = make_plan("high", [_scenario()])
    assert plan.version == SCHEMA_VERSION
    assert "T" in plan.created_at  # ISO format contains 'T'


def test_plan_coverage_suggestions_round_trip():
    suggestions = ["Test reset password", "Test empty state"]
    plan = make_plan("low", [_scenario()], coverage_suggestions=suggestions)
    rt = Plan.from_dict(plan.to_dict())
    assert rt.coverage_suggestions == suggestions


# ── validate_plan: valid cases ────────────────────────────────────────────────

def test_valid_plan_passes():
    validate_plan(_valid_plan())  # must not raise


def test_valid_plan_with_prerequisites_passes():
    s1 = _scenario(id="s-001")
    s2 = _scenario(id="s-002", prerequisites=["s-001"])
    validate_plan(_valid_plan([s1, s2]))


def test_assumption_assertion_without_source_is_valid():
    a = Assertion(id="a-1", description="UI looks correct",
                  kind=AssertionKind.ASSUMPTION, source=None)
    s = _scenario(assertions=[a])
    validate_plan(_valid_plan([s]))  # must not raise


def test_exploratory_assertion_without_source_is_valid():
    a = Assertion(id="a-1", description="Edge case check",
                  kind=AssertionKind.EXPLORATORY, source=None)
    s = _scenario(assertions=[a])
    validate_plan(_valid_plan([s]))  # must not raise


# ── validate_plan: empty plan ─────────────────────────────────────────────────

def test_empty_scenarios_raises():
    plan = make_plan("medium", scenarios=[])
    with pytest.raises(ValueError, match="no scenarios"):
        validate_plan(plan)


# ── validate_plan: duplicate IDs ─────────────────────────────────────────────

def test_duplicate_scenario_ids_raise():
    s1 = _scenario(id="s-001")
    s2 = _scenario(id="s-001")   # same ID
    plan = make_plan("medium", [s1, s2])
    with pytest.raises(ValueError, match="duplicate scenario ID"):
        validate_plan(plan)


# ── validate_plan: missing assertions ────────────────────────────────────────

def test_scenario_with_no_assertions_raises():
    s = Scenario(id="s-001", title="Empty", goal="do thing",
                 prerequisites=[], assertions=[],
                 requires_mutations=False, reason="test")
    plan = make_plan("medium", [s])
    with pytest.raises(ValueError, match="no assertions"):
        validate_plan(plan)


# ── validate_plan: assumptions masquerading as requirements ───────────────────

def test_required_assertion_without_source_raises():
    a = Assertion(id="a-001", description="Must work",
                  kind=AssertionKind.REQUIRED, source=None)
    s = _scenario(assertions=[a])
    plan = make_plan("medium", [s])
    with pytest.raises(ValueError, match="masquerade"):
        validate_plan(plan)


def test_required_assertion_with_source_does_not_raise():
    a = Assertion(id="a-001", description="Must work",
                  kind=AssertionKind.REQUIRED, source=_source())
    s = _scenario(assertions=[a])
    validate_plan(_valid_plan([s]))  # must not raise


def test_duplicate_assertion_ids_are_rejected():
    duplicate = [_assertion("same"), _assertion("same", AssertionKind.ASSUMPTION, False)]
    with pytest.raises(ValueError, match="duplicate assertion ID"):
        validate_plan(_valid_plan([_scenario(assertions=duplicate)]))


def test_invalid_depth_is_rejected():
    plan = _valid_plan()
    plan.depth = "extreme"
    with pytest.raises(ValueError, match="invalid plan depth"):
        validate_plan(plan)


def test_save_plan_validates_before_writing(tmp_path):
    plan = _valid_plan()
    plan.scenarios[0].assertions = []
    with pytest.raises(ValueError, match="no assertions"):
        save_plan(plan, tmp_path)
    assert not (tmp_path / "plan.json").exists()


# ── validate_plan: unknown prerequisites ─────────────────────────────────────

def test_unknown_prerequisite_raises():
    s = _scenario(id="s-001", prerequisites=["s-999"])
    plan = make_plan("medium", [s])
    with pytest.raises(ValueError, match="prerequisite.*does not exist"):
        validate_plan(plan)


# ── validate_plan: cycles ─────────────────────────────────────────────────────

def test_self_referencing_cycle_raises():
    s = _scenario(id="s-001", prerequisites=["s-001"])
    plan = make_plan("medium", [s])
    with pytest.raises(ValueError, match="cycle"):
        validate_plan(plan)


def test_two_node_cycle_raises():
    s1 = _scenario(id="s-001", prerequisites=["s-002"])
    s2 = _scenario(id="s-002", prerequisites=["s-001"])
    plan = make_plan("medium", [s1, s2])
    with pytest.raises(ValueError, match="cycle"):
        validate_plan(plan)


def test_three_node_cycle_raises():
    s1 = _scenario(id="s-001", prerequisites=["s-003"])
    s2 = _scenario(id="s-002", prerequisites=["s-001"])
    s3 = _scenario(id="s-003", prerequisites=["s-002"])
    plan = make_plan("medium", [s1, s2, s3])
    with pytest.raises(ValueError, match="cycle"):
        validate_plan(plan)


# ── validate_plan: multiple errors reported together ─────────────────────────

def test_multiple_errors_reported_together():
    """validate_plan collects all structural errors before raising."""
    # Two duplicate IDs AND one missing-assertions scenario
    s1 = _scenario(id="s-dup")
    s2 = _scenario(id="s-dup")
    s3 = Scenario(id="s-no-assert", title="X", goal="g",
                  prerequisites=[], assertions=[],
                  requires_mutations=False, reason="r")
    plan = make_plan("medium", [s1, s2, s3])
    with pytest.raises(ValueError) as exc_info:
        validate_plan(plan)
    msg = str(exc_info.value)
    assert "duplicate" in msg
    assert "no assertions" in msg


# ── validate_plan: wrong version ──────────────────────────────────────────────

def test_wrong_version_raises():
    plan = _valid_plan()
    plan.version = "99"
    with pytest.raises(ValueError, match="unrecognised plan version"):
        validate_plan(plan)


# ── save_plan / load_plan ─────────────────────────────────────────────────────

def test_save_plan_creates_plan_json(tmp_path):
    plan = _valid_plan()
    path = save_plan(plan, tmp_path)
    assert path == tmp_path / "plan.json"
    assert path.exists()


def test_save_plan_content_is_valid_json(tmp_path):
    plan = _valid_plan()
    save_plan(plan, tmp_path)
    data = json.loads((tmp_path / "plan.json").read_text())
    assert data["version"] == SCHEMA_VERSION
    assert len(data["scenarios"]) == 1


def test_load_plan_round_trips_correctly(tmp_path):
    original = _valid_plan()
    save_plan(original, tmp_path)
    loaded = load_plan(tmp_path / "plan.json")
    assert loaded.version == original.version
    assert loaded.depth == original.depth
    assert len(loaded.scenarios) == 1
    assert loaded.scenarios[0].id == original.scenarios[0].id
    assert loaded.coverage_suggestions == original.coverage_suggestions


def test_load_plan_validates_on_load(tmp_path):
    """load_plan rejects a plan that fails validation."""
    # Write a plan with no scenarios directly (bypass validate_plan)
    bad = {"version": SCHEMA_VERSION, "created_at": "2026-01-01T00:00:00+00:00",
           "depth": "medium", "scenarios": [], "coverage_suggestions": []}
    (tmp_path / "plan.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="no scenarios"):
        load_plan(tmp_path / "plan.json")


def test_load_plan_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_plan(tmp_path / "nonexistent.json")


def test_save_plan_creates_directory(tmp_path):
    out = tmp_path / "nested" / "output"
    save_plan(_valid_plan(), out)
    assert (out / "plan.json").exists()


# ── JSON field presence ───────────────────────────────────────────────────────

def test_plan_json_has_all_required_fields(tmp_path):
    plan = _valid_plan()
    save_plan(plan, tmp_path)
    data = json.loads((tmp_path / "plan.json").read_text())
    for key in ("version", "created_at", "depth", "scenarios", "coverage_suggestions"):
        assert key in data, f"missing key: {key}"


def test_scenario_json_has_all_required_fields(tmp_path):
    plan = _valid_plan()
    save_plan(plan, tmp_path)
    data = json.loads((tmp_path / "plan.json").read_text())
    s = data["scenarios"][0]
    for key in ("id", "title", "goal", "prerequisites", "assertions",
                "requires_mutations", "reason", "source", "skip"):
        assert key in s, f"missing key: {key}"


def test_assertion_json_has_all_required_fields(tmp_path):
    plan = _valid_plan()
    save_plan(plan, tmp_path)
    data = json.loads((tmp_path / "plan.json").read_text())
    a = data["scenarios"][0]["assertions"][0]
    for key in ("id", "description", "kind", "source"):
        assert key in a, f"missing key: {key}"


# ── Source reference traceability ─────────────────────────────────────────────

def test_scenario_can_reference_notes_section(tmp_path):
    src = SourceRef(kind="notes", path="changes.md",
                    excerpt="## Login\nFixed the token expiry bug")
    s = Scenario(id="s-1", title="Login", goal="Log in",
                 prerequisites=[], assertions=[Assertion("a-1", "Works", AssertionKind.REQUIRED, src)],
                 requires_mutations=False, reason="Needed", source=src)
    plan = make_plan("medium", [s])
    save_plan(plan, tmp_path)
    loaded = load_plan(tmp_path / "plan.json")
    assert loaded.scenarios[0].assertions[0].source.kind == "notes"
    assert "token expiry" in loaded.scenarios[0].assertions[0].source.excerpt


def test_scenario_can_reference_readme_section(tmp_path):
    src = SourceRef(kind="readme", path="README.md",
                    excerpt="## Authentication\nOAuth2 flow")
    a = Assertion("a-1", "OAuth flow works", AssertionKind.REQUIRED, src)
    s = _scenario(assertions=[a])
    plan = make_plan("medium", [s])
    save_plan(plan, tmp_path)
    loaded = load_plan(tmp_path / "plan.json")
    assert loaded.scenarios[0].assertions[0].source.kind == "readme"


def test_scenario_can_reference_diff_path(tmp_path):
    src = SourceRef(kind="diff", path="git diff main",
                    excerpt="+def new_endpoint(): pass")
    a = Assertion("a-1", "New endpoint works", AssertionKind.REQUIRED, src)
    s = _scenario(assertions=[a])
    plan = make_plan("medium", [s])
    save_plan(plan, tmp_path)
    loaded = load_plan(tmp_path / "plan.json")
    assert loaded.scenarios[0].assertions[0].source.kind == "diff"
    assert "new_endpoint" in loaded.scenarios[0].assertions[0].source.excerpt
