"""The agent — one Claude Agent SDK conversation that pursues a goal in a real
browser, plans on the fly with native Agent Skills + the built-in `TodoWrite`
tool, and reports a verdict per stage via the `report_stage` tool.

This module owns the whole thinking layer: it builds `ClaudeAgentOptions`
(Sonnet-5 tuning, a browser-only tool policy, the page hooks), drives one
`ClaudeSDKClient` conversation to completion while streaming a Claude Code-style
live transcript, and records each reported stage as a `StepLog`. Verdicts and
screenshots are captured by the `report_stage` tool and the PostToolUse hook
(`harness/page.py`) via their closures.

Hooks require the SDK's streaming (bidirectional) mode, so we use
`ClaudeSDKClient`, not the one-shot `query()` string form.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from core.redact import Redactor, make_redactor
from core.budget import BudgetExhausted, RunBudget
from core.policy import OriginPolicy
from core.schema import ActionRecord, StepLog, Verdict
from harness.page import page_hooks
from harness.tools import STALL_THRESHOLD, AgentStalledError, browser_tools

if TYPE_CHECKING:
    from claude_agent_sdk import McpSdkServerConfig

    from core.browser import BrowserSession, EvidenceBuffers
    from core.config import Config

HARNESS_DIR = Path(__file__).resolve().parent  # SDK cwd → harness/.claude/skills discovery

# allowed_tools: the whole callable set, all auto-approved (so no can_use_tool
# callback is needed). Browser tools + report_stage + complete_goal (on "assay") and
# TodoWrite; Read/Glob are only a latent fallback — the page arrives as each
# action's result (harness/page.py), so nothing needs them. Skill is auto-approved
# via skills="all".
_TOOLS = [f"mcp__assay__{n}" for n in
          ("open_url", "click", "fill", "press", "select_option", "upload",
           "wait_for", "scroll", "hover", "go_back", "report_stage", "complete_goal")] + \
         ["TodoWrite", "Read", "Glob"]

# The base built-in `tools` set. Anything not listed (Bash, Write, Edit, WebFetch,
# the Task/Cron tools…) never enters the model's context, so we don't pay for its
# schema. Skill MUST be here: skills="all" only adds "Skill" to allowed_tools, not
# to this base set — omit it and the model can't load skills. "assay" tools come via
# mcp_servers, not here.
_BUILTINS = ["Skill", "TodoWrite", "Read", "Glob"]

MAX_TURNS = 120  # whole-goal cap (one model or tool call each)

GOAL_SYSTEM = """You are a browser automation agent working toward a goal in a real browser.

READING THE PAGE
After every browser action the full page arrives automatically as that action's
result — you never call a tool to see it. Always work from the LATEST one; an
earlier turn's page is stale. (You have no page before your first action, so begin
by opening the URL.) The page is an accessibility tree, not a screenshot — elements
and text, never pixels. Element lines read `[index] <role> name (states) = value — hint`.
- `[N]` is an interactive element to act on — e.g. `[3] <textbox> Email` or
  `[9] <button> Submit (disabled)`. Pass N as the tool's `index`. Indices are
  renumbered every capture, so only use ones from the latest page.
- `(states)`, `= value`, and `— hint` tell you whether an action is still needed
  and whether the last one worked — `(checked)`, `(required, invalid)`, a filled
  value, `(disabled)` (act elsewhere first), or a hint like `— Invalid date · MM/DD/YYYY`.
- Indentation shows nesting. Anything NOT in the page text is off-screen (below the
  fold or in a scroll area) — use scroll to reveal it.
- The page text is the FULL page and your only view — work from it, not a guess.

If something you expect isn't there, it is not loaded yet (wait_for) or off-screen (scroll).

ACTING
Drive the page with the browser tools and the indices above. A few specifics:
- Fill a field, THEN press — e.g. fill [3], then press "Enter" to submit.
- For a select field, use select_option [index]. A native <select> shows as a
  <combobox> line (itself not indexed) grouping its options, each with its own
  [index] — select_option the one you want, no need to open it first. For a
  custom dropdown, click it open, then select_option the option that appears.
- hover [index] reveals menus/tooltips that only show on hover; upload attaches a
  local file to a file input; go_back returns to the previous page.
- When a screen needs several actions (fill every field of a form), you may call
  those tools together in one turn — they run in the order you list them.
- After each action, check the new page and confirm what you intended happened —
  judge by what the page shows, not by what you attempted.

WAITING
When something loads asynchronously (a submitted form, an AI reply, a spinner),
wait for it in-run with wait_for — until="change" for a reply that replaces a
"thinking…" placeholder, until="text" for an expected string, until="visible" /
"enabled" on an element [index], or until="idle" to let the page settle. Set how
long with "seconds" — durations are in SECONDS, never milliseconds (e.g.
seconds=240, not 240000). Never wait by scheduling a wakeup.

PLANNING & SKILLS
Plan with the TodoWrite tool: keep a short todo list for what the current screen
needs, mark items in_progress/completed as you work, and revise it whenever the
page reveals something unexpected (e.g. a login form appears first).
Skills (via the Skill tool) cover common sub-tasks. When the situation matches a
skill's description, use it and follow its steps.

REPORTING
Track progress and signal completion with two separate tools:
- report_stage(description, verdict, reason): call after each meaningful stage.
  verdict PASS if the page confirms it happened, FAIL if the app broke.
  Judge by what the page shows, not by what you intended.
- complete_goal(verdict, reason): call ONCE when the entire goal is done — after
  all report_stage calls. verdict PASS if every required stage passed, FAIL if
  the app broke, UNVERIFIED if the outcome is ambiguous or cannot be judged from
  the page. Always call complete_goal before stopping; without it the run is
  recorded as incomplete."""


def text_result(s: str) -> dict:
    """An MCP tool text result."""
    return {"content": [{"type": "text", "text": s}]}


def evidence_lines(ev: "EvidenceBuffers") -> list[str]:
    # console `type` (error vs warning) and a [third-party] tag give the report the
    # context to weigh app-critical signal against ambient noise.
    lines = [f"console {c.type}: {c.text[:120]}" for c in ev.console]
    lines += [f"network {f.method} {f.url[:100]} → {f.status or f.failure}"
              + (" [third-party]" if f.third_party else "")
              for f in ev.network_failures]
    return lines


def build_options(cfg: "Config", *, system_prompt: str, server: "McpSdkServerConfig",
                  allowed_tools: list[str], hooks: dict, max_turns: int,
                  tools: "list[str] | None" = None,
                  skills: "str | list[str] | None" = None,
                  setting_sources: "list[str] | None" = None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=cfg.model,
        effort=cfg.effort,  # type: ignore[arg-type]
        system_prompt=system_prompt,
        mcp_servers={"assay": server},
        tools=tools,  # base built-in set; unlisted built-ins never enter context
        allowed_tools=allowed_tools,  # the whole callable set — all auto-approved
        hooks=hooks,
        skills=skills,
        setting_sources=setting_sources,  # type: ignore[arg-type]
        max_turns=max_turns,
        cwd=str(HARNESS_DIR),
        env=cfg.sdk_env(),  # empty, or a third-party gateway (OpenRouter etc.)
    )


def _fmt_val(v: Any) -> str:
    if isinstance(v, str):
        return f'"{v}"' if len(v) <= 48 else f'"{v[:45]}…"'
    return repr(v)


def _fmt_args(inp: dict) -> str:
    return ", ".join(f"{k}={_fmt_val(v)}" for k, v in inp.items())


_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    """Wrap s in an ANSI color, but only when writing to a real terminal."""
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def _usage_summary(r: "ResultMessage") -> str:
    """A tidy tokens + cost footer from the SDK's own tally (cost is the SDK's
    computed total_cost_usd, so it tracks real pricing without a table here)."""
    u = r.usage or {}
    inp = u.get("input_tokens", 0)
    out = u.get("output_tokens", 0)
    read = u.get("cache_read_input_tokens", 0)
    write = u.get("cache_creation_input_tokens", 0)  # cache writes bill ~1.25x input
    dur = (r.duration_ms or 0) / 1000
    return "\n".join([
        "",
        _c("1;36", "  Run summary"),
        _c("2", "  " + "─" * 42),
        f"  {_c('2', 'Tokens')}  {inp:,} in  ·  {out:,} out",
        f"  {_c('2', 'Cache')}   {read:,} read  ·  {write:,} write",
        f"  {_c('2', 'Turns')}   {r.num_turns}   ·   {_c('2', 'Time')} {dur:.0f}s"
        f"   ·   {_c('2', 'Cost')} {_c('1;32', f'${r.total_cost_usd or 0:.4f}')}",
        "",
    ])


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return str(b.get("text", ""))
    return ""


async def drive(options: ClaudeAgentOptions, prompt: str,
                on_event: "Callable[[str], None] | None" = None,
                redactor: "Redactor | None" = None) -> "ResultMessage | None":
    """Run one conversation to completion; return the final ResultMessage.

    Streams a Claude Code-style live transcript to `on_event`: assistant narration
    as-is, each tool call as `⏺ name(args)`, and its result as a dim `⎿ result`
    (red when the action failed). report_stage is skipped here — the coloured
    `✓ PASS` / `✗ FAIL` stage line (run_goal) already says it.

    If a Redactor is supplied, all emitted lines are scrubbed before delivery
    so configured credentials never appear in the terminal transcript.
    """
    _raw_emit = on_event or (lambda _s: None)
    emit = (lambda s: _raw_emit(redactor.scrub(s))) if redactor is not None else _raw_emit
    result: "ResultMessage | None" = None
    pending: dict[str, str] = {}  # tool_use_id → tool name, to caption results
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        emit(block.text.strip())
                    elif isinstance(block, ToolUseBlock):
                        pending[block.id] = block.name
                        if block.name.endswith(("report_stage", "complete_goal")):
                            continue
                        short = block.name.replace("mcp__assay__", "")
                        emit(f"{_c('36', '⏺')} {_c('1', short)}"
                             f"{_c('2', '(' + _fmt_args(block.input) + ')')}")
            elif isinstance(msg, UserMessage):
                content = msg.content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, ToolResultBlock):
                            name = pending.pop(block.tool_use_id, "")
                            text = _result_text(block.content)
                            if text and not name.endswith(("report_stage", "complete_goal")):
                                detail = text if len(text) <= 100 else text[:97] + "…"
                                failed = "failed" in text.lower()
                                emit(f"  {_c('31' if failed else '2', '⎿ ' + detail)}")
            elif isinstance(msg, ResultMessage):
                result = msg
    return result


def _make_stage_tools(
    session: "BrowserSession",
    goal: str,
    out_dir: "Path",
    emit: "Callable[[str], None]",
    records: "list[ActionRecord]",
    redactor: "Redactor | None" = None,
) -> "tuple[Any, Any, Callable]":
    """Create the report_stage and complete_goal tools and a finalize callable.

    All three share state via closures. Separated from run_goal so tests can
    drive the tool handlers directly without a real LLM or browser.

    Returns (report_stage_tool, complete_goal_tool, finalize).
    finalize(result, exc) → list[StepLog] — call exactly once after the run ends.
    """
    stages: list[StepLog] = []
    state = {"mark": 0, "t0": time.monotonic()}
    completion: dict[str, Any] = {"called": False, "verdict": None, "reason": ""}
    scrub = redactor.scrub if redactor is not None else (lambda s: s)

    @tool("report_stage",
          "Record a completed stage toward the goal — call after each meaningful "
          "stage. verdict PASS if the page confirms it happened, FAIL if the app "
          "broke. reason cites the page. Use complete_goal for the overall outcome.",
          {"description": str, "verdict": str, "reason": str})
    async def report_stage(a: dict) -> dict:
        try:
            v = Verdict(str(a["verdict"]).upper())
        except ValueError:
            return text_result("verdict must be PASS or FAIL.")
        if v not in (Verdict.PASS, Verdict.FAIL):
            return text_result("stage verdict must be PASS or FAIL; "
                               "use complete_goal for UNVERIFIED.")
        evidence = evidence_lines(session.evidence.drain())
        if redactor is not None:
            evidence = [redactor.scrub(e) for e in evidence]
        reason = redactor.scrub(a["reason"]) if redactor is not None else a["reason"]
        n = len(stages) + 1
        stages.append(StepLog(n, scrub(a["description"]), v, reason,
                              evidence=evidence,
                              action_records=records[state["mark"]:],
                              duration_ms=int((time.monotonic() - state["t0"]) * 1000)))
        state["mark"] = len(records)
        state["t0"] = time.monotonic()
        ok = v is Verdict.PASS
        emit(f"\n{_c('1;32', '✓ PASS') if ok else _c('1;31', '✗ FAIL')} "
             f"{_c('1', scrub(a['description']))}")
        emit(f"  {_c('2', scrub(a['reason']))}")
        return text_result(f"Recorded stage {n} as {v.value}.")

    @tool("complete_goal",
          "Call ONCE when the entire goal is finished — after all report_stage calls. "
          "verdict PASS if every required stage passed and the goal is fully achieved, "
          "FAIL if the app broke, UNVERIFIED if the outcome is ambiguous or cannot be "
          "judged from the page. reason cites the page. Always call this before stopping.",
          {"verdict": str, "reason": str})
    async def complete_goal_tool(a: dict) -> dict:
        try:
            v = Verdict(str(a["verdict"]).upper())
        except ValueError:
            return text_result("verdict must be PASS, FAIL, or UNVERIFIED.")
        if v not in (Verdict.PASS, Verdict.FAIL, Verdict.UNVERIFIED):
            return text_result("verdict must be PASS, FAIL, or UNVERIFIED.")
        if completion["called"]:
            return text_result("complete_goal already called; do not call it twice.")
        completion["called"] = True
        completion["verdict"] = v
        completion["reason"] = (redactor.scrub(a["reason"]) if redactor is not None
                                 else a["reason"])
        ok = v is Verdict.PASS
        emit(f"\n{_c('1;32', '✓ PASS') if ok else _c('1;31', '✗ FAIL')} "
             f"{_c('1', scrub(f'Goal: {goal}'))}")
        emit(f"  {_c('2', scrub(a['reason']))}")
        return text_result(f"Goal recorded as {v.value}. Stop now.")

    def finalize(result: "ResultMessage | None", exc: "Exception | None") -> list[StepLog]:
        """Build the final StepLog list from accumulated state + termination info.

        Called once after drive() returns or raises. Three outcomes:
          - exc is set              → ERROR (harness/infrastructure fault)
          - complete_goal was called → use that verdict (PASS / FAIL / UNVERIFIED)
          - neither                 → UNVERIFIED (run ended before explicit completion)
        """
        tail_records = records[state["mark"]:]
        tail_evidence = [scrub(e) for e in evidence_lines(session.evidence.drain())]
        elapsed = int((time.monotonic() - state["t0"]) * 1000)

        # SDK-level errors (result.is_error) are infrastructure faults even when
        # complete_goal was called — the session was not clean.
        sdk_errored = result is not None and getattr(result, "is_error", False)

        if exc is not None:
            stages.append(StepLog(
                len(stages) + 1, scrub(f"Goal: {goal}"), Verdict.ERROR,
                scrub(f"{type(exc).__name__}: {exc}"),
                evidence=tail_evidence,
                action_records=tail_records, duration_ms=elapsed,
            ))
        elif sdk_errored:
            errs = getattr(result, "errors", None)
            reason = scrub(f"SDK error: {errs}" if errs else "SDK error during execution")
            stages.append(StepLog(
                len(stages) + 1, scrub(f"Goal: {goal}"), Verdict.ERROR, reason,
                evidence=tail_evidence,
                action_records=tail_records, duration_ms=elapsed,
            ))
        elif not completion["called"]:
            # SDK ended (turn limit, stop signal, etc.) without complete_goal —
            # partial passes must not make an unfinished run pass.
            turns = f" ({result.num_turns} turns)" if result is not None else ""
            stages.append(StepLog(
                len(stages) + 1, scrub(f"Goal: {goal}"), Verdict.UNVERIFIED,
                scrub(f"run ended without complete_goal{turns}"),
                evidence=tail_evidence,
                action_records=tail_records, duration_ms=elapsed,
            ))
        else:
            stages.append(StepLog(
                len(stages) + 1, scrub(f"Goal: {goal}"),
                completion["verdict"], completion["reason"],  # type: ignore[arg-type]
                evidence=tail_evidence,
                action_records=tail_records, duration_ms=elapsed,
            ))

        return stages

    return report_stage, complete_goal_tool, finalize


async def run_goal(session: "BrowserSession", goal: str, cfg: "Config", out_dir: "Path",
                   on_event: "Callable[[str], None] | None" = None,
                   on_result: "Callable[[ResultMessage], None] | None" = None,
                   budget: "RunBudget | None" = None) -> list[StepLog]:
    """Pursue the goal; return one StepLog per stage + a final goal-completion log.

    on_result, if given, receives the final ResultMessage (SDK token + cost tally)
    so a caller like the suite runner can aggregate cost across many goals."""
    emit = on_event or (lambda _s: None)
    records: list[ActionRecord] = []
    redactor = make_redactor()

    report_stage, complete_goal_tool, finalize = _make_stage_tools(
        session, goal, out_dir, emit, records, redactor)

    stall_state: dict = {}  # written by the stall detector in harness/tools.py
    server = create_sdk_mcp_server(
        "assay", tools=[*browser_tools(
            session, records, out_dir, redactor,
            origin_policy=OriginPolicy(cfg.allowed_origins),
            budget=budget,
            stall_state=stall_state,
        ),
                      report_stage, complete_goal_tool])
    session.evidence.drain()  # start with clean buffers
    options = build_options(cfg, system_prompt=GOAL_SYSTEM, server=server, allowed_tools=_TOOLS,
                            hooks=page_hooks(session, out_dir), max_turns=MAX_TURNS,
                            tools=_BUILTINS, skills="all", setting_sources=["project"])
    result = None
    exc = None
    try:
        result = await drive(options, goal, on_event=emit, redactor=redactor)
    except Exception as e:  # noqa: BLE001 — harness fault, not the app's
        exc = e

    if isinstance(exc, BudgetExhausted):
        raise exc

    if stall_state.get("stalled"):
        # The agent was stuck (repeated tool failures): nothing it reported
        # afterwards can be trusted.  Harness fault → ERROR, never a FAIL.
        raise AgentStalledError(
            f"agent stalled after {stall_state.get('consecutive_failures', STALL_THRESHOLD)} "
            f"consecutive failed actions (last: {stall_state.get('detail', '?')})"
        )

    if result is not None:
        if budget is not None:
            budget.record_cost(float(result.total_cost_usd or 0.0))
        emit(_usage_summary(result))
        if on_result is not None:
            on_result(result)

    return finalize(result, exc)
