"""GitHub orchestration; native credentials and toolchains are prepared by the caller.

Only dependency locks and Ship's generated skill copies may reach commits. A
failure never becomes success because reporting or PR creation succeeded.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path


class IntegrationError(RuntimeError):
    pass


class Runner:
    branch_name = "ship/deps-refresh"

    def __init__(self, root: Path, report_dir: str, ci_workflow: str):
        self.root = root.resolve()
        relative = Path(report_dir)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or ".git" in relative.parts
            or relative == Path(".")
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", report_dir)
        ):
            raise IntegrationError("report_dir must be a repository-relative diagnostics directory")
        self.report_dir = self.root / relative
        if self.report_dir.resolve() != self.report_dir or self.report_dir.exists():
            raise IntegrationError("report_dir already exists; use a fresh checkout or a new report directory")
        self.ci_workflow = ci_workflow
        self.config = tomllib.loads((root / "pyproject.toml").read_text()).get("tool", {}).get("ship", {})
        self.branch = self.config.get("branch", "main")
        self.remote = self.config.get("remote", "origin")
        self.locks = ["uv.lock", (Path(self.config.get("pnpm-root", ".")) / "pnpm-lock.yaml").as_posix()]
        if any(Path(path).is_absolute() or ".." in Path(path).parts for path in self.locks):
            raise IntegrationError("lockfiles must be inside this checkout")
        self.base = ""
        self.phase = "preflight"
        self.result = {"schema": 1, "status": "running", "phase": self.phase}
        self.run_url = (
            f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
            f"{os.environ.get('GITHUB_REPOSITORY', os.environ.get('GH_REPO', ''))}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID', '')}"
        )

    def artifact_path(self) -> str | None:
        directory = self.report_dir
        if not directory.exists():
            return None
        if (
            directory.resolve() != directory
            or not directory.is_dir()
            or not directory.is_relative_to(self.root)
            or any(path.is_symlink() for path in directory.rglob("*"))
        ):
            raise IntegrationError("diagnostics directory is not safe to upload")
        return directory.relative_to(self.root).as_posix()

    def output(self, args: list[str]) -> str:
        proc = subprocess.run(args, cwd=self.root, text=True, capture_output=True)
        if proc.returncode:
            # Do not embed native output/credentials in artifacts. The command's
            # own stdout belongs in the GitHub log where secret masking applies.
            raise IntegrationError(f"{args[0]} command failed ({proc.returncode}); see the workflow log")
        return proc.stdout.strip()

    def run(self, args: list[str]) -> int:
        print("$ " + " ".join(args), flush=True)
        return subprocess.run(args, cwd=self.root).returncode

    def git(self, *args: str) -> str:
        return self.output(["git", *args])

    def require(self, args: list[str]) -> None:
        code = self.run(args)
        if code:
            raise IntegrationError(f"{self.phase}: {args[0]} exited {code}; see workflow log")

    def save(self, **values) -> None:
        if self.report_dir.resolve() != self.report_dir:
            raise IntegrationError("diagnostics path became a symlink")
        self.result.update(values, phase=self.phase, source_commit=self.base, run_url=self.run_url)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        (self.report_dir / "integration.json").write_text(json.dumps(self.result, indent=2) + "\n")

    def changed(self) -> list[str]:
        tracked = self.git("diff", "--name-only", "HEAD", "--").splitlines()
        untracked = self.git("ls-files", "--others", "--exclude-standard").splitlines()
        return sorted(
            set(tracked + untracked)
            - {path for path in untracked if (self.root / path).is_relative_to(self.report_dir)}
        )

    def allowed(self, path: str) -> bool:
        candidate = self.root / path
        if not candidate.resolve().is_relative_to(self.root):
            return False
        if path in self.locks:
            return candidate.is_file() and not candidate.is_symlink()
        parts = Path(path).parts
        if parts[:2] in ((".agents", "skills"), (".claude", "skills")) and len(parts) == 4:
            return (
                parts[2] in {"ship-adopt", "ship-release"}
                and parts[3] in {"SKILL.md", "README.md", "conventions.md"}
                and candidate.is_file()
                and not candidate.is_symlink()
            )
        if path == ".claude/skills":
            candidate = self.root / path
            return candidate.is_symlink() and os.readlink(candidate) == "../.agents/skills"
        return False

    def validate_diff(self) -> list[str]:
        paths = self.changed()
        bad = [path for path in paths if not self.allowed(path)]
        if bad:
            raise IntegrationError("unexpected changed files: " + ", ".join(bad))
        return paths

    def remote_head(self, branch: str) -> str | None:
        rows = self.git("ls-remote", "--heads", self.remote, f"refs/heads/{branch}").splitlines()
        return rows[0].split()[0] if rows else None

    def open_pr(self) -> dict | None:
        rows = json.loads(
            self.output(
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "open",
                    "--head",
                    self.branch_name,
                    "--base",
                    self.branch,
                    "--json",
                    "number,url,body",
                    "--limit",
                    "2",
                ]
            )
        )
        if len(rows) > 1:
            raise IntegrationError("multiple refresh PRs found; preserve them for review")
        return rows[0] if rows else None

    def stage_commit(self, paths: list[str], message: str) -> None:
        self.git("config", "user.name", "github-actions[bot]")
        self.git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        self.git("add", "--", *paths)
        staged = self.git("diff", "--cached", "--name-only").splitlines()
        if sorted(staged) != sorted(paths):
            raise IntegrationError("staged files differ from the validated generated changes")
        self.require(["git", "-c", "core.hooksPath=/dev/null", "commit", "-m", message])

    def candidate_pr(self) -> None:
        report = self.report_dir / "refresh" / "refresh.json"
        if not report.exists():
            return
        if report.resolve() != report:
            raise IntegrationError("candidate report is a symlink")
        data = json.loads(report.read_text())
        paths = data.get("lockfiles", [])
        if not data.get("candidate_valid") or not paths:
            return
        if data.get("source_commit") != self.base or any(path not in self.locks for path in paths):
            raise IntegrationError("candidate report does not match the tested base/lockfile scope")
        # Only restore locks from the report; a test's unexpected source edits
        # must never be smuggled into an agent's proposed update.
        unexpected = [path for path in self.changed() if not self.allowed(path)]
        if unexpected:
            raise IntegrationError("refusing candidate PR after unexpected changes: " + ", ".join(unexpected))
        for path in paths:
            source = self.report_dir / "refresh" / "candidate" / path
            if not source.is_file() or source.resolve() != source or not self.allowed(path):
                raise IntegrationError("candidate lockfile is missing or is a symlink")
            (self.root / path).write_bytes(source.read_bytes())
        # A concurrent run or human branch is never force-replaced.
        if self.open_pr() or self.remote_head(self.branch_name):
            raise IntegrationError("refresh branch or PR already exists; candidate retained as an artifact")
        self.git("checkout", "-b", self.branch_name)
        changed = self.validate_diff()
        self.stage_commit(changed, "deps: candidate update needs investigation")
        self.require(
            [
                "git",
                "push",
                f"--force-with-lease=refs/heads/{self.branch_name}:",
                self.remote,
                f"HEAD:refs/heads/{self.branch_name}",
            ]
        )
        self.save(candidate_branch=self.branch_name, candidate_commit=self.git("rev-parse", "HEAD"))
        moves = data.get("moves", [])
        rows = [f"| {m['ecosystem']} / {m['name']} | {m['before'] or '—'} | {m['after'] or '—'} |" for m in moves]
        failed_phase = data["phase"] if data.get("status") == "failed" else self.phase
        body = (
            "Ship's selected maintenance checks rejected this candidate. This draft is for investigation.\n\n"
            f"Base commit: `{self.base}`\n\nRun and logs: {self.run_url}\n\n"
            f"Failed phase: `{failed_phase}`. The run remains failed.\n\n"
            "Reproduce from this branch with locked installation, `uv run --locked ship skills sync`, "
            "`uv run --locked ship deps check`, and `uv run --locked ship deps verify`. "
            "The workflow artifact retains the candidate lockfiles and refresh report.\n\n"
            "| Package | Before | Candidate |\n| --- | --- | --- |\n" + "\n".join(rows)
        )
        body_file = self.report_dir / "pr-body.md"
        body_file.write_text(body)
        self.require(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                self.branch,
                "--head",
                self.branch_name,
                "--title",
                "deps: investigate first-party dependency update",
                "--body-file",
                str(body_file),
            ]
        )
        self.save(candidate_branch=self.branch_name)

    def validate_ci(self) -> None:
        name = Path(self.ci_workflow)
        if name.name != self.ci_workflow or name.suffix not in {".yml", ".yaml"}:
            raise IntegrationError("ci_workflow must name a workflow YAML file")
        path = self.root / ".github" / "workflows" / name
        if not path.is_file() or path.is_symlink():
            raise IntegrationError("selected CI workflow file is missing")
        code = (
            "import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); "
            "events=d.get('on',d.get(True,{})); "
            "print('yes' if 'workflow_dispatch' in events else 'no')"
        )
        if self.output(["uv", "run", "--locked", "python", "-c", code, str(path)]) != "yes":
            raise IntegrationError("selected CI workflow must support workflow_dispatch")

    def execute(self) -> None:
        if self.git("status", "--porcelain", "--untracked-files=all"):
            raise IntegrationError("weekly refresh requires a clean checkout")
        self.base = self.git("rev-parse", "HEAD")
        if self.git("branch", "--show-current") != self.branch:
            raise IntegrationError("weekly refresh must start on the configured default branch")
        if self.remote_head(self.branch) != self.base:
            raise IntegrationError("remote branch advanced; start a new run on its current head")
        self.save()
        existing = self.open_pr()
        if existing:
            self.save(status="awaiting_review", existing_pr=existing["url"])
            raise IntegrationError(
                f"existing refresh PR preserved: {existing['url']}; resolve it before another update"
            )
        if self.remote_head(self.branch_name):
            raise IntegrationError("refresh branch already exists without an open PR; inspect it before retrying")
        self.phase = "baseline"
        self.save()
        self.require(["uv", "sync", "--locked"])
        self.validate_ci()
        pnpm = self.root / self.config.get("pnpm-root", ".")
        if (pnpm / "pnpm-lock.yaml").exists():
            self.require(["pnpm", "-C", str(pnpm), "install", "--frozen-lockfile"])
        self.require(["uv", "run", "--locked", "ship", "deps", "check"])
        self.require(["uv", "run", "--locked", "ship", "deps", "verify"])
        if self.changed():
            raise IntegrationError("baseline checks changed repository files")
        original_locks = {
            path: (self.root / path).read_bytes() if (self.root / path).exists() else None for path in self.locks
        }
        self.phase = "refresh"
        self.save()
        code = self.run(
            [
                "uv",
                "run",
                "--locked",
                "ship",
                "deps",
                "refresh",
                "--no-test",
                "--report-dir",
                str(self.report_dir / "refresh"),
            ]
        )
        if code:
            self.candidate_pr()
            raise IntegrationError("dependency refresh failed; candidate evidence retained")
        if all(
            ((self.root / path).read_bytes() if (self.root / path).exists() else None) == content
            for path, content in original_locks.items()
        ):
            if self.changed():
                raise IntegrationError("refresh changed files without changing dependency locks")
            self.phase = "complete"
            self.save(status="unchanged")
            return
        self.phase = "candidate_policy"
        self.save()
        try:
            self.require(["uv", "run", "--locked", "ship", "skills", "sync"])
            self.require(["uv", "run", "--locked", "ship", "deps", "check"])
            self.validate_diff()
            candidate_locks = {
                path: (self.root / path).read_bytes() if (self.root / path).exists() else None for path in self.locks
            }
            self.phase = "candidate_tests"
            self.require(["uv", "run", "--locked", "ship", "deps", "verify"])
            if any(
                ((self.root / path).read_bytes() if (self.root / path).exists() else None) != content
                for path, content in candidate_locks.items()
            ):
                raise IntegrationError("maintenance checks changed the resolved candidate lockfiles")
            self.phase = "candidate_policy"
            self.require(["uv", "run", "--locked", "ship", "deps", "check"])
            paths = self.validate_diff()
        except IntegrationError:
            self.candidate_pr()
            raise
        if not paths:
            self.phase = "complete"
            self.save(status="unchanged")
            return
        if self.remote_head(self.branch) != self.base:
            raise IntegrationError(
                "remote branch advanced after verification; rerun instead of rebasing untested changes"
            )
        self.phase = "commit"
        self.stage_commit(paths, "deps: refresh first-party locks and generated Ship skills")
        # Normal push is the second race check. Never force a main update.
        self.require(["git", "push", self.remote, f"HEAD:refs/heads/{self.branch}"])
        self.save(status="committed", commit=self.git("rev-parse", "HEAD"))
        self.phase = "dispatch_ci"
        self.require(["gh", "workflow", "run", self.ci_workflow, "--ref", self.branch])
        self.phase = "complete"
        self.save(status="committed")


def main() -> int:
    runner = None
    code = 0
    try:
        runner = Runner(
            Path.cwd(),
            os.environ.get("SHIP_REPORT_DIR", ".ship-refresh"),
            os.environ.get("SHIP_CI_WORKFLOW", "ci.yml"),
        )
        runner.execute()
    except (IntegrationError, OSError, ValueError) as exc:
        if runner is not None:
            try:
                runner.save(status="failed", error=str(exc))
            except (IntegrationError, OSError):
                print("::error::could not write integration diagnostics", file=sys.stderr)
        print(f"::error::{exc}", file=sys.stderr)
        code = 1
    finally:
        if runner is not None:
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with Path(summary).open("a") as stream:
                    stream.write(
                        "## Ship dependency refresh\n\n```json\n" + json.dumps(runner.result, indent=2) + "\n```\n"
                    )
            try:
                path = runner.artifact_path()
                if path and os.environ.get("GITHUB_OUTPUT"):
                    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                        stream.write(f"report_dir={path}\n")
            except (IntegrationError, OSError) as exc:
                print(f"::error::{exc}", file=sys.stderr)
                code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
