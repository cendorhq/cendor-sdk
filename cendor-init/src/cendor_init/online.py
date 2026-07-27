"""OPT-IN live version lookup for ``doctor --online``.

Cendor never checks for updates on its own — no library opens a socket, and ``doctor`` with no flag
makes zero network calls (there is a test that asserts exactly that). This module exists so that a
human, or a CI job, can *deliberately* ask "what is current?" and get a real answer instead of the
snapshot that was baked into whichever version of this CLI happens to be installed.

The offline snapshot in :mod:`.versions` is a lagging oracle by construction: it is only as fresh as
the CLI. ``uvx cendor-init`` fetches the latest CLI each run, so the documented path stays current —
but a *pinned* init in CI, which is precisely where "you are behind" matters most, can be arbitrarily
stale. ``--online`` closes that gap without making the network the default.

Source: https://cendor.ai/releases.json — a static, CORS-open feed rendered from the same data as the
human /releases page, so the two cannot disagree.

stdlib only (urllib): this package has no dependencies and keeps it that way.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .versions import RELEASES_URL

#: Short by design — a version check must never be the reason a CI job hangs.
TIMEOUT_SECONDS = 5.0

USER_AGENT = "cendor-init doctor --online (+https://cendor.ai)"


class OnlineLookupError(RuntimeError):
    """The live feed could not be read. Carries a human-readable reason, never a traceback."""


def fetch_releases(url: str = RELEASES_URL, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any]:
    """Fetch and parse the live release feed.

    Raises:
        OnlineLookupError: on any network, HTTP, or parse failure — with a reason a human can act on.
            Callers degrade to the offline snapshot rather than failing the whole run: being unable to
            reach the internet is not a wiring problem, and ``doctor`` is meant to be usable offline.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            if resp.status != 200:
                raise OnlineLookupError(f"{url} returned HTTP {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise OnlineLookupError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OnlineLookupError(f"could not reach {url} ({exc.reason})") from exc
    except TimeoutError as exc:  # pragma: no cover - timing dependent
        raise OnlineLookupError(f"timed out after {timeout:g}s reaching {url}") from exc
    except OSError as exc:  # pragma: no cover - defensive
        raise OnlineLookupError(f"could not reach {url} ({exc})") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnlineLookupError(f"{url} did not return valid JSON") from exc

    if not isinstance(data, dict) or "libraries" not in data:
        raise OnlineLookupError(f"{url} returned an unexpected shape (no 'libraries')")
    return data


def pypi_map(feed: dict[str, Any]) -> dict[str, str]:
    """Flatten the feed's package rows into ``{dist name: version}``, matching ``versions.PYPI``.

    Unknown or future top-level sections are ignored rather than erroring: the feed's contract is that
    fields are only ever ADDED, so an older CLI must keep working against a newer feed.
    """
    out: dict[str, str] = {}
    for section in ("libraries", "sdk", "devtooling"):
        for row in feed.get(section) or []:
            if not isinstance(row, dict):
                continue
            name, ver = row.get("pypi"), row.get("pypiVer")
            if isinstance(name, str) and isinstance(ver, str) and name and ver:
                out[name] = ver
    return out
