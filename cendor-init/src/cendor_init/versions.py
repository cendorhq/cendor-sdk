"""Committed FALLBACK snapshot of published versions.

GENERATED — do not hand-edit. Source: cendor-site/src/data/versions.json; regenerate with
``node scripts/gen-version-mirrors.mjs`` in cendor-site.

``doctor`` reads this OFFLINE to decide whether an installed / pinned cendor package is behind —
it is only a hint; the live source of truth is https://cendor.ai/releases.json, which
``doctor --online`` reads. Versions are INDEPENDENT across languages; the parity matrix, not
matching numbers, is the contract.
"""

from __future__ import annotations

AS_OF = "2026-07-27"

#: The live machine-readable feed ``doctor --online`` fetches.
RELEASES_URL = "https://cendor.ai/releases.json"

PYPI: dict[str, str] = {
    "cendor-core": "1.14.2",
    "cendor-tokenguard": "1.6.1",
    "cendor-guardrails": "1.6.1",
    "cendor-contextkit": "1.0.3",
    "cendor-squeeze": "1.1.1",
    "cendor-cassette": "1.1.1",
    "cendor-acttrace": "1.13.1",
    "cendor-libs": "1.2.0",
    "cendor": "1.1.0",
    "cendor-sdk": "1.20.0",
    "cendor-mcp": "0.1.6",
    "cendor-init": "0.3.0",
}

#: The optional self-hosted observability container (not a PyPI package).
MONITOR_IMAGE_TAG = "0.15.0"
