"""One-off diagnostic: dump everything we'd want to see for a recipe.

BeerSmith v4 stores recipe ingredients embedded as JSON-in-text inside
M_RECIPE columns (F_R_HOPS, F_R_GRAINS, F_R_YEAST, F_R_MISC) — same
compact-JSON-with-unescaped-quotes format we already deal with for
F_R_MASH. This script parses those and prints the rendered ingredient
list, plus interesting calculated stats.

Usage::

    python scripts/inspect_recipe.py "Boat"
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path


def _parse_inner_json(raw: str) -> list[dict]:
    """Parse a BeerSmith ingredient-array column.

    Three strategies, fastest-to-most-forgiving:

    1. Try ``json.loads(raw)``. The outer array in the ``Ingredients``
       column appears to be valid JSON; if so, this gives a clean list.

    2. Fall back to a brace-depth-tracking object scanner. Collects every
       top-level ``{...}`` substring (handles nested objects). Then runs
       a regex over each to pull ``"KEY":"value"`` pairs we care about.
       The regex-after-extract approach tolerates unescaped inner quotes
       inside values, which is BeerSmith's documented quirk for some
       fields (F_R_MASH.steps especially).

    3. Empty input → empty list.
    """
    if not raw or raw in ("0", "[]", "{}"):
        return []

    # Strategy 1: it might just be clean JSON
    import json
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [d for d in parsed if isinstance(d, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: brace-depth scanner. Only captures top-level objects;
    # nested {} inside a value (e.g. an embedded sub-record) is skipped.
    objects: list[str] = []
    depth = 0
    start: int | None = None
    for i, c in enumerate(raw):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(raw[start:i + 1])
                start = None

    out: list[dict] = []
    for obj in objects:
        d: dict[str, str] = {}
        for k, v in re.findall(r'"([A-Z_0-9]+)":"([^"]*)"', obj):
            d[k] = v
        # Also numeric values: "F_H_AMOUNT":0.0625
        for k, v in re.findall(r'"([A-Z_0-9]+)":(-?[0-9.]+)(?=[,}])', obj):
            d.setdefault(k, v)
        out.append(d)
    return out


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/inspect_recipe.py <name-fragment>")
    name_frag = sys.argv[1]

    db = Path(os.path.expandvars(r"%APPDATA%\BeerSmith4\BeerSmith.sqlite"))
    if not db.exists():
        sys.exit(f"not found: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    r = conn.execute(
        "SELECT * FROM M_RECIPE WHERE F_R_NAME LIKE ?",
        (f"%{name_frag}%",),
    ).fetchone()
    if r is None:
        sys.exit(f"no recipe matching {name_frag!r}")

    print(f"Recipe: {r['F_R_NAME']}  (permid {r['_PERMID_']})")
    print(f"Folder: {r['F_R_FOLDER_NAME']}")
    print(f"Type:   {r['F_R_TYPE']}  "
          "(0=Extract, 1=Partial Mash, 2=All Grain)")
    print(f"Volume: {(r['F_R_OLD_VOL'] or 0)/33.814:.1f} L (final), "
          f"{(r['F_R_OLD_BOIL_VOL'] or 0)/33.814:.1f} L (pre-boil)")
    print()

    # Style block — pull just the headline from the embedded JSON
    style = r["F_R_STYLE"] or ""
    # Tolerant regex: F_R_STYLE may be compact JSON ("key":"value") or
    # standard JSON ("key": "value") depending on which writer last
    # touched it. Accept any whitespace around the colon.
    name = re.search(r'"F_S_NAME"\s*:\s*"([^"]+)"', style)
    cat = re.search(r'"F_S_CATEGORY"\s*:\s*"([^"]+)"', style)
    min_ibu = re.search(r'"F_S_MIN_IBU"\s*:\s*"([^"]+)"', style)
    max_ibu = re.search(r'"F_S_MAX_IBU"\s*:\s*"([^"]+)"', style)
    min_og = re.search(r'"F_S_MIN_OG"\s*:\s*"([^"]+)"', style)
    max_og = re.search(r'"F_S_MAX_OG"\s*:\s*"([^"]+)"', style)
    if name:
        print(f"Style:  {name.group(1)} ({cat.group(1) if cat else '?'})")
        if min_ibu and max_ibu:
            print(f"        IBU range: {float(min_ibu.group(1)):.0f} – "
                  f"{float(max_ibu.group(1)):.0f}")
        if min_og and max_og:
            print(f"        OG  range: {float(min_og.group(1)):.3f} – "
                  f"{float(max_og.group(1)):.3f}")
        print(f"        Recipe target IBU: {r['F_R_DESIRED_IBU']:.1f}")
        print(f"        Recipe target OG:  {r['F_R_DESIRED_OG']:.3f}")
    print()

    # BeerSmith v4 stores recipe ingredients as one big JSON-in-text blob
    # in a column literally called "Ingredients" (mixed case, no F_ prefix
    # — odd; everything else is F_X_*). The blob is an array of objects,
    # each with a "_Schema_" tag identifying its type:
    #
    #     7406 = grain   (M_GRAIN row format)
    #     7403 = hops    (M_HOPS row format)
    #     7426 = yeast   (M_YEAST row format)
    #     7421 = misc    (M_MISC row format)
    #
    # We pull the column, parse it, and group by schema.
    raw = r["Ingredients"]
    items = _parse_inner_json(raw or "")
    if not items and raw:
        print(f"[debug] Ingredients column has {len(raw)} chars but parsed "
              f"to 0 items. First 300 chars:")
        print(f"  {raw[:300]!r}")
        print()

    schema_to_type = {"7406": "grain", "7403": "hops",
                       "7426": "yeast", "7421": "misc"}
    by_type: dict[str, list[dict]] = {t: [] for t in schema_to_type.values()}
    for it in items:
        t = schema_to_type.get(it.get("_Schema_", ""))
        if t:
            by_type[t].append(it)

    # ---- Grain bill ----
    print("Grain bill:")
    total_oz = 0.0
    for g in by_type["grain"]:
        name_g = g.get("F_G_NAME", "?")
        amt_oz = float(g.get("F_G_AMOUNT", 0) or 0)
        color = float(g.get("F_G_COLOR", 0) or 0)
        yield_pct = float(g.get("F_G_YIELD", 0) or 0)
        total_oz += amt_oz
        print(f"  {name_g:35s} {amt_oz/35.274:>5.2f} kg  "
              f"color={color:>5.1f}  yield={yield_pct:>5.1f}%")
    if by_type["grain"]:
        print(f"  -- total: {total_oz/35.274:.2f} kg --")
    print()

    # ---- Hop bill — the IBU question ----
    print("Hop bill:")
    use_names = {"0": "Boil", "1": "DryHop", "2": "Mash",
                  "3": "FWH", "4": "Aroma", "5": "Whirlpool"}
    for h in by_type["hops"]:
        name_h = h.get("F_H_NAME", "?")
        amt_oz = float(h.get("F_H_AMOUNT", 0) or 0)
        alpha = float(h.get("F_H_ALPHA", 0) or 0)
        use = h.get("F_H_USE", "?")
        boil_t = float(h.get("F_H_BOIL_TIME", 0) or 0)
        dry_t = float(h.get("F_H_DRY_HOP_TIME", 0) or 0)
        use_str = use_names.get(use, f"?{use}")
        time_str = f"DH {dry_t:.0f}d" if use == "1" else f"{boil_t:.0f}m"
        print(f"  {name_h:30s} {amt_oz*28.35:>6.1f} g  "
              f"α={alpha:>5.2f}%  use={use_str:>9s} {time_str:>6s}")
    if (by_type["hops"]
            and all(float(h.get("F_H_ALPHA", 0) or 0) == 0
                    for h in by_type["hops"])):
        print()
        print("*** Every hop has alpha=0 — that's the spec_reference seeding")
        print("*** bug (task #30). Fix that before drawing conclusions.")
    print()

    # ---- Yeast ----
    print("Yeast:")
    for y in by_type["yeast"]:
        name_y = y.get("F_Y_NAME", "?")
        atten = float(y.get("F_Y_ATTENUATION", 0) or 0)
        amt = float(y.get("F_Y_AMOUNT", 0) or 0)
        print(f"  {name_y:30s}  attn={atten:.1f}%  amount={amt}")
    print()

    # ---- Misc ----
    if by_type["misc"]:
        print("Misc:")
        for m in by_type["misc"]:
            print(f"  {m.get('F_M_NAME', '?')}  "
                  f"amount={m.get('F_M_AMOUNT', '?')}  "
                  f"use={m.get('F_M_USE', '?')}")

    conn.close()


if __name__ == "__main__":
    main()
