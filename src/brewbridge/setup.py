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
import json
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
    """Add the ``brewis://`` handler under HKCU\\Software\\Classes\\brewis.
    Per-user so we don't need admin. Idempotent.

    In a PyInstaller bundle ``sys.executable`` is ``brewbridge.exe`` itself,
    which is also the URL handler — ``__main__.main`` already routes a bare
    ``brewis://...`` first-arg to the ``order`` subcommand. In a source
    install we fall back to ``python -m brewbridge order "%1"``.

    Returns the registered command string."""
    if sys.platform != "win32":
        raise RuntimeError("brewis:// protocol registration is Windows-only")
    import winreg

    if getattr(sys, "frozen", False):
        # Frozen bundle — the EXE handles brewis:// URLs directly.
        cmd = f'"{sys.executable}" "%1"'
    else:
        # Source install — invoke the module through the running interpreter.
        cmd = f'"{sys.executable}" -m brewbridge order "%1"'
    root = winreg.HKEY_CURRENT_USER
    base = r"Software\Classes\brewis"
    with winreg.CreateKey(root, base) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Brew.is order")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(root, base + r"\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    return cmd


def unregister_protocol() -> None:
    """Remove the brewis:// handler. Safe to call when nothing's registered."""
    if sys.platform != "win32":
        return
    import winreg
    try:
        # Remove subkeys first
        for sub in (r"shell\open\command", r"shell\open", "shell"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\brewis\{sub}")
            except FileNotFoundError:
                pass
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\brewis")
    except FileNotFoundError:
        pass


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
  <a class="cta" href="brewis://order/$NAME">&#9654; Yfirfara og panta hj&aacute; brew.is</a>
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

def install_all(*, db_path: Path = bs.DEFAULT_DB_PATH,
                  reports_dir: Path = bs.DEFAULT_REPORTS_DIR,
                  skip_db: bool = False) -> dict:
    """Run all setup steps. Returns a summary dict of what was done."""
    summary: dict = {}
    summary["protocol_command"] = register_protocol()
    summary["report_template"] = install_report_template(reports_dir)
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
