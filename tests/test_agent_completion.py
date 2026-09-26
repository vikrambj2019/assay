"""Acceptance tests for Task 02: explicit completion and SDK termination handling.

Drives the _make_stage_tools closures directly — no LLM, no browser, no network.
Covers every scenario listed in OPEN_SOURCE_REQUIREMENTS.md §02:

  1. Stage PASS then stop (no complete_goal)     → UNVERIFIED overall
  2. Turn-limit termination                       → UNVERIFIED
  3. SDK error after PASS                         → ERROR overall  (PASS stage preserved)
  4. No stages at all                             → UNVERIFIED
  5. Explicit successful completion               → PASS
  6. App FAIL                                     → FAIL

Plus verdict restriction and double-call rejection guards.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.browser import EvidenceBuffers
from core.schema import ActionRecord, StepLog, Verdict, overall
from core.redact import Redactor
from harness.agent import _make_stage_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _FakeSession:
    """Minimal BrowserSession stand-in — only evidence is accessed by the tools."""

    def __init__(self) -> None:
        self.evidence = EvidenceBuffers()
        self.action_lock = asyncio.Lock()
        self.config = SimpleNamespace(show_evidence=False)
        self.page = None


def _fake_result(num_turns: int = 5) -> SimpleNamespace:
    """Minimal stand-in for the SDK ResultMessage."""
    return SimpleNamespace(num_turns=num_turns)


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def run(session: _FakeSession, tmp_path: Path):
    """Return (report_stage_tool, complete_goal_tool, finalize) for a fresh goal."""
    records: list[ActionRecord] = []
    return _make_stage_tools(session, "do the thing", tmp_path,
                             lambda _: None, records)


# ---------------------------------------------------------------------------
# Acceptance scenario 1: stage PASS then stop without complete_goal → UNVERIFIED
# ---------------------------------------------------------------------------

async def test_stage_pass_then_stop_is_unverified(run) -> None:
    rs, _, finalize = run
    await rs.handler({"description": "login", "verdict": "PASS", "reason": "logged in"})
    logs = finalize(result=None, exc=None)
    assert overall(logs) is Verdict.UNVERIFIED
    assert "without complete_goal" in logs[-1].reason
    # The earlier PASS stage is still recorded
    assert logs[0].verdict is Verdict.PASS


# ---------------------------------------------------------------------------
# Acceptance scenario 2: turn-limit termination → UNVERIFIED with turn count
# ---------------------------------------------------------------------------

async def test_turn_limit_termination_is_unverified(run) -> None:
    _, _, finalize = run
    logs = finalize(result=_fake_result(num_turns=120), exc=None)
    assert logs[-1].verdict is Verdict.UNVERIFIED
    assert "120 turns" in logs[-1].reason


# ---------------------------------------------------------------------------
# Acceptance scenario 3: SDK error after PASS → ERROR; PASS stage preserved
# ---------------------------------------------------------------------------

async def test_sdk_error_after_pass_is_error(run) -> None:
    rs, _, finalize = run
    await rs.handler({"description": "login", "verdict": "PASS", "reason": "ok"})
    logs = finalize(result=None, exc=RuntimeError("sdk exploded"))
    assert overall(logs) is Verdict.ERROR
    assert "RuntimeError" in logs[-1].reason
    assert logs[0].verdict is Verdict.PASS   # earlier stage preserved


# ---------------------------------------------------------------------------
# Acceptance scenario 4: no stages, no complete_goal → UNVERIFIED (not ERROR)
# ---------------------------------------------------------------------------

async def test_no_stages_yields_unverified(run) -> None:
    _, _, finalize = run
    logs = finalize(result=None, exc=None)
    assert len(logs) == 1
    assert logs[0].verdict is Verdict.UNVERIFIED


# ---------------------------------------------------------------------------
# Acceptance scenario 5: explicit successful completion → PASS
# ---------------------------------------------------------------------------

async def test_explicit_completion_pass(run) -> None:
    rs, cg, finalize = run
    await rs.handler({"description": "login", "verdict": "PASS", "reason": "ok"})
    await cg.handler({"verdict": "PASS", "reason": "all stages done"})
    logs = finalize(result=None, exc=None)
    assert overall(logs) is Verdict.PASS
    assert logs[-1].verdict is Verdict.PASS


# ---------------------------------------------------------------------------
# Acceptance scenario 6: app FAIL → FAIL
# ---------------------------------------------------------------------------

async def test_app_fail_is_fail(run) -> None:
    rs, cg, finalize = run
    await rs.handler({"description": "submit form", "verdict": "FAIL",
                      "reason": "error banner shown"})
    await cg.handler({"verdict": "FAIL", "reason": "form rejected valid input"})
    logs = finalize(result=None, exc=None)
    assert overall(logs) is Verdict.FAIL
    assert logs[-1].verdict is Verdict.FAIL


# ---------------------------------------------------------------------------
# complete_goal with UNVERIFIED verdict
# ---------------------------------------------------------------------------

async def test_complete_goal_unverified(run) -> None:
    _, cg, finalize = run
    await cg.handler({"verdict": "UNVERIFIED", "reason": "ambiguous result"})
    logs = finalize(result=None, exc=None)
    assert logs[-1].verdict is Verdict.UNVERIFIED


# ---------------------------------------------------------------------------
# Verdict restrictions
# ---------------------------------------------------------------------------

async def test_report_stage_rejects_unknown_verdict(run) -> None:
    rs, _, _ = run
    out = await rs.handler({"description": "s", "verdict": "MAYBE", "reason": "x"})
    assert "PASS or FAIL" in out["content"][0]["text"]


async def test_report_stage_rejects_unverified_verdict(run) -> None:
    # UNVERIFIED is only valid for complete_goal, not report_stage
    rs, _, _ = run
    out = await rs.handler({"description": "s", "verdict": "UNVERIFIED", "reason": "x"})
    assert "complete_goal" in out["content"][0]["text"]


async def test_complete_goal_rejects_unknown_verdict(run) -> None:
    _, cg, _ = run
    out = await cg.handler({"verdict": "SURE", "reason": "x"})
    assert "PASS, FAIL, or UNVERIFIED" in out["content"][0]["text"]


# ---------------------------------------------------------------------------
# complete_goal called twice is rejected; first call wins
# ---------------------------------------------------------------------------

async def test_complete_goal_called_twice_rejected(run) -> None:
    _, cg, finalize = run
    await cg.handler({"verdict": "PASS", "reason": "done"})
    out = await cg.handler({"verdict": "FAIL", "reason": "overwrite attempt"})
    assert "already called" in out["content"][0]["text"]
    logs = finalize(result=None, exc=None)
    assert len(logs) == 1
    assert logs[0].verdict is Verdict.PASS   # first call wins


# ---------------------------------------------------------------------------
# R1: SDK is_error=True must produce ERROR regardless of complete_goal
# ---------------------------------------------------------------------------

def _error_result(errors=None, num_turns=5) -> SimpleNamespace:
    """ResultMessage with is_error=True (SDK-level execution fault)."""
    return SimpleNamespace(is_error=True, errors=errors, num_turns=num_turns)


async def test_sdk_is_error_after_complete_goal_pass_is_error(run) -> None:
    """complete_goal(PASS) followed by is_error=True must still be ERROR."""
    rs, cg, finalize = run
    await rs.handler({"description": "login", "verdict": "PASS", "reason": "ok"})
    await cg.handler({"verdict": "PASS", "reason": "all done"})
    logs = finalize(result=_error_result(), exc=None)
    assert overall(logs) is Verdict.ERROR
    # The prior PASS stage is still recorded
    assert logs[0].verdict is Verdict.PASS


async def test_sdk_is_error_without_completion_is_error(run) -> None:
    """is_error=True without complete_goal must be ERROR (not UNVERIFIED)."""
    _, _, finalize = run
    logs = finalize(result=_error_result(errors=["timeout"]), exc=None)
    assert overall(logs) is Verdict.ERROR
    assert "SDK error" in logs[-1].reason


async def test_sdk_is_error_reason_includes_errors_field(run) -> None:
    """SDK error details from ResultMessage.errors appear in the reason."""
    _, _, finalize = run
    logs = finalize(result=_error_result(errors=["execution_error"]), exc=None)
    assert "execution_error" in logs[-1].reason


async def test_finalization_and_emitted_reasons_are_redacted(session, tmp_path) -> None:
    secret = "fixture-agent-secret"
    emitted: list[str] = []
    records: list[ActionRecord] = []
    rs, cg, finalize = _make_stage_tools(
        session, "goal", tmp_path, emitted.append, records, Redactor([secret])
    )
    await rs.handler({"description": "stage", "verdict": "PASS", "reason": secret})
    await cg.handler({"verdict": "PASS", "reason": secret})
    session.evidence.console.append(SimpleNamespace(type="error", text=secret))
    logs = finalize(result=None, exc=RuntimeError(secret))
    assert all(secret not in value for value in emitted)
    assert all(secret not in log.reason for log in logs)
    assert all(secret not in evidence for log in logs for evidence in log.evidence)


# ---------------------------------------------------------------------------
# overall() ranking: FAIL > ERROR > UNVERIFIED > PASS
# ---------------------------------------------------------------------------

def test_overall_ranking() -> None:
    def make(v: Verdict) -> StepLog:
        return StepLog(1, "s", v)

    assert overall([make(Verdict.PASS)]) is Verdict.PASS
    assert overall([make(Verdict.UNVERIFIED)]) is Verdict.UNVERIFIED
    assert overall([make(Verdict.ERROR)]) is Verdict.ERROR
    assert overall([make(Verdict.FAIL)]) is Verdict.FAIL
    # Higher severity wins
    assert overall([make(Verdict.PASS), make(Verdict.UNVERIFIED)]) is Verdict.UNVERIFIED
    assert overall([make(Verdict.UNVERIFIED), make(Verdict.ERROR)]) is Verdict.ERROR
    assert overall([make(Verdict.ERROR), make(Verdict.FAIL)]) is Verdict.FAIL
