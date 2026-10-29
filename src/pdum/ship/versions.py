"""Release versions: ``X.Y.Z`` plus the ``+dev`` development marker."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

DEV_SUFFIX = "+dev"
Bump = Literal["patch", "minor", "major"]
BUMPS: tuple[Bump, ...] = ("patch", "minor", "major")

_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionError(ValueError):
    """A version string that the release model does not accept."""


@dataclass(frozen=True, order=True)
class Version:
    """A release version. Only ``X.Y.Z`` is a release; everything else is rejected."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Version:
        match = _RELEASE_RE.match(text.strip())
        if not match:
            raise VersionError(f"{text!r} is not a release version of the form X.Y.Z")
        return cls(*(int(part) for part in match.groups()))

    def bump(self, level: Bump) -> Version:
        if level == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        if level == "minor":
            return Version(self.major, self.minor + 1, 0)
        if level == "major":
            return Version(self.major + 1, 0, 0)
        raise VersionError(f"unknown bump level {level!r}; expected one of {', '.join(BUMPS)}")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def dev(self) -> str:
        """The development marker that follows this release: ``X.Y.Z+dev``."""
        return f"{self}{DEV_SUFFIX}"


ZERO = Version(0, 0, 0)


def split_dev(text: str) -> tuple[str, bool]:
    """Split ``X.Y.Z+dev`` into (``X.Y.Z``, True); anything else into (text, False)."""
    text = text.strip()
    if text.endswith(DEV_SUFFIX):
        return text[: -len(DEV_SUFFIX)], True
    return text, False


def expected_dev_marker(last_release: Version | None) -> str:
    """What a package's manifest should say between releases."""
    return (last_release or ZERO).dev
