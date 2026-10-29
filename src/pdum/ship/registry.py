"""Ask a registry whether it already has a version, and what its newest version is.

Returns ``None`` when the answer is unknown (network failure, unexpected status) so the
caller can decide whether that blocks.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT = 10


def _get_json(url: str) -> tuple[int, dict | None]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return 0, None


def pypi_has(name: str, version: str) -> bool | None:
    status, _ = _get_json(f"https://pypi.org/pypi/{name}/{version}/json")
    if status == 200:
        return True
    if status == 404:
        return False
    return None


def pypi_latest(name: str) -> str | None:
    status, data = _get_json(f"https://pypi.org/pypi/{name}/json")
    if status == 200 and data:
        return data.get("info", {}).get("version")
    return None


def npm_has(name: str, version: str) -> bool | None:
    status, _ = _get_json(f"https://registry.npmjs.org/{name}/{version}")
    if status == 200:
        return True
    if status == 404:
        return False
    return None


def npm_latest(name: str) -> str | None:
    status, data = _get_json(f"https://registry.npmjs.org/{name}/latest")
    if status == 200 and data:
        return data.get("version")
    return None


def has(ecosystem: str, name: str, version: str) -> bool | None:
    return pypi_has(name, version) if ecosystem == "python" else npm_has(name, version)


def latest(ecosystem: str, name: str) -> str | None:
    return pypi_latest(name) if ecosystem == "python" else npm_latest(name)
