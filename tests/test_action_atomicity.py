"""Acceptance tests for Task 05: action/observation atomicity.

No LLM, no browser, no network. Covers every acceptance criterion from
OPEN_SOURCE_REQUIREMENTS.md §05:

  - Concurrent fake tool calls cannot interleave another action between an
    action and its capture
  - Capture failures release the lock (subsequent actions succeed)
  - Each result identifies its own action and observation

All browser calls and page rendering are replaced with fakes. The
action_lock from the real BrowserSession is used unmodified so the locking
behaviour is identical to production.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.browser import EvidenceBuffers
from core.schema import ActionRecord


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self) -> None:
        self.action_lock = asyncio.Lock()
        self.evidence = EvidenceBuffers()
        self.config = SimpleNamespace(show_evidence=False)
        self.page = SimpleNamespace()  # page is never directly accessed in act()


@dataclass
class _FakeResult:
    detail: str
    ok: bool = True


def _fake_actor_factory(page_state: list[str]):
    """Return a fake Actor class whose click() mutates page_state[0]."""

    class FakeActor:
        def __init__(self, session) -> None:
            pass

        async def click(self, index: int) -> _FakeResult:
            page_state[0] = f"after-click-{index}"
            await asyncio.sleep(0)  # yield — the lock must prevent interleaving
            return _FakeResult(detail=f"click [{index}]")

        async def fill(self, index: int, text: str) -> _FakeResult:
            page_state[0] = f"after-fill-{index}"
            return _FakeResult(detail=f"fill [{index}]")

        async def press(self, key: str) -> _FakeResult:
            page_state[0] = f"after-press-{key}"
            return _FakeResult(detail=f"press {key}")

    return FakeActor


# ---------------------------------------------------------------------------
# Acceptance: concurrent actions cannot interleave action and capture
# ---------------------------------------------------------------------------

async def test_concurrent_actions_do_not_interleave(tmp_path: Path) -> None:
    """Each captured observation must reflect that action's page state, not another's."""
    session = _FakeSession()
    records: list[ActionRecord] = []
    page_state = ["initial"]

    async def fake_render(s):
        # Returns whatever state the page is in RIGHT NOW (inside the lock).
        return page_state[0]

    async def fake_save(s, p):
        return None

    FakeActor = _fake_actor_factory(page_state)

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", FakeActor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path)}

        result1, result2 = await asyncio.gather(
            tools["click"].handler({"index": 1}),
            tools["click"].handler({"index": 2}),
        )

    text1 = result1["content"][0]["text"]
    text2 = result2["content"][0]["text"]

    # Each result must start with the detail from its own action.
    assert "click [1]" in text1
    assert "click [2]" in text2

    # The page state captured in each result must match that action's mutation —
    # not a later action's state.  This fails if action and capture are not atomic.
    assert "after-click-1" in text1, (
        f"click [1] captured wrong page state; got: {text1!r}"
    )
    assert "after-click-2" in text2, (
        f"click [2] captured wrong page state; got: {text2!r}"
    )


# ---------------------------------------------------------------------------
# Acceptance: capture failure releases the lock
# ---------------------------------------------------------------------------

async def test_capture_failure_releases_lock(tmp_path: Path) -> None:
    """A _render exception must not leave the lock permanently acquired."""
    session = _FakeSession()
    records: list[ActionRecord] = []
    render_call = [0]

    async def fake_render_fails_once(s):
        render_call[0] += 1
        if render_call[0] == 1:
            raise RuntimeError("render pipeline failed")
        return "recovered-page"

    async def fake_save(s, p):
        return None

    class FakeActor:
        def __init__(self, s) -> None:
            pass
        async def click(self, index: int) -> _FakeResult:
            return _FakeResult(detail=f"click [{index}]")

    with (patch("harness.tools._render", fake_render_fails_once),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", FakeActor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path)}

        # First call: _render raises — result must say observation unavailable.
        result1 = await tools["click"].handler({"index": 1})
        # Second call: must succeed (the lock was released despite the failure).
        result2 = await tools["click"].handler({"index": 2})

    text1 = result1["content"][0]["text"]
    text2 = result2["content"][0]["text"]

    assert "observation unavailable" in text1, f"expected failure message; got {text1!r}"
    assert "recovered-page" in text2, f"expected page after recovery; got {text2!r}"
    # The lock is not held — a concurrent acquire completes immediately.
    assert not session.action_lock.locked()

    # Both actions produced records, even the one with a failed render.
    assert len(records) == 2
    assert records[0].detail == "click [1]"
    assert records[1].detail == "click [2]"
    # Failed render means screenshot=None (no screenshot saved).
    assert records[0].screenshot is None  # fake_save returns None always here


# ---------------------------------------------------------------------------
# Acceptance: each result identifies its own action and observation
# ---------------------------------------------------------------------------

async def test_each_result_identifies_own_action_and_observation(tmp_path: Path) -> None:
    """Three sequential actions produce three distinct ActionRecords with matching IDs."""
    session = _FakeSession()
    records: list[ActionRecord] = []

    render_seq = [f"page-{i}" for i in range(10)]
    render_call = [0]

    async def fake_render(s):
        idx = render_call[0]
        render_call[0] += 1
        return render_seq[idx]

    async def fake_save(s, p):
        return p  # simulate a successful screenshot

    class FakeActor:
        def __init__(self, s) -> None:
            pass
        async def click(self, index: int) -> _FakeResult:
            return _FakeResult(detail=f"click [{index}]")
        async def fill(self, index: int, text: str) -> _FakeResult:
            return _FakeResult(detail=f"fill [{index}]")
        async def press(self, key: str) -> _FakeResult:
            return _FakeResult(detail=f"press {key}")

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", FakeActor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path)}

        r1 = await tools["click"].handler({"index": 5})
        r2 = await tools["fill"].handler({"index": 3, "text": "hello"})
        r3 = await tools["press"].handler({"key": "Enter"})

    # Each result contains its own action detail and its own page snapshot.
    assert "click [5]"   in r1["content"][0]["text"]
    assert "page-0"      in r1["content"][0]["text"]
    assert "fill [3]"    in r2["content"][0]["text"]
    assert "page-1"      in r2["content"][0]["text"]
    assert "press Enter" in r3["content"][0]["text"]
    assert "page-2"      in r3["content"][0]["text"]

    # Step files are written with monotonically increasing IDs.
    assert (tmp_path / "step-01.txt").read_text(encoding="utf-8") == "page-0"
    assert (tmp_path / "step-02.txt").read_text(encoding="utf-8") == "page-1"
    assert (tmp_path / "step-03.txt").read_text(encoding="utf-8") == "page-2"

    # ActionRecords bundle detail + screenshot together — no parallel-list pairing.
    assert len(records) == 3
    assert records[0].action_id == 1
    assert records[0].detail == "click [5]"
    assert records[0].screenshot == tmp_path / "step-01.png"
    assert records[1].action_id == 2
    assert records[1].detail == "fill [3]"
    assert records[1].screenshot == tmp_path / "step-02.png"
    assert records[2].action_id == 3
    assert records[2].detail == "press Enter"
    assert records[2].screenshot == tmp_path / "step-03.png"


# ---------------------------------------------------------------------------
# Action ID is monotonically increasing across tool types
# ---------------------------------------------------------------------------

async def test_action_id_increases_across_tools(tmp_path: Path) -> None:
    """IDs increment even when different tool types are called."""
    session = _FakeSession()
    records: list[ActionRecord] = []

    async def fake_render(s):
        return "page"

    async def fake_save(s, p):
        return None

    class FakeActor:
        def __init__(self, s) -> None:
            pass
        async def click(self, index: int) -> _FakeResult:
            return _FakeResult(detail=f"click [{index}]")
        async def go_back(self) -> _FakeResult:
            return _FakeResult(detail="go_back")

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", FakeActor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path)}

        await tools["click"].handler({"index": 1})
        await tools["go_back"].handler({})
        await tools["click"].handler({"index": 2})

    assert (tmp_path / "step-01.txt").exists()
    assert (tmp_path / "step-02.txt").exists()
    assert (tmp_path / "step-03.txt").exists()
    assert not (tmp_path / "step-04.txt").exists()
    assert [r.action_id for r in records] == [1, 2, 3]


# ---------------------------------------------------------------------------
# R8: action raises → record still appended with ok=False
# ---------------------------------------------------------------------------

async def test_action_exception_still_appends_record(tmp_path: Path) -> None:
    """A Playwright-level exception in the action must not lose the record."""
    session = _FakeSession()
    records: list[ActionRecord] = []

    async def fake_render(s):
        return "page-after-failure"

    async def fake_save(s, p):
        return None

    class FakeActor:
        def __init__(self, s) -> None:
            pass
        async def click(self, index: int) -> _FakeResult:
            raise RuntimeError(f"element {index} not found")

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", FakeActor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path)}

        result = await tools["click"].handler({"index": 7})

    # Record must exist despite the exception
    assert len(records) == 1
    assert records[0].action_id == 1
    assert records[0].ok is False
    assert "failed" in records[0].detail
    # The lock must be released
    assert not session.action_lock.locked()
    # Result text describes the failure
    text = result["content"][0]["text"]
    assert "failed" in text


async def test_failed_action_ok_false_successful_action_ok_true(tmp_path: Path) -> None:
    """ok=False for failed actions; ok=True for successful ones."""
    session = _FakeSession()
    records: list[ActionRecord] = []
    call = [0]

    async def fake_render(s):
        return "page"

    async def fake_save(s, p):
        return None

    class FakeActor:
        def __init__(self, s) -> None:
            pass
        async def click(self, index: int) -> _FakeResult:
            call[0] += 1
            if call[0] == 1:
                raise RuntimeError("timeout")
            return _FakeResult(detail=f"click [{index}]", ok=True)

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", FakeActor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path)}

        await tools["click"].handler({"index": 1})  # fails
        await tools["click"].handler({"index": 2})  # succeeds

    assert len(records) == 2
    assert records[0].ok is False
    assert records[1].ok is True
    assert records[1].detail == "click [2]"


async def test_repeated_identical_actions_have_distinct_ids(tmp_path: Path) -> None:
    """Two clicks on the same element must have different action IDs."""
    session = _FakeSession()
    records: list[ActionRecord] = []

    async def fake_render(s):
        return "page"

    async def fake_save(s, p):
        return None

    class FakeActor:
        def __init__(self, s) -> None:
            pass
        async def click(self, index: int) -> _FakeResult:
            return _FakeResult(detail=f"click [{index}]")

    with (patch("harness.tools._render", fake_render),
          patch("harness.tools.save_screenshot", fake_save),
          patch("harness.tools.Actor", FakeActor)):
        from harness.tools import browser_tools
        tools = {t.name.split("__")[-1]: t
                 for t in browser_tools(session, records, tmp_path)}

        r1 = await tools["click"].handler({"index": 5})
        r2 = await tools["click"].handler({"index": 5})

    assert records[0].action_id != records[1].action_id
    assert "step-01" in r1["content"][0]["text"]
    assert "step-02" in r2["content"][0]["text"]
