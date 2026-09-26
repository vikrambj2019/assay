"""Playwright session wrapper — the browser substrate.

Thin layer over Playwright's async API. It launches Chromium, hands out a page,
persists the session (cookies + storage) across runs, and passively captures the
"free, high-signal" evidence: console errors and failed network requests. That
evidence is what catches a login that "looks fine" but threw a JS exception —
exactly what this tool exists to find.

Deliberately built on Playwright, NOT raw CDP. The old prototypes hand-rolled
CDP (Runtime.evaluate, DOM.resolveNode) via a vendored browser_use stack;
Playwright gives the accessibility snapshot, actionability auto-wait, network
hooks and tracing for free.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import TracebackType
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Page,
    Playwright,
    Request,
    Response,
    async_playwright,
)

from core.config import Config
from core.policy import OriginPolicy


@dataclass
class ConsoleEntry:
    type: str
    text: str


@dataclass
class NetworkFailure:
    method: str
    url: str
    status: int | None      # None = request failed outright (DNS, connection, abort)
    failure: str | None = None
    third_party: bool = False  # host differs from the document — ambient (analytics, CDN, favicon)


def _registrable(host: str) -> str:
    """Naive eTLD+1: last two labels, but whole host for IPs / single-label hosts."""
    labels = host.split(".")
    if len(labels) < 2 or all(l.isdigit() for l in labels):  # IPv4 or bare host
        return host
    return ".".join(labels[-2:])


def _third_party(url: str, doc_url: str) -> bool:
    """True when `url` is not same-site as the current document (can't tell → False)."""
    h, d = urlparse(url).hostname or "", urlparse(doc_url).hostname or ""
    return bool(h and d and _registrable(h) != _registrable(d))


@dataclass
class EvidenceBuffers:
    """Accumulates console + network signal for the current page.

    The agent drains these per stage via the `report_stage` tool
    (harness/agent.py) to attach evidence to the report.
    """

    console: list[ConsoleEntry] = field(default_factory=list)
    network_failures: list[NetworkFailure] = field(default_factory=list)

    def peek(self) -> "EvidenceBuffers":
        """A copy of the current buffers, left intact (non-destructive read).

        Used to fold evidence into the page snapshot the model reads after each
        action, without stealing it from report_stage's per-stage drain."""
        return EvidenceBuffers(console=list(self.console),
                               network_failures=list(self.network_failures))

    def drain(self) -> "EvidenceBuffers":
        """Return a copy of the current buffers and clear them."""
        snapshot = self.peek()
        self.console.clear()
        self.network_failures.clear()
        return snapshot


class BrowserSession:
    """Async context manager owning one Playwright + browser + page lifecycle."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.from_env()
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        self.evidence = EvidenceBuffers()
        # Serializes page-touching tool calls. When the model emits several tool
        # calls in one turn, the in-process MCP server may run them concurrently —
        # but Playwright can't drive one page from parallel coroutines. This lock
        # makes a batch run one-at-a-time, and because asyncio.Lock is FIFO the
        # calls run in the order the model listed them (fill a, fill b, submit).
        self.action_lock = asyncio.Lock()
        self.origin_policy = OriginPolicy(getattr(self.config, "allowed_origins", []))

    async def start(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo_ms,
        )
        # Reuse a saved session (cookies + storage) when one exists, so a login
        # from a previous run carries over and the agent skips the login screen.
        ss = self.config.storage_state
        state = str(ss) if ss and ss.exists() else None
        self._context = await self._browser.new_context(
            storage_state=state,
            viewport={"width": 1920, "height": 1080},
        )
        self.page = await self._context.new_page()
        self._setup_page(self.page)
        self._context.on("page", self._adopt_page)  # follow popups / new tabs
        return self

    def _setup_page(self, page: Page) -> None:
        page.set_default_timeout(self.config.action_timeout_ms)
        page.set_default_navigation_timeout(self.config.nav_timeout_ms)
        self._wire_evidence(page)
        page.on("close", self._on_page_close)
        page.on("framenavigated", lambda frame: self._on_frame_navigated(page, frame))

    def _on_frame_navigated(self, page: Page, frame) -> None:
        """Enforce origin policy for top-level redirects and popup navigations."""
        if frame is not page.main_frame:
            return
        url = page.url
        if url in ("", "about:blank"):
            return
        allowed, reason = self.origin_policy.check_navigation(url)
        if allowed:
            return
        self.evidence.console.append(
            ConsoleEntry(type="error", text=f"[BLOCKED] {reason}")
        )
        # Navigation callbacks are synchronous; schedule cleanup without
        # blocking Playwright's event dispatcher.
        if not page.is_closed():
            if page is self.page:
                asyncio.create_task(page.goto("about:blank"))
            else:
                asyncio.create_task(page.close())

    def _adopt_page(self, page: Page) -> None:
        """A popup/new tab becomes the active page, with evidence wired."""
        self._setup_page(page)
        self.page = page

    def _on_page_close(self, closed: Page) -> None:
        """If the active page closes (popup dismissed), fall back to the last open one."""
        if self.page is closed and self._context is not None:
            remaining = [p for p in self._context.pages if not p.is_closed()]
            if remaining:
                self.page = remaining[-1]

    def _wire_evidence(self, page: Page) -> None:
        def on_console(msg: ConsoleMessage) -> None:
            if msg.type in ("error", "warning"):
                self.evidence.console.append(ConsoleEntry(type=msg.type, text=msg.text))

        def on_pageerror(exc: Exception) -> None:
            self.evidence.console.append(ConsoleEntry(type="error", text=str(exc)))

        def on_response(resp: Response) -> None:
            if resp.status >= 400:
                self.evidence.network_failures.append(
                    NetworkFailure(method=resp.request.method, url=resp.url, status=resp.status,
                                   third_party=_third_party(resp.url, page.url))
                )

        def on_requestfailed(req: Request) -> None:
            failure = req.failure or "request failed"
            if "ERR_ABORTED" in failure:
                return  # cancelled by navigation/unload — normal, not app failure
            self.evidence.network_failures.append(
                NetworkFailure(method=req.method, url=req.url, status=None, failure=failure,
                               third_party=_third_party(req.url, page.url))
            )

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)
        page.on("requestfailed", on_requestfailed)

    async def goto(self, url: str) -> None:
        assert self.page is not None, "session not started"
        await self.page.goto(url)

    async def close(self) -> None:
        # Persist the session before teardown so this run's login is remembered.
        ss = self.config.storage_state
        if ss and self._context is not None:
            try:
                ss.parent.mkdir(parents=True, exist_ok=True)
                await self._context.storage_state(path=str(ss))
            except Exception:  # noqa: BLE001 — a lost session never fails teardown
                pass
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._context = self.page = None

    async def __aenter__(self) -> "BrowserSession":
        return await self.start()

    async def __aexit__(self, exc_type: type[BaseException] | None,
                        exc: BaseException | None, tb: TracebackType | None) -> None:
        await self.close()
