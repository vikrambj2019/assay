"""EvidenceBuffers peek vs drain — the invariant Option 2 depends on. No browser.

The page snapshot the model reads *peeks* the console/network buffers (so it sees
live errors), while report_stage *drains* them per stage. peek must be
non-destructive, or the report would lose the evidence the snapshot showed.
"""

from __future__ import annotations

from core.browser import ConsoleEntry, EvidenceBuffers, NetworkFailure


def _buffers() -> EvidenceBuffers:
    return EvidenceBuffers(
        console=[ConsoleEntry(type="error", text="boom")],
        network_failures=[NetworkFailure(method="GET", url="http://x/api", status=500)],
    )


def test_peek_is_non_destructive() -> None:
    ev = _buffers()
    seen = ev.peek()
    assert seen.console and seen.network_failures      # peek sees the evidence
    assert ev.console and ev.network_failures          # …and leaves it in place
    # so a later drain (report_stage) still gets it
    drained = ev.drain()
    assert drained.console[0].text == "boom"
    assert drained.network_failures[0].status == 500


def test_drain_clears_then_peek_is_empty() -> None:
    ev = _buffers()
    ev.drain()
    assert not ev.console and not ev.network_failures
    seen = ev.peek()
    assert not seen.console and not seen.network_failures


def test_peek_returns_a_copy_not_a_view() -> None:
    ev = _buffers()
    seen = ev.peek()
    ev.console.clear()                                 # mutating the source…
    assert seen.console                                # …must not empty the peek
