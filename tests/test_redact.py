"""Tests for secret redaction (Task 07).

Unit tests: no browser, no LLM.
Browser integration tests: require Playwright (Chromium).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from core.redact import Redactor, make_redactor


# ── pure unit tests ───────────────────────────────────────────────────────

def test_known_secret_is_replaced():
    r = Redactor(["s3cr3t"])
    assert r.scrub("my s3cr3t password") == "my [REDACTED] password"


def test_scrub_is_a_no_op_when_no_secrets_match():
    r = Redactor(["s3cr3t"])
    assert r.scrub("nothing sensitive here") == "nothing sensitive here"


def test_empty_secret_is_not_registered():
    r = Redactor(["", "  ", ""])
    assert r._secrets == []
    assert r.scrub("anything") == "anything"


def test_multiple_secrets_all_replaced():
    r = Redactor(["abc", "xyz"])
    assert r.scrub("abc xyz abc") == "[REDACTED] [REDACTED] [REDACTED]"


def test_duplicate_secrets_deduplicated():
    r = Redactor(["same", "same"])
    assert len(r._secrets) == 1


def test_longer_secret_wins_over_prefix():
    """'abcdef' must not be broken into 'abc'+'def' when both are registered."""
    r = Redactor(["abc", "abcdef"])
    assert r.scrub("abcdef") == "[REDACTED]"


def test_make_redactor_reads_env(monkeypatch):
    monkeypatch.setenv("BTA_TEST_PASSWORD", "hunter2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fixture")
    r = make_redactor()
    assert r.scrub("password=hunter2 key=sk-ant-fixture") == \
           "password=[REDACTED] key=[REDACTED]"


def test_make_redactor_skips_unset_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "BTA_TEST_PASSWORD", "BTA_TEST_USERNAME"):
        monkeypatch.delenv(var, raising=False)
    r = make_redactor()
    assert r.scrub("nothing to redact") == "nothing to redact"


# ── browser integration tests ─────────────────────────────────────────────

_FORM = """
<form>
  <label for="u">Username</label><input id="u" type="text">
  <label for="p">Password</label><input id="p" type="password">
</form>
"""


@pytest_asyncio.fixture
async def session():
    from core.browser import BrowserSession
    from core.config import Config
    async with BrowserSession(Config(headless=True)) as s:
        yield s


async def test_fill_password_field_masks_detail(session):
    """Actor.fill on a password input must produce '[REDACTED]' in the detail."""
    from core.actions import Actor
    from core.snapshot import capture
    await session.page.set_content(_FORM)
    snap = await capture(session.page)
    pw_idx = next(n.index for n in snap._iter()
                  if n.role == "textbox" and n.name == "Password")
    actor = Actor(session)
    result = await actor.fill(pw_idx, "s3cr3t_pw")
    assert result.ok
    assert "s3cr3t_pw" not in result.detail
    assert "[REDACTED]" in result.detail


async def test_fill_text_field_shows_preview(session):
    """Actor.fill on a plain text input shows the typed text in the detail."""
    from core.actions import Actor
    from core.snapshot import capture
    await session.page.set_content(_FORM)
    snap = await capture(session.page)
    txt_idx = next(n.index for n in snap._iter()
                   if n.role == "textbox" and n.name == "Username")
    actor = Actor(session)
    result = await actor.fill(txt_idx, "myuser")
    assert result.ok
    assert "myuser" in result.detail


async def test_browser_tools_redactor_scrubs_step_files(session, tmp_path):
    """browser_tools with a Redactor must not write the secret to step .txt files."""
    from core.schema import ActionRecord
    from harness.tools import browser_tools

    secret = "SuperSecret99"
    redactor = Redactor([secret])
    records: list[ActionRecord] = []
    tools = {t.name: t for t in browser_tools(session, records, tmp_path, redactor)}

    # open_url loads a page whose body text contains the secret
    await tools["open_url"].handler({"url": f"data:text/html,<p>{secret}</p>"})

    step_files = list(tmp_path.glob("step-*.txt"))
    assert step_files, "no step file was written"
    content = step_files[0].read_text()
    assert secret not in content, "raw secret found in step file"
    assert "[REDACTED]" in content


async def test_browser_tools_redactor_scrubs_mcp_result(session, tmp_path):
    """The MCP tool result returned to the model must not contain the raw secret."""
    from core.schema import ActionRecord
    from harness.tools import browser_tools

    secret = "MySensitiveToken"
    redactor = Redactor([secret])
    records: list[ActionRecord] = []
    tools = {t.name: t for t in browser_tools(session, records, tmp_path, redactor)}

    result = await tools["open_url"].handler({"url": f"data:text/html,<p>{secret}</p>"})
    text = result["content"][0]["text"]
    assert secret not in text, "raw secret found in MCP result"
    assert "[REDACTED]" in text


async def test_action_record_detail_scrubbed_for_password_field(session, tmp_path):
    """ActionRecord.detail must not contain the typed password text."""
    from core.schema import ActionRecord
    from core.snapshot import capture
    from harness.tools import browser_tools

    records: list[ActionRecord] = []
    tools = {t.name: t for t in browser_tools(session, records, tmp_path)}

    await session.page.set_content(_FORM)
    snap = await capture(session.page)
    pw_idx = next(n.index for n in snap._iter()
                  if n.role == "textbox" and n.name == "Password")

    await tools["fill"].handler({"index": pw_idx, "text": "secret_val"})
    assert records, "no ActionRecord was appended"
    assert "secret_val" not in records[-1].detail
    assert "[REDACTED]" in records[-1].detail
