"""BeerSmith.sqlite read/write layer.

Encapsulates the format quirks discovered the hard way while building brewbridge:

* **Compact JSON, no spaces.** BeerSmith's serializer omits whitespace; its
  parser silently drops embedded mash steps if they're stored with default
  json.dumps spacing.
* **Unescaped inner quotes inside the F_R_MASH `steps` field.** BeerSmith stores
  ``"steps":"[{"_Schema_":"7432",...}]"`` — technically invalid standard JSON,
  but the only layout BeerSmith's parser accepts as authoritative.
* **All embedded ingredient values as strings.** Even numerics: ``"152.6000000"``,
  not 152.6.
* **Weights in ounces, volumes in fluid ounces** (US units, internal storage).
* **Dates: unix int on first write, ``YYYY-MM-DD`` string after BeerSmith
  re-saves.** Read both formats; always write unix ints.
* **Mash profile name must match an M_MASH library row** or BeerSmith reports
  "no profile / no steps" regardless of what's embedded.
* **Yeast library re-binds on recipe open** — library `F_Y_PKG_DATE` etc. flow
  back into recipes, so the library entries must be kept fresh, not just the
  embedded copies inside recipes.

This module exposes the constants and helpers everything else builds on.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3

from . import platform as bb_platform
import subprocess
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants — schema codes, units, and BeerSmith's identifying strings
# ---------------------------------------------------------------------------

# BeerSmith stores compound objects with a "_Schema_" field identifying the
# type. These are the codes used inside a recipe's embedded Ingredients array
# and elsewhere.
SCHEMA = {
    "grain":      "7406",
    "hops":       "7403",
    "yeast":      "7426",
    "misc":       "7421",
    "style":      "7428",
    "equipment":  "7430",
    "mash":       "7434",
    "mash_step":  "7432",
    "carb":       "7478",
    "age":        "7482",
}

# Library tables that hold ingredient masters and the columns that name them
LIBRARY_TABLE = {
    "grain": ("M_GRAIN", "F_G_NAME", "F_G_NOTES"),
    "hops":  ("M_HOPS",  "F_H_NAME", "F_H_NOTES"),
    "yeast": ("M_YEAST", "F_Y_NAME", "F_Y_NOTES"),
    "misc":  ("M_MISC",  "F_M_NAME", "F_M_NOTES"),
}

# Unit conversions (BeerSmith stores weights in oz, volumes in fl oz)
OZ_PER_KG  = 35.2739619       # kg  -> oz
LB_PER_KG  = 2.20462262       # kg  -> lb (for strike-water ratio math only)
OZ_PER_G   = 0.0352739619     # g   -> oz
FLOZ_PER_L = 33.8140227       # L   -> US fl oz

# Marker suffix that identifies rows we manage. Anything not ending in this
# is treated as a built-in or user-edited library entry that brewbridge won't
# touch.
TAG = " (brew.is)"

# Default BeerSmith install + data paths.
# Resolved through ``platform`` so they're correct on whatever OS we run on.
# We expose them as module-level Paths (not functions) so the long-existing
# call sites elsewhere in the codebase keep working as drop-in defaults.
DEFAULT_DB_PATH = bb_platform.beersmith_db_path()
DEFAULT_EXE_PATH = bb_platform.beersmith_exe_path()
DEFAULT_REPORTS_DIR = bb_platform.beersmith_reports_dir()


# ---------------------------------------------------------------------------
# Process detection and database safety
# ---------------------------------------------------------------------------

def is_running() -> bool:
    """Return True if BeerSmith is currently running.

    Writing to BeerSmith.sqlite while BeerSmith is open is unsafe — BeerSmith
    holds the recipes in memory and rewrites the file on its own save cycle,
    which can clobber whatever we wrote.

    Windows: shell out to ``tasklist`` and look for ``beersmith`` in the
    process list (case-insensitive). macOS: ``pgrep -i beersmith`` returns
    0 on match. On either, an exception from the lookup means we don't
    know — return False and let the caller decide whether to proceed.
    """
    try:
        if bb_platform.is_windows():
            # creationflags=no_window_flag() suppresses the cmd window
            # that would otherwise flash for every invocation. The tray
            # calls is_running() twice per Synca núna click (once as a
            # pre-flight check, once inside sync.run()), so this flag
            # turns "two visible flashes per sync" into zero.
            out = subprocess.run(
                ["tasklist"],
                capture_output=True, text=True, timeout=20,
                creationflags=bb_platform.no_window_flag(),
            ).stdout.lower()
            return "beersmith" in out
        # macOS + Linux: pgrep -i is the portable POSIX path. Exit code 0
        # means at least one matching process; 1 means none; anything else
        # is an unexpected error which we treat as "unknown" = False.
        rc = subprocess.run(
            ["pgrep", "-i", "beersmith"], capture_output=True, timeout=20
        ).returncode
        return rc == 0
    except Exception:
        return False  # can't tell — caller decides whether to proceed


def backup_db(db_path: Path = DEFAULT_DB_PATH, backup_dir: Path | None = None,
              keep: int = 14) -> Path:
    """Copy BeerSmith.sqlite to a timestamped backup file. Trims oldest beyond
    ``keep``. Always run before any write."""
    backup_dir = backup_dir or db_path.parent / "brewbridge-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"BeerSmith_{stamp}.sqlite"
    shutil.copy2(db_path, dest)
    snapshots = sorted(p for p in backup_dir.iterdir() if p.suffix == ".sqlite")
    for old in snapshots[:-keep]:
        old.unlink()
    return dest


def open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open BeerSmith.sqlite with Row factory set (so columns are accessible by
    name) — the only style this codebase uses."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Value formatting — BeerSmith stores embedded values as strings
# ---------------------------------------------------------------------------

def fmt(v: Any) -> str:
    """Stringify a Python value the way BeerSmith writes it inside embedded
    JSON objects (grains, hops, mash steps, style, equipment...)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:.7f}"
    if isinstance(v, int):
        return str(v)
    if v is None:
        return ""
    return str(v)


def parse_date_field(v: Any) -> int:
    """BeerSmith stores dates as either unix-int-string (``"1748390400"``) on
    first write or ``"YYYY-MM-DD"`` after it re-saves. Return a unix
    timestamp from either, or 0 if unparseable."""
    if not v:
        return 0
    s = str(v).strip()
    try:
        return int(s)
    except ValueError:
        pass
    for fmt_ in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(dt.datetime.strptime(s, fmt_).timestamp())
        except ValueError:
            continue
    return 0


# ---------------------------------------------------------------------------
# Compact-JSON serialization (matches BeerSmith's native style)
# ---------------------------------------------------------------------------

def compact_json(obj: Any) -> str:
    """``json.dumps`` with BeerSmith's compact spacing — no whitespace anywhere.
    BeerSmith silently drops embedded mash steps stored with default spacing,
    so every JSON column we write must use this."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Folder helpers (M_FOLDER, recipe folders use a path-string convention)
# ---------------------------------------------------------------------------

def ensure_folder(conn: sqlite3.Connection, name: str) -> int:
    """Get or create a top-level recipe folder, returning its ``_PERMID_``.
    Recipe rows reference folders by ``F_R_FOLDER_NAME`` (path string) and
    ``F_R_PARENT`` (folder _PERMID_)."""
    cur = conn.cursor()
    row = cur.execute("SELECT _PERMID_ FROM M_FOLDER WHERE F_F_NAME=?", (name,)).fetchone()
    if row:
        return row[0]
    nid = cur.execute("SELECT COALESCE(MAX(_PERMID_),0)+1 FROM M_FOLDER").fetchone()[0]
    cur.execute(
        "INSERT INTO M_FOLDER (_PERMID_,_MOD_,_CLOUDID_,_EXTRA_,F_F_NAME,F_F_PARENT,F_F_PRIVATE)"
        " VALUES (?,?,?,?,?,?,?)",
        (nid, str(int(dt.datetime.now().timestamp())), 0, 0, name, 0, 1),
    )
    return nid


# ---------------------------------------------------------------------------
# Library row access (catalog source for matcher and substitutions)
# ---------------------------------------------------------------------------

def library_rows(conn: sqlite3.Connection, ingredient_type: str,
                 tagged_only: bool = True) -> list[sqlite3.Row]:
    """Read all rows from M_GRAIN / M_HOPS / M_YEAST / M_MISC. With
    ``tagged_only`` (default True) returns only rows we manage — those whose
    name ends in ``" (brew.is)"``."""
    table, name_col, _ = LIBRARY_TABLE[ingredient_type]
    cur = conn.execute(
        f"SELECT * FROM {table}"
        + (f" WHERE {name_col} LIKE ?" if tagged_only else ""),
        (f"%{TAG}",) if tagged_only else (),
    )
    return cur.fetchall()
