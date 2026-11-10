"""Maintenance scope and candidate retention use ordinary rollback and real Git."""

import json
import subprocess
from dataclasses import replace
from unittest.mock import patch

import pytest

from pdum.ship import config as config_mod
from pdum.ship import deps, packages
from pdum.ship.repo import Repo


def test_refresh_test_selection_is_independent(single_repo):
    config = replace(config_mod.load(single_repo.root), test=["release-test"], refresh_test=["maintenance-test"])
    assert deps.refresh_tests(config) == ["maintenance-test"]
    with patch.object(deps.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
        deps.verify(config, lambda _: None)
    assert run.call_args.args[0] == "maintenance-test"
    assert config.test == ["release-test"]


@pytest.mark.parametrize("value", ['"bad"', "[]", '[""]', "[false]"])
def test_refresh_test_must_select_commands(single_repo, value):
    path = single_repo.root / "pyproject.toml"
    path.write_text(path.read_text().replace("[tool.ship]", f"[tool.ship]\nrefresh-test = {value}"))
    with pytest.raises(config_mod.ConfigError, match="refresh-test"):
        config_mod.load(single_repo.root)


def test_failed_candidate_is_saved_before_rollback(single_repo, tmp_path):
    config = replace(config_mod.load(single_repo.root), ours=["pytest"], refresh_test=["failing-test"])
    lock = single_repo.root / "uv.lock"
    original = lock.read_bytes()
    candidate = original + b"\n# attempted candidate\n"
    report = tmp_path / "report"

    def run(args, **kwargs):
        if isinstance(args, list) and args[:2] == ["uv", "lock"]:
            lock.write_bytes(candidate)
        return subprocess.CompletedProcess(args, 1 if args == "failing-test" else 0)

    with patch.object(deps.subprocess, "run", side_effect=run), patch.object(Repo, "head", return_value="a" * 40):
        with pytest.raises(deps.RefreshError, match="back as they were"):
            deps.refresh(
                Repo(config.root),
                config,
                packages.discover(config),
                everything=False,
                run_tests=True,
                dry_run=False,
                allow_downgrades=False,
                say=lambda _: None,
                report_dir=report,
            )
    assert lock.read_bytes() == original
    data = json.loads((report / "refresh.json").read_text())
    assert data["status"] == "failed" and data["phase"] == "tests" and data["candidate_valid"]
    assert (report / "candidate/uv.lock").read_bytes() == candidate
    assert "# attempted candidate" in (report / "candidate.patch").read_text()
    subprocess.run(["git", "apply", str(report / "candidate.patch")], cwd=config.root, check=True)
    assert lock.read_bytes() == candidate


def test_resolution_failure_has_no_valid_candidate(single_repo, tmp_path):
    config = replace(config_mod.load(single_repo.root), ours=["pytest"])
    lock = single_repo.root / "uv.lock"
    original = lock.read_bytes()
    report = tmp_path / "report"

    def run(args, **kwargs):
        lock.write_bytes(b"partial invalid resolution")
        return subprocess.CompletedProcess(args, 1)

    with patch.object(deps.subprocess, "run", side_effect=run), patch.object(Repo, "head", return_value="b" * 40):
        with pytest.raises(deps.RefreshError):
            deps.refresh(
                Repo(config.root),
                config,
                [],
                everything=False,
                run_tests=True,
                dry_run=False,
                allow_downgrades=False,
                say=lambda _: None,
                report_dir=report,
            )
    assert lock.read_bytes() == original
    data = json.loads((report / "refresh.json").read_text())
    assert not data["candidate_valid"] and data["phase"] == "resolve"
    assert not (report / "candidate.patch").exists()


def test_report_directory_must_not_overwrite_prior_evidence(single_repo, tmp_path):
    config = config_mod.load(single_repo.root)
    report = tmp_path / "report"
    report.mkdir()
    (report / "refresh.json").write_text("old")
    with pytest.raises(deps.RefreshError, match="must be empty"):
        deps.RefreshReport(report, Repo(config.root), config)
    assert (report / "refresh.json").read_text() == "old"


@pytest.mark.parametrize("failure", ["candidate", "failure_report"])
def test_diagnostic_io_failure_does_not_prevent_rollback(single_repo, tmp_path, monkeypatch, failure):
    config = replace(config_mod.load(single_repo.root), ours=["pytest"], refresh_test=["failing-test"])
    lock = single_repo.root / "uv.lock"
    original = lock.read_bytes()

    def run(args, **kwargs):
        if isinstance(args, list) and args[:2] == ["uv", "lock"]:
            lock.write_bytes(original + b"\n# candidate\n")
        return subprocess.CompletedProcess(args, 1 if args == "failing-test" else 0)

    if failure == "candidate":

        def broken_candidate(*args):
            raise OSError("synthetic full disk")

        monkeypatch.setattr(deps.RefreshReport, "candidate", broken_candidate)
    else:
        write = deps.RefreshReport.write

        def broken_write(self, **values):
            if values.get("status") == "failed":
                raise OSError("synthetic full disk")
            return write(self, **values)

        monkeypatch.setattr(deps.RefreshReport, "write", broken_write)
    with patch.object(deps.subprocess, "run", side_effect=run), patch.object(Repo, "head", return_value="d" * 40):
        with pytest.raises((deps.RefreshError, OSError)):
            deps.refresh(
                Repo(config.root),
                config,
                [],
                everything=False,
                run_tests=True,
                dry_run=False,
                allow_downgrades=False,
                say=lambda _: None,
                report_dir=tmp_path / "report",
            )
    assert lock.read_bytes() == original


def test_tests_must_not_change_resolved_lockfile(single_repo, tmp_path):
    config = replace(config_mod.load(single_repo.root), ours=["pytest"], refresh_test=["mutating-test"])
    lock = single_repo.root / "uv.lock"
    original = lock.read_bytes()

    def run(args, **kwargs):
        if isinstance(args, list) and args[:2] == ["uv", "lock"]:
            lock.write_bytes(original + b"\n# candidate\n")
        elif args == "mutating-test":
            lock.write_bytes(original + b"\n# untested alteration\n")
        return subprocess.CompletedProcess(args, 0)

    with patch.object(deps.subprocess, "run", side_effect=run), patch.object(Repo, "head", return_value="e" * 40):
        with pytest.raises(deps.RefreshError, match="changed the resolved candidate"):
            deps.refresh(
                Repo(config.root),
                config,
                [],
                everything=False,
                run_tests=True,
                dry_run=False,
                allow_downgrades=False,
                say=lambda _: None,
                report_dir=tmp_path / "report",
            )
    assert lock.read_bytes() == original
    assert (tmp_path / "report/candidate/uv.lock").read_bytes() == original + b"\n# candidate\n"
