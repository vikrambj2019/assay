"""Deterministic assertion evaluation for `assay check` scenarios.

Each assertion in the plan may carry an optional ``check`` dict that
specifies a machine-verifiable condition.  This module evaluates those
checks against a live (or fake) browser page via an injectable
``AssertionCheckerAdapter`` so the logic is unit-testable without
Playwright.

When an assertion has no ``check`` spec the result is UNVERIFIED:
undefined business rules must not be silently passed.  The model is cited
as the evaluator in the reason string so reports are honest about what
was machine-verified vs. model-inferred.

Supported check types
---------------------
``url_contains``   — current URL contains ``value``
``text_visible``   — page text contains ``text``
``text_absent``    — page text does NOT contain ``text``
``field_value``    — input identified by ``selector`` has ``value``
``element_visible``— element ``selector`` is visible
``element_hidden`` — element ``selector`` is not visible
``persistence``    — ``text`` visible before reload AND after reload
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.plan import Assertion, AssertionKind
from core.schema import Verdict


# ── Adapter protocol ──────────────────────────────────────────────────────────

class AssertionCheckerAdapter(Protocol):
    """Injectable browser interface for assertion evaluation.

    The real implementation wraps a Playwright page; tests use
    ``FakeAssertionCheckerAdapter``.
    """

    async def current_url(self) -> str:
        """Return the current page URL."""
        ...

    async def page_text(self) -> str:
        """Return all visible text on the current page."""
        ...

    async def field_value(self, selector: str) -> "str | None":
        """Return the value of the first matching input, or None if not found."""
        ...

    async def is_element_visible(self, selector: str) -> bool:
        """True when the first element matching *selector* is visible."""
        ...

    async def reload(self) -> None:
        """Reload the current page and wait for it to settle."""
        ...


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class AssertionOutcome:
    """Outcome of evaluating one assertion."""

    assertion_id: str
    verdict: Verdict
    reason: str
    evidence: str = ""   # observed value or relevant excerpt; "" = not applicable


# ── Public entry point ────────────────────────────────────────────────────────

async def evaluate_assertion(
    assertion: Assertion,
    adapter: AssertionCheckerAdapter,
) -> AssertionOutcome:
    """Evaluate *assertion* against the current browser state.

    If the assertion has a ``check`` spec → run the deterministic check.
    Otherwise → UNVERIFIED with an explanation that result is model-
    evaluated; undefined business rules must not be silently passed.

    The assertion kind (required / assumption / exploratory) does not
    affect how the check is run — it affects how the scenario verdict is
    computed by the executor.

    Args:
        assertion: The assertion to evaluate (from the validated plan).
        adapter:   Provides browser state (URL, text, field values, etc.).

    Returns:
        AssertionOutcome with PASS / FAIL / UNVERIFIED / ERROR.
    """
    if assertion.check is None:
        return AssertionOutcome(
            assertion_id=assertion.id,
            verdict=Verdict.UNVERIFIED,
            reason=(
                f"assertion {assertion.id!r} has no deterministic check spec — "
                f"result is model-evaluated: {assertion.description!r}; "
                f"undefined business rules are UNVERIFIED"
            ),
        )
    return await _dispatch(assertion, adapter)


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def _dispatch(
    assertion: Assertion,
    adapter: AssertionCheckerAdapter,
) -> AssertionOutcome:
    check = assertion.check  # type: ignore[union-attr]
    check_type = check.get("type", "")

    try:
        if check_type == "url_contains":
            return await _url_contains(assertion.id, check, adapter)
        if check_type == "text_visible":
            return await _text_visible(assertion.id, check, adapter)
        if check_type == "text_absent":
            return await _text_absent(assertion.id, check, adapter)
        if check_type == "field_value":
            return await _field_value(assertion.id, check, adapter)
        if check_type == "element_visible":
            return await _element_visible(assertion.id, check, adapter)
        if check_type == "element_hidden":
            return await _element_hidden(assertion.id, check, adapter)
        if check_type == "persistence":
            return await _persistence(assertion.id, check, adapter)
        return AssertionOutcome(
            assertion_id=assertion.id,
            verdict=Verdict.ERROR,
            reason=f"unknown check type {check_type!r} — executor implementation gap",
        )
    except Exception as exc:  # noqa: BLE001
        return AssertionOutcome(
            assertion_id=assertion.id,
            verdict=Verdict.ERROR,
            reason=f"check raised unexpected exception: {exc}",
        )


# ── Individual check functions ────────────────────────────────────────────────

async def _url_contains(
    aid: str, check: dict, adapter: AssertionCheckerAdapter
) -> AssertionOutcome:
    value = str(check["value"])
    url = await adapter.current_url()
    if value in url:
        return AssertionOutcome(aid, Verdict.PASS, f"URL {url!r} contains {value!r}", url)
    return AssertionOutcome(
        aid, Verdict.FAIL,
        f"URL {url!r} does not contain {value!r}",
        url,
    )


async def _text_visible(
    aid: str, check: dict, adapter: AssertionCheckerAdapter
) -> AssertionOutcome:
    text = str(check["text"])
    page = await adapter.page_text()
    if text in page:
        return AssertionOutcome(aid, Verdict.PASS, f"text {text!r} is visible on page", text)
    return AssertionOutcome(
        aid, Verdict.FAIL,
        f"text {text!r} is not visible on page",
        "",
    )


async def _text_absent(
    aid: str, check: dict, adapter: AssertionCheckerAdapter
) -> AssertionOutcome:
    text = str(check["text"])
    page = await adapter.page_text()
    if text not in page:
        return AssertionOutcome(aid, Verdict.PASS, f"text {text!r} is absent from page", "")
    return AssertionOutcome(
        aid, Verdict.FAIL,
        f"text {text!r} unexpectedly present on page",
        text,
    )


async def _field_value(
    aid: str, check: dict, adapter: AssertionCheckerAdapter
) -> AssertionOutcome:
    selector = str(check["selector"])
    expected = str(check["value"])
    actual = await adapter.field_value(selector)
    if actual is None:
        return AssertionOutcome(
            aid, Verdict.ERROR,
            f"element {selector!r} not found — cannot read field value",
        )
    if actual == expected:
        return AssertionOutcome(
            aid, Verdict.PASS,
            f"field {selector!r} has expected value {expected!r}",
            actual,
        )
    return AssertionOutcome(
        aid, Verdict.FAIL,
        f"field {selector!r}: expected {expected!r}, observed {actual!r}",
        actual,
    )


async def _element_visible(
    aid: str, check: dict, adapter: AssertionCheckerAdapter
) -> AssertionOutcome:
    selector = str(check["selector"])
    visible = await adapter.is_element_visible(selector)
    if visible:
        return AssertionOutcome(aid, Verdict.PASS, f"element {selector!r} is visible", "")
    return AssertionOutcome(
        aid, Verdict.FAIL,
        f"element {selector!r} is not visible",
        "",
    )


async def _element_hidden(
    aid: str, check: dict, adapter: AssertionCheckerAdapter
) -> AssertionOutcome:
    selector = str(check["selector"])
    visible = await adapter.is_element_visible(selector)
    if not visible:
        return AssertionOutcome(aid, Verdict.PASS, f"element {selector!r} is hidden", "")
    return AssertionOutcome(
        aid, Verdict.FAIL,
        f"element {selector!r} should be hidden but is visible",
        "",
    )


async def _persistence(
    aid: str, check: dict, adapter: AssertionCheckerAdapter
) -> AssertionOutcome:
    """Check that *text* is visible before reload AND after reload."""
    text = str(check["text"])

    page_before = await adapter.page_text()
    if text not in page_before:
        return AssertionOutcome(
            aid, Verdict.FAIL,
            f"text {text!r} not visible before reload — cannot check persistence",
            "",
        )

    await adapter.reload()
    page_after = await adapter.page_text()
    if text in page_after:
        return AssertionOutcome(
            aid, Verdict.PASS,
            f"text {text!r} persists after page reload",
            text,
        )
    return AssertionOutcome(
        aid, Verdict.FAIL,
        f"text {text!r} was lost after page reload — persistence defect",
        "",
    )
