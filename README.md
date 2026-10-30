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
  cannot be published by accident. npm would accept it: `pnpm pack` and
  `pnpm publish` strip `+dev` and produce a plain `0.4.0`, so the guard on
  the npm side is ship's alone.
- **Floors on our own packages.** A consumer declares one of our packages
  with a lower bound (`my-lib>=0.4`, `"@scope/lib": ">=0.4.0"`), never an
  exact version and never a cap (`~=0.4.0`, `^0.4.0`, `~0.4.0`), which
  blocks the next minor of a 0.x package for no reason the consumer knows.
  The lockfile holds the exact version; `ship deps refresh` moves it.
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
# every release touches them. For a package at the repository root the
# whole tree is already its directory, so this setting adds nothing there.
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

# Directory of the pnpm workspace when it is not the repository root. ship
# reads its pnpm-workspace.yaml and package.json, runs pnpm there and commits
# its pnpm-lock.yaml; the reusable workflow reads the same setting. Default ".".
pnpm-root = "widgets"

# Release every package at one version instead of independently. Give the
# reason; the default is independent releases.
# coupled = "the plugins share a generated protocol file"

[tool.ship.pins]
"@scope/old-lib" = "needs the v1 schema until the importer is rewritten"

[tool.ship.packages."my-package"]
tag = "my-package"          # tag component; default: the unscoped name
test = ["uv run --locked pytest packages/my-package"]
publish = true               # false: version and tag, never upload
# Shell commands run from the repository root before ship's own build step,
# for a distribution whose build needs generated inputs (a widget bundle).
before-build = ["pnpm -C widgets build:anywidget"]
# false: ship writes the version, tests, commits, tags and pushes, and another
# workflow builds and publishes from the tag (platform wheels the release
# runner cannot build). Implies publish = false.
build = true
```

`ship` inventories every workspace project, including private applications, for dependency checks and refresh tests. Its release inventory selects only publishable packages: the root `pyproject.toml` and every
`[tool.uv.workspace]` member that has a `[project]` table and is not
`package = false`; the `package.json` of the pnpm workspace root (the
repository root, or `pnpm-root`) and every `pnpm-workspace.yaml` member that
is not `"private": true`. For a scoped npm package the tag component is the
unscoped name (`@scope/core` tags as `core-v1.2.0`).

## Commands

### `ship release <package> --bump patch|minor|major`

In order:

1. Preflight: clean working tree, on the release branch, `HEAD` equal to the
   remote branch, no development links, lockfiles current. Tags are fetched
   from the remote first (as `ship deps check` also does): a release made
   elsewhere leaves a tag that `git pull` does not bring in.
2. Read the package's last tag; require the manifest to say
   `<last>+dev`; compute the next version; ask the registry that it does not
   exist yet (`--no-registry-check` skips the question).
3. Write the version. Python goes through `uv version --package`, so
   `uv.lock` follows. npm edits `package.json` in place.
4. Build. First the package's `before-build` commands, if any. Python:
   `uv build --package`, refusing any built file that still carries a
   development version. npm: the package's `build` script and those of its
   workspace dependencies (`pnpm --filter "<name>..." run build`), then packs the selected package into `dist/<package>/`. Its packed name and
   version are checked, and publication uses that exact tarball. A package with `build = false` is
   neither built nor published here; its own workflow does both from the tag.
5. Run the test commands.
6. Publish: `uv publish` with the credentials in the environment
   (`UV_PUBLISH_TOKEN`, or `UV_PUBLISH_USERNAME`/`UV_PUBLISH_PASSWORD`), or
   `pnpm publish <prepared-tarball> --no-git-checks` with the
   token your `.npmrc` reads from the environment (the reusable workflow lets
   `actions/setup-node` write that `.npmrc` from `NODE_AUTH_TOKEN`).
7. Commit the version and lockfile, tag `<package>-v<version>`, push the
   commit and the tag atomically.
8. Create a GitHub release for the tag when `gh` is available.
9. Write `<version>+dev` back, re-lock, commit, push.
10. Print the commits since the previous tag that touched the package.

Build or test failures restore the version files. Once an upload has been
attempted, a failure preserves the release versions and artifacts: the registry
may already contain some files. The error names the phase, confirmed uploads
and uncertain package; see [Interrupted releases](#interrupted-releases). `--dry-run` plans and prints, writing nothing. `--no-publish` and
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
`uv.lock`, and `pnpm update -r --no-save --lockfile-only <names>` for our
packages found in the pnpm lockfile. Declared floors and catalogs stay unchanged.
Then `uv sync --locked`, `pnpm install --frozen-lockfile`, the test commands,
and a table of what moved. With `--all`, third-party packages move too, within
their declared ranges and behind the release-age gates. The lockfile is left
uncommitted. `--dry-run` prints the commands; `--no-test` skips the tests.

Without `--all`, a third-party package that is **downgraded or removed** is an
error: upgrading one of our packages can add or raise a third-party
requirement, but cannot lower one, so such a move means the resolver did
something to look at. ship restores the lockfiles and prints the moves;
`--allow-downgrades` accepts them (for example when one of our packages added
an upper bound). Any failure — the lock command, the install, the tests —
restores the lockfiles and dependency declarations and reports the moves that
were attempted. Test selection includes private projects, and an explicit
repository `test` command runs even when nothing is publishable. A changed
lockfile always triggers installation and tests, including metadata-only changes;
the version table retains every version of a dependency and distinguishes the
two ecosystems.

### `ship deps check [--hook]`

Reports, and exits 1 on any of:

- a `path` or `editable` entry in `[tool.uv.sources]`, or a `link:`/`file:`
  dependency in a `package.json` (a development link);
- a stale lockfile (`uv lock --check`; `pnpm install --frozen-lockfile`);
- an exact pin on one of our packages without a reason;
- a `peerDependencies` range on one of our packages written as `^`, `~`,
  `*`, `workspace:^`, `workspace:~` or `workspace:*` (write `>=X.Y.0` or
  `workspace:>=X.Y.0`);
- a manifest version that is not `<last tag>+dev`;
- a locked first-party dependency on a private registry whose metadata cannot
  be read or lacks valid publication times (Python file `upload-time`; npm
  version `time`). These network checks stay outside `--hook`.

`--hook` runs the first two plus the skill-copy check, fast enough for a
pre-commit hook. The repository already installs ship through its lockfile,
so the hook runs that copy and nothing needs bumping when ship releases:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: ship-check
        name: ship deps check (development links, lockfiles, skill copies)
        entry: uv run --locked ship deps check --hook
        language: system
        pass_filenames: false
        always_run: true
```

The remote form (`repo: https://github.com/habemus-papadum/pdum_ship` with a
`rev:` of a release tag and hook id `ship-check`) also works; pre-commit then
installs ship into its own environment at that pinned release.

## Releasing from GitHub Actions

The reusable workflow `.github/workflows/release.yml` runs `ship release` as
the repository's CI identity, after the commit's CI has passed. A repository
calls it from its own `release.yml`; the one canonical caller, with every
input, is in
[`docs/conventions.md`](docs/conventions.md#the-release-workflow-caller).
Copy it from there rather than from another repository.

Repositories with their own native authentication setup can use the composite
`.github/actions/release` action inside their job instead. Provision uv and any
Node/pnpm tools, check out full history with push credentials, run the existing
authentication action, then call ship's action. It reuses the same CI gate,
locked installation, release command and interrupted-artifact handling as the
reusable workflow. It neither provisions registry credentials nor replaces the
caller's native configuration. The caller owns job permissions and release
concurrency. See the conventions for the caller shape.

The caller must grant `contents: write`, `actions: read` and
`id-token: write`: the reusable job requests all three, and a caller that
grants less fails as `startup_failure` with no log. The workflow waits for
`ci.yml` to succeed (input `ci_workflow` renames it; `skip_ci_check` overrides
it). A green run on an ancestor also counts when every intervening commit
changes only package version metadata: a manifest's own version and local
workspace package versions in `uv.lock`. Source, dependency requirements,
external locked versions, configuration, file additions/deletions and mode
changes require fresh CI. The nearest commit with a CI run takes precedence;
a pending or failed run cannot be hidden by an older green run. Both push and
manually dispatched CI runs count.

This lets consecutive per-package releases reuse the CI that tested their
source. GitHub does not trigger push CI for ship's own `GITHUB_TOKEN` commits.
Release build and test commands still run for every package unless explicitly
skipped. Run packages sequentially, dispatching each after the previous run
finishes so it starts from the updated branch. The `package` input accepts one
name, not a comma-separated list.

The workflow uses Bash with `pipefail`, so a failed build, test or publish
fails the job even though its output is copied into the step summary. These
workflow fixes apply to callers using `release.yml@main` without upgrading
their locked CLI or changing their permissions.

A Python-only repository whose tests need Node/pnpm can pass `setup_pnpm: true`;
ship’s own dispatch enables this and uses pnpm 11, matching its CI. Tests run
with isolated native credential stores and no inherited publish credentials.

The workflow sets the git identity, installs the
repository with `uv sync --locked`, exports the tokens, and runs
`uv run ship release`. For a pnpm workspace it installs pnpm and Node — the
version in `.nvmrc` when the repository has one, otherwise the
`node_version` input — and lets `actions/setup-node` write the `.npmrc` that
reads `NODE_AUTH_TOKEN`; `pnpm publish` runs with `--provenance`. Publishing
authenticates with the tokens; switching a repository to trusted publishing
changes only that step.

## Private registries

Registry selection and credentials follow the native package managers. A release
checks the same registry it will publish to. An authentication failure or unreadable
response is an unknown result, never proof that a version is absent.

Python defaults to public PyPI. To publish privately, reference a uv index:

```toml
[tool.ship]
ours = ["example-*", "@example/*"]
python-index = "internal"

[[tool.uv.index]]
name = "internal"
url = "https://packages.example/simple/"
publish-url = "https://packages.example/legacy/"
```

An existing `[tool.uv] publish-url` also works when it identifies exactly one
index by its `publish-url` or origin. `uv.toml` takes precedence over `[tool.uv]`.
`UV_PUBLISH_INDEX` and `UV_PUBLISH_URL` supply native environment overrides.
`[tool.ship] index-url` is an alternative when the read endpoint cannot be
inferred; the upload endpoint must still be configured. Ambiguous private
configuration fails rather than checking or uploading to public PyPI.

Metadata reads use the JSON Simple API. Credentials come from
`UV_INDEX_<NAME>_USERNAME` / `UV_INDEX_<NAME>_PASSWORD`, the matching publication
environment, `.netrc`, or uv's saved credentials. A username supplied through the
named-index environment also supports uv's native keyring lookup. Credentials
stay in memory or native stores, are never added to registry URLs or printed,
and are not forwarded through redirects. No login or token refresh is performed.

npm uses the package's `publishConfig.registry`, then the scope/default registry
from pnpm's native configuration. Metadata queries use `pnpm view`; publication
uses `pnpm publish` on the checked tarball with the same explicit registry.
Basic and Bearer credentials use the native user `.npmrc`, including environment
substitution through `NPM_CONFIG_USERCONFIG`. pnpm 11 intentionally refuses
secret environment expansion in a project `.npmrc`; keep only registry mappings
there. Provenance and public access are requested for the public npm registry;
private registries receive neither a forced access setting nor an unsupported
provenance request.

The reusable workflow accepts `npm_registry` for setup-node's credential target,
`NPM_TOKEN` or `NPM_BASIC_AUTH` (base64 `username:password`, mutually exclusive),
and `python_registry` with `PYTHON_USERNAME` / `PYTHON_PASSWORD`. The latter
installs native uv credentials before the repository is synced. Native project
configuration still selects upload destinations; these inputs prepare credentials.
A custom workflow can instead use its existing native credential setup and call
the same CLI. No organization-specific login code belongs in ship.

## Interrupted releases

An upload can succeed remotely before its command reports a failure. Ship keeps
prepared artifacts and records the source commit, phase, completed uploads and
uncertain package in `dist/ship-recovery/release.json`, with the manifest/lockfile
diff in `release.patch`. GitHub retains `dist/` as `interrupted-release` for seven
days on a failed job. A first-upload failure is also treated as uncertain.

For an interrupted CI release, download that artifact before the retention ends:
`gh run download RUN_ID -n interrupted-release -D dist`. Inspect the recorded
phase and the remote tags before taking another action:

| Failed phase | Finish |
| --- | --- |
| Publication | Check the configured registry for each prepared version/file. Use the original built artifacts to upload only missing files. If none reached the registry, restoring the source version files is safe. Otherwise retain the release versions. |
| Release commit/tags or push | Finish the version commit and any missing `<package>-v<version>` tags. Push the commit and tags atomically with `git push --atomic origin HEAD:refs/heads/main refs/tags/TAG...`, using the configured branch/remote. A moved remote branch needs an operator decision; do not force-push. |
| Development restore/push | The release tags are already pushed. Restore each manifest to its recorded version plus `+dev`, update the Python lock with `uv version --package NAME VERSION+dev`, and commit/push that restore. For npm use `npm --prefix PACKAGE_DIR pkg set version=VERSION+dev`; its version marker does not change the pnpm lock. |

A clean checkout at `source_commit` plus `git apply dist/ship-recovery/release.patch`
reconstructs the preserved version files. Completed phases need no repeat.
These are operator recovery steps; an agent still performs uploads through an
authorized GitHub runner. A normal release is not a resume command, and a new
version does not repair a missing tag for an already published version.

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
  Fronts [`docs/conventions.md`](docs/conventions.md).

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
