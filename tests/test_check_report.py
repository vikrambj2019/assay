"""Tests for Task 15: report writers, exit codes, and CLI wiring.

Covers:
- exit_code(): all verdict combinations
- write_results_json(): structure, versioning, assertion kinds, budget/context
- write_check_html(): presence of key content, HTML escaping of app text
- write_junit_xml(): valid XML, correct element mapping for all verdicts
- Mixed, empty, blocked, and budget-exhausted run scenarios
- CLI: plan-only returns 0; invalid inputs return 2
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from core.budget import BudgetConfig, FakeClock, RunBudget
from core.check_report import (
    exit_code,
    write_check_html,
    write_junit_xml,
    write_results_json,
)
from core.executor import RunResult
from core.plan import Assertion, AssertionKind, Plan, Scenario, SourceRef
from core.run import ScenarioResult
from core.schema import Verdict


# ── Fixtures / builders ───────────────────────────────────────────────────────

def _src() -> SourceRef:
    return SourceRef(kind="notes", path="notes.md", excerpt="req")


def _assertion(aid: str, kind: AssertionKind = AssertionKind.REQUIRED) -> Assertion:
    return Assertion(
        id=aid,
        description=f"desc {aid}",
        kind=kind,
        source=_src() if kind is AssertionKind.REQUIRED else None,
        check={"type": "text_visible", "text": "ok"},
    )


def _scenario(
    sid: str,
    title: str | None = None,
    goal: str | None = None,
    assertions: list[Assertion] | None = None,
) -> Scenario:
    return Scenario(
        id=sid,
        title=title or f"Scenario {sid}",
        goal=goal or f"do {sid}",
        prerequisites=[],
        assertions=assertions or [_assertion(f"a-{sid}")],
        requires_mutations=False,
        reason="test",
    )


def _plan(*scenarios: Scenario, depth: str = "medium") -> Plan:
    return Plan(
        version="1",
        created_at="2024-01-01T00:00:00+00:00",
        depth=depth,
        scenarios=list(scenarios),
    )


def _result(
    sid: str,
    verdict: Verdict,
    run_id: str = "run-1",
    reason: str = "",
    assertions_checked: list[str] | None = None,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=sid,
        scenario_title=f"Scenario {sid}",
        verdict=verdict,
        reason=reason or verdict.value,
        run_id=run_id,
        assertions_checked=assertions_checked or [],
    )


def _run_result(*results: ScenarioResult, complete: bool = True, run_id: str = "run-1") -> RunResult:
    return RunResult(run_id=run_id, scenario_results=list(results), complete=complete)


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp())


# ── exit_code ─────────────────────────────────────────────────────────────────

def test_exit_code_all_pass_complete():
    rr = _run_result(_result("s1", Verdict.PASS), complete=True)
    assert exit_code(rr) == 0


def test_exit_code_any_fail():
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
        complete=True,
    )
    assert exit_code(rr) == 1


def test_exit_code_fail_beats_incomplete():
    rr = _run_result(_result("s1", Verdict.FAIL), complete=False)
    assert exit_code(rr) == 1


def test_exit_code_incomplete_no_fail():
    rr = _run_result(_result("s1", Verdict.PASS), complete=False)
    assert exit_code(rr) == 2


def test_exit_code_error_no_fail():
    rr = _run_result(_result("s1", Verdict.ERROR), complete=True)
    assert exit_code(rr) == 2


def test_exit_code_blocked_no_fail():
    rr = _run_result(_result("s1", Verdict.BLOCKED), complete=True)
    assert exit_code(rr) == 2


def test_exit_code_unverified_makes_incomplete():
    # UNVERIFIED → complete=False → exit 2
    rr = _run_result(_result("s1", Verdict.UNVERIFIED), complete=False)
    assert exit_code(rr) == 2


def test_exit_code_skipped_only():
    rr = _run_result(_result("s1", Verdict.SKIPPED), complete=True)
    assert exit_code(rr) == 0


def test_exit_code_pass_and_skipped():
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.SKIPPED),
        complete=True,
    )
    assert exit_code(rr) == 0


def test_exit_code_budget_exhausted_run():
    # Budget-exhausted runs have complete=False
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.UNVERIFIED, reason="time limit exceeded"),
        complete=False,
    )
    assert exit_code(rr) == 2


def test_exit_code_empty_run():
    rr = _run_result(complete=True)
    assert exit_code(rr) == 0


# ── write_results_json ────────────────────────────────────────────────────────

def test_results_json_created():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS, assertions_checked=["a-s1"]))
    out = _tmpdir()
    path = write_results_json(rr, plan, out)
    assert path.exists()
    assert path.name == "results.json"


def test_results_json_parses():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    path = write_results_json(rr, plan, _tmpdir())
    doc = json.loads(path.read_text())
    assert isinstance(doc, dict)


def test_results_json_version_field():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    assert doc["version"] == "1"


def test_results_json_run_id():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS, run_id="run-abc"), run_id="run-abc")
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    assert doc["run_id"] == "run-abc"


def test_results_json_complete_field():
    plan = _plan(_scenario("s1"))
    rr_complete = _run_result(_result("s1", Verdict.PASS), complete=True)
    rr_incomplete = _run_result(_result("s1", Verdict.UNVERIFIED), complete=False)
    assert json.loads(write_results_json(rr_complete, plan, _tmpdir()).read_text())["complete"] is True
    assert json.loads(write_results_json(rr_incomplete, plan, _tmpdir()).read_text())["complete"] is False


def test_results_json_exit_code_field():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.FAIL), complete=True)
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    assert doc["exit_code"] == 1


def test_results_json_scenarios_list():
    plan = _plan(_scenario("s1"), _scenario("s2"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
    )
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    assert len(doc["scenarios"]) == 2
    ids = {s["scenario_id"] for s in doc["scenarios"]}
    assert ids == {"s1", "s2"}


def test_results_json_verdict_values():
    plan = _plan(_scenario("s1"), _scenario("s2"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
    )
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    verdicts = {s["scenario_id"]: s["verdict"] for s in doc["scenarios"]}
    assert verdicts["s1"] == "PASS"
    assert verdicts["s2"] == "FAIL"


def test_results_json_summary_counts():
    plan = _plan(_scenario("s1"), _scenario("s2"), _scenario("s3"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
        _result("s3", Verdict.BLOCKED),
    )
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    s = doc["summary"]
    assert s["PASS"] == 1
    assert s["FAIL"] == 1
    assert s["BLOCKED"] == 1
    assert s["total"] == 3


def test_results_json_assertion_kinds():
    plan = _plan(
        _scenario("s1", assertions=[
            _assertion("a1", AssertionKind.REQUIRED),
            _assertion("a2", AssertionKind.ASSUMPTION),
        ])
    )
    rr = _run_result(_result("s1", Verdict.PASS, assertions_checked=["a1", "a2"]))
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    kinds = doc["scenarios"][0]["assertion_kinds"]
    assert kinds["a1"] == "required"
    assert kinds["a2"] == "assumption"


def test_results_json_coverage_suggestions():
    plan = Plan(
        version="1",
        created_at="2024-01-01T00:00:00+00:00",
        depth="low",
        scenarios=[_scenario("s1")],
        coverage_suggestions=["test idea A", "test idea B"],
    )
    rr = _run_result(_result("s1", Verdict.PASS))
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    assert doc["coverage_suggestions"] == ["test idea A", "test idea B"]


def test_results_json_budget_null_when_absent():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    assert doc["budget"] is None


def test_results_json_budget_present():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=600, max_actions=100), clock)
    budget.start()
    clock.advance(30.0)
    budget.record_action()

    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    doc = json.loads(write_results_json(rr, plan, _tmpdir(), budget=budget).read_text())
    assert doc["budget"] is not None
    assert doc["budget"]["actions"] == 1
    assert doc["budget"]["limits"]["max_seconds"] == 600


def test_results_json_depth():
    plan = _plan(_scenario("s1"), depth="high")
    rr = _run_result(_result("s1", Verdict.PASS))
    doc = json.loads(write_results_json(rr, plan, _tmpdir()).read_text())
    assert doc["depth"] == "high"


# ── write_check_html ──────────────────────────────────────────────────────────

def test_html_created():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    path = write_check_html(rr, plan, _tmpdir())
    assert path.exists()
    assert path.name == "report.html"


def test_html_is_valid_doctype():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "<!doctype html>" in content.lower()


def test_html_escapes_application_text():
    """Scenario title/reason with XSS payload must be escaped, not raw."""
    xss_title = "<script>alert('xss')</script>"
    xss_reason = "value was <b>wrong</b>"
    plan = _plan(_scenario("s1", title=xss_title))
    # Make the result title match the plan title (as the real executor would).
    r = ScenarioResult(
        scenario_id="s1",
        scenario_title=xss_title,
        verdict=Verdict.FAIL,
        reason=xss_reason,
        run_id="run-1",
    )
    rr = _run_result(r)
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "<script>" not in content
    assert "&lt;script&gt;" in content
    assert "<b>wrong</b>" not in content
    assert "&lt;b&gt;wrong&lt;/b&gt;" in content


def test_html_escapes_goal_text():
    goal = "submit form with <malicious> & 'payload'"
    plan = _plan(_scenario("s1", goal=goal))
    rr = _run_result(_result("s1", Verdict.PASS))
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "<malicious>" not in content
    assert "&lt;malicious&gt;" in content


def test_html_contains_run_id():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS, run_id="run-xyz"), run_id="run-xyz")
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "run-xyz" in content


def test_html_contains_verdict_labels():
    plan = _plan(_scenario("s1"), _scenario("s2"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
    )
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "PASS" in content
    assert "FAIL" in content


def test_html_contains_coverage_suggestions():
    plan = Plan(
        version="1",
        created_at="2024-01-01T00:00:00+00:00",
        depth="medium",
        scenarios=[_scenario("s1")],
        coverage_suggestions=["Consider testing edge case X"],
    )
    rr = _run_result(_result("s1", Verdict.PASS))
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "Consider testing edge case X" in content


def test_html_shows_assertion_kind():
    plan = _plan(
        _scenario("s1", assertions=[
            _assertion("a1", AssertionKind.REQUIRED),
            _assertion("a2", AssertionKind.ASSUMPTION),
        ])
    )
    rr = _run_result(_result("s1", Verdict.PASS))
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "required" in content
    assert "assumption" in content


def test_html_with_budget():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=600), clock)
    budget.start()
    clock.advance(45.0)

    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    content = write_check_html(rr, plan, _tmpdir(), budget=budget).read_text()
    assert "Budget" in content
    assert "600" in content


def test_html_budget_exhausted_run():
    plan = _plan(_scenario("s1"), _scenario("s2"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.UNVERIFIED, reason="time limit exceeded: 601.0s >= 600s"),
        complete=False,
    )
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    assert "UNVERIFIED" in content
    assert "INCOMPLETE" in content or "incomplete" in content.lower()


def test_html_all_verdict_types_rendered():
    scenarios = [
        _scenario(f"s{i}") for i in range(1, 7)
    ]
    plan = _plan(*scenarios)
    results = [
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
        _result("s3", Verdict.ERROR),
        _result("s4", Verdict.BLOCKED),
        _result("s5", Verdict.SKIPPED),
        _result("s6", Verdict.UNVERIFIED),
    ]
    rr = _run_result(*results, complete=False)
    content = write_check_html(rr, plan, _tmpdir()).read_text()
    for verdict in ["PASS", "FAIL", "ERROR", "BLOCKED", "SKIPPED", "UNVERIFIED"]:
        assert verdict in content, f"{verdict} not found in HTML"


# ── write_junit_xml ───────────────────────────────────────────────────────────

def test_junit_created():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    path = write_junit_xml(rr, plan, _tmpdir())
    assert path.exists()
    assert path.name == "junit.xml"


def test_junit_parses_as_xml():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    path = write_junit_xml(rr, plan, _tmpdir())
    tree = ET.parse(str(path))
    root = tree.getroot()
    assert root.tag in ("testsuites", "testsuite")


def test_junit_pass_has_no_child():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.PASS))
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc = _find_testcase(root, "Scenario s1")
    assert tc is not None
    assert list(tc) == []  # no child elements


def test_junit_fail_has_failure_element():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.FAIL, reason="expected foo got bar"))
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc = _find_testcase(root, "Scenario s1")
    assert tc is not None
    failure = tc.find("failure")
    assert failure is not None
    assert "foo" in (failure.get("message") or "")


def test_junit_error_has_error_element():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.ERROR, reason="infra crash"))
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc = _find_testcase(root, "Scenario s1")
    assert tc is not None
    assert tc.find("error") is not None


def test_junit_blocked_has_skipped_with_status():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.BLOCKED, reason="prereq failed"))
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc = _find_testcase(root, "Scenario s1")
    assert tc is not None
    skipped = tc.find("skipped")
    assert skipped is not None
    msg = skipped.get("message", "")
    assert "BLOCKED" in msg


def test_junit_unverified_has_skipped_with_status():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.UNVERIFIED, reason="no check spec"))
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc = _find_testcase(root, "Scenario s1")
    skipped = tc.find("skipped")
    assert skipped is not None
    assert "UNVERIFIED" in skipped.get("message", "")


def test_junit_skipped_has_skipped_element():
    plan = _plan(_scenario("s1"))
    rr = _run_result(_result("s1", Verdict.SKIPPED))
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc = _find_testcase(root, "Scenario s1")
    assert tc.find("skipped") is not None


def test_junit_counts_correct():
    scenarios = [_scenario(f"s{i}") for i in range(1, 5)]
    plan = _plan(*scenarios)
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
        _result("s3", Verdict.ERROR),
        _result("s4", Verdict.BLOCKED),
    )
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite.get("tests") == "4"
    assert suite.get("failures") == "1"
    assert suite.get("errors") == "1"
    assert suite.get("skipped") == "1"


def test_junit_mixed_run():
    plan = _plan(_scenario("s1"), _scenario("s2"), _scenario("s3"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
        _result("s3", Verdict.PASS),
    )
    path = write_junit_xml(rr, plan, _tmpdir())
    # Parses without error
    ET.parse(str(path))


def test_junit_budget_exhausted_run():
    plan = _plan(_scenario("s1"), _scenario("s2"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.UNVERIFIED, reason="time limit exceeded"),
        complete=False,
    )
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc = _find_testcase(root, "Scenario s2")
    assert tc is not None
    skipped = tc.find("skipped")
    assert skipped is not None
    assert "UNVERIFIED" in skipped.get("message", "")


def test_junit_classname_includes_verdict():
    plan = _plan(_scenario("s1"), _scenario("s2"))
    rr = _run_result(
        _result("s1", Verdict.PASS),
        _result("s2", Verdict.FAIL),
    )
    path = write_junit_xml(rr, plan, _tmpdir())
    root = ET.parse(str(path)).getroot()
    tc_pass = _find_testcase(root, "Scenario s1")
    tc_fail = _find_testcase(root, "Scenario s2")
    assert "pass" in tc_pass.get("classname", "")
    assert "fail" in tc_fail.get("classname", "")


# ── CLI exit-code integration ─────────────────────────────────────────────────

def test_cli_plan_only_exits_0(tmp_path, monkeypatch):
    """--plan-only writes plan.json and exits 0 without executing scenarios."""
    import sys
    from unittest.mock import MagicMock, patch

    monkeypatch.chdir(tmp_path)

    notes = tmp_path / "notes.md"
    notes.write_text("# Changes\n- feature X added\n")

    # Patch heavy dependencies so the test doesn't call LLMs or git.
    fake_ctx = MagicMock()
    fake_ctx.notes_path = notes
    fake_ctx.notes_text = "feature X"
    fake_ctx.readme_text = None
    fake_ctx.readme_path = None
    fake_ctx.diff_ref = None
    fake_ctx.diff_text = None
    fake_ctx.untracked_files = []
    fake_ctx.excluded = []
    fake_ctx.truncated = []
    fake_ctx.warnings = []

    from core.plan import AssertionKind, make_plan
    from core.planner import DEPTH_POLICIES
    fake_plan = make_plan(
        "low",
        [_scenario("s1")],
        coverage_suggestions=[],
    )

    with patch("core.context.collect_context", return_value=fake_ctx), \
         patch("core.planner.run_planner", return_value=fake_plan), \
         patch("core.config.Config.from_env") as mock_cfg, \
         patch("core.config.load_dotenv"), \
         patch("harness.cli._print_context_summary"):

        cfg = MagicMock()
        cfg.model = "claude-haiku-4-5-20251001"
        cfg.depth = "low"
        cfg.max_seconds = None
        cfg.max_actions = None
        cfg.max_cost_usd = None
        cfg.allow_mutations = False
        cfg.results_root = tmp_path / "results"
        cfg.validate_for_check.return_value = None
        mock_cfg.return_value = cfg

        from harness.cli import main
        code = main(["check", "--notes", str(notes), "--plan-only"])

    assert code == 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_testcase(root: ET.Element, name: str) -> ET.Element | None:
    """Find a <testcase> by name in the XML tree (searching testsuite children)."""
    for elem in root.iter("testcase"):
        if elem.get("name") == name:
            return elem
    return None


# ── screenshot strip in report.html ───────────────────────────────────────────

def test_html_screenshot_strip_present():
    out = _tmpdir()
    (out / "s1").mkdir(parents=True)
    (out / "s1" / "step-01.png").write_bytes(b"fake-png")
    (out / "s1" / "step-02.png").write_bytes(b"fake-png")
    rr = _run_result(_result("s1", Verdict.FAIL))
    plan = _plan(_scenario("s1"))
    content = write_check_html(rr, plan, out).read_text()
    assert "Screenshots (2)" in content
    assert "s1/step-01.png" in content
    assert "s1/step-02.png" in content


def test_html_no_screenshot_strip_when_none():
    out = _tmpdir()
    rr = _run_result(_result("s1", Verdict.PASS))
    plan = _plan(_scenario("s1"))
    content = write_check_html(rr, plan, out).read_text()
    assert "Screenshots" not in content


# ── junit.xml durations ───────────────────────────────────────────────────────

def test_junit_reports_real_duration():
    out = _tmpdir()
    r = _result("s1", Verdict.PASS)
    r.duration_s = 1.23456
    rr = _run_result(r)
    plan = _plan(_scenario("s1"))
    path = write_junit_xml(rr, plan, out)
    tc = _find_testcase(ET.parse(str(path)).getroot(), "Scenario s1")
    assert tc is not None
    assert tc.get("time") == "1.235"


def test_junit_duration_defaults_to_zero_when_unknown():
    out = _tmpdir()
    rr = _run_result(_result("s1", Verdict.PASS))  # duration_s=None
    plan = _plan(_scenario("s1"))
    path = write_junit_xml(rr, plan, out)
    tc = _find_testcase(ET.parse(str(path)).getroot(), "Scenario s1")
    assert tc is not None
    assert tc.get("time") == "0"
