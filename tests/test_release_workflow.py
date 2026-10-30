"""Exercise the actual reusable workflow's gate and shell, without publishing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/release.yml").read_text())
JOB = WORKFLOW["jobs"]["release"]
ACTION_DIR = Path(__file__).parents[1] / ".github/actions/release"
ACTION = yaml.safe_load((ACTION_DIR / "action.yml").read_text())
GATE = (ACTION_DIR / "check_ci.py").read_text()
RELEASE = next(step for step in ACTION["runs"]["steps"] if step.get("name") == "ship release")


@pytest.fixture
def gate():
    namespace = {"__name__": "workflow_test"}
    exec(compile(GATE, "check_ci.py", "exec"), namespace)
    return namespace


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    git("init", "-q", "-b", "main")

    def commit(files):
        for name, content in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        git("add", ".")
        git("commit", "-qm", "change")
        return git("rev-parse", "HEAD")

    return commit, git


def manifest(version="0.1.0+dev", dependency=">=0.1"):
    return json.dumps({"name": "@demo/a", "version": version, "dependencies": {"demo": dependency}})


def test_ci_reused_across_multiple_independent_npm_releases(gate, history):
    commit, _ = history
    base = commit({"packages/a/package.json": manifest(), "packages/b/package.json": manifest()})
    release_a = commit({"packages/a/package.json": manifest("0.1.1")})
    restore_a = commit({"packages/a/package.json": manifest("0.1.1+dev")})
    release_b = commit({"packages/b/package.json": manifest("0.2.0")})
    head = commit({"packages/b/package.json": manifest("0.2.0+dev")})
    assert gate["candidates"](head) == [head, release_b, restore_a, release_a, base]


def python_files(version, external="1.0.0"):
    return {
        "pyproject.toml": f'[project]\nname = "demo"\nversion = "{version}"\n',
        "uv.lock": (
            f'[[package]]\nname = "demo"\nversion = "{version}"\nsource = {{ editable = "." }}\n'
            f'[[package]]\nname = "other"\nversion = "{external}"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
        ),
    }


def test_python_release_versions_are_allowed_but_external_lock_changes_are_not(gate, history):
    commit, _ = history
    base = commit(python_files("0.1.0+dev"))
    released = commit(python_files("0.1.1"))
    head = commit(python_files("0.1.1+dev"))
    assert gate["candidates"](head) == [head, released, base]
    changed = commit(python_files("0.1.1+dev", external="2.0.0"))
    assert gate["candidates"](changed) == [changed]


@pytest.mark.parametrize(
    "files",
    [
        {"src/code.py": "raise RuntimeError()\n"},
        {"packages/a/package.json": manifest("0.1.1", dependency=">=0.2")},
        {".github/workflows/ci.yml": "changed workflow\n"},
        {"pnpm-lock.yaml": "changed lock\n"},
        {"packages/new/package.json": manifest()},
        {"pyproject.toml": '[project]\nversion = "0.1.1"\n[tool.ship]\ntest = []\n'},
    ],
)
def test_source_dependency_configuration_and_new_package_changes_need_ci(gate, history, files):
    commit, _ = history
    commit({"packages/a/package.json": manifest(), **python_files("0.1.0+dev")})
    head = commit(files)
    assert gate["candidates"](head) == [head]


@pytest.mark.parametrize("operation", ["delete", "mode"])
def test_deletion_and_file_mode_changes_need_ci(gate, history, operation):
    commit, git = history
    commit({"package.json": manifest()})
    if operation == "delete":
        git("rm", "package.json")
    else:
        git("update-index", "--chmod=+x", "package.json")
    git("commit", "-qm", operation)
    head = git("rev-parse", "HEAD")
    assert gate["candidates"](head) == [head]


def run(sha, status="completed", conclusion="success", event="push"):
    return {
        "headSha": sha,
        "status": status,
        "conclusion": conclusion,
        "event": event,
        "url": "https://example.test/run",
    }


@pytest.mark.parametrize("status,conclusion", [("completed", "failure"), ("in_progress", "")])
def test_current_failed_or_pending_ci_takes_precedence_over_ancestor_success(gate, status, conclusion):
    current = run("head", status, conclusion)
    assert gate["choose_run"](["head", "base"], [run("base"), current]) == current


def test_manual_ci_is_accepted_but_pr_and_unrelated_runs_are_not(gate):
    manual = run("base", event="workflow_dispatch")
    assert gate["choose_run"](["head", "base"], [run("head", event="pull_request"), manual]) == manual
    assert gate["choose_run"](["head"], [run("unrelated")]) is None


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
def test_gate_exit_reflects_ci_result(gate, monkeypatch, conclusion):
    monkeypatch.setenv("SHA", "head")
    monkeypatch.setenv("BRANCH", "main")
    monkeypatch.setenv("WORKFLOW", "ci.yml")
    gate["candidates"] = lambda head: [head, "base"]
    monkeypatch.setattr(
        gate["subprocess"], "check_output", lambda *a, **kw: json.dumps([run("base", conclusion=conclusion)])
    )
    if conclusion == "success":
        gate["main"]()
    else:
        with pytest.raises(SystemExit, match=conclusion):
            gate["main"]()


@pytest.mark.parametrize("failure", ["missing", "api"])
def test_missing_ci_or_api_failure_never_passes(gate, monkeypatch, failure):
    monkeypatch.setenv("SHA", "head")
    monkeypatch.setenv("BRANCH", "main")
    monkeypatch.setenv("WORKFLOW", "ci.yml")
    gate["candidates"] = lambda head: [head]

    def query(*args, **kwargs):
        if failure == "api":
            raise subprocess.CalledProcessError(1, "gh")
        return "[]"

    monkeypatch.setattr(gate["subprocess"], "check_output", query)
    monkeypatch.setattr(gate["time"], "sleep", lambda seconds: None)
    with pytest.raises((SystemExit, subprocess.CalledProcessError)):
        gate["main"]()


def test_ship_failure_survives_summary_pipeline(tmp_path):
    # Execute the workflow's actual command with a failing stand-in for uv.
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/sh\necho "publish failed" >&2\nexit 23\n')
    uv.chmod(0o755)
    script = RELEASE["run"].replace("${{ inputs.dry_run }}", "false").replace("${{ inputs.skip_tests }}", "false")
    shell = RELEASE.get("shell", JOB.get("defaults", {}).get("run", {}).get("shell"))
    args = ["bash", "--noprofile", "--norc", "-eo", "pipefail"] if shell == "bash" else ["bash", "-e"]
    summary = tmp_path / "summary"
    proc = subprocess.run(
        [*args, "-c", script],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "PACKAGE": "demo",
            "BUMP": "patch",
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 23, proc.stdout + proc.stderr
    assert "publish failed" in summary.read_text()


def test_private_auth_setup_preserves_scope_routing_and_keeps_secrets_in_env(tmp_path, monkeypatch):
    step = next(s for s in JOB["steps"] if s.get("name") == "Configure private registry credentials")
    config = tmp_path / "npmrc"
    config.write_text("registry=https://packages.example/npm/\n//packages.example/npm/:_authToken=${NODE_AUTH_TOKEN}\n")
    for key in ("UV_PUBLISH_USERNAME", "UV_PUBLISH_PASSWORD", "NODE_AUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NPM_REGISTRY", "https://packages.example/npm/")
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(config))
    monkeypatch.setenv("NPM_BASIC_AUTH", "synthetic-secret-value")
    monkeypatch.setenv("PYTHON_REGISTRY", "")
    exec(compile(step["run"], "release.yml:private-auth", "exec"), {"__name__": "workflow_test"})
    text = config.read_text()
    assert "registry=" not in text
    assert "//packages.example/npm/:_auth=${NPM_BASIC_AUTH}" in text
    assert "synthetic-secret-value" not in text


def test_ship_self_release_provisions_pnpm_without_a_node_workspace(tmp_path):
    events = WORKFLOW.get("on", WORKFLOW.get(True))
    default = events["workflow_dispatch"]["inputs"]["setup_pnpm"]["default"]
    detect = next(step for step in JOB["steps"] if step.get("id") == "pnpm")
    script = detect["run"].replace("${{ inputs.setup_pnpm }}", str(default).lower())
    (tmp_path / "pyproject.toml").write_text('[tool.ship]\nours=["demo-*"]\n')
    output = tmp_path / "outputs"
    subprocess.run(
        ["bash", "-e", "-c", script], cwd=tmp_path, env={**os.environ, "GITHUB_OUTPUT": str(output)}, check=True
    )
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["present"] == "true" and values["pinned"] == "false"
    assert "locked" not in values
