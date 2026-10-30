"""The ``ship`` command line."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, deps, release
from . import config as config_mod
from . import packages as packages_mod
from .config import Config
from .packages import Package
from .repo import Repo
from .versions import BUMPS, Version, VersionError

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Release packages and refresh dependencies. One version per package; the tag is the truth.",
)
deps_app = typer.Typer(no_args_is_help=True, help="Refresh or check dependencies.")
app.add_typer(deps_app, name="deps")
skills_app = typer.Typer(no_args_is_help=True, help="Write ship's agent skills into this repository.")
app.add_typer(skills_app, name="skills")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version_flag: Annotated[
        bool, typer.Option("--version", help="Print ship's version and exit", callback=_print_version, is_eager=True)
    ] = False,
) -> None:
    """Release packages and refresh dependencies. One version per package; the tag is the truth."""


def say(message: str) -> None:
    typer.echo(message)


def fail(message: str, code: int = 1) -> None:
    typer.echo(f"ship: {message}", err=True)
    raise typer.Exit(code)


def _load() -> tuple[Repo, Config, list[Package]]:
    try:
        repo = Repo.discover(Path.cwd())
        cfg = config_mod.load(repo.root)
        pkgs = packages_mod.discover(cfg)
    except (config_mod.ConfigError, packages_mod.PackageError, release.GitError) as exc:
        fail(str(exc))
        raise  # unreachable; keeps type checkers calm
    return repo, cfg, pkgs


@app.command()
def version() -> None:
    """Print ship's own version."""
    say(__version__)


@app.command(name="packages")
def list_packages() -> None:
    """List the versioned release packages, their manifest versions and their last tags."""
    repo, cfg, pkgs = _load()
    if not pkgs:
        say("no versioned release packages found")
        return
    for p in pkgs:
        tag, _ = release.last_release(repo, p)
        say(f"{p.name}\t{p.ecosystem}\t{p.dir}\t{packages_mod.read_version(cfg.root, p)}\t{tag or '-'}")


@app.command()
def changed() -> None:
    """List the packages with commits since their last release tag."""
    repo, cfg, pkgs = _load()
    any_changed = False
    for p in pkgs:
        commits = release.commits_since_release(repo, cfg, p)
        tag, _ = release.last_release(repo, p)
        if commits:
            any_changed = True
            say(f"{p.name}: {len(commits)} commit(s) since {tag or 'the start'}")
            for line in commits[:10]:
                say(f"  {line}")
            if len(commits) > 10:
                say(f"  … {len(commits) - 10} more")
    if not any_changed:
        say("no package has changed since its last release")


@app.command(name="release")
def release_cmd(
    package: Annotated[str | None, typer.Argument(help="Package to release (omit in a coupled repository)")] = None,
    bump: Annotated[str | None, typer.Option(help="patch, minor or major, applied to the last tag")] = None,
    version: Annotated[str | None, typer.Option("--version", help="Exact X.Y.Z instead of a bump")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan and print; write nothing")] = False,
    no_publish: Annotated[bool, typer.Option("--no-publish", help="Do everything except upload")] = False,
    no_push: Annotated[bool, typer.Option("--no-push", help="Commit and tag locally only")] = False,
    skip_tests: Annotated[bool, typer.Option("--skip-tests", help="Do not run the test commands")] = False,
    no_registry_check: Annotated[
        bool, typer.Option("--no-registry-check", help="Do not ask the registry whether the version exists")
    ] = False,
) -> None:
    """Release one package: set the version from its last tag, build, test, publish, tag, push."""
    repo, cfg, pkgs = _load()
    if bump is not None and bump not in BUMPS:
        fail(f"--bump must be one of {', '.join(BUMPS)}")
    explicit: Version | None = None
    if version is not None:
        try:
            explicit = Version.parse(version)
        except VersionError as exc:
            fail(str(exc))
    selected: Package | None = None
    if package is not None:
        try:
            selected = packages_mod.find(pkgs, package)
        except packages_mod.PackageError as exc:
            fail(str(exc))

    problems = release.preflight(repo, cfg, pkgs, check_remote=not no_push and not dry_run, allow_dirty=dry_run)
    if problems:
        for problem in problems:
            say(f"✗ {problem}")
        fail("preflight failed")

    try:
        plan = release.plan(
            repo,
            cfg,
            pkgs,
            selected=selected,
            bump=bump,
            version=explicit,  # type: ignore[arg-type]
            registry_check=not no_registry_check,
        )
    except release.ReleaseError as exc:
        fail(str(exc))
        raise

    release.describe(plan, say)
    if dry_run:
        say("")
        say("dry run: nothing written")
        return
    try:
        release.execute(
            repo,
            cfg,
            plan,
            publish=not no_publish,
            push=not no_push,
            run_tests=not skip_tests,
            say=say,
        )
    except release.ReleaseError as exc:
        fail(str(exc))


@deps_app.command(name="refresh")
def deps_refresh(
    all_packages: Annotated[
        bool, typer.Option("--all", help="Upgrade third-party packages too, within their ranges")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the commands; change nothing")] = False,
    no_test: Annotated[bool, typer.Option("--no-test", help="Do not run the tests after refreshing")] = False,
    allow_downgrades: Annotated[
        bool,
        typer.Option(
            "--allow-downgrades",
            help="Accept third-party packages being downgraded or removed while refreshing ours",
        ),
    ] = False,
) -> None:
    """Upgrade our packages in the lockfiles to their newest releases, then test."""
    repo, cfg, pkgs = _load()
    if not repo.is_clean():
        fail("the working tree has uncommitted changes; refresh needs a clean tree")
    before = deps.lock_state(cfg)
    try:
        moved = deps.refresh(
            repo,
            cfg,
            pkgs,
            everything=all_packages,
            run_tests=not no_test,
            dry_run=dry_run,
            allow_downgrades=allow_downgrades,
            say=say,
        )
    except deps.RefreshError as exc:
        fail(str(exc))
        raise
    if dry_run:
        return
    if not moved:
        say("lockfiles unchanged" if deps.lock_state(cfg) == before else "lockfile metadata updated and not committed")
        return
    say("")
    say("package\tbefore\tafter\tchange")
    for m in moved:
        say(f"{m.name}\t{m.before or '-'}\t{m.after or '-'}\t{m.kind}")
    others = [m for m in moved if not cfg.is_ours(m.name)]
    if others and not all_packages:
        say("")
        say(f"{len(others)} third-party package(s) moved with ours (new or raised requirements)")
    say("")
    say("the lockfile is updated and not committed")


@deps_app.command(name="check")
def deps_check(
    hook: Annotated[
        bool, typer.Option("--hook", help="Fast subset for a pre-commit hook: development links and lockfiles")
    ] = False,
) -> None:
    """Report policy violations: development links, stale lockfiles, pins, peer ranges, version markers."""
    repo, cfg, pkgs = _load()
    problems = deps.check(repo, cfg, pkgs, hook=hook)
    if not problems:
        say("ok")
        return
    for problem in problems:
        say(f"✗ {problem}")
    raise typer.Exit(1)


@skills_app.command(name="sync")
def skills_sync() -> None:
    """Write current copies of the skills into .agents/skills and point .claude/skills at them."""
    from . import skills

    repo = _load()[0]
    try:
        changed = skills.sync(repo.root)
    except skills.SkillsError as exc:
        fail(str(exc))
        raise
    if not changed:
        say("skills are current")
        return
    for path in changed:
        say(f"wrote {path.relative_to(repo.root)}")


@skills_app.command(name="check")
def skills_check() -> None:
    """Report skill copies that are missing or stale for this version of ship."""
    from . import skills

    repo = _load()[0]
    problems = skills.problems(repo.root)
    if not problems:
        say("ok")
        return
    for problem in problems:
        say(f"✗ {problem}")
    raise typer.Exit(1)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
