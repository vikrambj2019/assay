"""Report unit tests — pure, no browser, no LLM."""

from __future__ import annotations

from pathlib import Path

from core.report import write_report
from core.schema import ActionRecord, StepLog, TestFile, Verdict


def _test_file():
    return TestFile(path=Path("examples/login.md"), name="login test", raw_text="1. Open x")


def test_verdict_taxonomy_is_distinct() -> None:
    # The core honesty guarantee: these must never collapse into one another.
    values = {v.value for v in Verdict}
    assert values == {"PASS", "FAIL", "BLOCKED", "ERROR", "SKIPPED", "UNVERIFIED"}


def test_writes_self_contained_html(tmp_path: Path):
    logs = [
        StepLog(1, "Open the login page", Verdict.PASS, reason="page shows the form",
                action_records=[ActionRecord(1, "goto https://x.test/login")],
                duration_ms=120),
        StepLog(2, "Log in as student", Verdict.FAIL,
                reason="error banner: Your password is invalid!",
                action_records=[ActionRecord(2, 'fill [2] "student"'),
                                ActionRecord(3, "click [4]")],
                evidence=["console error: boom"]),
    ]
    path = write_report(_test_file(), logs, tmp_path)

    assert path == tmp_path / "report.html"
    html = path.read_text()
    assert "<!doctype html>" in html
    assert "login test" in html
    assert "PASS" in html and "FAIL" in html
    assert "Log in as student" in html                       # step text rendered
    assert "fill [2] &quot;student&quot;" in html            # action trail rendered
    assert "console error: boom" in html                     # evidence rendered
    assert "error banner: Your password is invalid!" in html # verifier reason rendered


def test_report_embeds_step_screenshots(tmp_path: Path):
    """Each ActionRecord with a screenshot gets its own figure; screenshots embed as base64."""
    for n in ("step-01.png", "step-02.png"):
        (tmp_path / n).write_bytes(b"fake-png")
    logs = [StepLog(1, "the goal", Verdict.PASS, reason="done",
                    action_records=[
                        ActionRecord(1, "click [1]", tmp_path / "step-01.png"),
                        ActionRecord(2, "fill [2]", tmp_path / "step-02.png"),
                    ])]

    html = write_report(_test_file(), logs, tmp_path).read_text()
    assert html.count("data:image/png;base64,") == 2
    assert "step-01" in html and "step-02" in html
    assert "click [1]" in html and "fill [2]" in html


def test_failed_middle_screenshot_preserves_adjacent_captions(tmp_path: Path):
    """Task 06 acceptance test: a failed middle screenshot must not corrupt the
    captions of the first and third screenshots."""
    (tmp_path / "step-01.png").write_bytes(b"fake-png")
    # step-02.png intentionally absent — simulates a capture failure
    (tmp_path / "step-03.png").write_bytes(b"fake-png")

    logs = [StepLog(1, "three-action stage", Verdict.PASS, reason="done",
                    action_records=[
                        ActionRecord(1, "click [1]", tmp_path / "step-01.png"),
                        ActionRecord(2, "fill [3]", None),       # screenshot failed
                        ActionRecord(3, "press Enter", tmp_path / "step-03.png"),
                    ])]

    html = write_report(_test_file(), logs, tmp_path).read_text()

    # Exactly two images: step-01 and step-03; the failed middle has no image.
    assert html.count("data:image/png;base64,") == 2

    # All three actions are present — none is silently dropped.
    assert "click [1]" in html
    assert "fill [3]" in html
    assert "press Enter" in html

    # step-01 is captioned "click [1]" — not shifted to "fill [3]".
    assert "step-01:</span>click [1]" in html
    # step-03 is captioned "press Enter" — not shifted to "fill [3]".
    assert "step-03:</span>press Enter" in html
    # Specifically, "fill [3]" is NOT used as a caption for any screenshot.
    assert "step-01:</span>fill [3]" not in html
    assert "step-03:</span>fill [3]" not in html


def test_overall_is_fail_when_any_step_fails(tmp_path: Path):
    logs = [
        StepLog(1, "a", Verdict.PASS),
        StepLog(2, "b", Verdict.ERROR),
        StepLog(3, "c", Verdict.FAIL),
    ]
    html = write_report(_test_file(), logs, tmp_path).read_text()
    # header overall badge appears before the per-step cards
    assert html.index("FAIL") < html.index("class='step'")
