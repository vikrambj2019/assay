"""Tests for the scenario executor and deterministic assertion checks (Task 13).

All pure async unit tests — no browser, no LLM, no network, no git.
FakeAssertionCheckerAdapter and FakeScenarioAdapter inject pre-configured
page state so every code path is exercised without Playwright.

Four acceptance scenarios (mirroring the spec):
  1. Success toast fails to persist  → persistence check FAIL
  2. Rejected invalid input          → text_visible check PASS
  3. Wrong redirect                  → url_contains check FAIL
  4. Ambiguous calculation           → no check spec → UNVERIFIED
"""

from __future__ import annotations

import pytest

from core.assertions import AssertionOutcome, evaluate_assertion
from core.executor import RunResult, ScenarioAdapterFactory, run_plan
from core.plan import (
    Assertion,
    AssertionKind,
    Plan,
    Scenario,
    SourceRef,
    make_plan,
)
from core.policy import MutationPolicy
from core.schema import Verdict


# ── Fake adapters ─────────────────────────────────────────────────────────────

class FakeAssertionCheckerAdapter:
    """Fake AssertionCheckerAdapter for unit tests.

    ``text_after_reload`` lets tests simulate a page whose content changes
    after reload (e.g. a transient success toast disappears).
    """

    def __init__(
        self,
        url: str = "http://localhost:3000/page",
        text: str = "",
        fields: dict[str, str] | None = None,
        visible_selectors: set[str] | None = None,
        text_after_reload: str | None = None,
    ) -> None:
        self._url = url
        self._text = text
        self._fields = fields or {}
        self._visible = visible_selectors or set()
        self._text_after_reload = text_after_reload
        self.reload_count = 0

    async def current_url(self) -> str:
        return self._url

    async def page_text(self) -> str:
        if self.reload_count > 0 and self._text_after_reload is not None:
            return self._text_after_reload
        return self._text

    async def field_value(self, selector: str) -> str | None:
        return self._fields.get(selector)

    async def is_element_visible(self, selector: str) -> bool:
        return selector in self._visible

    async def reload(self) -> None:
        self.reload_count += 1


class FakeScenarioAdapter(FakeAssertionCheckerAdapter):
    """Extends FakeAssertionCheckerAdapter with a no-op run_goal."""

    def __init__(self, *, goal_raises: Exception | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._goal_raises = goal_raises
        self.goal_calls: list[str] = []

    async def run_goal(self, scenario) -> None:  # type: ignore[override]
        self.goal_calls.append(scenario.id)
        if self._goal_raises is not None:
            raise self._goal_raises


class AssessedScenarioAdapter(FakeScenarioAdapter):
    def __init__(self, assessed: Verdict, reason: str = "page confirms outcome", **kwargs):
        super().__init__(**kwargs)
        self._assessed = assessed
        self._assessment_reason = reason

    async def assess_assertion(self, assertion):
        return AssertionOutcome(
            assertion.id, self._assessed,
            f"agent assessment: {self._assessment_reason}",
            self._assessment_reason,
        )


class FakeAdapterFactory:
    """Creates FakeScenarioAdapters from a {scenario_id: adapter} mapping."""

    def __init__(
        self,
        adapters: dict[str, FakeScenarioAdapter] | None = None,
        default: FakeScenarioAdapter | None = None,
    ) -> None:
        self._adapters = adapters or {}
        self._default = default or FakeScenarioAdapter()

    async def create(self, scenario) -> FakeScenarioAdapter:
        return self._adapters.get(scenario.id, self._default)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _src() -> SourceRef:
    return SourceRef(kind="notes", path="changes.md", excerpt="feature added")


def _req_assertion(aid: str, description: str = "Works", check: dict | None = None) -> Assertion:
    return Assertion(
        id=aid,
        description=description,
        kind=AssertionKind.REQUIRED,
        source=_src(),
        check=check,
    )


def _assume_assertion(aid: str, check: dict | None = None) -> Assertion:
    return Assertion(
        id=aid,
        description="Assumed behavior",
        kind=AssertionKind.ASSUMPTION,
        source=None,
        check=check,
    )


def _scenario(
    sid: str = "s-001",
    *,
    assertions: list[Assertion] | None = None,
    prerequisites: list[str] | None = None,
    skip: bool = False,
    mutations: bool = False,
) -> Scenario:
    return Scenario(
        id=sid,
        title=f"Scenario {sid}",
        goal="Verify the feature",
        prerequisites=prerequisites or [],
        assertions=assertions or [_req_assertion("a-001")],
        requires_mutations=mutations,
        reason="Changed behavior",
        source=_src(),
        skip=skip,
    )


def _mp(allow: bool = False) -> MutationPolicy:
    return MutationPolicy(allow_mutations=allow)


# ── evaluate_assertion: url_contains ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_url_contains_pass():
    adapter = FakeAssertionCheckerAdapter(url="http://localhost:3000/dashboard")
    a = _req_assertion("a-1", check={"type": "url_contains", "value": "/dashboard"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_url_contains_fail():
    adapter = FakeAssertionCheckerAdapter(url="http://localhost:3000/home")
    a = _req_assertion("a-1", check={"type": "url_contains", "value": "/dashboard"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL
    assert "/dashboard" in outcome.reason


@pytest.mark.asyncio
async def test_url_contains_evidence_is_actual_url():
    adapter = FakeAssertionCheckerAdapter(url="http://localhost:3000/home")
    a = _req_assertion("a-1", check={"type": "url_contains", "value": "/dashboard"})
    outcome = await evaluate_assertion(a, adapter)
    assert "home" in outcome.evidence


# ── evaluate_assertion: text_visible ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_visible_pass():
    adapter = FakeAssertionCheckerAdapter(text="Welcome, Alice!")
    a = _req_assertion("a-1", check={"type": "text_visible", "text": "Welcome"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_text_visible_fail():
    adapter = FakeAssertionCheckerAdapter(text="Error: something went wrong")
    a = _req_assertion("a-1", check={"type": "text_visible", "text": "Welcome"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL


# ── evaluate_assertion: text_absent ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_text_absent_pass():
    adapter = FakeAssertionCheckerAdapter(text="Welcome to the dashboard")
    a = _req_assertion("a-1", check={"type": "text_absent", "text": "Error"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_text_absent_fail():
    adapter = FakeAssertionCheckerAdapter(text="Error: invalid email")
    a = _req_assertion("a-1", check={"type": "text_absent", "text": "Error"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL
    assert "Error" in outcome.evidence


# ── evaluate_assertion: field_value ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_field_value_pass():
    adapter = FakeAssertionCheckerAdapter(fields={"input[name=email]": "alice@example.com"})
    a = _req_assertion("a-1", check={"type": "field_value",
                                      "selector": "input[name=email]",
                                      "value": "alice@example.com"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_field_value_fail():
    adapter = FakeAssertionCheckerAdapter(fields={"input[name=email]": "wrong@example.com"})
    a = _req_assertion("a-1", check={"type": "field_value",
                                      "selector": "input[name=email]",
                                      "value": "alice@example.com"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL
    assert "wrong@example.com" in outcome.evidence


@pytest.mark.asyncio
async def test_field_value_element_not_found_is_error():
    adapter = FakeAssertionCheckerAdapter(fields={})
    a = _req_assertion("a-1", check={"type": "field_value",
                                      "selector": "input[name=missing]",
                                      "value": "x"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.ERROR


# ── evaluate_assertion: element_visible ──────────────────────────────────────

@pytest.mark.asyncio
async def test_element_visible_pass():
    adapter = FakeAssertionCheckerAdapter(visible_selectors={".success-banner"})
    a = _req_assertion("a-1", check={"type": "element_visible", "selector": ".success-banner"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_element_visible_fail():
    adapter = FakeAssertionCheckerAdapter(visible_selectors=set())
    a = _req_assertion("a-1", check={"type": "element_visible", "selector": ".success-banner"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL


# ── evaluate_assertion: element_hidden ───────────────────────────────────────

@pytest.mark.asyncio
async def test_element_hidden_pass():
    adapter = FakeAssertionCheckerAdapter(visible_selectors=set())
    a = _req_assertion("a-1", check={"type": "element_hidden", "selector": ".error-banner"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_element_hidden_fail():
    adapter = FakeAssertionCheckerAdapter(visible_selectors={".error-banner"})
    a = _req_assertion("a-1", check={"type": "element_hidden", "selector": ".error-banner"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL


# ── evaluate_assertion: persistence ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_persistence_pass():
    """Text visible before and after reload → PASS."""
    adapter = FakeAssertionCheckerAdapter(
        text="Record saved",
        text_after_reload="Record saved",
    )
    a = _req_assertion("a-1", check={"type": "persistence", "text": "Record saved"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.PASS


@pytest.mark.asyncio
async def test_persistence_fail_text_disappears_after_reload():
    """Text present before reload but absent after → FAIL (persistence defect)."""
    adapter = FakeAssertionCheckerAdapter(
        text="Record saved",
        text_after_reload="",    # toast is gone after reload
    )
    a = _req_assertion("a-1", check={"type": "persistence", "text": "Record saved"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL
    assert "persist" in outcome.reason.lower() or "reload" in outcome.reason.lower()


@pytest.mark.asyncio
async def test_persistence_fail_text_not_visible_before_reload():
    """Text not visible initially → FAIL (can't verify persistence)."""
    adapter = FakeAssertionCheckerAdapter(text="", text_after_reload="")
    a = _req_assertion("a-1", check={"type": "persistence", "text": "Record saved"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.FAIL


@pytest.mark.asyncio
async def test_persistence_check_calls_reload():
    adapter = FakeAssertionCheckerAdapter(text="X", text_after_reload="X")
    a = _req_assertion("a-1", check={"type": "persistence", "text": "X"})
    await evaluate_assertion(a, adapter)
    assert adapter.reload_count == 1


# ── evaluate_assertion: semantic / no check spec ─────────────────────────────

@pytest.mark.asyncio
async def test_semantic_assertion_is_unverified():
    """Assertion without check spec → UNVERIFIED (model-evaluated)."""
    adapter = FakeAssertionCheckerAdapter()
    a = _req_assertion("a-1", check=None)
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.UNVERIFIED


@pytest.mark.asyncio
async def test_semantic_assertion_reason_mentions_model_evaluated():
    adapter = FakeAssertionCheckerAdapter()
    a = _req_assertion("a-1", check=None)
    outcome = await evaluate_assertion(a, adapter)
    assert "model-evaluated" in outcome.reason.lower() or "unverified" in outcome.reason.lower()


@pytest.mark.asyncio
async def test_executor_does_not_inherit_agent_pass_for_semantic_assertion():
    s = _scenario("s-001", assertions=[_req_assertion("a-1", check=None)])
    plan = make_plan("medium", [s])
    adapter = AssessedScenarioAdapter(Verdict.PASS)
    result = await run_plan(plan, _mp(), FakeAdapterFactory(default=adapter))
    assert result.scenario_results[0].verdict is Verdict.UNVERIFIED
    assert "no deterministic check" in result.scenario_results[0].assertion_evidence["a-1"]


@pytest.mark.asyncio
async def test_unscoped_agent_fail_is_not_a_confirmed_assertion_failure():
    s = _scenario("s-001", assertions=[_req_assertion("a-1", check=None)])
    plan = make_plan("medium", [s])
    adapter = AssessedScenarioAdapter(Verdict.FAIL, reason="required outcome absent")
    result = await run_plan(plan, _mp(), FakeAdapterFactory(default=adapter))
    assert result.scenario_results[0].verdict is Verdict.UNVERIFIED


@pytest.mark.asyncio
async def test_unknown_check_type_is_error():
    adapter = FakeAssertionCheckerAdapter()
    a = _req_assertion("a-1", check={"type": "totally_unknown_check"})
    outcome = await evaluate_assertion(a, adapter)
    assert outcome.verdict is Verdict.ERROR


# ── Acceptance scenario 1: success toast fails to persist ────────────────────

@pytest.mark.asyncio
async def test_acceptance_success_toast_fails_to_persist():
    """The app shows 'Record saved' after a create action, but the toast
    disappears on reload — a persistence defect.  The executor must FAIL."""
    assertion = _req_assertion(
        "a-persist",
        description="Record saved message persists after reload",
        check={"type": "persistence", "text": "Record saved"},
    )
    scenario = _scenario("s-create", assertions=[assertion], mutations=True)
    plan = make_plan("medium", [scenario])
    adapter = FakeScenarioAdapter(
        text="Record saved",   # toast visible right after action
        text_after_reload="",  # but gone after reload
    )
    factory = FakeAdapterFactory(adapters={"s-create": adapter})
    result = await run_plan(plan, _mp(allow=True), factory)
    sr = result.scenario_results[0]
    assert sr.verdict is Verdict.FAIL
    assert "persist" in sr.reason.lower() or "reload" in sr.reason.lower()


# ── Acceptance scenario 2: rejected invalid input ─────────────────────────────

@pytest.mark.asyncio
async def test_acceptance_rejected_invalid_input():
    """The app shows an error message when invalid input is submitted.
    The executor should PASS when that error message is visible."""
    assertion = _req_assertion(
        "a-err",
        description="Error message 'Invalid email' is shown",
        check={"type": "text_visible", "text": "Invalid email"},
    )
    scenario = _scenario("s-invalid", assertions=[assertion])
    plan = make_plan("medium", [scenario])
    adapter = FakeScenarioAdapter(text="Invalid email. Please try again.")
    factory = FakeAdapterFactory(adapters={"s-invalid": adapter})
    result = await run_plan(plan, _mp(), factory)
    assert result.scenario_results[0].verdict is Verdict.PASS


# ── Acceptance scenario 3: wrong redirect ────────────────────────────────────

@pytest.mark.asyncio
async def test_acceptance_wrong_redirect():
    """After form submission the app redirects to /home instead of /dashboard.
    The url_contains check catches the wrong redirect as FAIL."""
    assertion = _req_assertion(
        "a-url",
        description="Redirected to /dashboard after login",
        check={"type": "url_contains", "value": "/dashboard"},
    )
    scenario = _scenario("s-login", assertions=[assertion])
    plan = make_plan("medium", [scenario])
    adapter = FakeScenarioAdapter(url="http://localhost:3000/home")  # wrong!
    factory = FakeAdapterFactory(adapters={"s-login": adapter})
    result = await run_plan(plan, _mp(), factory)
    assert result.scenario_results[0].verdict is Verdict.FAIL
    assert "dashboard" in result.scenario_results[0].reason


# ── Acceptance scenario 4: ambiguous calculation ─────────────────────────────

@pytest.mark.asyncio
async def test_acceptance_ambiguous_calculation_is_unverified():
    """A calculation result is visible but the expected value is undefined.
    No deterministic check spec → UNVERIFIED.  Must not silently PASS."""
    assertion = _req_assertion(
        "a-calc",
        description="Tax calculation is correct (business rule undefined)",
        check=None,   # no deterministic check — undefined business rule
    )
    scenario = _scenario("s-calc", assertions=[assertion])
    plan = make_plan("medium", [scenario])
    adapter = FakeScenarioAdapter(text="Total: $42.00 (tax: $3.82)")
    factory = FakeAdapterFactory(adapters={"s-calc": adapter})
    result = await run_plan(plan, _mp(), factory)
    assert result.scenario_results[0].verdict is Verdict.UNVERIFIED


# ── Executor: prerequisite failure blocks dependent ───────────────────────────

@pytest.mark.asyncio
async def test_failed_prerequisite_blocks_dependent():
    s1 = _scenario("s-001", assertions=[_req_assertion("a-1", check={"type": "url_contains", "value": "/dashboard"})])
    s2 = _scenario("s-002", prerequisites=["s-001"])
    plan = make_plan("medium", [s1, s2])
    adapter1 = FakeScenarioAdapter(url="http://localhost:3000/home")  # s-001 fails
    adapter2 = FakeScenarioAdapter(url="http://localhost:3000/dashboard")
    factory = FakeAdapterFactory(adapters={"s-001": adapter1, "s-002": adapter2})
    result = await run_plan(plan, _mp(), factory)
    results = {r.scenario_id: r for r in result.scenario_results}
    assert results["s-001"].verdict is Verdict.FAIL
    assert results["s-002"].verdict is Verdict.BLOCKED
    assert "s-001" in results["s-002"].reason


# ── Executor: SKIPPED scenarios ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skipped_scenario_produces_skipped_result():
    s = _scenario("s-001", skip=True)
    plan = make_plan("medium", [s])
    factory = FakeAdapterFactory()
    result = await run_plan(plan, _mp(), factory)
    assert result.scenario_results[0].verdict is Verdict.SKIPPED


@pytest.mark.asyncio
async def test_skipped_scenario_goal_not_run():
    s = _scenario("s-001", skip=True)
    plan = make_plan("medium", [s])
    adapter = FakeScenarioAdapter()
    factory = FakeAdapterFactory(default=adapter)
    await run_plan(plan, _mp(), factory)
    assert adapter.goal_calls == []  # run_goal was never called


# ── Executor: mutation-blocked scenarios ─────────────────────────────────────

@pytest.mark.asyncio
async def test_mutation_scenario_blocked_when_disabled():
    s = _scenario("s-001", mutations=True)
    plan = make_plan("medium", [s])
    factory = FakeAdapterFactory()
    result = await run_plan(plan, _mp(allow=False), factory)
    assert result.scenario_results[0].verdict is Verdict.BLOCKED


@pytest.mark.asyncio
async def test_mutation_scenario_runs_when_enabled():
    s = _scenario("s-001", mutations=True,
                  assertions=[_req_assertion("a-1", check={"type": "text_visible", "text": "OK"})])
    plan = make_plan("medium", [s])
    adapter = FakeScenarioAdapter(text="OK")
    factory = FakeAdapterFactory(default=adapter)
    result = await run_plan(plan, _mp(allow=True), factory)
    assert result.scenario_results[0].verdict is Verdict.PASS


# ── Executor: goal failure is ERROR ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_goal_execution_error_produces_error_result():
    s = _scenario("s-001")
    plan = make_plan("medium", [s])
    adapter = FakeScenarioAdapter(goal_raises=RuntimeError("browser crashed"))
    factory = FakeAdapterFactory(default=adapter)
    result = await run_plan(plan, _mp(), factory)
    assert result.scenario_results[0].verdict is Verdict.ERROR
    assert "browser crashed" in result.scenario_results[0].reason


# ── Executor: all required assertions pass → PASS ────────────────────────────

@pytest.mark.asyncio
async def test_all_required_assertions_pass():
    s = _scenario("s-001", assertions=[
        _req_assertion("a-1", check={"type": "text_visible", "text": "Hello"}),
        _req_assertion("a-2", check={"type": "url_contains", "value": "/home"}),
    ])
    plan = make_plan("medium", [s])
    adapter = FakeScenarioAdapter(url="http://localhost:3000/home", text="Hello world")
    factory = FakeAdapterFactory(default=adapter)
    result = await run_plan(plan, _mp(), factory)
    assert result.scenario_results[0].verdict is Verdict.PASS


# ── Executor: run_id in all results ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_id_present_in_all_results():
    plan = make_plan("medium", [_scenario("s-001"), _scenario("s-002")])
    factory = FakeAdapterFactory(
        default=FakeScenarioAdapter(
            text="OK",
            assertions_setup=[],
        ) if False else FakeScenarioAdapter()
    )
    result = await run_plan(plan, _mp(), factory, run_id="test-run-123")
    for sr in result.scenario_results:
        assert sr.run_id == "test-run-123"


@pytest.mark.asyncio
async def test_run_id_generated_when_not_supplied():
    plan = make_plan("medium", [_scenario("s-001")])
    factory = FakeAdapterFactory()
    result = await run_plan(plan, _mp(), factory)
    assert result.run_id
    for sr in result.scenario_results:
        assert sr.run_id == result.run_id


# ── Executor: completeness flag ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_is_complete_when_all_assertions_verified():
    s = _scenario("s-001",
                  assertions=[_req_assertion("a-1", check={"type": "text_visible", "text": "OK"})])
    plan = make_plan("medium", [s])
    adapter = FakeScenarioAdapter(text="OK")
    factory = FakeAdapterFactory(default=adapter)
    result = await run_plan(plan, _mp(), factory)
    assert result.complete is True


@pytest.mark.asyncio
async def test_run_is_not_complete_when_required_unverified():
    s = _scenario("s-001",
                  assertions=[_req_assertion("a-1", check=None)])  # no check → UNVERIFIED
    plan = make_plan("medium", [s])
    factory = FakeAdapterFactory()
    result = await run_plan(plan, _mp(), factory)
    assert result.complete is False


# ── Executor: assumption unverified does not block PASS ──────────────────────

@pytest.mark.asyncio
async def test_assumption_unverified_does_not_block_pass():
    """A REQUIRED assertion that passes + an ASSUMPTION without check.
    The scenario should still PASS (not UNVERIFIED) since the required
    assertion was verified."""
    s = _scenario("s-001", assertions=[
        _req_assertion("a-req", check={"type": "text_visible", "text": "OK"}),
        _assume_assertion("a-assume", check=None),  # unverified but not required
    ])
    plan = make_plan("medium", [s])
    adapter = FakeScenarioAdapter(text="OK")
    factory = FakeAdapterFactory(default=adapter)
    result = await run_plan(plan, _mp(), factory)
    assert result.scenario_results[0].verdict is Verdict.PASS


# ── plan.py: check field round-trip ──────────────────────────────────────────

def test_assertion_check_field_round_trips():
    a = Assertion(
        id="a-1",
        description="URL check",
        kind=AssertionKind.REQUIRED,
        source=SourceRef(kind="notes", path="x.md", excerpt="y"),
        check={"type": "url_contains", "value": "/dashboard"},
    )
    rt = Assertion.from_dict(a.to_dict())
    assert rt.check == {"type": "url_contains", "value": "/dashboard"}


def test_assertion_check_none_round_trips():
    a = Assertion(
        id="a-1",
        description="Semantic",
        kind=AssertionKind.ASSUMPTION,
        source=None,
        check=None,
    )
    rt = Assertion.from_dict(a.to_dict())
    assert rt.check is None


def test_assertion_from_dict_no_check_key_defaults_to_none():
    """Old plan.json files without 'check' still deserialise correctly."""
    d = {
        "id": "a-1",
        "description": "Old assertion",
        "kind": "required",
        "source": {"kind": "notes", "path": "f", "excerpt": "e"},
    }
    a = Assertion.from_dict(d)
    assert a.check is None
