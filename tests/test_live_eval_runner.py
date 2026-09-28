"""Exercise paired evaluation wiring without browsers or paid model calls."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.config import Config
from core.executor import RunResult
from core.plan import Assertion, AssertionKind, Scenario, SourceRef, make_plan
from core.run import ScenarioResult
from core.schema import Verdict
from fixture.eval.bugs import BUGS
from fixture.eval.live import run_live_eval
from fixture.eval import runner


async def test_live_runner_requires_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("ASSAY_EVAL_LIVE", raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        await runner.evaluate_fixture(Config(), tmp_path, 1, "test")


async def test_runner_reuses_plan_per_pair_and_resets_app(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSAY_EVAL_LIVE", "1")
    variants = []
    contexts = []
    plans_seen = []
    class Server:
        server_port = 12345
        def serve_forever(self): pass
        def set_app(self, app): variants.append(app)
        def shutdown(self): pass
        def server_close(self): pass
    monkeypatch.setattr(runner, "make_server", lambda *a: Server())
    monkeypatch.setattr(runner, "create_app", lambda bugs=None: tuple(bugs or []))
    monkeypatch.setattr(runner, "AnthropicPlannerAdapter", lambda **k: None)
    monkeypatch.setattr(runner, "BrowserScenarioAdapterFactory", lambda *a: None)
    def plan(ctx, depth, adapter):
        contexts.append(ctx.notes_text)
        a = Assertion("a", "expected", AssertionKind.REQUIRED,
                      SourceRef("notes", "requirements.md", ctx.notes_text))
        return make_plan("low", [Scenario("s", "t", "g", [], [a], False, "r")])
    monkeypatch.setattr(runner, "run_planner", plan)
    async def execute(plan, *a, **k):
        plans_seen.append(plan)
        verdict = Verdict.UNVERIFIED if "calculation rule is unspecified" in plan.scenarios[0].assertions[0].source.excerpt else (Verdict.FAIL if variants[-1] else Verdict.PASS)
        return RunResult("run", [ScenarioResult("s", "t", verdict, "r", "run")], verdict != Verdict.UNVERIFIED)
    monkeypatch.setattr(runner, "run_plan", execute)
    report = await runner.evaluate_fixture(Config(), tmp_path, 2, "test-model")
    assert len(variants) == 24 and len(contexts) == 12
    assert all(plans_seen[i] is plans_seen[i + 1] for i in range(0, 24, 2))
    assert plans_seen[0] is not plans_seen[12]
    assert all("BUG-" not in ctx and "AMBIGUOUS-" not in ctx for ctx in contexts)
    assert report.detected_count == 10 and report.ambiguous_unverified_count == 2
    assert (tmp_path / "eval_report.json").exists()


async def test_inconclusive_clean_run_is_not_credited_as_detection(monkeypatch):
    monkeypatch.setenv("ASSAY_EVAL_LIVE", "1")
    async def evaluate(spec, broken):
        return (Verdict.FAIL if broken else Verdict.UNVERIFIED), 0, 0.0
    report = await run_live_eval(BUGS[:1], evaluate, model="test", model_version="test")
    assert report.detected_count == 0
