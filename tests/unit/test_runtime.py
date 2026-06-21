from __future__ import annotations

import pytest

from ytdl.runtime import BinaryStatus, probe_binary, probe_deno, probe_ffmpeg


def test_probe_binary_present_for_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """python3 is on PATH on every test runner."""
    status = probe_binary("python3")
    assert isinstance(status, BinaryStatus)
    assert status.name == "python3"
    assert status.present is True
    assert status.path is not None


def test_probe_binary_absent_for_nonsense() -> None:
    status = probe_binary("definitely-not-a-real-binary-name-9999")
    assert status.present is False
    assert status.path is None


def test_probe_deno_uses_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deno-specific helper should defer to shutil.which so the same
    PATH semantics apply as for any other binary."""
    import shutil

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/local/bin/deno" if name == "deno" else None,
    )
    status = probe_deno()
    assert status.name == "deno"
    assert status.present is True
    assert status.path == "/usr/local/bin/deno"


def test_probe_deno_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    status = probe_deno()
    assert status.present is False
    assert status.path is None


def test_probe_ffmpeg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    status = probe_ffmpeg()
    assert status.name == "ffmpeg"
    assert status.present is False
