"""A bounded, dependency-free recursive source walk that skips the usual noise directories."""

from __future__ import annotations

from pathlib import Path

IGNORE_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "env",
    ".env",
    "dist",
    "build",
    "out",
    ".next",
    ".astro",
    ".nuxt",
    "coverage",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    ".tox",
    ".eggs",
    ".idea",
    ".cache",
}


def walk_source(root: Path, exts: tuple[str, ...], max_files: int = 4000) -> list[Path]:
    """Return paths under ``root`` whose suffix is in ``exts`` (e.g. ``('.py',)``)."""
    out: list[Path] = []
    stack: list[Path] = [Path(root)]
    while stack and len(out) < max_files:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in IGNORE_DIRS and not entry.name.endswith(".egg-info"):
                    stack.append(entry)
            elif entry.is_file() and entry.suffix in exts:
                out.append(entry)
                if len(out) >= max_files:
                    break
    return out


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
