"""Task 16: deterministic ground-truth tests for the synthetic regression fixture.

Three layers:
1. Flask test-client tests — prove the clean app serves correct responses and
   each broken variant introduces exactly the intended regression.
2. Mocked-eval tests — prove the evaluation harness scores detections correctly
   using FakeEvalAdapter (no browser, no LLM).
3. Report tests — prove eval_report.json is structured correctly and contains
   counts, not invented percentages.

None of these tests contact a real browser or LLM.

Fixture app is imported from ``fixture.app.factory``; if Flask is not installed
(e.g. in a minimal CI environment that hasn't installed dev deps) all tests
in this file are skipped.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _client(bugs: list[str] | None = None):
    """Return a Flask test client for the given bug configuration."""
    from fixture.app.factory import create_app
    app = create_app(bugs=bugs)
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, *, username: str = "testuser", password: str = "password123"):
    """POST login credentials; returns the response."""
    return client.post("/login", data={"username": username, "password": password})


def _location(response) -> str:
    """Extract the redirect Location header."""
    return response.headers.get("Location", "")


# ── 1. Flask app: clean behaviour ─────────────────────────────────────────────

class TestCleanApp:
    def test_root_redirects_to_login(self):
        c = _client()
        rv = c.get("/")
        assert rv.status_code in (301, 302)
        assert "/login" in _location(rv)

    def test_login_page_renders(self):
        c = _client()
        rv = c.get("/login")
        assert rv.status_code == 200
        assert b"Sign in" in rv.data

    def test_valid_login_redirects_to_dashboard(self):
        c = _client()
        rv = _login(c)
        assert rv.status_code == 302
        assert "/dashboard" in _location(rv)

    def test_invalid_login_stays_on_login_with_error(self):
        c = _client()
        rv = c.post("/login", data={"username": "testuser", "password": "wrong"})
        assert rv.status_code == 200
        assert b"Invalid credentials" in rv.data

    def test_dashboard_shows_welcome(self):
        c = _client()
        with c.session_transaction() as sess:
            sess["user"] = "testuser"
        rv = c.get("/dashboard")
        assert rv.status_code == 200
        assert b"Welcome, testuser" in rv.data

    def test_records_page_renders(self):
        c = _client()
        rv = c.get("/records")
        assert rv.status_code == 200
        assert b"Widget A" in rv.data

    def test_record_creation_shows_saved_toast(self):
        c = _client()
        rv = c.post("/records", data={"name": "Sprocket", "quantity": "3", "price": "4.99"},
                    follow_redirects=True)
        assert rv.status_code == 200
        assert b"Record saved" in rv.data

    def test_record_creation_redirects_with_saved_param(self):
        c = _client()
        rv = c.post("/records", data={"name": "Sprocket", "quantity": "3", "price": "4.99"})
        assert rv.status_code == 302
        assert "saved=1" in _location(rv)

    def test_validation_rejects_empty_name(self):
        c = _client()
        rv = c.post("/records", data={"name": "", "quantity": "1", "price": "1.00"})
        assert rv.status_code == 200
        assert b"Name is required" in rv.data

    def test_record_detail_renders(self):
        c = _client()
        rv = c.get("/records/1")
        assert rv.status_code == 200
        assert b"Widget A" in rv.data

    def test_record_detail_shows_total(self):
        c = _client()
        rv = c.get("/records/1")
        assert b"Total" in rv.data
        # qty=5, price=9.99 → 49.95
        assert b"49.95" in rv.data

    def test_record_detail_link_correct(self):
        c = _client()
        rv = c.get("/records")
        assert b"/records/1" in rv.data
        assert b"/records-detail" not in rv.data

    def test_edit_persists_after_redirect(self):
        c = _client()
        c.post("/records/1/edit", data={"name": "Updated Widget"})
        rv = c.get("/records/1")
        assert b"Updated Widget" in rv.data

    def test_unknown_record_returns_404(self):
        c = _client()
        rv = c.get("/records/9999")
        assert rv.status_code == 404

    def test_404_page_contains_not_found(self):
        c = _client()
        rv = c.get("/does-not-exist")
        assert rv.status_code == 404
        assert b"Not Found" in rv.data


# ── 2. Flask app: broken variants ─────────────────────────────────────────────

class TestBUG001LoginWrongRedirect:
    def test_login_redirects_to_home_not_dashboard(self):
        c = _client(bugs=["BUG-001"])
        rv = _login(c)
        assert rv.status_code == 302
        loc = _location(rv)
        assert "/home" in loc
        assert "/dashboard" not in loc

    def test_home_returns_404(self):
        c = _client(bugs=["BUG-001"])
        _login(c)
        # follow the redirect
        rv = c.get("/home")
        assert rv.status_code == 404


class TestBUG002ToastMissing:
    def test_record_creation_no_saved_toast(self):
        c = _client(bugs=["BUG-002"])
        rv = c.post("/records", data={"name": "Sprocket", "quantity": "1", "price": "1.00"},
                    follow_redirects=True)
        assert rv.status_code == 200
        assert b"Record saved" not in rv.data

    def test_record_creation_no_saved_query_param(self):
        c = _client(bugs=["BUG-002"])
        rv = c.post("/records", data={"name": "Sprocket", "quantity": "1", "price": "1.00"})
        assert rv.status_code == 302
        assert "saved=1" not in _location(rv)

    def test_record_still_created_despite_missing_toast(self):
        c = _client(bugs=["BUG-002"])
        c.post("/records", data={"name": "Sprocket", "quantity": "1", "price": "1.00"},
               follow_redirects=True)
        rv = c.get("/records")
        assert b"Sprocket" in rv.data


class TestBUG003EditNotPersisted:
    def test_edit_reverts_after_reload(self):
        c = _client(bugs=["BUG-003"])
        c.post("/records/1/edit", data={"name": "Updated Widget"})
        rv = c.get("/records/1")
        assert b"Updated Widget" not in rv.data
        assert b"Widget A" in rv.data

    def test_edit_redirect_still_issued(self):
        c = _client(bugs=["BUG-003"])
        rv = c.post("/records/1/edit", data={"name": "Updated Widget"})
        assert rv.status_code == 302
        assert "/records/1" in _location(rv)


class TestBUG004ValidationBypassed:
    def test_empty_name_accepted_without_error(self):
        c = _client(bugs=["BUG-004"])
        rv = c.post("/records", data={"name": "", "quantity": "1", "price": "1.00"})
        # Should redirect (accepted), NOT show error
        assert rv.status_code == 302

    def test_no_validation_error_message_shown(self):
        c = _client(bugs=["BUG-004"])
        rv = c.post("/records", data={"name": "", "quantity": "1", "price": "1.00"},
                    follow_redirects=True)
        assert b"Name is required" not in rv.data

    def test_unnamed_record_created(self):
        c = _client(bugs=["BUG-004"])
        c.post("/records", data={"name": "", "quantity": "2", "price": "0.50"},
               follow_redirects=True)
        rv = c.get("/records")
        assert b"Unnamed" in rv.data


class TestBUG005DetailLinkBroken:
    def test_detail_link_points_to_wrong_path(self):
        c = _client(bugs=["BUG-005"])
        rv = c.get("/records")
        assert b"/records-detail/1" in rv.data
        assert b'href="/records/1"' not in rv.data

    def test_wrong_path_returns_404(self):
        c = _client(bugs=["BUG-005"])
        rv = c.get("/records-detail/1")
        assert rv.status_code == 404

    def test_correct_path_still_works(self):
        # The correct /records/1 route is unaffected — only the link is wrong
        c = _client(bugs=["BUG-005"])
        rv = c.get("/records/1")
        assert rv.status_code == 200
        assert b"Widget A" in rv.data


class TestAMBIGUOUS001Calculation:
    def test_total_displayed(self):
        c = _client()
        rv = c.get("/records/1")
        assert b"Total" in rv.data

    def test_total_value_present(self):
        c = _client()
        rv = c.get("/records/1")
        # Total = 5 * 9.99 = 49.95
        assert b"49.95" in rv.data


# ── 3. Mocked evaluation harness ─────────────────────────────────────────────

from fixture.eval.bugs import BUGS, BUGS_BY_ID, BugSpec
from fixture.eval.harness import EvalReport, EvalScenarioResult, run_mocked_eval
from core.schema import Verdict


class TestMockedEval:
    def _run(self, bugs=None) -> EvalReport:
        return asyncio.run(run_mocked_eval(bugs if bugs is not None else BUGS))

    def test_all_bugs_evaluated(self):
        report = self._run()
        assert report.sample_size == len(BUGS)

    def test_mode_is_mocked(self):
        report = self._run()
        assert report.mode == "mocked"

    def test_model_is_none_for_mocked(self):
        report = self._run()
        assert report.model is None
        assert report.model_version is None

    def test_run_count_is_one(self):
        report = self._run()
        assert report.run_count == 1

    def test_bug001_detected(self):
        report = self._run([BUGS_BY_ID["BUG-001"]])
        r = report.results[0]
        assert r.detected is True
        assert r.false_positive is False

    def test_bug002_detected(self):
        report = self._run([BUGS_BY_ID["BUG-002"]])
        assert report.results[0].detected is True

    def test_bug003_detected(self):
        report = self._run([BUGS_BY_ID["BUG-003"]])
        assert report.results[0].detected is True

    def test_bug004_detected(self):
        report = self._run([BUGS_BY_ID["BUG-004"]])
        assert report.results[0].detected is True

    def test_bug005_detected(self):
        report = self._run([BUGS_BY_ID["BUG-005"]])
        assert report.results[0].detected is True

    def test_all_bugs_detected(self):
        bug_specs = [b for b in BUGS if not b.bug_id.startswith("AMBIGUOUS")]
        report = self._run(bug_specs)
        assert report.detected_count == len(bug_specs)
        assert report.missed_count == 0
        assert report.false_positive_count == 0

    def test_no_false_positives_on_clean_app(self):
        report = self._run()
        for r in report.results:
            assert r.false_positive is False, (
                f"{r.bug_id}: false positive on clean app "
                f"(clean_verdict={r.clean_verdict})"
            )
        assert report.false_positive_count == 0

    def test_ambiguous_remains_unverified(self):
        report = self._run([BUGS_BY_ID["AMBIGUOUS-001"]])
        r = report.results[0]
        assert r.broken_verdict is Verdict.UNVERIFIED
        assert r.clean_verdict is Verdict.UNVERIFIED
        assert r.detected is False
        assert r.is_ambiguous is True

    def test_ambiguous_unverified_count(self):
        report = self._run()
        assert report.ambiguous_unverified_count == 1
        assert report.ambiguous_incorrectly_resolved_count == 0

    def test_harness_error_distinct_from_missed(self):
        """A bad check type produces ERROR, not MISS."""
        bad = BugSpec(
            bug_id="TEST-ERR",
            flow="test",
            description="produces a harness error",
            expected_failure="error",
            detection_check={"type": "unknown_check_xyz"},
            clean_url="http://localhost/",
            clean_text="ok",
            clean_text_after_reload=None,
            broken_url="http://localhost/",
            broken_text="broken",
            broken_text_after_reload=None,
        )
        report = self._run([bad])
        r = report.results[0]
        assert r.harness_error is True
        assert r.detected is False
        # harness error is NOT counted as a missed detection
        assert report.harness_error_count == 1
        assert report.missed_count == 0

    def test_missed_bug_counted_correctly(self):
        """A BugSpec whose check always PASSes is counted as missed."""
        # Use a text_visible check where both clean and broken contain the text
        missed = BugSpec(
            bug_id="TEST-MISS",
            flow="test",
            description="always passes",
            expected_failure="should fail but doesn't",
            detection_check={"type": "text_visible", "text": "Widget A"},
            clean_url="http://localhost/",
            clean_text="Widget A",
            clean_text_after_reload=None,
            broken_url="http://localhost/",
            broken_text="Widget A",  # bug present but check still passes
            broken_text_after_reload=None,
        )
        report = self._run([missed])
        r = report.results[0]
        assert r.detected is False
        assert r.harness_error is False
        assert report.missed_count == 1
        assert report.detected_count == 0

    def test_full_run_counts_add_up(self):
        """detected + missed + harness_error + ambiguous == sample_size."""
        report = self._run()
        total = (
            report.detected_count
            + report.missed_count
            + report.harness_error_count
            + report.ambiguous_unverified_count
            + report.ambiguous_incorrectly_resolved_count
        )
        assert total == report.sample_size


# ── 4. Eval report JSON ───────────────────────────────────────────────────────

from fixture.eval.report import write_eval_report


class TestEvalReport:
    def _tmpdir(self) -> Path:
        return Path(tempfile.mkdtemp())

    def _report(self) -> EvalReport:
        return asyncio.run(run_mocked_eval(BUGS))

    def test_report_created(self):
        path = write_eval_report(self._report(), self._tmpdir())
        assert path.exists()
        assert path.name == "eval_report.json"

    def test_report_parses_as_json(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        assert isinstance(doc, dict)

    def test_report_version_field(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        assert doc["version"] == "1"

    def test_report_mode_mocked(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        assert doc["mode"] == "mocked"

    def test_report_model_null_for_mocked(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        assert doc["model"] is None
        assert doc["model_version"] is None

    def test_report_run_count(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        assert doc["run_count"] == 1

    def test_report_sample_size(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        assert doc["sample_size"] == len(BUGS)

    def test_report_has_count_fields(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        for field in ("detected_count", "missed_count", "false_positive_count",
                      "harness_error_count", "ambiguous_unverified_count"):
            assert field in doc, f"missing field: {field}"

    def test_report_no_detection_rate_field(self):
        """Invented percentages must NOT appear in the report."""
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        for bad_key in ("detection_rate", "accuracy", "precision", "recall", "f1"):
            assert bad_key not in doc, f"invented metric {bad_key!r} found in report"

    def test_report_scenarios_list(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        assert len(doc["scenarios"]) == len(BUGS)

    def test_report_scenario_fields(self):
        path = write_eval_report(self._report(), self._tmpdir())
        doc = json.loads(path.read_text())
        for s in doc["scenarios"]:
            for field in ("bug_id", "flow", "clean_verdict", "broken_verdict",
                          "detected", "false_positive", "harness_error", "is_ambiguous"):
                assert field in s, f"scenario missing field: {field}"

    def test_report_distinguishes_harness_error_from_missed(self):
        """harness_error scenarios have harness_error=True, not detected=False alone."""
        bad = BugSpec(
            bug_id="TEST-ERR2",
            flow="test",
            description="error case",
            expected_failure="error",
            detection_check={"type": "unknown_xyz"},
            clean_url="http://localhost/",
            clean_text="ok",
            clean_text_after_reload=None,
            broken_url="http://localhost/",
            broken_text="broken",
            broken_text_after_reload=None,
        )
        rpt = asyncio.run(run_mocked_eval([bad]))
        path = write_eval_report(rpt, self._tmpdir())
        doc = json.loads(path.read_text())
        s = doc["scenarios"][0]
        assert s["harness_error"] is True
        assert s["detected"] is False
        assert doc["harness_error_count"] == 1
        assert doc["missed_count"] == 0

    def test_report_ambiguous_verdict_recorded(self):
        rpt = asyncio.run(run_mocked_eval([BUGS_BY_ID["AMBIGUOUS-001"]]))
        path = write_eval_report(rpt, self._tmpdir())
        doc = json.loads(path.read_text())
        s = doc["scenarios"][0]
        assert s["broken_verdict"] == "UNVERIFIED"
        assert s["is_ambiguous"] is True
