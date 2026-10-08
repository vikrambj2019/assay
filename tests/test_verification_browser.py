"""Real Chromium checkpoint integration; no LLM or external application."""
from core.browser import BrowserSession
from core.config import Config
from core.executor import run_plan
from core.plan import Assertion, AssertionKind, Scenario, SourceRef, make_plan
from core.policy import MutationPolicy
from core.schema import StepLog, Verdict
from harness.scenario import BrowserScenarioAdapter
from harness.verification import verification_tool


async def test_intermediate_error_is_verified_before_navigation(tmp_path):
    cfg = Config(headless=True)
    folder = tmp_path / "s1"
    folder.mkdir()
    session = await BrowserSession(cfg).start()
    adapter = BrowserScenarioAdapter(session, cfg, folder)
    intermediate = Assertion("error", "Reject empty name", AssertionKind.REQUIRED,
                             SourceRef("notes", "notes.md", "Reject empty name"),
                             {"type": "text_visible", "text": "Name is required"}, "checkpoint")
    final = Assertion("saved", "Show saved", AssertionKind.REQUIRED,
                      SourceRef("notes", "notes.md", "Show saved"),
                      {"type": "text_visible", "text": "Saved"})
    scenario = Scenario("s1", "Correct an invalid form", "Submit, correct, save", [],
                        [intermediate, final], True, "r")

    async def run_goal(_scenario):
        await session.page.set_content('''<input id="name"><button onclick="document.querySelector('p').textContent=document.querySelector('input').value ? 'Saved' : 'Name is required'">Save</button><p></p>''')
        await session.page.locator("button").click()
        verify = verification_tool(session, scenario.assertions, adapter,
                                   adapter._checkpoint_outcomes, [], folder)
        await verify.handler({"assertion_id": "error"})
        await session.page.locator("input").fill("Widget")
        await session.page.locator("button").click()
        adapter._goal_logs = [StepLog(1, "Goal", Verdict.PASS, "Saved")]

    adapter.run_goal = run_goal
    class Factory:
        async def create(self, scenario):
            return adapter
    result = await run_plan(make_plan("low", [scenario]), MutationPolicy(True), Factory())
    row = result.scenario_results[0]
    assert row.verdict is Verdict.PASS
    assert [o.evidence for o in row.assertion_outcomes] == ["Name is required", "Saved"]
    assert all((tmp_path / o.screenshot).is_file() for o in row.assertion_outcomes)
