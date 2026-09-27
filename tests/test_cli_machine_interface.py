"""CLI machine interface: --format json, --output, --plan, --only.

No LLM, no browser, no network. The browser adapter factory is replaced with
the executor's fake so real CLI code paths run end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.check_summary import NOT_SELECTED_REASON, SUMMARY_SCHEMA, file_sha256
from core.plan import Assertion, AssertionKind, Scenario, SourceRef, make_plan, save_plan
from tests.test_executor import FakeAdapterFactory, FakeScenarioAdapter


# ── Fixtures and helpers ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Isolate from any developer .env / ASSAY_* variables."""
    import os

    for key in list(os.environ):
        if key.startswith(("ASSAY_", "BTA_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:4173/v/clean")
    monkeypatch.setenv("ASSAY_RESULTS_DIR", str(tmp_path / "results"))


def _req(aid: str, description: str, check: dict) -> Assertion:
    return Assertion(
        id=aid, description=description, kind=AssertionKind.REQUIRED,
        source=SourceRef(kind="notes", path="changes.md", excerpt=description), check=check,
    )


def _scenario(sid: str, prereqs: list[str] | None = None, check: dict | None = None) -> Scenario:
    return Scenario(
        id=sid, title=f"Scenario {sid}", goal=f"do {sid}", prerequisites=prereqs or [],
        assertions=[_req(f"a-{sid}", f"{sid} lands on the account page",
                         check or {"type": "url_contains", "value": "/account"})],
        requires_mutations=False, reason="test",
    )


def _write_plan(tmp_path: Path, *scenarios: Scenario) -> Path:
    plan = make_plan("low", list(scenarios))
    return save_plan(plan, tmp_path / "saved")


def _use_fake_browser(monkeypatch, adapters: dict[str, FakeScenarioAdapter]):
    factory = FakeAdapterFactory(adapters, default=FakeScenarioAdapter(url="http://localhost:4173/v/clean/account"))
    monkeypatch.setattr("harness.scenario.BrowserScenarioAdapterFactory", lambda *a, **k: factory)
    return factory


def _run_json(capsys, argv: list[str]) -> tuple[int, dict, str]:
    from harness.cli import main

    code = main(["check", *argv, "--format", "json"])
    out, err = capsys.readouterr()
    doc = json.loads(out)  # stdout must be exactly one JSON document
    return code, doc, err


# ── --format json ─────────────────────────────────────────────────────────────

def test_json_invalid_input_keeps_stdout_pure(capsys, monkeypatch):
    monkeypatch.delenv("ASSAY_BASE_URL")
    code, doc, err = _run_json(capsys, ["--notes", "missing.md"])
    assert code == 2
    assert doc["schema"] == SUMMARY_SCHEMA
    assert doc["status"] == "invalid_input"
    assert "ASSAY_BASE_URL" in doc["error"]
    assert "error:" in err


def test_json_failed_run_reports_expected_and_observed(tmp_path, capsys, monkeypatch):
    plan_path = _write_plan(
        tmp_path, _scenario("s1"),
        _scenario("s2", check={"type": "text_visible", "text": "Profile saved"}),
    )
    _use_fake_browser(monkeypatch, {"s2": FakeScenarioAdapter(text="We can't find that page")})
    out_dir = tmp_path / "out"

    code, doc, err = _run_json(capsys, ["--plan", str(plan_path), "--output", str(out_dir)])

    assert code == 1
    assert doc["status"] == "fail"
    assert doc["exit_meaning"].startswith("at least one confirmed")
    assert doc["counts"] == {"PASS": 1, "FAIL": 1, "total": 2}
    [failure] = doc["failures"]
    assert failure["scenario_id"] == "s2"
    [assertion] = failure["assertions"]
    assert assertion["expected"] == "s2 lands on the account page"
    assert assertion["check"] == {"type": "text_visible", "text": "Profile saved"}
    assert assertion["observed"]
    assert "--only s2" in doc["rerun"]["failed"]
    # Human logs went to stderr, not stdout.
    assert "result: FAIL" in err
    # Same document on disk; markdown rendered.
    assert json.loads((out_dir / "summary.json").read_text()) == doc
    md = (out_dir / "summary.md").read_text()
    assert "FAIL" in md and "Advisory" in md
    for key in ("plan", "results", "report", "junit", "summary_json", "summary_md"):
        assert Path(doc["artifacts"][key]).is_file(), key


def test_default_output_dir_and_text_mode(tmp_path, capsys, monkeypatch):
    plan_path = _write_plan(tmp_path, _scenario("s1"))
    _use_fake_browser(monkeypatch, {})
    from harness.cli import main

    code = main(["check", "--plan", str(plan_path)])
    out, _ = capsys.readouterr()
    assert code == 0
    assert "result: PASS" in out
    assert (tmp_path / "results" / "check" / "summary.json").is_file()


# ── --plan (frozen reruns) ────────────────────────────────────────────────────

def test_plan_is_reused_byte_for_byte(tmp_path, capsys, monkeypatch):
    plan_path = _write_plan(tmp_path, _scenario("s1"))
    before = plan_path.read_bytes()
    _use_fake_browser(monkeypatch, {})
    out_dir = tmp_path / "out"

    code, doc, _ = _run_json(capsys, ["--plan", str(plan_path), "--output", str(out_dir)])

    assert code == 0
    assert plan_path.read_bytes() == before
    assert (out_dir / "plan.json").read_bytes() == before
    assert doc["plan"]["source"] == "loaded"
    assert doc["plan"]["sha256"] == file_sha256(plan_path)
    results = json.loads((out_dir / "results.json").read_text())
    assert results["plan"]["sha256"] == doc["plan"]["sha256"]


def test_plan_rerun_in_place(tmp_path, capsys, monkeypatch):
    """Rerunning the plan that lives in the output directory itself works."""
    plan_path = _write_plan(tmp_path, _scenario("s1"))
    _use_fake_browser(monkeypatch, {})
    code, doc, _ = _run_json(capsys, ["--plan", str(plan_path), "--output", str(plan_path.parent)])
    assert code == 0
    assert doc["plan"]["sha256"] == file_sha256(plan_path)


def test_invalid_plan_file_is_rejected(tmp_path, capsys):
    bad = tmp_path / "plan.json"
    bad.write_text(json.dumps({"version": "1", "created_at": "x", "depth": "low", "scenarios": []}))
    code, doc, _ = _run_json(capsys, ["--plan", str(bad)])
    assert code == 2
    assert doc["status"] == "invalid_input"
    assert "not a valid plan" in doc["error"]


def test_plan_only_with_plan_is_rejected(tmp_path, capsys):
    plan_path = _write_plan(tmp_path, _scenario("s1"))
    code, doc, _ = _run_json(capsys, ["--plan", str(plan_path), "--plan-only"])
    assert code == 2 and "--plan-only" in doc["error"]


def test_notes_and_plan_are_mutually_exclusive():
    from harness.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["check", "--notes", "a.md", "--plan", "p.json"])


# ── --only ────────────────────────────────────────────────────────────────────

def test_only_runs_selection_plus_prerequisites(tmp_path, capsys, monkeypatch):
    plan_path = _write_plan(tmp_path, _scenario("login"), _scenario("edit", ["login"]), _scenario("other"))
    factory = _use_fake_browser(monkeypatch, {})
    calls: list[str] = []
    original = factory.create

    async def spy(scenario):
        calls.append(scenario.id)
        return await original(scenario)

    factory.create = spy
    out_dir = tmp_path / "out"

    code, doc, _ = _run_json(capsys, ["--plan", str(plan_path), "--only", "edit", "--output", str(out_dir)])

    assert code == 0
    assert calls == ["login", "edit"]
    assert doc["scope"] == "partial"
    assert doc["selected"] == ["login", "edit"]
    assert doc["counts"] == {"PASS": 2, "total": 2}
    results = json.loads((out_dir / "results.json").read_text())
    other = next(s for s in results["scenarios"] if s["scenario_id"] == "other")
    assert other["verdict"] == "SKIPPED" and other["reason"] == NOT_SELECTED_REASON
    assert results["selection"]["not_selected"] == ["other"]
    assert "Partial run" in (out_dir / "summary.md").read_text()
    # The saved plan was not modified by the selection.
    assert all(not s["skip"] for s in json.loads(plan_path.read_text())["scenarios"])


def test_only_unknown_id(tmp_path, capsys):
    plan_path = _write_plan(tmp_path, _scenario("s1"))
    code, doc, _ = _run_json(capsys, ["--plan", str(plan_path), "--only", "nope"])
    assert code == 2 and "unknown scenario id" in doc["error"]


def test_only_requires_plan(tmp_path, capsys):
    notes = tmp_path / "n.md"
    notes.write_text("x")
    code, doc, _ = _run_json(capsys, ["--notes", str(notes), "--only", "s1"])
    assert code == 2 and "--only requires --plan" in doc["error"]


# ── Legacy environment ────────────────────────────────────────────────────────

def test_legacy_bta_variables_warn_and_are_ignored(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ASSAY_BASE_URL")
    monkeypatch.setenv("BTA_BASE_URL", "http://localhost:3000")
    code, doc, err = _run_json(capsys, ["--notes", "missing.md"])
    assert code == 2
    assert "BTA_BASE_URL is ignored; the variable is now named ASSAY_BASE_URL" in err
    assert "ASSAY_BASE_URL" in doc["error"]


def test_json_stdout_survives_noisy_adapters(tmp_path, capsys, monkeypatch):
    """Anything printed during the run (agent transcript, warnings) goes to stderr."""

    class Noisy(FakeScenarioAdapter):
        async def run_goal(self, scenario):
            print("transcript: clicking Save")
            await super().run_goal(scenario)

    plan_path = _write_plan(tmp_path, _scenario("s1"))
    _use_fake_browser(monkeypatch, {"s1": Noisy(url="http://localhost:4173/v/clean/account")})
    code, doc, err = _run_json(capsys, ["--plan", str(plan_path), "--output", str(tmp_path / "o")])
    assert code == 0 and doc["status"] == "pass"
    assert "transcript: clicking Save" in err


# ── --plan-only: readable plan an agent can present before running ───────────

def _patch_planner(monkeypatch, plan):
    monkeypatch.setattr("core.planner.AnthropicPlannerAdapter", lambda **k: object())
    monkeypatch.setattr("core.planner.run_planner", lambda ctx, depth, adapter: plan)


def test_plan_only_json_lists_scenarios(tmp_path, capsys, monkeypatch):
    notes = tmp_path / "README.md"
    notes.write_text("# Driftline\n- Users can sign in.\n")
    plan = make_plan("high", [_scenario("login"), _scenario("edit", ["login"])],
                     coverage_suggestions=["Check the cookie banner"])
    _patch_planner(monkeypatch, plan)
    out_dir = tmp_path / "out"

    code, doc, err = _run_json(capsys, ["--notes", str(notes), "--plan-only", "--output", str(out_dir)])

    assert code == 0 and doc["status"] == "planned"
    assert [s["id"] for s in doc["scenarios"]] == ["login", "edit"]
    assert doc["scenarios"][1]["prerequisites"] == ["login"]
    assert doc["coverage_suggestions"] == ["Check the cookie banner"]
    assert doc["plan"]["source"] == "generated"
    assert doc["rerun"]["all"].startswith("assay check --plan ")
    md = (out_dir / "summary.md").read_text()
    assert "| `edit` |" in md and "Check the cookie banner" in md


def test_plan_only_text_prints_table(tmp_path, capsys, monkeypatch):
    notes = tmp_path / "README.md"
    notes.write_text("# x\n")
    _patch_planner(monkeypatch, make_plan("low", [_scenario("login"), _scenario("edit", ["login"])]))
    from harness.cli import main

    code = main(["check", "--notes", str(notes), "--plan-only", "--output", str(tmp_path / "o")])
    out, _ = capsys.readouterr()
    assert code == 0
    assert "Scenario login" in out and "Depends on" in out
    assert "--only <ID>" in out


def test_executed_summary_lists_verdicts(tmp_path, capsys, monkeypatch):
    plan_path = _write_plan(tmp_path, _scenario("s1"))
    _use_fake_browser(monkeypatch, {})
    _, doc, _ = _run_json(capsys, ["--plan", str(plan_path), "--output", str(tmp_path / "o")])
    assert doc["scenarios"] == [{"id": "s1", "title": "Scenario s1", "verdict": "PASS", "video": None}]
