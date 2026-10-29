"""ship: release packages and refresh dependencies in uv and pnpm workspaces.

The model: each package has its own version and its own tag ``<name>-v<version>``.
The tag is the truth; the manifest carries ``<last release>+dev`` between releases.
See README.md for the policy and the commands.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("habemus-papadum-ship")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
