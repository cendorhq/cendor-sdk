"""`cendor.sdk.__version__` is derived from installed metadata and stays in sync with the manifest.

Regression guard: `__version__` was once hardcoded and drifted from `pyproject.toml`. Deriving
it from `importlib.metadata` makes pyproject the single source of truth.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import cendor.sdk


def test_version_derived_from_metadata():
    # __version__ must equal the installed distribution's metadata — never a hardcoded literal.
    assert cendor.sdk.__version__ == version("cendor-sdk")
    assert cendor.sdk.__version__ != "0.0.0+unknown"  # metadata actually resolved in this env


def test_version_matches_pyproject():
    # Installed metadata (hence __version__) must match the manifest's declared version.
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert cendor.sdk.__version__ == declared
