"""Cross-platform helpers — paths, "open this in the OS", process checks.

Everything that varies between Windows and macOS funnels through here so
the rest of the codebase can stay platform-agnostic. Add a new platform
by extending the branches in :func:`_branch` and the path resolvers.

Linux is *not* a target — BeerSmith 4 doesn't ship for Linux — but a
Linux branch is included for development convenience (running the test
suite, etc) and so import-time path resolution doesn't crash for someone
poking at the code from WSL.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Literal


PlatformName = Literal["windows", "macos", "linux"]


def current_platform() -> PlatformName:
    """Return a normalized platform name. Centralised so we test it in one
    place instead of sprinkling ``sys.platform`` checks across modules."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def is_windows() -> bool:
    return current_platform() == "windows"


def is_macos() -> bool:
    return current_platform() == "macos"


# ---------------------------------------------------------------------------
# BeerSmith paths
# ---------------------------------------------------------------------------
# BeerSmith 4 stores its per-user data in a platform-specific location. The
# schema and filenames (BeerSmith.sqlite, Reports/, etc) are identical
# across Windows and macOS — only the parent directory changes.
#
# References:
#   * Windows: %APPDATA%\BeerSmith4\  (verified empirically from BeerSmith 4
#     installs).
#   * macOS:   ~/Library/Application Support/BeerSmith4/  (documented in
#     BeerSmith help; pattern matches every other macOS app's user data
#     conventions).


def beersmith_data_dir() -> Path:
    """Directory containing ``BeerSmith.sqlite`` and the ``Reports`` folder."""
    if is_windows():
        return Path(os.path.expandvars(r"%APPDATA%\BeerSmith4"))
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "BeerSmith4"
    # Linux convenience — BeerSmith doesn't run here but tests + tooling
    # might want a stable answer instead of an exception.
    return Path.home() / ".config" / "BeerSmith4"


def beersmith_db_path() -> Path:
    return beersmith_data_dir() / "BeerSmith.sqlite"


def beersmith_reports_dir() -> Path:
    return beersmith_data_dir() / "Reports"


def beersmith_exe_path() -> Path | None:
    """Best-guess path to the BeerSmith executable.

    Used by ``is_running()`` to look up the process and by the open-bsmx
    flow as a fallback when file association handling fails. Returns
    ``None`` on Linux (BeerSmith doesn't run there)."""
    if is_windows():
        # 64-bit installs land in `Program Files`; 32-bit/older in `(x86)`.
        # Check x86 first because BeerSmith 4 still ships as a 32-bit Win32
        # app at the time of writing.
        candidates = [
            Path(r"C:\Program Files (x86)\BeerSmith4\BeerSmith4.exe"),
            Path(r"C:\Program Files\BeerSmith4\BeerSmith4.exe"),
        ]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]  # default expected path even if not installed
    if is_macos():
        # macOS apps live as .app bundles in /Applications/. BeerSmith 4
        # ships as `BeerSmith 4.app` (space + version suffix). The "exe"
        # we'd want for `open -a` is the bundle itself.
        return Path("/Applications/BeerSmith 4.app")
    return None


# ---------------------------------------------------------------------------
# Open something in the OS
# ---------------------------------------------------------------------------

def open_path(path: Path | str) -> None:
    """Open a file or directory using the OS's default handler.

    On Windows we use ``os.startfile`` which respects file associations
    (clicking a ``.bsmx`` triggers BeerSmith, clicking a folder opens
    Explorer, clicking an HTML opens the default browser). On macOS we
    shell out to ``open`` which does the equivalent via Launch Services.
    On Linux ``xdg-open`` is the standard fallback.

    Raises :class:`OSError` if the underlying call fails, so callers can
    distinguish "OS doesn't have a handler" from "wrong path"."""
    p = str(path)
    plat = current_platform()
    if plat == "windows":
        # mypy: os.startfile only exists on win32; runtime branch makes
        # this safe but the type checker can't see that. The getattr
        # avoids a static-analysis complaint on non-win32 development
        # machines.
        getattr(os, "startfile")(p)
    elif plat == "macos":
        subprocess.run(["open", p], check=True)
    else:
        subprocess.run(["xdg-open", p], check=True)


# ---------------------------------------------------------------------------
# Subprocess flags
# ---------------------------------------------------------------------------

def detached_console_flag() -> int:
    """Return the subprocess ``creationflags`` value for spawning a child
    in a detached console.

    On Windows: ``CREATE_NEW_CONSOLE``. On other platforms: 0.
    Used when we explicitly want the child to have a visible console
    (rare — most callers want :func:`no_window_flag` instead)."""
    if is_windows():
        return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return 0


def no_window_flag() -> int:
    """Return the subprocess ``creationflags`` value for spawning a child
    that should NOT create a console window.

    On Windows: ``CREATE_NO_WINDOW`` (0x08000000). On other platforms: 0.

    Use for short-lived system tools like ``tasklist`` / ``schtasks`` /
    ``reg`` that brewbridge calls programmatically. Without this flag,
    every invocation flashes a cmd window briefly — visible during e.g.
    a tray sync because ``bs.is_running()`` shells out to ``tasklist``
    on the main thread before spawning the background sync."""
    if is_windows():
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0
