"""--record-video: real Chromium recording plus report wiring.

The recording test launches headless Chromium against an in-memory page (no
network, no model). The report tests use a placeholder file so they run fast.
"""

from __future__ import annotations

import json

from core.browser import BrowserSession
from core.check_report import write_check_html, write_results_json
from core.check_summary import build_summary
from core.config import Config
from core.executor import RunResult
from core.plan import Assertion, AssertionKind, Scenario, SourceRef, make_plan, save_plan
from core.run import ScenarioResult
from core.schema import Verdict


async def test_session_records_named_video(tmp_path):
    video_dir = tmp_path / "s-001"
    async with BrowserSession(Config(headless=True, record_video_dir=video_dir)) as s:
        await s.page.set_content("<h1>Profile saved</h1><button>Save</button>")
        await s.page.wait_for_timeout(300)
    video = video_dir / "video.webm"
    assert video.is_file() and video.stat().st_size > 0


async def test_no_video_without_flag(tmp_path):
    async with BrowserSession(Config(headless=True)) as s:
        await s.page.set_content("<p>hi</p>")
    assert not list(tmp_path.rglob("*.webm"))


def test_config_reads_record_video_env(monkeypatch):
    monkeypatch.setenv("ASSAY_RECORD_VIDEO", "true")
    assert Config.from_env().record_video is True


def _plan_and_result(tmp_path):
    scenario = Scenario(
        id="s-001", title="Profile change survives reload", goal="g", prerequisites=[],
        requires_mutations=False, reason="r",
        assertions=[Assertion(id="a1", description="new phone after reload", kind=AssertionKind.REQUIRED,
                              source=SourceRef(kind="notes", path="n", excerpt="e"))],
    )
    plan = make_plan("low", [scenario])
    result = RunResult(run_id="run-1", complete=True, scenario_results=[ScenarioResult(
        scenario_id="s-001", scenario_title=scenario.title, verdict=Verdict.FAIL,
        reason="old phone shown after reload", run_id="run-1",
        assertions_checked=["a1"], assertion_evidence={"a1": "(555) 010-2233"},
    )])
    return plan, result


def test_reports_link_recorded_video(tmp_path):
    plan, result = _plan_and_result(tmp_path)
    (tmp_path / "s-001").mkdir()
    (tmp_path / "s-001" / "video.webm").write_bytes(b"webm")

    results = json.loads(write_results_json(result, plan, tmp_path).read_text())
    assert results["scenarios"][0]["video"] == "s-001/video.webm"

    html = write_check_html(result, plan, tmp_path).read_text()
    assert "<video" in html and "s-001/video.webm" in html

    plan_path = save_plan(plan, tmp_path)
    doc = build_summary(code=1, run_result=result, plan=plan, plan_path=plan_path,
                        plan_source="generated", out_dir=tmp_path, base_url="http://x")
    assert doc["failures"][0]["video"].endswith("s-001/video.webm")


def test_reports_without_video_have_no_player(tmp_path):
    plan, result = _plan_and_result(tmp_path)
    results = json.loads(write_results_json(result, plan, tmp_path).read_text())
    assert "video" not in results["scenarios"][0]
    assert "<video" not in write_check_html(result, plan, tmp_path).read_text()


async def test_factory_records_into_scenario_folder(tmp_path):
    from dataclasses import replace

    from harness.scenario import BrowserScenarioAdapterFactory

    cfg = replace(Config(headless=True), record_video=True)
    plan, _ = _plan_and_result(tmp_path)
    adapter = await BrowserScenarioAdapterFactory(cfg, tmp_path).create(plan.scenarios[0])
    await adapter.session.page.set_content("<p>checkout</p>")
    await adapter.close()
    assert (tmp_path / "s-001" / "video.webm").is_file()
