"""System-tray icon — the everyday entry point.

Right-click menu:

    ● Synca núna             (run brewbridge sync in a background thread)
    ● Panta uppskrift…       (open the picker dialog)
    ● Yfirfara uppskriftir   (open the latest audit report)
    ─────────────
    ● Sýna skráarmöppu       (open ~/.brewbridge/)
    ● Hætta

Icon color reflects state:
    🟢  green   — synced today, last sync OK
    🟡  yellow  — catalog stale (>24h) or never synced
    🔴  red     — last sync failed

The picker dialog is a thin Tk window listing every BeerSmith recipe. Pick
one, choose fill-vs-report, and it kicks off ``brewbridge order``.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

# Absolute imports throughout — tray.py is *also* a PyInstaller entry
# point (brewbridge-tray.exe). When PyInstaller freezes a script as the
# top-level entry, __package__ is empty and relative imports raise
# ImportError. Absolute imports work in both the frozen-entry case AND
# the case where tray.main is called as `brewbridge.tray.main()` from
# __main__.py's cmd_tray.
from brewbridge import __version__
from brewbridge import _tray_log
from brewbridge.core import beersmith as bs
from brewbridge.core import platform as bb_platform

# Where we stash logs and the "last sync" stamp file
DATA_DIR = Path.home() / ".brewbridge"
LAST_SYNC_FILE = DATA_DIR / "last_sync.txt"

# Stale-catalog threshold (hours since last successful sync)
STALE_AFTER_HOURS = 26


# ---------------------------------------------------------------------------
# State + icon drawing
# ---------------------------------------------------------------------------

def _sync_state() -> str:
    """Return 'ok' | 'stale' | 'failed' | 'never'."""
    if not LAST_SYNC_FILE.exists():
        return "never"
    try:
        content = LAST_SYNC_FILE.read_text().strip().split("\n", 1)
        ts = float(content[0])
        status = content[1] if len(content) > 1 else "ok"
    except Exception:
        return "never"
    if status == "failed":
        return "failed"
    age_h = (dt.datetime.now().timestamp() - ts) / 3600
    return "stale" if age_h > STALE_AFTER_HOURS else "ok"


def _record_sync(success: bool):
    """Delegate to sync's own state-write so CLI and tray syncs both
    keep last_sync.txt accurate. Kept as a thin wrapper so we don't
    have to touch every existing call site, and so the failure path
    (a sync.run that raised before its own _record_sync_state could
    fire) is also covered."""
    from brewbridge.core import sync
    sync._record_sync_state("ok" if success else "failed")


def _make_icon_image(state: str, size: int = 64):
    """Draw the brewbridge "B" icon at ``size`` px square with a state-coloured dot.

    Scales every pixel offset off ``size`` so the same logic produces both the
    runtime 64×64 tray icon and the larger PNG used in docs/screenshots/.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    bg = {"ok": (40, 167, 69), "stale": (240, 192, 64),
          "failed": (200, 0, 0), "never": (140, 140, 140)}.get(state, (140, 140, 140))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)         # 4 at 64 px
    stroke = max(1, size // 64)      # 1 at 64 px, 4 at 256 px
    d.ellipse([pad, pad, size - pad, size - pad],
              fill=bg, outline=(0, 0, 0, 80), width=stroke)

    # Glyph centred on the circle. We can't trust font.getbbox / textbbox
    # for this — fonts report an em-square bbox with left-side bearing that
    # the painted ink doesn't fill. Centring on that reported bbox puts the
    # *bbox* at the disc centre but leaves the visible ink off-centre by the
    # bearing offset. So instead: render the glyph to a temp canvas, crop to
    # the actual ink bbox, then paste centred. Now the ink itself is centred,
    # regardless of font metrics.
    font_px = int(size * 0.56)
    try:
        font = ImageFont.truetype("seguibl.ttf", font_px)
    except (OSError, IOError):
        font = ImageFont.load_default()
    pad = font_px
    temp = Image.new("RGBA", (font_px * 3, font_px * 3), (0, 0, 0, 0))
    ImageDraw.Draw(temp).text((pad, pad), "B", fill="white", font=font)
    ink_bbox = temp.getbbox()
    if ink_bbox is not None:
        glyph = temp.crop(ink_bbox)
        gw, gh = glyph.size
        img.paste(glyph, (int(size / 2 - gw / 2), int(size / 2 - gh / 2)), glyph)
    return img


# ---------------------------------------------------------------------------
# Background worker — runs sync/audit without blocking the tray
# ---------------------------------------------------------------------------

def _run_in_thread(target: Callable[[], None], on_done: Callable[[bool], None] | None = None,
                    label: str = "task"):
    """Run ``target`` in a daemon thread, then call ``on_done(ok)``.

    Exceptions are caught at *two* levels:

      1. Inside ``target`` — sync, audit, etc. Logged to
         ``~/.brewbridge/tray.log`` (the tray is a windowed PyInstaller
         bundle with no stderr; the old ``traceback.print_exc()`` wrote
         into the void and tracebacks vanished). ``on_done`` then runs
         with ``ok=False``.

      2. Inside ``on_done`` — the callback that updates the icon and
         fires the notification. Previously unhandled, which meant a
         buggy ``done(ok)`` killed the daemon thread *and* the tray
         process visually. Now isolated: if the callback raises, the
         exception goes to the same log file and the tray stays up.

    ``label`` is used to tag log entries so they're attributable to a
    specific callback (e.g. "sync", "audit").
    """
    def wrap():
        ok = True
        try:
            target()
        except Exception:
            _tray_log.log_exception(label)
            ok = False
        if on_done:
            try:
                on_done(ok)
            except Exception:
                # A failing on_done used to kill the tray silently. Log
                # and move on — the tray icon stays alive even if the
                # post-sync notification path is broken.
                _tray_log.log_exception(f"{label}/on_done")
    threading.Thread(target=wrap, daemon=True).start()


# ---------------------------------------------------------------------------
# Picker dialog (Tk)
# ---------------------------------------------------------------------------

def _open_picker():
    """Picker window listing every BeerSmith recipe. Runs in the tray
    process; uses Tk because pystray is event-loop-driven and this is the
    cleanest way to get a dialog up."""
    import sqlite3
    root = tk.Tk()
    root.title("Panta uppskrift frá brew.is")
    root.geometry("560x500")

    ttk.Label(root, text="Veldu uppskrift:", padding=10).pack(anchor="w")
    frame = ttk.Frame(root); frame.pack(fill="both", expand=True, padx=10)
    sb = ttk.Scrollbar(frame); sb.pack(side="right", fill="y")
    lb = tk.Listbox(frame, yscrollcommand=sb.set, font=("Segoe UI", 10),
                    activestyle="dotbox")
    lb.pack(side="left", fill="both", expand=True); sb.config(command=lb.yview)

    conn = bs.open_db()
    rows = list(conn.execute(
        "SELECT _PERMID_, F_R_NAME, F_R_FOLDER_NAME FROM M_RECIPE ORDER BY F_R_NAME"
    ))
    conn.close()
    for r in rows:
        folder = (r["F_R_FOLDER_NAME"] or "").strip("/").split("/")[0] or "—"
        lb.insert("end", f"  {r['F_R_NAME']}    [{folder}]")

    mode = tk.StringVar(value="fill")
    box = ttk.Frame(root, padding=(10, 8))
    ttk.Radiobutton(box, text="Fylla Uppskriftavélina (Chromium)",
                    variable=mode, value="fill").pack(side="left", padx=4)
    ttk.Radiobutton(box, text="Sýna innkaupalista (HTML)",
                    variable=mode, value="report").pack(side="left", padx=4)
    box.pack(fill="x")

    def go():
        sel = lb.curselection()
        if not sel:
            return
        rid = rows[sel[0]]["_PERMID_"]
        url = f"brewis://order/{rid}" + ("/cart" if mode.get() == "fill" else "")
        # Frozen build vs source install need different subprocess shapes.
        #
        # In a frozen tray, sys.executable is brewbridge-tray.exe — passing
        # it `-m brewbridge order <url>` doesn't work because the tray
        # entry script ignores argv and just calls tray.main(), which
        # opens *another* tray icon. That was producing two symptoms at
        # once: (a) duplicate tray icons stacking after each picker use,
        # and (b) "Chrome doesn't open when I pick a recipe" — the order
        # command never actually ran.
        #
        # Fix: detect frozen mode and invoke the sibling brewbridge.exe
        # directly with the URL as its sole arg. __main__.main routes
        # bare brewis:// URLs straight to the order subcommand, same as
        # the Windows URL handler does.
        if getattr(sys, "frozen", False):
            cli = Path(sys.executable).parent / "brewbridge.exe"
            cmd = [str(cli), url]
        else:
            cmd = [sys.executable, "-m", "brewbridge", "order", url]
        subprocess.Popen(cmd,
                         creationflags=bb_platform.detached_console_flag())
        root.destroy()

    bar = ttk.Frame(root, padding=10)
    ttk.Button(bar, text="Panta", command=go, default="active").pack(side="right")
    ttk.Button(bar, text="Hætta við", command=root.destroy).pack(side="right", padx=6)
    bar.pack(fill="x")
    lb.bind("<Double-1>", lambda e: go())
    root.bind("<Return>", lambda e: go())
    lb.focus_set()
    root.mainloop()


# ---------------------------------------------------------------------------
# Tray menu actions
# ---------------------------------------------------------------------------

def _action_sync(icon):
    """Run a catalog sync in a background thread; refresh icon when done.

    On success: if any recipes flipped from blocked → orderable, fire a
    notification naming them (truncated to the first 3 with a "+N" tail
    when longer). If nothing flipped, generic "complete" toast.
    """
    icon.notify("Brew.is sync started...", "brewbridge")
    # The sync result needs to survive across threads — _run_in_thread
    # passes `ok` (bool) to the done callback but not the return value.
    # Stash it in a dict for the closure.
    result_holder: dict = {}

    def do_it():
        from brewbridge.core import sync
        result_holder["res"] = sync.run()

    def done(ok):
        _record_sync(ok)
        icon.icon = _make_icon_image(_sync_state())
        if not ok:
            # The underlying traceback is already in tray.log (see
            # _run_in_thread). Point the user at it so they can paste
            # it back to a maintainer.
            icon.notify(
                f"Sync failed — see {_tray_log.LOG_PATH}",
                "brewbridge",
            )
            return
        res = result_holder.get("res")
        if res is not None and res.unlocked:
            # Top-3 names plus overflow indicator. pystray's notify body
            # has limited width on every platform; keep it scannable.
            names = ", ".join(name for _, name in res.unlocked[:3])
            extra = len(res.unlocked) - 3
            tail = f" (+{extra})" if extra > 0 else ""
            icon.notify(
                f"Nýjar uppskriftir tilbúnar í pöntun: {names}{tail}",
                "brew.is sync — pantanir mögulegar",
            )
        else:
            icon.notify("Brew.is sync complete.", "brewbridge")

    _run_in_thread(do_it, done, label="sync")


def _action_order(icon):
    threading.Thread(target=_open_picker, daemon=True).start()


def _action_audit(icon):
    def do_it():
        from brewbridge.core import audit
        audit.run(fix=False)
    _run_in_thread(do_it, label="audit")


def _action_open_folder(icon):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bb_platform.open_path(DATA_DIR)


def _action_quit(icon):
    icon.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _acquire_singleton():
    """Make sure only one brewbridge tray runs at a time.

    Windows: use a named kernel mutex. CreateMutexW returns a handle and
    sets ERROR_ALREADY_EXISTS (183) on the second caller. If we get
    that, another tray instance is alive — bail. Otherwise hold the
    handle for the process lifetime so the kernel keeps the mutex
    alive on our behalf (closing the handle would release it).

    macOS / Linux: no-op for now. mac singleton would use NSRunningApplication
    or a lockfile; we'll wire it when the .app path is verified on real
    hardware. (See task #29.)

    Returns the mutex handle on success (caller must keep the reference),
    or ``None`` if another instance owns it.
    """
    if not bb_platform.is_windows():
        return True   # truthy sentinel — no real handle on non-Windows

    import ctypes
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    # Use a fixed, well-known name. Don't prefix Global\\ — that would
    # require admin and apply across user sessions, which we don't want.
    # Per-user (default Local namespace) is correct.
    h = kernel32.CreateMutexW(None, False, "brewbridge_tray_singleton_v1")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return h


def main():
    try:
        import pystray
        from pystray import MenuItem as Item, Menu
    except ImportError:
        print("pystray not installed — pip install pystray pillow", file=sys.stderr)
        sys.exit(1)

    # Singleton: refuse to launch a second tray. The picker's subprocess
    # bug used to spawn duplicate trays as a side-effect; that's fixed,
    # but this catches the human case (double-clicking the Start Menu
    # shortcut, or a leftover tray + a fresh launch) too.
    _mutex_handle = _acquire_singleton()
    if _mutex_handle is None:
        # Use the file logger so this trail exists in tray.log if a user
        # ever wonders why their tray "didn't open".
        try:
            raise RuntimeError("brewbridge tray already running")
        except RuntimeError:
            _tray_log.log_exception("singleton")
        sys.exit(0)

    icon = pystray.Icon(
        "brewbridge",
        icon=_make_icon_image(_sync_state()),
        title=f"brewbridge {__version__}",
        menu=Menu(
            Item("Synca núna", _action_sync),
            Item("Panta uppskrift…", _action_order),
            Item("Yfirfara uppskriftir", _action_audit),
            Menu.SEPARATOR,
            Item("Sýna skráarmöppu", _action_open_folder),
            Menu.SEPARATOR,
            Item("Hætta", _action_quit),
        ),
    )
    icon.run()


if __name__ == "__main__":
    main()
