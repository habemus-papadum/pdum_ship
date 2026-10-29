# ship

`ship` releases packages and refreshes dependencies in repositories that use
[uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), or both. It is
built for a small group of related packages maintained by a few people, where
every consumer of a package is also its author.

```sh
uv add --dev habemus-papadum-ship      # in each repository
uv run ship release my-package --bump minor
uv run ship deps refresh
uv run ship deps check
```

## The model

- **One version per package.** A repository may publish several packages;
  each has its own version in its own manifest.
- **The tag is the truth.** A package's releases are its tags, named
  `<package>-v<version>` (`my-package-v0.4.0`). The next version is the last
  tag plus the bump you ask for. `ship` never trusts the manifest for this.
- **A development marker between releases.** After releasing `0.4.0`, the
  manifest says `0.4.0+dev` until the next release. The suffix is valid in both
  ecosystems (a PEP 440 local version; semver build metadata). PyPI refuses to
  accept it, and `ship` refuses to publish it anywhere, so a development build
  cannot be published by accident.
- **Floors on our own packages.** A consumer declares one of our packages
  with a lower bound (`my-lib>=0.4`, `"@scope/lib": ">=0.4.0"`), never an
  exact version. The lockfile holds the exact version; `ship deps refresh`
  moves it.
- **Lockfiles change only on command.** `uv run`, `uv sync` and `pnpm install`
  keep locked versions. `ship deps refresh` is the one command that upgrades.
- **An exact pin needs a reason.** A pin on one of our packages is allowed
  only with a comment on the same line (Python) or an entry in
  `[tool.ship.pins]` (either ecosystem). `ship deps check` reports the rest.

## Configuration

Every repository has a root `pyproject.toml`, even a pnpm-only one; it installs
`ship` and holds the configuration:

```toml
[tool.ship]
# Patterns for the packages that count as ours. Required.
ours = ["@scope/*", "my-org-*"]

# Paths outside a package directory whose changes count as a change to every
# package (for `ship changed`). Lockfiles are not listed: every refresh and
# every release touches them.
shared-paths = [".github/workflows/**", "pnpm-workspace.yaml"]

# Paths whose changes never count. Optional.
ignore-paths = ["docs/**"]

# Commit subjects (prefixes) that never count as a change, in addition to
# ship's own "back to development:" commits. Useful after adopting ship in a
# repository whose old pipeline left a post-release commit. Optional.
ignore-subjects = ["back to dev"]

# Release from this branch to this remote. Defaults shown.
branch = "main"
remote = "origin"

# Create a GitHub release for each tag when `gh` is installed. Default true.
github-release = true

# Test commands run before publishing and after a refresh. Defaults: the
# package's `uv run --locked pytest` or `pnpm --filter <name> test`.
test = ["uv run --locked pytest", "pnpm -r test"]

# Release every package at one version instead of independently. Give the
# reason; the default is independent releases.
# coupled = "the plugins share a generated protocol file"

[tool.ship.pins]
"@scope/old-lib" = "needs the v1 schema until the importer is rewritten"

[tool.ship.packages."my-package"]
tag = "my-package"          # tag component; default: the unscoped name
test = ["uv run --locked pytest packages/my-package"]
publish = true               # false: version and tag, never upload
```

`ship` discovers packages itself: the root `pyproject.toml` and every
`[tool.uv.workspace]` member that has a `[project]` table and is not
`package = false`; the root `package.json` and every `pnpm-workspace.yaml`
member that is not `"private": true`. For a scoped npm package the tag
component is the unscoped name (`@scope/core` tags as `core-v1.2.0`).

## Commands

### `ship release <package> --bump patch|minor|major`

In order:

1. Preflight: clean working tree, on the release branch, `HEAD` equal to the
   remote branch, no development links, lockfiles current.
2. Read the package's last tag; require the manifest to say
   `<last>+dev`; compute the next version; ask the registry that it does not
   exist yet (`--no-registry-check` skips the question).
3. Write the version. Python goes through `uv version --package`, so
   `uv.lock` follows. npm edits `package.json` in place.
4. Build. Python: `uv build --package`, refusing any built file that still
   carries a development version. npm: the package's `build` script and those
   of its workspace dependencies (`pnpm --filter "<name>..." run build`), so
   `pnpm publish` packs fresh output.
5. Run the test commands.
6. Publish: `uv publish` with the credentials in the environment
   (`UV_PUBLISH_TOKEN`, or `UV_PUBLISH_USERNAME`/`UV_PUBLISH_PASSWORD`), or
   `pnpm -r publish --filter <name> --no-git-checks --access public` with the
   token your `.npmrc` reads from the environment (the reusable workflow lets
   `actions/setup-node` write that `.npmrc` from `NODE_AUTH_TOKEN`).
7. Commit the version and lockfile, tag `<package>-v<version>`, push the
   commit and the tag atomically.
8. Create a GitHub release for the tag when `gh` is available.
9. Write `<version>+dev` back, re-lock, commit, push.
10. Print the commits since the previous tag that touched the package.

Any failure before publishing is reverted. A failure after publishing is not:
the registry already has the version, so `ship` prints the remaining commands
instead. `--dry-run` plans and prints, writing nothing. `--no-publish` and
`--no-push` do everything except those steps; `--skip-tests` skips step 5.
`--version X.Y.Z` sets the exact version instead of a bump.

In a coupled repository, omit the package: every publishable package moves to
one version, computed from the newest last tag, with one tag per package.

### `ship changed`

Lists the packages with commits since their last tag. A commit counts when it
touches the package's directory (minus `ignore-paths`) or one of the
`shared-paths`. A package with no tag counts as changed. A package is not
marked changed because a workspace dependency of it changed: consumers hold
floors, so republishing a dependent that did not change adds nothing.
ship's own `back to development:` commits never count, nor do commits whose
subject starts with an entry of `ignore-subjects`.

### `ship packages`

Lists the discovered packages with their manifest versions and last tags.

### `ship deps refresh [--all]`

Upgrades our packages in the lockfiles to their newest releases:
`uv lock --upgrade-package <name>` for each of our packages found in
`uv.lock`, and `pnpm update -r <patterns>` for the scoped patterns in `ours`.
Then `uv sync --locked`, `pnpm install --frozen-lockfile`, the test commands,
and a table of what moved. With `--all`, third-party packages move too, within
their declared ranges and behind the release-age gates. The lockfile is left
uncommitted. `--dry-run` prints the commands; `--no-test` skips the tests.

### `ship deps check [--hook]`

Reports, and exits 1 on any of:

- a `path` or `editable` entry in `[tool.uv.sources]`, or a `link:`/`file:`
  dependency in a `package.json` (a development link);
- a stale lockfile (`uv lock --check`; `pnpm install --frozen-lockfile`);
- an exact pin on one of our packages without a reason;
- a `peerDependencies` range on one of our packages written as `^`, `~`,
  `*`, `workspace:^`, `workspace:~` or `workspace:*` (write `>=X.Y.0` or
  `workspace:>=X.Y.0`);
- a manifest version that is not `<last tag>+dev`.

`--hook` runs only the first two, fast enough for a pre-commit hook:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/habemus-papadum/pdum_ship
    rev: habemus-papadum-ship-v0.1.0
    hooks:
      - id: ship-check
```

## Releasing from GitHub Actions

The reusable workflow runs `ship release` as the repository's CI identity,
after the commit's CI has passed. A repository calls it from its own
`release.yml`:

```yaml
name: release
on:
  workflow_dispatch:
    inputs:
      package: { description: Package to release, required: true, type: string }
      bump: { description: patch, minor or major, type: choice, options: [patch, minor, major], default: minor }
      dry_run: { description: Plan only, type: boolean, default: true }
jobs:
  release:
    uses: habemus-papadum/pdum_ship/.github/workflows/release.yml@main
    with:
      package: ${{ inputs.package }}
      bump: ${{ inputs.bump }}
      dry_run: ${{ inputs.dry_run }}
    secrets:
      PYPI_API_TOKEN: ${{ secrets.PYPI_API_TOKEN }}
      NPM_TOKEN: ${{ secrets.NPM_TOKEN }}
    permissions:
      contents: write
      actions: read
```

The workflow waits for the `ci.yml` run on the same commit to succeed (input
`ci_workflow` renames it; `skip_ci_check` overrides it), sets the git identity,
installs the repository with `uv sync --locked`, exports the tokens, and runs
`uv run ship release`. For a pnpm workspace it installs pnpm and Node — the
version in `.nvmrc` when the repository has one, otherwise the
`node_version` input — and lets `actions/setup-node` write the `.npmrc` that
reads `NODE_AUTH_TOKEN`. The full caller, with the `skip_ci_check` and
`skip_tests` inputs, is in [`docs/conventions.md`](conventions.md#the-release-workflow-caller). Publishing authenticates with the tokens; switching a
repository to trusted publishing changes only that step.

## Why peer dependencies matter for npm libraries

In `node_modules`, two versions of a package are two separate modules. Two
copies of a reactive runtime are two runtimes; two copies of a class are two
classes that fail `instanceof` against each other. A library declares a
dependency as a `peerDependency` when objects of that dependency's types cross
the library's public API or the dependency holds process-wide state; the
application then provides the single copy. A peer range that excludes the
application's copy makes pnpm install a second one, which is why `ship deps
check` rejects `^0.x` and `workspace:^` on our 0.x packages. `workspace:>=0.4.0`
resolves to the sibling source during development and is published as
`>=0.4.0`.

## Version marker in code

Do not mirror the version into a Python module by hand. Read it from the
installed metadata:

```python
from importlib.metadata import version

__version__ = version("my-package")
```

## Skills for agents

This repository is also a plugin for Claude Code and Codex: the manifest is
`.claude-plugin/plugin.json`, the skills are under `skills/`.

- `ship-release` — releasing, refreshing, checking; what an agent does and
  does not do in a repository that uses ship. Fronts this README.
- `ship-adopt` — bringing a repository or a new package into the model.
  Fronts [`docs/conventions.md`](conventions.md).

The same skills and documents travel inside the wheel. In any repository
that installs ship:

```sh
uv run ship skills sync    # writes .agents/skills/ship-*; .claude/skills -> ../.agents/skills
uv run ship skills check   # reports copies missing or stale for the installed ship
```

`ship deps check` includes that check, so a repository's copies move with
its pinned ship version. The copies are generated; edit the source here.

## Status

0.x. The interface may change between releases.
