"""Playwright-backed scenario adapter for the structured check executor."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.browser import BrowserSession
from core.budget import RunBudget
from core.config import Config
from core.plan import Scenario
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

    async def run_goal(self, scenario: Scenario) -> None:
        logs = await run_goal(
            self.session, scenario.goal, self.cfg, self.out_dir,
            budget=self.budget,
        )
        errors = [log.reason for log in logs if log.verdict is Verdict.ERROR]
        if errors:
            raise RuntimeError("; ".join(errors))

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
        return BrowserScenarioAdapter(session, scenario_cfg, scenario_dir, self.budget)
