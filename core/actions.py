"""Actions — typed operations on interactive elements by their snapshot index.

Each interactive element was tagged with ``data-assay-index`` during capture, so an
index maps straight to a Playwright locator. Actions inherit Playwright's
actionability auto-wait (visible, enabled, stable) and its native value setter —
so the React "field reverts after submit" problem from the old repos doesn't
arise, and no raw ``el.value = x`` dance is needed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from core.settle import settle

if TYPE_CHECKING:
    from playwright.async_api import Locator

    from core.browser import BrowserSession

# The conditions wait_for understands. "idle" watches the whole page settle;
# the rest watch one element (by index) or the page's text.
WAIT_CONDITIONS = ("time", "idle", "visible", "hidden", "enabled", "text", "change")


@dataclass
class ActionResult:
    ok: bool
    detail: str
    navigated: bool = False


class Actor:
    def __init__(self, session: "BrowserSession"):
        self.session = session

    def _loc(self, index: int) -> "Locator":
        return self.session.page.locator(f'[data-assay-index="{index}"]')  # type: ignore[union-attr]

    async def _text(self, index: int | None) -> str:
        """Visible text of one element (by index), or the whole page body."""
        if index is None:
            return await self.session.page.inner_text("body")  # type: ignore[union-attr]
        loc = self._loc(index)
        return await loc.first.inner_text() if await loc.count() else ""

    async def _run(self, verb: str, op: Callable[[], Awaitable[None]],
                   index: int | None = None) -> ActionResult:
        page = self.session.page
        assert page is not None, "session not started"
        before = page.url
        try:
            # Fast-fail on a stale index instead of letting the locator auto-wait
            # its full timeout for an element that re-rendered away.
            if index is not None and await self._loc(index).count() == 0:
                return ActionResult(
                    False,
                    f"{verb} failed: no element with index [{index}] on the current "
                    "page — the page re-rendered; use the indices from the updated "
                    "page shown below",
                )
            await op()
        except Exception as e:  # surfaced as evidence, never a crash
            return ActionResult(False, f"{verb} failed: {type(e).__name__}: {e}")
        return ActionResult(True, verb, navigated=page.url != before)

    async def goto(self, url: str) -> ActionResult:
        return await self._run(f"goto {url}", lambda: self.session.goto(url))

    async def click(self, index: int) -> ActionResult:
        return await self._run(f"click [{index}]", lambda: self._loc(index).click(), index)

    async def _is_password(self, index: int) -> bool:
        """True when the element at [index] is an <input type='password'>."""
        try:
            loc = self._loc(index)
            if await loc.count() == 0:
                return False
            return (await loc.first.get_attribute("type") or "").lower() == "password"
        except Exception:  # noqa: BLE001
            return False

    async def fill(self, index: int, text: str) -> ActionResult:
        preview = '"[REDACTED]"' if await self._is_password(index) else _preview(text)
        return await self._run(f"fill [{index}] {preview}",
                               lambda: self._loc(index).fill(text), index)

    async def press(self, key: str, index: int | None = None) -> ActionResult:
        page = self.session.page
        assert page is not None, "session not started"
        target = self._loc(index) if index is not None else page.keyboard
        return await self._run(f"press {key}", lambda: target.press(key), index)

    async def upload(self, index: int, path: str) -> ActionResult:
        # set_input_files drives the <input type=file> directly — no native OS
        # picker, and it works even when the input is hidden (the common case).
        return await self._run(f"upload [{index}] {_preview(path)}",
                               lambda: self._loc(index).set_input_files(path), index)

    async def wait_for(self, until: str, index: int | None = None, text: str = "",
                       timeout: float = 15.0) -> ActionResult:
        """Poll until a condition holds (or time out). Lets the agent wait out
        async work — a slow API, a spinner, a late-arriving reply — in one run,
        instead of scheduling an out-of-band wakeup that abandons the run."""
        page = self.session.page
        assert page is not None, "session not started"
        timeout = max(0.5, min(timeout, 300.0))  # cap at 5 min
        label = (f"wait_for {until}" + (f" [{index}]" if index is not None else "")
                 + (f" {_preview(text)}" if text else ""))
        t0 = time.monotonic()

        if until == "time":  # a plain fixed pause, e.g. "wait 30 seconds"
            await asyncio.sleep(timeout)
            return ActionResult(True, f"{label} ({time.monotonic() - t0:.0f}s)")
        if until == "idle":
            res = await settle(page, timeout_ms=int(timeout * 1000))
            return ActionResult(True, f"{label} — {res.reason} ({time.monotonic() - t0:.0f}s)")
        if until not in WAIT_CONDITIONS:
            return ActionResult(False, f"wait_for failed: unknown condition {until!r} "
                                       f"(use one of {', '.join(WAIT_CONDITIONS)})")

        baseline = await self._text(index) if until == "change" else None

        async def holds() -> bool:
            if until == "change":
                return (await self._text(index)) != baseline
            if until == "text":
                if not text:
                    raise ValueError("condition 'text' needs a text argument")
                return text.lower() in (await page.inner_text("body")).lower()
            if index is None:
                raise ValueError(f"condition {until!r} needs an index argument")
            loc = self._loc(index)
            if await loc.count() == 0:
                return until == "hidden"  # gone counts as hidden, never visible/enabled
            el = loc.first
            if until == "visible":
                return await el.is_visible()
            if until == "hidden":
                return not await el.is_visible()
            return await el.is_enabled()  # "enabled"

        try:
            while True:
                if await holds():
                    return ActionResult(True, f"{label} ({time.monotonic() - t0:.0f}s)")
                if time.monotonic() - t0 >= timeout:
                    return ActionResult(False, f"{label} — timed out after {timeout:g}s")
                await asyncio.sleep(0.3)
        except Exception as e:
            return ActionResult(False, f"{label} failed: {type(e).__name__}: {e}")

    async def scroll(self, mode: str, index: int | None = None) -> ActionResult:
        page = self.session.page
        assert page is not None, "session not started"
        if index is not None:
            return await self._run(f"scroll [{index}] {mode}",
                                   lambda: self._loc(index).evaluate(_SCROLL_EL_JS, mode), index)
        return await self._run(f"scroll {mode}",
                               lambda: page.evaluate(_SCROLL_PAGE_JS, mode))

    async def hover(self, index: int) -> ActionResult:
        return await self._run(f"hover [{index}]", lambda: self._loc(index).hover(), index)

    async def go_back(self) -> ActionResult:
        page = self.session.page
        assert page is not None, "session not started"
        return await self._run("go_back", lambda: page.go_back())

    async def select_option(self, index: int) -> ActionResult:
        """Select the option at [index] directly — for a page that lists <option>
        items each with their own index. If the option belongs to a native
        <select>, drive that select with Playwright's native setter; otherwise
        click it (a custom dropdown item)."""
        loc = self._loc(index)

        async def op() -> None:
            handle = await loc.element_handle()
            select = (await handle.evaluate_handle(  # the owning <select>, or null
                "o => (o.closest && o.closest('select')) || null")).as_element()
            if select is None:
                await loc.click()  # custom option item — a click selects it
                return
            value = await handle.get_attribute("value")
            if value:
                await select.select_option(value=value)
            else:  # options without a value attribute select by their label
                await select.select_option(label=(await handle.text_content() or "").strip())

        return await self._run(f"select_option [{index}]", op, index)


def _preview(text: str, n: int = 40) -> str:
    return f'"{text if len(text) <= n else text[:n] + "…"}"'


# Scroll by ~a viewport, or jump to either end. One down is the common "reveal
# more / load next" move; top/bottom go to the extremes. Two shapes because
# locator.evaluate passes the element as the first arg, page.evaluate doesn't.
_SCROLL_EL_JS = """(el, dir) => {
  if (dir === 'into_view') return el.scrollIntoView({ block: 'center' });
  const step = el.clientHeight * 0.9;
  if (dir === 'top') el.scrollTo({ top: 0 });
  else if (dir === 'bottom') el.scrollTo({ top: el.scrollHeight });
  else el.scrollBy({ top: dir === 'up' ? -step : step });
}"""

_SCROLL_PAGE_JS = """(dir) => {
  const el = document.scrollingElement || document.documentElement;
  const step = window.innerHeight * 0.9;
  if (dir === 'top') el.scrollTo({ top: 0 });
  else if (dir === 'bottom') el.scrollTo({ top: el.scrollHeight });
  else el.scrollBy({ top: dir === 'up' ? -step : step });
}"""
