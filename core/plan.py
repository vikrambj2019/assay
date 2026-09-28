"""Structured test-plan schema for `assay check`.

A Plan is the source of truth for what a run intends to verify and why.
It is created by the planner (Task 11), validated, and persisted as
plan.json *before* any scenario is executed — so a partial run always has
a complete plan record and expected outcomes can never be silently weakened
once execution begins.

Schema version is embedded in every plan.json so future format changes
are detectable at load time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from core.redact import make_redactor

SCHEMA_VERSION = "1"


# ── Enumerations ──────────────────────────────────────────────────────────────

class AssertionKind(str, Enum):
    """How an assertion was determined.

    REQUIRED     — explicitly stated in the supplied context (notes, README,
                   or diff).  Must carry a SourceRef; absent → validation error.
    ASSUMPTION   — inferred by the planner without an explicit requirement basis.
    EXPLORATORY  — additional coverage added beyond stated requirements.

    The distinction is enforced so assumptions can never masquerade as
    supplied requirements.
    """
    REQUIRED    = "required"
    ASSUMPTION  = "assumption"
    EXPLORATORY = "exploratory"


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SourceRef:
    """A reference to the exact piece of context that motivated a decision.

    Enables traceability: each REQUIRED assertion (and optionally each
    scenario) points back to the notes section, README paragraph, or diff
    path that necessitated it.
    """
    kind: str     # "notes" | "readme" | "diff"
    path: str     # file path or e.g. "git diff main"
    excerpt: str  # the motivating text excerpt (may be truncated for display)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path, "excerpt": self.excerpt}

    @classmethod
    def from_dict(cls, d: dict) -> "SourceRef":
        return cls(kind=str(d["kind"]), path=str(d["path"]),
                   excerpt=str(d["excerpt"]))


@dataclass
class Assertion:
    """One expected outcome within a scenario.

    Every scenario must have at least one assertion.  REQUIRED assertions
    must carry a SourceRef so the requirement is traceable back to the
    supplied context.

    The optional ``check`` field encodes a deterministic, machine-verifiable
    check specification so the executor can verify the assertion without an
    LLM call. When absent, the assertion remains UNVERIFIED. Overall agent
    verdicts never substitute for assertion-specific verification.
    timing="checkpoint" runs the check explicitly within the browser flow;
    timing="final" (default) runs it after the scenario completes.

    Supported check types::

        {"type": "url_contains",    "value": "/dashboard"}
        {"type": "text_visible",    "text": "Welcome"}
        {"type": "text_absent",     "text": "Error"}
        {"type": "field_value",     "selector": "input[name=email]", "value": "x"}
        {"type": "element_visible", "selector": ".success-banner"}
        {"type": "element_hidden",  "selector": ".error-banner"}
        {"type": "persistence",     "text": "Record saved"}   # must survive reload
    """
    id: str
    description: str
    kind: AssertionKind
    source: "SourceRef | None" = None  # mandatory when kind is REQUIRED
    check: "dict | None" = None        # optional deterministic check spec
    timing: str = "final"             # final page or explicit in-flow checkpoint

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "kind": self.kind.value,
            "source": self.source.to_dict() if self.source else None,
            "check": self.check,
            "timing": self.timing,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Assertion":
        return cls(
            id=str(d["id"]),
            description=str(d["description"]),
            kind=AssertionKind(d["kind"]),
            source=SourceRef.from_dict(d["source"]) if d.get("source") else None,
            check=d.get("check"),
            timing=d.get("timing", "final"),
        )


@dataclass
class Scenario:
    """One test scenario — a goal the agent will pursue in a real browser.

    IDs are stable across serialisation; every result refers to its
    scenario ID so evidence is traceable even when the run is incomplete.
    """
    id: str
    title: str
    goal: str                              # NL goal handed to the agent
    prerequisites: list[str]              # IDs of scenarios that must pass first
    assertions: list[Assertion]           # expected outcomes (must be non-empty)
    requires_mutations: bool              # creates / edits / deletes data
    reason: str                           # human-readable reason for inclusion
    source: "SourceRef | None" = None    # originating context reference
    skip: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "prerequisites": list(self.prerequisites),
            "assertions": [a.to_dict() for a in self.assertions],
            "requires_mutations": self.requires_mutations,
            "reason": self.reason,
            "source": self.source.to_dict() if self.source else None,
            "skip": self.skip,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        return cls(
            id=str(d["id"]),
            title=str(d["title"]),
            goal=str(d["goal"]),
            prerequisites=list(d.get("prerequisites") or []),
            assertions=[Assertion.from_dict(a) for a in d.get("assertions") or []],
            requires_mutations=bool(d.get("requires_mutations", False)),
            reason=str(d.get("reason", "")),
            source=SourceRef.from_dict(d["source"]) if d.get("source") else None,
            skip=bool(d.get("skip", False)),
        )


@dataclass
class Plan:
    """A complete, validated test plan persisted as plan.json before execution.

    coverage_suggestions lists test ideas that were considered but not
    selected (e.g. exceeded the depth cap).  They are tracked separately
    from planned scenarios so no suggestion is silently discarded.
    """
    version: str
    created_at: str                             # ISO 8601 UTC timestamp
    depth: str                                  # low | medium | high
    scenarios: list[Scenario]
    coverage_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "depth": self.depth,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "coverage_suggestions": list(self.coverage_suggestions),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(
            version=str(d["version"]),
            created_at=str(d["created_at"]),
            depth=str(d["depth"]),
            scenarios=[Scenario.from_dict(s) for s in d.get("scenarios") or []],
            coverage_suggestions=list(d.get("coverage_suggestions") or []),
        )


# ── Factory ───────────────────────────────────────────────────────────────────

def make_plan(
    depth: str,
    scenarios: list[Scenario],
    coverage_suggestions: list[str] | None = None,
) -> Plan:
    """Create a new Plan stamped with the current UTC time."""
    return Plan(
        version=SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        depth=depth,
        scenarios=scenarios,
        coverage_suggestions=coverage_suggestions or [],
    )


# ── Validation ────────────────────────────────────────────────────────────────

def validate_plan(plan: Plan) -> None:
    """Validate a Plan; raise ValueError listing every problem found.

    Rules (all collected before raising so the caller sees the full list):
    1. Recognised schema version.
    2. At least one scenario (empty plans must never pass).
    3. Unique scenario IDs.
    4. Every scenario has at least one assertion (expected outcomes required).
    5. REQUIRED assertions carry a SourceRef (assumptions ≠ requirements).
    6. Prerequisites reference known scenario IDs.
    7. No cycles in the prerequisite graph.
    """
    errors: list[str] = []

    if plan.depth not in ("low", "medium", "high"):
        errors.append(f"invalid plan depth {plan.depth!r}; expected low, medium, or high")

    if plan.version != SCHEMA_VERSION:
        errors.append(
            f"unrecognised plan version {plan.version!r}; "
            f"expected {SCHEMA_VERSION!r}"
        )

    if not plan.scenarios:
        errors.append("plan has no scenarios — empty plans must never pass")
        _raise_if(errors)  # further checks are meaningless without scenarios

    # Unique IDs
    seen_ids: set[str] = set()
    for s in plan.scenarios:
        if s.id in seen_ids:
            errors.append(f"duplicate scenario ID {s.id!r}")
        seen_ids.add(s.id)

    known_ids = {s.id for s in plan.scenarios}

    for s in plan.scenarios:
        if not isinstance(s.id, str) or not s.id.strip():
            errors.append("scenario ID must be a non-empty string")
        if not isinstance(s.title, str) or not s.title.strip():
            errors.append(f"scenario {s.id!r}: title must be a non-empty string")
        if not isinstance(s.goal, str) or not s.goal.strip():
            errors.append(f"scenario {s.id!r}: goal must be a non-empty string")
        if not isinstance(s.skip, bool):
            errors.append(f"scenario {s.id!r}: skip must be a boolean")
        if not isinstance(s.requires_mutations, bool):
            errors.append(f"scenario {s.id!r}: requires_mutations must be a boolean")
        if not isinstance(s.prerequisites, list) or any(
            not isinstance(dep, str) or not dep.strip() for dep in s.prerequisites
        ):
            errors.append(f"scenario {s.id!r}: prerequisites must be a list of non-empty IDs")
        if s.source is not None:
            if s.source.kind not in ("notes", "readme", "diff"):
                errors.append(f"scenario {s.id!r}: source kind must be notes, readme, or diff")
            if not s.source.path.strip() or not s.source.excerpt.strip():
                errors.append(f"scenario {s.id!r}: source path and excerpt are required")
        # Missing expected outcomes
        if not s.assertions:
            errors.append(
                f"scenario {s.id!r} ({s.title!r}): "
                f"has no assertions — expected outcomes are required"
            )

        # REQUIRED assertions must carry a source reference
        assertion_ids: set[str] = set()
        for a in s.assertions:
            if not isinstance(a.id, str) or not a.id.strip():
                errors.append(f"scenario {s.id!r}: assertion ID must be a non-empty string")
            elif a.id in assertion_ids:
                errors.append(f"scenario {s.id!r}: duplicate assertion ID {a.id!r}")
            assertion_ids.add(a.id)
            if a.timing not in ("final", "checkpoint"):
                errors.append(f"assertion {a.id!r}: timing must be final or checkpoint")
            if not isinstance(a.description, str) or not a.description.strip():
                errors.append(f"scenario {s.id!r}, assertion {a.id!r}: description must be non-empty")
            if not isinstance(a.kind, AssertionKind):
                errors.append(f"scenario {s.id!r}, assertion {a.id!r}: invalid assertion kind")
                continue
            if a.kind is AssertionKind.REQUIRED and a.source is None:
                errors.append(
                    f"scenario {s.id!r}, assertion {a.id!r}: "
                    f"kind='required' but no source reference — "
                    f"assumptions cannot masquerade as supplied requirements"
                )
            if a.source is not None:
                if a.source.kind not in ("notes", "readme", "diff"):
                    errors.append(
                        f"scenario {s.id!r}, assertion {a.id!r}: "
                        f"source kind {a.source.kind!r} must be notes, readme, or diff"
                    )
                if not a.source.path.strip() or not a.source.excerpt.strip():
                    errors.append(
                        f"scenario {s.id!r}, assertion {a.id!r}: source path and excerpt are required"
                    )

        # Unknown prerequisites
        for dep in s.prerequisites:
            if dep not in known_ids:
                errors.append(
                    f"scenario {s.id!r}: prerequisite {dep!r} does not exist"
                )

    _raise_if(errors)

    # Cycle detection via DFS — only runs after the above structural checks pass
    graph = {s.id: s.prerequisites for s in plan.scenarios}
    state: dict[str, int] = {}  # 0 = visiting, 1 = done

    def _visit(n: str, trail: list[str]) -> None:
        if state.get(n) == 1:
            return
        if state.get(n) == 0:
            errors.append(f"prerequisite cycle: {' → '.join(trail + [n])}")
            return
        state[n] = 0
        for dep in graph.get(n, []):
            _visit(dep, trail + [n])
        state[n] = 1

    for s in plan.scenarios:
        _visit(s.id, [])

    _raise_if(errors)


# ── Persistence ───────────────────────────────────────────────────────────────

def save_plan(plan: Plan, out_dir: Path) -> Path:
    """Write plan.json to *out_dir* and return the path.

    Creates the directory when it does not exist.  Called before any
    scenario is executed so a partial run always has a complete plan record.
    """
    validate_plan(plan)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "plan.json"
    text = json.dumps(plan.to_dict(), indent=2, ensure_ascii=False)
    path.write_text(make_redactor().scrub(text), encoding="utf-8")
    return path


def load_plan(path: Path) -> Plan:
    """Load plan.json from *path*, deserialise, and validate.

    Raises:
        FileNotFoundError: when *path* does not exist.
        json.JSONDecodeError: when the file is not valid JSON.
        ValueError: when the plan fails validation.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    plan = Plan.from_dict(data)
    validate_plan(plan)
    return plan


# ── Internal helpers ──────────────────────────────────────────────────────────

def _raise_if(errors: list[str]) -> None:
    if errors:
        raise ValueError(
            "Plan validation errors:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
