"""Exercise the real draft-PR path on disposable refs, never the default branch.

This is a controlled transport test: a deliberately failed check and a harmless
lockfile comment stand in for a rejected dependency candidate. All PR/branch
operations below use the same Runner path as the weekly updater.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from refresh import Runner


class SmokeRunner(Runner):
    def capture(self, args: list[str]) -> str:
        if args[:3] == ["gh", "pr", "create"]:
            args = list(args)
            args[args.index("--title") + 1] = "[Ship smoke test] controlled failed-candidate PR"
            body = Path(args[args.index("--body-file") + 1])
            body.write_text(
                "Controlled verification of Ship automation. No real dependency regression is claimed. "
                "This draft and its disposable refs will be cleaned up by the test.\n\n" + body.read_text()
            )
        return super().capture(args)


def main() -> None:
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    if not run_id.isdigit() or not attempt.isdigit():
        raise RuntimeError("GitHub run identity must be numeric")
    runner = SmokeRunner(Path.cwd(), ".ship-refresh-smoke", "ci.yml")
    runner.base = runner.git("rev-parse", "HEAD")
    runner.branch = f"ship/refresh-smoke-base-{run_id}-{attempt}"
    runner.branch_name = f"ship/refresh-smoke-candidate-{run_id}-{attempt}"
    runner.phase = "controlled_failed_check"
    created_base = False
    pr = None
    try:
        if runner.remote_head(runner.branch) or runner.remote_head(runner.branch_name):
            raise RuntimeError("smoke refs already exist; refusing to replace them")
        runner.require(
            [
                "git",
                "push",
                f"--force-with-lease=refs/heads/{runner.branch}:",
                runner.remote,
                f"HEAD:refs/heads/{runner.branch}",
            ]
        )
        created_base = True
        runner.save(status="controlled_failure")
        directory = runner.report_dir / "refresh"
        candidate = directory / "candidate" / "uv.lock"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes((runner.root / "uv.lock").read_bytes() + b"\n# Controlled Ship PR transport test.\n")
        (directory / "refresh.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "source_commit": runner.base,
                    "status": "failed",
                    "phase": "tests",
                    "candidate_valid": True,
                    "lockfiles": ["uv.lock"],
                    "moves": [],
                }
            )
            + "\n"
        )
        if runner.run([sys.executable, "-c", "raise SystemExit(17)"]) != 17:
            raise RuntimeError("controlled check did not fail as expected")
        runner.candidate_pr()
        pr = runner.open_pr()
        if not pr:
            raise RuntimeError("candidate PR was not found")
        details = json.loads(
            runner.output(
                [
                    "gh",
                    "pr",
                    "view",
                    str(pr["number"]),
                    "--json",
                    "isDraft,baseRefName,headRefName,url",
                ]
            )
        )
        if not details["isDraft"] or details["baseRefName"] != runner.branch:
            raise RuntimeError("PR is not a draft against the disposable base")
        if details["headRefName"] != runner.branch_name:
            raise RuntimeError("PR candidate ref differs")
        runner.save(status="verified", smoke_pr=details["url"])
        print(f"Verified real draft PR: {details['url']}", flush=True)
    finally:
        cleanup_errors = []
        try:
            pr = pr or runner.open_pr()
            if pr:
                runner.require(["gh", "pr", "close", str(pr["number"])])
        except Exception as exc:
            cleanup_errors.append(str(exc))
        for branch in [runner.branch_name, runner.branch] if created_base else []:
            try:
                head = runner.remote_head(branch)
                if head:
                    expected = runner.base if branch == runner.branch else runner.result.get("candidate_commit")
                    if not expected or head != expected:
                        raise RuntimeError(f"smoke ref changed or ownership is uncertain; preserving {branch}")
                    runner.require(
                        [
                            "git",
                            "push",
                            f"--force-with-lease=refs/heads/{branch}:{expected}",
                            runner.remote,
                            f":refs/heads/{branch}",
                        ]
                    )
            except Exception as exc:
                cleanup_errors.append(str(exc))
        runner.save(cleanup_errors=cleanup_errors)
        if cleanup_errors:
            raise RuntimeError("smoke cleanup needs attention: " + "; ".join(cleanup_errors))


if __name__ == "__main__":
    main()
