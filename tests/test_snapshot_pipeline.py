"""Capture → render pipeline tests against local HTML (no network, no LLM).

Regression guards for the two ways snapshot text has gone wrong: visible text
silently dropped (deduped against a name that was never printed), and text
printed twice (an element's accessible name restated by its descendants).
Every visible string must appear exactly once.
"""

from __future__ import annotations

import pytest_asyncio

from core.browser import BrowserSession
from core.config import Config
from core.snapshot import capture, render

PAGE = """
<header>
  <span>Airestacks v</span><span>1.0.1</span>
  <button><span>New Stack</span></button>
  <button><span>Active</span><span>2</span></button>
</header>
<p aria-label="Seller signature missing">Seller signature missing</p>
<p title="doc@example.com"
   style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:120px">doc@example.com</p>
"""


@pytest_asyncio.fixture
async def session():
    async with BrowserSession(Config(headless=True)) as s:
        yield s


async def test_every_visible_string_renders_exactly_once(session):
    await session.page.set_content(PAGE)
    out = render(await capture(session.page))

    # Text whose aria-label/title equals it must not be deduped into nothing.
    assert out.count("Seller signature missing") == 1
    assert out.count("doc@example.com") == 1
    # A button's name is not restated as child text (even via nested spans).
    assert out.count("New Stack") == 1
    assert out.count("Active") == 1  # name reads "Active2"; span pieces suppressed
    # <header> maps to banner, not heading — it must not take its whole subtree
    # text as its name (which would restate everything under it).
    assert out.count("Airestacks v") == 1
    assert "<banner> Airestacks" not in out
