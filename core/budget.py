"""Run budget management for `assay check` runs.

A RunBudget tracks elapsed time, action count, and accumulated cost against
configured limits.  It is started once before planning begins and checked
before / during every browser action.

When a limit is exceeded, ``BudgetExhausted`` is raised.  The executor catches
this at the run level and marks unfinished scenarios UNVERIFIED.

Cost thresholds
---------------
SDK-reported token usage may be batched or delayed, so a cost limit cannot be
enforced as a hard cap.  The budget records accumulated cost and raises
``BudgetExhausted`` when it detects a threshold crossing — but a single large
request could exceed the threshold before the next check.  This is disclosed
in the ``BudgetExhausted`` reason string and in the summary.

Clock injection
---------------
Pass a ``FakeClock`` in tests so time is fully deterministic.  Production
code uses ``RealClock`` (the default).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol


# ── Clock protocol ────────────────────────────────────────────────────────────

class Clock(Protocol):
    """Injectable time source."""

    def now(self) -> float:
        """Return current time as a monotonic float (seconds)."""
        ...


class RealClock:
    """Production clock backed by ``time.monotonic``."""

    def now(self) -> float:
        return time.monotonic()


class FakeClock:
    """Deterministic clock for tests.

    Start at *start* seconds (default 0.0).  Call ``advance(delta)`` to
    simulate elapsed time.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        """Advance the clock by *seconds*."""
        self._t += seconds


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class BudgetConfig:
    """User-visible budget limits.  ``None`` means unlimited.

    Produced from depth presets and optional CLI/env overrides.  All fields
    are optional so that the executor can run without any budget configured.
    """

    max_seconds: int | None = None
    max_actions: int | None = None
    max_cost_usd: float | None = None


# ── Exception ─────────────────────────────────────────────────────────────────

class BudgetExhausted(Exception):
    """Raised when any configured budget limit is exceeded.

    The *reason* string is forwarded as the UNVERIFIED verdict reason for
    every scenario that did not complete before the limit was hit.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ── Budget manager ────────────────────────────────────────────────────────────

class RunBudget:
    """Tracks time, actions, and cost against configured limits.

    Usage::

        budget = RunBudget(BudgetConfig(max_seconds=600, max_actions=200))
        budget.start()
        # … before each browser action …
        budget.record_action()   # increments counter and checks limits
        # … after model call with usage info …
        budget.record_cost(0.05)

    All ``check``-family methods raise ``BudgetExhausted`` when a limit is
    exceeded.
    """

    def __init__(
        self,
        config: BudgetConfig,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock: Clock = clock if clock is not None else RealClock()
        self._start_time: float | None = None
        self._actions: int = 0
        self._cost_usd: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Record the start time.  Must be called before any checks."""
        self._start_time = self._clock.now()

    # ── Mutators ──────────────────────────────────────────────────────────────

    def record_action(self) -> None:
        """Increment the action counter then check all limits."""
        self._actions += 1
        self.check()

    def record_cost(self, usd: float) -> None:
        """Accumulate *usd* of model cost then check the cost limit.

        The cost limit is advisory: SDK-reported usage may be delayed so a
        single large call may push the total above the threshold before the
        next check.  The reason string discloses this limitation.
        """
        self._cost_usd += usd
        self._check_cost()

    # ── Checks ────────────────────────────────────────────────────────────────

    def check(self) -> None:
        """Check all limits; raise ``BudgetExhausted`` if any is exceeded."""
        self._check_time()
        self._check_actions()
        self._check_cost()

    def _check_time(self) -> None:
        if self._config.max_seconds is None:
            return
        elapsed = self.elapsed_seconds()
        if elapsed >= self._config.max_seconds:
            raise BudgetExhausted(
                f"time limit exceeded: {elapsed:.1f}s >= {self._config.max_seconds}s"
            )

    def _check_actions(self) -> None:
        if self._config.max_actions is None:
            return
        if self._actions > self._config.max_actions:
            raise BudgetExhausted(
                f"action limit exceeded: {self._actions} actions "
                f"> {self._config.max_actions} allowed"
            )

    def _check_cost(self) -> None:
        if self._config.max_cost_usd is None:
            return
        if self._cost_usd >= self._config.max_cost_usd:
            raise BudgetExhausted(
                f"cost threshold reached: ${self._cost_usd:.4f} >= "
                f"${self._config.max_cost_usd:.4f} — note: SDK usage reporting "
                f"may be batched; actual spend could exceed this limit"
            )

    # ── Accessors ─────────────────────────────────────────────────────────────

    def elapsed_seconds(self) -> float:
        """Seconds since ``start()`` was called; 0.0 if not yet started."""
        if self._start_time is None:
            return 0.0
        return self._clock.now() - self._start_time

    def remaining_seconds(self) -> float | None:
        """Seconds remaining before time limit, or ``None`` if unlimited."""
        if self._config.max_seconds is None:
            return None
        return max(0.0, self._config.max_seconds - self.elapsed_seconds())

    def actions_count(self) -> int:
        """Total actions recorded so far."""
        return self._actions

    def cost_usd(self) -> float:
        """Accumulated cost in USD."""
        return self._cost_usd

    def summary(self) -> dict:
        """Serialisable snapshot of budget state for report inclusion."""
        return {
            "elapsed_seconds": round(self.elapsed_seconds(), 2),
            "actions": self._actions,
            "cost_usd": round(self._cost_usd, 6),
            "limits": {
                "max_seconds": self._config.max_seconds,
                "max_actions": self._config.max_actions,
                "max_cost_usd": self._config.max_cost_usd,
            },
        }


# ── Factory ───────────────────────────────────────────────────────────────────

def budget_from_depth(
    depth: str,
    max_seconds: int | None = None,
    max_actions: int | None = None,
    max_cost_usd: float | None = None,
    clock: Clock | None = None,
) -> RunBudget:
    """Create a ``RunBudget`` using depth preset values with optional overrides.

    CLI/env overrides replace the preset values when provided.
    """
    from core.planner import DEPTH_POLICIES  # avoid circular import at module level

    policy = DEPTH_POLICIES[depth]
    config = BudgetConfig(
        max_seconds=max_seconds if max_seconds is not None else policy.max_seconds,
        max_actions=max_actions if max_actions is not None else policy.max_actions,
        max_cost_usd=max_cost_usd,
    )
    return RunBudget(config, clock=clock)
