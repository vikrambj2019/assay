"""Tests for FAIL adjudication (core/adjudicate.py, harness/adjudicators.py).

No LLM, no browser: stub adjudicators stand in for the model call, and a
fake adapter drives the executor integration tests.
"""

from __future__ import annotations

import pytest

from core.adjudicate import (
    AdjudicationInput,
    AdjudicationResult,
    adjudicate_fail,
    build_adjudication_prompt,
)
from core.plan import Assertion, AssertionKind, Plan, Scenario, SourceRef
from core.policy import MutationPolicy
from core.executor import run_plan
from core.schema import Verdict
from harness.adjudicators import make_adjudicator, parse_adjudication_response


def _inp() -> AdjudicationInput:
    return AdjudicationInput(
        goal="Log in and see the dashboard",
        reported_reason="login button not visible",
        action_trail=["open_url /login", "click [3]"],
        final_page_text="Welcome back",
        screenshot_paths=["step-01.png"],
    )


# ── prompt building ───────────────────────────────────────────────────────────

def test_prompt_contains_goal_reason_trail_and_page():
    prompt = build_adjudication_prompt(_inp())
    assert "Log in and see the dashboard" in prompt
    assert "login button not visible" in prompt
    assert "1. open_url /login" in prompt
    assert "2. click [3]" in prompt
    assert "Welcome back" in prompt


def test_prompt_handles_empty_trail():
    prompt = build_adjudication_prompt(AdjudicationInput(goal="g", reported_reason="r"))
    assert "(no actions recorded)" in prompt


# ── adjudicate_fail orchestrator ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_adjudicator_returns_none():
    assert await adjudicate_fail(_inp(), None) is None


@pytest.mark.asyncio
async def test_stub_adjudicator_result_passes_through():
    async def stub(inp: AdjudicationInput) -> AdjudicationResult:
        assert inp.goal == "Log in and see the dashboard"
        return AdjudicationResult(confirmed=False, reason="agent was lost", reviewer="stub")

    result = await adjudicate_fail(_inp(), stub)
    assert result is not None
    assert result.confirmed is False
    assert result.reviewer == "stub"


@pytest.mark.asyncio
async def test_broken_adjudicator_yields_none_not_pass():
    async def broken(inp: AdjudicationInput) -> AdjudicationResult:
        raise RuntimeError("model exploded")

    # A broken reviewer must never manufacture a verdict — None keeps the
    # original FAIL standing.
    assert await adjudicate_fail(_inp(), broken) is None


# ── response parsing ──────────────────────────────────────────────────────────

def test_parse_confirmed():
    confirmed, reason = parse_adjudication_response(
        "VERDICT: CONFIRMED\nREASON: the error banner is clearly visible"
    )
    assert confirmed is True
    assert "error banner" in reason


def test_parse_not_confirmed():
    confirmed, reason = parse_adjudication_response(
        "VERDICT: NOT CONFIRMED\nREASON: the agent never left the login page"
    )
    assert confirmed is False
    assert "never left" in reason


def test_parse_garbage_is_not_confirmed():
    confirmed, _ = parse_adjudication_response("looks fine to me, probably")
    assert confirmed is False


def test_parse_case_insensitive_verdict():
    confirmed, _ = parse_adjudication_response("verdict: confirmed\nreason: yes")
    assert confirmed is True


# ── make_adjudicator ──────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, adjudicate_fails: bool, model: str = "claude-sonnet-5"):
        self.adjudicate_fails = adjudicate_fails
        self.model = model


def test_make_adjudicator_none_when_disabled():
    assert make_adjudicator(_Cfg(adjudicate_fails=False)) is None


# ── executor integration ──────────────────────────────────────────────────────

def _src() -> SourceRef:
    return SourceRef(kind="notes", path="changes.md", excerpt="x")


def _scenario() -> Scenario:
    return Scenario(
        id="s-001",
        title="Scenario s-001",
        goal="See the dashboard",
        prerequisites=[],
        assertions=[
            Assertion(
                id="a-1",
                description="dashboard text visible",
                kind=AssertionKind.REQUIRED,
                source=_src(),
                check={"type": "text_visible", "text": "Dashboard"},
            )
        ],
        requires_mutations=False,
        reason="test",
        source=_src(),
    )


def _plan() -> Plan:
    from datetime import datetime, timezone
    return Plan(
        version="1",
        created_at=datetime.now(timezone.utc).isoformat(),
        depth="low",
        scenarios=[_scenario()],
    )


class _FailingAdapter:
    """Deterministic FAIL (page lacks the expected text), no agent involved."""

    def __init__(self, reviewer_result: "AdjudicationResult | None | Exception" = None):
        self._reviewer_result = reviewer_result
        self.adjudicate_calls = 0

    async def run_goal(self, scenario) -> None:
        pass

    async def current_url(self) -> str:
        return "http://localhost:3000/"

    async def page_text(self) -> str:
        return "nothing relevant here"

    async def field_value(self, selector: str) -> "str | None":
        return None

    async def is_element_visible(self, selector: str) -> "bool | None":
        return False

    async def reload(self) -> None:
        pass

    async def assess_assertion(self, assertion):
        raise AssertionError("should not be called for deterministic checks")

    async def adjudicate_fail(self, verdict, reason, outcomes):
        self.adjudicate_calls += 1
        r = self._reviewer_result
        if isinstance(r, Exception):
            raise r
        if r is None:
            return verdict, " [adjudication unavailable; original FAIL stands]"
        if r.confirmed:
            return Verdict.FAIL, f" [adjudicated by {r.reviewer}: FAIL confirmed]"
        return (
            Verdict.UNVERIFIED,
            f" [adjudicator {r.reviewer} did not confirm the FAIL: {r.reason}]",
        )


class _LegacyAdapter:
    """No adjudicate_fail method — must behave exactly as before."""

    async def run_goal(self, scenario) -> None:
        pass

    async def current_url(self) -> str:
        return "http://localhost:3000/"

    async def page_text(self) -> str:
        return "nothing relevant here"

    async def field_value(self, selector: str) -> "str | None":
        return None

    async def is_element_visible(self, selector: str) -> "bool | None":
        return False

    async def reload(self) -> None:
        pass

    async def assess_assertion(self, assertion):
        raise AssertionError("should not be called for deterministic checks")


class _Factory:
    def __init__(self, adapter):
        self._adapter = adapter

    async def create(self, scenario):
        return self._adapter


@pytest.mark.asyncio
async def test_confirmed_fail_keeps_fail_with_note():
    adapter = _FailingAdapter(
        AdjudicationResult(confirmed=True, reason="banner visible", reviewer="stub")
    )
    result = await run_plan(_plan(), MutationPolicy(False), _Factory(adapter))
    r = result.scenario_results[0]
    assert r.verdict is Verdict.FAIL
    assert "adjudicated by stub: FAIL confirmed" in r.reason
    assert adapter.adjudicate_calls == 1


@pytest.mark.asyncio
async def test_rejected_fail_downgrades_to_unverified():
    adapter = _FailingAdapter(
        AdjudicationResult(confirmed=False, reason="agent was lost", reviewer="stub")
    )
    result = await run_plan(_plan(), MutationPolicy(False), _Factory(adapter))
    r = result.scenario_results[0]
    assert r.verdict is Verdict.UNVERIFIED
    assert "did not confirm the FAIL" in r.reason
    assert "agent was lost" in r.reason  # reviewer rationale preserved


@pytest.mark.asyncio
async def test_legacy_adapter_without_adjudication_untouched():
    adapter = _LegacyAdapter()
    result = await run_plan(_plan(), MutationPolicy(False), _Factory(adapter))
    r = result.scenario_results[0]
    assert r.verdict is Verdict.FAIL
    assert "adjudicat" not in r.reason


@pytest.mark.asyncio
async def test_broken_reviewer_keeps_original_fail():
    adapter = _FailingAdapter(RuntimeError("model exploded"))
    result = await run_plan(_plan(), MutationPolicy(False), _Factory(adapter))
    r = result.scenario_results[0]
    assert r.verdict is Verdict.FAIL
    assert "adjudicator errored" in r.reason
