"""``[tool.ship]`` in the repository's root ``pyproject.toml``.

Every repository that uses ship has a root ``pyproject.toml``, even a pnpm-only one,
because that file installs ship and holds this table.
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """``[tool.ship]`` is missing or malformed."""


@dataclass(frozen=True)
class PackageConfig:
    """Per-package overrides under ``[tool.ship.packages."<name>"]``."""

    tag: str | None = None
    test: list[str] | None = None
    publish: bool = True
    # ``build = false``: ship writes the version, tags and pushes, and another workflow
    # builds and publishes from the tag (platform wheels). Implies ``publish = false``.
    build: bool = True
    # Shell commands run from the repository root before ship's own build step.
    before_build: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Config:
    root: Path
    ours: list[str]
    shared_paths: list[str] = field(default_factory=list)
    ignore_paths: list[str] = field(default_factory=list)
    ignore_subjects: list[str] = field(default_factory=list)
    coupled: str | None = None
    branch: str = "main"
    remote: str = "origin"
    github_release: bool = True
    test: list[str] | None = None
    pins: dict[str, str] = field(default_factory=dict)
    packages: dict[str, PackageConfig] = field(default_factory=dict)
    # Directory of the pnpm workspace relative to the repository root; "." by default.
    pnpm_root: str = "."

    @property
    def pnpm_dir(self) -> Path:
        return (self.root / self.pnpm_root).resolve()

    def is_pnpm_root(self, directory: Path) -> bool:
        """Whether ``directory`` (relative to the root) is the pnpm workspace root."""
        return (self.root / directory).resolve() == self.pnpm_dir

    def is_ours(self, name: str) -> bool:
        """Whether a package name matches one of the ``ours`` patterns.

        Python distribution names compare case-insensitively with ``-`` and ``_`` equal.
        """
        candidates = {name, _normalize(name)}
        return any(
            fnmatch.fnmatchcase(candidate, pattern) or fnmatch.fnmatchcase(candidate, _normalize(pattern))
            for candidate in candidates
            for pattern in self.ours
        )

    def package(self, name: str) -> PackageConfig:
        return self.packages.get(name, PackageConfig())


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def load(root: Path) -> Config:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise ConfigError(f"{pyproject} does not exist. ship needs a root pyproject.toml with a [tool.ship] table.")
    data = tomllib.loads(pyproject.read_text())
    table = data.get("tool", {}).get("ship")
    if table is None:
        raise ConfigError(f"{pyproject} has no [tool.ship] table.")
    ours = table.get("ours")
    if not isinstance(ours, list) or not ours:
        raise ConfigError("[tool.ship] needs `ours`, a non-empty list of package name patterns.")

    coupled = table.get("coupled")
    if coupled is not None and not isinstance(coupled, str):
        raise ConfigError("[tool.ship] `coupled` must be a string giving the reason, or absent.")

    packages: dict[str, PackageConfig] = {}
    for name, raw in table.get("packages", {}).items():
        if not isinstance(raw, dict):
            raise ConfigError(f'[tool.ship.packages."{name}"] must be a table.')
        build = raw.get("build", True)
        if not isinstance(build, bool):
            raise ConfigError(f'[tool.ship.packages."{name}"] `build` must be true or false.')
        if build is False and raw.get("publish") is True:
            raise ConfigError(
                f'[tool.ship.packages."{name}"] has build = false; ship cannot publish what it does not build.'
            )
        before_build = raw.get("before-build", [])
        if not isinstance(before_build, list) or not all(isinstance(c, str) for c in before_build):
            raise ConfigError(f'[tool.ship.packages."{name}"] `before-build` must be a list of shell commands.')
        packages[name] = PackageConfig(
            tag=raw.get("tag"),
            test=raw.get("test"),
            publish=raw.get("publish", True) and build,
            build=build,
            before_build=list(before_build),
        )

    pins = table.get("pins", {})
    if not isinstance(pins, dict) or not all(isinstance(v, str) for v in pins.values()):
        raise ConfigError("[tool.ship.pins] maps a package name to the reason it is pinned.")

    pnpm_root = table.get("pnpm-root", ".")
    if not isinstance(pnpm_root, str) or not pnpm_root:
        raise ConfigError("[tool.ship] `pnpm-root` is the pnpm workspace directory relative to the repository root.")
    if not (root / pnpm_root).is_dir():
        raise ConfigError(f"[tool.ship] pnpm-root = {pnpm_root!r} is not a directory under {root}.")

    return Config(
        root=root,
        ours=list(ours),
        shared_paths=list(table.get("shared-paths", [])),
        ignore_paths=list(table.get("ignore-paths", [])),
        ignore_subjects=list(table.get("ignore-subjects", [])),
        coupled=coupled,
        branch=table.get("branch", "main"),
        remote=table.get("remote", "origin"),
        github_release=bool(table.get("github-release", True)),
        test=table.get("test"),
        pins=dict(pins),
        packages=packages,
        pnpm_root=pnpm_root,
    )
