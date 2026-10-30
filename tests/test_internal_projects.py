"""Internal projects keep dependency management without becoming release targets."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from pdum.ship import config as config_mod
from pdum.ship import deps, packages, registry, release
from pdum.ship.cli import app
from pdum.ship.repo import Repo
from pdum.ship.versions import Version


def test_installable_python_can_disable_versioning(single_repo, monkeypatch):
    root = single_repo.root
    project = root / "pyproject.toml"
    project.write_text(project.read_text().replace("[tool.ship]\n", "[tool.ship]\nversioning = false\n"))
    cfg = config_mod.load(root)
    internal = packages.projects(cfg)[0]
    assert internal.publishable  # installability is unchanged
    packages.write_version(root, internal, "7.8.9")
    single_repo.commit_all("internal application")
    cfg = config_mod.load(root)
    assert packages.discover(cfg) == []
    assert deps.refresh_tests(cfg) == ["uv run --locked pytest"]
    checked = []
    monkeypatch.setattr(registry, "check_timestamps", lambda cfg: checked.append(True) or [])
    assert deps.check(Repo(root), cfg, [], hook=False) == []
    assert checked == [True]
    monkeypatch.chdir(root)
    before = (project.read_bytes(), single_repo.git("rev-parse", "HEAD"), single_repo.git("tag"))
    result = CliRunner().invoke(app, ["release", "demo-pkg", "--bump", "minor", "--no-push"])
    assert result.exit_code == 1 and "no package named" in result.output
    assert (project.read_bytes(), single_repo.git("rev-parse", "HEAD"), single_repo.git("tag")) == before
    assert deps.marker_problems(Repo(root), cfg, [internal]) == []
    stale_plan = release.ReleasePlan([release.PackagePlan(internal, None, None, "7.8.9", Version(8, 0, 0), [])])
    with pytest.raises(release.ReleaseError, match="versioning = false"):
        release.execute(Repo(root), cfg, stale_plan, publish=True, push=True, run_tests=False, say=lambda _: None)
    assert (project.read_bytes(), single_repo.git("rev-parse", "HEAD"), single_repo.git("tag")) == before
    with pytest.raises(release.ReleaseError, match="versioning = false"):
        release.plan(Repo(root), cfg, [internal], selected=internal, bump="minor", version=None, registry_check=False)


def test_mixed_workspace_only_versions_selected_library(workspace_repo):
    root = workspace_repo.root
    project = root / "pyproject.toml"
    project.write_text(
        project.read_text().replace("[tool.ship]\n", "[tool.ship]\nversioning = false\n")
        + '\n[tool.ship.packages."demo-a"]\nversioning = true\npublish = false\n'
    )
    cfg = config_mod.load(root)
    internal = next(p for p in packages.projects(cfg) if p.name == "demo-b")
    packages.write_version(root, internal, "6.0.0")
    workspace_repo.commit_all("select the library")
    before = (root / internal.manifest).read_bytes()
    selected = packages.discover(cfg)
    assert [p.name for p in selected] == ["demo-a"]
    repo = Repo(root)
    plan = release.plan(repo, cfg, selected, selected=selected[0], bump="minor", version=None, registry_check=False)
    release.execute(repo, cfg, plan, publish=True, push=False, run_tests=False, say=lambda _: None)
    assert repo.tags("*") == ["demo-a-v0.1.0"]
    assert (root / internal.manifest).read_bytes() == before
    assert packages.read_version(root, selected[0]) == "0.1.0+dev"
    assert deps.check(repo, cfg, selected, hook=False) == []


def test_unversioned_npm_and_private_projects_keep_checks_and_tests(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.ship]\nours=["@demo/*"]\nversioning=false\n')
    (tmp_path / "pnpm-workspace.yaml").write_text('packages: ["apps/*"]\n')
    for name, private in [("internal", False), ("private", True)]:
        path = tmp_path / "apps" / name
        path.mkdir(parents=True)
        (path / "package.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "private": private,
                    "dependencies": {"@demo/lib": "^1.0.0"},
                    "scripts": {"test": "offline-tests"},
                }
            )
        )
    cfg = config_mod.load(tmp_path)
    assert packages.discover(cfg) == []
    assert len(deps.pin_problems(cfg, [])) == 2
    assert deps.refresh_tests(cfg) == ["pnpm --filter internal test", "pnpm --filter private test"]
    cfg = replace(cfg, packages={"private": config_mod.PackageConfig(versioning=True)})
    assert packages.discover(cfg) == []  # explicit versioning never overrides npm private


@pytest.mark.parametrize("scope", ["repository", "package"])
@pytest.mark.parametrize("value", ['"false"', "0", "[]"])
def test_versioning_requires_a_boolean(tmp_path, scope, value):
    text = '[tool.ship]\nours=["demo-*"]\n'
    if scope == "package":
        text += '[tool.ship.packages."demo-pkg"]\n'
    (tmp_path / "pyproject.toml").write_text(text + f"versioning = {value}\n")
    with pytest.raises(config_mod.ConfigError, match="versioning.*true or false"):
        config_mod.load(tmp_path)


def allow(manifest="package.json", spec="file:./vendor", **extra):
    return {"manifest": manifest, "dependency": "@vendor/tool", "spec": spec, "reason": "Pinned vendored tool", **extra}


def write_config(root, entries):
    text = '[tool.ship]\nours=["@demo/*"]\nversioning=false\n'
    for entry in entries:
        text += "\n[[tool.ship.local-dependencies]]\n"
        text += "\n".join(f"{key} = {json.dumps(value)}" for key, value in entry.items()) + "\n"
    (root / "pyproject.toml").write_text(text)
    return config_mod.load(root)


@pytest.mark.parametrize(
    "entry",
    [
        allow(reason=""),
        allow(manifest="../package.json"),
        allow(manifest="/package.json"),
        allow(manifest="apps/*/package.json"),
        allow(spec="*"),
        allow(extra="unknown"),
    ],
)
def test_local_override_is_explicit_and_validated(tmp_path, entry):
    with pytest.raises(config_mod.ConfigError):
        write_config(tmp_path, [entry])


def test_local_override_matches_only_exact_manifest_name_and_spec(tmp_path):
    (tmp_path / "pnpm-workspace.yaml").write_text('packages: ["apps/*"]\n')
    for name in ["one", "two"]:
        path = tmp_path / "apps" / name
        path.mkdir(parents=True)
        (path / "package.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "private": True,
                    "dependencies": {"@vendor/tool": "file:../../vendor", "@vendor/other": "file:../../vendor"},
                }
            )
        )
    cfg = write_config(tmp_path, [allow("apps/one/package.json", "file:../../vendor")])
    problems = deps.dev_link_problems(cfg, [])
    assert len(problems) == 3
    assert all("apps/one/package.json: dependencies @vendor/tool" not in p for p in problems)
    changed = replace(
        cfg, local_dependencies=[config_mod.LocalDependency(**allow("apps/one/package.json", "file:../new"))]
    )
    assert len(deps.dev_link_problems(changed, [])) == 4


def test_catalog_override_applies_to_one_consumer_only(tmp_path):
    (tmp_path / "pnpm-workspace.yaml").write_text(
        'packages: ["apps/*"]\ncatalog:\n  "@vendor/tool": file:../../vendor\n'
    )
    for name in ["one", "two"]:
        path = tmp_path / "apps" / name
        path.mkdir(parents=True)
        (path / "package.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "private": True,
                    "dependencies": {"@vendor/tool": "catalog:"},
                }
            )
        )
    cfg = write_config(tmp_path, [allow("apps/one/package.json", "file:../../vendor")])
    assert deps.dev_link_problems(cfg, []) == [
        "apps/two/package.json: dependencies @vendor/tool = 'file:../../vendor' is a development link"
    ]


def test_allowed_source_link_cannot_escape_in_published_npm_metadata(pnpm_repo):
    cfg = config_mod.load(pnpm_repo.root)
    target = packages.discover(cfg)[0]
    vendor = cfg.pnpm_dir / "vendor"
    vendor.mkdir()
    (vendor / "package.json").write_text('{"name":"@vendor/tool","version":"1.0.0"}')
    manifest = cfg.root / target.manifest
    data = json.loads(manifest.read_text())
    data["dependencies"] = {"@vendor/tool": "file:../../vendor"}
    manifest.write_text(json.dumps(data))
    cfg = replace(
        cfg, local_dependencies=[config_mod.LocalDependency(**allow(target.manifest.as_posix(), "file:../../vendor"))]
    )
    assert deps.dev_link_problems(cfg, packages.discover(cfg)) == []
    plan = release.plan(
        Repo(cfg.root), cfg, [target], selected=target, bump="minor", version=None, registry_check=False
    )
    packages.write_version(cfg.root, target, "0.1.0")
    with pytest.raises(release.ReleaseError, match="packed npm dependencies @vendor/tool retains a local dependency"):
        release._build(cfg, plan.items[0], lambda _: None)
    cfg = replace(cfg, packages={target.name: config_mod.PackageConfig(publish=False)})
    assert len(release._build(cfg, plan.items[0], lambda _: None)) == 1


def test_our_vendored_package_is_not_a_registry_release(tmp_path, monkeypatch):
    lock = tmp_path / "pnpm-lock.yaml"
    lock.write_text('packages:\n  "@demo/vendor@file:vendor": {}\n  "@demo/library@1.0.0": {}\n')
    cfg = config_mod.Config(root=tmp_path, ours=["@demo/*"], versioning=False)
    calls = []
    monkeypatch.setattr(registry, "npm_target", lambda cfg, name: "https://registry.example/")

    def metadata(cfg, name, url):
        calls.append(name)
        return 200, {"versions": {"1.0.0": {}}, "time": {"1.0.0": "2026-01-01T00:00:00Z"}}

    monkeypatch.setattr(registry, "npm_metadata", metadata)
    assert registry.check_timestamps(cfg) == []
    assert calls == ["@demo/library"]
    commands = []
    deps.refresh(
        Repo(tmp_path),
        cfg,
        [],
        everything=False,
        run_tests=True,
        dry_run=True,
        allow_downgrades=False,
        say=commands.append,
    )
    assert len(commands) == 1 and "@demo/library" in commands[0] and "@demo/vendor" not in commands[0]


def test_package_can_disable_the_repository_versioning_default(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"internal-library","version":"42"}')
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ship]\nours=["internal-*"]\n[tool.ship.packages."internal-library"]\nversioning=false\n'
    )
    cfg = config_mod.load(tmp_path)
    assert cfg.versioning is True
    assert [p.name for p in packages.projects(cfg)] == ["internal-library"]
    assert packages.discover(cfg) == []
