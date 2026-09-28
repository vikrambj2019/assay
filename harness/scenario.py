"""Playwright-backed scenario adapter for the structured check executor."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.browser import BrowserSession
from core.budget import RunBudget
from core.config import Config
from core.plan import Scenario
from core.plan import Assertion
from core.assertions import AssertionOutcome
from core.auth import resolve_auth_setup, verify_authenticated, AuthMode
from core.settle import settle
from core.schema import Verdict
from harness.agent import run_goal


class BrowserScenarioAdapter:
    """Expose one live BrowserSession through the executor adapter contract."""

    def __init__(self, session: BrowserSession, cfg: Config, out_dir: Path,
                 budget: RunBudget | None = None):
        self.session = session
        self.cfg = cfg
        self.out_dir = out_dir
        self.budget = budget
        self._goal_logs = []

    async def run_goal(self, scenario: Scenario) -> None:
        logs = await run_goal(
            self.session, scenario.goal, self.cfg, self.out_dir,
            budget=self.budget,
        )
        self._goal_logs = logs
        errors = [log.reason for log in logs if log.verdict is Verdict.ERROR]
        if errors:
            raise RuntimeError("; ".join(errors))
        final = logs[-1] if logs else None
        if final is not None and final.verdict is Verdict.FAIL:
            text = final.reason.lower()
            timeout_markers = ("time limit exceeded", "time limit", "timed out",
                               "timeout", "time limit exceeded")
            if any(marker in text for marker in timeout_markers):
                raise RuntimeError(
                    "browser agent appears hung: repeated tool timeout while "
                    f"executing scenario ({final.reason})"
                )

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

    async def is_element_visible(self, selector: str) -> bool:
        if self.session.page is None:
            raise RuntimeError("browser page is not available")
        return await self.session.page.locator(selector).first.is_visible()

    async def reload(self) -> None:
        if self.session.page is None:
            raise RuntimeError("browser page is not available")
        await self.session.page.reload()
        await settle(self.session.page)

    async def close(self) -> None:
        await self.session.close()


class BrowserScenarioAdapterFactory:
    """Create isolated browser sessions sequentially for plan scenarios."""

    def __init__(self, cfg: Config, out_dir: Path, budget: RunBudget | None = None):
        self.cfg = cfg
        self.out_dir = out_dir
        self.budget = budget

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
        return BrowserScenarioAdapter(session, scenario_cfg, scenario_dir, self.budget)


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
