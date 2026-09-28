"""Tests for Task 14: run budget enforcement and partial result retention.

Covers:
- BudgetConfig construction
- FakeClock mechanics
- RunBudget: time/action/cost limits, check(), summary()
- budget_from_depth: depth presets and overrides
- Executor integration: budget before scenario, budget mid-execution,
  action exhaustion, cost threshold, preserved prior results, complete=False
"""

from __future__ import annotations

import asyncio

import pytest

from core.budget import (
    BudgetConfig,
    BudgetExhausted,
    FakeClock,
    RealClock,
    RunBudget,
    budget_from_depth,
)
from core.executor import RunResult, run_plan
from core.plan import Assertion, AssertionKind, Plan, Scenario, SourceRef
from core.policy import MutationPolicy
from core.schema import Verdict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _src() -> SourceRef:
    return SourceRef(kind="notes", path="notes.md", excerpt="req")


def _assertion(aid: str = "a1") -> Assertion:
    return Assertion(
        id=aid,
        description="passes",
        kind=AssertionKind.REQUIRED,
        source=_src(),
        check={"type": "text_visible", "text": "ok"},
    )


def _scenario(
    sid: str = "s1",
    *,
    prereqs: list[str] | None = None,
    assertions: list[Assertion] | None = None,
) -> Scenario:
    return Scenario(
        id=sid,
        title=f"Scenario {sid}",
        goal=f"goal {sid}",
        prerequisites=prereqs or [],
        assertions=assertions or [_assertion()],
        requires_mutations=False,
        reason="test",
    )


def _plan(*scenarios: Scenario) -> Plan:
    return Plan(
        version="1",
        created_at="2024-01-01T00:00:00+00:00",
        depth="medium",
        scenarios=list(scenarios),
    )


class FakeScenarioAdapter:
    """Adapter that always succeeds and returns 'ok' in page text."""

    def __init__(self, raise_after: int = 0, raise_exc: Exception | None = None) -> None:
        self._raise_after = raise_after  # raise after N run_goal calls (0=never)
        self._raise_exc = raise_exc or RuntimeError("test error")
        self._calls = 0

    async def run_goal(self, scenario) -> None:
        self._calls += 1
        if self._raise_after and self._calls >= self._raise_after:
            raise self._raise_exc

    async def current_url(self) -> str:
        return "http://example.com/"

    async def page_text(self) -> str:
        return "ok"

    async def field_value(self, selector: str) -> str | None:
        return None

    async def is_element_visible(self, selector: str) -> bool:
        return False

    async def reload(self) -> None:
        pass


class FixedAdapterFactory:
    """Returns the same adapter for every scenario."""

    def __init__(self, adapter: FakeScenarioAdapter) -> None:
        self._adapter = adapter

    async def create(self, scenario) -> FakeScenarioAdapter:
        return self._adapter


class BudgetRaisingAdapter(FakeScenarioAdapter):
    """Raises BudgetExhausted during run_goal."""

    def __init__(self, reason: str = "time limit exceeded: 601.0s >= 600s") -> None:
        super().__init__()
        self._reason = reason

    async def run_goal(self, scenario) -> None:
        raise BudgetExhausted(self._reason)


class BudgetRaisingFactory:
    """Returns a BudgetRaisingAdapter."""

    def __init__(self, reason: str = "time limit exceeded") -> None:
        self._reason = reason

    async def create(self, scenario) -> BudgetRaisingAdapter:
        return BudgetRaisingAdapter(self._reason)


# ── FakeClock ─────────────────────────────────────────────────────────────────

def test_fake_clock_starts_at_zero():
    clock = FakeClock()
    assert clock.now() == 0.0


def test_fake_clock_custom_start():
    clock = FakeClock(start=100.0)
    assert clock.now() == 100.0


def test_fake_clock_advance():
    clock = FakeClock()
    clock.advance(30.0)
    assert clock.now() == 30.0


def test_fake_clock_advance_multiple():
    clock = FakeClock()
    clock.advance(10.0)
    clock.advance(5.5)
    assert clock.now() == pytest.approx(15.5)


def test_real_clock_returns_float():
    clock = RealClock()
    t = clock.now()
    assert isinstance(t, float)
    assert t > 0


# ── BudgetConfig ──────────────────────────────────────────────────────────────

def test_budget_config_defaults():
    cfg = BudgetConfig()
    assert cfg.max_seconds is None
    assert cfg.max_actions is None
    assert cfg.max_cost_usd is None


def test_budget_config_with_values():
    cfg = BudgetConfig(max_seconds=600, max_actions=100, max_cost_usd=1.50)
    assert cfg.max_seconds == 600
    assert cfg.max_actions == 100
    assert cfg.max_cost_usd == 1.50


# ── RunBudget — basic state ───────────────────────────────────────────────────

def test_budget_elapsed_before_start():
    budget = RunBudget(BudgetConfig(), FakeClock())
    assert budget.elapsed_seconds() == 0.0


def test_budget_elapsed_after_start():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(), clock)
    budget.start()
    clock.advance(42.0)
    assert budget.elapsed_seconds() == pytest.approx(42.0)


def test_budget_remaining_unlimited():
    budget = RunBudget(BudgetConfig(), FakeClock())
    budget.start()
    assert budget.remaining_seconds() is None


def test_budget_remaining_with_limit():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=60), clock)
    budget.start()
    clock.advance(20.0)
    assert budget.remaining_seconds() == pytest.approx(40.0)


def test_budget_remaining_clamps_at_zero():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=60), clock)
    budget.start()
    clock.advance(120.0)
    assert budget.remaining_seconds() == 0.0


def test_budget_actions_count_initial():
    budget = RunBudget(BudgetConfig(), FakeClock())
    assert budget.actions_count() == 0


def test_budget_cost_initial():
    budget = RunBudget(BudgetConfig(), FakeClock())
    assert budget.cost_usd() == 0.0


# ── RunBudget — no limits ─────────────────────────────────────────────────────

def test_check_no_limits_never_raises():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(), clock)
    budget.start()
    clock.advance(99999.0)
    budget.check()  # must not raise


def test_record_action_no_limit():
    budget = RunBudget(BudgetConfig(), FakeClock())
    budget.start()
    for _ in range(1000):
        budget.record_action()  # must not raise


def test_record_cost_no_limit():
    budget = RunBudget(BudgetConfig(), FakeClock())
    budget.start()
    budget.record_cost(999.0)  # must not raise


# ── RunBudget — time limit ────────────────────────────────────────────────────

def test_time_limit_not_exceeded():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=600), clock)
    budget.start()
    clock.advance(599.9)
    budget.check()  # should not raise


def test_time_limit_exactly_reached_raises():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=600), clock)
    budget.start()
    clock.advance(600.0)
    with pytest.raises(BudgetExhausted) as exc_info:
        budget.check()
    assert "600" in exc_info.value.reason
    assert "time limit" in exc_info.value.reason


def test_time_limit_exceeded_raises():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=180), clock)
    budget.start()
    clock.advance(500.0)
    with pytest.raises(BudgetExhausted) as exc_info:
        budget.check()
    assert "180" in exc_info.value.reason


def test_budget_exhausted_reason_is_exception_message():
    exc = BudgetExhausted("time limit exceeded: 600.0s >= 600s")
    assert str(exc) == exc.reason


# ── RunBudget — action limit ──────────────────────────────────────────────────

def test_action_limit_not_exceeded():
    budget = RunBudget(BudgetConfig(max_actions=100), FakeClock())
    budget.start()
    for _ in range(100):
        budget.record_action()  # 100th action is exactly at limit — OK


def test_action_limit_exceeded_raises():
    budget = RunBudget(BudgetConfig(max_actions=5), FakeClock())
    budget.start()
    for _ in range(5):
        budget.record_action()
    with pytest.raises(BudgetExhausted) as exc_info:
        budget.record_action()  # 6th action
    assert "action limit" in exc_info.value.reason
    assert "5" in exc_info.value.reason


def test_action_count_increments_on_each_call():
    budget = RunBudget(BudgetConfig(max_actions=1000), FakeClock())
    budget.start()
    budget.record_action()
    budget.record_action()
    budget.record_action()
    assert budget.actions_count() == 3


# ── RunBudget — cost limit ────────────────────────────────────────────────────

def test_cost_limit_not_exceeded():
    budget = RunBudget(BudgetConfig(max_cost_usd=1.0), FakeClock())
    budget.start()
    budget.record_cost(0.99)  # should not raise


def test_cost_limit_reached_raises():
    budget = RunBudget(BudgetConfig(max_cost_usd=1.0), FakeClock())
    budget.start()
    with pytest.raises(BudgetExhausted) as exc_info:
        budget.record_cost(1.0)
    assert "cost threshold" in exc_info.value.reason
    assert "possible overrun" in exc_info.value.reason or "could exceed" in exc_info.value.reason


def test_cost_accumulates_across_calls():
    budget = RunBudget(BudgetConfig(max_cost_usd=1.0), FakeClock())
    budget.start()
    budget.record_cost(0.4)
    budget.record_cost(0.4)
    assert budget.cost_usd() == pytest.approx(0.8)
    with pytest.raises(BudgetExhausted):
        budget.record_cost(0.3)  # pushes to 1.1


# ── RunBudget — summary ───────────────────────────────────────────────────────

def test_summary_structure():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=600, max_actions=100, max_cost_usd=2.0), clock)
    budget.start()
    clock.advance(30.0)
    budget.record_action()
    budget.record_cost(0.05)

    s = budget.summary()
    assert s["elapsed_seconds"] == pytest.approx(30.0, abs=0.01)
    assert s["actions"] == 1
    assert s["cost_usd"] == pytest.approx(0.05, abs=1e-6)
    assert s["limits"]["max_seconds"] == 600
    assert s["limits"]["max_actions"] == 100
    assert s["limits"]["max_cost_usd"] == pytest.approx(2.0)


def test_summary_unlimited_fields_are_none():
    budget = RunBudget(BudgetConfig(), FakeClock())
    budget.start()
    s = budget.summary()
    assert s["limits"]["max_seconds"] is None
    assert s["limits"]["max_actions"] is None
    assert s["limits"]["max_cost_usd"] is None


# ── budget_from_depth ─────────────────────────────────────────────────────────

def test_budget_from_depth_low_presets():
    budget = budget_from_depth("low")
    assert budget._config.max_seconds == 180
    assert budget._config.max_actions == 40


def test_budget_from_depth_medium_presets():
    budget = budget_from_depth("medium")
    assert budget._config.max_seconds == 600
    assert budget._config.max_actions == 200


def test_budget_from_depth_high_presets():
    budget = budget_from_depth("high")
    assert budget._config.max_seconds == 1200
    assert budget._config.max_actions == 300


def test_budget_from_depth_override_seconds():
    budget = budget_from_depth("medium", max_seconds=999)
    assert budget._config.max_seconds == 999
    assert budget._config.max_actions == 200  # preset unchanged


def test_budget_from_depth_override_actions():
    budget = budget_from_depth("medium", max_actions=50)
    assert budget._config.max_seconds == 600
    assert budget._config.max_actions == 50


def test_budget_from_depth_override_cost():
    budget = budget_from_depth("low", max_cost_usd=0.50)
    assert budget._config.max_cost_usd == pytest.approx(0.50)


def test_budget_from_depth_no_cost_by_default():
    budget = budget_from_depth("high")
    assert budget._config.max_cost_usd is None


def test_budget_from_depth_injects_clock():
    clock = FakeClock(start=10.0)
    budget = budget_from_depth("medium", clock=clock)
    budget.start()
    assert budget.elapsed_seconds() == pytest.approx(0.0)
    clock.advance(5.0)
    assert budget.elapsed_seconds() == pytest.approx(5.0)


# ── Executor integration ──────────────────────────────────────────────────────

_POLICY = MutationPolicy(allow_mutations=False)


async def _run(plan: Plan, budget: RunBudget | None, factory=None) -> RunResult:
    if factory is None:
        factory = FixedAdapterFactory(FakeScenarioAdapter())
    return await run_plan(plan, _POLICY, factory, budget=budget)


def test_no_budget_run_completes():
    plan = _plan(_scenario("s1"), _scenario("s2"))
    result = asyncio.run(_run(plan, None))
    assert result.complete is True
    assert all(r.verdict is Verdict.PASS for r in result.scenario_results)


def test_budget_not_exceeded_run_completes():
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=600), clock)
    budget.start()
    plan = _plan(_scenario("s1"), _scenario("s2"))
    result = asyncio.run(_run(plan, budget))
    assert result.complete is True


def test_planning_timeout_marks_all_unverified():
    """Budget already exhausted before any scenario starts."""
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=60), clock)
    budget.start()
    clock.advance(61.0)  # time limit exceeded

    plan = _plan(_scenario("s1"), _scenario("s2"), _scenario("s3"))
    result = asyncio.run(_run(plan, budget))

    assert result.complete is False
    for r in result.scenario_results:
        assert r.verdict is Verdict.UNVERIFIED
        assert "time limit" in r.reason.lower() or "60" in r.reason


def test_execution_timeout_mid_run():
    """Budget exhausted during run_goal of second scenario."""
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=600), clock)
    budget.start()

    # s1 passes, then clock advances past limit, s2 raises BudgetExhausted
    reason = "time limit exceeded: 601.0s >= 600s"
    factory = BudgetRaisingFactory(reason)
    plan = _plan(_scenario("s1"), _scenario("s2"), _scenario("s3"))

    # Use a mixed factory: s1 uses normal adapter, rest raise budget
    class MixedFactory:
        def __init__(self):
            self._count = 0

        async def create(self, scenario):
            self._count += 1
            if self._count == 1:
                return FakeScenarioAdapter()
            return BudgetRaisingAdapter(reason)

    result = asyncio.run(run_plan(plan, _POLICY, MixedFactory(), budget=budget))

    assert result.complete is False
    assert result.scenario_results[0].verdict is Verdict.PASS
    assert result.scenario_results[1].verdict is Verdict.UNVERIFIED
    assert result.scenario_results[2].verdict is Verdict.UNVERIFIED
    assert reason in result.scenario_results[1].reason


def test_preserved_pass_before_budget_exhaustion():
    """Already-completed PASS results are not overwritten."""
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=300), clock)
    budget.start()

    class SlowAfterOneFactory:
        """First scenario passes; second raises BudgetExhausted."""
        def __init__(self):
            self._count = 0

        async def create(self, scenario):
            self._count += 1
            if self._count == 1:
                return FakeScenarioAdapter()
            return BudgetRaisingAdapter("time limit exceeded: 301.0s >= 300s")

    plan = _plan(_scenario("s1"), _scenario("s2"))
    result = asyncio.run(run_plan(plan, _POLICY, SlowAfterOneFactory(), budget=budget))

    assert result.scenario_results[0].verdict is Verdict.PASS
    assert result.scenario_results[1].verdict is Verdict.UNVERIFIED
    assert result.complete is False


def test_preserved_fail_before_budget_exhaustion():
    """A confirmed FAIL before budget exhaustion is retained; run is incomplete."""
    class FailThenBudgetFactory:
        def __init__(self):
            self._count = 0

        async def create(self, scenario):
            self._count += 1
            if self._count == 1:
                # Returns adapter where page text is "wrong" — text_visible check fails
                class FailAdapter(FakeScenarioAdapter):
                    async def page_text(self):
                        return "wrong"
                return FailAdapter()
            return BudgetRaisingAdapter("time limit exceeded")

    plan = _plan(_scenario("s1"), _scenario("s2"))
    result = asyncio.run(run_plan(plan, _POLICY, FailThenBudgetFactory()))
    # s1 FAILs because page_text="wrong" doesn't contain "ok"
    assert result.scenario_results[0].verdict is Verdict.FAIL
    assert result.scenario_results[1].verdict is Verdict.UNVERIFIED
    assert result.complete is False


def test_action_limit_mid_run_marks_remaining_unverified():
    """Budget raised mid-execution due to action count propagates correctly."""
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_actions=0), clock)
    budget.start()

    plan = _plan(_scenario("s1"), _scenario("s2"))

    class ActionBudgetRaisingFactory:
        async def create(self, scenario):
            return BudgetRaisingAdapter("action limit exceeded: 1 actions > 0 allowed")

    result = asyncio.run(run_plan(plan, _POLICY, ActionBudgetRaisingFactory(), budget=budget))

    assert result.complete is False
    for r in result.scenario_results:
        assert r.verdict is Verdict.UNVERIFIED
        assert "action limit" in r.reason


def test_cost_threshold_marks_remaining_unverified():
    """Cost threshold reached mid-execution marks current + remaining UNVERIFIED."""
    budget = RunBudget(BudgetConfig(max_cost_usd=0.001), FakeClock())
    budget.start()
    reason = "cost threshold reached: $0.0010 >= $0.0010"

    plan = _plan(_scenario("s1"), _scenario("s2"))

    class CostBudgetRaisingFactory:
        async def create(self, scenario):
            return BudgetRaisingAdapter(reason)

    result = asyncio.run(run_plan(plan, _POLICY, CostBudgetRaisingFactory(), budget=budget))

    assert result.complete is False
    assert result.scenario_results[0].verdict is Verdict.UNVERIFIED


def test_budget_exhausted_run_never_complete():
    """complete=False whenever BudgetExhausted was raised, even if all results are PASS."""
    # Simulate: budget check before s2 raises even though s1 passed
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=1), clock)
    budget.start()
    clock.advance(2.0)  # already over limit

    plan = _plan(_scenario("s1"))
    result = asyncio.run(_run(plan, budget))

    assert result.complete is False
    assert result.scenario_results[0].verdict is Verdict.UNVERIFIED


def test_no_budget_exhaustion_complete_true_when_all_pass():
    budget = RunBudget(BudgetConfig(max_seconds=600), FakeClock())
    budget.start()
    plan = _plan(_scenario("s1"))
    result = asyncio.run(_run(plan, budget))
    assert result.complete is True
    assert result.scenario_results[0].verdict is Verdict.PASS


def test_run_id_preserved_in_budget_unverified_results():
    """run_id is set correctly on UNVERIFIED results caused by budget."""
    clock = FakeClock()
    budget = RunBudget(BudgetConfig(max_seconds=1), clock)
    budget.start()
    clock.advance(5.0)

    plan = _plan(_scenario("s1"), _scenario("s2"))
    result = asyncio.run(_run(plan, budget, run_id="test-run-42"))

    assert result.run_id == "test-run-42"
    for r in result.scenario_results:
        assert r.run_id == "test-run-42"


def test_run_id_generated_when_absent():
    plan = _plan(_scenario("s1"))
    result = asyncio.run(_run(plan, None))
    assert result.run_id  # non-empty


async def _run(plan: Plan, budget: RunBudget | None, factory=None, *, run_id: str | None = None) -> RunResult:  # type: ignore[misc]  # noqa: F811
    if factory is None:
        factory = FixedAdapterFactory(FakeScenarioAdapter())
    return await run_plan(plan, _POLICY, factory, run_id=run_id, budget=budget)
