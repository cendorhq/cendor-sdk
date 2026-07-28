# cendor-init

**One command to make your project Cendor-ready and Cendor-fluent for your AI assistant** — plus a
`doctor` that catches the wiring mistakes before they bite. Offline by default: no API key, and no
network call unless you ask for one.

![PyPI](https://img.shields.io/pypi/v/cendor-init) ![license](https://img.shields.io/badge/license-Apache_2.0-blue) · `uvx cendor-init`

```bash
uvx cendor-init            # detect + write assistant rules files (idempotent)
uvx cendor-init doctor     # validate the wiring; exit 1 on hard problems (CI-usable)
uvx cendor-init doctor --online   # same, but check versions against the live release feed
```

> Optional developer tooling — dependency-free stdlib. It writes files and inspects your project; it
> makes **no network call** unless you pass `doctor --online`, and no Cendor library depends on it at
> runtime. (Node users:
> `npx @cendor/init`.) This is the Python twin of `@cendor/init`; both share the same behavior.

## What `init` does

1. **Detects your project** — Python (`pyproject.toml` / `requirements`) or Node (`package.json`),
   which provider SDKs you have, and which `cendor-*` / `@cendor/*` packages are installed.
2. **Writes the matching assistant rules file(s)** so your assistant reads the correct Cendor
   call-shapes on every edit. Detected by default; `--all` for every one:
   - `.github/copilot-instructions.md` (GitHub Copilot)
   - `.cursor/rules/cendor.mdc` (Cursor)
   - `AGENTS.md` (the cross-tool default — always written)
   - a marked section in `CLAUDE.md` (Claude Code)
   - `.windsurf/rules` (Windsurf)

   **Idempotent and safe:** re-running updates a marker-delimited block in place — never duplicates,
   never clobbers your surrounding content. A dedicated file it didn't create is left alone unless
   you pass `--force`.
3. **Offers MCP setup** (`--mcp`) — drops the connect config for the Cendor MCP server (remote
   `mcp.cendor.ai` or local `uvx cendor-mcp` / `npx @cendor/mcp`) where it's absent.
4. **Optional starter** (`--scaffold`) — a minimal, correct starter for your language: a governed
   **`Agent`** loop (budget + guardrails + `guard` + audit) when `cendor-sdk` / `@cendor/sdk` is
   detected, otherwise an `instrument()` + budgeted-call example.

The rules content is a copy of the docs source of truth,
[Rules files](https://cendor.ai/docs/assistant-rules) — kept in sync, never forked.

## What `doctor` checks

Static checks only — it **never mutates** your project, and exits non-zero on hard problems so it
works in CI:

- **Namespace** — a stray `cendor/__init__.py` in your tree, or a bare `import cendor` (the namespace
  has no module body — import `from cendor.<tool>`).
- **Provider deps** — a provider SDK your code imports but hasn't installed/declared (Cendor never
  pulls one for you; they are optional extras). Uses the installed environment when available.
- **`instrument()` once** — warns if Cendor is imported but the client is never wrapped.
- **Money** — flags coercing a price/cost to `float` (it should stay `Decimal`).
- **Telemetry wiring** — a committed `CENDOR_TELEMETRY=off` beside a configured OpenTelemetry
  provider, or an OTel pipeline on a `cendor-*` older than the version that emits on its own. Neither
  raises at runtime — the emitters are deliberately silent.
- **Versions** — warns when an installed/pinned `cendor-*` version trails the latest release, or when
  the installed `cendor-core`'s bundled price snapshot is more than 30 days old.
- **Lockfiles** — reads `uv.lock` / `poetry.lock` / `pdm.lock` and names the **lock** when the lock is
  what's holding Cendor back: a declared range can be perfectly wide while the lock beside it pins
  something old, and the build stays green the whole time. Honest limit: it reads the lock as text —
  it reports what is pinned, it does not resolve.

**`doctor` is offline by default — no network call of any kind.** Add `--online` and the version check
reads the live feed at `https://cendor.ai/releases.json` instead of the snapshot compiled into this
CLI; that's the case that matters in CI, where the CLI itself is pinned and its snapshot can be
arbitrarily stale. An unreachable feed degrades to the snapshot with an `info` finding and never
changes the exit code — being offline is not a wiring problem.

```bash
uvx cendor-init --help
```

## Options

`init` (the default command):

| Flag | Effect |
|---|---|
| `--all` | write every assistant rules file, not just the detected ones |
| `--assistant <list>` | comma-separated subset: `copilot,cursor,agents,claude,windsurf` |
| `--mcp` | also drop MCP connect config where absent |
| `--scaffold` | also write a correct starter — a governed `Agent` when the SDK is present, else `instrument()`+budget |
| `--force` | overwrite an owned file (`.cursor/rules/cendor.mdc`) even if not ours |
| `--dry-run` | show what would change without writing |

`doctor`:

| Flag | Effect |
|---|---|
| `--online` | check versions against `https://cendor.ai/releases.json` instead of the bundled snapshot |

Apache-2.0 · [cendor.ai](https://cendor.ai) · [For AI assistants](https://cendor.ai/docs/for-ai-assistants) · [MCP](https://cendor.ai/mcp)
