---
name: ship-release
description: Use when working in a repository that releases with ship (a `[tool.ship]` table in its pyproject.toml) and the task touches a release, a version, a tag, a lockfile, or a dependency on one of the repository's own packages — releasing a package, deciding whether to release, refreshing dependencies to their newest releases, running the policy check, reading what changed since the last release, or answering why a manifest version ends in `+dev`. Fronts the ship README; the README is the specification.
---

<!-- Written by `ship skills sync` from the installed habemus-papadum-ship. Do not edit; change the source in pdum_ship and run the sync again. -->
# Releasing and dependencies with ship

Read [the README](README.md) before acting; it defines the model and
every command. This skill says what to do first and what not to do.

## The model in one paragraph

Each package has its own version and its own tag `<package>-v<version>`.
The tags are the record of releases; between releases the manifest says
`<last release>+dev`. Consumers declare the repository's own packages with
floors (`>=X.Y`), never exact pins, and commit their lockfiles; a lockfile
changes only when a command is asked to change it.

## What to run

| Question | Command |
| --- | --- |
| Which packages exist, at what version, last tag | `uv run ship packages` |
| What changed since each package's last release | `uv run ship changed` |
| Is the repository within policy | `uv run ship deps check` |
| Plan a release without writing anything | `uv run ship release <package> --bump minor --dry-run` |
| Move the lockfile to the newest releases of our packages, then test | `uv run ship deps refresh` |
| Third-party too, within ranges, behind the release-age gate | `uv run ship deps refresh --all` |

`ship` runs through `uv run` in every repository, including a pnpm-only
one: the root `pyproject.toml` exists to install it. An npm package is named
by its scoped name (`@scope/name`); the unscoped tag component is accepted
too.

## Rules an agent follows here

- Never edit a version field by hand. `ship release` owns it; between
  releases it says `<last release>+dev`, and `ship deps check` reports
  anything else.
- Never publish from this machine. When the task implies a release, ask the
  operator; when they agree, run the repository's release workflow:
  `gh workflow run release.yml -f package=<name> -f bump=minor -f dry_run=true`
  to see the plan, then the same with `dry_run=false`. The workflow waits for
  CI on the commit, publishes, tags, pushes and restores the `+dev` marker.
- Do not update other repositories that consume a package you changed; their
  scheduled `ship deps refresh` does that. The one exception: when your change
  needs a newer release of another of our packages, raise that package's
  floor in this repository.
- Never pin one of our packages to an exact version without a reason on the
  same line or in `[tool.ship.pins]`.
- Never commit a development link: a `path` or `editable` entry in
  `[tool.uv.sources]`, or a `link:`/`file:` dependency in a `package.json`.
  For a local experiment in Python use
  `uv run --with-editable ../other/package …`, which edits nothing. In a
  pnpm workspace there is no equivalent: `pnpm link` writes `link:` into
  `package.json` and is itself the leak; work inside the workspace, or
  publish.
- In a TypeScript library, a dependency whose objects, exception classes or
  types cross the library's public API, or that holds process-wide state, is
  a `peerDependency` with a floor: `>=X.Y.0`, or `workspace:>=X.Y.0` inside
  the workspace. Never `^0.x`, never `workspace:^`.
- Run `uv run ship deps check` before finishing.

## When something is off

- `ship deps check` says the manifest is not `<tag>+dev`: someone edited the
  version. Set it back (`uv version --package <name> <tag>+dev`, or edit
  `package.json`); do not release around it.
- `ship deps check` says a skill copy is stale: run `uv run ship skills
  sync` and commit the result; the installed ship changed.
- The release workflow stopped at "Require a successful CI run": CI is red
  on that commit. Fix CI; the gate is doing its job.
- A refresh broke a test: fix the consumer when the fix is clear; otherwise
  pin the offending package at its previous version with a reason naming the
  failure, and tell the operator. The check will keep reporting the pin.

Configuration reference, the release steps in order, the changed-detection
rule and the failure behaviour are all in [the README](README.md).
