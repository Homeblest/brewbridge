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


def load_spec_reference(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Read or create the frozen spec reference. Built-in BeerSmith ingredient
    rows (those not tagged ``(brew.is)``) are snapshotted to disk on first run
    so that matching keeps working even after we purge the live library."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if REF_PATH.exists():
        with REF_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    refs: dict[str, list[dict]] = {}
    for t, (table, name_col, _) in bs.LIBRARY_TABLE.items():
        refs[t] = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {name_col} NOT LIKE ?",
            (f"%{bs.TAG}",),
        )]
    with REF_PATH.open("w", encoding="utf-8") as f:
        json.dump(refs, f, ensure_ascii=False)
    return refs


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
    """Build a complete column dict for one managed ingredient row, starting
    from the matched built-in row (or the blank template) and overriding
    identity / inventory / supplier / price."""
    row = dict(matched) if matched else dict(template)
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

def run(*, db_path: Path = bs.DEFAULT_DB_PATH, purge_builtins: bool = True,
        report_dir: Path | None = None) -> SyncResult:
    """Execute one sync. Raises RuntimeError if BeerSmith is running.

    purge_builtins: if True, non-(brew.is) ingredient rows are also deleted
    each run, keeping the library brew.is-only (best for a homebrewer whose
    only supplier is brew.is). If False, leaves built-in libraries intact and
    only manages the (brew.is)-tagged rows.
    """
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
