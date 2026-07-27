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


def test_price_snapshot_staleness_warns(tmp_path: Path, monkeypatch):
    import importlib.metadata as md

    from cendor_init.detect import Detected
    from cendor_init.doctor import Finding, _check_price_snapshot

    snap = tmp_path / "prices.json"
    snap.write_text('{"_updated": "2020-01-01", "models": {}}', encoding="utf-8")

    class _File:
        name = "prices.json"

    class _Dist:
        files = [_File()]

        def locate_file(self, f):
            return snap

    monkeypatch.setattr(md, "distribution", lambda name: _Dist())
    detected = Detected(
        root=tmp_path,
        ecosystem="python",
        node=False,
        python=True,
        installed_pypi={"cendor-core": "1.5.1"},
    )
    out: list[Finding] = []
    _check_price_snapshot(detected, out)
    assert any("price snapshot" in f.title for f in out)
    assert all(f.severity == "warn" for f in out)  # a hint, never an error


def test_price_snapshot_fresh_is_silent(tmp_path: Path, monkeypatch):
    import importlib.metadata as md
    from datetime import date

    from cendor_init.detect import Detected
    from cendor_init.doctor import Finding, _check_price_snapshot

    snap = tmp_path / "prices.json"
    snap.write_text(
        f'{{"_updated": "{date.today().isoformat()}", "models": {{}}}}', encoding="utf-8"
    )

    class _File:
        name = "prices.json"

    class _Dist:
        files = [_File()]

        def locate_file(self, f):
            return snap

    monkeypatch.setattr(md, "distribution", lambda name: _Dist())
    detected = Detected(
        root=tmp_path,
        ecosystem="python",
        node=False,
        python=True,
        installed_pypi={"cendor-core": "1.5.1"},
    )
    out: list[Finding] = []
    _check_price_snapshot(detected, out)
    assert out == []


# --------------------------------------------------------------------------- telemetry (the switch)
# Since cendor-core 1.13 telemetry flows on its own, so the failure modes moved: not "you forgot to
# attach", but "you turned it off next to a configured provider" or "your Cendor is too old to emit".
# Neither warns at runtime (the emitters are deliberately silent), so doctor is where they surface.


def test_off_switch_next_to_a_configured_provider_is_flagged(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\ndependencies = ["cendor-core"]\n', encoding="utf-8"
    )
    (tmp_path / "app.py").write_text(
        "from opentelemetry import trace\n"
        "trace.set_tracer_provider(provider)\n"
        "# deployment note: CENDOR_TELEMETRY=off\n",
        encoding="utf-8",
    )
    result = run_doctor(tmp_path)
    titles = [f.title for f in result.findings]
    assert any("CENDOR_TELEMETRY=off" in t for t in titles)
    assert result.exit_code == 0, "a deliberate opt-out is a warning, never a CI failure"


def test_no_provider_means_no_telemetry_finding(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\ndependencies = ["cendor-core"]\n', encoding="utf-8"
    )
    (tmp_path / "app.py").write_text(
        "from cendor.core import instrument\nclient = instrument(OpenAI())\n", encoding="utf-8"
    )
    result = run_doctor(tmp_path)
    assert not any("CENDOR_TELEMETRY" in f.title for f in result.findings)


def test_a_handwritten_ts_otelsink_gets_the_ordering_note(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name":"app","dependencies":{"@cendor/sdk":"^0.22.0"}}', encoding="utf-8"
    )
    (tmp_path / "app.ts").write_text(
        "import { OTelSink } from '@cendor/tokenguard/sinks';\nuseSink(new OTelSink());\n",
        encoding="utf-8",
    )
    result = run_doctor(tmp_path)
    assert any("OTelSink" in f.title for f in result.findings)


# --------------------------------------------------------------------------- lockfile + --online
# W2.4 / W4.2. Two properties matter here and only one of them is about features:
#   1. a LOCKFILE that pins Cendor low is named as the cause (a wide range hides it entirely)
#   2. `doctor` with no flag makes ZERO network calls — the whole no-phone-home posture rests on it


def _stale_lock_project(root: Path) -> None:
    _write(
        root,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["cendor-core>=1.0,<2.0"]\n',
    )
    _write(
        root,
        "uv.lock",
        "version = 1\n\n"
        '[[package]]\nname = "cendor-core"\nversion = "1.0.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
    )


def test_lockfile_pinning_cendor_low_is_reported(tmp_path: Path):
    # The declared range (>=1.0,<2.0) is perfectly wide, so the range check sees nothing wrong.
    # The lock is what freezes the project — measured on Cendor's own cookbook, which sat 8 minors
    # behind on core with a green build the whole time.
    _stale_lock_project(tmp_path)
    warns = _titles(run_doctor(tmp_path), "warn")
    assert any("uv.lock" in t for t in warns), warns


def test_current_lockfile_is_silent(tmp_path: Path):
    # Green control: the same project with the lock on the snapshot's version says nothing.
    from cendor_init import versions

    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["cendor-core>=1.0,<2.0"]\n',
    )
    _write(
        tmp_path,
        "uv.lock",
        "version = 1\n\n"
        f'[[package]]\nname = "cendor-core"\nversion = "{versions.PYPI["cendor-core"]}"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
    )
    warns = _titles(run_doctor(tmp_path), "warn")
    assert not any("lock" in t.lower() for t in warns), warns


def test_doctor_makes_no_network_call_by_default(tmp_path: Path, monkeypatch):
    """The default path must not open a socket. Not "should not" — must not.

    Cendor's whole update posture is "we never check for updates at runtime, and the tooling only
    checks when you ask". A default that quietly reached the network would make that claim false, and
    the claim is the point. Poisoning socket.socket is the bluntest possible assertion: anything that
    tries to connect raises instead of succeeding slowly.
    """
    import socket

    def _boom(*_a, **_kw):  # pragma: no cover - the assertion is that this is never reached
        raise AssertionError("doctor opened a socket without --online")

    monkeypatch.setattr(socket, "socket", _boom)
    _stale_lock_project(tmp_path)
    run_doctor(tmp_path)  # must not raise


def test_online_uses_the_live_feed_and_degrades_when_unreachable(tmp_path: Path, monkeypatch):
    from cendor_init import doctor as doctor_mod
    from cendor_init import online as online_mod

    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["cendor-core==1.0.0"]\n',
    )

    # A live feed that reports a much newer core → the range check must use IT, not the snapshot.
    monkeypatch.setattr(
        online_mod,
        "fetch_releases",
        lambda *_a, **_kw: {
            "asOf": "2099-01-01",
            "libraries": [
                {
                    "name": "core",
                    "pypi": "cendor-core",
                    "pypiVer": "99.0.0",
                    "npm": "core",
                    "npmVer": "",
                }
            ],
            "sdk": [],
            "devtooling": [],
        },
    )
    res = run_doctor(tmp_path, online=True)
    behind = [f for f in res.findings if "behind" in f.title]
    assert behind, [f.title for f in res.findings]
    assert any("99.0.0" in loc for loc in (behind[0].locations or [])), behind[0].locations
    assert "2099-01-01" in behind[0].detail

    # An unreachable feed is an INFO, not a failure: no internet is not a wiring problem.
    def _fail(*_a, **_kw):
        raise online_mod.OnlineLookupError("could not reach the feed (test)")

    monkeypatch.setattr(online_mod, "fetch_releases", _fail)
    res2 = run_doctor(tmp_path, online=True)
    assert any("Could not reach the live release feed" in f.title for f in res2.findings)
    assert res2.exit_code == doctor_mod.run_doctor(tmp_path).exit_code, (
        "an unreachable feed must not change the exit code"
    )
