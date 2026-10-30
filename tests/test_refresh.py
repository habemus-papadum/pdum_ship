from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import tarfile

import pytest

from pdum.ship import deps, packages
from pdum.ship.config import Config
from pdum.ship.repo import Repo


def test_private_workspace_projects_are_checked_but_not_released(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="tooling"\nversion="0"\n[tool.uv]\npackage=false\n')
    (tmp_path / "package.json").write_text('{"name":"root", "private":true}')
    (tmp_path / "pnpm-workspace.yaml").write_text('packages: ["apps/*", "!apps/excluded"]\n')
    for name in ["site", "excluded"]:
        path = tmp_path / "apps" / name
        path.mkdir(parents=True)
        (path / "package.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "private": name == "site",
                    "version": "0.0.0+dev",
                    "dependencies": {"@demo/lib": "link:../lib"},
                }
            )
        )
    cfg = Config(root=tmp_path, ours=["@demo/*"])
    assert packages.discover(cfg) == []
    assert {p.name for p in packages.projects(cfg)} == {"tooling", "root", "site"}
    assert len(deps.dev_link_problems(cfg, [])) == 1
    assert "apps/site/package.json" in deps.dev_link_problems(cfg, [])[0]


@pytest.mark.parametrize("kind", ["multiple_versions", "metadata_only", "declaration_change"])
def test_changed_lock_runs_tests_and_declarations_are_protected(tmp_path, monkeypatch, kind):
    manifest = tmp_path / "package.json"
    manifest.write_text('{"name":"app","private":true}')
    lock = tmp_path / "pnpm-lock.yaml"
    before = 'packages:\n  "@demo/lib@1.0.0": {}\n  "@demo/lib@2.0.0": {}\n'
    lock.write_text(before)
    cfg = Config(root=tmp_path, ours=["@demo/*"], test=["repository-tests"])
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if isinstance(args, list) and args[:2] == ["pnpm", "update"]:
            assert "--no-save" in args and "--lockfile-only" in args
            lock.write_text(
                before.replace("1.0.0", "1.1.0") if kind == "multiple_versions" else before + "# metadata\n"
            )
            if kind == "declaration_change":
                manifest.write_text('{"name":"app","private":true,"dependencies":{"@demo/lib":"^2"}}')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(deps.subprocess, "run", run)
    if kind == "declaration_change":
        with pytest.raises(deps.RefreshError, match="dependency declaration"):
            deps.refresh(
                Repo(tmp_path),
                cfg,
                [],
                everything=False,
                run_tests=True,
                dry_run=False,
                allow_downgrades=False,
                say=lambda x: None,
            )
        assert lock.read_text() == before
        assert manifest.read_text() == '{"name":"app","private":true}'
    else:
        moves = deps.refresh(
            Repo(tmp_path),
            cfg,
            [],
            everything=False,
            run_tests=True,
            dry_run=False,
            allow_downgrades=False,
            say=lambda x: None,
        )
        assert "repository-tests" in calls
        if kind == "multiple_versions":
            assert [(m.before, m.after) for m in moves] == [("1.0.0", "1.1.0")]
        else:
            assert moves == []


def test_lock_inventory_keeps_ecosystems_and_python_marker_versions(tmp_path):
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname="demo"\nversion="1.0.0"\n[[package]]\nname="demo"\nversion="2.0.0"\n'
    )
    (tmp_path / "pnpm-lock.yaml").write_text("packages:\n  demo@3.0.0: {}\n")
    assert deps.lock_versions(Config(root=tmp_path, ours=["demo"])) == {
        ("python", "demo"): frozenset(["1.0.0", "2.0.0"]),
        ("npm", "demo"): frozenset(["3.0.0"]),
    }


def npm_registry_fixture(server):
    versions = {}
    for version in ["1.0.0", "1.1.0"]:
        manifest = {"name": "@demo/lib", "version": version, "main": "index.js"}
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            body = json.dumps(manifest).encode()
            info = tarfile.TarInfo("package/package.json")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        tar = stream.getvalue()
        path = f"/npm/@demo/lib/-/lib-{version}.tgz"
        server["routes"][path] = (200, tar, {"Content-Type": "application/octet-stream"})
        versions[version] = {
            **manifest,
            "dist": {
                "tarball": server["url"] + path,
                "shasum": hashlib.sha1(tar).hexdigest(),
                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(tar).digest()).decode(),
            },
        }
    server["routes"]["/npm/@demo/lib"] = (
        200,
        {
            "name": "@demo/lib",
            "dist-tags": {"latest": "1.1.0"},
            "versions": versions,
            "time": {v: "2020-01-01T00:00:00Z" for v in versions},
        },
        {},
    )


@pytest.mark.parametrize("catalog", [False, True])
@pytest.mark.parametrize("fail_tests", [False, True])
def test_real_pnpm_refresh_preserves_floors_and_restores_on_failed_tests(
    tmp_path, registry_server, catalog, fail_tests
):
    npm_registry_fixture(registry_server)
    manifest = tmp_path / "package.json"
    workspace = tmp_path / "pnpm-workspace.yaml"
    dependency = "catalog:" if catalog else "1.0.0"
    manifest.write_text(json.dumps({"name": "app", "private": True, "dependencies": {"@demo/lib": dependency}}))
    workspace.write_text(
        "packages: []\nminimumReleaseAge: 0\n" + ('catalog:\n  "@demo/lib": "1.0.0"\n' if catalog else "")
    )
    (tmp_path / ".npmrc").write_text(f"@demo:registry={registry_server['url']}/npm/\n")

    def install():
        subprocess.run(
            ["pnpm", "install", "--lockfile-only", "--ignore-scripts"], cwd=tmp_path, capture_output=True, check=True
        )

    install()
    target = workspace if catalog else manifest
    target.write_text(target.read_text().replace('"1.0.0"', '">=1.0.0"'))
    install()
    lock = tmp_path / "pnpm-lock.yaml"
    snapshot = {p: p.read_bytes() for p in [manifest, workspace, lock]}
    command = (
        "python3 -c \"from pathlib import Path; Path('tested').touch(); "
        + ("raise SystemExit(9)" if fail_tests else "pass")
        + '"'
    )
    cfg = Config(root=tmp_path, ours=["@demo/*"], test=[command])
    if fail_tests:
        with pytest.raises(deps.RefreshError, match="tests failed"):
            deps.refresh(
                Repo(tmp_path),
                cfg,
                [],
                everything=False,
                run_tests=True,
                dry_run=False,
                allow_downgrades=False,
                say=lambda x: None,
            )
        assert all(p.read_bytes() == content for p, content in snapshot.items())
    else:
        moved = deps.refresh(
            Repo(tmp_path),
            cfg,
            [],
            everything=False,
            run_tests=True,
            dry_run=False,
            allow_downgrades=False,
            say=lambda x: None,
        )
        assert [(m.before, m.after) for m in moved] == [("1.0.0", "1.1.0")]
        assert manifest.read_bytes() == snapshot[manifest] and workspace.read_bytes() == snapshot[workspace]
    assert (tmp_path / "tested").exists()


def test_pnpm_release_age_uses_packument_times(tmp_path, registry_server):
    npm_registry_fixture(registry_server)
    data = registry_server["routes"]["/npm/@demo/lib"][1]
    data["time"]["1.1.0"] = "2099-01-01T00:00:00Z"
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "app", "private": True, "dependencies": {"@demo/lib": ">=1.0.0"}})
    )
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages: []\nminimumReleaseAge: 1440\nminimumReleaseAgeStrict: true\n"
    )
    (tmp_path / ".npmrc").write_text(f"@demo:registry={registry_server['url']}/npm/\n")
    proc = subprocess.run(
        ["pnpm", "install", "--lockfile-only", "--ignore-scripts"], cwd=tmp_path, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert deps.pnpm_lock_versions(tmp_path)["@demo/lib"] == frozenset(["1.0.0"])
