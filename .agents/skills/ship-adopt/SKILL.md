---
name: ship-adopt
description: Use when bringing a repository into the ship release model or keeping it there — a repository that still releases with its own scripts, a lockstep versioning script, `-alpha` or hand-edited versions, or no release process; adding a new publishable package to a repository that already uses ship; adding the `[tool.ship]` table, the baseline tags, the release workflow, the CI check, the pre-commit hook or the agent instructions; or when `ship deps check` reports a repository out of policy and the fix is structural rather than a one-line edit. Fronts docs/conventions.md, which is the procedure.
---

<!-- Written by `ship skills sync` from the installed habemus-papadum-ship. Do not edit; change the source in pdum_ship and run the sync again. -->
# Adopting ship in a repository

Read [the conventions](conventions.md) before changing anything;
they define what a compliant repository looks like and give one numbered
procedure for a uv repository and one for a pnpm workspace. [The
README](README.md) is the reference for the commands and the
configuration keys the procedure uses.

`ship` always runs as `uv run ship …`, in a pnpm-only repository too: the
root `pyproject.toml` the procedure adds exists to install it.

## Before you start

1. Confirm the operator wants this repository on ship now. Adoption deletes
   release scripts, re-tags history and changes how versions are written.
2. Find the last release: the highest existing tag and the version it
   shipped. That version is the **baseline**: the baseline tags
   `<package>-v<baseline>` go on that commit, the manifests are set to
   `<baseline>+dev`, and floors between siblings start at it.
3. Check for work in flight on other branches or worktrees. Decoupling a
   lockstep repository edits every manifest and will conflict with it.

## The procedure

- uv repository: [the uv steps](conventions.md#adopting-ship-in-a-uv-repository).
- pnpm workspace: [the pnpm steps](conventions.md#adopting-ship-in-a-pnpm-workspace),
  which begin with the peer audit. The rule for peer versus regular
  dependency, with the cases that need judgment (a thrown exception class,
  a type-only import), is in step 2 there.

Do the steps in order; each ends with a check (`ship packages`, `ship deps
check`, `ship changed`, a dry-run release). The caller workflow, the CI
snippet and the pre-commit entry are copied from the conventions, not from
another repository. Commit once, push the commit and the baseline tags, run
the workflow with `dry_run=true`, and leave the real release to the
operator.

## Adding a package to a repository that already uses ship

See [adding a package](conventions.md#adding-a-package). The new
manifest starts at `0.0.0+dev` with no tag; its first `ship release` computes
`0.1.0` (minor) or `0.0.1` (patch) from that. A scaffolder in the repository
must write the same.

## What not to do

- Do not keep a lockstep versioning script "for now": ship either owns the
  versions or it does not.
- Do not write the release version into the manifest and commit it; the
  release command does that on the release commit only.
- Do not copy another repository's `release.yml` steps; call the reusable
  workflow.
- Do not leave `workspace:^` on a sibling dependency, peer or regular; it
  publishes as one minor version of a 0.x package.
- Do not mention any organisation-specific infrastructure in a public
  repository's instructions; the conventions are generic.
