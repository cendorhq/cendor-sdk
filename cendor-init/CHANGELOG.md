# cendor-init changelog

## Unreleased

## 0.3.0 — 2026-07-27

- **`doctor --online`** — opt-in. Compares your installed/pinned versions against the live feed at
  `https://cendor.ai/releases.json` instead of the snapshot compiled into this CLI. The snapshot is a
  lagging oracle by construction (only as fresh as the CLI), and `uvx cendor-init` keeps the
  documented path current — but a **pinned** init in CI, which is exactly where "you are behind"
  matters, can be arbitrarily stale.
  **Without the flag there is no network call at all**, and that is now asserted by a test rather than
  assumed. An unreachable feed degrades to the bundled snapshot with an `info` finding and does not
  change the exit code — being offline is not a wiring problem.
- **Lockfile detection** — `doctor` now reads `uv.lock` / `poetry.lock` / `pdm.lock` and names the
  **lock** when the lock is what is holding Cendor back. A declared range can be perfectly wide
  (`cendor-core>=1.0,<2.0`) while the lock beside it pins something years old, and the build stays
  green the whole time — the range, which is what a reader checks, is not the constraint.
  Honest limit: it reads the lock as text. It reports what is pinned; it does not resolve.
- Offline versions snapshot refreshed to the 2026-07-27 shelf (`cendor-core` 1.14.2 /
  `@cendor/core` 0.16.2, `cendor-cassette` 1.1.1, `cendor-guardrails` 1.6.1). The snapshot and its
  TypeScript twin are now **generated** from `cendor-site/src/data/versions.json` — five hand-synced
  files became one edited and four generated, after they drifted on 2026-07-26.

- Offline versions snapshot refreshed to the 2026-07-24 shelf (`cendor-sdk` 1.17.0 / `@cendor/sdk`
  0.21.1; `/releases` remains the source of truth).
- **Snapshot-bump policy:** the JS `doctor` clean-bill test fixture now derives its `@cendor/core`
  pin from the bundled versions snapshot rather than hardcoding it, so a snapshot refresh can no
  longer make the fixture read as "behind" (it broke CI twice); the Python `doctor` test already uses
  open `>=` ranges, which never trip the behind check. Snapshot refreshes are changelog-noted here.
- Dev tooling: pinned `ruff==0.15.20` (org-wide pin; matches `cendor-sdk`) and added a CI lint+test
  job so `cendor-init`'s tests actually run on push (they previously ran nowhere). Bookkeeping only —
  no behaviour change, no release.

## 0.2.2 — 2026-07-13

- `init --scaffold` now emits a provider-key line in the starter and next-steps (the SDK reads the
  provider's standard env var, e.g. `OPENAI_API_KEY`, or `Agent(api_key=…)` — never a Cendor key).
- Offline versions snapshot refreshed to the 2026-07-13 shelf (`cendor-sdk` 1.6.2 / `@cendor/sdk`
  0.9.2, `cendor-init` / `@cendor/init` 0.2.2); `/releases` remains the source of truth.

## 0.2.1 — 2026-07-11

- `doctor` warns when the installed `cendor-core`'s bundled price snapshot is more than 30 days old
  (models released since then estimate at $0 until `prices.refresh()` or an upgrade — an offline
  hint, never an error).
- `__version__` is now derived from the installed distribution metadata (`importlib.metadata`), so
  it can no longer drift from the published version (0.2.0 shipped reporting 0.1.1).
- Offline versions snapshot refreshed to the 2026-07-11 patch shelf (core 1.5.2 / 0.5.2,
  mcp 0.1.3, init 0.2.1).

## 0.2.0 — 2026-07-11

- `init --scaffold` is now **SDK-aware**: when the project declares/installs `cendor-sdk` (or
  `@cendor/sdk` in the TS twin) it writes a governed `Agent(...)` + `run(...)` starter instead of the
  libraries' `instrument()` + `budget` starter. Detection falls back to the libs starter otherwise.
- Version snapshot refreshed to the 2026-07-11 shelf (`/releases` remains the source of truth).

## 0.1.1 — 2026-07-11

- Remove the dead, undocumented `-y/--yes` flag (a vestige of the dropped interactive design).
- README: add the full `Options` table (was npm-README-only), keeping the two READMEs in lockstep.
- `doctor`: snapshot the `cendor-mcp` / `cendor-init` package versions so it can flag them when a
  project pins an outdated one (offline hint; `/releases` remains the source of truth).
- Docs: the vendored rules templates now point at the dedicated
  [`assistant-rules`](https://cendor.ai/docs/assistant-rules) page (the docs split out the AI-assistant
  material). Template bodies are byte-identical — no behavior change.

## 0.1.0 — 2026-07-10

- First release. `init` writes the assistant rules files (Copilot / Cursor / AGENTS.md / Claude Code /
  Windsurf), can add the MCP connect config (`--mcp`), and scaffolds a correct `instrument()` starter
  (`--scaffold`) — idempotent, offline, no key. `doctor` static-checks wiring and exits non-zero on
  hard problems for CI.
