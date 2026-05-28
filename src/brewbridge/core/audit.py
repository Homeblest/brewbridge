"""Recipe sanity audit + auto-fix.

Checks every recipe in the ``/Brew.is/`` folder for:

1. **Ingredient mismatches.** Recipe ingredients that the brew.is catalog
   resolves to a different brewing family — usually a sign that brewis_sync's
   matcher made a bad call at sync time and the recipe imported with the
   wrong specs.
2. **Mash profile sanity.** Steps populated, positive grain weight, name not
   colliding with a stock M_MASH library profile (BeerSmith clobbers embedded
   steps when the name resolves to a library row).
3. **Yeast PKG_DATE freshness.** Library and embedded copies both.
4. **Color vs style range.** Soft Suggestion-tier check — reports without
   fixing because style choice is recipe-author intent.

Auto-fixable:
* Stale yeast dates -> reset to "today" (library + embedded).
* Empty/missing mash steps -> rebuild with the canonical brew.is profile.
* Mash name collisions -> rename to the brewbridge-managed profile.

The colour mismatches are reported only — fixing them requires changing
the recipe's grain bill or its BJCP style label, which is a human call.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import beersmith as bs
from . import matching as mm

# Stale-date cutoff. Anything before 2024 we consider "obvious test data" and
# refresh on auto-fix.
RECENT_CUTOFF = int(dt.datetime(2024, 1, 1).timestamp())

# brewbridge-managed mash profile name (must match the M_MASH row created by
# install_mash_profile).
MASH_PROFILE_NAME = "Brew.is einfaldur"

# Default mash temperature (°C). Most brew.is recipes target 66-67 °C.
DEFAULT_MASH_C = 67.0


@dataclass
class Issue:
    recipe: str
    severity: str   # 'CRIT' | 'WARN' | 'INFO'
    category: str   # 'mash' | 'match' | 'yeast_date' | 'color'
    message: str


@dataclass
class AuditResult:
    recipes_checked: int
    issues: list[Issue] = field(default_factory=list)
    yeast_dates_fixed: tuple[int, int] = (0, 0)  # (library_rows, recipe_rows)
    mashes_rebuilt: int = 0
    backup: Path | None = None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit(conn: sqlite3.Connection,
          folder: str = "/Brew.is/") -> tuple[list[sqlite3.Row], list[Issue]]:
    """Walk all recipes in ``folder``, return (rows, issues)."""
    catalog = _catalog_for_match(conn)
    issues: list[Issue] = []
    rows = list(conn.execute(
        "SELECT * FROM M_RECIPE WHERE F_R_FOLDER_NAME=? ORDER BY F_R_NAME",
        (folder,),
    ))
    for r in rows:
        issues.extend(_audit_recipe(r, catalog))
    return rows, issues


def _catalog_for_match(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Build the same catalog shape ``matching.match_product`` expects from
    the (brew.is) library rows."""
    out: dict[str, list[dict]] = {}
    for t, (table, name_col, _) in bs.LIBRARY_TABLE.items():
        out[t] = []
        for r in conn.execute(
            f"SELECT * FROM {table} WHERE {name_col} LIKE ?", (f"%{bs.TAG}",)
        ):
            d = dict(r)
            d["name"] = d[name_col].replace(bs.TAG, "")
            d["alt_name"] = mm.translate(d["name"])
            out[t].append(d)
    return out


def _audit_recipe(r: sqlite3.Row, catalog: dict) -> list[Issue]:
    issues: list[Issue] = []
    name = r["F_R_NAME"]
    ings = json.loads(r["Ingredients"]) if r["Ingredients"] else []
    issues.extend(_audit_matches(name, ings, catalog))
    issues.extend(_audit_mash(name, r["F_R_MASH"]))
    issues.extend(_audit_yeast_dates(name, ings))
    issues.extend(_audit_color(name, r))
    return issues


def _audit_matches(name: str, ings: list[dict], catalog: dict) -> list[Issue]:
    out: list[Issue] = []
    for ing in ings:
        s = ing.get("_Schema_")
        if s == bs.SCHEMA["grain"]:
            ing_name = ing["F_G_NAME"]
            ing_bucket = mm.grain_bucket(ing_name, mm._f(ing.get("F_G_COLOR")))
            prod, score, _ = mm.match_product(ing_name, "grain", catalog, ing_meta=ing)
            if prod:
                cat_bucket = mm.grain_bucket(prod["name"], mm._f(prod.get("F_G_COLOR")))
                if ing_bucket != cat_bucket:
                    out.append(Issue(name, "WARN", "match",
                        f"grain '{ing_name}' ({ing_bucket}) -> "
                        f"'{prod['name']}' ({cat_bucket}) [score {score:.2f}]"))
                elif score < 0.75:
                    out.append(Issue(name, "INFO", "match",
                        f"grain '{ing_name}' -> '{prod['name']}' [score {score:.2f}]"))
        elif s == bs.SCHEMA["hops"]:
            ing_name = ing["F_H_NAME"]
            ing_fams = mm.hop_families(ing_name)
            prod, score, _ = mm.match_product(ing_name, "hops", catalog, ing_meta=ing)
            if prod:
                c_fams = mm.hop_families(prod["name"])
                if c_fams and "unknown" not in c_fams and "unknown" not in ing_fams \
                   and not (ing_fams & c_fams):
                    out.append(Issue(name, "WARN", "match",
                        f"hop '{ing_name}' ({ing_fams}) -> "
                        f"'{prod['name']}' ({c_fams}) [score {score:.2f}]"))
        elif s == bs.SCHEMA["yeast"]:
            ing_name = ing["F_Y_NAME"]
            ing_pid = ing.get("F_Y_PRODUCT_ID", "")
            ing_fam = mm.yeast_family(ing_name, ing_pid)
            prod, score, _ = mm.match_product(ing_name, "yeast", catalog,
                                              alt_name=ing_pid, ing_meta=ing)
            if prod:
                cfam = mm.yeast_family(prod["name"], prod.get("sku", ""))
                if ing_fam != cfam and "unknown" not in (ing_fam, cfam):
                    out.append(Issue(name, "WARN", "match",
                        f"yeast '{ing_name}' ({ing_fam}) -> "
                        f"'{prod['name']}' ({cfam}) [score {score:.2f}]"))
    return out


def _audit_mash(name: str, mash_json: str | None) -> list[Issue]:
    out: list[Issue] = []
    if not mash_json:
        out.append(Issue(name, "CRIT", "mash", "no F_R_MASH content"))
        return out
    try:
        m = json.loads(mash_json)
        steps_raw = m.get("steps", "[]")
        steps = json.loads(steps_raw) if isinstance(steps_raw, str) else (steps_raw or [])
        grain_wt = mm._f(m.get("F_MH_GRAIN_WEIGHT")) or 0
        if grain_wt <= 0:
            out.append(Issue(name, "CRIT", "mash",
                f"mash grain weight is {grain_wt} oz"))
        if not steps:
            out.append(Issue(name, "CRIT", "mash", "no mash steps"))
    except (json.JSONDecodeError, TypeError) as e:
        # BeerSmith's native mash uses non-standard JSON (unescaped inner
        # quotes); a parse error here usually means the recipe is actually OK
        # in BeerSmith's eyes — we report INFO so the user knows.
        out.append(Issue(name, "INFO", "mash", f"non-standard mash JSON ({e})"))
    return out


def _audit_yeast_dates(name: str, ings: list[dict]) -> list[Issue]:
    out: list[Issue] = []
    for ing in ings:
        if ing.get("_Schema_") != bs.SCHEMA["yeast"]:
            continue
        ts = bs.parse_date_field(ing.get("F_Y_PKG_DATE", ""))
        if 0 < ts < RECENT_CUTOFF:
            out.append(Issue(name, "WARN", "yeast_date",
                f"'{ing['F_Y_NAME']}' pkg date "
                f"{dt.datetime.fromtimestamp(ts).date()}"))
    return out


def _audit_color(name: str, r: sqlite3.Row) -> list[Issue]:
    try:
        st = json.loads(r["F_R_STYLE"]) if r["F_R_STYLE"] else {}
        mn = mm._f(st.get("F_S_MIN_COLOR")) or 0
        mx = mm._f(st.get("F_S_MAX_COLOR")) or 0
        dc = mm._f(r["F_R_DESIRED_COLOR"]) or 0
        if mn > 0 and mx > 0 and dc > 0 and (dc < mn * 0.9 or dc > mx * 1.1):
            return [Issue(name, "INFO", "color",
                f"target {dc:.1f} SRM vs style {st.get('F_S_NAME')} "
                f"({mn:.0f}-{mx:.0f} SRM)")]
    except (TypeError, json.JSONDecodeError):
        pass
    return []


# ---------------------------------------------------------------------------
# Auto-fixes
# ---------------------------------------------------------------------------

def fix_yeast_dates(conn: sqlite3.Connection,
                    folder: str = "/Brew.is/") -> tuple[int, int]:
    """Refresh stale yeast pkg/culture/brew dates everywhere — library AND
    embedded inside each recipe. Returns (library_rows, recipe_rows) changed."""
    now_ts = int(dt.datetime.now().timestamp())
    now_s = str(now_ts)
    lib_fixed = 0
    for r in conn.execute(
        "SELECT _PERMID_, F_Y_PKG_DATE, F_Y_CULTURE_DATE, F_Y_BREW_DATE "
        "FROM M_YEAST WHERE F_Y_NAME LIKE ?", (f"%{bs.TAG}",)
    ):
        stale = any(0 < bs.parse_date_field(v) < RECENT_CUTOFF
                    for v in (r["F_Y_PKG_DATE"], r["F_Y_CULTURE_DATE"],
                              r["F_Y_BREW_DATE"]))
        if stale:
            conn.execute(
                "UPDATE M_YEAST SET F_Y_PKG_DATE=?, F_Y_CULTURE_DATE=?, "
                "F_Y_BREW_DATE=?, _MOD_=? WHERE _PERMID_=?",
                (now_ts, now_ts, now_ts, now_s, r["_PERMID_"]),
            )
            lib_fixed += 1
    rec_fixed = 0
    for r in conn.execute(
        "SELECT _PERMID_, Ingredients FROM M_RECIPE WHERE F_R_FOLDER_NAME=?",
        (folder,)
    ):
        ings = json.loads(r["Ingredients"]) if r["Ingredients"] else []
        changed = False
        for ing in ings:
            if ing.get("_Schema_") != bs.SCHEMA["yeast"]:
                continue
            for k in ("F_Y_PKG_DATE", "F_Y_CULTURE_DATE", "F_Y_BREW_DATE"):
                ts = bs.parse_date_field(ing.get(k, ""))
                if 0 < ts < RECENT_CUTOFF:
                    ing[k] = now_s
                    changed = True
        if changed:
            conn.execute(
                "UPDATE M_RECIPE SET Ingredients=?, _MOD_=? WHERE _PERMID_=?",
                (bs.compact_json(ings), now_s, r["_PERMID_"]),
            )
            rec_fixed += 1
    conn.commit()
    return lib_fixed, rec_fixed


def fix_mashes(conn: sqlite3.Connection,
               folder: str = "/Brew.is/") -> int:
    """Rebuild mash data for any recipe where steps are empty / grain weight
    is zero / the mash name is a library profile (which BeerSmith re-binds
    and silently clears). Returns count of recipes touched."""
    library_mash_names = {r[0] for r in conn.execute("SELECT F_MH_NAME FROM M_MASH")}
    now_s = str(int(dt.datetime.now().timestamp()))
    fixed = 0
    for r in conn.execute(
        "SELECT _PERMID_, F_R_MASH, Ingredients FROM M_RECIPE WHERE F_R_FOLDER_NAME=?",
        (folder,)
    ):
        try:
            m = json.loads(r["F_R_MASH"]) if r["F_R_MASH"] else {}
            steps_raw = m.get("steps", "[]")
            steps = json.loads(steps_raw) if isinstance(steps_raw, str) else (steps_raw or [])
            grain_wt = mm._f(m.get("F_MH_GRAIN_WEIGHT")) or 0
            cur_name = m.get("F_MH_NAME", "")
            needs_fix = (grain_wt <= 0 or not steps
                         or (cur_name in library_mash_names
                             and cur_name != MASH_PROFILE_NAME))
        except Exception:
            needs_fix = True
        if not needs_fix:
            continue
        ings = json.loads(r["Ingredients"]) if r["Ingredients"] else []
        grain_kg = sum(mm._f(i.get("F_G_AMOUNT", 0), 0) / bs.OZ_PER_KG
                       for i in ings if i.get("_Schema_") == bs.SCHEMA["grain"])
        if grain_kg <= 0:
            continue
        conn.execute(
            "UPDATE M_RECIPE SET F_R_MASH=?, _MOD_=? WHERE _PERMID_=?",
            (build_native_mash(grain_kg), now_s, r["_PERMID_"]),
        )
        fixed += 1
    conn.commit()
    return fixed


def build_native_mash(grain_kg: float, mash_c: float = DEFAULT_MASH_C) -> str:
    """Render an F_R_MASH JSON value in BeerSmith's exact native serialisation
    — compact, all-quoted values, with the inner ``steps`` JSON's quotes
    UNESCAPED. Anything else gets silently rejected by BeerSmith's parser."""
    grain_lb = grain_kg * bs.LB_PER_KG
    grain_oz = grain_kg * bs.OZ_PER_KG
    temp_f = mash_c * 9 / 5 + 32
    ratio = 1.5
    grain_temp_f = 72.0
    infusion_floz = grain_lb * ratio * 32.0
    strike_f = (0.2 / ratio) * (temp_f - grain_temp_f) + temp_f

    step = (
        '{"_Schema_":"7432","F_MS_NAME":"Mash In","F_MS_TYPE":"0",'
        f'"F_MS_INFUSION":"{infusion_floz:.7f}",'
        f'"F_MS_STEP_TEMP":"{temp_f:.7f}",'
        '"F_MS_STEP_TIME":"60.0000000","F_MS_RISE_TIME":"2.0000000",'
        '"F_MS_TUN_ADDITION":"0.0000000","F_MS_TUN_HC":"0.3000000",'
        '"F_MS_TUN_VOL":"640.0000000","F_MS_TUN_TEMP":"72.0000000",'
        '"F_MS_TUN_MASS":"0.0000000","F_MS_START_TEMP":"0.0000000",'
        f'"F_MS_GRAIN_TEMP":"{grain_temp_f:.7f}","F_MS_START_VOL":"0.0000000",'
        f'"F_MS_GRAIN_WEIGHT":"{grain_oz:.7f}",'
        f'"F_MS_INFUSION_TEMP":"{strike_f:.7f}",'
        '"F_MS_DECOCTION_AMT":"0.0000000"}'
    )
    return (
        '{"_Schema_":"7434","F_MH_NAME":"' + MASH_PROFILE_NAME + '",'
        f'"F_MH_GRAIN_WEIGHT":"{grain_oz:.7f}",'
        '"F_MH_GRAIN_TEMP":"72.0000000","F_MH_BOIL_TEMP":"212.0000000",'
        '"F_MH_TUN_TEMP":"72.0000000","F_MH_PH":"5.4000000",'
        '"F_MH_SPARGE_TEMP":"168.0000000","F_MH_BATCH":"0",'
        '"F_MH_BATCH_PCT":"100.0000000","F_MH_BATCH_EVEN":"0",'
        '"F_MH_BATCH_DRAIN":"0","F_MASH_39":"1",'
        '"F_MH_TUN_DEADSPACE":"0.0000000","F_MH_BIAB_VOL":"640.0000000",'
        '"F_MH_BIAB":"0","F_MH_NOTES":"Brew.is einfaldur — innrennslis-mesking við 67°C.",'
        f'"steps":"[{step}]",'
        '"_CLOUD_STATE_":"0","F_MH_EQUIP_ADJUST":"0",'
        '"F_MH_TUN_VOL":"640.0000000","F_MH_TUN_MASS":"0.0000000",'
        '"F_MH_TUN_HC":"0.3000000"}'
    )


# ---------------------------------------------------------------------------
# Top-level CLI helper
# ---------------------------------------------------------------------------

def run(*, db_path: Path = bs.DEFAULT_DB_PATH, fix: bool = False,
        folder: str = "/Brew.is/") -> AuditResult:
    if fix and bs.is_running():
        raise RuntimeError(
            "BeerSmith is running — close it before running with --fix."
        )
    conn = bs.open_db(db_path)
    rows, issues = audit(conn, folder)
    result = AuditResult(recipes_checked=len(rows), issues=issues)
    if fix:
        result.backup = bs.backup_db(db_path)
        result.yeast_dates_fixed = fix_yeast_dates(conn, folder)
        result.mashes_rebuilt = fix_mashes(conn, folder)
    conn.close()
    return result
