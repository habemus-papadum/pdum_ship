"""Dependency policy: refresh our packages in the lockfiles, and check a repository.

``refresh`` upgrades only the packages that match the ``ours`` patterns (or everything
with ``all=True``), re-installs with the lockfile, runs the tests and reports what moved.
``check`` reports violations of the policy described in README.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import Config
from .packages import Package, read_version
from .repo import Repo
from .versions import ZERO, split_dev

# -- lockfiles -------------------------------------------------------------------


def uv_lock_versions(root: Path) -> dict[str, str]:
    lock = root / "uv.lock"
    if not lock.exists():
        return {}
    data = tomllib.loads(lock.read_text())
    return {p["name"]: p.get("version", "") for p in data.get("package", []) if "name" in p}


def uv_lock_registry_packages(root: Path) -> set[str]:
    """Names in ``uv.lock`` that come from a registry, not from the workspace."""
    lock = root / "uv.lock"
    if not lock.exists():
        return set()
    data = tomllib.loads(lock.read_text())
    names: set[str] = set()
    for p in data.get("package", []):
        source = p.get("source", {})
        if "registry" in source:
            names.add(p["name"])
    return names


_PNPM_KEY_RE = re.compile(r"^(?P<name>@?[^@]+)@(?P<version>[^(]+)")


def pnpm_lock_versions(root: Path) -> dict[str, str]:
    lock = root / "pnpm-lock.yaml"
    if not lock.exists():
        return {}
    data = yaml.safe_load(lock.read_text()) or {}
    versions: dict[str, str] = {}
    for key in data.get("packages") or {}:
        match = _PNPM_KEY_RE.match(key)
        if match:
            versions[match.group("name")] = match.group("version")
    return versions


def lock_versions(root: Path) -> dict[str, str]:
    return {**uv_lock_versions(root), **pnpm_lock_versions(root)}


# -- refresh ---------------------------------------------------------------------


@dataclass(frozen=True)
class Moved:
    name: str
    before: str | None
    after: str | None


def refresh(
    repo: Repo,
    config: Config,
    packages: list[Package],
    *,
    everything: bool,
    run_tests: bool,
    dry_run: bool,
    say,
) -> list[Moved]:
    root = config.root
    before = lock_versions(root)
    workspace_names = {p.name for p in packages}

    commands: list[list[str]] = []
    if (root / "uv.lock").exists():
        if everything:
            commands.append(["uv", "lock", "--upgrade"])
        else:
            ours = sorted(n for n in uv_lock_registry_packages(root) if config.is_ours(n) and n not in workspace_names)
            if ours:
                args = ["uv", "lock"]
                for name in ours:
                    args += ["--upgrade-package", name]
                commands.append(args)
    if (root / "pnpm-lock.yaml").exists():
        if everything:
            commands.append(["pnpm", "update", "-r"])
        else:
            patterns = [p for p in config.ours if p.startswith("@") or "/" in p]
            if patterns:
                commands.append(["pnpm", "update", "-r", *patterns])

    if not commands:
        say("nothing to refresh: no lockfile holds one of our packages")
        return []
    for args in commands:
        say(f"  $ {' '.join(args)}" + ("  (dry run)" if dry_run else ""))
        if dry_run:
            continue
        proc = subprocess.run(args, cwd=root)
        if proc.returncode != 0:
            raise RuntimeError(f"command failed: {' '.join(args)}")
    if dry_run:
        return []

    if (root / "uv.lock").exists():
        subprocess.run(["uv", "sync", "--locked"], cwd=root, check=True)
    if (root / "pnpm-lock.yaml").exists():
        subprocess.run(["pnpm", "install", "--frozen-lockfile"], cwd=root, check=True)

    after = lock_versions(root)
    moved = [
        Moved(name, before.get(name), after.get(name))
        for name in sorted(set(before) | set(after))
        if before.get(name) != after.get(name)
    ]

    if run_tests and moved:
        seen: set[str] = set()
        for package in packages:
            from .release import default_tests  # local import: release imports deps

            for command in default_tests(config, package):
                if command in seen:
                    continue
                seen.add(command)
                say(f"  $ {command}")
                proc = subprocess.run(command, cwd=root, shell=True)
                if proc.returncode != 0:
                    raise RuntimeError(f"tests failed after refresh: {command}")
    return moved


# -- checks ----------------------------------------------------------------------


def _python_manifests(config: Config, packages: list[Package]) -> list[Path]:
    paths = {config.root / "pyproject.toml"}
    paths.update(config.root / p.manifest for p in packages if p.ecosystem == "python")
    return sorted(p for p in paths if p.exists())


def _npm_manifests(config: Config, packages: list[Package]) -> list[Path]:
    paths = {config.root / "package.json"}
    paths.update(config.root / p.manifest for p in packages if p.ecosystem == "npm")
    return sorted(p for p in paths if p.exists())


def dev_link_problems(config: Config, packages: list[Package]) -> list[str]:
    problems: list[str] = []
    for manifest in _python_manifests(config, packages):
        data = tomllib.loads(manifest.read_text())
        sources = data.get("tool", {}).get("uv", {}).get("sources", {})
        for name, spec in sources.items():
            specs = spec if isinstance(spec, list) else [spec]
            for s in specs:
                if isinstance(s, dict) and ("path" in s or s.get("editable")):
                    rel = manifest.relative_to(config.root)
                    problems.append(f"{rel}: [tool.uv.sources] {name} is a path/editable link")
    for manifest in _npm_manifests(config, packages):
        data = json.loads(manifest.read_text())
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            for name, spec in (data.get(section) or {}).items():
                if isinstance(spec, str) and (spec.startswith("link:") or spec.startswith("file:")):
                    rel = manifest.relative_to(config.root)
                    problems.append(f"{rel}: {section} {name} = {spec!r} is a development link")
    return problems


def lock_problems(config: Config) -> list[str]:
    problems: list[str] = []
    root = config.root
    if (root / "uv.lock").exists():
        proc = subprocess.run(["uv", "lock", "--check"], cwd=root, capture_output=True, text=True)
        if proc.returncode != 0:
            problems.append("uv.lock is not current with pyproject.toml; run `uv lock`")
    if (root / "pnpm-lock.yaml").exists():
        proc = subprocess.run(
            ["pnpm", "install", "--frozen-lockfile", "--lockfile-only"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            problems.append("pnpm-lock.yaml is not current with the manifests; run `pnpm install`")
    return problems


_REQ_RE = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(?P<spec>.*)$")


def _python_requirements(data: dict) -> list[str]:
    reqs: list[str] = list(data.get("project", {}).get("dependencies", []))
    for extra in data.get("project", {}).get("optional-dependencies", {}).values():
        reqs.extend(extra)
    for group in data.get("dependency-groups", {}).values():
        reqs.extend(r for r in group if isinstance(r, str))
    return reqs


def pin_problems(config: Config, packages: list[Package]) -> list[str]:
    problems: list[str] = []
    for manifest in _python_manifests(config, packages):
        text = manifest.read_text()
        data = tomllib.loads(text)
        rel = manifest.relative_to(config.root)
        for req in _python_requirements(data):
            match = _REQ_RE.match(req)
            if not match or not config.is_ours(match.group("name")):
                continue
            spec = match.group("spec")
            name = match.group("name")
            if "==" in spec:
                line = next((ln for ln in text.splitlines() if req in ln), "")
                commented = "#" in line.split(req, 1)[-1] if line else False
                if not commented and name not in config.pins:
                    problems.append(
                        f"{rel}: {req!r} pins one of our packages without a reason "
                        f"(add a comment on that line or [tool.ship.pins])"
                    )
    for manifest in _npm_manifests(config, packages):
        data = json.loads(manifest.read_text())
        rel = manifest.relative_to(config.root)
        for section in ("dependencies", "devDependencies"):
            for name, spec in (data.get(section) or {}).items():
                if not config.is_ours(name) or not isinstance(spec, str):
                    continue
                if re.match(r"^\d", spec) and name not in config.pins:
                    problems.append(
                        f"{rel}: {section} {name} = {spec!r} pins one of our packages "
                        f"without a reason in [tool.ship.pins]"
                    )
    return problems


def peer_problems(config: Config, packages: list[Package]) -> list[str]:
    problems: list[str] = []
    for manifest in _npm_manifests(config, packages):
        data = json.loads(manifest.read_text())
        rel = manifest.relative_to(config.root)
        for name, spec in (data.get("peerDependencies") or {}).items():
            if not config.is_ours(name) or not isinstance(spec, str):
                continue
            range_part = spec.removeprefix("workspace:")
            if range_part in ("*", "^", "~") or range_part.startswith(("^", "~")):
                problems.append(
                    f"{rel}: peerDependencies {name} = {spec!r} is narrow; use '>=X.Y.0' or 'workspace:>=X.Y.0'"
                )
    return problems


def marker_problems(repo: Repo, config: Config, packages: list[Package]) -> list[str]:
    from .release import last_release  # local import: release imports deps

    problems: list[str] = []
    for package in packages:
        tag, last = last_release(repo, package)
        current = read_version(config.root, package)
        expected = (last or ZERO).dev
        base, is_dev = split_dev(current)
        if not is_dev or (last is not None and base != str(last)):
            problems.append(
                f"{package.name}: manifest says {current!r}; expected {expected!r} (last tag {tag or 'none'})"
            )
    return problems


def check(repo: Repo, config: Config, packages: list[Package], *, hook: bool) -> list[str]:
    from . import skills  # local import: skills reads package metadata

    problems = dev_link_problems(config, packages)
    problems += lock_problems(config)
    # Cheap file comparisons; in the hook they catch an edited copy before it is committed.
    try:
        problems += skills.problems(config.root)
    except skills.SkillsError as exc:
        problems.append(str(exc))
    if hook:
        return problems
    problems += pin_problems(config, packages)
    problems += peer_problems(config, packages)
    problems += marker_problems(repo, config, packages)
    if config.coupled is not None and not config.coupled.strip():
        problems.append("[tool.ship] coupled must give the reason, not an empty string")
    return problems
