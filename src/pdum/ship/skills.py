"""Write ship's agent skills into a repository, and report when the copies are stale.

The plugin skills under ``skills/`` and the documents they point at (``README.md``,
``docs/conventions.md``) are bundled into the wheel as ``pdum/ship/_bundled``. ``sync``
writes each skill into ``.agents/skills/<skill>/`` with its documents beside it, links
rewritten so the directory is self-contained, and makes ``.claude/skills`` point at
``.agents/skills``. Claude Code reads the latter, Codex the former. ``problems`` compares
the files in a repository with what this installed version would write.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from . import __version__

SKILLS = ("ship-release", "ship-adopt")

# For each skill: the documents copied next to SKILL.md, as {name in the skill dir: source}.
_DOCS = {"README.md": "README.md", "conventions.md": "docs/conventions.md"}
COMPANIONS: dict[str, dict[str, str]] = {
    "ship-release": dict(_DOCS),
    "ship-adopt": dict(_DOCS),
}

# Relative links in the plugin skill resolve against the plugin root (the repository).
# In a synced copy they resolve against the skill directory, where the companions sit.
_LINK_REWRITES = {
    "../../README.md": "README.md",
    "../../docs/conventions.md": "conventions.md",
}

# No version in the header: a copy is stale only when its content differs from what the
# installed ship would write, so a release of ship that changes no skill text changes nothing.
HEADER = (
    "<!-- Written by `ship skills sync` from the installed habemus-papadum-ship. Do not edit;"
    " change the source in pdum_ship and run the sync again. -->\n"
)

_FRONTMATTER_END = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


class SkillsError(RuntimeError):
    """The bundled skills could not be found."""


def _source_root() -> Path:
    """Where the skills and documents live: the wheel's bundle, or the source checkout."""
    bundled = Path(str(resources.files("pdum.ship") / "_bundled"))
    if (bundled / "skills").is_dir():
        return bundled
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / "skills").is_dir() and (checkout / "README.md").exists():
        return checkout
    raise SkillsError("ship's bundled skills are missing from this installation")


def _read(relative: str) -> str:
    path = _source_root() / relative
    if not path.exists():
        raise SkillsError(f"bundled file {relative} is missing")
    return path.read_text()


def render(skill: str) -> dict[str, str]:
    """The files a synced skill directory holds, as {file name: content}."""
    if skill not in SKILLS:
        raise SkillsError(f"unknown skill {skill!r}")
    text = _read(f"skills/{skill}/SKILL.md")
    for source, target in _LINK_REWRITES.items():
        text = text.replace(f"]({source})", f"]({target})").replace(f"]({source}#", f"]({target}#")
    match = _FRONTMATTER_END.match(text)
    if not match:
        raise SkillsError(f"skills/{skill}/SKILL.md has no frontmatter")
    text = text[: match.end()] + "\n" + HEADER + text[match.end() :].lstrip("\n")
    files = {"SKILL.md": text}
    for name, source in COMPANIONS[skill].items():
        body = _read(source)
        # The README links to docs/conventions.md; beside the synced skill it is conventions.md.
        body = body.replace("](docs/conventions.md", "](conventions.md").replace("](../README.md", "](README.md")
        files[name] = body
    return files


def expected(root: Path) -> dict[Path, str]:
    """Every file ``sync`` would write under ``root``, keyed by absolute path."""
    out: dict[Path, str] = {}
    for skill in SKILLS:
        for name, content in render(skill).items():
            out[root / ".agents" / "skills" / skill / name] = content
    return out


def _claude_state(root: Path) -> str:
    """'linked', 'directory' or 'missing' for ``.claude/skills``."""
    claude = root / ".claude" / "skills"
    if claude.is_symlink():
        return "linked"
    if claude.is_dir():
        return "directory"
    return "missing"


def sync(root: Path) -> list[Path]:
    """Write the skills; return the paths that were created or changed."""
    changed: list[Path] = []
    for path, content in expected(root).items():
        if not path.exists() or path.read_text() != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            changed.append(path)

    claude = root / ".claude" / "skills"
    state = _claude_state(root)
    if state == "missing":
        claude.parent.mkdir(parents=True, exist_ok=True)
        claude.symlink_to(Path("..") / ".agents" / "skills")
        changed.append(claude)
    elif state == "directory":
        # Someone keeps real files there; mirror the skills into it as well.
        for path, content in expected(root).items():
            mirror = claude / path.relative_to(root / ".agents" / "skills")
            if not mirror.exists() or mirror.read_text() != content:
                mirror.parent.mkdir(parents=True, exist_ok=True)
                mirror.write_text(content)
                changed.append(mirror)
    return changed


def problems(root: Path) -> list[str]:
    """What ``sync`` would change, as messages for ``ship deps check``."""
    out: list[str] = []
    for path, content in expected(root).items():
        rel = path.relative_to(root)
        if not path.exists():
            out.append(f"{rel} is missing; run `ship skills sync`")
        elif path.read_text() != content:
            out.append(f"{rel} is stale for ship {__version__}; run `ship skills sync`")
    state = _claude_state(root)
    if state == "missing":
        out.append(".claude/skills does not exist; run `ship skills sync`")
    elif state == "directory":
        for path, content in expected(root).items():
            mirror = root / ".claude" / "skills" / path.relative_to(root / ".agents" / "skills")
            if not mirror.exists() or mirror.read_text() != content:
                out.append(f"{mirror.relative_to(root)} is missing or stale; run `ship skills sync`")
    return out
