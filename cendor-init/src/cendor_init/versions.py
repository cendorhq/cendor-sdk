"""Committed FALLBACK snapshot of published versions.

GENERATED — do not hand-edit; a maintainer regenerates it from Cendor's single version source,
and a hand edit is overwritten.

``doctor`` reads this OFFLINE to decide whether an installed / pinned cendor package is behind —
it is only a hint; the live source of truth is https://cendor.ai/releases.json, which
``doctor --online`` reads. Versions are INDEPENDENT across languages; the parity matrix, not
matching numbers, is the contract.
"""

from __future__ import annotations

AS_OF = "2026-07-31"

#: The live machine-readable feed ``doctor --online`` fetches.
RELEASES_URL = "https://cendor.ai/releases.json"

PYPI: dict[str, str] = {
    "cendor-core": "1.16.0",
    "cendor-tokenguard": "1.7.0",
    "cendor-guardrails": "1.7.0",
    "cendor-contextkit": "1.0.3",
    "cendor-squeeze": "1.1.1",
    "cendor-cassette": "1.1.1",
    "cendor-acttrace": "1.14.0",
    "cendor-libs": "1.2.0",
    "cendor": "1.1.0",
    "cendor-sdk": "1.22.0",
    "cendor-mcp": "0.1.7",
    "cendor-init": "0.3.0",
}

#: The optional self-hosted observability container (not a PyPI package).
MONITOR_IMAGE_TAG = "0.15.0"
