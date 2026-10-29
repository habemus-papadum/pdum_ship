"""The plugin manifest, the skills' links, and `ship skills sync` into a repository."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pdum.ship import __version__, skills

REPO = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def _links(text: str) -> list[str]:
    return [t for t in LINK_RE.findall(text) if not t.startswith(("http://", "https://", "#"))]


def test_plugin_manifest_parses_and_names_the_plugin():
    data = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "ship"
    assert "ship-release" in data["description"] and "ship-adopt" in data["description"]


def test_every_skill_has_frontmatter_and_resolving_links():
    for skill in skills.SKILLS:
        path = REPO / "skills" / skill / "SKILL.md"
        text = path.read_text()
        assert text.startswith("---\n"), skill
        assert f"name: {skill}\n" in text
        assert "description: Use when" in text
        for target in _links(text):
            resolved = (path.parent / target.split("#")[0]).resolve()
            assert resolved.exists(), f"{skill}: {target} does not resolve"


def test_render_makes_each_skill_directory_self_contained():
    for skill in skills.SKILLS:
        files = skills.render(skill)
        assert set(files) == {"SKILL.md", *skills.COMPANIONS[skill]}
        assert skills.HEADER in files["SKILL.md"]
        assert __version__ not in files["SKILL.md"]  # a release alone must not stale the copies
        assert files["SKILL.md"].startswith("---\n")
        for name, content in files.items():
            for target in _links(content):
                base = target.split("#")[0]
                assert base in files, f"{skill}/{name}: {target} points outside the skill directory"


def test_sync_writes_agents_skills_and_links_claude(tmp_path: Path):
    changed = skills.sync(tmp_path)
    assert changed
    for skill in skills.SKILLS:
        assert (tmp_path / ".agents" / "skills" / skill / "SKILL.md").exists()
        assert (tmp_path / ".claude" / "skills" / skill / "SKILL.md").exists()
    claude = tmp_path / ".claude" / "skills"
    assert claude.is_symlink() and claude.resolve() == (tmp_path / ".agents" / "skills").resolve()
    assert skills.problems(tmp_path) == []
    assert skills.sync(tmp_path) == []


def test_problems_reports_missing_then_stale(tmp_path: Path):
    assert any("missing" in p for p in skills.problems(tmp_path))
    skills.sync(tmp_path)
    target = tmp_path / ".agents" / "skills" / "ship-release" / "SKILL.md"
    target.write_text(target.read_text() + "\nlocal edit\n")
    assert [p for p in skills.problems(tmp_path) if "stale" in p]
    skills.sync(tmp_path)
    assert skills.problems(tmp_path) == []


def test_sync_mirrors_into_an_existing_claude_directory(tmp_path: Path):
    (tmp_path / ".claude" / "skills" / "other").mkdir(parents=True)
    skills.sync(tmp_path)
    assert not (tmp_path / ".claude" / "skills").is_symlink()
    assert (tmp_path / ".claude" / "skills" / "ship-adopt" / "conventions.md").exists()
    assert (tmp_path / ".claude" / "skills" / "other").is_dir()
    assert skills.problems(tmp_path) == []
