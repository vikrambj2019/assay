"""Capture a Snapshot from a live page.

Capture and rendering are separate concerns (§6): this module only injects the
DOM walk and builds the structured tree + evidence. Turning the tree into text is
render.py's job, a pure function with parameters.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.snapshot.models import ConsoleEntry, NetworkFailure, Snapshot, SnapNode

if TYPE_CHECKING:
    from playwright.async_api import Page

    from core.browser import EvidenceBuffers

_DOM_WALK_JS = (Path(__file__).parent / "dom_walk.js").read_text(encoding="utf-8")


async def capture(page: "Page", evidence: "EvidenceBuffers | None" = None) -> Snapshot:
    """Walk the current DOM and return a Snapshot.

    If ``evidence`` is provided, its console + network buffers are read
    non-destructively into the snapshot (console errors and failed requests
    accumulated so far this stage), so the snapshot carries the "free,
    high-signal" evidence for the model to see. It is *peeked*, not drained —
    report_stage drains the same buffers separately for the report.
    """
    try:
        raw = await page.evaluate(_DOM_WALK_JS)
    except Exception:
        # A late navigation destroyed the JS context mid-capture; wait for the
        # new document and walk that one instead.
        await page.wait_for_load_state("domcontentloaded")
        raw = await page.evaluate(_DOM_WALK_JS)

    nodes = [SnapNode.from_json(n) for n in raw.get("nodes", [])]

    console: list[ConsoleEntry] = []
    failures: list[NetworkFailure] = []
    if evidence is not None:
        seen = evidence.peek()  # non-destructive: report_stage still drains it
        console = [ConsoleEntry(type=c.type, text=c.text) for c in seen.console]
        failures = [
            NetworkFailure(method=f.method, url=f.url, status=f.status, failure=f.failure,
                           third_party=f.third_party)
            for f in seen.network_failures
        ]

    return Snapshot(
        url=raw.get("url", ""),
        title=raw.get("title", ""),
        nodes=nodes,
        interactive_count=int(raw.get("interactive_count", 0)),
        console=console,
        network_failures=failures,
    )
