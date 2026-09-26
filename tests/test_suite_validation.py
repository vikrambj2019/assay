"""Acceptance tests for Task 03: suite validation and per-test failure containment.

No LLM, no browser. _run_test is patched throughout. Covers every acceptance
criterion from OPEN_SOURCE_REQUIREMENTS.md §03:

  - One failing worker does not lose another worker's report
  - Dependents become BLOCKED when upstream fails
  - Zero parallelism fails immediately (at load time)
  - Traversal names cannot write outside the run directory
  - Simultaneous runs get distinct directories
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from core.schema import StepLog, Verdict
from core.suite import load_suite, run_suite


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_suite(tmp_path: Path, tests: list[dict],
                 defaults: dict | None = None) -> Path:
    data: dict = {"tests": tests}
    if defaults:
        data["defaults"] = defaults
    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _pass_log(goal: str) -> list[StepLog]:
    return [StepLog(1, f"Goal: {goal}", Verdict.PASS, "ok")]


def _fail_log(goal: str) -> list[StepLog]:
    return [StepLog(1, f"Goal: {goal}", Verdict.FAIL, "app broke")]


# ---------------------------------------------------------------------------
# load_suite validation — all errors must fire before any browser starts
# ---------------------------------------------------------------------------

def test_empty_suite_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [])
    with pytest.raises(ValueError, match="no tests"):
        load_suite(p)


def test_zero_parallelism_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [{"name": "a", "goal": "g"}],
                     {"max_parallel": 0})
    with pytest.raises(ValueError, match="max_parallel"):
        load_suite(p)


def test_negative_parallelism_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [{"name": "a", "goal": "g"}],
                     {"max_parallel": -1})
    with pytest.raises(ValueError, match="max_parallel"):
        load_suite(p)


def test_traversal_name_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [{"name": "../escape", "goal": "g"}])
    with pytest.raises(ValueError, match="unsafe"):
        load_suite(p)


def test_dotdot_name_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [{"name": "..", "goal": "g"}])
    with pytest.raises(ValueError, match="unsafe"):
        load_suite(p)


def test_slash_in_name_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [{"name": "foo/bar", "goal": "g"}])
    with pytest.raises(ValueError, match="unsafe"):
        load_suite(p)


def test_empty_name_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [{"name": "", "goal": "g"}])
    with pytest.raises(ValueError, match="unsafe"):
        load_suite(p)


def test_safe_names_accepted(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [
        {"name": "login-test", "goal": "g"},
        {"name": "update_timeline", "goal": "g"},
        {"name": ".hidden", "goal": "g"},   # dotfiles are fine
    ])
    specs, _ = load_suite(p)
    assert [s.name for s in specs] == ["login-test", "update_timeline", ".hidden"]


def test_concurrent_root_session_conflict_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [
        {"name": "root-a", "goal": "g", "session": "shared.json"},
        {"name": "root-b", "goal": "g", "session": "shared.json"},
    ])
    with pytest.raises(ValueError, match="session"):
        load_suite(p)


def test_concurrent_root_session_ok_when_one_is_dependent(tmp_path: Path) -> None:
    # root-b has needs: [root-a], so it is not a concurrent root.
    p = _write_suite(tmp_path, [
        {"name": "root-a", "goal": "g", "session": "shared.json"},
        {"name": "root-b", "goal": "g", "session": "shared.json",
         "needs": ["root-a"]},
    ])
    specs, _ = load_suite(p)   # must not raise
    assert len(specs) == 2


def test_concurrent_root_session_ok_when_one_is_skipped(tmp_path: Path) -> None:
    # A skipped root never runs, so there's no actual write race.
    p = _write_suite(tmp_path, [
        {"name": "root-a", "goal": "g", "session": "shared.json"},
        {"name": "root-b", "goal": "g", "session": "shared.json", "skip": True},
    ])
    specs, _ = load_suite(p)   # must not raise
    assert len(specs) == 2


# ---------------------------------------------------------------------------
# R7: session path conflict detection uses resolved paths
# ---------------------------------------------------------------------------

def test_session_conflict_detected_via_dotslash_alias(tmp_path: Path) -> None:
    # "shared.json" and "./shared.json" resolve to the same path — must conflict.
    p = _write_suite(tmp_path, [
        {"name": "root-a", "goal": "g", "session": "shared.json"},
        {"name": "root-b", "goal": "g", "session": "./shared.json"},
    ])
    with pytest.raises(ValueError, match="session"):
        load_suite(p)


def test_session_conflict_detected_via_absolute_path(tmp_path: Path) -> None:
    # An absolute path equivalent to the relative one must also conflict.
    import os
    abs_path = str(Path(os.getcwd()) / "shared.json")
    p = _write_suite(tmp_path, [
        {"name": "root-a", "goal": "g", "session": "shared.json"},
        {"name": "root-b", "goal": "g", "session": abs_path},
    ])
    with pytest.raises(ValueError, match="session"):
        load_suite(p)


# ---------------------------------------------------------------------------
# R6: invalid YAML types rejected before browser launch
# ---------------------------------------------------------------------------

def test_string_skip_rejected(tmp_path: Path) -> None:
    # YAML quoted "false" is a string — must not silently skip the test.
    p = tmp_path / "suite.yaml"
    p.write_text('tests:\n  - name: t\n    goal: g\n    skip: "false"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="skip"):
        load_suite(p)


def test_string_headless_rejected(tmp_path: Path) -> None:
    p = tmp_path / "suite.yaml"
    p.write_text('tests:\n  - name: t\n    goal: g\n    headless: "false"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="headless"):
        load_suite(p)


def test_null_goal_rejected(tmp_path: Path) -> None:
    p = _write_suite(tmp_path, [{"name": "t", "goal": None}])
    with pytest.raises(ValueError, match="goal"):
        load_suite(p)


# ---------------------------------------------------------------------------
# run_suite: failing worker does not lose passing worker's report
# ---------------------------------------------------------------------------

async def test_failing_worker_does_not_lose_passing_worker(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_RESULTS_DIR", str(tmp_path / "results"))
    suite = _write_suite(tmp_path, [
        {"name": "good", "goal": "do good"},
        {"name": "bad",  "goal": "do bad"},
    ])

    async def fake_run_test(spec, cfg_base, suite_dir, emit, usage):
        if spec.name == "bad":
            raise RuntimeError("browser crashed")
        return _pass_log(spec.goal)

    monkeypatch.setattr("core.suite._run_test", fake_run_test)
    events: list[str] = []
    rc = await run_suite(suite, on_event=events.append)

    assert rc == 1                                       # not all PASS
    index_files = list((tmp_path / "results").rglob("index.html"))
    assert index_files, "suite index.html must be written even after a worker crash"
    # "bad" became ERROR, "good" stayed PASS — both recorded
    assert any("ERROR" in e for e in events)
    assert any("good" in e and "PASS" in e for e in events)


# ---------------------------------------------------------------------------
# run_suite: dependents become BLOCKED when upstream fails
# ---------------------------------------------------------------------------

async def test_dependent_blocked_when_upstream_fails(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_RESULTS_DIR", str(tmp_path / "results"))
    suite = _write_suite(tmp_path, [
        {"name": "login",     "goal": "log in"},
        {"name": "dashboard", "goal": "check dash", "needs": ["login"]},
    ])

    async def fake_run_test(spec, cfg_base, suite_dir, emit, usage):
        return _fail_log(spec.goal)

    monkeypatch.setattr("core.suite._run_test", fake_run_test)
    events: list[str] = []
    rc = await run_suite(suite, on_event=events.append)

    assert rc == 1
    assert any("BLOCKED" in e for e in events)
    # dashboard never received a START — it was blocked before execution
    assert not any("START" in e and "dashboard" in e for e in events)


# ---------------------------------------------------------------------------
# run_suite: exception in _run_test becomes ERROR, dependents are BLOCKED
# ---------------------------------------------------------------------------

async def test_crash_becomes_error_and_blocks_dependents(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_RESULTS_DIR", str(tmp_path / "results"))
    suite = _write_suite(tmp_path, [
        {"name": "login",     "goal": "log in"},
        {"name": "dashboard", "goal": "check dash", "needs": ["login"]},
    ])

    async def fake_run_test(spec, cfg_base, suite_dir, emit, usage):
        raise RuntimeError("playwright failed to start")

    monkeypatch.setattr("core.suite._run_test", fake_run_test)
    events: list[str] = []
    rc = await run_suite(suite, on_event=events.append)

    assert rc == 1
    assert any("ERROR" in e for e in events)
    assert any("BLOCKED" in e for e in events)


# ---------------------------------------------------------------------------
# Collision-resistant run directories
# ---------------------------------------------------------------------------

def test_simultaneous_runs_get_distinct_directories(tmp_path: Path) -> None:
    from datetime import datetime
    from core.config import Config

    # Fix datetime.now() to the same value for both calls so they produce the
    # same timestamp stamp — the second call must still get a distinct directory.
    fixed = datetime(2025, 1, 1, 12, 0, 0)
    with patch("core.config.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        cfg = Config(results_root=tmp_path)
        d1 = cfg.new_run_dir()
        d2 = cfg.new_run_dir()

    assert d1 != d2, "simultaneous runs must get distinct directories"
    assert d1.exists() and d2.exists()
    assert d1.parent == tmp_path and d2.parent == tmp_path
