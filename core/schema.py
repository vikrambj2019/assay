"""The shared vocabulary.

The verdict taxonomy, the per-step record the report renders, and the TestFile
record naming a goal. The agent speaks its verdict through the `report_stage`
tool (harness/agent.py); this module just holds the types the report is built from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Verdict(str, Enum):
    """The product's core honesty guarantee — these must stay strictly separate.

    PASS    expected outcome observed; evidence attached.
    FAIL    the app misbehaved (error banner, wrong redirect, console
            exception, 4xx/5xx on a critical request) — the valuable output.
    BLOCKED an earlier step failed; this step never ran.
    ERROR   harness problem (unresolvable target, ambiguous instruction,
            infra fault) — our bug, not the app's.
    SKIPPED     the author marked the step `skip:` in the test file; never ran.
                Not a pass, not a defect — doesn't halt the run or fail it.
    UNVERIFIED  ambiguous expectation, unsupported assertion, exhausted budget,
                or unfinished execution (run ended without complete_goal).
    """

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    UNVERIFIED = "UNVERIFIED"


@dataclass
class ActionRecord:
    """One browser action and its captured observation, identified by action_id.

    The action_id is assigned before execution (monotonically increasing within
    a goal run) so each record is self-describing: its detail, screenshot, and
    absence of a screenshot are all tied to that specific action ID — not to
    list position. A None screenshot means capture failed; it never shifts the
    captions of adjacent records.
    """

    action_id: int          # from _counter in tools.py, assigned before execution
    detail: str             # e.g. "click [3]" or "fill [2]"
    screenshot: Path | None = None  # None = capture failed; record still exists
    ok: bool = True         # False when the action itself raised (Playwright error etc.)


@dataclass
class StepLog:
    """One NL step's outcome: verdict + the action records behind it."""

    index: int  # 1-based position in the test file
    text: str  # the NL step as written
    verdict: Verdict
    reason: str = ""  # agent's reason / block reason / exception text
    evidence: list[str] = field(default_factory=list)   # console/network lines
    action_records: list[ActionRecord] = field(default_factory=list)
    duration_ms: int | None = None


@dataclass
class TestFile:
    """A natural-language goal named for the report — kept opaque (splitting prose
    into steps is the agent loop's job, harness/agent.py)."""

    __test__ = False  # not a pytest test class despite the name

    path: Path
    name: str       # e.g. "login"
    raw_text: str   # the goal prose, handed verbatim to the agent loop


def overall(logs: list[StepLog]) -> Verdict:
    """The worst verdict across logs: FAIL > ERROR > UNVERIFIED > PASS."""
    verdicts = {l.verdict for l in logs}
    if Verdict.FAIL in verdicts:
        return Verdict.FAIL
    if Verdict.ERROR in verdicts:
        return Verdict.ERROR
    if Verdict.UNVERIFIED in verdicts:
        return Verdict.UNVERIFIED
    return Verdict.PASS
