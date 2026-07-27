"""``doctor`` — validate the wiring so it doesn't break at runtime. Static checks only; NEVER mutates.

Exits non-zero on hard problems (CI-usable), zero when only warnings remain. Python is checked with
the installed environment (importlib.metadata) when available — accurate — and falls back to the
declared pins under ``uvx`` isolation. The npm ecosystem gets a "run @cendor/init doctor" pointer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import versions as version_snapshot
from .detect import (
    PYPI_PKG_FOR_PROVIDER,
    SDK_EXTRA_FOR_PROVIDER,
    Detected,
    detect_project,
    provider_installed,
    providers_used_in_source,
)
from .scan import rel, walk_source
from .semver import clean_version, compare_versions, range_blocks_latest

PY_EXTS = (".py",)
NODE_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")

_MONEY_CONTEXT = re.compile(r"(cost|price|prices|money|usd|\.estimate\s*\(|Money|Decimal)")
_INSTRUMENT_RE = re.compile(r"\binstrument\s*\(")
_USES_CENDOR_RE = re.compile(r"from\s+cendor[.\s]|import\s+cendor|@cendor/")
_BARE_IMPORT_RE = re.compile(r"(?:^|\n)[ \t]*import[ \t]+cendor[ \t]*(?:#.*)?(?:\n|$)")
_STRAY_INIT_RE = re.compile(r"(?:^|[\\/])cendor[\\/]__init__\.py$")


@dataclass
class Finding:
    severity: str  # "error" | "warn" | "info" | "ok"
    title: str
    detail: str
    fix: str | None = None
    locations: list[str] = field(default_factory=list)


@dataclass
class DoctorResult:
    findings: list[Finding]
    exit_code: int


@dataclass
class _Src:
    py: list[tuple[str, str]]  # (path, text)
    node: list[tuple[str, str]]


def _read_all(paths: list[Path]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in paths:
        try:
            out.append((str(p), p.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return out


def _index(root: Path) -> _Src:
    return _Src(
        py=_read_all(walk_source(root, PY_EXTS)), node=_read_all(walk_source(root, NODE_EXTS))
    )


def _usage(src: _Src) -> tuple[int, bool]:
    count = 0
    uses = False
    for _, text in src.py + src.node:
        count += len(_INSTRUMENT_RE.findall(text))
        if _USES_CENDOR_RE.search(text):
            uses = True
    return count, uses


def _check_stray_init(root: Path, src: _Src, out: list[Finding]) -> None:
    stray = [rel(root, Path(p)) for p, _ in src.py if _STRAY_INIT_RE.search(p)]
    if stray:
        out.append(
            Finding(
                "error",
                "A top-level cendor/__init__.py exists",
                "`cendor` is a PEP 420 namespace package. A top-level `cendor/__init__.py` in your own "
                "tree shadows the namespace and breaks every `from cendor.<tool> import ...`.",
                "Delete the file. Each Cendor distribution owns only cendor/<tool>/, never cendor/__init__.py.",
                stray,
            )
        )


def _check_bare_import(root: Path, src: _Src, out: list[Finding]) -> None:
    hits = [rel(root, Path(p)) for p, text in src.py if _BARE_IMPORT_RE.search(text)]
    if hits:
        out.append(
            Finding(
                "warn",
                "Bare `import cendor`",
                "`cendor` is a namespace with no module body, so `import cendor` imports nothing usable.",
                "Import from the flat path instead, e.g. `from cendor.tokenguard import budget`.",
                hits[:8],
            )
        )


def _check_instrument(count: int, uses: bool, out: list[Finding]) -> None:
    if uses and count == 0:
        out.append(
            Finding(
                "warn",
                "No instrument() call found",
                "Cendor is imported but the provider client is never wrapped, so nothing is observed — "
                "budgets, gating, and audit will all see zero calls.",
                "Wrap the client once: `client = instrument(OpenAI())`.",
            )
        )


def _check_money(root: Path, src: _Src, out: list[Finding]) -> None:
    hits: list[str] = []
    for p, text in src.py:
        for line in text.splitlines():
            if re.search(r"\bfloat\s*\(", line) and _MONEY_CONTEXT.search(line):
                hits.append(rel(root, Path(p)))
                break
    for p, text in src.node:
        for line in text.splitlines():
            if (
                re.search(r"\bNumber\s*\(", line) or ".toNumber()" in line
            ) and _MONEY_CONTEXT.search(line):
                hits.append(rel(root, Path(p)))
                break
    if hits:
        out.append(
            Finding(
                "warn",
                "Money coerced to a float / number",
                "Cost and price values are Decimal / decimal.js on purpose — converting to float/number "
                "reintroduces the rounding error the Decimal type exists to prevent.",
                "Keep money as Decimal; format only at the edge with str().",
                sorted(set(hits))[:8],
            )
        )


def _check_py_providers(detected: Detected, src: _Src, out: list[Finding]) -> None:
    used: set[str] = set()
    for _, text in src.py:
        used |= providers_used_in_source(text, "python")
    for p in sorted(used):
        present = p in detected.declared_providers or provider_installed(p)
        if present:
            continue
        pkg = PYPI_PKG_FOR_PROVIDER.get(p, p)
        extra = SDK_EXTRA_FOR_PROVIDER.get(p)
        fix = (
            f'pip install "cendor-sdk[{extra}]"  (or `pip install {pkg}`)'
            if extra
            else f"pip install {pkg}"
        )
        out.append(
            Finding(
                "error",
                f'Provider SDK for "{p}" is used but not installed',
                f"Your code imports the {p} SDK, but it is neither declared in pyproject/requirements "
                "nor importable here. Cendor never pulls a provider SDK for you — it is an optional extra.",
                fix,
            )
        )


def _check_py_versions(
    detected: Detected,
    out: list[Finding],
    latest_map: dict[str, str] | None = None,
    as_of: str | None = None,
) -> None:
    """Flag Cendor packages behind the latest release.

    ``latest_map``/``as_of`` come from the LIVE feed when ``doctor --online`` was asked for; with no
    arguments this reads the bundled snapshot and makes no network call at all (the default).
    """
    latest_of = latest_map if latest_map is not None else version_snapshot.PYPI
    stamp = as_of or version_snapshot.AS_OF
    live = latest_map is not None

    behind: list[str] = []
    for name, ver in detected.installed_pypi.items():
        latest = latest_of.get(name)
        have = clean_version(ver)
        if latest and have and compare_versions(have, latest) < 0:
            behind.append(f"{name} {have} (installed) < {latest}")
    for name, spec in detected.declared_pypi.items():
        if name in detected.installed_pypi:
            continue
        latest = latest_of.get(name)
        if latest and range_blocks_latest(spec, latest):
            behind.append(f'{name} "{spec}" excludes {latest}')
    if behind:
        source = (
            f"the live feed at {version_snapshot.RELEASES_URL} (as of {stamp})"
            if live
            else f"the bundled snapshot (as of {stamp}). This is an offline hint — "
            "re-run with --online, or see /releases, for the canonical answer"
        )
        out.append(
            Finding(
                "warn",
                "A Cendor package looks behind the latest release",
                f"An installed or pinned version trails {source}. "
                "Type Teach and fixes only arrive on upgrade.",
                'pip install -U "cendor-sdk"  (check https://cendor.ai/releases)',
                behind,
            )
        )


#: Lockfiles that pin a resolved version regardless of how wide the declared range is.
_LOCKFILES = ("uv.lock", "poetry.lock", "pdm.lock")

_LOCK_CENDOR_RE = re.compile(
    r'name\s*=\s*"(cendor(?:-[a-z]+)?)"\s*\n\s*version\s*=\s*"([^"]+)"', re.MULTILINE
)


def _check_lockfile(detected: Detected, out: list[Finding]) -> None:
    """Name the LOCKFILE when it is what is holding a project back.

    A wide declared range (``cendor-core>=1.0,<2.0``) looks perfectly healthy while the lock beside it
    pins 1.6.0. Nothing upgrades, CI stays green, and the range — the thing a reader checks — is not
    the constraint. This is not hypothetical: Cendor's own cookbook sat 8 minors behind on core and 12
    on the SDK for exactly this reason, with a green build the whole time.

    Reads the lock as TEXT (no toml parse, no resolver): we only need "which cendor version is pinned
    here", and staying text-only keeps this dependency-free and offline.
    """
    for lock_name in _LOCKFILES:
        lock = detected.root / lock_name
        if not lock.exists():
            continue
        try:
            text = lock.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable lock is not our problem to diagnose
            continue

        stale: list[str] = []
        for name, pinned in _LOCK_CENDOR_RE.findall(text):
            latest = version_snapshot.PYPI.get(name)
            have = clean_version(pinned)
            if latest and have and compare_versions(have, latest) < 0:
                stale.append(f"{name} {have} (locked in {lock_name}) < {latest}")

        if stale:
            out.append(
                Finding(
                    "warn",
                    f"{lock_name} pins Cendor below the latest release",
                    "Your declared ranges may be perfectly wide — the LOCKFILE is what is holding these "
                    "versions. Nothing will upgrade, and the build stays green, until you move the lock.",
                    "uv lock --upgrade   (then re-run your tests)",
                    stale,
                )
            )
        return  # one lockfile per project; the first found is the one in force


_SNAPSHOT_UPDATED_RE = re.compile(r'"_updated"\s*:\s*"(\d{4}-\d{2}-\d{2})"')
PRICE_SNAPSHOT_MAX_AGE_DAYS = 30


def _check_price_snapshot(detected: Detected, out: list[Finding]) -> None:
    """Warn when the installed cendor-core's bundled price snapshot is >30 days old.

    Reads the installed dist's ``prices.json`` via importlib.metadata (no cendor import, still
    offline). Skips silently when cendor-core isn't visible here (e.g. ``uvx`` isolation).
    """
    if "cendor-core" not in detected.installed_pypi:
        return
    try:
        from datetime import date
        from importlib import metadata

        dist = metadata.distribution("cendor-core")
        text = None
        for f in dist.files or []:
            if f.name == "prices.json":
                located = Path(str(dist.locate_file(f)))
                text = located.read_text(encoding="utf-8")
                break
        if not text:
            return
        m = _SNAPSHOT_UPDATED_RE.search(text)
        if not m:
            return
        updated = date.fromisoformat(m.group(1))
        age = (date.today() - updated).days
    except Exception:
        return  # a hint, never a doctor failure
    if age > PRICE_SNAPSHOT_MAX_AGE_DAYS:
        out.append(
            Finding(
                "warn",
                f"Bundled price snapshot is {age} days old",
                f"cendor-core's offline price table is dated {updated.isoformat()}. Models released "
                "since then estimate at $0 (a warn-once blind spot for USD budgets) until the table "
                "is refreshed.",
                "Call `prices.refresh()` at startup, or upgrade: pip install -U cendor-core",
            )
        )


# --------------------------------------------------------------------------- telemetry (the switch)
#: Source patterns that mean "this app configures an OpenTelemetry provider itself".
_OTEL_PROVIDER_RE = re.compile(
    r"set_tracer_provider\s*\(|configure_azure_monitor\s*\(|OTEL_EXPORTER_OTLP_ENDPOINT"
)
#: The explicit attachments. Since core 1.13 / @cendor/core 0.15 they are optional — telemetry flows
#: on its own — so *seeing* them is fine (explicit wins), but combining them with `CENDOR_TELEMETRY=off`
#: is a contradiction worth naming.
_OTEL_ATTACH_RE = re.compile(r"use_span_emitter\s*\(|OTelSink\s*\(|OTelMirror\s*\(|live_spans\s*\(")
#: Floors for the automatic path (the versions that carry the switch).
TELEMETRY_FLOORS = {"cendor-core": "1.13", "cendor-sdk": "1.19", "cendor-acttrace": "1.12"}


def _check_telemetry(root: Path, detected: Detected, src: _Src, out: list[Finding]) -> None:
    """Telemetry wiring, statically.

    Three things can silently cost an app its telemetry: `CENDOR_TELEMETRY=off` committed next to a
    configured provider, an OTel pipeline on a Cendor too old to emit by itself, and (historically) a
    TypeScript `new OTelSink()` constructed above the provider. None of them raises at runtime — the
    emitters are deliberately silent — so they belong in `doctor`.
    """
    texts = [t for _, t in src.py + src.node]
    configures = any(_OTEL_PROVIDER_RE.search(t) for t in texts)
    off_locations = [
        rel(root, Path(path)) for path, t in src.py + src.node if "CENDOR_TELEMETRY=off" in t
    ]
    if configures and off_locations:
        out.append(
            Finding(
                "warn",
                "CENDOR_TELEMETRY=off next to a configured OpenTelemetry provider",
                "This app configures an OTel provider, but `CENDOR_TELEMETRY=off` appears in the "
                "source/config — so Cendor emits nothing: no call spans, no spend counters, no "
                "governance spans. That is a valid choice; it is only worth flagging because nothing "
                "warns at runtime.",
                "Remove `CENDOR_TELEMETRY=off` (or set it to `auto`) to let telemetry flow.",
                off_locations,
            )
        )
    if configures:
        for pkg, floor in TELEMETRY_FLOORS.items():
            installed = detected.installed_pypi.get(pkg)
            if not installed:
                continue
            have = clean_version(installed)
            if have is None:  # an unparseable version is not evidence of anything
                continue
            if compare_versions(have, floor) < 0:
                out.append(
                    Finding(
                        "warn",
                        f"{pkg} {installed} predates automatic telemetry",
                        f"This app configures an OpenTelemetry provider, but {pkg} {installed} is "
                        f"older than {floor}, which is where Cendor started emitting on its own. "
                        "Until you upgrade, telemetry needs the explicit attachments "
                        "(`otel.use_span_emitter()`, `use_sink(sinks.OTelSink())`, `live_spans()`, "
                        "`AuditLog(mirror=OTelMirror())`).",
                        f"pip install -U {pkg}",
                    )
                )
    ts_sink = [
        rel(root, Path(path))
        for path, t in src.node
        if "new OTelSink(" in t and "@cendor/tokenguard" in t
    ]
    if ts_sink:
        out.append(
            Finding(
                "info",
                "TypeScript OTelSink constructed by hand",
                "In `@cendor/tokenguard` < 0.7.0 a sink constructed BEFORE the app's provider bound a "
                "no-op counter permanently (the JS metrics API has no proxy), so spend counters were "
                "silently empty. From 0.7.0 the meter is acquired lazily and order no longer matters "
                "— and the automatic spend tap means you usually need no sink at all.",
                "Drop the explicit sink (telemetry flows on its own), or keep it on @cendor/tokenguard >= 0.7.0.",
                ts_sink,
            )
        )


def run_doctor(root: Path, *, online: bool = False) -> DoctorResult:
    """Static-check a project's Cendor wiring.

    Args:
        root: the project to inspect.
        online: OPT-IN. Fetch the live release feed instead of comparing against the version snapshot
            baked into this CLI. **Default False, and with False this function makes no network call
            of any kind** — that is the contract, and a test asserts it. A failed fetch degrades to the
            snapshot with an ``info`` finding: no internet is not a wiring problem.
    """
    root = Path(root)
    detected = detect_project(root)
    src = _index(root)
    count, uses = _usage(src)
    findings: list[Finding] = []

    latest_map: dict[str, str] | None = None
    latest_as_of: str | None = None
    if online:
        from .online import OnlineLookupError, fetch_releases, pypi_map

        try:
            feed = fetch_releases()
            latest_map = pypi_map(feed)
            latest_as_of = str(feed.get("asOf") or "")
        except OnlineLookupError as exc:
            findings.append(
                Finding(
                    "info",
                    "Could not reach the live release feed — using the bundled snapshot",
                    f"{exc}. Version findings below compare against the snapshot baked into this CLI "
                    f"(as of {version_snapshot.AS_OF}), which may be older than what is published.",
                    f"Check {version_snapshot.RELEASES_URL} in a browser, or drop --online.",
                )
            )

    _check_stray_init(root, src, findings)
    _check_bare_import(root, src, findings)
    _check_instrument(count, uses, findings)
    _check_money(root, src, findings)
    _check_telemetry(root, detected, src, findings)

    if detected.python:
        _check_py_providers(detected, src, findings)
        _check_py_versions(detected, findings, latest_map, latest_as_of)
        _check_lockfile(detected, findings)
        _check_price_snapshot(detected, findings)

    if detected.node:
        findings.append(
            Finding(
                "info",
                "Node project detected",
                "This CLI checks the Python ecosystem exactly. For the npm ecosystem (peer deps, "
                "@cendor/* versions), run `npx @cendor/init doctor` from the same project.",
            )
        )

    has_cendor = (
        uses
        or bool(detected.installed_pypi)
        or bool(detected.declared_pypi)
        or bool(detected.declared_npm)
    )
    if not has_cendor:
        findings.insert(
            0,
            Finding(
                "info",
                "No Cendor usage detected",
                "Found no Cendor imports or dependencies in this project — nothing to validate.",
                "Run `uvx cendor-init` to wire Cendor + your AI assistant in one step.",
            ),
        )
    elif not any(f.severity in ("error", "warn") for f in findings):
        findings.append(
            Finding(
                "ok",
                "Wiring looks good",
                f"Cendor usage found{f', instrument() called {count}×' if count else ''}; no problems detected.",
            )
        )

    exit_code = 1 if any(f.severity == "error" for f in findings) else 0
    return DoctorResult(findings=findings, exit_code=exit_code)


SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2, "ok": 3}
