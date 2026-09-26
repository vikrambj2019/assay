"""Browser action tools as an in-process MCP server (server name: "bta").

Each tool is a one-liner over `core.actions.Actor`. The full transaction —
action, settlement, snapshot, and screenshot — runs under a single
`session.action_lock` acquisition inside `act()`, so the observation returned
to the model always corresponds exactly to the action that triggered it.
Concurrent tool calls in the same turn are serialized by the lock and each
receives its own capture. A monotonically increasing action ID is assigned
before execution and labels the step files on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from claude_agent_sdk import tool

from core.actions import Actor
from core.budget import BudgetExhausted, RunBudget
from core.policy import OriginPolicy
from core.redact import Redactor
from core.schema import ActionRecord
from core.settle import settle
from core.snapshot import capture, render as render_snap

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from claude_agent_sdk import SdkMcpTool

    from core.actions import ActionResult
    from core.browser import BrowserSession

_JTYPE = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema(props: dict, required: list[str]) -> dict:
    """A strict tool schema: only these params are allowed (a misspelled or
    invented one — timeout_ms, amount, instructions — errors clearly instead of
    being silently dropped) and `required` must be present. Each prop value is a
    Python type, or a full property dict for enums/descriptions."""
    properties = {n: (v if isinstance(v, dict) else {"type": _JTYPE[v]})
                  for n, v in props.items()}
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": required}


async def _render(session: "BrowserSession") -> str:
    """Settle the page then capture + render it.

    When config.show_evidence is on, folds in any console/network errors seen
    since the last drain so the model can react to broken actions; off = DOM only.
    """
    await settle(session.page)
    evidence = session.evidence if session.config.show_evidence else None
    return render_snap(await capture(session.page, evidence))


async def save_screenshot(session: "BrowserSession", path: Path) -> "Path | None":
    """Take a screenshot; return None on failure (a lost screenshot never fails the step)."""
    try:
        await session.page.screenshot(path=str(path))  # type: ignore[union-attr]
        return path
    except Exception:  # noqa: BLE001
        return None


def browser_tools(session: "BrowserSession", records: "list[ActionRecord]",
                  out_dir: Path, redactor: "Redactor | None" = None,
                  origin_policy: "OriginPolicy | None" = None,
                  budget: "RunBudget | None" = None) -> list["SdkMcpTool"]:
    """The browser tools, closing over the session, action-record list, and output dir."""
    actor = Actor(session)
    _counter = [0]  # monotonically increasing action ID, assigned before execution

    async def act(result_coro: "Awaitable[ActionResult]") -> dict:
        """Execute an action + observation as one serialized transaction.

        The action ID is assigned before execution begins. The lock is acquired
        once for the complete action → settle → snapshot → screenshot sequence
        and is always released on exit, including when _render raises.

        An ActionRecord is appended unconditionally — even when the action
        itself raises (ok=False) — so no attempted action is silently lost and
        adjacent captions are never shifted by a missing entry.
        """
        _counter[0] += 1
        action_id = _counter[0]
        if budget is not None:
            # Count the attempt before touching the browser, including actions
            # that subsequently fail in Playwright.
            budget.record_action()
        shot: "Path | None" = None
        page_text = ""
        detail = f"action-{action_id}"
        action_ok = True

        async with session.action_lock:
            try:
                action_result = await result_coro
                action_ok = action_result.ok
                detail = action_result.detail
            except Exception as exc:  # noqa: BLE001
                action_ok = False
                detail = f"action-{action_id} [failed: {exc}]"
            if redactor is not None:
                detail = redactor.scrub(detail)
            try:
                page_text = await _render(session)
                if redactor is not None:
                    page_text = redactor.scrub(page_text)
            except Exception as exc:  # noqa: BLE001
                page_text = f"[observation unavailable: {exc}]"
            shot = await save_screenshot(session, out_dir / f"step-{action_id:02d}.png")
            try:
                (out_dir / f"step-{action_id:02d}.txt").write_text(
                    page_text, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 — preserve the action record
                page_text = f"{page_text}\n[observation artifact unavailable: {exc}]"
            print(f"  ⎿ step-{action_id:02d} loaded", flush=True)

        if budget is not None:
            budget.check()

        # Always record — ok=False means action failed, screenshot=None means capture failed.
        records.append(ActionRecord(action_id=action_id, detail=detail,
                                    ok=action_ok, screenshot=shot))
        # Include the step label so two calls with identical detail are still distinct.
        header = f"[step-{action_id:02d}] {detail}"
        text = f"{header}\n\n{page_text}" if page_text else header
        return {"content": [{"type": "text", "text": text}]}

    @tool("open_url", "Go to a URL — load a page directly by its address, to start "
          "the task or jump to a known page.", _schema({"url": str}, ["url"]))
    async def open_url(a: dict) -> dict:
        url = a["url"]
        if origin_policy is not None:
            allowed, reason = origin_policy.check_navigation(url)
            if not allowed:
                _counter[0] += 1
                action_id = _counter[0]
                if budget is not None:
                    budget.record_action()
                block_msg = f"[BLOCKED] open_url({url!r}) denied: {reason}"
                if redactor is not None:
                    block_msg = redactor.scrub(block_msg)
                records.append(ActionRecord(action_id=action_id, detail=block_msg, ok=False))
                return {"content": [{"type": "text", "text": block_msg}]}
        return await act(actor.goto(url))

    @tool("click", "Click element [index] — a button, link, tab, checkbox, or a "
          "custom dropdown to open it.", _schema({"index": int}, ["index"]))
    async def click(a: dict) -> dict:
        return await act(actor.click(a["index"]))

    @tool("fill", "Type into text field [index] (an input or textarea), replacing "
          "any current value.", _schema({"index": int, "text": str}, ["index", "text"]))
    async def fill(a: dict) -> dict:
        return await act(actor.fill(a["index"], a["text"]))

    @tool("press", "Press one key on the focused element — e.g. Enter to submit, Tab "
          "to move focus, Escape to dismiss (or a combo like Control+A).",
          _schema({"key": str}, ["key"]))
    async def press(a: dict) -> dict:
        return await act(actor.press(a["key"]))

    @tool("select_option", "Pick in a select field: select the <option> at [index] "
          "directly. Picks that option whether it belongs to a native <select> or "
          "a custom dropdown list.", _schema({"index": int}, ["index"]))
    async def select_option(a: dict) -> dict:
        return await act(actor.select_option(a["index"]))

    @tool("upload", "Attach a local file (absolute path) to file-input [index] — for "
          "an upload/attach field, no OS picker needed.",
          _schema({"index": int, "path": str}, ["index", "path"]))
    async def upload(a: dict) -> dict:
        return await act(actor.upload(a["index"], a["path"]))

    @tool("wait_for",
          "Wait in-run for async work (durations in SECONDS). Pick 'until': "
          "change = watched text changes (best for a reply replacing 'thinking…'); "
          "text = a string appears on the page; visible/hidden/enabled = element "
          "[index] reaches that state; idle = page settles, returns as soon as "
          "quiet; time = plain pause. E.g. wait_for(until=enabled, index=10, seconds=240).",
          _schema({
              "until": {"type": "string",
                        "enum": ["change", "text", "time", "visible", "hidden",
                                 "enabled", "idle"]},
              "index": {"type": "integer",
                        "description": "element to watch — required for "
                                       "visible/hidden/enabled, optional for change"},
              "text": {"type": "string", "description": "the string to wait for — "
                                                        "required when until=text"},
              "seconds": {"type": "integer",
                          "description": "seconds to wait, default 30, max 300"},
          }, ["until"]))
    async def wait_for(a: dict) -> dict:
        idx = a.get("index", -1)
        return await act(actor.wait_for(a["until"], index=None if idx < 0 else idx,
                                        text=a.get("text", ""), timeout=a.get("seconds", 30)))

    @tool("scroll", "Reveal off-screen content. No index: scroll the whole page. "
          "With index: down/up/top/bottom scroll inside that [scroll] container; "
          "into_view brings any element onto the screen.",
          _schema({
              "mode": {"type": "string",
                       "enum": ["down", "up", "top", "bottom", "into_view"]},
              "index": {"type": "integer",
                        "description": "the element to scroll into view, or the "
                                       "[scroll] container to scroll inside; "
                                       "omit to scroll the page"},
          }, ["mode"]))
    async def scroll(a: dict) -> dict:
        idx = a.get("index", -1)
        return await act(actor.scroll(a["mode"], index=None if idx < 0 else idx))

    @tool("hover", "Hover element [index] — to reveal a menu or tooltip that only "
          "shows on hover.", _schema({"index": int}, ["index"]))
    async def hover(a: dict) -> dict:
        return await act(actor.hover(a["index"]))

    @tool("go_back", "Go back to the previous page in history.", _schema({}, []))
    async def go_back(a: dict) -> dict:
        return await act(actor.go_back())

    return [open_url, click, fill, press, select_option, upload,
            wait_for, scroll, hover, go_back]
