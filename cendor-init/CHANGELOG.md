# cendor-init changelog

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
