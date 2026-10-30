# AGENTS.md

`ship` is a command-line tool: it releases packages and refreshes dependencies in
uv and pnpm workspaces. README.md is the specification; keep it current when
behaviour changes.

## Working here

```sh
uv sync                 # installs the dev group (pytest, ruff)
uv run pytest           # runs real git and uv in temporary repositories; needs network for uv build
uv run ruff check .
uv run ruff format .
uv run ship --help
```

Source lives in `src/pdum/ship/`. `versions.py` is the version model, `repo.py` wraps git,
`packages.py` discovers packages and reads or writes versions, `release.py` is the release
flow, `deps.py` is refresh and check, `cli.py` is the Typer command line. Tests in `tests/`
build throwaway repositories with a bare `origin` and run the flow end to end with
publishing disabled.

## Rules

- Never edit a version field by hand, here or in a repository that uses ship. The
  release command owns them; between releases the manifest says `<last release>+dev`.
- Never publish from your own machine. To release, ask the operator; when they agree,
  run the repository's release workflow (`gh workflow run release.yml -f package=NAME
  -f bump=minor -f dry_run=false`).
- Never commit a `path` or `editable` entry in `[tool.uv.sources]`, or an unapproved
  `link:`/`file:` dependency in a `package.json`. Intentional npm vendor references
  need an exact, reasoned `[[tool.ship.local-dependencies]]` exception. Published
  runtime metadata must remain free of local references.
- Do not update repositories that consume a package you changed; their scheduled
  refresh does that. The one exception: if your change needs a newer release of
  another of our packages, raise that package's floor in this repository.
- Run `uv run ship deps check` before you finish.
