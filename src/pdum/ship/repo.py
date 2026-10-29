"""Git operations on the repository being released. Every call shells out to ``git``."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """A git command failed."""


@dataclass
class Repo:
    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> Repo:
        start = (start or Path.cwd()).resolve()
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=start,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError as exc:
            raise GitError(f"{start} is not inside a git repository") from exc
        return cls(Path(out))

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=False)
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
        return proc.stdout

    # -- state -----------------------------------------------------------------

    def status_porcelain(self) -> str:
        """Tracked changes only. Untracked files (a stray .DS_Store, a scratch notebook)
        cannot reach a release commit, which stages explicit paths, so they do not block one."""
        return self.git("status", "--porcelain", "--untracked-files=no").strip()

    def is_clean(self) -> bool:
        return self.status_porcelain() == ""

    def current_branch(self) -> str:
        return self.git("branch", "--show-current").strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def rev_parse(self, ref: str) -> str | None:
        out = self.git("rev-parse", "--verify", "--quiet", ref, check=False).strip()
        return out or None

    def has_remote(self, remote: str) -> bool:
        return remote in self.git("remote").split()

    # -- tags ------------------------------------------------------------------

    def tags(self, pattern: str) -> list[str]:
        out = self.git("tag", "--list", pattern)
        return [line for line in out.splitlines() if line.strip()]

    def tag_commit(self, tag: str) -> str:
        return self.git("rev-list", "-n", "1", tag).strip()

    # -- history ---------------------------------------------------------------

    def commits_since(self, ref: str | None, pathspecs: list[str]) -> list[str]:
        """Commits after ``ref`` (all history when None) that touch any pathspec.

        Each entry is ``<short sha> <subject>``.
        """
        rev_range = f"{ref}..HEAD" if ref else "HEAD"
        args = ["log", "--format=%h %s", rev_range]
        if pathspecs:
            args += ["--", *pathspecs]
        return [line for line in self.git(*args).splitlines() if line.strip()]

    # -- writes ----------------------------------------------------------------

    def add(self, paths: list[Path | str]) -> None:
        self.git("add", "--", *(str(p) for p in paths))

    def commit(self, message: str) -> str:
        self.git("commit", "--quiet", "-m", message)
        return self.head()

    def tag(self, name: str, message: str) -> None:
        self.git("tag", "-a", name, "-m", message)

    def fetch(self, remote: str, branch: str) -> None:
        self.git("fetch", "--quiet", remote, branch)

    def fetch_tags(self, remote: str) -> bool:
        """Fetch the remote's tags; False when the remote is missing or unreachable.

        A plain ``git pull`` does not bring in a tag made by a release elsewhere, and the
        version-marker check reads tags, so the commands that need them fetch first.
        """
        if not self.has_remote(remote):
            return False
        proc = subprocess.run(
            ["git", "fetch", "--quiet", "--tags", remote], cwd=self.root, capture_output=True, text=True
        )
        return proc.returncode == 0

    def push_atomic(self, remote: str, branch: str, tag: str | None) -> None:
        refs = [f"HEAD:refs/heads/{branch}"]
        if tag:
            refs.append(f"refs/tags/{tag}")
        self.git("push", "--quiet", "--atomic", remote, *refs)

    def push(self, remote: str, branch: str) -> None:
        self.git("push", "--quiet", remote, f"HEAD:refs/heads/{branch}")

    def checkout_paths(self, paths: list[Path | str]) -> None:
        self.git("checkout", "--", *(str(p) for p in paths))
