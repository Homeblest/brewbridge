"""Tiny file-based exception logger for the tray.

The tray is a windowed PyInstaller bundle (``console=False`` in
brewbridge.spec), which means ``sys.stderr`` is connected to nothing —
``traceback.print_exc()`` writes into the void. Any unhandled exception
in a tray callback used to vanish without trace; the user would just see
the icon disappear with no way to diagnose what happened.

This module persists tracebacks to ``~/.brewbridge/tray.log`` so silent
crashes leave an artifact. Single function, no logging-config ceremony —
the tray only needs to write a few lines on rare failures.
"""
from __future__ import annotations

import datetime as dt
import traceback
from pathlib import Path

LOG_PATH = Path.home() / ".brewbridge" / "tray.log"


def log_exception(label: str) -> Path:
    """Append the current exception's traceback to ``tray.log``.

    Must be called from inside an ``except`` block — uses
    ``traceback.format_exc()`` which reads ``sys.exc_info()``.

    ``label`` is a short tag (e.g. ``"sync"``, ``"done-callback"``)
    that goes at the top of the entry so a reader can tell at a glance
    which code path failed.

    Returns the log file path so the caller can show it in a
    notification (``"Sync failed — see ~/.brewbridge/tray.log"``).

    Best-effort: if writing the log itself raises (disk full, permission
    error, etc.) we swallow that — the tray crashing because the log
    can't be written would defeat the entire point of logging in the
    first place.
    """
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n=== {stamp}  [{label}] ===\n")
            f.write(traceback.format_exc())
    except Exception:
        pass
    return LOG_PATH
