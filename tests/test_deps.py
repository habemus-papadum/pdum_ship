from __future__ import annotations

import json

import pytest

from pdum.ship import config as config_mod
from pdum.ship import deps
from pdum.ship import packages as packages_mod
from pdum.ship.repo import Repo


def load(demo):
    repo = Repo(demo.root)
    cfg = config_mod.load(demo.root)
    pkgs = packages_mod.discover(cfg)
    return repo, cfg, pkgs


def test_clean_repo_passes(single_repo):
    repo, cfg, pkgs = load(single_repo)
    assert deps.check(repo, cfg, pkgs, hook=False) == []


def test_is_ours_matches_python_normalization():
    cfg = config_mod.Config(root=None, ours=["demo-*", "@demo/*"])  # type: ignore[arg-type]
    assert cfg.is_ours("demo-pkg")
    assert cfg.is_ours("Demo_Pkg")
    assert cfg.is_ours("@demo/widgets")
    assert not cfg.is_ours("requests")


def test_dev_link_in_uv_sources_is_reported(single_repo):
    pyproject = single_repo.root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + '\n[tool.uv.sources]\ndemo-other = { path = "../other", editable = true }\n'
    )
    repo, cfg, pkgs = load(single_repo)
    problems = deps.dev_link_problems(cfg, pkgs)
    assert problems == ["pyproject.toml: [tool.uv.sources] demo-other is a path/editable link"]


def test_dev_link_in_package_json_is_reported(single_repo):
    manifest = single_repo.root / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "@demo/app",
                "version": "0.0.0+dev",
                "dependencies": {"@demo/lib": "link:../lib"},
            }
        )
    )
    repo, cfg, pkgs = load(single_repo)
    problems = deps.dev_link_problems(cfg, pkgs)
    assert problems == ["package.json: dependencies @demo/lib = 'link:../lib' is a development link"]


def test_pin_without_reason_is_reported_and_reason_clears_it(single_repo):
    pyproject = single_repo.root / "pyproject.toml"
    text = pyproject.read_text().replace("dependencies = []", 'dependencies = ["demo-lib==1.2.3"]')
    pyproject.write_text(text)
    repo, cfg, pkgs = load(single_repo)
    problems = deps.pin_problems(cfg, pkgs)
    assert len(problems) == 1 and "pins one of our packages" in problems[0]

    pyproject.write_text(text + '\n[tool.ship.pins]\n"demo-lib" = "needs the old API until #12"\n')
    repo, cfg, pkgs = load(single_repo)
    assert deps.pin_problems(cfg, pkgs) == []


def test_inline_comment_counts_as_a_reason(single_repo):
    pyproject = single_repo.root / "pyproject.toml"
    text = pyproject.read_text().replace(
        "dependencies = []",
        'dependencies = [\n    "demo-lib==1.2.3",  # exact: schema v1 only\n]',
    )
    pyproject.write_text(text)
    repo, cfg, pkgs = load(single_repo)
    assert deps.pin_problems(cfg, pkgs) == []


def test_narrow_peer_range_is_reported(single_repo):
    manifest = single_repo.root / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "@demo/plugin",
                "version": "0.0.0+dev",
                "peerDependencies": {"@demo/core": "workspace:^", "@demo/ui": ">=0.3.0", "solid-js": "^1"},
            }
        )
    )
    repo, cfg, pkgs = load(single_repo)
    problems = deps.peer_problems(cfg, pkgs)
    assert len(problems) == 1 and "@demo/core" in problems[0]


def test_marker_problem_when_manifest_not_dev(single_repo):
    repo, cfg, pkgs = load(single_repo)
    packages_mod.write_version(cfg.root, pkgs[0], "0.4.0")
    problems = deps.marker_problems(repo, cfg, pkgs)
    assert problems == ["demo-pkg: manifest says '0.4.0'; expected '0.0.0+dev' (last tag none)"]


def test_stale_lock_is_reported(single_repo):
    pyproject = single_repo.root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("dependencies = []", 'dependencies = ["attrs"]'))
    repo, cfg, pkgs = load(single_repo)
    problems = deps.lock_problems(cfg)
    assert problems == ["uv.lock is not current with pyproject.toml; run `uv lock`"]


def test_pnpm_lock_key_parsing():
    assert deps._PNPM_KEY_RE.match("@demo/core@0.24.0").groupdict() == {"name": "@demo/core", "version": "0.24.0"}
    m = deps._PNPM_KEY_RE.match("vite@8.3.3(@types/node@22.0.0)")
    assert m.group("name") == "vite" and m.group("version") == "8.3.3"


def test_check_fetches_tags_made_elsewhere(single_repo):
    """A release tag that only origin has (after a plain `git pull`) is fetched before the marker check."""
    from pdum.ship import release

    repo, cfg, pkgs = load(single_repo)
    plan = release.plan(repo, cfg, pkgs, selected=pkgs[0], bump="minor", version=None, registry_check=False)
    release.execute(repo, cfg, plan, publish=False, push=True, run_tests=False, say=lambda _: None)
    single_repo.git("tag", "-d", "demo-pkg-v0.1.0")
    assert "demo-pkg-v0.1.0" not in single_repo.git("tag")
    assert deps.check(repo, cfg, pkgs, hook=False) == []
    assert "demo-pkg-v0.1.0" in single_repo.git("tag")


def test_moved_kind_spots_downgrades_and_removals():
    assert deps.Moved("a", None, "1.0.0").kind == "added"
    assert deps.Moved("a", "1.0.0", None).kind == "removed"
    assert deps.Moved("a", "4.6.2", "3.6.8").kind == "downgraded"
    assert deps.Moved("a", "0.3.0", "0.4.0").kind == "upgraded"
    assert deps.Moved("a", "1.33.1-dev45.0", "1.33.1-dev64.0").kind == "upgraded"  # equal numeric prefix
    assert deps._version_key("2.0.0-rc.13") == (2, 0, 0)
    assert deps._version_key("8.3.3") < deps._version_key("8.10.0")


def test_refresh_refuses_third_party_downgrades(single_repo, monkeypatch):
    """A refresh of ours that downgrades a third-party package is reverted and reported."""
    repo, cfg, pkgs = load(single_repo)
    lock = single_repo.root / "uv.lock"
    original = lock.read_text()
    snapshots = iter([{"demo-lib": "1.0.0", "attrs": "24.0.0"}, {"demo-lib": "1.1.0", "attrs": "23.0.0"}])
    monkeypatch.setattr(deps, "lock_versions", lambda root: next(snapshots))
    monkeypatch.setattr(deps, "uv_lock_registry_packages", lambda root: {"demo-lib", "attrs"})

    real_run = deps.subprocess.run

    def fake_run(args, cwd=None, **kwargs):
        # git (used by the restore) runs for real; uv's lock is simulated as churn.
        if args[0] == "git":
            return real_run(args, cwd=cwd, **kwargs)
        if args[:2] == ["uv", "lock"]:
            lock.write_text(original + "\n# churned\n")

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    with pytest.raises(deps.RefreshError, match="downgraded or removed third-party"):
        deps.refresh(
            repo,
            cfg,
            pkgs,
            everything=False,
            run_tests=False,
            dry_run=False,
            allow_downgrades=False,
            say=lambda _: None,
        )
    assert lock.read_text() == original  # restored
    assert repo.is_clean()
