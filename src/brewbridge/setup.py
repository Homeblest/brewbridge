"""One-time setup steps for brewbridge.

Runs ``brewbridge install`` (or via the first-run wizard). Each step is
idempotent — re-running just refreshes paths if anything moved.

Steps:
    * register the ``brewis://`` Windows URL protocol so report buttons fire
      the CLI with the right arguments;
    * install ``BrewIsOrder.htm`` into BeerSmith's user Reports folder so the
      template is available to import via Tools -> Options -> Reports;
    * add the ``Brew.is einfaldur`` mash profile to ``M_MASH`` (so embedded
      mash steps in our recipes link to a real library row);
    * add the ``Reykjavík tap`` water profile to ``M_WATER`` (so the imported
      recipes have a plausible water chemistry default and BeerSmith's mash
      pH calculation has something to chew on).

Refuses to write to ``BeerSmith.sqlite`` while BeerSmith is open."""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .core import beersmith as bs


# ---------------------------------------------------------------------------
# Step 1: brewis:// URL protocol
# ---------------------------------------------------------------------------

def register_protocol() -> str:
    """Register the ``brewis://`` URL handler with the OS.

    Windows: write to ``HKCU\\Software\\Classes\\brewis``. Per-user, no
    admin needed. Idempotent. In a PyInstaller bundle ``sys.executable``
    is ``brewbridge.exe`` itself and routes bare ``brewis://...`` first-arg
    to the ``order`` subcommand; in a source install we go via
    ``python -m brewbridge order "%1"``.

    macOS: the handler is declared in the .app bundle's ``Info.plist``
    (``CFBundleURLTypes`` → ``CFBundleURLSchemes`` → ``["brewis"]``) and
    Launch Services scans it automatically the first time the bundle is
    placed in ``/Applications/`` or run from anywhere. So on mac this
    function is a no-op and returns a descriptive status string for the
    install summary.

    Source installs on macOS *cannot* register the URL handler — Launch
    Services only honours bundle-declared schemes, not arbitrary CLI
    invocations. ``brewbridge install`` on macOS-from-source therefore
    skips this step with a warning.

    Returns the registered command string (or a status message on mac).
    """
    from .core import platform as bb_platform

    if bb_platform.is_windows():
        import winreg

        if getattr(sys, "frozen", False):
            # Register the windowed sibling (brewbridge-url.exe) rather
            # than brewbridge.exe so clicking a brewis:// link doesn't
            # flash a cmd window. Both binaries are the same code path
            # — only the PE subsystem differs. CLI users still use the
            # console binary; the URL handler uses the windowed one.
            url_exe = Path(sys.executable).parent / "brewbridge-url.exe"
            target = str(url_exe) if url_exe.exists() else sys.executable
            cmd = f'"{target}" "%1"'
        else:
            cmd = f'"{sys.executable}" -m brewbridge order "%1"'
        root = winreg.HKEY_CURRENT_USER
        base = r"Software\Classes\brewis"
        with winreg.CreateKey(root, base) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Brew.is order")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(root, base + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
        return cmd

    if bb_platform.is_macos():
        if getattr(sys, "frozen", False):
            return ("registered automatically via brewbridge.app Info.plist "
                    "(Launch Services)")
        return ("skipped (source install on macOS — clone-build the .app "
                "or run with `pip install brewbridge[app]`)")

    raise RuntimeError(
        f"brewis:// protocol registration not implemented for "
        f"platform={sys.platform!r}"
    )


def unregister_protocol() -> None:
    """Remove the brewis:// handler. Safe to call when nothing's registered.

    Windows: scrub ``HKCU\\Software\\Classes\\brewis``. macOS: no-op —
    Launch Services drops the binding automatically when the .app is
    moved to the Trash."""
    from .core import platform as bb_platform

    if bb_platform.is_windows():
        import winreg
        try:
            for sub in (r"shell\open\command", r"shell\open", "shell"):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                                     rf"Software\Classes\brewis\{sub}")
                except FileNotFoundError:
                    pass
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Classes\brewis")
        except FileNotFoundError:
            pass
    # macOS / Linux: nothing to undo.


# ---------------------------------------------------------------------------
# Step 2: BeerSmith report template
# ---------------------------------------------------------------------------

_REPORT_TEMPLATE = """<html><head>
<meta charset="utf-8">
<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
<title>P&ouml;ntun fr&aacute; brew.is &mdash; $NAME</title>
<link rel="stylesheet" href="style.css" type="text/css">
<meta http-equiv="X-UA-Compatible" content="IE=edge" />
<style>
 body { font-family: Segoe UI, Arial, sans-serif; padding: 2em; background: #f5f5f5; color: #222; }
 .card { background: white; padding: 2em 2.5em; border-radius: 10px;
         box-shadow: 0 2px 8px rgba(0,0,0,0.08); max-width: 720px; }
 h1 { margin: 0 0 0.2em; }
 .meta { color: #666; margin-bottom: 1.5em; }
 .row { display: flex; gap: 2.5em; margin: 1em 0; }
 .row .item { font-weight: 600; color: #555; }
 .cta { display: inline-block; background: #1a7ed6; color: white;
        padding: 14px 26px; border-radius: 8px; text-decoration: none;
        font-weight: 700; font-size: 1.05em; margin: 1.2em 0 0.5em; }
 .cta:hover { background: #1466ad; }
 .note { color: #666; font-size: 0.9em; line-height: 1.45; margin-top: 1em; }
 code { background: #eee; padding: 1px 6px; border-radius: 4px; }
</style></head><body>
<div class="card">
  <h1>$NAME</h1>
  <div class="meta">$STYLE_NAME &middot; $TYPE &middot; $DISPLAY_BATCH_SIZE</div>
  <div class="row">
    <div><div class="item">&Aacute;&aelig;tla&eth; OG</div>$EST_OG</div>
    <div><div class="item">&Aacute;&aelig;tla&eth; FG</div>$EST_FG</div>
    <div><div class="item">&Aacute;fengi</div>$EST_ABV</div>
    <div><div class="item">Beiskja</div>$IBU</div>
    <div><div class="item">Litur</div>$EST_COLOR</div>
  </div>
  <a class="cta" href="brewis://order/$NAME/cart">&#9654; Panta hj&aacute; brew.is (fylla Uppskriftav&eacute;lina)</a>
  <div class="note">
    Hnappurinn opnar innkaupalista &iacute; vafranum sem ber saman uppskriftina vi&eth; vah&ouml;rulista
    brew.is. Ef &ouml;ll hr&aacute;efni eru til er hnappur &thorn;ar virkur sem opnar Uppskriftav&eacute;lina
    &iacute; Chromium me&eth; uppskriftinni forfylltri. Vanti eitthva&eth; er hnappurinn d&ouml;kkur og
    ne&eth;an vi&eth; er listi yfir &thorn;a&eth; sem vantar.
    <br><br>
    Kn&uacute;i&eth; af <code>brewbridge</code> v{version} &middot; daglegur samr&uacute;ningur vi&eth; brew.is.
  </div>
</div>
</body></html>"""


def install_report_template(reports_dir: Path = bs.DEFAULT_REPORTS_DIR) -> Path:
    """Write ``BrewIsOrder.htm`` into BeerSmith's user Reports folder.

    The user still needs to import it through BeerSmith's Tools -> Options ->
    Reports tab — BeerSmith requires that flow to register the template with
    the right metadata (RptType, etc.) in its own ``Reports.sqlite``."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "BrewIsOrder.htm"
    path.write_text(_REPORT_TEMPLATE.replace("{version}", __version__),
                    encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Step 3: Mash profile in M_MASH library
# ---------------------------------------------------------------------------

MASH_PROFILE_NAME = "Brew.is einfaldur"

# Two-step infusion mash at 67°C / 152.6°F. Compact format matters — BeerSmith
# silently drops embedded mash steps stored with any other whitespace shape.
_MASH_STEPS = [
    {"_Schema_": "7432", "F_MS_NAME": "Mash In", "F_MS_TYPE": "0",
     "F_MS_INFUSION": "400.0000000",     "F_MS_STEP_TEMP": "152.6000000",
     "F_MS_STEP_TIME": "60.0000000",     "F_MS_RISE_TIME": "2.0000000",
     "F_MS_TUN_ADDITION": "0.0000000",   "F_MS_TUN_HC": "0.0000000",
     "F_MS_TUN_VOL": "0.0000000",        "F_MS_TUN_TEMP": "72.0000000",
     "F_MS_TUN_MASS": "0.0000000",       "F_MS_START_TEMP": "0.0000000",
     "F_MS_GRAIN_TEMP": "0.0000000",     "F_MS_START_VOL": "0.0000000",
     "F_MS_GRAIN_WEIGHT": "160.0000000", "F_MS_INFUSION_TEMP": "163.3466667",
     "F_MS_DECOCTION_AMT": "0.0000000"},
    {"_Schema_": "7432", "F_MS_NAME": "Mash Out", "F_MS_TYPE": "0",
     "F_MS_INFUSION": "224.0000000",     "F_MS_STEP_TEMP": "168.0000000",
     "F_MS_STEP_TIME": "10.0000000",     "F_MS_RISE_TIME": "2.0000000",
     "F_MS_TUN_ADDITION": "0.0000000",   "F_MS_TUN_HC": "0.0000000",
     "F_MS_TUN_VOL": "0.0000000",        "F_MS_TUN_TEMP": "72.0000000",
     "F_MS_TUN_MASS": "0.0000000",       "F_MS_START_TEMP": "0.0000000",
     "F_MS_GRAIN_TEMP": "0.0000000",     "F_MS_START_VOL": "400.0000000",
     "F_MS_GRAIN_WEIGHT": "160.0000000", "F_MS_INFUSION_TEMP": "200.7345136",
     "F_MS_DECOCTION_AMT": "0.0000000"},
]


def install_mash_profile(conn: sqlite3.Connection) -> bool:
    """Idempotent insert of the brewbridge-managed mash profile. Returns True
    if a new row was added, False if it already existed."""
    if conn.execute("SELECT _PERMID_ FROM M_MASH WHERE F_MH_NAME=?",
                    (MASH_PROFILE_NAME,)).fetchone():
        return False
    now_s = str(int(dt.datetime.now().timestamp()))
    new_id = conn.execute(
        "SELECT COALESCE(MAX(_PERMID_),0)+1 FROM M_MASH"
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO M_MASH
           (_PERMID_, _MOD_, _CLOUDID_, _EXTRA_, F_MH_NAME, F_MH_GRAIN_WEIGHT,
            F_MH_GRAIN_TEMP, F_MH_BOIL_TEMP, F_MH_TUN_TEMP, F_MH_PH, F_MH_SPARGE_TEMP,
            F_MH_BATCH, F_MH_BATCH_PCT, F_MH_BATCH_EVEN, F_MH_BATCH_DRAIN,
            F_MASH_39, F_MH_TUN_DEADSPACE, F_MH_BIAB_VOL, F_MH_BIAB, F_MH_NOTES,
            steps, _CLOUD_STATE_, F_MH_EQUIP_ADJUST, F_MH_TUN_VOL, F_MH_TUN_MASS, F_MH_TUN_HC)
           VALUES (?, ?, 0, 0, ?, 160.0, 72.0, 212.0, 72.0, 5.4, 168.0,
                   0, 100.0, 0, 0, 0, 0.0, 640.0, 0,
                   'Brew.is einfaldur innrennslis-mesking við 67°C.',
                   ?, 0, 0, 640.0, 0.0, 0.12)""",
        (new_id, now_s, MASH_PROFILE_NAME, bs.compact_json(_MASH_STEPS)),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Step 4: Reykjavík tap water profile
# ---------------------------------------------------------------------------

REYKJAVIK_WATER = {
    "name":     "Reykjavík tap",
    "ph":       8.0,
    "calcium":  6.0,
    "magnesium":2.0,
    "sodium":   10.0,
    "sulfate":  3.0,
    "chloride": 10.0,
    "bicarb":   28.0,
    "notes":    "Soft Reykjavík tap water (Veitur supply, Gvendarbrunnar springs). "
                "pH ~8.0, very low alkalinity. Excellent as-is for pale beer styles.",
}


def install_water_profile(conn: sqlite3.Connection) -> bool:
    """Idempotent insert of the Reykjavík tap water profile."""
    if conn.execute("SELECT _PERMID_ FROM M_WATER WHERE F_W_NAME=?",
                    (REYKJAVIK_WATER["name"],)).fetchone():
        return False
    w = REYKJAVIK_WATER
    now_s = str(int(dt.datetime.now().timestamp()))
    new_id = conn.execute("SELECT COALESCE(MAX(_PERMID_),0)+1 FROM M_WATER").fetchone()[0]
    conn.execute(
        """INSERT INTO M_WATER
           (_PERMID_, _MOD_, _CLOUDID_, _EXTRA_, F_W_NAME, F_W_AMOUNT, F_W_IN_RECIPE,
            _CLOUD_STATE_, F_W_PH, F_W_CALCIUM, F_W_MAGNESIUM, F_W_SODIUM,
            F_W_SULFATE, F_W_CHLORIDE, F_W_BICARB,
            F_W_GYPSUM, F_W_SALT, F_W_EPSOM, F_W_CACL, F_W_SODA, F_W_CHALK,
            F_W_ADDITIONS_VOL, F_W_NOTES, F_W_INVENTORY, F_W_PRICE, F_ORDER)
           VALUES (?, ?, 0, 0, ?, 640.0, 0, 0, ?, ?, ?, ?, ?, ?, ?,
                   0, 0, 0, 0, 0, 0, 640.0, ?, 0, 0, 0)""",
        (new_id, now_s, w["name"], w["ph"], w["calcium"], w["magnesium"],
         w["sodium"], w["sulfate"], w["chloride"], w["bicarb"], w["notes"]),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Top-level installer
# ---------------------------------------------------------------------------

SCHEDULED_TASK_NAME = "BrewbridgeSync"


def install_scheduled_task(at: str = "06:00") -> str:
    """Register a per-user Scheduled Task that runs ``brewbridge sync``
    every day at ``at`` (HH:MM, local time). Idempotent — re-running
    deletes any existing task with the same name first.

    Per-user (not /SC ONIDLE or system-wide) because brewbridge state
    lives in the user's profile and BeerSmith.sqlite is in %APPDATA%.
    No /RU SYSTEM either — needs to run as the actual user.

    Returns the task command string for the install summary.
    """
    if not sys.platform == "win32":
        return "skipped (scheduled tasks via brewbridge are Windows-only)"

    import subprocess

    # Find the brewbridge CLI executable. In a frozen build this is the
    # exe next to the running process; in a source install this is the
    # `brewbridge` script that pip installed alongside python.
    if getattr(sys, "frozen", False):
        cli = sys.executable
        cmd = f'"{cli}" sync'
    else:
        # `python -m brewbridge sync` works from any source install
        cmd = f'"{sys.executable}" -m brewbridge sync'

    # schtasks.exe is available on every Windows since Vista. /F forces
    # overwrite if the task already exists. /SC DAILY + /ST HH:MM is the
    # simplest possible schedule shape.
    args = [
        "schtasks", "/Create", "/F",
        "/TN", SCHEDULED_TASK_NAME,
        "/TR", cmd,
        "/SC", "DAILY",
        "/ST", at,
    ]
    # capture output so we can include the schtasks error in any
    # RuntimeError we raise; otherwise it'd just hit stderr and vanish
    # in a frozen-bundle context.
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        # Don't fail the whole install over the schedule — the rest of
        # the setup (protocol handler, report template, profiles) is
        # more important. Surface the issue in the summary so the user
        # can re-register it manually if they care.
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        return f"FAILED ({' | '.join(msg)})"
    return cmd


def uninstall_scheduled_task() -> None:
    """Best-effort removal of the daily-sync task. Used by uninstall
    flows; ignores errors (task may not exist)."""
    if sys.platform != "win32":
        return
    import subprocess
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", SCHEDULED_TASK_NAME],
                    capture_output=True)


def install_chromium() -> str:
    """Make sure Playwright's Chromium browser is downloaded.

    Playwright the Python package is bundled into the MSI (~10 MB) but
    the actual Chromium binary (~150 MB) is NOT — that's a separate
    download per the standard Playwright distribution model. This
    function runs the equivalent of ``playwright install chromium``
    using the bundled-CLI shim, so users who installed via MSI get the
    headline "fill brew.is Recipe Machine" feature working without
    needing pip.

    Idempotent — if Chromium is already in the user's Playwright cache,
    Playwright's installer no-ops. Skipped on non-Windows (mac users
    install Chromium via their .app build flow; Linux isn't a target).

    Returns a status string for the install summary. Errors are caught
    and stringified rather than raised — Chromium failing to download
    shouldn't break the rest of the install (the user can re-run
    `brewbridge install` later when they have a working network).
    """
    if sys.platform != "win32":
        return "skipped (not on Windows)"
    # Direct Playwright at the user's cache directory rather than the
    # frozen install's `_internal/playwright/.../package/.local-browsers`
    # (which would require admin to write to AND moves with the install).
    # %LOCALAPPDATA%\ms-playwright is the platform-default cache the
    # standalone Playwright CLI uses too, so users get the same location
    # whether they install via MSI or via pip — and `brewbridge` itself
    # is forced through the same env in fill_recipe_machine so the two
    # halves agree on where Chromium lives.
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
        Path(os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
    )
    try:
        # Playwright's `install chromium` command is exposed as a Python
        # entry point (playwright.__main__). Calling it in-process avoids
        # the frozen-vs-source subprocess shape mismatch — sys.argv just
        # needs the right shape and playwright's main handles the rest,
        # including locating its bundled node.exe + cli.js inside the
        # PyInstaller bundle.
        import playwright.__main__ as pw_main
        # The main() function reads sys.argv directly, so we patch it.
        # SystemExit is the normal return path — Playwright's CLI exits
        # with 0 on success.
        old_argv = sys.argv
        try:
            sys.argv = ["playwright", "install", "chromium"]
            try:
                pw_main.main()
                return "installed (or already present)"
            except SystemExit as e:
                if e.code in (0, None):
                    return "installed (or already present)"
                return f"FAILED (playwright exited {e.code})"
        finally:
            sys.argv = old_argv
    except ImportError:
        return ("FAILED (playwright not bundled — rebuild MSI from "
                "v0.1.4 source)")
    except Exception as e:
        return f"FAILED ({e!r})"


def install_all(*, db_path: Path = bs.DEFAULT_DB_PATH,
                  reports_dir: Path = bs.DEFAULT_REPORTS_DIR,
                  skip_db: bool = False) -> dict:
    """Run all setup steps. Returns a summary dict of what was done."""
    summary: dict = {}
    summary["protocol_command"] = register_protocol()
    summary["report_template"] = install_report_template(reports_dir)
    summary["scheduled_task"] = install_scheduled_task()
    summary["chromium"] = install_chromium()
    if skip_db:
        return summary
    if bs.is_running():
        raise RuntimeError(
            "BeerSmith is running — close it before running install. "
            "We need exclusive write access to BeerSmith.sqlite to add the "
            "mash and water profiles."
        )
    conn = bs.open_db(db_path)
    summary["mash_profile_added"] = install_mash_profile(conn)
    summary["water_profile_added"] = install_water_profile(conn)
    conn.close()
    return summary
