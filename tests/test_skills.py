"""The migrated skill files are well-formed for native SDK discovery.

Skills are now native Agent Skills under .claude/skills/ (the SDK's Skill tool
discovers them); there is no custom loader to unit-test. These checks guard the
one thing the migration owns: that each SKILL.md carries the name/description
frontmatter the SDK needs to surface it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parents[1] / "harness" / ".claude" / "skills"
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))


def test_expected_skills_present() -> None:
    names = {p.parent.name for p in SKILL_FILES}
    assert names >= {"fixture-login"}


@pytest.mark.parametrize("skill_md", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_has_name_and_description_frontmatter(skill_md: Path) -> None:
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_md} must open with YAML frontmatter"
    frontmatter = text.split("---", 2)[1]
    assert "name:" in frontmatter, f"{skill_md} frontmatter missing name:"
    assert "description:" in frontmatter, f"{skill_md} frontmatter missing description:"
