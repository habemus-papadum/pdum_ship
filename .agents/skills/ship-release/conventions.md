# Conventions for a repository that releases with ship

This document says what a repository looks like when it is on the ship
release model and how to get it there. The [README](README.md) is the
reference for the commands and the configuration; this is the procedure.
The rules here hold for any repository; nothing in them is specific to one
organisation.

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
  `workspace:>=X.Y.0`). An exact pin only with a reason on the same line or
  in `[tool.ship.pins]`.
- Committed lockfiles (`uv.lock`, `pnpm-lock.yaml`), changed only by
  `uv add`/`pnpm add`-style commands and `ship deps refresh`.
- In TypeScript libraries, framework and identity dependencies as
  `peerDependencies` with floors (see
  [the README](README.md#why-peer-dependencies-matter-for-npm-libraries)).
- The `ship-check` pre-commit hook in `.pre-commit-config.yaml`, and
  `ship deps check` in CI.
- Documentation deployed on push to `main` (or on a schedule or by hand),
  not on "release published": a per-package release publishes one GitHub
  release per package.
- The policy paragraph in the repository's agent instructions (below).
- Current copies of ship's skills in `.agents/skills/` with `.claude/skills`
  pointing at them (`ship skills sync`), so agents find them with or without
  the plugin marketplace.
- No release scripts of its own. Setup and notebook helpers may stay.
- `__version__`, if exported, read from the installed metadata:
  `importlib.metadata.version("<distribution>")`.

## Adopting ship in a uv repository

Prerequisites: a clean working tree on `main`, `HEAD` equal to
`origin/main`, no other branch or worktree with unmerged changes to the
manifests. Each step ends with a check; stop when one fails.

1. **Identify the last release.** The highest existing tag and the version
   it shipped, for example `v0.6.0` for `0.6.0`. Record both.
2. **Delete the release scripts** (`release.sh`, `publish.sh`,
   `pre-release.sh`, a versioning script). Read each first; keep anything
   that is setup rather than release.
3. **`pyproject.toml`.** Append:

   ```toml
   # ship releases this package: one version per package, the tag is the truth,
   # the manifest carries <last release>+dev between releases. See the README.
   [tool.ship]
   ours = ["<pattern for your distributions>", "<pattern for your npm scope>"]
   shared-paths = [".github/workflows/**"]
   ```

   Then `uv add --dev habemus-papadum-ship`; remove `hatch` and any
   interactive release helper from the dev group; set the development
   marker: `uv version <last release>+dev --no-sync`
   (`--package <name>` in a workspace, once per member).
4. **`__version__`.** Replace any literal with
   `importlib.metadata.version("<distribution>")`. Grep `src/`, `tests/`,
   `docs/` and the docs configuration for the literal version and for
   `-alpha`; fix what asserts or prints it.
5. **Baseline tag.** `git tag -a <package>-v<last> -m "<package> <last> (baseline for ship; same commit as v<last>)" v<last>^{commit}`.
   One per package in a workspace. Keep the old tag.
6. **`release.yml`.** Create:

   ```yaml
   name: release
   on:
     workflow_dispatch:
       inputs:
         package:
           description: Package to release
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
   permissions:
     contents: read
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

   A repository with one package may hard-code `package:` and drop the
   input. Change the documentation workflow's trigger to
   `push: branches: [main]` plus `workflow_dispatch`. If CI called a deleted
   script, fix CI.
7. **Pre-commit hook.** Add to `.pre-commit-config.yaml`:

   ```yaml
     - repo: https://github.com/habemus-papadum/pdum_ship
       rev: habemus-papadum-ship-v<current ship version>
       hooks:
         - id: ship-check
   ```
8. **Skills.** `uv run ship skills sync`. It writes `.agents/skills/ship-*`
   and makes `.claude/skills` a symlink to `../.agents/skills` (or writes
   copies if `.claude/skills` is already a directory). Commit the result.
9. **Agent instructions and README.** Replace any "humans only" release
   rules with the policy paragraph below. Replace a "Publishing" section
   with a short "Releasing" section: releases run from the `release`
   workflow, each release is tagged `<package>-v<version>`, the version
   between releases is `<last>+dev`, and
   `uv run ship release <package> --bump minor --dry-run` shows the plan.
10. **Check.** `uv lock`, `uv sync`, lint, tests, `uv run ship packages`
    (manifest `<last>+dev`, last tag the baseline), `uv run ship deps check`
    (`ok`), `uv run ship changed`.
11. **Commit and push** the commit and the baseline tags. Set the
    `PYPI_API_TOKEN` repository secret. Run the workflow with
    `dry_run=true`; it waits for CI on the commit and prints the plan. The
    operator runs the real release.

## Adopting ship in a pnpm workspace

The uv steps apply, with these differences.

- The root `pyproject.toml` is not a package:

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

  Then `uv lock` and commit `uv.lock`.
- **Peer audit before decoupling.** For each library, every dependency
  whose objects cross its public API or that holds process-wide state moves
  from `dependencies` to `peerDependencies` with a floor
  (`workspace:>=X.Y.0` for siblings, `>=X.Y.0` otherwise) and a matching
  `devDependencies` entry so the library builds alone. A dependency used
  only internally stays a regular dependency. `workspace:^` is published
  as `^X.Y.Z`, one minor on a 0.x package, and must not appear in
  `peerDependencies`.
- **Baseline tags**, one per publishable package, all on the last
  lockstep release commit; manifests to `<last>+dev`.
- **Versioning script.** Delete it and the shared-version logic in
  `release.yml`; `ship release` writes one package's version.
- **Publishing token.** `NPM_TOKEN` as the repository secret. The reusable
  workflow has `actions/setup-node` write the `.npmrc` that reads it from
  `NODE_AUTH_TOKEN`; the repository's committed `.npmrc` must not hold a
  token.
- **Build.** `ship release` runs the package's `build` script and those of
  its workspace dependencies before `pnpm publish`.
- **Checks.** `pnpm install --frozen-lockfile` must pass after the manifest
  edits; `ship deps check` reports narrow peer ranges and `link:`/`file:`
  dependencies.

## Adding a package

In a repository that already uses ship:

1. Create the package where the workspace expects it (`[tool.uv.workspace]
   members` or `pnpm-workspace.yaml`); ship discovers it from there.
2. Set its manifest version to `0.0.0+dev`. Do not tag.
3. Declare its dependencies on sibling packages as floors
   (`workspace:>=0.0.0` in pnpm until the sibling has a release;
   `{ workspace = true }` in uv).
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
> after a `dry_run=true` to see the plan). Do not publish from this machine.
> Do not update repositories that consume a package you changed; their
> scheduled refresh does that. The one exception: if your change needs a
> newer release of another of our packages, raise that package's floor
> here. Do not pin one of our packages to an exact version without a reason
> on the same line or in `[tool.ship.pins]`. Never commit a `path` or
> `editable` entry in `[tool.uv.sources]`, or a `link:`/`file:` dependency.
> Run `uv run ship deps check` before you finish.
