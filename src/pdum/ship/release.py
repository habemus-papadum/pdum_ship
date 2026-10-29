"""The release flow: plan from the tags, then execute step by step.

Order of an execution, for each package in the plan:

1. write the release version into the manifest (and re-lock);
2. run the package's ``before-build`` commands, then build and check the built files
   carry no ``+dev`` (a package with ``build = false`` is neither built nor published here);
3. run the tests;
4. publish;
5. commit, tag ``<component>-v<version>``, push commit and tags atomically;
6. create a GitHub release when ``gh`` is available;
7. write ``<version>+dev`` back, re-lock, commit, push.

Up to step 4 every failure is reverted with ``git checkout``. After publishing, nothing is
reverted: the registry already has the version, so the remaining steps are printed as
recovery instructions instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import deps, registry
from .config import Config
from .packages import Package, lockfile_for, read_version, write_version
from .repo import GitError, Repo
from .versions import ZERO, Bump, Version, VersionError, split_dev

Say = Callable[[str], None]


class ReleaseError(RuntimeError):
    """The release cannot proceed; the message says why and what to do."""


@dataclass
class PackagePlan:
    package: Package
    last_tag: str | None
    last_release: Version | None
    manifest_version: str
    version: Version
    commits: list[str]

    @property
    def tag(self) -> str:
        return self.package.tag(str(self.version))


@dataclass
class ReleasePlan:
    items: list[PackagePlan]
    coupled: bool = False
    warnings: list[str] = field(default_factory=list)


# -- reading the tags ------------------------------------------------------------


def last_release(repo: Repo, package: Package) -> tuple[str | None, Version | None]:
    prefix = f"{package.tag_component}-v"
    best: tuple[Version, str] | None = None
    for tag in repo.tags(package.tag_glob):
        try:
            version = Version.parse(tag[len(prefix) :])
        except VersionError:
            continue
        if best is None or version > best[0]:
            best = (version, tag)
    if best is None:
        return None, None
    return best[1], best[0]


def pathspecs(config: Config, package: Package) -> list[str]:
    """What counts as a change to this package: its directory and the shared paths."""
    specs = [str(package.dir) if str(package.dir) != "." else "."]
    specs.extend(f":(glob){p}" for p in config.shared_paths)
    specs.extend(f":(exclude,glob){p}" for p in config.ignore_paths)
    return specs


RESTORE_SUBJECT = "back to development: "


def _commits_touching(repo: Repo, config: Config, package: Package, since: str | None) -> list[str]:
    """Commits after ``since`` that count as a change to the package.

    ship's own restore commit touches the manifest and the lockfile after every release;
    it is recognised by its subject and left out.
    """
    prefixes = (RESTORE_SUBJECT, *config.ignore_subjects)
    lines = repo.commits_since(since, pathspecs(config, package))
    return [line for line in lines if not line.split(" ", 1)[-1].startswith(prefixes)]


def commits_since_release(repo: Repo, config: Config, package: Package) -> list[str]:
    tag, _ = last_release(repo, package)
    return _commits_touching(repo, config, package, tag)


# -- preflight -------------------------------------------------------------------


def preflight(
    repo: Repo, config: Config, packages: list[Package], *, check_remote: bool, allow_dirty: bool = False
) -> list[str]:
    """``allow_dirty`` is for a dry run: it writes nothing, so uncommitted changes do not matter."""
    problems: list[str] = []
    if not allow_dirty and not repo.is_clean():
        problems.append("the working tree has uncommitted changes:\n" + repo.status_porcelain())
    branch = repo.current_branch()
    if branch != config.branch:
        problems.append(f"on branch {branch!r}; releases happen on {config.branch!r}")
    problems.extend(deps.dev_link_problems(config, packages))
    problems.extend(deps.lock_problems(config))
    if check_remote:
        if not repo.has_remote(config.remote):
            problems.append(f"remote {config.remote!r} is not configured")
        else:
            try:
                repo.fetch(config.remote, config.branch)
                repo.fetch_tags(config.remote)
            except GitError as exc:
                problems.append(str(exc))
            else:
                remote_head = repo.rev_parse(f"{config.remote}/{config.branch}")
                if remote_head != repo.head():
                    problems.append(f"HEAD is not {config.remote}/{config.branch}; pull or push first")
    return problems


# -- planning --------------------------------------------------------------------


def _plan_one(
    repo: Repo,
    config: Config,
    package: Package,
    *,
    bump: Bump | None,
    version: Version | None,
    registry_check: bool,
) -> PackagePlan:
    tag, last = last_release(repo, package)
    manifest_version = read_version(config.root, package)
    base, is_dev = split_dev(manifest_version)
    expected = (last or ZERO).dev
    if not is_dev:
        raise ReleaseError(
            f"{package.name} manifest says {manifest_version!r}; between releases it must say "
            f"{expected!r}. ship owns version fields; set it back with "
            f"`uv version --package {package.name} {expected}` or edit package.json."
        )
    if last is not None and base != str(last):
        raise ReleaseError(
            f"{package.name} manifest says {manifest_version!r} but the last tag is {tag}; expected {expected!r}."
        )
    if version is None:
        if bump is None:
            raise ReleaseError("give --bump patch|minor|major or --version X.Y.Z")
        version = (last or ZERO).bump(bump)
    if last is not None and version <= last:
        raise ReleaseError(f"{version} is not after the last release {last} of {package.name}")
    if registry_check:
        exists = registry.has(package.ecosystem, package.name, str(version))
        if exists is True:
            raise ReleaseError(
                f"the registry already has {package.name} {version}; "
                "tag it instead if that upload came from an interrupted release"
            )
        if exists is None:
            raise ReleaseError(
                f"could not ask the registry about {package.name} {version}; "
                "pass --no-registry-check to proceed without the answer"
            )
    commits = _commits_touching(repo, config, package, tag)
    return PackagePlan(package, tag, last, manifest_version, version, commits)


def plan(
    repo: Repo,
    config: Config,
    packages: list[Package],
    *,
    selected: Package | None,
    bump: Bump | None,
    version: Version | None,
    registry_check: bool,
) -> ReleasePlan:
    if config.coupled is None:
        if selected is None:
            raise ReleaseError("name the package to release; this repository is not coupled")
        return ReleasePlan(
            [_plan_one(repo, config, selected, bump=bump, version=version, registry_check=registry_check)]
        )

    # Coupled: every package moves to one version, computed from the newest last release.
    if selected is not None:
        raise ReleaseError(f"this repository is coupled ({config.coupled}); `ship release` releases every package")
    targets = [p for p in packages if config.package(p.name).publish]
    if not targets:
        raise ReleaseError("no publishable packages found")
    if version is None:
        if bump is None:
            raise ReleaseError("give --bump patch|minor|major or --version X.Y.Z")
        newest = max((last_release(repo, p)[1] or ZERO for p in targets), default=ZERO)
        version = newest.bump(bump)
    items = [_plan_one(repo, config, p, bump=None, version=version, registry_check=registry_check) for p in targets]
    return ReleasePlan(items, coupled=True)


# -- execution -------------------------------------------------------------------


def _run(args: list[str], cwd: Path, say: Say) -> None:
    say(f"  $ {' '.join(args)}")
    proc = subprocess.run(args, cwd=cwd)
    if proc.returncode != 0:
        raise ReleaseError(f"command failed ({proc.returncode}): {' '.join(args)}")


def _run_shell(command: str, cwd: Path, say: Say) -> None:
    say(f"  $ {command}")
    proc = subprocess.run(command, cwd=cwd, shell=True)
    if proc.returncode != 0:
        raise ReleaseError(f"command failed ({proc.returncode}): {command}")


def _pnpm(config: Config) -> list[str]:
    """``pnpm`` as run from the repository root, entering the workspace when it is nested."""
    return ["pnpm"] if config.pnpm_root == "." else ["pnpm", "-C", config.pnpm_root]


def default_tests(config: Config, package: Package) -> list[str]:
    override = config.package(package.name).test
    if override is not None:
        return override
    if config.test is not None:
        return config.test
    if package.ecosystem == "python":
        return ["uv run --locked pytest"]
    pnpm = " ".join(_pnpm(config))
    if config.is_pnpm_root(package.dir):
        return [f"{pnpm} test"]
    return [f"{pnpm} --filter {package.name} test"]


def _version_files(config: Config, package: Package) -> list[Path]:
    files = [config.root / package.manifest]
    lock = lockfile_for(config, package)
    if lock is not None:
        files.append(lock)
    return files


def _npm_has_script(root: Path, package: Package, script: str) -> bool:
    import json

    data = json.loads((root / package.manifest).read_text())
    return script in (data.get("scripts") or {})


def _build(config: Config, item: PackagePlan, say: Say) -> list[Path]:
    package = item.package
    settings = config.package(package.name)
    for command in settings.before_build:
        _run_shell(command, config.root, say)
    if not settings.build:
        say(f"→ {package.name} is marked build = false; another workflow builds and publishes it from the tag")
        return []
    if package.ecosystem == "npm":
        # Build the package and the workspace packages it depends on; pnpm publish packs
        # whatever `files` names, so the build must come first.
        if _npm_has_script(config.root, package, "build"):
            if config.is_pnpm_root(package.dir):
                _run(["pnpm", "run", "build"], config.pnpm_dir, say)
            else:
                _run(["pnpm", "--filter", f"{package.name}...", "run", "build"], config.pnpm_dir, say)
        return []
    out_dir = config.root / "dist" / package.tag_component
    if out_dir.exists():
        shutil.rmtree(out_dir)
    _run(
        ["uv", "build", "--package", package.name, "--out-dir", str(out_dir)],
        config.root,
        say,
    )
    # uv build also writes a .gitignore into the output directory; only distributions count.
    files = sorted(f for f in out_dir.iterdir() if f.suffix == ".whl" or f.name.endswith(".tar.gz"))
    if not files:
        raise ReleaseError(f"uv build produced no distribution in {out_dir}")
    expected = f"-{item.version}"
    bad = [f.name for f in files if expected not in f.name or "dev" in f.name]
    if bad:
        raise ReleaseError(f"built files do not carry the release version {item.version}: {bad}")
    return files


def _publish(config: Config, item: PackagePlan, files: list[Path], say: Say) -> None:
    package = item.package
    if package.ecosystem == "python":
        _run(["uv", "publish", *(str(f) for f in files)], config.root, say)
        return
    # `--filter` needs the recursive form; a bare `pnpm publish` publishes the root package.
    if config.is_pnpm_root(package.dir):
        args = ["pnpm", "publish", "--no-git-checks", "--access", "public"]
    else:
        args = ["pnpm", "-r", "publish", "--filter", package.name, "--no-git-checks", "--access", "public"]
    # In GitHub Actions with `id-token: write`, attach a provenance attestation. Publishing
    # still authenticates with the token; the attestation only records where the build ran.
    if os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"):
        args.append("--provenance")
    _run(args, config.pnpm_dir, say)


def execute(
    repo: Repo,
    config: Config,
    release_plan: ReleasePlan,
    *,
    publish: bool,
    push: bool,
    run_tests: bool,
    say: Say,
) -> None:
    root = config.root
    items = release_plan.items
    touched: list[Path] = []
    for item in items:
        touched.extend(_version_files(config, item.package))
    touched = sorted(set(touched))

    try:
        built: dict[str, list[Path]] = {}
        for item in items:
            say(f"→ {item.package.name}: {item.manifest_version} → {item.version}")
            write_version(root, item.package, str(item.version))
            built[item.package.name] = _build(config, item, say)
            if read_version(root, item.package) != str(item.version):
                raise ReleaseError(f"{item.package.manifest} did not take version {item.version}")

        if run_tests:
            seen: set[str] = set()
            for item in items:
                for command in default_tests(config, item.package):
                    if command in seen:
                        continue
                    seen.add(command)
                    say(f"→ tests for {item.package.name}")
                    _run_shell(command, root, say)
        else:
            say("→ tests skipped (--skip-tests)")
    except ReleaseError:
        say("✗ reverting version changes")
        repo.checkout_paths([p.relative_to(root) for p in touched if p.exists()])
        raise

    if publish:
        for item in items:
            if not config.package(item.package.name).publish:
                say(f"→ {item.package.name} is marked publish = false; not uploaded")
                continue
            say(f"→ publishing {item.package.name} {item.version}")
            try:
                _publish(config, item, built[item.package.name], say)
            except ReleaseError:
                say("✗ publish failed; reverting version changes (nothing was pushed)")
                repo.checkout_paths([p.relative_to(root) for p in touched if p.exists()])
                raise
    else:
        say("→ publish skipped (--no-publish)")

    # From here on the registry may already hold the version: never revert, only report.
    summary = ", ".join(f"{i.package.name} {i.version}" for i in items)
    repo.add([p.relative_to(root) for p in touched if p.exists()])
    repo.commit(summary)
    for item in items:
        repo.tag(item.tag, f"{item.package.name} {item.version}")
    tags = [item.tag for item in items]
    say(f"→ committed and tagged: {', '.join(tags)}")

    if push:
        try:
            refs = [f"HEAD:refs/heads/{config.branch}", *(f"refs/tags/{t}" for t in tags)]
            say(f"  $ git push --atomic {config.remote} {' '.join(refs)}")
            repo.git("push", "--quiet", "--atomic", config.remote, *refs)
        except GitError as exc:
            raise ReleaseError(
                f"{exc}\nThe release is published and tagged locally. Recover with:\n"
                f"  git push --atomic {config.remote} HEAD:refs/heads/{config.branch} "
                + " ".join(f"refs/tags/{t}" for t in tags)
                + "\nthen run the development-version restore by hand (see README)."
            ) from exc
        if config.github_release and shutil.which("gh"):
            for item in items:
                args = [
                    "gh",
                    "release",
                    "create",
                    item.tag,
                    "--verify-tag",
                    "--generate-notes",
                    "--title",
                    f"{item.package.name} {item.version}",
                ]
                args += [str(f) for f in built.get(item.package.name, [])]
                say(f"  $ {' '.join(args)}")
                proc = subprocess.run(args, cwd=root)
                if proc.returncode != 0:
                    say(f"! GitHub release for {item.tag} failed; the tag and package are published")
    else:
        say("→ push skipped (--no-push); the commit and tags are local")

    for item in items:
        write_version(root, item.package, item.version.dev)
    repo.add([p.relative_to(root) for p in touched if p.exists()])
    repo.commit(RESTORE_SUBJECT + ", ".join(i.version.dev for i in items))
    if push:
        repo.push(config.remote, config.branch)
    say("→ development versions restored")

    for item in items:
        say("")
        say(f"{item.package.name} {item.version} ({item.tag}); since {item.last_tag or 'the start'}:")
        for line in item.commits or ["  (no commits touched this package)"]:
            say(f"  {line}")


def describe(release_plan: ReleasePlan, say: Say) -> None:
    for item in release_plan.items:
        say(f"{item.package.name} ({item.package.ecosystem}, {item.package.dir})")
        say(f"  last release: {item.last_tag or 'none'}")
        say(f"  manifest:     {item.manifest_version}")
        say(f"  next:         {item.version}  tag {item.tag}")
        say(f"  commits since last release: {len(item.commits)}")
        for line in item.commits[:20]:
            say(f"    {line}")
        if len(item.commits) > 20:
            say(f"    … {len(item.commits) - 20} more")
