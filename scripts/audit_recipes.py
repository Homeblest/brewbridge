"""Walk every recipe in /Brew.is/ and report per-recipe health.

For each recipe, prints:
    name | style | target OG | target IBU | yeast attn | issues

Issue flags:
    OG!     OG outside style range
    IBU!    target IBU outside style range
    YST0    embedded yeast attenuation = 0 (means BeerSmith hasn't
            re-bound from the library yet; opening the recipe in
            BeerSmith fixes it, or `brewbridge audit --fix` will
            once we extend audit to push yeast attenuation through)
    HOP0    one or more hops have alpha=0 in the recipe (much rarer;
            usually means the original brew.is recipe didn't specify)

Usage::

    python scripts/audit_recipes.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def parse_objects(raw: str) -> list[dict]:
    """Same JSON-or-brace-scan parser as inspect_recipe.py. Returns list
    of dicts from BeerSmith's compact-JSON ingredient blob."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [d for d in v if isinstance(d, dict)]
        if isinstance(v, dict):
            return [v]
    except (json.JSONDecodeError, ValueError):
        pass
    objects, depth, start = [], 0, None
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
    out = []
    for obj in objects:
        d: dict[str, str] = {}
        for k, v in re.findall(r'"([A-Z_0-9]+)":"([^"]*)"', obj):
            d[k] = v
        for k, v in re.findall(r'"([A-Z_0-9]+)":(-?[0-9.]+)(?=[,}])', obj):
            d.setdefault(k, v)
        out.append(d)
    return out


def main():
    db = Path(os.path.expandvars(r"%APPDATA%\BeerSmith4\BeerSmith.sqlite"))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    recipes = list(conn.execute(
        "SELECT _PERMID_, F_R_NAME, F_R_FOLDER_NAME, F_R_STYLE, "
        "F_R_DESIRED_OG, F_R_DESIRED_IBU, F_R_DESIRED_COLOR, Ingredients "
        "FROM M_RECIPE WHERE F_R_FOLDER_NAME LIKE '%Brew.is%' "
        "ORDER BY F_R_NAME"
    ))
    if not recipes:
        sys.exit("No recipes found in /Brew.is/ folder. "
                 "Has the recipe import run yet?")

    print(f"{'Recipe':32s} {'Style':30s} {'OG':>5s} {'IBU':>4s} "
          f"{'YstAtt':>6s} {'Flags':<18s}")
    print("-" * 100)

    schema_to_type = {"7406": "grain", "7403": "hops",
                       "7426": "yeast", "7421": "misc"}

    counts = {"YST0": 0, "HOP0": 0, "OG!": 0, "IBU!": 0, "all_clean": 0}

    for r in recipes:
        name = r["F_R_NAME"]
        og = r["F_R_DESIRED_OG"] or 0
        ibu = r["F_R_DESIRED_IBU"] or 0

        # Parse style range
        style_blob = r["F_R_STYLE"] or ""
        style_name = re.search(r'"F_S_NAME":"([^"]+)"', style_blob)
        s_min_og = re.search(r'"F_S_MIN_OG":"([^"]+)"', style_blob)
        s_max_og = re.search(r'"F_S_MAX_OG":"([^"]+)"', style_blob)
        s_min_ibu = re.search(r'"F_S_MIN_IBU":"([^"]+)"', style_blob)
        s_max_ibu = re.search(r'"F_S_MAX_IBU":"([^"]+)"', style_blob)

        style = style_name.group(1) if style_name else "(no style)"
        og_in_range = True
        ibu_in_range = True
        if s_min_og and s_max_og:
            og_lo, og_hi = float(s_min_og.group(1)), float(s_max_og.group(1))
            og_in_range = og_lo <= og <= og_hi
        if s_min_ibu and s_max_ibu:
            ibu_lo, ibu_hi = float(s_min_ibu.group(1)), float(s_max_ibu.group(1))
            ibu_in_range = ibu_lo <= ibu <= ibu_hi

        # Parse ingredients
        items = parse_objects(r["Ingredients"] or "")
        by_type: dict[str, list[dict]] = {t: [] for t in schema_to_type.values()}
        for it in items:
            t = schema_to_type.get(it.get("_Schema_", ""))
            if t:
                by_type[t].append(it)

        # Yeast attenuation — primary smoke test
        yeast_attn_values = [float(y.get("F_Y_ATTENUATION", 0) or 0)
                              for y in by_type["yeast"]]
        yeast_attn_max = max(yeast_attn_values) if yeast_attn_values else 0.0

        # Hop alphas — anything zero is a flag
        hop_zero_count = sum(
            1 for h in by_type["hops"]
            if float(h.get("F_H_ALPHA", 0) or 0) == 0
        )

        flags = []
        if yeast_attn_max == 0:
            flags.append("YST0")
            counts["YST0"] += 1
        if hop_zero_count > 0:
            flags.append(f"HOP0×{hop_zero_count}")
            counts["HOP0"] += 1
        if not og_in_range:
            flags.append("OG!")
            counts["OG!"] += 1
        if not ibu_in_range:
            flags.append("IBU!")
            counts["IBU!"] += 1
        if not flags:
            counts["all_clean"] += 1

        # Trim long display strings
        disp_name = name[:32]
        disp_style = style[:30]
        print(f"{disp_name:32s} {disp_style:30s} {og:>5.3f} "
              f"{ibu:>4.0f} {yeast_attn_max:>5.1f}%  {' '.join(flags):<18s}")

    print()
    print(f"{len(recipes)} recipes total")
    print(f"  fully clean       : {counts['all_clean']}")
    print(f"  yeast attn = 0    : {counts['YST0']}  (BeerSmith hasn't "
          "rebound from library yet — open recipe to fix, or run audit-fix)")
    print(f"  hop alpha = 0     : {counts['HOP0']}")
    print(f"  OG out of style   : {counts['OG!']}  "
          "(may be intentional — style picker is heuristic)")
    print(f"  IBU out of style  : {counts['IBU!']}  "
          "(same — see Boat Bitter for an example)")

    conn.close()


if __name__ == "__main__":
    main()
