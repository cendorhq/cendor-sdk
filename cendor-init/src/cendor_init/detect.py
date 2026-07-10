"""Detect the project shape, which assistants are configured, which providers / cendor packages exist."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

# PyPI provider distribution / import token -> normalized provider key.
PYPI_PROVIDERS: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google-genai": "google",
    "google-generativeai": "google",
    "google.genai": "google",
    "google_genai": "google",
    "boto3": "bedrock",
    "ollama": "ollama",
    "mistralai": "mistral",
    "cohere": "cohere",
    "huggingface_hub": "huggingface",
    "huggingface-hub": "huggingface",
}

# npm provider package -> normalized provider key (for the cross-ecosystem "used in .ts source" note).
NPM_PROVIDERS: dict[str, str] = {
    "openai": "openai",
    "@anthropic-ai/sdk": "anthropic",
    "@google/genai": "google",
    "@google/generative-ai": "google",
    "@aws-sdk/client-bedrock-runtime": "bedrock",
    "ollama": "ollama",
    "@mistralai/mistralai": "mistral",
    "cohere-ai": "cohere",
}

# The PyPI distribution that ships each provider SDK (for the "install X" fix hint).
PYPI_PKG_FOR_PROVIDER: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google-genai",
    "bedrock": "boto3",
    "ollama": "ollama",
    "mistral": "mistralai",
    "cohere": "cohere",
    "huggingface": "huggingface_hub",
}

# The cendor-sdk extra that pulls each provider (nicer fix hint than the bare package).
SDK_EXTRA_FOR_PROVIDER: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "bedrock": "bedrock",
    "ollama": "ollama",
    "huggingface": "huggingface",
}

CENDOR_DISTS = list(
    {
        "cendor-core",
        "cendor-tokenguard",
        "cendor-guardrails",
        "cendor-contextkit",
        "cendor-squeeze",
        "cendor-cassette",
        "cendor-acttrace",
        "cendor-libs",
        "cendor",
        "cendor-sdk",
    }
)

_PY_IMPORT_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:import|from)[ \t]+"
    r"(openai|anthropic|google\.genai|google_genai|boto3|ollama|mistralai|cohere|huggingface_hub)\b"
)
_NPM_IMPORT_RE = re.compile(
    r"""(?:from|require|import)\s*\(?\s*['"]"""
    r"(openai|@anthropic-ai/sdk|@google/genai|@google/generative-ai|"
    r"@aws-sdk/client-bedrock-runtime|ollama|@mistralai/mistralai|cohere-ai)['\"]"
)
# A dependency token like `cendor-core>=1.3,<2` or `openai` (quoted or bare) in pyproject/requirements.
_DEP_RE = re.compile(r"""["'\s]([A-Za-z][A-Za-z0-9._-]*)\s*((?:[<>=!~]=?|==)\s*[0-9][^"',\]]*)?""")


@dataclass
class Detected:
    root: Path
    ecosystem: str  # "node" | "python" | "unknown"
    node: bool
    python: bool
    declared_providers: set[str] = field(default_factory=set)
    assistants: list[str] = field(default_factory=list)
    # cendor-* -> version spec declared in pyproject/requirements.
    declared_pypi: dict[str, str] = field(default_factory=dict)
    # cendor-* -> installed version (importlib.metadata) if importable in THIS environment.
    installed_pypi: dict[str, str] = field(default_factory=dict)
    # @cendor/* -> range declared in package.json (cross-ecosystem hint).
    declared_npm: dict[str, str] = field(default_factory=dict)


def installed_version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # pragma: no cover - defensive
        return None


def _parse_py_deps(text: str) -> tuple[dict[str, str], set[str]]:
    cendor: dict[str, str] = {}
    providers: set[str] = set()
    for m in _DEP_RE.finditer(text):
        raw = (m.group(1) or "").lower()
        spec = re.sub(r"\s+", "", m.group(2) or "")
        base = raw.split("[")[0]  # strip extras like cendor-sdk[all]
        if base.startswith("cendor"):
            cendor[base] = spec or "*"
        if base in PYPI_PROVIDERS:
            providers.add(PYPI_PROVIDERS[base])
    return cendor, providers


def _detect_assistants(root: Path) -> list[str]:
    found: list[str] = []
    if (root / ".github").exists():
        found.append("copilot")
    if (root / ".cursor").exists():
        found.append("cursor")
    if (root / "AGENTS.md").exists():
        found.append("agents")
    if (root / "CLAUDE.md").exists():
        found.append("claude")
    if (root / ".windsurf").exists():
        found.append("windsurf")
    return found


def _pkg_deps(pkg: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        block = pkg.get(key)
        if isinstance(block, dict):
            out.update({str(k): str(v) for k, v in block.items()})
    return out


def detect_project(root: Path) -> Detected:
    root = Path(root)
    node = (root / "package.json").exists()
    python = (
        (root / "pyproject.toml").exists()
        or (root / "setup.py").exists()
        or (root / "setup.cfg").exists()
        or any(
            p.name.startswith("requirements") and p.suffix == ".txt"
            for p in root.glob("requirements*.txt")
        )
    )
    ecosystem = "node" if node else "python" if python else "unknown"

    declared_providers: set[str] = set()
    declared_pypi: dict[str, str] = {}
    declared_npm: dict[str, str] = {}

    if python:
        for fname in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg"):
            fpath = root / fname
            if not fpath.exists():
                continue
            cendor, provs = _parse_py_deps(fpath.read_text(encoding="utf-8", errors="ignore"))
            declared_pypi.update(cendor)
            declared_providers |= provs

    if node:
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8", errors="ignore"))
            for name, ver in _pkg_deps(pkg).items():
                if name in NPM_PROVIDERS:
                    declared_providers.add(NPM_PROVIDERS[name])
                if name.startswith("@cendor/"):
                    declared_npm[name] = ver
        except (json.JSONDecodeError, OSError):
            pass

    # Installed cendor versions from the CURRENT environment (accurate when installed into the
    # project venv; empty under `uvx` isolation, where we fall back to the declared pins above).
    installed_pypi = {d: v for d in CENDOR_DISTS if (v := installed_version(d)) is not None}

    return Detected(
        root=root,
        ecosystem=ecosystem,
        node=node,
        python=python,
        declared_providers=declared_providers,
        assistants=_detect_assistants(root),
        declared_pypi=declared_pypi,
        installed_pypi=installed_pypi,
        declared_npm=declared_npm,
    )


def providers_used_in_source(text: str, kind: str) -> set[str]:
    used: set[str] = set()
    if kind == "python":
        for m in _PY_IMPORT_RE.finditer(text):
            tok = m.group(1)
            key = PYPI_PROVIDERS.get(tok) or PYPI_PROVIDERS.get(tok.replace(".", "_"))
            if key:
                used.add(key)
    else:
        for m in _NPM_IMPORT_RE.finditer(text):
            key = NPM_PROVIDERS.get(m.group(1))
            if key:
                used.add(key)
    return used


def provider_installed(provider: str) -> bool:
    """Is the provider SDK importable in THIS environment?"""
    pkg = PYPI_PKG_FOR_PROVIDER.get(provider)
    return bool(pkg and installed_version(pkg) is not None)
