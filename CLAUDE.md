# CLAUDE.md — cendor-sdk

Cardinal rules for **this** repository. They live here so they travel WITH the repo: an AI assistant
(or a human) working from this checkout alone must still see them. Every path referenced below exists
in this repo — nothing here points at a file you don't have.

Contributors: start with [`CONTRIBUTING.md`](CONTRIBUTING.md). This file is the short, non-negotiable
subset — the rules that break something silently if you get them wrong.

## Cardinal rules

1. **NEVER create `src/cendor/__init__.py`.** `cendor-sdk` is one distribution contributing to the
   shared PEP 420 `cendor` namespace; it owns `src/cendor/sdk/` **only**. A top-level
   `cendor/__init__.py` turns the implicit namespace into a regular package and silently breaks every
   other `cendor.<tool>` import in the user's environment. `src/cendor/sdk/__init__.py` *should*
   exist — only the `src/cendor/` level is forbidden.
   Check: `.claude/skills/namespace-guard/SKILL.md`; enforced in
   [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (`namespace guard` step).

2. **Money is `Decimal`, never `float`.** Costs, budgets, and prices stay `decimal.Decimal` end to
   end (`src/cendor/sdk/pricing.py`, `Result.cost`) — never coerce to `float` for arithmetic,
   comparison, or serialization. Binary floating point cannot represent a price, and a budget that
   is wrong in the 15th digit is a budget nobody can audit.

3. **Provider SDKs are optional extras, never hard dependencies.** `openai`, `anthropic`,
   `google-genai`, `boto3`, `ollama`, `huggingface_hub`, `foundry-local-sdk`, `mcp` are declared in
   `[project.optional-dependencies]` in [`pyproject.toml`](pyproject.toml) and imported **lazily
   behind `try/except`**. Installing `cendor-sdk` must never pull a provider SDK. The only hard deps
   are the published `cendor-*` libraries.

4. **No network in tests — ever.** Provider HTTP is mocked with `respx` so the *real* client
   libraries are exercised offline, with no API key (`tests/conftest.py`). A test that needs a
   network call to pass does not belong in the suite; record a `cassette` fixture instead.

5. **Cooperate through `cendor-core`'s seams — never patch anything.** Governance attaches to core's
   bus / interceptor / `Sink` / `Compressor` seams. `budget`, `guard`, `Policy`, `AuditLog`, `trace`
   exported from `cendor.sdk` are the **real** library objects re-exported, not wrappers — pinned by
   `tests/test_lib_parity.py`.

6. **Honest claims.** Every number in docs, the README, or a docstring must be reproducible from the
   tests or a benchmark. `acttrace` produces *evidence to support* a compliance case — never a
   guarantee, and never legal advice.

7. **No `Co-Authored-By` trailer** on commits (org-wide rule).

## Gates — what CI runs, and how to run it locally

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) is the contract; reproduce it with:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
find src -path '*/cendor/__init__.py'   # must print NOTHING (rule 1)
uv run mypy -p cendor.sdk
uv run pytest -q
```

`cendor-init/` is a **standalone** package in this repo — its own `pyproject.toml`, its own
environment, its own CI job, and not a dependency of `cendor-sdk`. Gate it separately:

```bash
cd cendor-init && uv sync --extra dev && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy src && uv run pytest -q
```

Local iteration resolves the `cendor-*` libraries from a sibling `../cendor-libs` checkout via the
dev `[tool.uv.sources]` override in `pyproject.toml`. **Without that sibling, set `UV_NO_SOURCES=1`**
and uv ignores the override and resolves the published libraries from PyPI — which is exactly what CI
does when it has no sibling checkout. A released build never carries the override.

> Never chain a gate off a pipeline: a pipeline's exit code is the *last* command's, so
> `uv run ruff check . | tail -1 && git commit` commits even when ruff failed. Run gates bare and
> read the exit code.

## Versioning

1. **A MAJOR bump needs the maintainer's explicit approval. Never autonomous.** Propose it, say what
   breaks, and wait. **Minor and patch need no approval** — ship them. The approval is recorded
   in-band as an `APPROVED-MAJOR` file at this repo root naming the exact version; a maintainer-side
   check that runs across the Cendor repos refuses a major without it. No file, no major.
2. **All Cendor libraries in one language share ONE major** — the `cendor-*` distributions move to a
   new major together, so one number tells a reader the set is coherent. Minors and patches stay
   independent per package.
3. **Majors are NOT coupled across languages.** `cendor-sdk` on PyPI and `@cendor/sdk` on npm can be
   the same capability at different majors — the [parity matrix](https://cendor.ai/docs/languages) is
   the contract, not matching numbers.
4. **Use minors.** A new capability a user can call is a **minor**; a fix is a **patch**. Do not drift
   into patch-patch-patch-then-a-surprise-major — the version number has to carry information.
5. **`cendor-init` versions on its own cadence** (it is optional dev tooling), outside rules 2 and 4.
   Rule 1 still applies.

## Release

- `cendor-sdk` publishes on a `v*.*.*` tag ([`.github/workflows/release.yml`](.github/workflows/release.yml),
  PyPI OIDC trusted publishing). The tag **must** match `version` in `pyproject.toml` — the workflow
  asserts it and fails the release otherwise.
- `cendor-init` publishes from [`.github/workflows/release-init.yml`](.github/workflows/release-init.yml)
  (manual dispatch), gated on the same lint/type/test checks. Its history is
  [`cendor-init/CHANGELOG.md`](cendor-init/CHANGELOG.md); releases are marked with an `init-v*` tag.
- Keep [`CHANGELOG.md`](CHANGELOG.md) current in the same commit as the change — `release.yml` cuts
  the GitHub Release notes from the matching `## [<version>]` section.
- The PyPI long description is [`README-pypi.md`](README-pypi.md), not `README.md`. Both need the
  update when a user-visible surface changes.
- [`INTEGRATION.md`](INTEGRATION.md) is a **verbatim** copy of the canonical file shared by every
  Cendor package and ships inside the wheel (`cendor/sdk/INTEGRATION.md`). Edit it only to track the
  canonical text — never to say something specific to this repo.

## Docs

Pages live in [`docs/`](docs/) and are also published at
[cendor.ai/docs/sdk](https://cendor.ai/docs/sdk/getting-started) — the markdown here is the source,
so fix docs *here*, never downstream.

- Same-product links are relative and GitHub-style (`memory.md#anchor`); links to the **libraries**
  side are absolute site paths (`/docs/tokenguard`) because only basenames are rewritten.
- Language tabs use the `<!-- tabs: lang -->` / `<!-- tab: Python -->` / `<!-- tab: TypeScript -->` /
  `<!-- /tabs -->` comment markers — invisible on GitHub, rendered as a synced toggle on the site.
  A Python-only feature still gets a TypeScript panel, and it says *"Python only (for now)"* with a
  pointer to the parity matrix — never silent Python code in a TS tab, never a fake TS sample.
- Every public symbol carries an `@example` and a one-line correct call-shape in its docstring, so an
  editor's language server hands an assistant the right shape inline. The canonical trap registry is
  [cendor.ai/docs/for-ai-assistants](https://cendor.ai/docs/for-ai-assistants); the SDK pointer page
  is [`docs/for-ai-assistants.md`](docs/for-ai-assistants.md).
