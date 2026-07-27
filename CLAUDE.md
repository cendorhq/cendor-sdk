# CLAUDE.md — cendor-sdk

The org constitution is the workspace-root `cendorhq/CLAUDE.md`; this repo obeys it. This file
exists so the rules below travel WITH the repo — a session checked out here alone must still see
them.

- **No `Co-Authored-By` trailer** on commits (org-wide rule).

## Versioning — the org standard (see the workspace `CLAUDE.md`)

1. **A MAJOR bump needs Raghav's explicit approval. Never autonomous.** Propose it, say what breaks,
   wait. **Minor and patch need no approval** — ship them. Enforced by
   `node scripts/check-major-bump.mjs` (in CI and in `verify-hold`), which reads an in-band
   `Approved-Major:` line in the changeset, or an `APPROVED-MAJOR` file listing the exact version.
2. **All libraries in one language share ONE major** — `@cendor/*` move together, `cendor-*` move
   together. Minors and patches stay independent per package.
3. **Majors are NOT coupled across languages.** The parity matrix is the contract, not matching
   numbers.
4. **Use minors.** A new capability is a **minor**; a fix is a **patch**. Do not drift into
   patch-patch-patch-then-a-surprise-major — the version number has to carry information.
