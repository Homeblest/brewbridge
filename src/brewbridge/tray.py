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

from . import __version__
from .core import beersmith as bs

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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAST_SYNC_FILE.write_text(
        f"{dt.datetime.now().timestamp()}\n{'ok' if success else 'failed'}\n"
    )


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

    # Glyph centred on the circle. textbbox gives the actual painted extents
    # (cap-height, not the full em box), so centring on its midpoint puts the
    # visual mass of "B" at the disc's centre.
    font_px = int(size * 0.56)
    try:
        font = ImageFont.truetype("seguibl.ttf", font_px)
    except (OSError, IOError):
        font = ImageFont.load_default()
    cx = cy = size / 2
    l, t, r, b = d.textbbox((0, 0), "B", font=font)
    d.text((cx - (l + r) / 2, cy - (t + b) / 2), "B", fill="white", font=font)
    return img


# ---------------------------------------------------------------------------
# Background worker — runs sync/audit without blocking the tray
# ---------------------------------------------------------------------------

def _run_in_thread(target: Callable[[], None], on_done: Callable[[bool], None] | None = None):
    def wrap():
        ok = True
        try:
            target()
        except Exception:
            import traceback
            traceback.print_exc()
            ok = False
        if on_done:
            on_done(ok)
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
        subprocess.Popen([sys.executable, "-m", "brewbridge", "order", url],
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
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
    """Run a catalog sync in a background thread; refresh icon when done."""
    icon.notify("Brew.is sync started...", "brewbridge")
    def do_it():
        from .core import sync
        sync.run()
    def done(ok):
        _record_sync(ok)
        icon.icon = _make_icon_image(_sync_state())
        icon.notify("Brew.is sync complete." if ok else "Sync failed — check log.",
                    "brewbridge")
    _run_in_thread(do_it, done)


def _action_order(icon):
    threading.Thread(target=_open_picker, daemon=True).start()


def _action_audit(icon):
    def do_it():
        from .core import audit
        audit.run(fix=False)
    _run_in_thread(do_it)


def _action_open_folder(icon):
    import os
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.startfile(DATA_DIR)


def _action_quit(icon):
    icon.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    try:
        import pystray
        from pystray import MenuItem as Item, Menu
    except ImportError:
        print("pystray not installed — pip install pystray pillow", file=sys.stderr)
        sys.exit(1)

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
