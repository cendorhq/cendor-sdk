"""Committed FALLBACK snapshot of published versions.

Mirrors the site /releases source of truth (cendor-site/src/pages/releases.astro),
cendor-mcp/data/versions.json, and the TypeScript twin's templates/versions.json. ``doctor`` reads
this OFFLINE to decide whether an installed / pinned cendor package is behind — it is only a hint;
the live source of truth is https://cendor.ai/releases. Keep in sync after every release (see the
cendorhq root CLAUDE.md -> 'After EVERY release, sync all version/reference surfaces'). Versions are
INDEPENDENT across languages; the parity matrix, not matching numbers, is the contract.
"""

from __future__ import annotations

AS_OF = "2026-07-23"

PYPI: dict[str, str] = {
    "cendor-core": "1.10.0",
    "cendor-tokenguard": "1.5.0",
    "cendor-guardrails": "1.6.0",
    "cendor-contextkit": "1.0.3",
    "cendor-squeeze": "1.1.0",
    "cendor-cassette": "1.1.0",
    "cendor-acttrace": "1.10.1",
    "cendor-libs": "1.2.0",
    "cendor": "1.1.0",
    "cendor-sdk": "1.14.0",
    "cendor-mcp": "0.1.5",
    "cendor-init": "0.2.2",
}
