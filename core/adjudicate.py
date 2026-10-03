"""Independent adjudication of FAIL verdicts.

The agent that drives the browser also reports the verdict — a self-graded
claim.  Adjudication re-examines a reported FAIL with fresh eyes before it is
recorded as a confirmed application failure.

The adjudicator sees the *whole* run at once (goal, full action trail, final
page state) and is briefed to be skeptical: its job is to distinguish "the app
genuinely broke" from "the agent got lost, misread the page, or gave up early".

Contract:
  - ``adjudicate_fail`` returns None when no adjudicator is configured.
  - A broken adjudicator (exception) also yields None: the original FAIL
    stands, annotated that adjudication was unavailable.  A broken reviewer
    must never manufacture a PASS, and its absence must not erase an
    established verdict.
  - Only an explicit NOT CONFIRMED downgrades FAIL → UNVERIFIED, with both
    rationales preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Protocol


@dataclass
class AdjudicationInput:
    """Everything a reviewer needs to judge a reported FAIL."""

    goal: str
    reported_reason: str
    action_trail: list[str] = field(default_factory=list)  # per-action summaries
    final_page_text: str = ""
    screenshot_paths: list[str] = field(default_factory=list)  # for the human report


@dataclass
class AdjudicationResult:
    """The reviewer's judgment."""

    confirmed: bool   # True → the FAIL stands; False → downgrade to UNVERIFIED
    reason: str       # the reviewer's rationale (preserved in the report)
    reviewer: str     # identifier, e.g. "anthropic:claude-sonnet-5" or "stub"


class Adjudicator(Protocol):
    """A fresh-eyes reviewer of FAIL verdicts."""

    async def __call__(self, inp: AdjudicationInput) -> AdjudicationResult:
        ...


ADJUDICATION_SYSTEM = """\
You are reviewing a browser test run. A test agent pursued a goal in a web \
application and reported FAIL — that the application is broken. Your job is to \
confirm or reject that verdict from the evidence.

You are skeptical by design. Test agents fail in two very different ways:
1. The APP is genuinely broken (wrong redirect, error banner, missing data,
   console exceptions on critical requests). -> CONFIRM the FAIL.
2. The AGENT got confused: it navigated to the wrong page, misread the page
   state, timed out waiting for something that needed a different action, or
   gave up before the outcome was observable. -> DO NOT CONFIRM.

Judge ONLY from the evidence below: the goal, the agent's action trail, and the
final page state. Do not invent page states. When in doubt, DO NOT CONFIRM — a \
false FAIL erodes trust in the whole system, while UNVERIFIED simply asks a \
human to look.

Respond in exactly this format:
VERDICT: CONFIRMED
REASON: <one or two sentences citing the specific evidence>

or

VERDICT: NOT CONFIRMED
REASON: <one or two sentences explaining what the agent got wrong>
"""


def build_adjudication_prompt(inp: AdjudicationInput) -> str:
    """Render the review prompt for one reported FAIL."""
    trail = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(inp.action_trail))
    page_text = inp.final_page_text[-8000:]  # tail: the end state matters most
    return (
        f"GOAL:\n{inp.goal}\n\n"
        f"AGENT'S REPORTED FAILURE:\n{inp.reported_reason}\n\n"
        f"ACTION TRAIL ({len(inp.action_trail)} actions):\n{trail or '(no actions recorded)'}\n\n"
        f"FINAL PAGE STATE (tail):\n{page_text or '(no page text captured)'}\n"
    )


async def adjudicate_fail(
    inp: AdjudicationInput,
    adjudicator: "Adjudicator | None",
) -> "AdjudicationResult | None":
    """Run the adjudicator; None when unconfigured or when it errors.

    A broken reviewer yields None (original FAIL stands, annotated) — it must
    never manufacture a PASS nor silently erase an established verdict.
    """
    if adjudicator is None:
        return None
    try:
        return await adjudicator(inp)
    except Exception:  # noqa: BLE001 — reviewer fault, not the app's
        return None
