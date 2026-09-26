"""Bug registry for the synthetic regression evaluation fixture.

Each ``BugSpec`` describes one known bug with a stable ID and the page states
that a real browser would observe on the clean vs. broken app.  These are the
"answer labels" used to score evaluation runs — they are NEVER passed to the
planner or model as context.

The ``detection_check`` field mirrors the ``Assertion.check`` dict used by
``evaluate_assertion`` so the mocked harness can run the same assertion logic
without a browser.

Bug IDs match those in ``fixture/app/factory.py`` and are stable across
releases so results from different evaluation runs can be compared.

AMBIGUOUS-001 has ``detection_check=None`` — its assertion intentionally has
no deterministic check spec and must always result in UNVERIFIED.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BugSpec:
    """Describes one evaluation scenario: a known bug + page states for scoring.

    Attributes:
        bug_id:               Stable identifier (e.g. "BUG-001").
        flow:                 Which user flow is affected (e.g. "login").
        description:          Human-readable description of the regression.
        expected_failure:     What the scanner should observe when the bug fires.
        detection_check:      Assertion check dict (see Assertion.check).
                              ``None`` for ambiguous assertions.
        clean_url:            URL the browser would see on the clean app.
        clean_text:           Page text the browser would see on the clean app.
        clean_text_after_reload: Page text after reload (for persistence checks).
        broken_url:           URL on the broken app.
        broken_text:          Page text on the broken app.
        broken_text_after_reload: Page text after reload on the broken app.
    """

    bug_id: str
    flow: str
    description: str
    expected_failure: str
    detection_check: dict | None

    # Page state observed by the scanner on the CLEAN app (no bugs active):
    clean_url: str
    clean_text: str
    clean_text_after_reload: str | None

    # Page state observed by the scanner on the BROKEN app (this bug active):
    broken_url: str
    broken_text: str
    broken_text_after_reload: str | None


# ── Ground-truth bug registry ─────────────────────────────────────────────────
# Answer labels: keep outside planner context.

BUGS: list[BugSpec] = [
    BugSpec(
        bug_id="BUG-001",
        flow="login",
        description=(
            "After successful login the app redirects to /home (does not exist) "
            "instead of /dashboard."
        ),
        expected_failure="URL does not contain /dashboard",
        detection_check={"type": "url_contains", "value": "/dashboard"},
        clean_url="http://localhost:5173/dashboard",
        clean_text="Welcome, testuser! Dashboard Records",
        clean_text_after_reload=None,
        broken_url="http://localhost:5173/home",
        broken_text="Not Found The requested page does not exist.",
        broken_text_after_reload=None,
    ),
    BugSpec(
        bug_id="BUG-002",
        flow="record_creation",
        description=(
            "After creating a record the 'Record saved' confirmation banner "
            "is absent from the page."
        ),
        expected_failure="text 'Record saved' not visible on page",
        detection_check={"type": "text_visible", "text": "Record saved"},
        clean_url="http://localhost:5173/records?saved=1",
        clean_text="Records Record saved Widget A",
        clean_text_after_reload=None,
        broken_url="http://localhost:5173/records",
        broken_text="Records Widget A",  # no "Record saved"
        broken_text_after_reload=None,
    ),
    BugSpec(
        bug_id="BUG-003",
        flow="edit_persistence",
        description=(
            "A record edit appears to succeed but the new name reverts to the "
            "original value after a page reload."
        ),
        expected_failure="text 'Updated Widget' lost after page reload",
        detection_check={"type": "persistence", "text": "Updated Widget"},
        clean_url="http://localhost:5173/records/1",
        clean_text="Updated Widget",
        clean_text_after_reload="Updated Widget",   # edit persists
        broken_url="http://localhost:5173/records/1",
        broken_text="Updated Widget",               # appears to save initially
        broken_text_after_reload="Widget A",        # reverts after reload
    ),
    BugSpec(
        bug_id="BUG-004",
        flow="form_validation",
        description=(
            "Submitting the new-record form with an empty name field is accepted "
            "silently; no 'Name is required' error message is displayed."
        ),
        expected_failure="text 'Name is required' not visible on page",
        detection_check={"type": "text_visible", "text": "Name is required"},
        clean_url="http://localhost:5173/records",
        clean_text="Records Name is required",
        clean_text_after_reload=None,
        broken_url="http://localhost:5173/records",
        broken_text="Records Unnamed",   # empty name silently accepted
        broken_text_after_reload=None,
    ),
    BugSpec(
        bug_id="BUG-005",
        flow="navigation",
        description=(
            "Record detail links in the list navigate to /records-detail/<id> "
            "instead of /records/<id>, returning 404."
        ),
        expected_failure="URL does not contain /records/1",
        detection_check={"type": "url_contains", "value": "/records/1"},
        clean_url="http://localhost:5173/records/1",
        clean_text="Widget A Quantity: 5",
        clean_text_after_reload=None,
        broken_url="http://localhost:5173/records-detail/1",
        broken_text="Not Found The requested page does not exist.",
        broken_text_after_reload=None,
    ),
    BugSpec(
        bug_id="AMBIGUOUS-001",
        flow="calculation",
        description=(
            "The record detail page displays a 'Total' figure.  The requirement "
            "states 'the total should be correct' but does not define what "
            "'correct' means — there is no deterministic check spec."
        ),
        expected_failure="UNVERIFIED — 'correct' is undefined; cannot confirm outcome",
        detection_check=None,   # no deterministic check; must remain UNVERIFIED
        clean_url="http://localhost:5173/records/1",
        clean_text="Widget A Total: $49.95",
        clean_text_after_reload=None,
        broken_url="http://localhost:5173/records/1",
        broken_text="Widget A Total: $49.95",   # same page; bug is the ambiguous req
        broken_text_after_reload=None,
    ),
]

# Index for fast lookup by ID
BUGS_BY_ID: dict[str, BugSpec] = {b.bug_id: b for b in BUGS}
