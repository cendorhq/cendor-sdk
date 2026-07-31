"""Q1 — ``Checkpointer.save()`` must survive a transient Windows sharing violation WITHOUT giving up
the crash-atomicity the temp file exists for.

MEASURED MECHANISM (``plan/evidence-gapclose-2026-07-31/q1_probe_python_twin.py``, Windows 11 /
CPython 3.13): ``Path.replace`` is ``MoveFileExW(MOVEFILE_REPLACE_EXISTING)``, which needs exclusive
access to the DESTINATION. **8 of 500** replaces over an existing file raised ``PermissionError``
(errno 13 / winerror 5) — the OS had a handle open on the file just created — and it is
deterministic while a handle is held. The analysis had recorded Python as unaffected; it is not, and
that measurement is why this file exists alongside the TypeScript twin.

Every "now works" claim is paired with a negative control: a retry loop that retries *everything*
would turn a full disk into a 62 ms pause and the same failure, and would hide a real bug.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from cendor.sdk.checkpoint import Checkpointer, _atomic_replace


def _oserror(err: int, winerror: int | None = None) -> OSError:
    """An OSError shaped like the real one, so the classification under test is the real one."""
    exc = OSError(err, "simulated")
    if winerror is not None:
        exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


class _Counter:
    attempts = 0


def _flaky_replace(exc: OSError, fail_times: int, counter: _Counter):
    """A stand-in for ``Path.replace`` that fails `fail_times` times, then does the real move.

    Deliberately a plain function, not a callable object: ``monkeypatch.setattr`` installs a CLASS
    attribute, and only a function is a descriptor — a callable instance would be handed the
    argument list unbound and lose ``self``.
    """

    def replace(this: Path, target: Path) -> None:
        counter.attempts += 1
        if counter.attempts <= fail_times:
            raise exc
        target.write_bytes(this.read_bytes())

    return replace


def test_transient_sharing_violation_is_retried_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "run.ckpt.json"
    tmp = target.with_suffix(".json.tmp")
    target.write_text('{"gen": 1}', encoding="utf-8")
    tmp.write_text('{"gen": 2}', encoding="utf-8")

    counter = _Counter()
    monkeypatch.setattr(
        Path, "replace", _flaky_replace(_oserror(errno.EACCES, winerror=5), 3, counter)
    )

    _atomic_replace(tmp, target)

    assert counter.attempts == 4  # 1 failed + 3 retries consumed, then through
    assert json.loads(target.read_text(encoding="utf-8")) == {"gen": 2}


@pytest.mark.parametrize("err", [errno.EACCES, errno.EPERM, errno.EBUSY])
def test_every_sharing_violation_errno_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    target = tmp_path / "run.ckpt.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text('{"ok": true}', encoding="utf-8")

    counter = _Counter()
    monkeypatch.setattr(Path, "replace", _flaky_replace(_oserror(err), 1, counter))

    _atomic_replace(tmp, target)
    assert counter.attempts == 2


# --- NEGATIVE CONTROL 1: a permanent error must NOT be retried at all. ----------------------------
@pytest.mark.parametrize("err", [errno.ENOENT, errno.ENOSPC, errno.EROFS, errno.EXDEV])
def test_permanent_error_raises_on_the_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    target = tmp_path / "run.ckpt.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text('{"x": 1}', encoding="utf-8")

    attempts = 0

    def always_fail(this: Path, other: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise _oserror(err)

    monkeypatch.setattr(Path, "replace", always_fail)

    with pytest.raises(OSError) as caught:
        _atomic_replace(tmp, target)
    assert caught.value.errno == err
    assert attempts == 1, f"errno {err} must not be retried"


# --- NEGATIVE CONTROL 2: the budget is bounded, and the original error survives. ------------------
def test_unrelenting_violation_gives_up_after_a_bounded_number_of_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "run.ckpt.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text('{"x": 1}', encoding="utf-8")

    attempts = 0

    def always_busy(this: Path, other: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise _oserror(errno.EACCES, winerror=5)

    monkeypatch.setattr(Path, "replace", always_busy)

    with pytest.raises(OSError) as caught:
        _atomic_replace(tmp, target)
    assert caught.value.errno == errno.EACCES
    assert attempts == 6  # 1 + 5 retries — a ceiling, never an unbounded loop


# --- NEGATIVE CONTROL 3: the guarantee `unlink`-then-`replace` would have broken. -----------------
def test_failed_replace_leaves_the_previous_checkpoint_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "run.ckpt.json"
    tmp = target.with_suffix(".json.tmp")
    target.write_text('{"generation": "previous-good"}', encoding="utf-8")
    tmp.write_text('{"generation": "new"}', encoding="utf-8")

    def no_space(this: Path, other: Path) -> None:
        raise _oserror(errno.ENOSPC)

    monkeypatch.setattr(Path, "replace", no_space)

    with pytest.raises(OSError):
        _atomic_replace(tmp, target)

    # The measured alternative (unlink(dest) then replace) leaves NO file here at all.
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": "previous-good"}


def test_failed_replace_leaves_no_stale_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "run.ckpt.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text('{"x": 1}', encoding="utf-8")

    def read_only(this: Path, other: Path) -> None:
        raise _oserror(errno.EROFS)

    monkeypatch.setattr(Path, "replace", read_only)

    with pytest.raises(OSError):
        _atomic_replace(tmp, target)
    assert not tmp.exists()


# --- End-to-end through the real filesystem: the wiring, not just the helper. ---------------------
def test_save_overwrites_repeatedly_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "run.ckpt.json"
    ckpt = Checkpointer(str(path))
    for i in range(40):
        ckpt.save({"run_id": "r1", "messages": [], "done": False, "seg": i})

    state = ckpt.load()
    assert state is not None
    assert state["seg"] == 39
    assert not path.with_suffix(".json.tmp").exists()
