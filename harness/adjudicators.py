"""Production adjudicators: fresh-eyes reviewers of FAIL verdicts.

``AnthropicAdjudicator`` calls the Anthropic messages API directly (the same
pattern as ``AnthropicPlannerAdapter`` in core/planner.py) with the skeptical
review brief from core/adjudicate.py.  It is text-only by design: the reviewer
judges from the full action trail and final page state, which the driving
agent never saw all at once.

``make_adjudicator`` wires it to Config: adjudication runs only when
``ASSAY_ADJUDICATE_FAILS=true`` (or ``--adjudicate-fails``); otherwise None and
FAIL verdicts are recorded unreviewed, exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.adjudicate import (
    ADJUDICATION_SYSTEM,
    AdjudicationInput,
    AdjudicationResult,
    build_adjudication_prompt,
)

if TYPE_CHECKING:
    from core.config import Config


def parse_adjudication_response(text: str) -> tuple[bool, str]:
    """Parse the reviewer's VERDICT/REASON response.

    Returns (confirmed, reason).  Anything unparseable — or anything other
    than an explicit CONFIRMED — counts as NOT CONFIRMED: when the reviewer
    cannot speak clearly, the FAIL is not confirmed.
    """
    verdict_line = ""
    reason_lines: list[str] = []
    in_reason = False
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("VERDICT:"):
            verdict_line = upper[len("VERDICT:"):].strip()
            in_reason = False
        elif upper.startswith("REASON:"):
            reason_lines.append(stripped[len("REASON:"):].strip())
            in_reason = True
        elif in_reason and stripped:
            reason_lines.append(stripped)
    reason = " ".join(reason_lines).strip() or "(no reason given)"
    confirmed = verdict_line == "CONFIRMED"
    return confirmed, reason


def _message_cost(msg: object, model: str) -> float:
    """Estimate direct API cost from usage, when the SDK exposes no total."""
    total = getattr(msg, "total_cost_usd", None)
    if total is not None:
        return float(total)
    usage = getattr(msg, "usage", None)
    if usage is None:
        return 0.0
    inp = float(getattr(usage, "input_tokens", 0) or 0)
    out = float(getattr(usage, "output_tokens", 0) or 0)
    # Sonnet pricing; keep a conservative fallback for compatible model names.
    return (inp * 3.0 + out * 15.0) / 1_000_000


class AnthropicAdjudicator:
    """Review FAIL verdicts via the Anthropic messages API.

    Requires the ``anthropic`` package.  Reads ANTHROPIC_API_KEY from the
    environment unless *api_key* is given.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        max_tokens: int = 2048,
        api_key: "str | None" = None,
    ) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for adjudication. "
                "Install it with: pip install anthropic"
            ) from exc
        kwargs = {"api_key": api_key} if api_key else {}
        self._client = _anthropic.Anthropic(**kwargs)
        self._model = model
        self._max_tokens = max_tokens
        self.reviewer = f"anthropic:{model}"

    async def __call__(self, inp: AdjudicationInput) -> AdjudicationResult:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=ADJUDICATION_SYSTEM,
            messages=[{"role": "user", "content": build_adjudication_prompt(inp)}],
        )
        text = ""
        for block in msg.content:
            if hasattr(block, "text"):
                text = block.text
                break
        confirmed, reason = parse_adjudication_response(text)
        return AdjudicationResult(
            confirmed=confirmed, reason=reason, reviewer=self.reviewer,
            cost_usd=_message_cost(msg, self._model),
        )


def make_adjudicator(cfg: "Config") -> "AnthropicAdjudicator | None":
    """Return the configured adjudicator, or None when adjudication is off."""
    if not cfg.adjudicate_fails:
        return None
    return AnthropicAdjudicator(model=cfg.model)
