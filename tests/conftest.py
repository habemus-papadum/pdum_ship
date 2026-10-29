"""Fixtures: a throwaway uv project in a git repository with a bare ``origin``.

The tests run real ``git`` and ``uv`` commands. ``uv build`` fetches the build backend from
PyPI the first time, so the release tests need network access.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SHIP_TABLE = """
[tool.ship]
ours = ["demo-*", "@demo/*"]
shared-paths = ["shared/**"]
github-release = false
"""


def run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


@dataclass
class DemoRepo:
    root: Path
    origin: Path

    def git(self, *args: str) -> str:
        return run(["git", *args], self.root)

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def origin_tags(self) -> list[str]:
        return run(["git", "tag", "--list"], self.origin).split()

    def origin_log(self) -> list[str]:
        return run(["git", "log", "--format=%s", "main"], self.origin).splitlines()


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "ship tests")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "ship@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "ship tests")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "ship@example.invalid")
    # Never let a developer's global config (signing, hooks) leak into the tests.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    # The throwaway projects have their own environments; ship's own must not leak in.
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


def _init_repo(root: Path, origin: Path) -> DemoRepo:
    run(["git", "init", "-q", "-b", "main", str(root)], root.parent)
    run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], root.parent)
    demo = DemoRepo(root=root, origin=origin)
    demo.git("remote", "add", "origin", str(origin))
    return demo


@pytest.fixture
def single_repo(tmp_path: Path) -> DemoRepo:
    """One Python package at the root: ``demo-pkg``, manifest at ``0.0.0+dev``."""
    root = tmp_path / "repo"
    root.mkdir()
    origin = tmp_path / "origin.git"
    demo = _init_repo(root, origin)
    run(["uv", "init", "--lib", "--name", "demo-pkg", "--no-workspace", "--vcs", "none", "."], root)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + SHIP_TABLE)
    run(["uv", "version", "0.0.0+dev", "--no-sync"], root)
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    run(["uv", "add", "--dev", "pytest", "--no-sync"], root)
    (root / ".gitignore").write_text(".venv/\ndist/\n__pycache__/\n")
    demo.commit_all("initial")
    demo.git("push", "-q", "-u", "origin", "main")
    return demo


@pytest.fixture
def workspace_repo(tmp_path: Path) -> DemoRepo:
    """A uv workspace with members ``demo-a`` and ``demo-b`` under ``packages/``."""
    root = tmp_path / "ws"
    root.mkdir()
    origin = tmp_path / "ws-origin.git"
    demo = _init_repo(root, origin)
    run(["uv", "init", "--bare", "--name", "ws-root", "--vcs", "none", "."], root)
    run(["uv", "init", "--lib", "--name", "demo-a", "--vcs", "none", "packages/a"], root)
    run(["uv", "init", "--lib", "--name", "demo-b", "--vcs", "none", "packages/b"], root)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text()
    text += "\n[tool.uv]\npackage = false\n" + SHIP_TABLE
    pyproject.write_text(text)
    for member in ("demo-a", "demo-b"):
        run(["uv", "version", "--package", member, "0.0.0+dev", "--no-sync"], root)
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    run(["uv", "add", "--dev", "pytest", "--no-sync"], root)
    (root / ".gitignore").write_text(".venv/\ndist/\n__pycache__/\n")
    demo.commit_all("initial")
    demo.git("push", "-q", "-u", "origin", "main")
    return demo


_BUILD_SCRIPT = (
    "node -e \"const fs=require('fs');fs.mkdirSync('dist',{recursive:true});"
    "fs.writeFileSync('dist/index.js','export {}')\""
)


def _package_json(name: str, **extra) -> str:
    import json

    data = {
        "name": name,
        "version": "0.0.0+dev",
        "type": "module",
        "main": "dist/index.js",
        "files": ["dist"],
        "scripts": {"build": _BUILD_SCRIPT, "test": 'node -e "process.exit(0)"'},
        **extra,
    }
    return json.dumps(data, indent=2) + "\n"


@pytest.fixture
def pnpm_repo(tmp_path: Path) -> DemoRepo:
    """A pnpm workspace: ``@demo/core`` and ``@demo/plugin`` (peer on core), no uv project.

    The root ``pyproject.toml`` exists only to hold ``[tool.ship]``; it is not a package.
    """
    root = tmp_path / "pn"
    root.mkdir()
    origin = tmp_path / "pn-origin.git"
    demo = _init_repo(root, origin)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pn-tooling"\nversion = "0"\nrequires-python = ">=3.12"\n\n'
        "[tool.uv]\npackage = false\n" + SHIP_TABLE
    )
    (root / "package.json").write_text(
        '{\n  "name": "pn-root",\n  "private": true,\n  "version": "0.0.0",\n'
        '  "scripts": { "test": "pnpm -r test" }\n}\n'
    )
    (root / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
    demo.write("packages/core/package.json", _package_json("@demo/core"))
    demo.write(
        "packages/plugin/package.json",
        _package_json(
            "@demo/plugin",
            peerDependencies={"@demo/core": "workspace:>=0.0.0"},
            devDependencies={"@demo/core": "workspace:*"},
        ),
    )
    run(["pnpm", "install", "--offline"], root)
    (root / ".gitignore").write_text("node_modules/\ndist/\n")
    demo.commit_all("initial")
    demo.git("push", "-q", "-u", "origin", "main")
    return demo
