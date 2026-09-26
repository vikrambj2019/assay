"""Settle detector tests, against local HTML (no network, no LLM)."""

from __future__ import annotations

import pytest_asyncio

from core.browser import BrowserSession
from core.config import Config
from core.settle import settle
from core.snapshot import capture

# Renders 5 buttons one at a time over ~500ms, like an SPA drawing progressively.
PROGRESSIVE = """
<div id="root"></div>
<script>
  let i = 0;
  const t = setInterval(() => {
    const b = document.createElement('button');
    b.textContent = 'btn' + i;
    document.getElementById('root').appendChild(b);
    if (++i >= 5) clearInterval(t);
  }, 100);
</script>
"""


@pytest_asyncio.fixture
async def session():
    async with BrowserSession(Config(headless=True)) as s:
        yield s


async def test_settle_waits_for_progressive_render(session):
    await session.page.set_content(PROGRESSIVE)
    result = await settle(session.page, dom_quiet_ms=400)

    snap = await capture(session.page)
    buttons = [n for n in snap._iter() if n.role == "button"]
    assert len(buttons) == 5           # settle waited until all 5 were drawn
    assert "dom-quiet" in result.reason
    assert result.elapsed_ms >= 500     # it did not return before the last button


async def test_settle_reports_network_idle_on_static_page(session):
    await session.page.set_content("<h1>done</h1>")
    result = await settle(session.page)
    assert "network-idle" in result.reason
