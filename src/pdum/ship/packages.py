"""Discover the publishable packages in a repository and read or write their versions.

Python packages come from the root ``pyproject.toml`` and the ``[tool.uv.workspace]``
members. npm packages come from ``pnpm-workspace.yaml`` and the ``package.json`` of the
pnpm workspace root, which is the repository root unless ``[tool.ship] pnpm-root`` names
a subdirectory. A package is one that can be published: a Python project with a ``[project]`` table
that uv does not mark ``package = false``, or an npm package that is not ``private``.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from .config import Config

Ecosystem = Literal["python", "npm"]


class PackageError(RuntimeError):
    """A package could not be discovered, read or written."""


@dataclass(frozen=True)
class Package:
    name: str
    ecosystem: Ecosystem
    dir: Path  # relative to the repository root
    manifest: Path  # relative to the repository root
    tag_component: str
    publishable: bool = True

    def tag(self, version: str) -> str:
        return f"{self.tag_component}-v{version}"

    @property
    def tag_glob(self) -> str:
        return f"{self.tag_component}-v*"


def unscoped(name: str) -> str:
    """``@scope/name`` becomes ``name``; other names are unchanged."""
    return name.rsplit("/", 1)[-1] if name.startswith("@") else name


# -- discovery -------------------------------------------------------------------


def discover(config: Config) -> list[Package]:
    """The release inventory; dependency operations use ``projects`` instead."""
    found = [p for p in projects(config) if p.publishable]
    names = [p.name for p in found]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise PackageError(f"package names are not unique: {sorted(duplicates)}")
    return found


def projects(config: Config) -> list[Package]:
    """Every workspace project, including private apps and virtual Python projects."""
    root = config.root
    found: list[Package] = []
    found.extend(_python_packages(root, config))
    found.extend(_npm_packages(root, config))
    return found


def _python_packages(root: Path, config: Config) -> list[Package]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return []
    data = tomllib.loads(pyproject.read_text())
    dirs: list[Path] = [root]
    workspace = data.get("tool", {}).get("uv", {}).get("workspace", {})
    members = workspace.get("members", [])
    excludes = workspace.get("exclude", [])
    excluded = {p.resolve() for pattern in excludes for p in root.glob(pattern)}
    for pattern in members:
        for member in sorted(root.glob(pattern)):
            if member.resolve() in excluded or not (member / "pyproject.toml").exists():
                continue
            dirs.append(member)

    packages: list[Package] = []
    for directory in dirs:
        manifest = directory / "pyproject.toml"
        member_data = tomllib.loads(manifest.read_text())
        project = member_data.get("project")
        if not project or "name" not in project:
            continue
        name = project["name"]
        packages.append(
            Package(
                name=name,
                ecosystem="python",
                dir=directory.relative_to(root),
                manifest=manifest.relative_to(root),
                tag_component=config.package(name).tag or name,
                publishable=member_data.get("tool", {}).get("uv", {}).get("package") is not False,
            )
        )
    return packages


def _npm_packages(root: Path, config: Config) -> list[Package]:
    dirs: list[Path] = []
    base = config.pnpm_dir
    if (base / "package.json").exists():
        dirs.append(base)
    workspace_file = base / "pnpm-workspace.yaml"
    if workspace_file.exists():
        workspace = yaml.safe_load(workspace_file.read_text()) or {}
        excluded = {
            p.resolve()
            for pattern in workspace.get("packages", []) or []
            if pattern.startswith("!")
            for p in base.glob(pattern[1:])
        }
        for pattern in workspace.get("packages", []) or []:
            negate = pattern.startswith("!")
            if negate:
                continue
            for member in sorted(base.glob(pattern)):
                if member.resolve() not in excluded and (member / "package.json").exists() and member not in dirs:
                    dirs.append(member)

    packages: list[Package] = []
    for directory in dirs:
        manifest = directory / "package.json"
        data = json.loads(manifest.read_text())
        name = data.get("name")
        if not name:
            continue
        packages.append(
            Package(
                name=name,
                ecosystem="npm",
                dir=directory.relative_to(root.resolve()),
                manifest=manifest.relative_to(root.resolve()),
                tag_component=config.package(name).tag or unscoped(name),
                publishable=not data.get("private", False),
            )
        )
    return packages


def find(packages: list[Package], name: str) -> Package:
    for package in packages:
        if package.name == name or package.tag_component == name:
            return package
    known = ", ".join(p.name for p in packages) or "none"
    raise PackageError(f"no package named {name!r}; known packages: {known}")


# -- versions --------------------------------------------------------------------

_JSON_VERSION_RE = re.compile(r'("version"\s*:\s*")([^"]*)(")')


def read_version(root: Path, package: Package) -> str:
    manifest = root / package.manifest
    if package.ecosystem == "python":
        project = tomllib.loads(manifest.read_text()).get("project", {})
        version = project.get("version")
        if version is None:
            raise PackageError(
                f"{package.manifest} has no static [project] version; ship needs a literal version to manage."
            )
        return version
    match = _JSON_VERSION_RE.search(manifest.read_text())
    if not match:
        raise PackageError(f'{package.manifest} has no "version" field')
    return match.group(2)


def write_version(root: Path, package: Package, version: str, *, relock: bool = True) -> None:
    """Set the manifest version. Python goes through ``uv version`` so ``uv.lock`` follows."""
    if package.ecosystem == "python":
        args = ["uv", "version", "--package", package.name, version, "--no-sync"]
        if not relock:
            args.append("--frozen")
        proc = subprocess.run(args, cwd=root, capture_output=True, text=True)
        if proc.returncode != 0:
            raise PackageError(f"uv version failed for {package.name}:\n{proc.stderr.strip()}")
        return
    manifest = root / package.manifest
    text = manifest.read_text()
    new_text, count = _JSON_VERSION_RE.subn(rf"\g<1>{version}\g<3>", text, count=1)
    if count != 1:
        raise PackageError(f'{package.manifest} has no "version" field')
    manifest.write_text(new_text)


def lockfile_for(config: Config, package: Package) -> Path | None:
    if package.ecosystem == "python":
        path = config.root / "uv.lock"
    else:
        path = config.pnpm_dir / "pnpm-lock.yaml"
    return path if path.exists() else None
