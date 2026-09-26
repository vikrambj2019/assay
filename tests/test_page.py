"""Tests for harness/page.py after Task 05.

After Task 05 the PostToolUse hook was removed. The full action → settle →
snapshot → screenshot transaction now runs inside act() in harness/tools.py
under a single lock acquisition. page_hooks() is retained for API
compatibility but returns an empty dict (no hooks are installed).

The substantive capture/delivery behaviour is tested in test_action_atomicity.py.
"""

from __future__ import annotations

from harness.page import page_hooks


def test_page_hooks_returns_empty_dict() -> None:
    """page_hooks() is now a no-op — no PostToolUse hook is registered."""
    result = page_hooks(None, None, None)  # type: ignore[arg-type]
    assert result == {}
