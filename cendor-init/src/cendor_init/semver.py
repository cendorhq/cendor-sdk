"""Minimal version helpers — enough for x.y.z comparisons; no dependency (offline, light)."""

from __future__ import annotations

import re


def clean_version(spec: str) -> str | None:
    """Strip a range operator/prefix (``^1.2.3``, ``>=1.2``, ``~1``) to its first x.y.z-ish token."""
    m = re.search(r"(\d+(?:\.\d+){0,2})", spec)
    return m.group(1) if m else None


def compare_versions(a: str, b: str) -> int:
    """-1 if a<b, 0 if equal, 1 if a>b. Missing parts count as 0."""
    pa = [int(x) if x.isdigit() else 0 for x in a.split(".")]
    pb = [int(x) if x.isdigit() else 0 for x in b.split(".")]
    for i in range(max(len(pa), len(pb))):
        x = pa[i] if i < len(pa) else 0
        y = pb[i] if i < len(pb) else 0
        if x != y:
            return -1 if x < y else 1
    return 0


def range_blocks_latest(spec: str, latest: str) -> bool:
    """Does a declared range PROVABLY exclude ``latest`` (so the user is stuck behind it)? Honest and
    conservative: an open-ended ``>=`` returns False (latest is reachable, just not pinned)."""
    s = spec.strip()
    if s in ("", "*", "latest"):
        return False

    upper = re.search(r"<(=?)\s*(\d+(?:\.\d+){0,2})", s)
    if upper:
        inclusive = upper.group(1) == "="
        c = compare_versions(latest, upper.group(2))
        if (c > 0) if inclusive else (c >= 0):
            return True

    floor = clean_version(s)
    if not floor:
        return False

    parts = [int(x) if x.isdigit() else 0 for x in floor.split(".")]
    a = parts[0] if len(parts) > 0 else 0
    b = parts[1] if len(parts) > 1 else 0
    cc = parts[2] if len(parts) > 2 else 0

    if s.startswith("^"):
        ceil = f"{a + 1}.0.0" if a > 0 else (f"0.{b + 1}.0" if b > 0 else f"0.0.{cc + 1}")
        return compare_versions(latest, ceil) >= 0
    if s.startswith("~"):
        return compare_versions(latest, f"{a}.{b + 1}.0") >= 0
    # Exact pin (`==1.2.3` or bare `1.2.3`) below latest.
    if re.match(r"^(==)?\s*\d", s) and not re.search(r"[>~^]", s):
        return compare_versions(floor, latest) < 0

    return False  # open-ended >= / > — latest is reachable
