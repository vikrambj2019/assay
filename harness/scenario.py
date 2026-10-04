"""Playwright-backed scenario adapter for the structured check executor."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.browser import BrowserSession
from core.budget import RunBudget
from core.config import Config
from core.plan import Scenario
from core.plan import Assertion
from core.adjudicate import (
    AdjudicationInput,
    Adjudicator,
    adjudicate_fail,
)
from core.assertions import AssertionOutcome
from core.auth import resolve_auth_setup, verify_authenticated, AuthMode
from core.settle import settle
from core.schema import Verdict
from harness.agent import run_goal


class BrowserScenarioAdapter:
    """Expose one live BrowserSession through the executor adapter contract."""

    def __init__(self, session: BrowserSession, cfg: Config, out_dir: Path,
                 budget: RunBudget | None = None,
                 adjudicator: "Adjudicator | None" = None):
        self.session = session
        self.cfg = cfg
        self.out_dir = out_dir
        self.budget = budget
        self.adjudicator = adjudicator
        self._goal_logs = []
        self._goal = ""

    async def run_goal(self, scenario: Scenario) -> None:
        self._goal = scenario.goal
        logs = await run_goal(
            self.session, scenario.goal, self.cfg, self.out_dir,
            budget=self.budget,
        )
        self._goal_logs = logs
        errors = [log.reason for log in logs if log.verdict is Verdict.ERROR]
        if errors:
            raise RuntimeError("; ".join(errors))
        # NOTE: a stalled agent raises AgentStalledError from run_goal above;
        # the executor maps it to ERROR.  No timeout substring heuristics here —
        # stall detection is structural (consecutive failed actions), not textual.

    async def assess_assertion(self, assertion: Assertion) -> AssertionOutcome:
        """Use the page-grounded agent assessment for semantic assertions."""
        if not self._goal_logs:
            return AssertionOutcome(
                assertion.id, Verdict.UNVERIFIED,
                "no agent assessment was recorded for this assertion",
            )
        final = self._goal_logs[-1]
        if final.verdict is Verdict.PASS:
            return AssertionOutcome(
                assertion.id, Verdict.PASS,
                f"agent assessment: {final.reason}", final.reason,
            )
        if final.verdict is Verdict.FAIL:
            return AssertionOutcome(
                assertion.id, Verdict.FAIL,
                f"agent assessment: {final.reason}", final.reason,
            )
        return AssertionOutcome(
            assertion.id, Verdict.UNVERIFIED,
            f"agent assessment was {final.verdict.value}: {final.reason}",
            final.reason,
        )

    async def adjudicate_fail(
        self,
        verdict: Verdict,
        reason: str,
        outcomes: "list[AssertionOutcome]",
    ) -> "tuple[Verdict, str]":
        """Independently review a FAIL verdict before it is recorded.

        Returns (verdict, note).  With no adjudicator configured the verdict
        passes through untouched.  A confirmed FAIL keeps its verdict with a
        confirmation note; a rejected FAIL is downgraded to UNVERIFIED with
        both rationales preserved.  A broken reviewer never disturbs the
        original verdict.
        """
        if self.adjudicator is None:
            return verdict, ""
        trail = [
            record.detail
            for log in self._goal_logs
            for record in log.action_records
        ]
        final_text = ""
        if self.session.page is not None:
            try:
                final_text = await self.session.page.locator("body").inner_text()
            except Exception:  # noqa: BLE001 — page may be gone; trail still stands
                final_text = ""
        screenshots = sorted(p.name for p in self.out_dir.glob("step-*.png"))
        result = await adjudicate_fail(
            AdjudicationInput(
                goal=self._goal,
                reported_reason=reason,
                action_trail=trail,
                final_page_text=final_text,
                screenshot_paths=screenshots,
            ),
            self.adjudicator,
        )
        if result is None:
            return verdict, " [adjudication unavailable; original FAIL stands]"
        if self.budget is not None and result.cost_usd:
            self.budget.record_cost(result.cost_usd)
        if result.confirmed:
            return Verdict.FAIL, f" [adjudicated by {result.reviewer}: FAIL confirmed]"
        return (
            Verdict.UNVERIFIED,
            f" [adjudicator {result.reviewer} did not confirm the FAIL: "
            f"{result.reason}]",
        )

    async def current_url(self) -> str:
        return self.session.page.url if self.session.page is not None else ""

    async def page_text(self) -> str:
        if self.session.page is None:
            raise RuntimeError("browser page is not available")
        await settle(self.session.page)
        return await self.session.page.locator("body").inner_text()

    async def field_value(self, selector: str) -> str | None:
        if self.session.page is None:
            raise RuntimeError("browser page is not available")
        locator = self.session.page.locator(selector).first
        try:
            return await locator.input_value()
        except Exception as exc:  # noqa: BLE001
            if "not an input" in str(exc).lower():
                raise
            return None

    async def is_element_visible(self, selector: str) -> bool | None:
        if self.session.page is None:
            raise RuntimeError("browser page is not available")
        locator = self.session.page.locator(selector)
        if await locator.count() == 0:
            # Selector matched nothing: a planner/harness problem, reported as
            # ERROR by the assertion layer (never as an application FAIL).
            return None
        return await locator.first.is_visible()

    async def reload(self) -> None:
        if self.session.page is None:
            raise RuntimeError("browser page is not available")
        await self.session.page.reload()
        await settle(self.session.page)

    async def close(self) -> None:
        await self.session.close()


class BrowserScenarioAdapterFactory:
    """Create isolated browser sessions sequentially for plan scenarios."""

    def __init__(self, cfg: Config, out_dir: Path, budget: RunBudget | None = None,
                 adjudicator: "Adjudicator | None" = None):
        self.cfg = cfg
        self.out_dir = out_dir
        self.budget = budget
        self.adjudicator = adjudicator

    async def create(self, scenario: Scenario) -> BrowserScenarioAdapter:
        scenario_cfg = replace(self.cfg)
        if scenario_cfg.auth_state is not None and scenario_cfg.auth_state.exists():
            scenario_cfg.storage_state = scenario_cfg.auth_state
        scenario_dir = self.out_dir / scenario.id
        scenario_dir.mkdir(parents=True, exist_ok=True)
        if scenario_cfg.record_video:
            scenario_cfg.record_video_dir = scenario_dir
        session = await BrowserSession(scenario_cfg).start()
        await _prepare_authenticated_session(session, scenario_cfg)
        return BrowserScenarioAdapter(session, scenario_cfg, scenario_dir, self.budget,
                                      adjudicator=self.adjudicator)


async def _prepare_authenticated_session(session: BrowserSession, cfg: Config) -> None:
    """Log in before handing a credentialed browser session to the agent.

    Credentials are used only by Playwright here and never enter the agent
    prompt or transcript. Storage-state authentication is already applied by
    BrowserSession.start().
    """
    setup = resolve_auth_setup(cfg)
    if setup.mode is not AuthMode.CREDENTIALS:
        return
    if session.page is None:
        raise RuntimeError("authentication setup failed: browser page unavailable")
    if not cfg.login_url:
        raise RuntimeError("credentials configured but ASSAY_LOGIN_URL is empty")
    await session.page.goto(cfg.login_url)
    username = session.page.locator(
        "input[type='email'], input[name*='user' i], input[name*='email' i]"
    ).first
    password = session.page.locator("input[type='password']").first
    await username.fill(cfg.test_username)
    await password.fill(cfg.test_password)
    await session.page.locator("button[type='submit'], input[type='submit']").first.click()
    result = await verify_authenticated(session, cfg.login_url)
    if result.status != "pass":
        raise RuntimeError(f"credential authentication {result.status}: {result.reason}")
