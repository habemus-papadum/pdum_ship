from __future__ import annotations

import base64
import json
import subprocess

import pytest

from pdum.ship import config as config_mod
from pdum.ship import packages, registry, release
from pdum.ship.config import Config
from pdum.ship.repo import Repo


def python_config(root, url):
    (root / "pyproject.toml").write_text(
        '[project]\nname="demo-pkg"\nversion="0.0.0+dev"\n'
        '[tool.ship]\nours=["demo-*"]\npython-index="private"\n'
        '[[tool.uv.index]]\nname="private"\n'
        f'url="{url}/simple/"\npublish-url="{url}/legacy/"\n'
    )
    return config_mod.load(root)


def simple_metadata(version="0.1.0"):
    return {
        "meta": {"api-version": "1.1"},
        "name": "demo-pkg",
        "versions": [version],
        "files": [
            {
                "filename": f"demo_pkg-{version}-py3-none-any.whl",
                "url": "/file.whl",
                "hashes": {},
                "size": 100,
                "upload-time": "2024-01-01T00:00:00Z",
            }
        ],
    }


def test_python_checks_selected_index_and_reports_unknown_auth(registry_server, tmp_path, monkeypatch):
    url = registry_server["url"]
    cfg = python_config(tmp_path, url)
    registry_server["auth"] = "Basic " + base64.b64encode(b"robot:fixture-secret").decode()
    registry_server["routes"]["/simple/demo-pkg/"] = (200, simple_metadata(), {})
    monkeypatch.setenv("UV_INDEX_PRIVATE_USERNAME", "robot")
    monkeypatch.setenv("UV_INDEX_PRIVATE_PASSWORD", "fixture-secret")
    package = packages.discover(cfg)[0]
    assert registry.has(cfg, package, "0.1.0") is True
    assert registry.has(cfg, package, "0.2.0") is False
    monkeypatch.setenv("UV_INDEX_PRIVATE_PASSWORD", "wrong")
    assert registry.has(cfg, package, "0.2.0") is None
    assert all(path == "/simple/demo-pkg/" for _, path, _, _ in registry_server["requests"])
    assert all(agent.startswith("habemus-papadum-ship/") for agent in registry_server["user_agents"])


def test_native_uv_credentials_are_read_without_a_second_store(registry_server, tmp_path, monkeypatch):
    cfg = python_config(tmp_path, registry_server["url"])
    monkeypatch.setenv("UV_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    for key in (
        "UV_PUBLISH_USERNAME",
        "UV_PUBLISH_PASSWORD",
        "UV_PUBLISH_TOKEN",
        "UV_PREVIEW_FEATURES",
        "UV_KEYRING_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)
    subprocess.run(
        ["uv", "auth", "login", registry_server["url"], "--username", "fixture-user", "--password", "fixture-password"],
        check=True,
        capture_output=True,
    )
    assert registry._credentials(registry.python_target(cfg)) == ("fixture-user", "fixture-password")


def test_python_reads_netrc_and_refuses_credential_redirects(registry_server, tmp_path, monkeypatch):
    cfg = python_config(tmp_path, registry_server["url"])
    path = tmp_path / "netrc"
    path.write_text("machine 127.0.0.1 login fixture password secret\n")
    path.chmod(0o600)
    monkeypatch.setenv("NETRC", str(path))
    registry_server["routes"]["/simple/demo-pkg/"] = (302, {}, {"Location": registry_server["url"] + "/other/"})
    package = packages.discover(cfg)[0]
    assert registry.has(cfg, package, "0.1.0") is None
    assert len(registry_server["requests"]) == 1
    assert registry_server["requests"][0][2] == "Basic " + base64.b64encode(b"fixture:secret").decode()


def test_default_public_and_inferred_private_publish_target(tmp_path, monkeypatch):
    monkeypatch.delenv("UV_PUBLISH_URL", raising=False)
    cfg = Config(root=tmp_path, ours=["demo-*"])
    assert registry.python_target(cfg).index_url == registry.PYPI_INDEX
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\npublish-url="https://packages.example/legacy/"\n'
        '[[tool.uv.index]]\nname="private"\nurl="https://packages.example/simple/"\n'
    )
    assert registry.python_target(cfg) == registry.PythonRegistry(
        "https://packages.example/simple/", "https://packages.example/legacy/", "private"
    )
    (tmp_path / "uv.toml").write_text('publish-url="https://different.example/legacy/"\n')
    with pytest.raises(registry.RegistryError, match="unambiguous"):
        registry.python_target(cfg)


def npm_config(root, url):
    (root / "package.json").write_text(
        json.dumps({"name": "@demo/lib", "version": "0.0.0+dev", "publishConfig": {"registry": url + "/npm/"}})
    )
    (root / ".npmrc").write_text(
        f"@demo:registry={url}/npm/\n//{url.split('//')[1]}/npm/:_authToken=${{FIXTURE_TOKEN}}\n"
    )
    return Config(root=root, ours=["@demo/*"])


def npm_metadata():
    return {
        "name": "@demo/lib",
        "dist-tags": {"latest": "0.1.0"},
        "versions": {"0.1.0": {"name": "@demo/lib", "version": "0.1.0"}},
        "time": {"0.1.0": "2024-01-01T00:00:00Z"},
    }


@pytest.mark.parametrize("auth_kind", ["bearer", "basic"])
def test_npm_uses_native_scoped_registry_and_auth(registry_server, tmp_path, monkeypatch, auth_kind):
    monkeypatch.setenv("FIXTURE_TOKEN", "synthetic-token")
    cfg = npm_config(tmp_path, registry_server["url"])
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(tmp_path / ".npmrc"))
    registry_server["auth"] = "Bearer synthetic-token"
    if auth_kind == "basic":
        token = base64.b64encode(b"fixture-user:fixture-password").decode()
        monkeypatch.setenv("FIXTURE_TOKEN", token)
        path = tmp_path / ".npmrc"
        path.write_text(path.read_text().replace(":_authToken=", ":_auth="))
        registry_server["auth"] = "Basic " + token
    registry_server["routes"]["/npm/@demo/lib"] = (200, npm_metadata(), {})
    pkg = packages.discover(cfg)[0]
    assert registry.npm_target(cfg, pkg.name) == registry_server["url"] + "/npm/"
    assert registry.has(cfg, pkg, "0.1.0") is True
    assert registry.has(cfg, pkg, "0.2.0") is False
    assert (
        registry.timestamp_problems(
            "npm", pkg.name, registry.npm_metadata(cfg, pkg.name, registry_server["url"] + "/npm/")[1]
        )
        == []
    )
    registry_server["routes"].clear()
    assert registry.has(cfg, pkg, "0.2.0") is False
    registry_server["auth"] = "Bearer something-else"
    assert registry.has(cfg, pkg, "0.2.0") is None


def test_metadata_dates_are_checked_on_both_private_registries(registry_server, tmp_path, monkeypatch):
    cfg = python_config(tmp_path, registry_server["url"])
    (tmp_path / "uv.lock").write_text(
        f'[[package]]\nname="demo-pkg"\nversion="0.1.0"\nsource={{registry="{registry_server["url"]}/simple/"}}\n'
    )
    registry_server["routes"]["/simple/demo-pkg/"] = (200, simple_metadata(), {})
    monkeypatch.setattr(registry, "_credentials", lambda target: None)
    assert registry.check_timestamps(cfg) == []
    registry_server["routes"]["/simple/demo-pkg/"][1]["files"][0].pop("upload-time")
    assert "upload-time" in registry.check_timestamps(cfg)[0]
    data = npm_metadata()
    data["time"] = {}
    assert "publication times" in registry.timestamp_problems("npm", "@demo/lib", data)[0]


def test_unknown_metadata_is_not_absence(registry_server, tmp_path, monkeypatch):
    cfg = python_config(tmp_path, registry_server["url"])
    monkeypatch.setattr(registry, "_credentials", lambda target: None)
    pkg = packages.discover(cfg)[0]
    for status, body in [(200, {}), (500, {}), (200, b"not json")]:
        registry_server["routes"]["/simple/demo-pkg/"] = (status, body, {})
        assert registry.has(cfg, pkg, "0.1.0") is None


def test_python_native_upload_targets_private_registry(single_repo, registry_server, monkeypatch):
    root = single_repo.root
    path = root / "pyproject.toml"
    path.write_text(
        path.read_text()
        + f'\n[tool.uv]\npublish-url="{registry_server["url"]}/legacy/"\n'
        + f'[[tool.uv.index]]\nname="private"\nurl="{registry_server["url"]}/simple/"\n'
    )
    cfg = config_mod.load(root)
    pkg = packages.discover(cfg)[0]
    monkeypatch.setenv("UV_INDEX_PRIVATE_USERNAME", "fixture")
    monkeypatch.setenv("UV_INDEX_PRIVATE_PASSWORD", "secret")
    registry_server["auth"] = "Basic " + base64.b64encode(b"fixture:secret").decode()
    plan = release.plan(Repo(root), cfg, [pkg], selected=pkg, bump="minor", version=None, registry_check=True)
    packages.write_version(root, pkg, "0.1.0")
    files = release._build(cfg, plan.items[0], lambda message: None)
    release._publish(cfg, plan.items[0], files, lambda message: None)
    uploads = [r for r in registry_server["requests"] if r[0] == "POST"]
    assert len(uploads) == 2 and all(r[1] == "/legacy/" for r in uploads)


def test_npm_native_upload_publishes_prepared_tarball(pnpm_repo, registry_server, monkeypatch):
    cfg = config_mod.load(pnpm_repo.root)
    pkg = packages.find(packages.discover(cfg), "@demo/core")
    manifest = cfg.root / pkg.manifest
    data = json.loads(manifest.read_text())
    data["publishConfig"] = {"registry": registry_server["url"] + "/npm/"}
    manifest.write_text(json.dumps(data))
    host = registry_server["url"].split("//")[1]
    (cfg.pnpm_dir / ".npmrc").write_text(f"//{host}/npm/:_authToken=${{FIXTURE_TOKEN}}\n")
    monkeypatch.setenv("FIXTURE_TOKEN", "synthetic-token")
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(cfg.pnpm_dir / ".npmrc"))
    registry_server["auth"] = "Bearer synthetic-token"
    plan = release.plan(Repo(cfg.root), cfg, [pkg], selected=pkg, bump="minor", version=None, registry_check=True)
    packages.write_version(cfg.root, pkg, "0.1.0")
    files = release._build(cfg, plan.items[0], lambda message: None)
    release._publish(cfg, plan.items[0], files, lambda message: None)
    uploads = [r for r in registry_server["requests"] if r[0] == "PUT"]
    assert len(uploads) == 1
    body = json.loads(uploads[0][3])
    assert body["versions"]["0.1.0"]["name"] == "@demo/core"
    assert list((cfg.root / "dist" / "core").glob("*.tgz")) == files


def test_uv_release_age_cutoff_uses_json_upload_times(single_repo, registry_server, tmp_path):
    import hashlib

    root = single_repo.root
    cfg = config_mod.load(root)
    pkg = packages.discover(cfg)[0]
    files = []
    for version, date in [("0.1.0", "2020-01-01T00:00:00Z"), ("0.2.0", "2099-01-01T00:00:00Z")]:
        from pdum.ship.versions import Version

        item = release.PackagePlan(pkg, None, None, "0.0.0+dev", Version.parse(version), [])
        packages.write_version(root, pkg, version)
        for artifact in release._build(cfg, item, lambda x: None):
            payload = artifact.read_bytes()
            path = "/python/demo-pkg/" + artifact.name
            registry_server["routes"][path] = (200, payload, {"Content-Type": "application/octet-stream"})
            files.append(
                {
                    "filename": artifact.name,
                    "url": registry_server["url"] + path,
                    "size": len(payload),
                    "hashes": {"sha256": hashlib.sha256(payload).hexdigest()},
                    "upload-time": date,
                }
            )
    registry_server["routes"]["/simple/demo-pkg/"] = (
        200,
        {
            "meta": {"api-version": "1.1"},
            "name": "demo-pkg",
            "versions": ["0.1.0", "0.2.0"],
            "files": files,
        },
        {"Content-Type": "application/vnd.pypi.simple.v1+json"},
    )
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    import tomllib

    python_range = tomllib.loads((root / "pyproject.toml").read_text())["project"]["requires-python"]
    (consumer / "pyproject.toml").write_text(
        f'[project]\nname="consumer"\nversion="0"\nrequires-python="{python_range}"\n'
        'dependencies=["demo-pkg>=0.1"]\n[tool.uv]\npackage=false\n'
    )
    proc = subprocess.run(
        [
            "uv",
            "lock",
            "--default-index",
            registry_server["url"] + "/simple/",
            "--exclude-newer",
            "2024-01-01T00:00:00Z",
        ],
        cwd=consumer,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    from pdum.ship.deps import uv_lock_versions

    assert uv_lock_versions(consumer)["demo-pkg"] == frozenset(["0.1.0"])


def test_publish_credentials_follow_upload_origin_and_explicit_override(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\npublish-url="https://upload.example/legacy/"\n[[tool.uv.index]]\nname="private"\nurl="https://read.example/simple/"\npublish-url="https://upload.example/legacy/"\n'
    )
    cfg = Config(root=tmp_path, ours=["demo-*"], python_index="private")
    for key in ("UV_PUBLISH_TOKEN", "UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    targets = []

    def credentials(target):
        targets.append(target.index_url)
        return "writer", "write-secret"

    monkeypatch.setattr(registry, "_credentials", credentials)
    env = registry.python_publish_env(cfg)
    assert targets == ["https://upload.example/legacy/"] and env["UV_PUBLISH_USERNAME"] == "writer"
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "explicit-secret")
    assert registry.python_publish_env(cfg)["UV_PUBLISH_TOKEN"] == "explicit-secret"
    assert len(targets) == 1


def test_dependency_checks_do_not_repurpose_publish_credentials(tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname="demo-pkg"\nversion="0.1.0"\nsource={registry="https://other.example/simple/"}\n'
    )
    cfg = Config(root=tmp_path, ours=["demo-*"])

    def metadata(target, name):
        assert target.use_publish_credentials is False
        return 200, simple_metadata()

    monkeypatch.setattr(registry, "python_metadata", metadata)
    assert registry.check_timestamps(cfg) == []


def test_empty_optional_publish_credentials_do_not_conflict_with_token(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_PUBLISH_USERNAME", "")
    monkeypatch.setenv("UV_PUBLISH_PASSWORD", "")
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "synthetic-token")
    env = registry.python_publish_env(Config(root=tmp_path, ours=["demo-*"]))
    assert env["UV_PUBLISH_TOKEN"] == "synthetic-token"
    assert "UV_PUBLISH_USERNAME" not in env and "UV_PUBLISH_PASSWORD" not in env
