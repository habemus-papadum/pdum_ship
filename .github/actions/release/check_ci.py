"""Reuse CI only across version-only changes; shared by every release caller."""

import json
import os
import subprocess
import time
import tomllib
from pathlib import PurePosixPath


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def normalized(path, text):
    # Release commits may change only the package's own version, never requirements.
    if PurePosixPath(path).name == "package.json":
        data = json.loads(text)
        del data["version"]
    elif PurePosixPath(path).name == "pyproject.toml":
        data = tomllib.loads(text)
        del data["project"]["version"]
    elif path == "uv.lock":
        data = tomllib.loads(text)
        for package in data.get("package", []):
            source = package.get("source", {})
            if "editable" in source or "virtual" in source:
                package.pop("version", None)
    else:
        raise ValueError("not a release version file")
    return data


def only_versions(parent, commit):
    entries = git("diff", "--raw", "--no-renames", "-z", parent, commit).split("\0")
    for header, path in zip(entries[::2], entries[1::2]):
        old_mode, new_mode, _, _, status = header.split()
        if old_mode[1:] != new_mode or status != "M":
            return False
        try:
            before = normalized(path, git("show", f"{parent}:{path}"))
            after = normalized(path, git("show", f"{commit}:{path}"))
        except (ValueError, KeyError, TypeError):
            return False
        if before != after:
            return False
    return True


def candidates(head):
    result = [head]
    while True:
        parents = git("rev-list", "--parents", "-n", "1", result[-1]).split()[1:]
        if len(parents) != 1 or not only_versions(parents[0], result[-1]):
            return result
        result.append(parents[0])


def choose_run(commits, runs):
    # Prefer the nearest commit's latest run, including pending and failed runs.
    # Never hide a failed current run behind an older successful one.
    for commit in commits:
        for run in runs:
            if run["headSha"] == commit and run["event"] in ("push", "workflow_dispatch"):
                return run
    return None


def main():
    head = os.environ["SHA"]
    workflow = os.environ["WORKFLOW"]
    commits = candidates(head)
    for attempt in range(60):
        runs = json.loads(
            subprocess.check_output(
                [
                    "gh",
                    "run",
                    "list",
                    "--workflow",
                    workflow,
                    "--branch",
                    os.environ["BRANCH"],
                    "--limit",
                    "100",
                    "--json",
                    "headSha,status,conclusion,url,event",
                ],
                text=True,
            )
        )
        run = choose_run(commits, runs)
        if run and run["status"] == "completed":
            if run["conclusion"] != "success":
                raise SystemExit(f"::error::{workflow} concluded {run['conclusion']!r}: {run['url']}")
            print(f"CI passed for {run['headSha']}: {run['url']}")
            if run["headSha"] != head:
                print(f"Reusing CI for {head}: only package version metadata changed.")
            return
        print(f"Waiting for {workflow} ({attempt + 1}/60).", flush=True)
        time.sleep(30)
    raise SystemExit(f"::error::Timed out waiting for {workflow} on {head} or a version-only ancestor.")


if __name__ == "__main__":
    main()
