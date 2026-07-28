<!-- Thanks for the PR. Keep it focused and green. Full contract: CONTRIBUTING.md -->

## What & why

<!-- What does this change, and what problem does it solve? Link the related issue. Explain the *why* —
     that is the part a reviewer cannot reconstruct from the diff. -->

Touches: <!-- cendor-sdk / cendor-init / docs -->

## Gates — run each one bare and read its exit code

<!-- Exactly what CI runs (.github/workflows/ci.yml). Never pipe a gate into `tail`/`grep` and chain
     the next step off `&&`: a pipeline's exit code is the last command's, so a failing check reads
     as a pass. -->

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
find src -path '*/cendor/__init__.py'   # the namespace guard — must print NOTHING
uv run mypy -p cendor.sdk
uv run pytest -q
```

- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] The namespace guard printed nothing
- [ ] `uv run mypy -p cendor.sdk` — `ruff` + `pytest` alone do not catch a type error CI will
- [ ] `uv run pytest -q` — green, and **offline**: no API key, no network (provider HTTP mocked with `respx`, or a recorded `cassette` fixture)

No sibling `../cendor-libs` checkout? Drop the dev `[tool.uv.sources]` override and resolve the
published libraries from PyPI, which is what CI does:

```bash
UV_NO_SOURCES=1 uv sync && UV_NO_SOURCES=1 uv run pytest -q
```

Touched `cendor-init/`? It is a **standalone** package with its own environment and CI job:

```bash
cd cendor-init && uv sync --extra dev && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy src && uv run pytest -q
```

- [ ] The `cendor-init` gates above (only if this PR touches `cendor-init/`)

## Checklist

- [ ] Tests added or updated in this PR for the new behavior
- [ ] Public symbols carry a docstring with an `@example` and the correct one-line call shape — that is what an editor's language server (and an AI assistant) reads
- [ ] The relevant page in `docs/` is updated — these pages are the published source for cendor.ai/docs/sdk, so fix docs *here*, never downstream. A Python-only feature gets the mandated *"Python only (for now)"* TypeScript panel
- [ ] User-visible changes noted in `CHANGELOG.md` (or `cendor-init/CHANGELOG.md`) in this same commit
- [ ] **No version bump** — that is a maintainer step, and a major is never autonomous

## The rules this repo will not bend

- [ ] I did **not** create `src/cendor/__init__.py` — this distribution owns `src/cendor/sdk/` only, and a top-level `__init__.py` silently breaks every other `cendor.*` import in the user's environment
- [ ] Money is `decimal.Decimal`, never `float` — costs, prices, and budgets end to end
- [ ] Provider SDKs stay in `[project.optional-dependencies]` and are imported lazily behind `try/except` — installing `cendor-sdk` must never pull a provider SDK
- [ ] Governance still attaches through `cendor-core`'s bus / interceptor / `Sink` / `Compressor` seams — nothing patched, and the re-exported `budget` / `guard` / `Policy` / `AuditLog` / `trace` are still the identical library objects
- [ ] Still local-first: no required account, network, or running server
- [ ] Every number I added is reproducible from the tests or a benchmark, and nothing claims regulatory compliance (`acttrace` produces *evidence to support* a case) or gives legal advice
- [ ] Commit messages are conventional-ish with a body, and carry **no `Co-Authored-By` trailer**
