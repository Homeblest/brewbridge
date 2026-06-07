"""Install verifier — check that every piece of the brewbridge stack
is wired up correctly.

Exposed via the ``brewbridge doctor`` CLI subcommand. Each check returns
a (status, message) tuple where status is "ok" / "warn" / "fail" and
message contains either a brief affirmation or a specific remediation
hint. Doctor is read-only — it never modifies the system, so it's safe
to run while BeerSmith is open.

Why this matters: brewbridge has six install-time side effects that can
all fail or drift independently — protocol registration, report template
copy, mash profile, water profile, library populated, recent sync
timestamp. When a user reports "it's not working" the support load is
mostly "which of those is missing?" — doctor answers that in one shot.
"""
from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from dataclasses import dataclass
from typing import Literal

from . import beersmith as bs
from . import platform as bb_platform
from . import sync as bb_sync


Status = Literal["ok", "warn", "fail"]


@dataclass
class Check:
    name: str
    status: Status
    message: str


def _check_brewbridge_on_path() -> Check:
    cli = shutil.which("brewbridge")
    if cli:
        return Check("brewbridge on PATH", "ok", cli)
    return Check(
        "brewbridge on PATH",
        "fail",
        "brewbridge not found via PATH. If installed via MSI, open a "
        "new shell so the env update takes effect. If installed via "
        "pip, run `python -m brewbridge ...` instead.",
    )


def _check_protocol_registration() -> Check:
    """brewis:// URL handler registered for the current user."""
    if not bb_platform.is_windows():
        return Check(
            "brewis:// URL handler",
            "warn",
            f"skipped on {bb_platform.current_platform()} "
            "(URL handler is managed by macOS Launch Services / "
            "Info.plist on that platform — not via this check)",
        )
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Classes\brewis\shell\open\command") as k:
            cmd, _ = winreg.QueryValueEx(k, "")
        return Check("brewis:// URL handler", "ok", cmd)
    except FileNotFoundError:
        return Check(
            "brewis:// URL handler",
            "fail",
            "not registered. Run `brewbridge install` to register it.",
        )


def _check_beersmith_db() -> Check:
    db = bb_platform.beersmith_db_path()
    if not db.exists():
        return Check(
            "BeerSmith.sqlite",
            "fail",
            f"not found at {db}. Has BeerSmith 4 been installed and "
            "run at least once? (It only creates this file on first "
            "launch.)",
        )
    # Try to actually open it so a permissions / lock issue surfaces
    try:
        with sqlite3.connect(str(db)) as conn:
            n_recipes = conn.execute("SELECT COUNT(*) FROM M_RECIPE").fetchone()[0]
            n_grain = conn.execute(
                "SELECT COUNT(*) FROM M_GRAIN WHERE F_G_NAME LIKE ?",
                (f"%{bs.TAG}",),
            ).fetchone()[0]
    except sqlite3.OperationalError as e:
        return Check("BeerSmith.sqlite", "fail",
                     f"can't open: {e}. Close BeerSmith and retry.")
    return Check("BeerSmith.sqlite", "ok",
                 f"{db}  ({n_recipes} recipes, {n_grain} (brew.is) grains)")


def _check_report_template() -> Check:
    p = bb_platform.beersmith_reports_dir() / "BrewIsOrder.htm"
    if p.exists():
        return Check("BrewIsOrder.htm template", "ok", str(p))
    return Check(
        "BrewIsOrder.htm template",
        "fail",
        f"not at {p}. Run `brewbridge install` to drop it, then in "
        "BeerSmith: Tools → Options → Reports → Add Report… → browse "
        "to the file and import as type 'Recipe'.",
    )


def _check_mash_profile() -> Check:
    db = bb_platform.beersmith_db_path()
    if not db.exists():
        return Check("Brew.is einfaldur mash profile", "warn",
                     "BeerSmith.sqlite missing — skipping")
    try:
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT _PERMID_ FROM M_MASH WHERE F_MH_NAME = ?",
                ("Brew.is einfaldur",),
            ).fetchone()
    except sqlite3.OperationalError as e:
        return Check("Brew.is einfaldur mash profile", "fail", str(e))
    if row:
        return Check("Brew.is einfaldur mash profile", "ok",
                     f"in M_MASH (permid {row[0]})")
    return Check(
        "Brew.is einfaldur mash profile",
        "fail",
        "not in M_MASH. Close BeerSmith and run `brewbridge install`.",
    )


def _check_water_profile() -> Check:
    db = bb_platform.beersmith_db_path()
    if not db.exists():
        return Check("Reykjavík tap water profile", "warn",
                     "BeerSmith.sqlite missing — skipping")
    try:
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT _PERMID_ FROM M_WATER WHERE F_W_NAME = ?",
                ("Reykjavík tap",),
            ).fetchone()
    except sqlite3.OperationalError as e:
        return Check("Reykjavík tap water profile", "fail", str(e))
    if row:
        return Check("Reykjavík tap water profile", "ok",
                     f"in M_WATER (permid {row[0]})")
    return Check(
        "Reykjavík tap water profile",
        "fail",
        "not in M_WATER. Close BeerSmith and run `brewbridge install`.",
    )


def _check_spec_reference() -> Check:
    p = bb_sync.REF_PATH
    if not p.exists():
        return Check(
            "specs_reference.json",
            "fail",
            f"missing at {p}. Run any `brewbridge sync` — the file gets "
            "seeded on first sync, with the bundled fallback if no "
            "live data is available.",
        )
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    except Exception as e:
        return Check("specs_reference.json", "fail",
                     f"can't read: {e}. Delete the file and re-sync.")
    total = sum(counts.values())
    if total == 0:
        return Check(
            "specs_reference.json",
            "fail",
            "exists but is empty. Delete the file and re-sync; the "
            "bundled fallback will repopulate it.",
        )
    return Check(
        "specs_reference.json",
        "ok",
        f"{p.stat().st_size:,} bytes  "
        f"({counts.get('grain', 0)} grain, "
        f"{counts.get('hops', 0)} hops, "
        f"{counts.get('yeast', 0)} yeast, "
        f"{counts.get('misc', 0)} misc)",
    )


def _check_scheduled_task() -> Check:
    """Daily-sync Windows Scheduled Task — registered by `brewbridge
    install` so the user doesn't have to remember to sync."""
    if not bb_platform.is_windows():
        return Check(
            "daily-sync scheduled task",
            "warn",
            f"skipped on {bb_platform.current_platform()} "
            "(not yet implemented for this platform)",
        )
    import subprocess
    from brewbridge.setup import SCHEDULED_TASK_NAME
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", SCHEDULED_TASK_NAME, "/FO", "LIST"],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        # Try to extract the schedule line; output format is
        # ``Schedule: ... At 6:00 AM every day ...``. Best-effort —
        # show whatever we can find.
        next_run = ""
        for line in proc.stdout.splitlines():
            if line.strip().startswith(("Next Run Time", "Næsta ker")):
                next_run = line.split(":", 1)[1].strip()
                break
        return Check("daily-sync scheduled task", "ok",
                     f"registered as {SCHEDULED_TASK_NAME}"
                     + (f"; next run {next_run}" if next_run else ""))
    return Check(
        "daily-sync scheduled task",
        "warn",
        "not registered. Run `brewbridge install` to register a "
        "daily sync at 06:00. (Without it, sync only runs when you "
        "open the tray and click Synca núna.)",
    )


def _check_last_sync() -> Check:
    p = bb_sync.LAST_SYNC_FILE
    if not p.exists():
        return Check(
            "last sync",
            "warn",
            "no record found. Run `brewbridge sync` once.",
        )
    try:
        content = p.read_text(encoding="utf-8").strip().split("\n", 1)
        ts = float(content[0])
        status = content[1] if len(content) > 1 else "ok"
    except Exception as e:
        return Check("last sync", "warn", f"can't parse: {e}")
    age_h = (dt.datetime.now().timestamp() - ts) / 3600
    when = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    if status == "failed":
        return Check(
            "last sync",
            "fail",
            f"the most recent sync ({when}) reported a failure. Run "
            "`brewbridge sync` from PowerShell to see the traceback, or "
            f"check {bb_sync.DATA_DIR / 'tray.log'}.",
        )
    if age_h > 48:
        return Check(
            "last sync",
            "warn",
            f"last successful sync was {age_h:.0f} hours ago ({when}). "
            "Stock data is likely out of date.",
        )
    return Check("last sync", "ok",
                 f"{status} at {when}  ({age_h:.1f} hours ago)")


# Order matters: cheapest, broadest checks first so a missing BeerSmith
# install doesn't make us run twenty downstream checks that all fail
# the same way.
CHECKS = (
    _check_brewbridge_on_path,
    _check_protocol_registration,
    _check_beersmith_db,
    _check_report_template,
    _check_mash_profile,
    _check_water_profile,
    _check_spec_reference,
    _check_scheduled_task,
    _check_last_sync,
)


def run() -> list[Check]:
    """Run every check, return the list. Never raises — each check
    catches its own exceptions and reports them as a "fail" status."""
    out: list[Check] = []
    for fn in CHECKS:
        try:
            out.append(fn())
        except Exception as e:
            out.append(Check(fn.__name__.lstrip("_"), "fail", f"check crashed: {e}"))
    return out


def print_report(checks: list[Check]) -> int:
    """Render the result table and return a process exit code:
    0 if everything's "ok" or "warn", 1 if any "fail"."""
    symbol = {"ok": "✓", "warn": "⚠", "fail": "✗"}
    width = max(len(c.name) for c in checks) + 2
    for c in checks:
        print(f"  {symbol[c.status]} {c.name:<{width}}{c.message}")
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    print()
    print(f"Total: {len(checks)} checks — "
          f"{len(checks) - len(fails) - len(warns)} ok, "
          f"{len(warns)} warn, {len(fails)} fail")
    return 1 if fails else 0
