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
  `workspace:>=X.Y.0`). An exact pin only with a reason on the same line or
  in `[tool.ship.pins]`.
- Committed lockfiles (`uv.lock`, `pnpm-lock.yaml`), changed only by
  `uv add`/`pnpm add`-style commands and `ship deps refresh`.
- In TypeScript libraries, framework and identity dependencies as
  `peerDependencies` with floors (the rule is below, under the pnpm
  procedure).
- The `ship-check` pre-commit hook in `.pre-commit-config.yaml`, and
  `ship deps check` in CI (the CI snippet is below).
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

Prerequisites: a clean working tree on `main`, `HEAD` equal to
`origin/main`, no other branch or worktree with unmerged changes to the
manifests. Each step ends with a check; stop when one fails.

1. **Identify the last release.** The highest existing tag and the version
   it shipped, for example `v0.6.0` for `0.6.0`. Record both; the version
   is the **baseline**.
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
   marker: `uv version <baseline>+dev --no-sync` (`--package <name>` in a
   workspace, once per member).
4. **`__version__`.** Replace any literal with
   `importlib.metadata.version("<distribution>")`. Grep `src/`, `tests/`,
   `docs/` and the docs configuration for the literal version and for
   `-alpha`; fix what asserts or prints it.
5. **Baseline tag.** `git tag -a <package>-v<baseline> -m "<package> <baseline> (baseline for ship; same commit as v<baseline>)" v<baseline>^{commit}`.
   One per package in a workspace. Keep the old tag.
6. **`release.yml`.** Create the caller below. Change the documentation
   workflow's trigger to `push: branches: [main]` plus `workflow_dispatch`.
   If CI called a deleted script, fix CI; add the CI snippet below.
7. **Pre-commit hook.** Add the `ship-check` entry (below) to
   `.pre-commit-config.yaml`, creating the file if the repository had none.
   `pre-commit` itself is not a dependency of ship: install the hooks with
   `uvx pre-commit install`, or add `pre-commit` to the dev group and use
   `uv run pre-commit install`. Say which in the README.
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
    (manifest `<baseline>+dev`, last tag the baseline), `uv run ship deps
    check` (`ok`), `uv run ship changed`. The first `ship changed` after
    adoption lists the old pipeline's post-release commit ("back to dev",
    "bump to next version"); it clears at each package's first release, or
    list that subject under `ignore-subjects` in `[tool.ship]`.
11. **Commit and push** the commit and the baseline tags. Set the
    `PYPI_API_TOKEN` repository secret. Run the workflow with
    `dry_run=true`; it waits for CI on the commit and prints the plan. The
    operator runs the real release.

## Adopting ship in a pnpm workspace

Same prerequisites. The steps parallel the uv ones; where a step says
"manifest" it means each package's `package.json`.

1. **Identify the last release**: the highest tag and the version it
   shipped (the baseline). In a lockstep repository every package shares
   it.
2. **Peer audit.** For each publishable package, classify every dependency
   on a sibling or on a framework:
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
     and tests alone.
   - **Regular dependency** when the package uses the dependency only
     internally and nothing of it reaches the caller. Change `workspace:^`
     to `workspace:>=<baseline>` all the same; `workspace:^` publishes as
     `^X.Y.Z`, one minor on a 0.x package.
   Record each decision with a one-line reason in the commit message or the
   README.
3. **Delete the versioning script** and the root `package.json` scripts
   that called it. Leave the root `version` field (ship ignores a private
   root) or set it to `0.0.0`. Fix any scaffolder (`new-package` script)
   that wrote the shared version: a new package starts at `0.0.0+dev`.
4. **Manifests.** Set each publishable package's `version` to
   `<baseline>+dev`. No tool does this for npm; edit the files.
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
   Node; when `.nvmrc` exists the reusable workflow reads it. The
   repository's `.npmrc` must hold no token; the workflow has
   `actions/setup-node` write one that reads `NODE_AUTH_TOKEN`. The
   repository secret is `NPM_TOKEN`.
8. **CI.** Remove any lockstep check; add the CI snippet below.
9. **Pre-commit hook, skills, agent instructions, README**: steps 7–9 of
   the uv procedure.
10. **Check.** `pnpm install` (the lockfile updates for the new specifiers),
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
      # node_version: "24"          # only if the repository pins Node and has no .nvmrc
    secrets:
      PYPI_API_TOKEN: ${{ secrets.PYPI_API_TOKEN }}
      NPM_TOKEN: ${{ secrets.NPM_TOKEN }}
    permissions:
      contents: write
      actions: read
```

A repository with one package may hard-code `package:` and drop the input.
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
`0.0.0+dev`.

## The pre-commit entry

```yaml
repos:
  - repo: https://github.com/habemus-papadum/pdum_ship
    rev: habemus-papadum-ship-v<current ship version>
    hooks:
      - id: ship-check
```

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
> name). Do not publish from this machine. Do not update repositories that
> consume a package you changed; their scheduled refresh does that. The one
> exception: if your change needs a newer release of another of our
> packages, raise that package's floor here. Do not pin one of our packages
> to an exact version without a reason on the same line or in
> `[tool.ship.pins]`. Never commit a `path` or `editable` entry in
> `[tool.uv.sources]`, or a `link:`/`file:` dependency (`pnpm link` writes
> one). Run `uv run ship deps check` before you finish.
