"""Real local Git pushes with fake package-manager/GitHub boundaries."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "refresh_action", Path(__file__).parents[1] / ".github/actions/refresh/refresh.py"
)
action = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action)


@pytest.fixture
def weekly_repo(single_repo):
    single_repo.write(".github/workflows/package-ci.yml", "on:\n  workflow_dispatch:\njobs: {}\n")
    single_repo.commit_all("CI accepts explicit dispatch")
    single_repo.git("push", "origin", "main")
    return single_repo


class Harness(action.Runner):
    def __init__(self, root, scenario="pass"):
        super().__init__(root, ".ship-refresh", "package-ci.yml")
        self.scenario = scenario
        self.calls = []
        self.pr = None
        self.old = (root / "uv.lock").read_bytes()
        self.new = self.old + b"\n# tested candidate\n"

    def output(self, args):
        if args[:4] == ["uv", "run", "--locked", "python"]:
            return "no" if self.scenario == "missing-dispatch" else "yes"
        if args[:3] == ["gh", "pr", "list"]:
            return json.dumps([self.pr] if self.pr else [])
        return super().output(args)

    def capture(self, args):
        self.calls.append(args)
        if args[:3] == ["gh", "pr", "create"]:
            if self.scenario == "pr-denied":
                raise action.IntegrationError("synthetic PR permission denial")
            self.pr = {"number": 123, "url": "https://example.invalid/pull/123", "body": "candidate"}
            return self.pr["url"]
        return super().capture(args)

    def run(self, args):
        self.calls.append(args)
        if args[0] == "git":
            return super().run(args)
        if self.scenario == "baseline" and args[-2:] == ["deps", "verify"]:
            return 1
        if args[-2:] == ["deps", "verify"] and self.phase == "candidate_tests":
            if self.scenario in {"failed", "pr-denied"}:
                return 1
            if self.scenario == "mutated-lock":
                (self.root / "uv.lock").write_bytes(b"untested lock")
        if "refresh" in args and args[0] == "uv":
            report = self.report_dir / "refresh"
            report.mkdir()
            data = {
                "source_commit": self.base,
                "status": "passed",
                "phase": "complete",
                "candidate_valid": self.scenario != "resolve",
                "lockfiles": [],
                "moves": [],
            }
            if self.scenario != "noop":
                data["lockfiles"] = ["uv.lock"] if data["candidate_valid"] else []
                (self.root / "uv.lock").write_bytes(self.new)
                (report / "candidate").mkdir()
                (report / "candidate/uv.lock").write_bytes(self.new)
            if self.scenario == "resolve":
                data.update(status="failed", phase="resolve" if self.scenario == "resolve" else "tests")
                (self.root / "uv.lock").write_bytes(self.old)
            if self.scenario == "source-write":
                (self.root / "README.md").write_text("unexpected mutation")
            (report / "refresh.json").write_text(json.dumps(data))
            return 1 if self.scenario == "resolve" else 0
        if self.scenario == "pr-denied" and args[:3] == ["gh", "pr", "create"]:
            return 1
        if self.scenario == "post-policy" and args[-2:] == ["deps", "check"] and self.phase == "candidate_policy":
            return 1
        if self.scenario == "dispatch-denied" and args[:3] == ["gh", "workflow", "run"]:
            return 1
        if self.scenario == "skills-fail" and args[-2:] == ["skills", "sync"]:
            return 1
        if self.scenario == "skills" and args[-2:] == ["skills", "sync"]:
            (self.root / ".agents/skills/ship-release/README.md").write_text("new bundled documentation")
        return 0


def remote_file(repo, path="uv.lock", branch="main"):
    return subprocess.check_output(["git", "show", f"{branch}:{path}"], cwd=repo.origin)


def test_pass_commits_only_candidate_and_dispatches_ci(weekly_repo):
    h = Harness(weekly_repo.root)
    h.execute()
    assert remote_file(weekly_repo) == h.new
    assert h.result["status"] == "committed"
    assert ["gh", "workflow", "run", "package-ci.yml", "--ref", "main"] in h.calls
    assert not any(call[:3] == ["gh", "pr", "create"] for call in h.calls)
    assert weekly_repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split() == ["uv.lock"]


def test_generated_skill_copies_are_checked_and_committed(weekly_repo):
    h = Harness(weekly_repo.root, "skills")
    h.execute()
    assert remote_file(weekly_repo, ".agents/skills/ship-release/README.md") == b"new bundled documentation"
    assert h.calls.index(["uv", "run", "--locked", "ship", "skills", "sync"]) < max(
        i for i, call in enumerate(h.calls) if call[-2:] == ["deps", "check"]
    )


def test_noop_does_not_commit_or_dispatch(weekly_repo):
    h = Harness(weekly_repo.root, "noop")
    before = weekly_repo.git("rev-parse", "HEAD")
    h.execute()
    assert weekly_repo.git("rev-parse", "HEAD") == before
    assert h.result["status"] == "unchanged"
    assert not any(call[0] in {"git", "gh"} for call in h.calls)


@pytest.mark.parametrize("scenario", ["failed", "post-policy", "skills-fail"])
def test_failed_candidate_creates_draft_pr_without_main_update(weekly_repo, scenario):
    h = Harness(weekly_repo.root, scenario)
    before = remote_file(weekly_repo)
    with pytest.raises(action.IntegrationError):
        h.execute()
    assert remote_file(weekly_repo) == before
    assert remote_file(weekly_repo, branch=h.branch_name) == h.new
    create = next(call for call in h.calls if call[:3] == ["gh", "pr", "create"])
    assert "--draft" in create
    body = (h.report_dir / "pr-body.md").read_text()
    assert h.base in body and "ship deps verify" in body
    assert h.result["update_status"] == "rejected"
    assert h.result["pr_status"] == "created"
    assert h.result["candidate_pr"] == "https://example.invalid/pull/123"
    assert "Draft PR creation: **succeeded**" in h.summary()
    assert "workflow remains failed" in h.summary()
    assert "not to creation of this draft" in body


@pytest.mark.parametrize("scenario", ["baseline", "resolve"])
def test_failure_without_candidate_does_not_create_pr(weekly_repo, scenario):
    h = Harness(weekly_repo.root, scenario)
    before = remote_file(weekly_repo)
    with pytest.raises(action.IntegrationError):
        h.execute()
    assert remote_file(weekly_repo) == before
    assert not any(call[:3] == ["gh", "pr", "create"] for call in h.calls)
    if scenario == "baseline":
        assert not any("refresh" in call for call in h.calls)


def test_pr_permission_failure_retains_branch_and_diagnostics(weekly_repo):
    h = Harness(weekly_repo.root, "pr-denied")
    before = remote_file(weekly_repo)
    with pytest.raises(action.IntegrationError):
        h.execute()
    assert remote_file(weekly_repo) == before
    assert remote_file(weekly_repo, branch=h.branch_name) == h.new
    assert h.result["candidate_branch"] == h.branch_name
    assert h.result["candidate_commit"]
    assert h.result["update_status"] == "rejected"
    assert h.result["rejection_phase"] == "candidate_tests"
    assert h.result["pr_status"] == "creation_failed"
    assert "Draft PR creation: **failed**" in h.summary()


def test_existing_pr_is_preserved_and_freezes_refresh(weekly_repo):
    h = Harness(weekly_repo.root)
    h.pr = {"number": 123, "url": "https://example.invalid/pull/123", "body": "agent work"}
    with pytest.raises(action.IntegrationError, match="existing refresh PR preserved"):
        h.execute()
    assert h.calls == []
    assert h.pr["body"] == "agent work"
    assert h.result["existing_pr"] == h.pr["url"]


def test_remote_advance_never_rebases_or_pushes(weekly_repo):
    h = Harness(weekly_repo.root)
    calls = 0
    original = h.remote_head

    def head(branch):
        nonlocal calls
        if branch == "main":
            calls += 1
            if calls == 2:
                return "c" * 40
        return original(branch)

    h.remote_head = head
    before = remote_file(weekly_repo)
    with pytest.raises(action.IntegrationError, match="advanced after verification"):
        h.execute()
    assert remote_file(weekly_repo) == before
    assert not any(call[0] == "git" for call in h.calls)


def test_unexpected_source_changes_are_not_committed_or_proposed(weekly_repo):
    h = Harness(weekly_repo.root, "source-write")
    before = remote_file(weekly_repo)
    with pytest.raises(action.IntegrationError, match="unexpected changes"):
        h.execute()
    assert remote_file(weekly_repo) == before
    assert not any(call[0] in {"git", "gh"} for call in h.calls)


def test_dispatch_failure_does_not_hide_completed_commit(weekly_repo):
    h = Harness(weekly_repo.root, "dispatch-denied")
    with pytest.raises(action.IntegrationError):
        h.execute()
    assert remote_file(weekly_repo) == h.new
    assert h.result["commit"]
    assert h.phase == "dispatch_ci"


@pytest.mark.parametrize("directory", [".", "..", "/tmp", ".git/report", "a/../../b", "report\npath"])
def test_invalid_report_path_never_exposes_artifact_output(weekly_repo, monkeypatch, tmp_path, directory):
    output = tmp_path / "outputs"
    output.touch()
    monkeypatch.chdir(weekly_repo.root)
    monkeypatch.setenv("SHIP_REPORT_DIR", directory)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert action.main() == 1
    assert output.read_text() == ""


def test_existing_or_symlink_report_directory_is_not_uploaded(weekly_repo, monkeypatch, tmp_path):
    output = tmp_path / "outputs"
    output.touch()
    monkeypatch.chdir(weekly_repo.root)
    monkeypatch.setenv("SHIP_REPORT_DIR", ".ship-refresh")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    report = weekly_repo.root / ".ship-refresh"
    report.symlink_to(tmp_path)
    assert action.main() == 1
    assert output.read_text() == ""
    report.unlink()
    report.mkdir()
    (report / "sensitive").write_text("synthetic private content")
    assert action.main() == 1
    assert output.read_text() == ""


def test_action_uploads_only_validated_output():
    text = (Path(__file__).parents[1] / ".github/actions/refresh/action.yml").read_text()
    assert "path: ${{ steps.refresh.outputs.report_dir }}" in text
    assert "always() && steps.refresh.outputs.report_dir != ''" in text
    assert "path: ${{ inputs.report_dir }}" not in text


def test_candidate_verification_cannot_commit_untested_lock_mutation(weekly_repo):
    h = Harness(weekly_repo.root, "mutated-lock")
    before = remote_file(weekly_repo)
    with pytest.raises(action.IntegrationError, match="changed the resolved candidate"):
        h.execute()
    assert remote_file(weekly_repo) == before
    assert remote_file(weekly_repo, branch=h.branch_name) == h.new
    assert not any(call[:3] == ["gh", "workflow", "run"] for call in h.calls)


def test_artifact_validation_rejects_late_symlink(weekly_repo, tmp_path):
    h = Harness(weekly_repo.root)
    h.report_dir.mkdir()
    (h.report_dir / "escape").symlink_to(tmp_path)
    with pytest.raises(action.IntegrationError, match="safe to upload"):
        h.artifact_path()


def test_preexisting_human_candidate_branch_is_not_overwritten(weekly_repo):
    weekly_repo.git("branch", "ship/deps-refresh")
    weekly_repo.git("push", "origin", "ship/deps-refresh")
    h = Harness(weekly_repo.root)
    before = h.remote_head(h.branch_name)
    with pytest.raises(action.IntegrationError, match="branch already exists"):
        h.execute()
    assert h.remote_head(h.branch_name) == before
    assert h.calls == []


def test_candidate_symlink_is_never_read_or_pushed(weekly_repo, tmp_path):
    h = Harness(weekly_repo.root)
    h.base = weekly_repo.git("rev-parse", "HEAD").strip()
    directory = h.report_dir / "refresh"
    directory.mkdir(parents=True)
    (directory / "refresh.json").write_text(
        json.dumps({"source_commit": h.base, "candidate_valid": True, "lockfiles": ["uv.lock"]})
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "uv.lock").write_text("synthetic private content")
    (directory / "candidate").symlink_to(outside)
    with pytest.raises(action.IntegrationError, match="missing or is a symlink"):
        h.candidate_pr()
    assert remote_file(weekly_repo) == h.old
    assert not any(call[0] in {"git", "gh"} for call in h.calls)


def test_missing_dispatch_fails_before_lock_update(weekly_repo):
    h = Harness(weekly_repo.root, "missing-dispatch")
    before = remote_file(weekly_repo)
    with pytest.raises(action.IntegrationError, match="workflow_dispatch"):
        h.execute()
    assert remote_file(weekly_repo) == before
    assert not any("refresh" in call for call in h.calls)


def test_unchanged_locks_never_generate_skills(weekly_repo):
    import shutil

    shutil.rmtree(weekly_repo.root / ".agents/skills")
    weekly_repo.commit_all("fixture without generated copies")
    weekly_repo.git("push", "origin", "main")
    h = Harness(weekly_repo.root, "noop")
    h.execute()
    assert not (weekly_repo.root / ".agents/skills").exists()
    assert not any(call[-2:] == ["skills", "sync"] for call in h.calls)
    assert h.result["status"] == "unchanged"
