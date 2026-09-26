"""Actions + capture integration tests, against local HTML (no network, no LLM)."""

from __future__ import annotations

import time

import pytest_asyncio

from core.actions import Actor
from core.browser import BrowserSession
from core.config import Config
from core.snapshot import capture

FORM = """
<form>
  <label for="u">Username</label><input id="u" type="text">
  <label for="p">Password</label><input id="p" type="password">
  <select id="s"><option value="a">Apple</option><option value="b">Banana</option></select>
  <button type="button" id="go"
    onclick="document.getElementById('out').textContent='hello ' + document.getElementById('u').value">
    Go
  </button>
  <div id="out"></div>
</form>
"""


@pytest_asyncio.fixture
async def session():
    async with BrowserSession(Config(headless=True)) as s:
        yield s


def _index(snap, role, name=None):
    for n in snap._iter():
        if n.role == role and (name is None or (n.name or "") == name):
            return n.index
    raise AssertionError(f"no {role} named {name!r} in snapshot")


async def test_fill_and_click(session):
    await session.page.set_content(FORM)
    snap = await capture(session.page)
    actor = Actor(session)

    assert (await actor.fill(_index(snap, "textbox", "Username"), "student")).ok
    assert (await actor.click(_index(snap, "button", "Go"))).ok
    assert await session.page.inner_text("#out") == "hello student"


async def test_select_option_on_native_select(session):
    await session.page.set_content(FORM)
    snap = await capture(session.page)
    actor = Actor(session)

    banana = next(n.index for n in snap._iter() if n.role == "option" and n.name == "Banana")
    assert (await actor.select_option(banana)).ok
    assert await session.page.eval_on_selector("#s", "e => e.value") == "b"


async def test_password_value_is_redacted_in_snapshot(session):
    await session.page.set_content(FORM)
    snap = await capture(session.page)
    await Actor(session).fill(_index(snap, "textbox", "Password"), "secret123")

    snap2 = await capture(session.page)
    pw = next(n for n in snap2._iter() if n.name == "Password")
    assert "secret123" not in (pw.value or "")
    assert pw.value == "••• (redacted)"


async def test_stale_index_fails_fast_with_actionable_message(session):
    await session.page.set_content(FORM)
    snap = await capture(session.page)
    idx = _index(snap, "button", "Go")

    await session.page.set_content("<p>a different page</p>")  # index is now gone
    t0 = time.monotonic()
    r = await Actor(session).click(idx)
    assert not r.ok
    assert "re-rendered" in r.detail  # actionable: tells the model the page changed
    assert time.monotonic() - t0 < 2  # no auto-wait stall on a dead index


async def test_browser_tool_records_action_to_trail(session, tmp_path):
    from core.schema import ActionRecord
    from harness.tools import browser_tools

    records: list[ActionRecord] = []
    tools = {t.name: t for t in browser_tools(session, records, tmp_path)}

    await session.page.goto("data:text/html,<a href='about:blank'>go</a>")
    await capture(session.page)  # tag [0] on the link
    out = await tools["click"].handler({"index": 0})

    text = out["content"][0]["text"]  # MCP tool result — step label + detail + page body
    assert "click [0]" in text           # action detail is always present
    assert text.startswith("[step-")     # step label heads the result
    assert records[-1].detail == "click [0]"  # record stores just the detail


async def test_popup_becomes_active_page_and_close_falls_back(session):
    await session.page.set_content('<a id="l" href="about:blank" target="_blank">open</a>')
    first = session.page

    async with first.context.expect_page() as popup_info:
        await first.click("#l")
    popup = await popup_info.value
    assert session.page is popup

    await popup.close()
    assert session.page is first
