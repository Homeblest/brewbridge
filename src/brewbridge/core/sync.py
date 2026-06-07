"""Daily catalog sync — brew.is ingredients into BeerSmith's library tables.

What it does
------------
1. Fetch the /uppskriftir page once (one HTTP request).
2. Decode the embedded Nuxt payload (no API key needed).
3. Keep only in-stock brewing ingredients (`quantity > 0`, in one of the
   grain/hops/yeast/misc category sets).
4. For each in-stock item, classify it (grain/hops/yeast/misc) and look up a
   matching built-in BeerSmith ingredient from the frozen spec reference so
   we can copy brewing properties (color, alpha, attenuation, etc.).
5. Replace the managed ``(brew.is)``-tagged rows in M_GRAIN / M_HOPS / M_YEAST
   / M_MISC with the fresh set, optionally clearing any non-tagged rows when
   running in "brew.is only" mode.

Refuses to run while BeerSmith is open — writing to BeerSmith.sqlite during
its session causes BeerSmith to clobber our changes on its own save cycle.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import beersmith as bs
from . import catalog as cat
from . import matching as mm
from . import orders


# ---------------------------------------------------------------------------
# Spec reference (frozen snapshot of BeerSmith built-in libraries)
# ---------------------------------------------------------------------------

DATA_DIR = Path.home() / ".brewbridge"
REF_PATH = DATA_DIR / "specs_reference.json"
LAST_SYNC_FILE = DATA_DIR / "last_sync.txt"


def _record_sync_state(status: str) -> None:
    """Persist the latest sync outcome to ``~/.brewbridge/last_sync.txt``.

    The tray's icon-colour logic reads this file to render green/yellow/
    red. Used to live only in tray.py — which meant CLI syncs (and any
    future cron-driven sync) silently left the icon stuck on whatever
    the LAST tray sync wrote, often "failed" from a long-ago crash. By
    putting the write here, every sync — regardless of caller — keeps
    the icon honest.

    Format is ``<unix_timestamp>\\n<status>\\n`` where status is "ok"
    or "failed". Same format the tray's ``_sync_state()`` reader
    expects.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAST_SYNC_FILE.write_text(
        f"{dt.datetime.now().timestamp()}\n{status}\n",
        encoding="utf-8",
    )

# Bundled fallback. Used when the user's library is already purged on
# first sync (which happens to anyone who ran an earlier version of
# brewbridge or the legacy brewis-beersmith scripts before this code
# existed). The asset is a frozen snapshot of a clean BeerSmith 4
# install's M_GRAIN / M_HOPS / M_YEAST / M_MISC libraries (142/464/
# 538/129 rows), so the spec-matcher always has something to bind
# against even when the live DB has nothing.
_BUNDLED_REF = Path(__file__).resolve().parent.parent / "assets" / "specs_reference.json"


def _is_empty_snapshot(data: dict) -> bool:
    """A snapshot is 'empty' for our purposes if every category is
    zero-length. Half-empty (e.g. grains populated but yeast zero) is
    still better than the bundled fallback for those non-empty
    categories, so we don't replace those."""
    return all(not data.get(t) for t in bs.LIBRARY_TABLE)


def load_spec_reference(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Return the frozen spec reference — used to copy brewing properties
    (color, alpha, attenuation...) onto newly-inserted (brew.is) library
    rows so BeerSmith's IBU/ABV calculators have real numbers to work with.

    Resolution order:
      1. Existing ``~/.brewbridge/specs_reference.json`` IF it has rows.
      2. Snapshot the user's live BeerSmith library (every non-(brew.is)
         row) — that's the case for a clean BeerSmith 4 install where the
         built-in library hasn't been purged yet.
      3. Bundled package asset ``brewbridge/assets/specs_reference.json``
         — a frozen snapshot from a clean BeerSmith 4. Last-resort
         fallback for users whose library was already purged when they
         first installed brewbridge. (1.3 MB; shipped in the wheel/MSI.)
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Existing file, if non-empty
    if REF_PATH.exists():
        with REF_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        if not _is_empty_snapshot(data):
            return data
        # Empty snapshot from a prior bad run — keep going, we'll repopulate.

    # 2. Snapshot the live library
    refs: dict[str, list[dict]] = {}
    for t, (table, name_col, _) in bs.LIBRARY_TABLE.items():
        refs[t] = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {name_col} NOT LIKE ?",
            (f"%{bs.TAG}",),
        )]

    if not _is_empty_snapshot(refs):
        with REF_PATH.open("w", encoding="utf-8") as f:
            json.dump(refs, f, ensure_ascii=False)
        return refs

    # 3. Bundled asset fallback
    if _BUNDLED_REF.exists():
        with _BUNDLED_REF.open(encoding="utf-8") as f:
            refs = json.load(f)
        # Cache it locally so subsequent runs don't re-read the asset and
        # so the user can edit it if they want to override.
        with REF_PATH.open("w", encoding="utf-8") as f:
            json.dump(refs, f, ensure_ascii=False)
        return refs

    # Shouldn't happen — bundled asset is committed and shipped. Be
    # explicit if it does so we don't silently match nothing.
    raise RuntimeError(
        "No spec reference available: live BeerSmith library is empty AND "
        f"the bundled fallback at {_BUNDLED_REF} is missing. brewbridge "
        "install is malformed."
    )


def column_template(conn: sqlite3.Connection, ingredient_type: str) -> dict[str, object]:
    """A blank column dict for an ingredient type, with type-aware defaults.
    Used when no built-in spec match exists for an item, so we still produce
    a row with every column filled to something sensible."""
    table = bs.LIBRARY_TABLE[ingredient_type][0]
    tmpl = {}
    for _, cname, ctype, *_ in conn.execute(f"PRAGMA table_info({table})"):
        tmpl[cname] = "" if (ctype or "").upper().startswith("TEXT") else 0
    return tmpl


# ---------------------------------------------------------------------------
# Building managed library rows from brew.is products
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    products: int
    inserted: dict[str, int]
    deleted: int
    matched: int
    unmatched: int
    backup: Path
    report_path: Path
    # Recipes that flipped from "blocked (something missing)" to "fully
    # orderable" as a result of this sync. Empty list = nothing changed
    # (or this was a first-ever sync with no pre-state to compare to).
    unlocked: list[tuple[int, str]] = None  # type: ignore[assignment]

    def __post_init__(self):
        # dataclass defaults can't use mutable list literals safely; the
        # None sentinel is the standard workaround. Convert on construction.
        if self.unlocked is None:
            self.unlocked = []


def _make_notes(product: dict, amount: tuple[float, str] | None,
                matched_name: str | None, score: float) -> str:
    parts = [
        "Source: brew.is",
        f"SKU: {product.get('sku') or '-'}",
        f"Stock qty: {product.get('quantity')}",
        f"Price: {product.get('price')} ISK",
    ]
    if product.get("keyword"):
        parts.append(f"https://www.brew.is/p/{product['keyword']}")
    if amount:
        parts.append(f"Pack: {amount[0]:g} {amount[1]}")
    if matched_name:
        parts.append(f"Specs from built-in: {matched_name} (match {score:.0%})")
    else:
        parts.append("Specs NOT matched - please verify color/alpha/attenuation.")
    return " | ".join(parts)


def _row_for(t: str, name: str, matched: dict | None, template: dict,
             inv: float, note: str, now: str) -> dict:
    """Build a complete column dict for one managed ingredient row.

    Start from the column template (which has every real schema column
    with a sane default), then overlay matched values ONLY for keys
    that exist in the template. The matched dict can carry synthetic
    keys added during matching (e.g. ``"name"``) that aren't real DB
    columns — including them in the INSERT raises
    ``sqlite3.OperationalError: table X has no column named name``.
    This filter-via-template approach is robust to future additions of
    synthetic match-helper keys.
    """
    row = dict(template)
    if matched:
        for k, v in matched.items():
            if k in template:
                row[k] = v
    row.pop("_PERMID_", None)
    row.update({
        "_MOD_": now, "_CLOUDID_": 0, "_EXTRA_": 0,
        "_CLOUD_STATE_": 0, "F_ORDER": 99000,
    })
    name_col = bs.LIBRARY_TABLE[t][1]
    row[name_col] = name
    if t == "grain":
        row.update({
            "F_G_SUPPLIER": "brew.is",
            "F_G_INVENTORY": inv,
            "F_G_PRICE": 0.0,
            "F_G_AMOUNT": 0.0,
            "F_G_IN_RECIPE": 0,
            "F_G_NOTES": note,
        })
    elif t == "hops":
        row.update({
            "F_H_INVENTORY": inv,
            "F_H_PRICE": 0.0,
            "F_H_AMOUNT": 0.0,
            "F_H_IN_RECIPE": 0,
            "F_H_NOTES": note,
        })
    elif t == "yeast":
        # Stamp fresh dates so BeerSmith doesn't whine about old yeast.
        now_int = int(dt.datetime.now().timestamp())
        row.update({
            "F_Y_INVENTORY": inv,
            "F_Y_PRICE": 0.0,
            "F_Y_AMOUNT": 0.0,
            "F_Y_IN_RECIPE": 0,
            "F_Y_NOTES": note,
            "F_Y_PKG_DATE": now_int,
            "F_Y_CULTURE_DATE": now_int,
            "F_Y_BREW_DATE": now_int,
        })
    else:  # misc
        row.update({
            "F_M_INVENTORY": inv,
            "F_M_PRICE": 0.0,
            "F_M_AMOUNT": 0.0,
            "F_M_NOTES": note,
        })
    return row


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def run(*, db_path: Path = bs.DEFAULT_DB_PATH, purge_builtins: bool = False,
        report_dir: Path | None = None) -> SyncResult:
    """Execute one sync. Raises RuntimeError if BeerSmith is running.

    purge_builtins: if True, non-(brew.is) ingredient rows are also deleted
    each run, keeping the library brew.is-only (best for a homebrewer whose
    only supplier is brew.is). If False (the default since v0.1.3), leaves
    every existing library row untouched and only adds / refreshes the
    (brew.is)-tagged rows. The old default was destructive: an experienced
    BeerSmith user installing brewbridge for the first time would lose
    every custom hop / yeast / grain they'd added over years. We always
    take a BeerSmith.sqlite backup before any write either way.

    Always records sync outcome to ``~/.brewbridge/last_sync.txt`` — on
    success ("ok") at the end, on any raised exception ("failed") via the
    try/except wrapper. The tray's icon colour reads from that file, so
    CLI and tray syncs both keep the icon honest.
    """
    try:
        return _run_inner(db_path=db_path, purge_builtins=purge_builtins,
                          report_dir=report_dir)
    except Exception:
        _record_sync_state("failed")
        raise


def _run_inner(*, db_path: Path, purge_builtins: bool,
                report_dir: Path | None) -> SyncResult:
    """Actual sync implementation. Wrapped by ``run()`` which guarantees
    last_sync.txt is updated whether we succeed or raise."""
    if bs.is_running():
        raise RuntimeError(
            "BeerSmith is running — close it before syncing. "
            "BeerSmith caches the database in memory and overwrites changes on save."
        )
    report_dir = report_dir or DATA_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching brew.is catalog ...")
    html = cat.fetch_payload()
    products, cat_names = cat.parse_products(html)
    print(f"  {len(products)} products, {len(cat_names)} categories")

    kept = list(cat.in_stock(products, cat_names, ingredient_only=True))
    print(f"  {len(kept)} in-stock brewing ingredients")

    conn = bs.open_db(db_path)
    refs = load_spec_reference(conn)
    templates = {t: column_template(conn, t) for t in bs.LIBRARY_TABLE}

    # Pre-sync snapshot: which recipes are blocked right now? Captured
    # against the OLD (brew.is) library rows before the DELETE wipes
    # them. If the old catalog is empty (first-ever sync, or all rows
    # cleared), skip the snapshot — there's no meaningful "before" to
    # compare against and dumping every recipe in a first-sync toast
    # would be noisy. ``pre_state = None`` flows through to "no diff".
    pre_catalog = orders.load_catalog(conn)
    pre_has_rows = any(items for items in pre_catalog.values())
    pre_state: dict[int, dict] | None = (
        orders.all_recipe_blockers(conn, pre_catalog) if pre_has_rows else None
    )

    now_s = str(int(time.time()))
    rows: dict[str, list[dict]] = {t: [] for t in bs.LIBRARY_TABLE}
    matched_count = 0
    unmatched: list[tuple[str, str]] = []

    for p in kept:
        t = p["_bs_type"]
        amount, base = cat.parse_pack_amount(p["name"])
        # Match against the spec reference (the built-in BeerSmith specs we
        # snapshotted before any purge) so we can copy brewing properties.
        ref_catalog = {t: [{"name": (r.get("F_G_NAME") or r.get("F_H_NAME")
                                     or r.get("F_Y_NAME") or r.get("F_M_NAME") or ""),
                            **r} for r in refs[t]]}
        prod, score, _ = mm.match_product(base, t, ref_catalog)
        matched_name = prod["name"] if prod else None
        if prod:
            matched_count += 1
        else:
            unmatched.append((t, p["name"].strip()))
        note = _make_notes(p, amount, matched_name, score)
        # inventory = 1.0 as a presence flag; real stock count lives in the note
        display_name = (base.strip() or p["name"].strip()) + bs.TAG
        rows[t].append(_row_for(t, display_name, prod, templates[t],
                                 1.0, note, now_s))

    backup = bs.backup_db(db_path)
    cur = conn.cursor()
    deleted = 0
    for t, (table, name_col, _) in bs.LIBRARY_TABLE.items():
        if purge_builtins:
            deleted += cur.execute(f"DELETE FROM {table}").rowcount
        else:
            deleted += cur.execute(
                f"DELETE FROM {table} WHERE {name_col} LIKE ?", (f"%{bs.TAG}",)
            ).rowcount
    inserted: dict[str, int] = {}
    for t, table in ((tt, info[0]) for tt, info in bs.LIBRARY_TABLE.items()):
        for row in rows[t]:
            cols = ", ".join(f'"{c}"' for c in row)
            ph = ", ".join("?" for _ in row)
            cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", list(row.values()))
        inserted[t] = len(rows[t])
    conn.commit()

    # Post-sync snapshot against the freshly-written (brew.is) rows.
    # Done before conn.close() so we don't have to reopen. Diff against
    # pre_state to find recipes that flipped blocked → orderable.
    if pre_state is not None:
        post_catalog = orders.load_catalog(conn)
        post_state = orders.all_recipe_blockers(conn, post_catalog)
        unlocked = orders.newly_orderable(pre_state, post_state)
    else:
        unlocked = []

    conn.close()

    # Write a human-readable report alongside the backup
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt = report_dir / f"sync_{stamp}.txt"
    with rpt.open("w", encoding="utf-8") as f:
        f.write(f"brewbridge sync  {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        for t, n in inserted.items():
            f.write(f"  {t:<6}: {n:>3} in-stock items\n")
        f.write(f"\nMatched to built-in specs: {matched_count} / {len(kept)}\n")
        if unmatched:
            f.write("\nUnmatched (verify specs in BeerSmith):\n")
            for t, name in sorted(unmatched):
                f.write(f"  [{t}] {name}\n")
        if unlocked:
            f.write(f"\nNow orderable after this sync ({len(unlocked)}):\n")
            for _rid, name in unlocked:
                f.write(f"  - {name}\n")
        f.write(f"\nBackup: {backup}\n")

    _record_sync_state("ok")
    return SyncResult(
        products=len(kept),
        inserted=inserted,
        deleted=deleted,
        matched=matched_count,
        unmatched=len(unmatched),
        backup=backup,
        report_path=rpt,
        unlocked=unlocked,
    )
