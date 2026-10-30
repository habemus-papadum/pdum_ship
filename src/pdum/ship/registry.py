"""Registry selection and metadata, using native uv/pnpm configuration and credentials.

No package or organization names belong here. A release checks the same registry it
uploads to. Authentication failures and unavailable metadata are never treated as absence.
"""

from __future__ import annotations

import base64
import json
import netrc
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename

from . import __version__
from .config import Config
from .packages import Package

TIMEOUT = 15
PYPI_INDEX = "https://pypi.org/simple/"
PYPI_PUBLISH = "https://upload.pypi.org/legacy/"
NPM_REGISTRY = "https://registry.npmjs.org/"


class RegistryError(ValueError):
    """Missing or contradictory non-secret registry configuration."""


def _url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise RegistryError("registry URLs must not contain credentials, query strings or fragments")
    if not parts.hostname or (
        parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"))
    ):
        raise RegistryError("registry URLs must use HTTPS (HTTP is allowed only for local tests)")
    return value.rstrip("/") + "/"


def _origin(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.netloc


def uv_settings(config: Config) -> dict:
    native = config.root / "uv.toml"
    if native.exists():
        return tomllib.loads(native.read_text())
    project = config.root / "pyproject.toml"
    return tomllib.loads(project.read_text()).get("tool", {}).get("uv", {}) if project.exists() else {}


@dataclass(frozen=True)
class PythonRegistry:
    index_url: str
    publish_url: str
    name: str | None = None
    use_publish_credentials: bool = True


def python_target(config: Config) -> PythonRegistry:
    uv = uv_settings(config)
    indexes = uv.get("index", [])
    selected = os.environ.get("UV_PUBLISH_INDEX") or config.python_index
    publish = os.environ.get("UV_PUBLISH_URL") or uv.get("publish-url")
    if selected:
        index = next((i for i in indexes if i.get("name") == selected), None)
        if index is None or not (publish or index.get("publish-url")):
            raise RegistryError("python-index must name a uv index with a publish-url")
        return PythonRegistry(_url(index["url"]), _url(publish or index["publish-url"]), selected)
    publish = _url(publish or PYPI_PUBLISH)
    if config.index_url:
        index = next((i for i in indexes if _url(i["url"]) == _url(config.index_url)), {})
        if publish == PYPI_PUBLISH and _url(config.index_url) != PYPI_INDEX:
            raise RegistryError("a private index-url also needs uv publish-url or python-index")
        return PythonRegistry(_url(config.index_url), publish, index.get("name"))
    if publish == PYPI_PUBLISH:
        return PythonRegistry(PYPI_INDEX, publish)
    matches = [i for i in indexes if i.get("publish-url") and _url(i["publish-url"]) == publish]
    if not matches:
        matches = [i for i in indexes if _origin(_url(i["url"])) == _origin(publish)]
    if len(matches) != 1:
        raise RegistryError(
            "private Python publication needs an unambiguous uv index; set [tool.ship] python-index or index-url"
        )
    index = matches[0]
    return PythonRegistry(_url(index["url"]), publish, index.get("name"))


def _credentials(target: PythonRegistry) -> tuple[str, str] | None:
    """Named-index env, publishing env, netrc, then uv's native credential store.

    Values remain in memory. Native keyring lookup is delegated to uv when a
    username is supplied; no keyring backend or second credential store is added.
    """
    user = password = None
    if target.name:
        prefix = "UV_INDEX_" + re.sub(r"[^A-Za-z0-9]", "_", target.name).upper()
        user, password = os.environ.get(prefix + "_USERNAME"), os.environ.get(prefix + "_PASSWORD")
    if password is not None:
        if not user:
            raise RegistryError("named index password requires its UV_INDEX_<NAME>_USERNAME")
        return user, password
    if target.use_publish_credentials and _origin(target.index_url) == _origin(target.publish_url):
        publish_user, publish_password = os.environ.get("UV_PUBLISH_USERNAME"), os.environ.get("UV_PUBLISH_PASSWORD")
        if publish_user and publish_password:
            return publish_user, publish_password
        token = os.environ.get("UV_PUBLISH_TOKEN")
        if token:
            return "__token__", token
    try:
        entry = netrc.netrc(os.environ.get("NETRC")).authenticators(urllib.parse.urlsplit(target.index_url).hostname)
        if entry and (not user or user == entry[0]):
            return entry[0], entry[2]
    except (OSError, netrc.NetrcParseError):
        pass
    # uv auth token requires a username; the native plaintext store identifies it.
    proc = subprocess.run(["uv", "auth", "dir"], capture_output=True, text=True)
    if proc.returncode == 0:
        path = Path(proc.stdout.strip()) / "credentials.toml"
        if path.exists():
            try:
                entries = tomllib.loads(path.read_text()).get("credential", [])
                users = {
                    e["username"]
                    for e in entries
                    if _origin(e["service"]) == _origin(target.index_url) and (not user or user == e["username"])
                }
            except (OSError, ValueError, KeyError):
                users = set()
            if len(users) == 1:
                user = users.pop()
            elif len(users) > 1:
                raise RegistryError("multiple uv registry identities; select UV_INDEX_<NAME>_USERNAME")
    if user:
        proc = subprocess.run(
            ["uv", "auth", "token", target.index_url, "--username", user], capture_output=True, text=True
        )
        if proc.returncode == 0:
            return user, proc.stdout.strip()
    return None


def python_publish_env(config: Config) -> dict[str, str]:
    target = python_target(config)
    env = dict(os.environ)
    if env.get("UV_PUBLISH_TOKEN") or env.get("UV_PUBLISH_PASSWORD"):
        return env
    if target.index_url != PYPI_INDEX:
        # Stored HTTP credentials are scoped to the upload origin. A named-index
        # environment credential explicitly applies to that index's endpoints.
        credentials = _credentials(PythonRegistry(target.publish_url, target.publish_url, target.name))
        if credentials:
            env.pop("UV_PUBLISH_TOKEN", None)
            env["UV_PUBLISH_USERNAME"], env["UV_PUBLISH_PASSWORD"] = credentials
    return env


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not follow login redirects or forward registry credentials elsewhere.
        return None


def _get_json(
    url: str, *, accept: str = "application/json", credentials: tuple[str, str] | None = None
) -> tuple[int, dict | None]:
    headers = {"Accept": accept, "User-Agent": f"habemus-papadum-ship/{__version__}"}
    if credentials:
        headers["Authorization"] = "Basic " + base64.b64encode(":".join(credentials).encode()).decode()
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=TIMEOUT) as response:
            data = json.load(response)
            return response.status, data if isinstance(data, dict) else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, None


def python_metadata(target: PythonRegistry, name: str) -> tuple[int, dict | None]:
    return _get_json(
        target.index_url + canonicalize_name(name) + "/",
        accept="application/vnd.pypi.simple.v1+json",
        credentials=None if target.index_url == PYPI_INDEX else _credentials(target),
    )


def _file_version(filename: str) -> str | None:
    try:
        return str(
            parse_wheel_filename(filename)[1] if filename.endswith(".whl") else parse_sdist_filename(filename)[1]
        )
    except ValueError:
        return None


def _python_versions(data: dict) -> set[str]:
    return {v for f in data.get("files", []) if (v := _file_version(f.get("filename", "")))}


def npm_target(config: Config, name: str, package: Package | None = None) -> str:
    if package:
        manifest = json.loads((config.root / package.manifest).read_text())
        explicit = manifest.get("publishConfig", {}).get("registry")
        if explicit:
            return _url(explicit)
    keys = ([name.split("/")[0] + ":registry"] if name.startswith("@") else []) + ["registry"]
    for key in keys:
        proc = subprocess.run(["pnpm", "config", "get", key], cwd=config.pnpm_dir, capture_output=True, text=True)
        value = proc.stdout.strip()
        if proc.returncode:
            raise RegistryError("could not read pnpm registry configuration")
        if value and value not in ("undefined", "null"):
            return _url(value)
    return NPM_REGISTRY


def npm_metadata(config: Config, name: str, url: str) -> tuple[int, dict | None]:
    if url == NPM_REGISTRY:
        return _get_json(url + urllib.parse.quote(name, safe="@"))
    # pnpm owns .npmrc precedence, environment expansion and Basic/Bearer auth.
    # In pnpm 11, env-backed credentials belong in trusted user config (including
    # setup-node's NPM_CONFIG_USERCONFIG), not a committed project .npmrc.
    # Capture errors: native diagnostics may contain authentication material.
    proc = subprocess.run(
        ["pnpm", "view", name, "--json", "--registry", url], cwd=config.pnpm_dir, capture_output=True, text=True
    )
    if proc.returncode:
        try:
            error = json.loads(proc.stdout).get("error", {}).get("code", "")
        except ValueError:
            error = ""
        return (404 if error in ("E404", "ERR_PNPM_FETCH_404") else 0), None
    try:
        data = json.loads(proc.stdout)
        return 200, data if isinstance(data, dict) else None
    except ValueError:
        return 0, None


def has(config: Config, package: Package, version: str) -> bool | None:
    if package.ecosystem == "python":
        status, data = python_metadata(python_target(config), package.name)
        versions = _python_versions(data) if data and isinstance(data.get("files"), list) else None
    else:
        status, data = npm_metadata(config, package.name, npm_target(config, package.name, package))
        versions = data.get("versions") if data else None
    if status == 404:
        return False
    if status != 200 or versions is None:
        return None
    return version in versions


def _valid_time(value) -> bool:
    try:
        return (
            isinstance(value, str)
            and value.endswith("Z")
            and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
        )
    except ValueError:
        return False


def timestamp_problems(ecosystem: str, name: str, data: dict) -> list[str]:
    if ecosystem == "python":
        files = data.get("files")
        if not isinstance(files, list) or not files:
            return [f"{name}: Python index returned no file metadata"]
        if any(not _valid_time(f.get("upload-time")) for f in files):
            return [f"{name}: Python index files lack valid upload-time metadata"]
    else:
        versions, times = data.get("versions"), data.get("time", {})
        if not versions or not isinstance(times, dict) or any(not _valid_time(times.get(v)) for v in versions):
            return [f"{name}: npm registry versions lack valid publication times"]
    return []


def check_timestamps(config: Config) -> list[str]:
    """Check private registries actually used by locked first-party dependencies."""
    problems = []
    lock = config.root / "uv.lock"
    indexes = uv_settings(config).get("index", [])
    checked = set()
    if lock.exists():
        for package in tomllib.loads(lock.read_text()).get("package", []):
            url = package.get("source", {}).get("registry")
            name = package.get("name", "")
            if not url or not config.is_ours(name) or _url(url) == PYPI_INDEX or (url, name) in checked:
                continue
            checked.add((url, name))
            index = next((i for i in indexes if _url(i["url"]) == _url(url)), {})
            target = PythonRegistry(_url(url), _url(index.get("publish-url", url)), index.get("name"), False)
            status, data = python_metadata(target, name)
            problems.extend(
                timestamp_problems("python", name, data)
                if status == 200 and data
                else [f"{name}: could not verify private Python index publication times"]
            )
    from .deps import pnpm_lock_versions

    scopes = {}
    for name in pnpm_lock_versions(config.pnpm_dir):
        if not config.is_ours(name):
            continue
        scope = name.split("/")[0] if name.startswith("@") else ""
        if scope not in scopes:
            scopes[scope] = npm_target(config, name)
        url = scopes[scope]
        if url == NPM_REGISTRY:
            continue
        status, data = npm_metadata(config, name, url)
        problems.extend(
            timestamp_problems("npm", name, data)
            if status == 200 and data
            else [f"{name}: could not verify private npm registry publication times"]
        )
    return problems
