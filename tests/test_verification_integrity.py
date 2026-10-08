"""Regressions for false passes, checkpoint timing, and evidence provenance."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.assertions import AssertionOutcome
from core.check_report import write_check_html, write_results_json
from core.config import Config
from core.executor import run_plan
from core.plan import Assertion, AssertionKind, Scenario, SourceRef, make_plan, validate_plan
from core.policy import MutationPolicy
from core.schema import ActionRecord, StepLog, Verdict
from harness.scenario import BrowserScenarioAdapter
from harness.verification import verification_tool


def assertion(aid="a1", text="Name is required", timing="checkpoint"):
    return Assertion(aid, f"Show {text}", AssertionKind.REQUIRED,
                     SourceRef("notes", "notes.md", f"Show {text}"),
                     {"type": "text_visible", "text": text}, timing)


class Page:
    url = "http://test/form"
    text = "Name is required"

    async def screenshot(self, path):
        from pathlib import Path
        Path(path).write_bytes(b"fake screenshot")


class Checker:
    def __init__(self, page):
        self.page = page

    async def page_text(self):
        return self.page.text


@pytest.mark.parametrize("overall", [Verdict.PASS, Verdict.FAIL])
async def test_unrelated_goal_verdict_cannot_resolve_assertion(tmp_path, overall):
    adapter = BrowserScenarioAdapter(None, Config(), tmp_path)
    adapter._goal_logs = [StepLog(1, "Goal", overall, "Opened profile")]
    a = replace(assertion(), check=None, timing="final")
    outcome = await adapter.assess_assertion(a)
    assert outcome.verdict is Verdict.UNVERIFIED


async def test_checkpoint_captures_intermediate_state_and_is_immutable(tmp_path):
    folder = tmp_path / "s1"
    folder.mkdir()
    page = Page()
    session = SimpleNamespace(page=page, action_lock=asyncio.Lock())
    a = assertion()
    outcomes = {}
    records = [ActionRecord(1, "Submit empty form")]
    verify = verification_tool(session, [a], Checker(page), outcomes, records, folder)
    await verify.handler({"assertion_id": "a1"})
    assert outcomes["a1"].verdict is Verdict.PASS
    assert outcomes["a1"].url == "http://test/form"
    assert outcomes["a1"].action_ids == [1]
    assert (tmp_path / outcomes["a1"].screenshot).is_file()
    page.text = "Checkout complete"
    await verify.handler({"assertion_id": "a1"})
    assert outcomes["a1"].evidence == "Name is required"


async def test_failed_checkpoint_cannot_be_healed_to_pass(tmp_path):
    page = Page()
    page.text = "Checkout complete"
    session = SimpleNamespace(page=page, action_lock=asyncio.Lock())
    outcomes = {}
    verify = verification_tool(session, [assertion()], Checker(page), outcomes, [], tmp_path)
    await verify.handler({"assertion_id": "a1"})
    page.text = "Name is required"
    await verify.handler({"assertion_id": "a1"})
    assert outcomes["a1"].verdict is Verdict.FAIL
    assert outcomes["a1"].evidence == "Checkout complete"


async def test_unknown_or_final_assertion_cannot_be_checkpointed(tmp_path):
    session = SimpleNamespace(page=Page(), action_lock=asyncio.Lock())
    outcomes = {}
    verify = verification_tool(session, [assertion(timing="final")], Checker(session.page), outcomes, [], tmp_path)
    for aid in ("invented", "a1"):
        assert (await verify.handler({"assertion_id": aid}))["isError"]
    assert outcomes == {}


async def test_missing_checkpoint_cannot_pass_from_final_page(tmp_path):
    adapter = BrowserScenarioAdapter(None, Config(), tmp_path)
    outcome = await adapter.assess_assertion(assertion())
    assert outcome.verdict is Verdict.UNVERIFIED


async def test_full_assertions_are_delivered_to_goal_agent(monkeypatch, tmp_path):
    calls = []
    async def goal(session, text, cfg, folder, **kwargs):
        calls.append(kwargs)
        return [StepLog(1, "Goal", Verdict.PASS, "done")]
    monkeypatch.setattr("harness.scenario.run_goal", goal)
    adapter = BrowserScenarioAdapter(None, Config(), tmp_path)
    a = assertion()
    scenario = Scenario("s1", "t", "g", [], [a], False, "r")
    await adapter.run_goal(scenario)
    assert calls[0]["assertions"] == [a]
    assert calls[0]["checkpoint_outcomes"] is adapter._checkpoint_outcomes


async def test_results_keep_mixed_outcomes_and_stage_evidence(tmp_path):
    from core.executor import RunResult
    from core.run import ScenarioResult
    folder = tmp_path / "s1"
    folder.mkdir()
    shot = folder / "step-01.png"
    shot.write_bytes(b"png")
    a = assertion()
    b = replace(assertion("a2", "Saved"), timing="final")
    plan = make_plan("low", [Scenario("s1", "t", "g", [], [a, b], False, "r")])
    outcomes = [AssertionOutcome("a1", Verdict.PASS, "error shown", "Name is required",
                                 screenshot="s1/step-01.png", action_ids=[1]),
                AssertionOutcome("a2", Verdict.FAIL, "Saved missing", "<script>bad</script>")]
    stages = [StepLog(1, "Submit", Verdict.PASS, "submitted", ["network POST /save → 500"],
                      [ActionRecord(1, "click", shot)])]
    result = RunResult("run", [ScenarioResult("s1", "t", Verdict.FAIL, "Saved missing", "run",
                       assertion_outcomes=outcomes, stages=stages)], True)
    doc = json.loads(write_results_json(result, plan, tmp_path).read_text())
    row = doc["scenarios"][0]
    assert [o["verdict"] for o in row["assertion_results"]] == ["PASS", "FAIL"]
    assert row["stages"][0]["action_records"][0]["screenshot"] == "s1/step-01.png"
    html = write_check_html(result, plan, tmp_path).read_text()
    assert "network POST /save" in html and "s1/step-01.png" in html
    assert "<script>bad</script>" not in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html


def test_old_plans_default_to_final_and_checkpoint_roundtrips():
    a = assertion()
    assert Assertion.from_dict(a.to_dict()).timing == "checkpoint"
    data = a.to_dict()
    del data["timing"]
    assert Assertion.from_dict(data).timing == "final"


def test_invalid_timing_is_rejected():
    a = replace(assertion(), timing="whenever")
    with pytest.raises(ValueError, match="timing"):
        validate_plan(make_plan("low", [Scenario("s1", "t", "g", [], [a], False, "r")]))
