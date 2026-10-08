"""Explicit, frozen assertion checkpoints; the model cannot supply verdicts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from claude_agent_sdk import tool

from core.assertions import evaluate_assertion
from core.redact import make_redactor
from harness.tools import save_screenshot


async def capture_outcome(session, assertion, outcome, records, out_dir):
    """Attach browser provenance; paths are relative to the check report root."""
    redactor = make_redactor()
    outcome.reason = redactor.scrub(outcome.reason)
    outcome.evidence = redactor.scrub(outcome.evidence)
    outcome.action_ids = [r.action_id for r in records]
    if session.page is not None:
        outcome.url = redactor.scrub(session.page.url)
        # Never turn a model-controlled assertion ID into a filesystem path.
        token = hashlib.sha256(assertion.id.encode()).hexdigest()[:16]
        shot = await save_screenshot(session, out_dir / f"assertion-{token}.png")
        if shot is not None:
            outcome.screenshot = f"{out_dir.name}/{shot.name}"
    return outcome


def verification_tool(session, assertions, checker, outcomes, records, out_dir, budget=None):
    lookup = {a.id: a for a in assertions}

    @tool("verify_assertion",
          "Verify a frozen timing=checkpoint assertion on the CURRENT page before "
          "navigating onward. Supply only its assertion_id; the harness computes "
          "the verdict. Each checkpoint is immutable once evaluated.",
          {"assertion_id": str})
    async def verify(args):
        aid = args["assertion_id"]
        assertion = lookup.get(aid)
        if assertion is None or assertion.timing != "checkpoint":
            return {"isError": True, "content": [{"type": "text", "text": "Unknown checkpoint ID"}]}
        async with session.action_lock:
            if aid not in outcomes:
                if budget is not None:
                    budget.record_action()
                outcome = await evaluate_assertion(assertion, checker)
                outcomes[aid] = await capture_outcome(
                    session, assertion, outcome, records, out_dir,
                )
                if budget is not None:
                    budget.check()
            data = asdict(outcomes[aid])
        return {"content": [{"type": "text", "text": json.dumps(data)}]}

    return verify
