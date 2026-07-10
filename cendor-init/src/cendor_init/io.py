"""Filesystem helpers: a marker-delimited "managed block" so re-running ``init`` updates its own block
in place instead of duplicating it, and never clobbers the user's surrounding content."""

from __future__ import annotations

import re

MARKER_BEGIN = (
    "<!-- BEGIN CENDOR: managed by @cendor/init — edits between these markers are overwritten. -->"
)
MARKER_END = "<!-- END CENDOR -->"


def managed_block(body: str) -> str:
    return f"{MARKER_BEGIN}\n{body.strip()}\n{MARKER_END}"


def upsert_managed(existing: str | None, body: str) -> tuple[str, str]:
    """Insert or refresh our managed block. Returns ``(content, kind)`` where kind is
    ``created`` | ``updated`` | ``appended``:

    - no file yet → create it as just the block;
    - file has our markers → replace only the region between them (idempotent), keep the rest;
    - file exists without markers → append the block after a blank line (never touch existing text).
    """
    block = managed_block(body)
    if existing is None or existing.strip() == "":
        return f"{block}\n", "created"

    b = existing.find(MARKER_BEGIN)
    e = existing.find(MARKER_END)
    if b != -1 and e != -1 and e > b:
        before = re.sub(r"\s+$", "", existing[:b])
        after = re.sub(r"^\s+", "", existing[e + len(MARKER_END) :])
        parts = [p for p in (before, block, after) if p]
        return "\n\n".join(parts) + "\n", "updated"

    trimmed = re.sub(r"\s+$", "", existing)
    return f"{trimmed}\n\n{block}\n", "appended"
