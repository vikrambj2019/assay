"""Settlement detector (§7) — wait until the page is actually "done".

Replaces guessing a fixed wait. After a navigation or action:
  1. network-idle  — no in-flight requests for a sustained window (Playwright).
  2. dom-quiet      — a MutationObserver sees no DOM changes for a short window,
                      so a client-rendered SPA is given time to draw.

Both phases share one budget and never hang: network-idle is bounded and treated
as "mostly idle" (a timeout is noted, not fatal — handles infinite pollers), and
dom-quiet has a hard cap. The result records why it settled, which becomes step
evidence later.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

if TYPE_CHECKING:
    from playwright.async_api import Page

# Resolves when no mutation has fired for `quietMs`, or after `maxMs` regardless.
_DOM_QUIET_JS = """
(args) => new Promise((resolve) => {
  const { quietMs, maxMs } = args;
  let quiet, hard;
  const done = (reason) => { obs.disconnect(); clearTimeout(quiet); clearTimeout(hard); resolve(reason); };
  const obs = new MutationObserver(() => {
    clearTimeout(quiet);
    quiet = setTimeout(() => done('dom-quiet'), quietMs);
  });
  obs.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  quiet = setTimeout(() => done('dom-quiet'), quietMs);
  hard = setTimeout(() => done('dom-busy'), maxMs);
})
"""


@dataclass
class SettleResult:
    reason: str        # e.g. "network-idle; dom-quiet"
    elapsed_ms: int


async def settle(page: "Page", timeout_ms: int = 8000, dom_quiet_ms: int = 400,
                 dom_max_ms: int = 3000) -> SettleResult:
    start = time.monotonic()

    def remaining() -> int:
        left = timeout_ms - int((time.monotonic() - start) * 1000)
        return max(1, left)  # never pass 0 to Playwright (0 = wait forever)

    phases: list[str] = []

    try:
        await page.wait_for_load_state("networkidle", timeout=remaining())
        phases.append("network-idle")
    except PlaywrightTimeoutError:
        phases.append("network-busy")  # mostly-idle: note it, keep going
    except Exception:
        phases.append("network-unknown")  # navigation race: keep going

    # dom_max_ms caps this phase on its own: a page that animates forever
    # (spinner, carousel) must not eat the whole budget on every action.
    args = {"quietMs": dom_quiet_ms, "maxMs": min(remaining(), dom_max_ms)}
    try:
        reason = await page.evaluate(_DOM_QUIET_JS, args)
    except Exception:
        # The click navigated mid-settle and destroyed the JS context; wait for
        # the new document and watch that one instead.
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=remaining())
            reason = await page.evaluate(_DOM_QUIET_JS, args)
        except Exception:
            reason = "dom-unavailable"
    phases.append(reason)

    return SettleResult(reason="; ".join(phases), elapsed_ms=int((time.monotonic() - start) * 1000))
