"""Tests for the shared vocabulary in core/schema.py."""

from core.schema import StepLog, Verdict, overall


def _log(verdict: Verdict) -> StepLog:
    return StepLog(index=1, text="step", verdict=verdict)


def test_overall_fail_wins():
    logs = [_log(Verdict.PASS), _log(Verdict.FAIL), _log(Verdict.ERROR)]
    assert overall(logs) is Verdict.FAIL


def test_overall_error_beats_unverified():
    logs = [_log(Verdict.UNVERIFIED), _log(Verdict.ERROR)]
    assert overall(logs) is Verdict.ERROR


def test_overall_all_blocked_is_blocked_not_pass():
    """A suite where nothing ran must not headline PASS."""
    logs = [_log(Verdict.BLOCKED), _log(Verdict.BLOCKED)]
    assert overall(logs) is Verdict.BLOCKED


def test_overall_all_skipped_is_skipped_not_pass():
    logs = [_log(Verdict.SKIPPED), _log(Verdict.SKIPPED)]
    assert overall(logs) is Verdict.SKIPPED


def test_overall_blocked_beats_unverified():
    logs = [_log(Verdict.UNVERIFIED), _log(Verdict.BLOCKED)]
    assert overall(logs) is Verdict.BLOCKED


def test_overall_unverified_beats_skipped():
    logs = [_log(Verdict.SKIPPED), _log(Verdict.UNVERIFIED)]
    assert overall(logs) is Verdict.UNVERIFIED


def test_overall_all_pass_is_pass():
    logs = [_log(Verdict.PASS), _log(Verdict.PASS)]
    assert overall(logs) is Verdict.PASS
