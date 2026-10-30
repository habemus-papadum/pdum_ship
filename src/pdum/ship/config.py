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

    versioning: bool | None = None
    tag: str | None = None
    test: list[str] | None = None
    publish: bool = True
    # ``build = false``: ship writes the version, tags and pushes, and another workflow
    # builds and publishes from the tag (platform wheels). Implies ``publish = false``.
    build: bool = True
    # Shell commands run from the repository root before ship's own build step.
    before_build: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LocalDependency:
    """One intentional npm local dependency, matched without globs."""

    manifest: str
    dependency: str
    spec: str
    reason: str


@dataclass(frozen=True)
class Config:
    root: Path
    ours: list[str]
    versioning: bool = True
    local_dependencies: list[LocalDependency] = field(default_factory=list)
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
    python_index: str | None = None
    index_url: str | None = None

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

    def versions(self, name: str) -> bool:
        override = self.package(name).versioning
        return self.versioning if override is None else override

    def allows_local(self, manifest: Path, dependency: str, spec: str) -> bool:
        relative = manifest.relative_to(self.root).as_posix()
        return any(
            (entry.manifest, entry.dependency, entry.spec) == (relative, dependency, spec)
            for entry in self.local_dependencies
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

    versioning = table.get("versioning", True)
    if not isinstance(versioning, bool):
        raise ConfigError("[tool.ship] `versioning` must be true or false.")
    local_dependencies: list[LocalDependency] = []
    entries = table.get("local-dependencies", [])
    if not isinstance(entries, list):
        raise ConfigError("[[tool.ship.local-dependencies]] must be an array of tables.")
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        keys = {"manifest", "dependency", "spec", "reason"}
        if (
            not isinstance(entry, dict)
            or set(entry) != keys
            or not all(isinstance(entry[key], str) and entry[key].strip() for key in keys)
        ):
            raise ConfigError(
                "Each local-dependencies entry needs exactly manifest, dependency, spec and reason strings."
            )
        manifest = Path(entry["manifest"])
        if manifest.is_absolute() or ".." in manifest.parts or manifest.name != "package.json":
            raise ConfigError("A local-dependencies manifest must name a repository-relative package.json.")
        if manifest.as_posix() != entry["manifest"] or any(c in entry["manifest"] for c in "*?[]"):
            raise ConfigError("A local-dependencies manifest must be a normalized exact path, without globs.")
        if not entry["spec"].startswith(("file:", "link:")) or not entry["spec"].partition(":")[2]:
            raise ConfigError("A local-dependencies spec must be an exact file: or link: dependency.")
        identity = (entry["manifest"], entry["dependency"], entry["spec"])
        if identity in seen:
            raise ConfigError("Duplicate local-dependencies entry.")
        seen.add(identity)
        local_dependencies.append(LocalDependency(**entry))

    coupled = table.get("coupled")
    if coupled is not None and not isinstance(coupled, str):
        raise ConfigError("[tool.ship] `coupled` must be a string giving the reason, or absent.")

    packages: dict[str, PackageConfig] = {}
    for name, raw in table.get("packages", {}).items():
        if not isinstance(raw, dict):
            raise ConfigError(f'[tool.ship.packages."{name}"] must be a table.')
        package_versioning = raw.get("versioning")
        if "versioning" in raw and not isinstance(package_versioning, bool):
            raise ConfigError(f'[tool.ship.packages."{name}"] `versioning` must be true or false.')
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
            versioning=package_versioning,
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
    for key in ("python-index", "index-url"):
        if key in table and (not isinstance(table[key], str) or not table[key].strip()):
            raise ConfigError(f"[tool.ship] {key} must be a non-empty string")
    if table.get("python-index") and table.get("index-url"):
        raise ConfigError("choose python-index or index-url, not both")

    return Config(
        root=root,
        ours=list(ours),
        versioning=versioning,
        local_dependencies=local_dependencies,
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
        python_index=table.get("python-index"),
        index_url=table.get("index-url"),
    )
