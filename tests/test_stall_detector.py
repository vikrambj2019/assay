"""Tests for the structured stall detector (harness/tools.py).

No LLM, no browser: a fake Actor whose actions always raise, so every action
fails and the detector's consecutive-failure counter can be driven directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.browser import EvidenceBuffers
from core.schema import ActionRecord
from harness.tools import STALL_THRESHOLD, AgentStalledError


class _FakeSession:
    def __init__(self) -> None:
        self.action_lock = asyncio.Lock()
        self.evidence = EvidenceBuffers()
        self.config = SimpleNamespace(show_evidence=False)
        self.page = SimpleNamespace()


class _FailingActor:
    def __init__(self, session) -> None:
        pass

    async def click(self, index: int):
        raise RuntimeError(f"element {index} not found")


class _FlakyActor:
    """Fails except when told to succeed — for counter-reset tests."""

    def __init__(self, session) -> None:
        self.fail = True

    async def click(self, index: int):
        if self.fail:
            raise RuntimeError("boom")
        from types import SimpleNamespace as NS
        return NS(ok=True, detail=f"click [{index}]")


def _make_tools(session, records, tmp_path, actor_cls, stall_state):
    async def fake_render(s):
        return "page"

    async def fake_save(s, p):
        return None

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", actor_cls)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path,
                                        stall_state=stall_state)}
    return tools


@pytest.mark.asyncio
async def test_no_stall_below_threshold(tmp_path: Path):
    session = _FakeSession()
    records: list[ActionRecord] = []
    stall: dict = {}
    tools = _make_tools(session, records, tmp_path, _FailingActor, stall)

    for _ in range(STALL_THRESHOLD - 1):
        result = await tools["click"].handler({"index": 1})
        assert "[STALLED]" not in result["content"][0]["text"]

    assert not stall.get("stalled")
    assert len(records) == STALL_THRESHOLD - 1
    assert all(r.ok is False for r in records)


@pytest.mark.asyncio
async def test_stall_fires_at_threshold(tmp_path: Path):
    session = _FakeSession()
    records: list[ActionRecord] = []
    stall: dict = {}
    tools = _make_tools(session, records, tmp_path, _FailingActor, stall)

    last_text = ""
    for _ in range(STALL_THRESHOLD):
        result = await tools["click"].handler({"index": 1})
        last_text = result["content"][0]["text"]

    assert stall.get("stalled") is True
    assert stall["consecutive_failures"] == STALL_THRESHOLD
    assert "[STALLED]" in last_text
    assert "complete_goal with UNVERIFIED" in last_text
    # The record for the stalling action still exists (ok=False).
    assert len(records) == STALL_THRESHOLD
    assert records[-1].ok is False


@pytest.mark.asyncio
async def test_success_resets_consecutive_counter(tmp_path: Path):
    session = _FakeSession()
    records: list[ActionRecord] = []
    stall: dict = {}
    actor = _FlakyActor(session)

    async def fake_render(s):
        return "page"

    async def fake_save(s, p):
        return None

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", lambda s: actor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path,
                                        stall_state=stall)}

        for _ in range(STALL_THRESHOLD - 1):
            await tools["click"].handler({"index": 1})
        actor.fail = False  # one success resets the counter
        await tools["click"].handler({"index": 1})
        actor.fail = True
        for _ in range(STALL_THRESHOLD - 1):
            await tools["click"].handler({"index": 1})

    assert not stall.get("stalled")


def test_agent_stalled_error_is_exception():
    assert issubclass(AgentStalledError, Exception)
