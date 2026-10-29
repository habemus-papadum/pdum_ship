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


def pnpm_lock_versions(pnpm_dir: Path) -> dict[str, str]:
    lock = pnpm_dir / "pnpm-lock.yaml"
    if not lock.exists():
        return {}
    data = yaml.safe_load(lock.read_text()) or {}
    versions: dict[str, str] = {}
    for key in data.get("packages") or {}:
        match = _PNPM_KEY_RE.match(key)
        if match:
            versions[match.group("name")] = match.group("version")
    return versions


def lock_versions(config: Config) -> dict[str, str]:
    return {**uv_lock_versions(config.root), **pnpm_lock_versions(config.pnpm_dir)}


# -- refresh ---------------------------------------------------------------------


@dataclass(frozen=True)
class Moved:
    name: str
    before: str | None
    after: str | None

    @property
    def kind(self) -> str:
        """``added``, ``removed``, ``upgraded`` or ``downgraded``."""
        if self.before is None:
            return "added"
        if self.after is None:
            return "removed"
        return "downgraded" if _version_key(self.after) < _version_key(self.before) else "upgraded"


def _version_key(text: str) -> tuple:
    """Order versions by their leading numeric components; good enough to spot a downgrade."""
    parts: list[int] = []
    for piece in re.split(r"[.+-]", text):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    return tuple(parts)


class RefreshError(RuntimeError):
    """A refresh did not complete; the lockfiles are back as they were."""


def _pnpm_lock(config: Config) -> Path:
    return config.root / config.pnpm_root / "pnpm-lock.yaml"


def _lockfiles(config: Config) -> list[Path]:
    return [p for p in (config.root / "uv.lock", _pnpm_lock(config)) if p.exists()]


def _moved(before: dict[str, str], after: dict[str, str]) -> list[Moved]:
    return [
        Moved(name, before.get(name), after.get(name))
        for name in sorted(set(before) | set(after))
        if before.get(name) != after.get(name)
    ]


def _table(moved: list[Moved]) -> str:
    return "\n".join(f"  {m.name}\t{m.before or '-'}\t{m.after or '-'}\t{m.kind}" for m in moved)


def refresh(
    repo: Repo,
    config: Config,
    packages: list[Package],
    *,
    everything: bool,
    run_tests: bool,
    dry_run: bool,
    allow_downgrades: bool,
    say,
) -> list[Moved]:
    """Upgrade our packages (or everything), reinstall, test, and report what moved.

    In the house-only mode a third-party package that is downgraded or removed is treated
    as an error: upgrading one of our packages can add or raise a third-party requirement,
    but it cannot legitimately lower one, so such a move means the resolver did something
    the operator should look at. ``allow_downgrades`` accepts it. Any failure restores the
    lockfiles to their committed state.
    """
    root = config.root
    before = lock_versions(config)
    workspace_names = {p.name for p in packages}
    lockfiles = [p.relative_to(root) for p in _lockfiles(config)]

    def restore(message: str) -> RefreshError:
        if lockfiles:
            repo.checkout_paths(lockfiles)
        return RefreshError(message + "\nThe lockfiles are back as they were.")

    # Each command runs in the directory of the lockfile it moves.
    commands: list[tuple[list[str], Path]] = []
    if (root / "uv.lock").exists():
        if everything:
            commands.append((["uv", "lock", "--upgrade"], root))
        else:
            ours = sorted(n for n in uv_lock_registry_packages(root) if config.is_ours(n) and n not in workspace_names)
            if ours:
                # --refresh: a cached index page can be older than the release being
                # refreshed to; --upgrade-package alone reuses it.
                args = ["uv", "lock", "--refresh"]
                for name in ours:
                    args += ["--upgrade-package", name]
                commands.append((args, root))
    if _pnpm_lock(config).exists():
        if everything:
            commands.append((["pnpm", "update", "-r"], config.pnpm_dir))
        else:
            patterns = [p for p in config.ours if p.startswith("@") or "/" in p]
            if patterns:
                commands.append((["pnpm", "update", "-r", *patterns], config.pnpm_dir))

    if not commands:
        say("nothing to refresh: no lockfile holds one of our packages")
        return []
    for args, cwd in commands:
        say(f"  $ {' '.join(args)}" + ("  (dry run)" if dry_run else ""))
        if dry_run:
            continue
        proc = subprocess.run(args, cwd=cwd)
        if proc.returncode != 0:
            raise restore(f"command failed: {' '.join(args)}")
    if dry_run:
        return []

    moved = _moved(before, lock_versions(config))
    if not moved:
        return []

    if not everything:
        suspicious = [m for m in moved if not config.is_ours(m.name) and m.kind in ("downgraded", "removed")]
        if suspicious and not allow_downgrades:
            raise restore(
                "refreshing our packages also downgraded or removed third-party packages, which an "
                "upgrade of ours cannot require:\n"
                + _table(suspicious)
                + "\nAll moves:\n"
                + _table(moved)
                + "\nPass --allow-downgrades if this is intended (for example, one of our packages added "
                "an upper bound), or investigate the resolution."
            )

    for args, cwd, lock in (
        (["uv", "sync", "--locked"], root, root / "uv.lock"),
        (["pnpm", "install", "--frozen-lockfile"], config.pnpm_dir, _pnpm_lock(config)),
    ):
        if not lock.exists():
            continue
        say(f"  $ {' '.join(args)}")
        proc = subprocess.run(args, cwd=cwd)
        if proc.returncode != 0:
            raise restore(f"install failed after the lockfile moved:\n{_table(moved)}\n  $ {' '.join(args)}")

    if run_tests:
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
                    raise restore(f"tests failed after the lockfile moved:\n{_table(moved)}\n  $ {command}")
    return moved


# -- checks ----------------------------------------------------------------------


def _python_manifests(config: Config, packages: list[Package]) -> list[Path]:
    paths = {config.root / "pyproject.toml"}
    paths.update(config.root / p.manifest for p in packages if p.ecosystem == "python")
    return sorted(p for p in paths if p.exists())


def _npm_manifests(config: Config, packages: list[Package]) -> list[Path]:
    paths = {config.root / config.pnpm_root / "package.json"}
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
    if _pnpm_lock(config).exists():
        proc = subprocess.run(
            ["pnpm", "install", "--frozen-lockfile", "--lockfile-only"],
            cwd=config.pnpm_dir,
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
            # The environment marker after ";" may contain "==" (sys_platform == 'linux').
            match = _REQ_RE.match(req.split(";", 1)[0])
            if not match or not config.is_ours(match.group("name")):
                continue
            spec = match.group("spec")
            name = match.group("name")
            narrow = "==" in spec or "~=" in spec
            if narrow:
                line = next((ln for ln in text.splitlines() if req in ln), "")
                commented = "#" in line.split(req, 1)[-1] if line else False
                if not commented and name not in config.pins:
                    what = "pins" if "==" in spec else "caps (~=)"
                    problems.append(
                        f"{rel}: {req!r} {what} one of our packages without a reason "
                        f"(add a comment on that line or [tool.ship.pins]); the policy is a floor"
                    )
    for manifest in _npm_manifests(config, packages):
        data = json.loads(manifest.read_text())
        rel = manifest.relative_to(config.root)
        for section in ("dependencies", "devDependencies"):
            for name, spec in (data.get(section) or {}).items():
                if not config.is_ours(name) or not isinstance(spec, str):
                    continue
                range_part = spec.removeprefix("workspace:")
                if re.match(r"^\d", range_part) and name not in config.pins:
                    problems.append(
                        f"{rel}: {section} {name} = {spec!r} pins one of our packages "
                        f"without a reason in [tool.ship.pins]"
                    )
                elif range_part.startswith(("^", "~")) and name not in config.pins:
                    problems.append(
                        f"{rel}: {section} {name} = {spec!r} is narrow; the policy is a floor "
                        f"('>=X.Y.0' or 'workspace:>=X.Y.0'), or give a reason in [tool.ship.pins]"
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
    # Tags made by a release elsewhere do not arrive with `git pull`; the marker check needs them.
    repo.fetch_tags(config.remote)
    problems += marker_problems(repo, config, packages)
    if config.coupled is not None and not config.coupled.strip():
        problems.append("[tool.ship] coupled must give the reason, not an empty string")
    return problems
