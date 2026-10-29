from __future__ import annotations

import json

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
