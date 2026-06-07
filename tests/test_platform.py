"""Tests for ``brewbridge.core.platform`` — the OS-abstraction layer.

These tests are the *only* thing in the codebase that can be verified
without a Mac, so they cover all three branches (windows / macos / linux)
by monkey-patching ``sys.platform``. They exist specifically so a future
refactor doesn't silently break path resolution on a platform the dev
machine doesn't run.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from brewbridge.core import platform as P


# ---------------------------------------------------------------------------
# current_platform / is_X
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sys_plat, expected", [
    ("win32", "windows"),
    ("darwin", "macos"),
    ("linux", "linux"),
    ("linux2", "linux"),         # older Python on Linux
    ("freebsd", "linux"),        # bucket BSD as linux for our purposes
])
def test_current_platform(sys_plat, expected):
    with mock.patch.object(sys, "platform", sys_plat):
        assert P.current_platform() == expected


def test_is_windows_and_is_macos_are_exclusive():
    with mock.patch.object(sys, "platform", "win32"):
        assert P.is_windows() and not P.is_macos()
    with mock.patch.object(sys, "platform", "darwin"):
        assert P.is_macos() and not P.is_windows()
    with mock.patch.object(sys, "platform", "linux"):
        assert not P.is_macos() and not P.is_windows()


# ---------------------------------------------------------------------------
# BeerSmith paths
# ---------------------------------------------------------------------------

def test_beersmith_data_dir_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\testuser\AppData\Roaming")
    d = P.beersmith_data_dir()
    # Use string comparison — Path semantics differ between PurePath and
    # the runtime Path class on a non-Windows test host.
    assert str(d).replace("/", "\\").endswith(r"AppData\Roaming\BeerSmith4")


def test_beersmith_data_dir_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    # On non-mac hosts Path.home() reads $HOME directly on POSIX-ish
    # systems. On a Windows dev host HOME isn't typically set, so we
    # also patch the expanduser pathway to be safe.
    with mock.patch.object(Path, "home", return_value=tmp_path):
        d = P.beersmith_data_dir()
    assert d == tmp_path / "Library" / "Application Support" / "BeerSmith4"


def test_beersmith_data_dir_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    with mock.patch.object(Path, "home", return_value=tmp_path):
        d = P.beersmith_data_dir()
    assert d == tmp_path / ".config" / "BeerSmith4"


def test_beersmith_db_path_descends_from_data_dir(monkeypatch, tmp_path):
    """Pin the contract that db_path and reports_dir agree with data_dir.
    If someone later hardcodes a different path in one but not the other,
    this test catches the drift."""
    monkeypatch.setattr(sys, "platform", "darwin")
    with mock.patch.object(Path, "home", return_value=tmp_path):
        data = P.beersmith_data_dir()
        assert P.beersmith_db_path() == data / "BeerSmith.sqlite"
        assert P.beersmith_reports_dir() == data / "Reports"


def test_beersmith_exe_path_linux_is_none(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert P.beersmith_exe_path() is None


def test_beersmith_exe_path_macos_points_at_app_bundle(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    exe = P.beersmith_exe_path()
    assert exe is not None
    # Whether the bundle actually exists on the test host is irrelevant;
    # we just want the right shape (a .app under /Applications).
    assert str(exe).endswith(".app")
    assert "/Applications/" in str(exe).replace("\\", "/")


# ---------------------------------------------------------------------------
# open_path — dispatches to the right OS handler
# ---------------------------------------------------------------------------

def test_open_path_macos_calls_open(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with mock.patch("brewbridge.core.platform.subprocess.run") as run:
        P.open_path("/tmp/foo")
        run.assert_called_once_with(["open", "/tmp/foo"], check=True)


def test_open_path_linux_calls_xdg_open(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with mock.patch("brewbridge.core.platform.subprocess.run") as run:
        P.open_path("/tmp/foo")
        run.assert_called_once_with(["xdg-open", "/tmp/foo"], check=True)


def test_open_path_windows_calls_startfile(monkeypatch):
    """On non-Windows hosts os.startfile doesn't exist, so we attach a
    stand-in via setattr just for the duration of the test."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake_startfile = mock.MagicMock()
    monkeypatch.setattr(P.os, "startfile", fake_startfile, raising=False)
    P.open_path(r"C:\Users\foo\bar.bsmx")
    fake_startfile.assert_called_once_with(r"C:\Users\foo\bar.bsmx")


def test_open_path_accepts_pathlib(monkeypatch):
    """Callers pass either str or Path; both must work."""
    monkeypatch.setattr(sys, "platform", "darwin")
    with mock.patch("brewbridge.core.platform.subprocess.run") as run:
        P.open_path(Path("/tmp/foo"))
        # subprocess.run gets a string, not a Path — open(1) is fine with
        # strings but the contract matters for cross-arg consistency.
        args, _ = run.call_args
        assert args[0][1] == str(Path("/tmp/foo"))


# ---------------------------------------------------------------------------
# detached_console_flag
# ---------------------------------------------------------------------------

def test_detached_console_flag_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    import subprocess as sp
    # CREATE_NEW_CONSOLE is 0x00000010 on Windows.
    # On non-Windows hosts subprocess doesn't define the attr, so we
    # inject a value to confirm the lookup path.
    monkeypatch.setattr(sp, "CREATE_NEW_CONSOLE", 0x10, raising=False)
    assert P.detached_console_flag() == 0x10


def test_detached_console_flag_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert P.detached_console_flag() == 0


def test_detached_console_flag_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert P.detached_console_flag() == 0
