"""Per-action page capture — retained as a stub after Task 05.

The full action → settle → snapshot → screenshot transaction was moved into
`act()` in harness/tools.py (Task 05) so it runs under a single lock
acquisition. The PostToolUse hook that used to re-capture the page here was
removed: the tool already returns the captured observation, so no replacement
via updatedMCPToolOutput is needed.

page_hooks() is kept for API compatibility; it returns an empty dict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from core.browser import BrowserSession


def page_hooks(session: "BrowserSession", out_dir: "Path", shots=None) -> dict:
    """No-op: the PostToolUse hook has been removed.

    The action + observation transaction is now atomic inside act() in
    harness/tools.py. No hook is needed to deliver the page to the model.
    """
    return {}
