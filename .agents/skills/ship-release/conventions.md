# Conventions for a repository that releases with ship

This document says what a repository looks like when it is on the ship
release model and how to get it there. The [README](README.md) is the
reference for the commands and the configuration; this is the procedure.
The rules here hold for any repository; nothing in them is specific to one
organisation.

`ship` runs as `uv run ship …` in every repository, including a pnpm-only
one: the root `pyproject.toml` installs it, so the command is the same
everywhere and CI needs one setup step for it.

## What a compliant repository has

- A root `pyproject.toml`, even in a pnpm-only repository. It installs
  `habemus-papadum-ship` in the dev group and holds `[tool.ship]` with at
  least `ours`, the patterns for the packages the maintainers publish.
- One version per publishable package, in that package's manifest, and one
  tag series per package: `<package>-v<version>`. Between releases the
  manifest says `<last release>+dev`.
- A `release.yml` with a `workflow_dispatch` trigger that calls
  `habemus-papadum/pdum_ship/.github/workflows/release.yml@main`. No other
  way to publish is documented.
- Dependencies on the maintainers' own packages declared as floors
  (`name>=X.Y`, `"@scope/name": ">=X.Y.0"`; inside a pnpm workspace,
  `workspace:>=X.Y.0`). `~=X.Y.0`, `^X.Y.0` and `~X.Y.0` are caps, not
  floors; rewrite them. An exact pin or a cap only with a reason on the
  same line or in `[tool.ship.pins]`; `ship deps check` reports the rest.
- In a repository with a pnpm workspace, `"packageManager": "pnpm@<version>"`
  in the workspace root's `package.json`; the reusable workflow and CI
  install that pnpm from it.
- Committed lockfiles (`uv.lock`, `pnpm-lock.yaml`), changed only by
  `uv add`/`pnpm add`-style commands and `ship deps refresh`.
- In TypeScript libraries, framework and identity dependencies as
  `peerDependencies` with floors (the rule is below, under the pnpm
  procedure).
- `ship deps check --hook` before every commit: the `ship-check` entry in
  `.pre-commit-config.yaml`, or a line in the repository's own
  `core.hooksPath` script; and `ship deps check` in CI (the CI snippet is
  below).
- Documentation deployed on push to `main` (or on a schedule or by hand),
  not on "release published": a per-package release publishes one GitHub
  release per package.
- The policy paragraph in the repository's agent instructions (below).
- Current copies of ship's skills in `.agents/skills/` with `.claude/skills`
  pointing at them (`ship skills sync`), so agents find them with or without
  the plugin marketplace.
- No release scripts or version-bumping scripts of its own. Setup and
  notebook helpers may stay. A scaffolder for new packages writes
  `0.0.0+dev`.
- `__version__`, if exported, read from the installed metadata:
  `importlib.metadata.version("<distribution>")`.

## Adopting ship in a uv repository

Prerequisites: on `main` with `HEAD` equal to `origin/main`, CI green on
`main` (the workflow's dry run waits on it; `skip_ci_check=true` shows the
plan while CI is repaired, but a real release needs it green), no other
branch or worktree with unmerged changes to the files the procedure
touches (manifests, lockfiles, workflows, README, agent instructions,
docs), and no uncommitted changes to them. Unrelated
uncommitted files (a scratch notebook, `.DS_Store`) may stay; stage by
explicit path and leave them out. Install the newest `habemus-papadum-ship`
on PyPI (`uv add --dev habemus-papadum-ship` resolves it); a version quoted
in a brief may be behind. Each step ends with a check; stop when one fails.

1. **Identify the last release.** The highest existing tag and the version
   it shipped, for example `v0.6.0` for `0.6.0`. Record both; the version
   is the **baseline**. In a workspace, check which members that tag
   published (the GitHub release's assets, the registry, the workflow as
   it was at that commit): a lockstep tag can cover some members only. A
   member that has never been published has no baseline: its manifest
   becomes `0.0.0+dev`, it gets no tag, and its first `--bump minor`
   release computes `0.1.0`.
2. **Delete the release scripts** (`release.sh`, `publish.sh`,
   `pre-release.sh`, a versioning script). Read each first; keep anything
   that is setup rather than release, including a `build.sh` that only syncs
   the environment, and fix the README and docs lines that described it as
   a release step (`docs/index.md` is sometimes a symlink to the README). A
   pre-release checker (lint, tests, clean-tree check) is release-only: CI
   and `ship release` do that work. Grep the workflows for each deleted
   script's name before deleting it.
3. **`pyproject.toml`.** Append:

   ```toml
   # ship releases this package: one version per package, the tag is the truth,
   # the manifest carries <last release>+dev between releases. See the README.
   [tool.ship]
   ours = ["<pattern for your distributions>", "<pattern for your npm scope>"]
   shared-paths = [".github/workflows/**"]
   ```

   Then, in this order: `uv add --dev habemus-papadum-ship`; `uv remove
   --dev hatch` and the interactive release helpers (`inquirerpy` is the
   usual one; grep `src/` and `tests/` for imports before removing anything
   and keep what the code uses, such as `rich`); `uv version
   <baseline>+dev --no-sync` (`--package <name>` in a workspace, once per
   member; `0.0.0+dev` for a never-published member), which rewrites
   `uv.lock` for the new version and prints the old version in normalized
   form (`0.7.0a0 => 0.6.0+dev` for `0.7.0-alpha`; a member already on the
   marker prints `0.1.0+dev => 0.1.0+dev`, which is fine); and `uv lock`
   last so the lockfile is final before the checks. A workspace root
   without a `[project]` table takes `uv add --dev` all the same; ship
   skips it and `ship packages` lists exactly the members. Sibling
   dependencies become floors wherever they are declared, in
   `dependencies` and in `[project.optional-dependencies]` alike, with the
   `{ workspace = true }` sources kept (uv inherits them from the root
   `[tool.uv.sources]`). The next `uv sync` removes packages that only
   `hatch` needed; that is expected. If any of these commands fails saying
   a package that certainly exists "was not found in the package
   registry", an extra index from the machine's user-level uv
   configuration answered badly; run the whole sequence with
   `UV_NO_CONFIG=1`. Afterwards `grep registry uv.lock | sort -u` must list
   only the indexes the project declares; a public repository must not lock
   against a private one.
4. **`__version__`.** Replace the literal with the installed metadata:

   ```python
   from importlib.metadata import version as _dist_version

   __version__ = _dist_version("<distribution>")
   ```

   Let the import sorter place the import. Grep `src/`, `tests/`, `docs/`,
   the docs configuration and every other tree the old release script
   wrote to (a TypeScript `version.ts`) for the current version, the
   previous one and `-alpha` (a mirror the script stopped maintaining holds
   an old value); delete each mirror or derive it from the manifest at build
   time. A version field that a registry other than PyPI or npm reads and
   that no package owns (a Claude plugin's `plugin.json`) is not a mirror:
   it keeps its last released value and is bumped by hand in the commit
   that changes the plugin materially; ship ignores it. A test that asserts
   a non-empty string keeps passing; one that asserts the literal is
   rewritten.
5. **Baseline tag.** `git tag -a <package>-v<baseline> -m "<package> <baseline> (baseline for ship; same commit as v<baseline>)" v<baseline>^{commit}`.
   One per package in a workspace. Keep the old tag. Check with
   `git tag --points-at 'v<baseline>^{commit}'`, which lists the old tag
   and every new one (`--points-at v<baseline>` names only the annotated
   tag itself).
6. **`release.yml`.** Create the caller below. In a Python-only
   repository drop its `NPM_TOKEN` line and the "npm packages by their
   scoped name" wording. The reusable workflow gates on one CI workflow
   (`ci_workflow`, default `ci.yml`); if the old release gated on a second
   one, fold that job into `ci.yml` or document it as a check the
   releaser runs by hand. Change the documentation workflow's trigger to
   `push: branches: [main]` plus `workflow_dispatch`, deleting the
   `release: types: [published]` entry that is usually there.
   A documentation or site deploy that was a job inside the old release
   workflow moves verbatim into its own workflow with that trigger,
   narrowed with `paths` when the build's inputs are clear; an OIDC role
   step moves with it (check that the role's trust policy binds the branch,
   not the workflow file name). The migration push itself runs that first
   deploy. If CI called a deleted script, fix CI; add the CI snippet below.
7. **Pre-commit hook.** Add the local `ship-check` entry (below) to
   `.pre-commit-config.yaml`, creating the file if the repository had none.
   `pre-commit` itself is not a dependency of ship: if the repository's
   setup script already runs `pre-commit install` from the dev group, keep
   that; otherwise install the hooks with `uvx pre-commit install`. Say
   which in the README. A repository that already runs its hooks through
   `core.hooksPath` (a `.githooks/pre-commit` script installed by a
   `prepare` script) adds `uv run --locked ship deps check --hook` to that
   script instead; `pre-commit install` refuses to run alongside
   `core.hooksPath`, so a `.pre-commit-config.yaml` there would be dead. The
   requirement is the check before every commit, not the file.
8. **Skills.** `uv run ship skills sync`. It writes `.agents/skills/ship-*`
   and makes `.claude/skills` a symlink to `../.agents/skills` (or writes
   copies under `.claude/skills/ship-*` beside whatever is there if
   `.claude/skills` is already a directory). Commit the result, copies
   included; `ship deps check --hook` compares both trees against the
   installed ship.
9. **Agent instructions and README.** Grep both, and the docs, for
   `release.sh`, `publish`, `-alpha`, `hatch`, `Release Process` and
   `version`: the old model usually appears in several places (a version
   rule, a release rule, a Publishing section, a CI/CD release section, a
   build-system line, an automation-scripts list). Replace each in place;
   do not add a new section beside an old one. The "humans only" release
   rules become the policy paragraph below. A "Publishing" section becomes a
   short "Releasing" section: releases run from the `release` workflow, each
   release is tagged `<package>-v<version>`, the version between releases is
   `<last>+dev`, and `uv run ship release <package> --bump minor --dry-run`
   shows the plan.
10. **Check.** `uv lock`, then the repository's own sync command (`uv sync
    --all-packages --all-extras` where CI uses it: a plain `uv sync` after
    `uv add` drops extra-only packages and the tests fail at collection),
    lint, tests, `uv run ship packages` (manifest `<baseline>+dev`, last
    tag the baseline), `uv run ship deps check` (`ok`), `uv run ship
    changed`, and `uv run ship release <package> --bump patch --dry-run`
    for one package (a dry run tolerates the uncommitted migration). ship's
    default test command is `uv run --locked pytest` from the repository
    root, the whole workspace with no markers; a workspace sets `test` in
    `[tool.ship]` to CI's command (`["uv run --locked pytest -m 'not
    live'"]`) so a release runs what CI runs. The first `ship changed` after
    adoption lists the old pipeline's post-release commit ("back to dev",
    "Bump to 0.7.0-alpha"). Add that subject's prefix to `ignore-subjects`
    in `[tool.ship]` (for example `ignore-subjects = ["Bump to", "back to
    dev"]`) so `ship changed` reports only real changes from the start; the
    entry is harmless once the package has released. The migration commit
    itself counts as a change, so the first dry run reports it under
    "commits since last release"; that is correct.
11. **Commit and push** the commit and the baseline tags in one push
    (`git push origin main <tag>…`): CI's `ship deps check` on that commit
    needs the tags. Stage by explicit path; leave unrelated files in the
    working tree (an untracked `.DS_Store`, a scratch notebook) alone. Run
    the workflow with `dry_run=true`; it waits for CI on the commit, which
    takes a minute or two after a push, then prints the plan. A dry run
    needs no secret. The `PYPI_API_TOKEN` (or `NPM_TOKEN`) repository secret
    is needed only for the real release, which the operator runs; check
    with `gh secret list` and report whether it exists. If the run ends as
    `startup_failure` with no job log ("likely failed because of a workflow
    file issue"), the caller grants fewer `permissions` than the reusable
    job requests; compare the two.

## Adopting ship in a pnpm workspace

Same prerequisites. The steps parallel the uv ones; where a step says
"manifest" it means each package's `package.json`.

1. **Identify the last release**: the highest tag and the version it
   shipped (the baseline). In a lockstep repository every package shares
   it.
2. **Peer audit.** For each publishable package, classify every dependency
   on a sibling, on a framework, or on one of the maintainers' packages
   from another repository (the rule is about the relation, not where the
   dependency lives):
   - **Peer** when identity crosses the package's public API or the
     dependency holds process-wide state: the package returns or accepts
     instances of the dependency's classes; it throws the dependency's
     exception classes (an application's `instanceof` against a second copy
     would fail); it imports the dependency's types only (`import type`),
     which means no runtime module is needed and a regular dependency was
     never right; the dependency is a framework runtime, context provider or
     plugin host. Write `peerDependencies: { "<name>": "workspace:>=<baseline>" }`
     for a sibling (`>=X.Y.0` for an external package) and a matching
     `devDependencies: { "<name>": "workspace:*" }` so the package builds
     and tests alone. For a framework, copy the range the repository
     already uses for it (one package's `>=2.0.0-rc.13 <3`, say) rather
     than inventing a second, and rewrite an existing `^` on a framework
     peer (`solid-js: ^1.8.0`) to the floor form too; `ship deps check`
     polices only the maintainers' own packages, so this is by hand.
   - **Regular dependency** when the package uses the dependency only
     internally and nothing of it reaches the caller. Change `workspace:^`
     to `workspace:>=<baseline>` all the same; `workspace:^` publishes as
     `^X.Y.Z`, one minor on a 0.x package. An external package of the
     maintainers' at `^X.Y.Z` becomes `>=X.Y.Z`.
   - **devDependencies** when the only use is in the package's own lab,
     example or tests: move it there as `workspace:*` instead of
     classifying it.
   - **Private members** (demos, internal packages, spikes) publish
     nothing, so their ranges never reach a consumer; apply the one rule
     anyway so the workspace reads the same everywhere:
     `workspace:>=<baseline>` on a released sibling, `workspace:>=0.0.0`
     on a never-released one, `workspace:*` in `devDependencies`.
     `ship deps check` polices peers only; the rest is by hand here.
   Record each decision with a one-line reason in the commit message or the
   README.
3. **Delete the versioning script** and the root `package.json` scripts
   that called it. Leave the root `version` field (ship ignores a private
   root) or set it to `0.0.0`; a private workspace member likewise carries
   `0.0.0` and no tag. Fix any scaffolder (`new-package` script)
   that wrote the shared version: a new package starts at `0.0.0+dev`. A
   published scaffolder or CLI that derived its template's sibling ranges
   from its own version (correct only under lockstep) must instead bake
   each sibling's manifest version at build time and write `^<that
   version>` per package; when the template needs a newer sibling, release
   the sibling before the scaffolder. A canary or prerelease dist-tag mode
   has no equivalent in ship: delete it, grep `canary` in docs, scripts and
   `.npmrc` for consumers, and tell them a patch release of the one package
   they need replaces it.
4. **Manifests.** Set each publishable package's `version` to
   `<baseline>+dev`. No tool does this for npm; edit the files. The note in
   the uv procedure's step 4 about a `plugin.json` or other registry-read
   version field applies here too.
5. **Root `pyproject.toml`** (not a package):

   ```toml
   [project]
   name = "<repo>-tooling"
   version = "0"
   requires-python = ">=3.12"

   [tool.uv]
   package = false

   [dependency-groups]
   dev = ["habemus-papadum-ship>=<current>"]

   [tool.ship]
   ours = ["@<scope>/*"]
   shared-paths = [".github/workflows/**", "pnpm-workspace.yaml"]
   ```

   Then `uv lock`, commit `uv.lock`, add `.venv/` to `.gitignore`.
6. **Baseline tags**, one per publishable package, all on the last
   lockstep release commit:
   `git tag -a <unscoped name>-v<baseline> -m "…" v<baseline>^{commit}`.
   The tag component is the unscoped npm name (`@scope/core` tags as
   `core-v…`).
7. **`release.yml`.** Replace the old workflow with the caller below. Set
   `node_version` to match `.nvmrc` or `engines.node` if the repository pins
   Node; when `.nvmrc` exists the reusable workflow reads it. Put
   `"packageManager": "pnpm@<version>"` in the root `package.json` so the
   workflow installs that pnpm (otherwise its `pnpm_version` input, default
   latest, applies), and remove `version:` from any `pnpm/action-setup`
   step in the repository's own workflows; the action errors when the two
   disagree. The
   repository's `.npmrc` must hold no token; the workflow has
   `actions/setup-node` write one that reads `NODE_AUTH_TOKEN`. The
   repository secret is `NPM_TOKEN`.
8. **CI.** Remove any lockstep check; add the CI snippet below.
9. **Pre-commit hook, skills, agent instructions, README**: steps 7–9 of
   the uv procedure.
10. **Check.** Run pnpm non-interactively and let it rewrite the lockfile
    for the new specifiers: `CI=true pnpm install --no-frozen-lockfile`
    (`CI=true` alone implies `--frozen-lockfile` and fails on the changed
    ranges; without `CI=true`, a local pnpm other than the one
    `packageManager` names may prompt to recreate `node_modules`, and a
    newer one may rewrite `pnpm-workspace.yaml` through an `allowBuilds`
    prompt). Confirm `pnpm-workspace.yaml` is unchanged afterwards. Then
    `pnpm install --frozen-lockfile --lockfile-only` (consistent), `pnpm -r
    run build`, typecheck, lint, tests, `uv run ship packages`, `uv run ship
    deps check` (`ok`), `uv run ship changed`.
11. **Commit and push** the commit and the baseline tags; dry-run the
    workflow for one package with `package=@<scope>/<name>` (the scoped npm
    name; the unscoped tag component is also accepted).

## The release workflow caller

```yaml
name: release
on:
  workflow_dispatch:
    inputs:
      package:
        description: Package to release (npm packages by their scoped name)
        type: string
        required: true
      bump:
        description: patch, minor or major, applied to the last release tag
        type: choice
        options: [patch, minor, major]
        default: minor
      dry_run:
        description: Plan and print; write nothing
        type: boolean
        default: true
      skip_ci_check:
        description: Release without waiting for the CI workflow
        type: boolean
        default: false
      skip_tests:
        description: Do not run the test commands before publishing
        type: boolean
        default: false
permissions:
  contents: read
jobs:
  release:
    uses: habemus-papadum/pdum_ship/.github/workflows/release.yml@main
    with:
      package: ${{ inputs.package }}
      bump: ${{ inputs.bump }}
      dry_run: ${{ inputs.dry_run }}
      skip_ci_check: ${{ inputs.skip_ci_check }}
      skip_tests: ${{ inputs.skip_tests }}
      # node_version: "24"          # match the Node CI tests on when there is no .nvmrc (the reusable default is 22)
    secrets:
      PYPI_API_TOKEN: ${{ secrets.PYPI_API_TOKEN }}
      NPM_TOKEN: ${{ secrets.NPM_TOKEN }}
    permissions:
      contents: write
      actions: read
      id-token: write   # npm provenance attestations
```

A repository with one package may hard-code `package:` and drop the input;
its dispatch is then `gh workflow run release.yml -f bump=minor -f dry_run=true`
with no `package`, and the policy paragraph below is adjusted to match. A
Python-only repository drops the `NPM_TOKEN` line. Keep all three
permissions as written: the reusable job requests them, and
a caller that grants less fails before any step runs, as `startup_failure`
with no log. `id-token: write` lets `pnpm publish` attach a provenance
attestation (the repository, commit and workflow run that built the
package, shown on npmjs.com); publishing still authenticates with the
token. A Python-only repository grants it too and simply does not use it.
The reusable workflow also takes `ci_workflow` (default `ci.yml`) and
`uv_version`.

## The CI snippet

`ship deps check` reads tags and lockfiles, so CI needs the full history and
ship installed:

```yaml
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0            # the version-marker check reads tags
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --locked       # installs ship from the dev group
      # … the repository's own build and tests …
      - run: uv run ship deps check
```

A shallow checkout makes every manifest look like it should be
`0.0.0+dev`. Merge these lines into the repository's existing CI job rather
than replacing its steps: keep its checkout action version and options such
as `persist-credentials`, add `fetch-depth: 0` to that step, and add the uv
setup and the two `run` lines where they fit. If CI already installs
through a setup script that runs `uv sync --frozen`, that install is
enough (`--frozen` installs the committed lockfile as it is; `--locked`
additionally asserts it is current, which `ship deps check` also does);
put `uv run ship deps check` right after the setup step. CI's `ship deps check` reads
the baseline tags, so the migration commit and its tags must be pushed
together (step 11) or the first CI run on the commit reports every manifest
as ahead of its tag.

## The pre-commit entry

The repository already installs ship through its lockfile, so the hook runs
that copy; there is no `rev` to bump when ship releases:

```yaml
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

The alternative, for a repository that does not want to run `uv` from the
hook, is the remote form, which pre-commit installs into its own environment
at a pinned release and which therefore needs updating when ship releases:

```yaml
repos:
  - repo: https://github.com/habemus-papadum/pdum_ship
    rev: habemus-papadum-ship-v<current ship version>
    hooks:
      - id: ship-check
```

## A Python package with a build-only pnpm workspace

Some repositories publish one Python distribution and keep a pnpm workspace
only to build assets that ship inside it (widgets bundled into the wheel).
Decide first what is published: `npm view <name>` for each `package.json`
name, and the old release script's publish commands. A workspace member
that is not published gets `"private": true` and `"version": "0.0.0"`; ship
ignores private packages, so the repository then follows the uv procedure
with one addition: the root `package.json` must carry
`"packageManager": "pnpm@<version>"`, because the reusable workflow sets up
pnpm whenever a `package.json` exists and takes the version from that field
(or from its `pnpm_version` input, which defaults to the latest). Remove
`version:` from any existing `pnpm/action-setup` step in the repository's
own workflows, which errors when the two disagree. A version literal the
old script mirrored into TypeScript (`src/version.ts`) is deleted with the
script.

## A pnpm workspace in a subdirectory

Some repositories keep the pnpm workspace under a subdirectory (`widgets/`)
beside a Python package at the root. ship reads only the workspace that
`pnpm-root` names (the repository root by default), so a nested workspace
that publishes nothing (`npm view` 404 for every name) is left out by
leaving `pnpm-root` unset; report its publish status in the migration. A
nested workspace that publishes gets `pnpm-root = "widgets"` in
`[tool.ship]`. ship then reads `widgets/pnpm-workspace.yaml` and
`widgets/package.json`, runs `pnpm` there, and commits
`widgets/pnpm-lock.yaml` with a release. The reusable workflow reads the
same setting: it installs pnpm from `widgets/package.json`'s
`packageManager` and runs `pnpm -C widgets install --frozen-lockfile`. A
public `package.json` at the workspace root is itself a package (ship
publishes it with a bare `pnpm publish` in that directory); the members
publish with `pnpm -r publish --filter`. The default test command becomes
`pnpm -C widgets --filter <name> test`. Everything else follows the pnpm
procedure: private members carry `0.0.0`, the workspace root's
`package.json` carries `packageManager`, and

```toml
shared-paths = [".github/workflows/**", "widgets/pnpm-workspace.yaml"]
```

In the repository's own CI, `pnpm/action-setup` takes
`package_json_file: widgets/package.json` once its `version:` is removed,
and the `ship deps check` step must run in a job that has pnpm installed
(the check runs `pnpm install --frozen-lockfile --lockfile-only`). pnpm 11
records no peer specifiers in the lockfile, so moving peers from
`workspace:^` to `workspace:>=` may leave `pnpm-lock.yaml` unchanged; that
is not a failure.

## A package built outside ship

ship builds with `uv build` on the workflow's Linux runner. A distribution
that needs another platform or an SDK the runner lacks (a CUDA extension, a
macOS framework binding) keeps its own build workflow and tells ship not to
build it:

```toml
[tool.ship.packages."habemus-papadum-nvenc"]
build = false
```

`ship release habemus-papadum-nvenc --bump patch` then writes the version,
runs the tests, commits, tags `habemus-papadum-nvenc-v<version>`, pushes,
and creates the GitHub release without assets; it uploads nothing
(`build = false` implies `publish = false`). The build workflow builds from
that tag and publishes. Four facts shape it:

- A tag pushed with the workflow's `GITHUB_TOKEN` fires no `push: tags:`
  trigger in any other workflow. Keep the `push: tags:` trigger (it serves
  a tag pushed with user credentials) but have the release caller dispatch
  the build: one extra job after the reusable one, with
  `permissions: actions: write`, that finds the newest `<package>-v*` tag
  and runs `gh workflow run <build workflow> --ref main -f ref=<tag>`,
  skipped on dry runs and for the other packages. `pdum_rfb`'s
  `release.yml` has the job.
- A build workflow that reads `inputs.*` sees them empty on a tag push;
  default each one (`${{ inputs.python_versions || '3.12 3.13 3.14' }}`).
- The publish job runs when the ref is the pushed tag or the `ref` input
  (`startsWith(github.ref, 'refs/tags/<package>-v') ||
  startsWith(inputs.ref, '<package>-v')`), checks that ref out, downloads
  the build artifacts, publishes with
  `uv publish --check-url https://pypi.org/simple/ dist/*.whl` under
  `PYPI_API_TOKEN` (`--check-url` makes a re-run skip files PyPI has), and
  attaches the wheels to the release ship created minutes earlier with
  `gh release upload "$TAG" dist/*.whl --clobber`; it needs
  `permissions: contents: write`.
- The registry check before a release still applies: when a build
  workflow failed, re-run it for the tag (`gh workflow run … -f ref=<tag>`);
  do not release again.

A distribution whose `uv build` needs generated inputs (a widget bundle
packed into the wheel) lists the commands that produce them:

```toml
[tool.ship.packages."habemus-papadum-rfb"]
before-build = ["pnpm -C widgets build:anywidget", "pnpm -C widgets build:demo"]
```

They run from the repository root, in order, before `uv build`. The
workflow has installed the pnpm workspace by then; list the build steps
only. `uv build` builds the sdist first and the wheel from the sdist, so
generated, git-ignored files reach the wheel only if the sdist carries
them: with hatch, list them in `[tool.hatch.build] artifacts` (a
conditional `force_include` hook is not enough). Check in step 10 by
building the package the way ship does (`uv build --package <name>`) and
listing the wheel for the generated files.

## Adding a package

In a repository that already uses ship:

1. Create the package where the workspace expects it (`[tool.uv.workspace]
   members` or `pnpm-workspace.yaml`); ship discovers it from there. If the
   repository has a scaffolder, it must write the next two items.
2. Set its manifest version to `0.0.0+dev`. Do not tag.
3. Declare its dependencies on sibling packages as floors
   (`workspace:>=0.0.0` in pnpm until the sibling has a release;
   `{ workspace = true }` in uv), as peers where the rule above says so.
4. `uv lock` or `pnpm install`; `ship packages` lists it with no tag;
   `ship deps check` passes.
5. Its first release computes `0.1.0` from `--bump minor`, or `0.0.1` from
   `--bump patch`.

## Coupled repositories

Releasing every package at one version is supported
(`coupled = "<reason>"` in `[tool.ship]`) but is the exception. The reason
is required and `ship deps check` reports an empty one. Prefer decoupling:
with floors on consumers and peers in libraries, the reasons lockstep
existed (duplicate copies of a shared class, one version to reason about)
no longer apply.

## The policy paragraph for agent instructions

Put this, adapted to the repository's instruction style, where agents will
read it:

> `ship` (habemus-papadum-ship) releases this repository's packages. Each
> package has its own version and tag; between releases the manifest says
> `<last release>+dev`. Never edit a version field by hand. To release, ask
> the operator; when they agree, run the release workflow
> (`gh workflow run release.yml -f package=<name> -f bump=minor -f dry_run=false`,
> after a `dry_run=true` to see the plan; npm packages by their scoped
> name, a wording to drop in a Python-only repository). Do not publish
> from this machine. Do not update repositories that
> consume a package you changed; their scheduled refresh does that. The one
> exception: if your change needs a newer release of another of our
> packages, raise that package's floor here. Do not pin one of our packages
> to an exact version without a reason on the same line or in
> `[tool.ship.pins]`. Never commit a `path` or `editable` entry in
> `[tool.uv.sources]`, or a `link:`/`file:` dependency (`pnpm link` writes
> one). Run `uv run ship deps check` before you finish.
