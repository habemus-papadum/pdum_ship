# Roadmap: one release model across the habemus-papadum repositories

This file is the working record for moving every habemus-papadum repository to
`ship`. It says what the target is, which repository is in which state, and what
is open. Update it as repositories move; keep the history short.

## The target

Every repository:

- has a root `pyproject.toml` that installs `habemus-papadum-ship` in its dev
  group and holds `[tool.ship]`;
- releases each package on its own, tagged `<package>-v<version>`, with the
  manifest at `<last release>+dev` between releases;
- releases only through its `release.yml`, which calls
  `habemus-papadum/pdum_ship/.github/workflows/release.yml@main`;
- declares dependencies on other habemus-papadum packages as floors
  (`habemus-papadum-x>=0.4`, `"@habemus-papadum/x": ">=0.4.0"`) and commits
  its lockfile;
- runs `ship-check` from `.pre-commit-config.yaml` and `ship deps check` in CI;
- deploys documentation on push to `main`, not on "release published" (a
  per-package release publishes several GitHub releases per repository);
- carries no local release scripts.

The README of this repository is the specification of the model and the
commands.

## Migration order

1. `pdum_aws` — pilot for a single uv package. **Done 2026-10-29** (commit
   `ed8a755`; baseline tag `habemus-papadum-aws-v0.6.0`; first ship release
   pending).
2. `cf_browser_credentials` — pilot for a pnpm workspace: peer audit, baseline
   tags on the `v0.8.0` commit, decouple, replace `scripts/versioning.mjs`.
   Finish or merge the `codex/credential-sources` branch first; decoupling
   edits every manifest and will conflict with it.
3. `pdum_gcp`, `pdum_plumbum`, `pdum_coiled` — same migration as `pdum_aws`.
4. `pdum_agent_tools` — replace the inline `uv version` loop in `release.yml`
   (it bumps four of six members); decouple.
5. `pdum_dsl` — finish the in-progress migration with `ship` instead of
   `scripts/_versioning.py`; make its first release (it has never been
   released). Remove the old `src/pdum/{dsl,dsl_reference}` tree if dead.
6. `pdum_rfb` — replace `_versioning.py`; decouple `rfb`, `nvenc`, `vtenc` and
   the npm widgets; leave the wheel-building workflows alone.
7. `pdum_aiui` — after `cf_browser_credentials`, same steps; merge or finish
   `codex/credential-sources` first; the catalog keeps its structure; the
   gallery deploy stays in `release.yml`.

## Repository status

| Repository | Before | Now | Notes |
| --- | --- | --- | --- |
| `pdum_ship` | — | 0.1.0 released 2026-10-29 from the operator's machine (the one bootstrap); 0.1.1 through the workflow the same day | |
| `pdum_aws` | local `release.sh` + `hatch publish`, `-alpha` marker | **done**: 0.6.1 released 2026-10-29 through the workflow, tag `habemus-papadum-aws-v0.6.1`, manifest `0.6.1+dev` | CI test fixed (console width) on the way |
| `cf_browser_credentials` | `versioning.mjs`, coupled, `v0.8.0` | next | `codex/credential-sources` is fully merged into `main`; its worktree can go |
| `pdum_aiui` | `versioning.mjs`, coupled, `v0.24.0` | not started | `codex/credential-sources` is fully merged into `main`; its worktree can go; stale worktree `pdum_aiui-1d` |
| `pdum_rfb` | `_versioning.py`, coupled, `v0.3.0` | not started | |
| `pdum_dsl` | `_versioning.py`, coupled, no tags | not started | |
| `pdum_agent_tools` | inline `uv version`, coupled, `v0.1.0` | not started | |
| `pdum_gcp`, `pdum_plumbum` | as `pdum_aws`, `0.7.0-alpha` vs `v0.6.0` | not started | |
| `pdum_coiled` | as `pdum_aws`, `0.1.1-alpha` vs `v0.1.0` | not started | |
| `pdum-aiui-intent-tool-v0` | tags, `versioning.mjs`, no `release.yml`, root still named `pdum_aiui` | undecided | rename and adopt, or archive |

## The migration, step by step

The procedure lives in [conventions.md](conventions.md): what a compliant
repository has, the steps for a uv repository and for a pnpm workspace,
adding a package, and the policy paragraph for agent instructions. The
`ship-adopt` skill fronts it. For this estate, `ours` is
`["habemus-papadum-*", "@habemus-papadum/*"]`.

## Open items

- `ship-check` at tag `habemus-papadum-ship-v0.1.0` has an invalid manifest
  (fixed in `main`; released as 0.1.1). Repositories reference 0.1.1 or later.
- `pdum-aiui-intent-tool-v0`: decide between adopting and archiving.
- `pdum_aiui-1d`: a July detached worktree at `0.13.0+dev`; remove unless it
  holds unmerged work.
- `pre-commit run` on the maintainer's machine prompts for credentials for a
  private index from inside hook environments; a machine-level pip index
  setting, unrelated to ship, to be fixed before the hooks are relied on
  locally.
- Trusted publishing (OIDC) to PyPI and npm: later; the workflow's publish
  step is the only place that changes.
- `ship deps refresh` has run only in tests; the first scheduled integration
  run in a consumer repository is still to be set up.
