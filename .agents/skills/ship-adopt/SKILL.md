---
name: ship-adopt
description: Use when bringing a repository into the ship release model or keeping it there — a repository that still releases with its own scripts, a lockstep versioning script, `-alpha` or hand-edited versions, or no release process; adding a new publishable package to a repository that already uses ship; adding the `[tool.ship]` table, the baseline tags, the release workflow, the pre-commit hook or the agent instructions; or when `ship deps check` reports a repository out of policy and the fix is structural rather than a one-line edit. Fronts docs/conventions.md, which is the procedure.
---

<!-- Written by `ship skills sync` from the installed habemus-papadum-ship. Do not edit; change the source in pdum_ship and run the sync again. -->
# Adopting ship in a repository

Read [the conventions](conventions.md) before changing anything;
they define what a compliant repository looks like and give the procedure
for a uv project, a uv workspace and a pnpm workspace. [The
README](README.md) is the reference for the commands and the
configuration keys the procedure uses.

## Before you start

1. Confirm the operator wants this repository on ship now. Adoption deletes
   release scripts, re-tags history and changes how versions are written.
2. Find the last release: the highest existing tag and the version it
   shipped. The baseline tag `<package>-v<that version>` goes on that
   commit, and the manifest is set to `<that version>+dev`.
3. Check for work in flight on other branches or worktrees. Decoupling a
   lockstep repository edits every manifest and will conflict with it.

## What a compliant repository has

The checklist is the first section of
[the conventions](conventions.md#what-a-compliant-repository-has).
In short: a root `pyproject.toml` that installs ship and holds `[tool.ship]`;
one version and one tag series per package; a `release.yml` that calls the
reusable workflow; floors on the repository's own packages; committed
lockfiles; the `ship-check` pre-commit hook; documentation deployed on push
to `main`; the policy paragraph in the agent instructions; synced skill
copies in `.agents/skills` and `.claude/skills`; no release scripts.

## The procedure

Follow the numbered steps in
[the conventions](conventions.md#adopting-ship-in-a-uv-repository)
for the repository's kind. Do them in order; each ends with a check
(`ship packages`, `ship deps check`, `ship changed`, a dry-run release).
Commit once, push the commit and the baseline tags, run the workflow with
`dry_run=true`, and leave the real release to the operator.

## Adding a package to a repository that already uses ship

See [the conventions](conventions.md#adding-a-package). The new
manifest starts at `0.0.0+dev` with no tag; its first `ship release` computes
`0.1.0` (minor) or `0.0.1` (patch) from that.

## What not to do

- Do not keep a lockstep versioning script "for now": ship either owns the
  versions or it does not.
- Do not write the release version into the manifest and commit it; the
  release command does that on the release commit only.
- Do not copy another repository's `release.yml` steps; call the reusable
  workflow.
- Do not mention any organisation-specific infrastructure in a public
  repository's instructions; the conventions are generic.
