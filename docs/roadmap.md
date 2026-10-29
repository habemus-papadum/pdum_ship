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
2. `cf_browser_credentials` — pilot for a pnpm workspace. **Done 2026-10-29**
   (commit `14881d6`): peer audit, baseline tags on the `v0.8.0` commit,
   decoupled, `scripts/versioning.mjs` gone. The migration was run from the
   `ship-adopt` skill alone; its feedback shaped the pnpm procedure in
   [conventions.md](conventions.md).
3. `pdum_gcp`, `pdum_plumbum`, `pdum_coiled` — same migration as `pdum_aws`.
4. `pdum_agent_tools` — replace the inline `uv version` loop in `release.yml`
   (it bumps four of six members); decouple.
5. `pdum_dsl` — **skipped** by the operator on 2026-10-29; it stays on
   `scripts/_versioning.py` until someone picks it up. When that happens:
   migrate with `ship`, make its first release (it has never been
   released), and remove the old `src/pdum/{dsl,dsl_reference}` tree if dead.
6. `pdum_rfb` — replace `_versioning.py`; decouple `rfb`, `nvenc`, `vtenc` and
   the npm widgets; the wheel-building workflows keep their build jobs and
   gain a tag trigger plus a publish job (`build = false` in ship).
7. `pdum_aiui` — same steps as `cf_browser_credentials`; the canary publish
   mode goes; the gallery deploy moves to its own workflow on push to `main`.
8. `pdum-aiui-intent-tool-v0` — **archive**, decided 2026-10-29: not a
   working project. Not migrated.

## Repository status

| Repository | Before | Now | Notes |
| --- | --- | --- | --- |
| `pdum_ship` | — | 0.1.0 released 2026-10-29 from the operator's machine (the one bootstrap); 0.1.1 to 0.8.0 through the workflow the same day | 0.5.0 refuses a refresh that downgrades or removes third-party packages; 0.6.0 adds npm provenance, tag fetching before checks, the local hook form; 0.7.0 installs pnpm from `packageManager`, ignores untracked files in preflight, adds `ship --version`; 0.8.0 adds `pnpm-root`, `before-build` and `build = false` for `pdum_rfb` |
| `pdum_aws` | local `release.sh` + `hatch publish`, `-alpha` marker | **done**: 0.6.1 released 2026-10-29 through the workflow, tag `habemus-papadum-aws-v0.6.1`, manifest `0.6.1+dev` | CI test fixed (console width) on the way |
| `cf_browser_credentials` | `versioning.mjs`, coupled, `v0.8.0` | **done**: migrated 2026-10-29 (commit `14881d6`), nine packages decoupled with baseline tags `<package>-v0.8.0`, every sibling dependency a peer with a floor; dry run green | first per-package release pending |
| `pdum_aiui` | `versioning.mjs`, coupled, canary mode, gallery deploy in `release.yml`, `v0.24.0` | **done** 2026-10-29 by an agent from the `ship-adopt` skill (e7115456): fourteen public packages decoupled with baseline tags `<unscoped>-v0.24.0`, peer audit applied, canary mode removed, gallery deploy moved to `gallery.yml` on push to `main`, `ship-check` in the existing `.githooks/pre-commit`; CI, gallery and dry run green | three agent worktrees carry manifest edits in the old `workspace:^` shape and will need the floor form on merge; the cf_browser_credentials kit stays a regular dependency of `aiui-cf-creds` (peer promotion is an operator call) |
| `pdum_rfb` | `_versioning.py`, coupled, nested pnpm workspace, platform wheels, `v0.3.0` | **done** 2026-10-29 by an agent from the `ship-adopt` skill (71ce3ba): seven packages decoupled with baseline tags on the `v0.3.0` commit; `pnpm-root = "widgets"`, `before-build` for the rfb wheel's JavaScript, `build = false` for nvenc and vtenc, whose wheel workflows gained a tag trigger and a publish job; the release caller dispatches them after a real release; CI and dry run green | the dispatch job and the wheel publish jobs run only on a real nvenc or vtenc release, so they are unexercised; `~=` member dependencies became floors |
| `pdum_dsl` | `_versioning.py`, coupled, no tags | skipped (operator decision 2026-10-29) | |
| `pdum_agent_tools` | inline `uv version`, coupled, `v0.1.0` covering three of six members | **done** 2026-10-29 by an agent from the `ship-adopt` skill (c403d07): baseline tags for mitm, codex-launcher and hostfork; claude-launcher, agent-history and orchestrator at `0.0.0+dev` with no tag; three CI repairs followed (e45f0eb, 748d924, 2891837); CI green and dry run green (`skip_ci_check` while CI was red) | `typescript/` is a nested pnpm workspace with nothing on npm, outside ship; the Hostfork workflow is a manual check before a hostfork release |
| `pdum_gcp`, `pdum_plumbum` | as `pdum_aws`, `0.7.0-alpha` vs `v0.6.0` | **done** 2026-10-29 by agents from the `ship-adopt` skill (`pdum_gcp` 074027a, `pdum_plumbum` 8a4d4c0); baseline tags `<package>-v0.6.0`; 0.6.1 of each released through the workflow the same day | |
| `pdum_coiled` | as `pdum_aws` plus a build-only pnpm workspace, `0.1.1-alpha` vs `v0.1.0` | **done** 2026-10-29 by an agent from the `ship-adopt` skill (3f9281d); the never-published widgets package made private; baseline tag `habemus-papadum-coiled-v0.1.0`; 0.1.1 released through the workflow the same day | |
| `pdum-aiui-intent-tool-v0` | tags, `versioning.mjs`, no `release.yml`, root still named `pdum_aiui` | archive (operator decision 2026-10-29) | not a working project; no migration |

## The migration, step by step

The procedure lives in [conventions.md](conventions.md): what a compliant
repository has, the steps for a uv repository and for a pnpm workspace,
adding a package, and the policy paragraph for agent instructions. The
`ship-adopt` skill fronts it. For this estate, `ours` is
`["habemus-papadum-*", "@habemus-papadum/*"]`.

## Open items

- `ship-check` at tag `habemus-papadum-ship-v0.1.0` has an invalid manifest
  (fixed in `main`; released as 0.1.1). Repositories reference 0.1.1 or later.
- `pdum-aiui-intent-tool-v0`: archive the repository on GitHub (operator
  action; nothing in ship depends on it).
- Trusted publishing (OIDC) to PyPI and npm: later; the workflow's publish
  step is the only place that changes.
- `ship deps refresh` ran for real on 2026-10-29 in `pdum_aws`. One run asked
  uv to upgrade only `habemus-papadum-ship` and came back with jupyterlab
  downgraded 4.6.2 → 3.6.8 and sixteen other packages churned; it did not
  reproduce and is unexplained. Since 0.5.0 a refresh that downgrades or
  removes a third-party package fails and restores the lockfiles. The first
  scheduled integration run in a consumer repository is still to be set up.
- A release is visible to `uv lock --upgrade-package` only once PyPI's JSON
  simple index has it, a few minutes after the HTML page; a refresh run right
  after a release can miss it. Harmless for a scheduled run.
- `cf-creds-aws` 0.8.1 was published without an npm provenance attestation.
  Since ship 0.6.0 the reusable workflow requests `id-token: write` and
  `pnpm publish` runs with `--provenance` in GitHub Actions; callers grant
  the permission. Later releases carry attestations; 0.8.1 cannot be
  retro-fitted.
- The conventions recommend the `repo: local` hook form since 0.6.0 (runs
  the ship pinned in `uv.lock`; nothing to bump on a ship release).
  `pdum_aws` and `cf_browser_credentials` use it.
