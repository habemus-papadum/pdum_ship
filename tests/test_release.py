"""End-to-end release against a throwaway repository with a bare origin.

Publishing is disabled; everything else (version write, re-lock, build, tests, commit,
tag, atomic push, development-version restore) runs for real.
"""

from __future__ import annotations

import pytest

from pdum.ship import config as config_mod
from pdum.ship import packages as packages_mod
from pdum.ship import release
from pdum.ship.repo import Repo
from pdum.ship.versions import Version

NOISE: list[str] = []


def say(message: str) -> None:
    NOISE.append(message)


def load(demo):
    repo = Repo(demo.root)
    cfg = config_mod.load(demo.root)
    pkgs = packages_mod.discover(cfg)
    return repo, cfg, pkgs


def do_release(demo, name: str, bump: str = "minor", **kwargs):
    repo, cfg, pkgs = load(demo)
    selected = packages_mod.find(pkgs, name) if cfg.coupled is None else None
    problems = release.preflight(repo, cfg, pkgs, check_remote=True)
    assert problems == [], problems
    plan = release.plan(repo, cfg, pkgs, selected=selected, bump=bump, version=None, registry_check=False)
    release.execute(repo, cfg, plan, publish=False, push=True, run_tests=kwargs.get("run_tests", True), say=say)
    return plan


def test_discovers_single_package(single_repo):
    _, cfg, pkgs = load(single_repo)
    assert [p.name for p in pkgs] == ["demo-pkg"]
    assert pkgs[0].tag_component == "demo-pkg"
    assert packages_mod.read_version(cfg.root, pkgs[0]) == "0.0.0+dev"


def test_first_release_tags_and_restores_dev(single_repo):
    plan = do_release(single_repo, "demo-pkg", "minor")
    item = plan.items[0]
    assert item.version == Version(0, 1, 0)
    assert item.tag == "demo-pkg-v0.1.0"

    # The tag reached origin, atomically with the release commit.
    assert single_repo.origin_tags() == ["demo-pkg-v0.1.0"]
    log = single_repo.origin_log()
    assert log[0].startswith("back to development: 0.1.0+dev")
    assert log[1] == "demo-pkg 0.1.0"

    # The manifest is back on the development marker and the lock agrees.
    repo, cfg, pkgs = load(single_repo)
    assert packages_mod.read_version(cfg.root, pkgs[0]) == "0.1.0+dev"
    assert repo.is_clean()
    assert release.preflight(repo, cfg, pkgs, check_remote=True) == []

    # The tagged commit carries the release version, not the marker.
    tagged = repo.tag_commit("demo-pkg-v0.1.0")
    shown = repo.git("show", f"{tagged}:pyproject.toml")
    assert 'version = "0.1.0"' in shown

    # Built files carry the release version.
    dist = cfg.root / "dist" / "demo-pkg"
    names = sorted(p.name for p in dist.iterdir())
    assert any(n.startswith("demo_pkg-0.1.0") for n in names), names
    assert not any("dev" in n for n in names)


def test_second_release_bumps_from_tag_not_manifest(single_repo):
    do_release(single_repo, "demo-pkg", "minor")
    single_repo.write("src/demo_pkg/extra.py", "X = 1\n")
    single_repo.commit_all("add extra")
    single_repo.git("push", "-q", "origin", "main")
    plan = do_release(single_repo, "demo-pkg", "patch")
    assert plan.items[0].last_tag == "demo-pkg-v0.1.0"
    assert plan.items[0].version == Version(0, 1, 1)
    assert plan.items[0].commits and plan.items[0].commits[0].endswith("add extra")
    assert single_repo.origin_tags() == ["demo-pkg-v0.1.0", "demo-pkg-v0.1.1"]


def test_plan_refuses_manifest_without_dev_marker(single_repo):
    repo, cfg, pkgs = load(single_repo)
    packages_mod.write_version(cfg.root, pkgs[0], "0.3.0")
    single_repo.commit_all("hand-edited version")
    single_repo.git("push", "-q", "origin", "main")
    with pytest.raises(release.ReleaseError, match="between releases it must say '0.0.0\\+dev'"):
        release.plan(repo, cfg, pkgs, selected=pkgs[0], bump="minor", version=None, registry_check=False)


def test_failing_tests_revert_the_version(single_repo):
    single_repo.write("tests/test_fail.py", "def test_fail():\n    assert False\n")
    single_repo.commit_all("add failing test")
    single_repo.git("push", "-q", "origin", "main")
    repo, cfg, pkgs = load(single_repo)
    plan = release.plan(repo, cfg, pkgs, selected=pkgs[0], bump="minor", version=None, registry_check=False)
    with pytest.raises(release.ReleaseError, match="command failed"):
        release.execute(repo, cfg, plan, publish=False, push=True, run_tests=True, say=say)
    assert repo.is_clean()
    assert packages_mod.read_version(cfg.root, pkgs[0]) == "0.0.0+dev"
    assert single_repo.origin_tags() == []


def test_preflight_catches_dirty_tree_and_wrong_branch(single_repo):
    repo, cfg, pkgs = load(single_repo)
    single_repo.write("scratch.txt", "x")
    problems = release.preflight(repo, cfg, pkgs, check_remote=False)
    assert any("uncommitted" in p for p in problems)
    (single_repo.root / "scratch.txt").unlink()
    single_repo.git("checkout", "-q", "-b", "feature")
    problems = release.preflight(repo, cfg, pkgs, check_remote=False)
    assert any("branch 'feature'" in p for p in problems)


def test_changed_rule(single_repo):
    do_release(single_repo, "demo-pkg", "minor")
    repo, cfg, pkgs = load(single_repo)
    pkg = pkgs[0]
    assert release.commits_since_release(repo, cfg, pkg) == []

    # A root-package repository: every file is under the package directory.
    single_repo.write("README.md", "hello\n")
    single_repo.commit_all("docs")
    assert len(release.commits_since_release(repo, cfg, pkg)) == 1


def test_workspace_packages_release_independently(workspace_repo):
    repo, cfg, pkgs = load(workspace_repo)
    assert sorted(p.name for p in pkgs) == ["demo-a", "demo-b"]

    plan = do_release(workspace_repo, "demo-a", "minor")
    assert plan.items[0].tag == "demo-a-v0.1.0"
    assert workspace_repo.origin_tags() == ["demo-a-v0.1.0"]

    repo, cfg, pkgs = load(workspace_repo)
    a = packages_mod.find(pkgs, "demo-a")
    b = packages_mod.find(pkgs, "demo-b")
    assert packages_mod.read_version(cfg.root, a) == "0.1.0+dev"
    assert packages_mod.read_version(cfg.root, b) == "0.0.0+dev"

    # demo-b has never been released, so its history counts from the start.
    baseline = len(release.commits_since_release(repo, cfg, b))
    assert baseline >= 1

    # Only commits under packages/b or a shared path count as a change to demo-b.
    workspace_repo.write("packages/a/README.md", "a\n")
    workspace_repo.commit_all("touch a")
    assert len(release.commits_since_release(repo, cfg, b)) == baseline
    workspace_repo.write("shared/schema.json", "{}\n")
    workspace_repo.commit_all("touch shared")
    assert len(release.commits_since_release(repo, cfg, b)) == baseline + 1
    workspace_repo.write("packages/b/README.md", "b\n")
    workspace_repo.commit_all("touch b")
    assert len(release.commits_since_release(repo, cfg, b)) == baseline + 2
    # demo-a was released: only touch a and touch shared count, not ship's restore commit.
    assert len(release.commits_since_release(repo, cfg, a)) == 2


def test_coupled_repository_releases_everything_at_one_version(workspace_repo):
    pyproject = workspace_repo.root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("[tool.ship]\n", '[tool.ship]\ncoupled = "test fixture"\n', 1))
    workspace_repo.commit_all("couple")
    workspace_repo.git("push", "-q", "origin", "main")

    repo, cfg, pkgs = load(workspace_repo)
    plan = release.plan(repo, cfg, pkgs, selected=None, bump="major", version=None, registry_check=False)
    assert plan.coupled and {str(i.version) for i in plan.items} == {"1.0.0"}
    release.execute(repo, cfg, plan, publish=False, push=True, run_tests=False, say=say)
    assert sorted(workspace_repo.origin_tags()) == ["demo-a-v1.0.0", "demo-b-v1.0.0"]


def test_pnpm_package_release_builds_tags_and_restores(pnpm_repo):
    from pdum.ship import deps

    repo, cfg, pkgs = load(pnpm_repo)
    assert sorted(p.name for p in pkgs) == ["@demo/core", "@demo/plugin"]
    plugin = packages_mod.find(pkgs, "@demo/plugin")
    assert plugin.tag_component == "plugin"
    assert deps.check(repo, cfg, pkgs, hook=False) == []

    plan = do_release(pnpm_repo, "@demo/plugin", "minor")
    assert plan.items[0].tag == "plugin-v0.1.0"
    assert pnpm_repo.origin_tags() == ["plugin-v0.1.0"]

    # The build ran for the package and its workspace dependency before publishing.
    assert (cfg.root / "packages" / "plugin" / "dist" / "index.js").exists()
    assert (cfg.root / "packages" / "core" / "dist" / "index.js").exists()

    repo, cfg, pkgs = load(pnpm_repo)
    assert packages_mod.read_version(cfg.root, plugin) == "0.1.0+dev"
    core = packages_mod.find(pkgs, "@demo/core")
    assert packages_mod.read_version(cfg.root, core) == "0.0.0+dev"
    assert repo.is_clean()
    # The lockfile is still consistent with the manifests and the peer range is untouched.
    assert deps.lock_problems(cfg) == []
    import json

    manifest = json.loads((cfg.root / plugin.manifest).read_text())
    assert manifest["peerDependencies"] == {"@demo/core": "workspace:>=0.0.0"}
    tagged = repo.tag_commit("plugin-v0.1.0")
    assert '"version": "0.1.0"' in repo.git("show", f"{tagged}:packages/plugin/package.json")
