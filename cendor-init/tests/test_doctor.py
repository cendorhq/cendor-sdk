from pathlib import Path

from cendor_init.doctor import run_doctor


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _titles(result, sev: str) -> list[str]:
    return [f.title for f in result.findings if f.severity == sev]


def test_missing_provider_is_error_exit_nonzero(tmp_path: Path):
    # cohere is not installed in the isolated test env and not declared → hard error.
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname="x"\nversion="0.1.0"\ndependencies=["cendor-core>=1.5"]\n',
    )
    _write(
        tmp_path,
        "app.py",
        "import cohere\nfrom cendor.core import instrument\nc = instrument(object())\n",
    )
    r = run_doctor(tmp_path)
    assert r.exit_code == 1
    assert any("cohere" in t for t in _titles(r, "error"))


def test_stray_init_is_error(tmp_path: Path):
    _write(tmp_path, "src/cendor/__init__.py", "# oops\n")
    _write(tmp_path, "src/cendor/tool/__init__.py", "")
    r = run_doctor(tmp_path)
    assert r.exit_code == 1
    assert any("__init__.py" in t for t in _titles(r, "error"))


def test_bare_import_and_money_are_warnings_exit_zero(tmp_path: Path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname="x"\nversion="0.1.0"\ndependencies=["cendor-core>=1.5"]\n',
    )
    _write(
        tmp_path,
        "app.py",
        "import cendor\nfrom cendor.core import instrument\nprice = 0.01\nx = float(price)\n",
    )
    r = run_doctor(tmp_path)
    assert r.exit_code == 0
    warns = _titles(r, "warn")
    assert any("import cendor" in t for t in warns)
    assert any("float" in t for t in warns)


def test_warns_when_imported_but_not_instrumented(tmp_path: Path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname="x"\nversion="0.1.0"\ndependencies=["cendor-tokenguard>=1.1"]\n',
    )
    _write(tmp_path, "app.py", "from cendor.tokenguard import budget\n")
    r = run_doctor(tmp_path)
    assert any("instrument" in t for t in _titles(r, "warn"))


def test_no_usage_exits_zero(tmp_path: Path):
    r = run_doctor(tmp_path)
    assert r.exit_code == 0
    assert any("No Cendor usage" in t for t in _titles(r, "info"))


def test_clean_project_exits_zero(tmp_path: Path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname="x"\nversion="0.1.0"\ndependencies=["cendor-core>=1.5","openai>=1.0"]\n',
    )
    _write(
        tmp_path,
        "app.py",
        "from cendor.core import instrument\nfrom openai import OpenAI\nc = instrument(OpenAI())\n",
    )
    r = run_doctor(tmp_path)
    assert r.exit_code == 0
    assert _titles(r, "error") == []
    assert any(f.severity == "ok" for f in r.findings)
