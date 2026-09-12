# Contributing

## Development setup

Requires [uv](https://docs.astral.sh/uv/), Xcode with an iOS 27 simulator for
the integration suite, and the shortcuts-playground plugin's `validate-shortcut`
and `sign-shortcut` on `PATH` for a build. The library this project builds on,
[shortcut-forge](../shortcut-forge), is an editable path dependency and must be
checked out beside this repo:

```
git clone https://github.com/steverice/brightwheel-checkin.git
cd brightwheel-checkin
uv sync --dev
uv run pre-commit install --hook-type commit-msg --hook-type pre-commit
```

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/) with
[Gitmoji](https://gitmoji.dev/). Write `feat: …`, `fix: …`, `docs: …`,
`style: …`, `refactor: …`, `test: …`, `chore: …`, or `ci: …`; the commit-msg
hook validates the format and prepends the emoji. Include a scope when it helps
(`fix(attendance): …`). `BREAKING CHANGE:` in the body, or `!` after the type,
marks a breaking change.

## Running checks

```
make lint          # ruff check + ruff format --check + ty check
make test          # the pytest files, no simulator
make check         # lint + test — run before every commit
make test-integ    # ./test.sh: the real shortcuts on a simulator against the mock
```

`./build.sh` builds, validates, and signs the three shortcuts; `./release.sh`
cuts a release. Both are documented in `README.md`.

## Pull requests

1. Branch from `main`.
2. Make changes with conventional commits.
3. Run `make check`, and `make test-integ` for anything that touches the
   generator.
4. Open a PR against `main`. CI validates commit messages, lint, and tests.

## Releases

`./release.sh <tag>` builds and attaches the zip; publishing is a deliberate
second step. Never edit the version or a changelog by hand.
