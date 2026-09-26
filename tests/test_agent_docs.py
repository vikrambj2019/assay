"""Agent-facing docs must not drift from the real CLI and summary schema.

The browser-check skill and the AGENTS.md snippet are the contract coding
agents follow. If a flag is renamed or a summary field removed, these tests
fail before an agent is told to use something that no longer exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / "harness" / ".claude" / "skills" / "browser-check" / "SKILL.md",
    ROOT / "integrations" / "AGENTS.md",
]


def _check_flags() -> set[str]:
    from harness.cli import build_parser

    parser = build_parser()
    for action in parser._actions:
        if hasattr(action, "_name_parser_map"):
            check = action._name_parser_map["check"]
            return {opt for a in check._actions for opt in a.option_strings}
    raise AssertionError("check subcommand not found")


def _summary_keys() -> set[str]:
    from core.check_summary import build_summary
    from core.executor import RunResult
    from core.plan import Assertion, AssertionKind, Scenario, SourceRef, make_plan, save_plan
    import tempfile

    plan = make_plan("low", [Scenario(
        id="s1", title="t", goal="g", prerequisites=[], requires_mutations=False, reason="r",
        assertions=[Assertion(id="a1", description="d", kind=AssertionKind.REQUIRED,
                              source=SourceRef(kind="notes", path="n", excerpt="e"))],
    )])
    with tempfile.TemporaryDirectory() as d:
        path = save_plan(plan, Path(d))
        doc = build_summary(code=0, run_result=RunResult(run_id="r", scenario_results=[], complete=True),
                            plan=plan, plan_path=path, plan_source="generated", out_dir=Path(d),
                            base_url="http://x")
    return set(doc) | {f"plan.{k}" for k in doc["plan"]} | {f"rerun.{k}" for k in ("all", "failed")}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_documented_flags_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    commands = re.findall(r"assay check[^\n`]*(?:\\\n[^\n`]*)*", text)
    assert commands, f"{doc} documents no assay check command"
    used = {f for cmd in commands for f in re.findall(r"(?<![\w-])--[a-z][a-z-]*", cmd)}
    missing = used - _check_flags()
    assert not missing, f"{doc.name} documents flags the CLI does not have: {sorted(missing)}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_documented_summary_fields_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    fields = {"status", "exit_code", "failures", "needs_attention", "scope"}
    fields |= set(re.findall(r"`(plan\.sha256|rerun\.failed)`", text))
    missing = {f for f in fields if f in text} - _summary_keys()
    assert not missing, f"{doc.name} references summary fields that do not exist: {sorted(missing)}"


def test_docs_use_new_env_prefix() -> None:
    for doc in DOCS:
        assert "BTA_" not in doc.read_text(encoding="utf-8"), doc
