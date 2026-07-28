# Contributing to cendor-sdk

Thanks for your interest in contributing. This file covers this repository: the `cendor-sdk` package
at the root, and the standalone [`cendor-init`](cendor-init/) CLI beside it.

Non-negotiables live in [`CLAUDE.md`](CLAUDE.md) — read it first. The short version: never create
`src/cendor/__init__.py`, money is `Decimal`, provider SDKs are optional extras, and no test ever
touches the network.

## Ground rules

- **Honest claims.** Every number in docs, the README, or a docstring must be reproducible from the
  test suite or a benchmark. Never overstate coverage, provider support, or compliance. `acttrace`
  produces *evidence to support* compliance — never a guarantee.
- **Local-first.** Nothing here may require an account, a network call, or a running server. Provider
  SDKs are optional extras, never hard dependencies.
- **Cooperate through `cendor-core`.** Governance attaches to core's bus / interceptor / `Sink` /
  `Compressor` seams. The SDK adds no governance machinery of its own and patches nothing.
- Be respectful and constructive — see the [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting set up

Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -q
```

`pyproject.toml` carries a **dev-only** `[tool.uv.sources]` override that resolves the `cendor-*`
libraries from a sibling [`cendor-libs`](https://github.com/cendorhq/cendor-libs) checkout
(`../cendor-libs/packages/*`, editable) so you can iterate against a local `cendor-core`. You do
**not** need it:

```bash
# No sibling checkout? Ignore the override and resolve the PUBLISHED libraries from PyPI:
UV_NO_SOURCES=1 uv sync
UV_NO_SOURCES=1 uv run pytest -q
```

CI does exactly this when it has no sibling checkout, so both paths are supported and both are green.
A released build never carries the override.

### The `cendor-init` CLI

`cendor-init/` is a separate, self-contained package (stdlib only) with its own environment and its
own CI job — it is not a dependency of `cendor-sdk`:

```bash
cd cendor-init
uv sync --extra dev
uv run pytest -q
```

## Before you open a PR — run the gates bare

These are the same checks CI runs ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

```bash
uv run ruff check .
uv run ruff format --check .
find src -path '*/cendor/__init__.py'   # must print NOTHING — see CLAUDE.md rule 1
uv run mypy -p cendor.sdk
uv run pytest -q
```

and, in `cendor-init/`:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

Run each **bare** and read its exit code. Do not pipe a gate into `tail`/`grep` and chain the next
step off `&&` — a pipeline's exit code is the last command's, so a failing check reads as a pass.

All tests run **offline** — no API key, no network. Provider HTTP is mocked with `respx` so the real
client libraries are still exercised. If a change needs a network call to pass, it doesn't belong in
the test suite; record a `cassette` fixture instead.

## Making a change

1. Open an issue first for anything non-trivial, so we can agree on the approach.
2. Fork, branch, and keep the change focused. Match the surrounding style; `ruff` is the linter *and*
   the formatter (pinned in the dev group — don't bump it in a feature PR).
3. Add or update tests in the same PR. New behavior ships with tests.
4. Update the relevant page in [`docs/`](docs/). Those pages are the published source for
   [cendor.ai/docs/sdk](https://cendor.ai/docs/sdk/getting-started) — fix docs here, never downstream.
   If you add a Python-only feature, add the mandated *"Python only (for now)"* TypeScript panel so
   the docs' language toggle stays honest.
5. Public symbols carry a docstring with an `@example` and the correct one-line call shape — that is
   what an editor's language server (and an AI assistant) reads.
6. Note user-visible changes in [`CHANGELOG.md`](CHANGELOG.md) (or
   [`cendor-init/CHANGELOG.md`](cendor-init/CHANGELOG.md)) in the same commit.
7. Open a PR against `main` with a clear description of the *why*.

Version bumps are a maintainer step, not part of a feature PR — and a **major** bump is never
autonomous (see [`CLAUDE.md`](CLAUDE.md) → Versioning).

## Commit and PR conventions

- Conventional-ish commit messages (`feat:`, `fix:`, `docs:`, `chore:`), with a body explaining the
  reasoning.
- Do **not** add a `Co-Authored-By` trailer.
- Keep PRs green: CI runs lint, format, the namespace guard, type checks, and the test suite on Linux
  and Windows, plus a job that runs the suite at the declared dependency floors.

## Security

Please don't file security problems as public issues — see [`SECURITY.md`](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the project's Apache-2.0
license (see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)).
