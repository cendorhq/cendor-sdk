# cendor-sdk — repository

Source for **`cendor-sdk`**, the governed, provider-agnostic agent SDK — the second door into the
[Cendor](https://github.com/cendorhq/cendor-libs) stack.

![CI](https://github.com/cendorhq/cendor-sdk/actions/workflows/ci.yml/badge.svg) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-Apache_2.0-blue)

> **A governed agent in 10 lines** — cost budgets, tamper-evident audit, PII redaction, context
> governance, and record/replay testing as the *foundation*, not plugins.

📦 **On PyPI:** [`pip install cendor-sdk`](https://pypi.org/project/cendor-sdk/) · 📖 **Full project
description & features:** [README-pypi.md](README-pypi.md) (also rendered on the PyPI page).

## Install

```bash
pip install "cendor-sdk[openai,anthropic]"   # or [all] for every provider + interop
```

## Documentation

| Page | Covers |
|---|---|
| [docs/index.md](docs/index.md) | Start here — the two doors + the public API surface |
| [docs/sdk.md](docs/sdk.md) | Quickstart, the loop, tools, structured output, providers, streaming, RAG, memory |
| [docs/multi-agent.md](docs/multi-agent.md) | Handoff, supervisor/router, sequential & parallel |
| [docs/interop.md](docs/interop.md) | MCP, A2A, Foundry/Copilot, OTel span tree, human-in-the-loop |
| [docs/hardening.md](docs/hardening.md) | Retries, checkpointed/resumable runs, durable memory |
| [docs/eval.md](docs/eval.md) | Cassette-backed governed eval & regression testing |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [examples/](examples/) | Runnable, network-free examples |

## Status — stable

All four phases are shipped, tested offline, and documented; provider coverage and agent
capabilities have since been rounded out (streaming, RAG, memory, Hugging Face / Azure AI Foundry /
Foundry Local). See [CHANGELOG.md](CHANGELOG.md).

## Development

This repo ships **`cendor.sdk` only** (a PEP 420 namespace package — there is never a top-level
`src/cendor/__init__.py`). It consumes the published Cendor libraries; for local iteration, a dev
source override in `pyproject.toml` resolves them from a sibling `../cendor-libs` monorepo checkout.

```bash
uv sync                        # install (with the dev source override)
uv run pytest -q               # tests — no network, ever
uv run ruff check . && uv run ruff format --check .
uv run mypy -p cendor.sdk
```

CI runs the above plus a **namespace-guard** check (no `src/cendor/__init__.py`) on Linux + Windows.
Releases are tag-triggered (`v*.*.*`) and publish to PyPI via OIDC trusted publishing
(`.github/workflows/release.yml`).

## License

Apache-2.0.
