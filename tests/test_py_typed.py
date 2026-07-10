"""The SDK must ship a ``py.typed`` marker (PEP 561).

Without it, a consumer's type checker sees ``Any`` for ``cendor.sdk`` and the inline "Type Teach"
call-shape guidance (docstrings + the ``SQLiteSessionStore`` casing alias) delivers nothing. This
pins the marker into the wheel via ``[tool.hatch.build.targets.wheel] packages = ["src/cendor"]``.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]  # tests -> repo root


def test_sdk_ships_py_typed() -> None:
    marker = _REPO_ROOT / "src" / "cendor" / "sdk" / "py.typed"
    assert marker.is_file(), f"cendor-sdk is missing its py.typed marker at {marker}"
