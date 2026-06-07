"""Recipe import + clone-with-substitution.

Two main responsibilities:

1. **Import** brew.is's published recipes (from the curated
   ``recipes_parsed.json`` shipped with brewbridge — extracted once from the
   site's HTML descriptions, since the recipe text format is too inconsistent
   for reliable regex parsing). Each recipe lands in BeerSmith's
   ``/Brew.is/`` folder as a proper M_RECIPE row with embedded ingredients,
   the canonical mash profile, BJCP style, and the original brew.is text in
   notes.

2. **Clone-with-substitution** — given a recipe id and a list of
   ``(original_name, substitute_name)`` pairs, build a copy with the swaps
   applied (every occurrence of the original replaced with the substitute's
   full library spec, preserving the original amount / boil time / use code).
   Returns the modified row dict so the caller can also export a .bsmx file
   for BeerSmith to open directly via Windows file association.

The .bsmx serializer mirrors BeerSmith's archive format byte-for-byte, with
nested ingredient objects, compact JSON, all-string values, and the raw
unescaped inner quotes inside collection ``steps`` containers.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any

from . import beersmith as bs

CLONE_FOLDER = "Brew.is afrit"
IMPORT_FOLDER = "Brew.is"

# Fields preserved from the original ingredient when swapping (amount, use,
# timing flags). Everything else comes from the substitute's library spec.
_PRESERVE = {
    bs.SCHEMA["grain"]: ["F_G_AMOUNT", "F_G_USE", "F_G_USE_SET", "F_G_IN_RECIPE"],
    bs.SCHEMA["hops"]:  ["F_H_AMOUNT", "F_H_USE", "F_H_BOIL_TIME",
                          "F_H_DRY_HOP_TIME", "F_H_IN_RECIPE"],
    bs.SCHEMA["yeast"]: ["F_Y_AMOUNT", "F_Y_NEW_AMOUNT", "F_Y_USE_PKGS",
                          "F_Y_IN_RECIPE"],
    bs.SCHEMA["misc"]:  ["F_M_AMOUNT", "F_M_USE", "F_M_TIME", "F_M_IN_RECIPE"],
}

_NAME_FIELD = {
    bs.SCHEMA["grain"]: "F_G_NAME",
    bs.SCHEMA["hops"]:  "F_H_NAME",
    bs.SCHEMA["yeast"]: "F_Y_NAME",
    bs.SCHEMA["misc"]:  "F_M_NAME",
}

_SCHEMA_TYPE = {
    bs.SCHEMA["grain"]: "grain",
    bs.SCHEMA["hops"]:  "hops",
    bs.SCHEMA["yeast"]: "yeast",
    bs.SCHEMA["misc"]:  "misc",
}


# ---------------------------------------------------------------------------
# Clone-with-substitution
# ---------------------------------------------------------------------------

def _swap_ingredient(sub_library_row: dict, original_embedded: dict,
                     clean_name: str) -> dict:
    """Embed a substitute: copy the substitute's library spec into a new
    embedded ingredient dict, then re-apply the original's amount/use/timing
    fields. ``clean_name`` is the brew.is product name without our ``(brew.is)``
    suffix — looks better in the recipe view."""
    skip = {"_PERMID_", "_MOD_", "_CLOUDID_", "_EXTRA_",
            "_CLOUD_STATE_", "F_ORDER"}
    schema = original_embedded["_Schema_"]
    out: dict[str, Any] = {"_Schema_": schema}
    for k, v in sub_library_row.items():
        if k in skip:
            continue
        out[k] = bs.fmt(v)
    out[_NAME_FIELD[schema]] = clean_name
    for k in _PRESERVE.get(schema, []):
        if k in original_embedded:
            out[k] = original_embedded[k]
    return out


def clone_recipe(conn: sqlite3.Connection, recipe_id: int,
                 swaps: list[tuple[str, str]]):
    """Insert a clone of recipe ``recipe_id`` with ``swaps`` applied.

    ``swaps`` = list of (original_ingredient_name, brewis_substitute_name).
    Every occurrence of ``original_name`` in the recipe's Ingredients gets
    replaced with the substitute's spec.

    Returns ``(new_permid, new_name, applied, missed, new_row_dict)`` —
    ``applied`` is ``[(orig, sub, count_of_replacements), ...]``; ``missed``
    is ``[(orig, sub, reason), ...]``; ``new_row_dict`` is the inserted row
    so the caller can render it to .bsmx without re-reading from the DB
    (BeerSmith may clobber the insert if it's open)."""
    cur = conn.cursor()
    orig = cur.execute("SELECT * FROM M_RECIPE WHERE _PERMID_=?",
                       (recipe_id,)).fetchone()
    if not orig:
        raise RuntimeError(f"Recipe id {recipe_id} not found")

    ings = json.loads(orig["Ingredients"]) if orig["Ingredients"] else []
    applied: list[tuple[str, str, int]] = []
    missed: list[tuple[str, str, str]] = []

    for orig_name, sub_name in swaps:
        replaced = 0
        sub_row_dict = None
        for idx, ing in enumerate(ings):
            schema = ing.get("_Schema_")
            if schema not in _NAME_FIELD:
                continue
            if ing.get(_NAME_FIELD[schema]) != orig_name:
                continue
            if sub_row_dict is None:
                t = _SCHEMA_TYPE[schema]
                table, name_col, _ = bs.LIBRARY_TABLE[t]
                # Try exact tag suffix first ("Saaz (brew.is)"), then no-tag
                row = cur.execute(
                    f"SELECT * FROM {table} WHERE {name_col} IN (?, ?)",
                    (sub_name + bs.TAG, sub_name),
                ).fetchone()
                if not row:
                    missed.append((orig_name, sub_name, "not in library"))
                    break
                sub_row_dict = dict(row)
            ings[idx] = _swap_ingredient(sub_row_dict, ing, sub_name)
            replaced += 1
        if replaced:
            applied.append((orig_name, sub_name, replaced))
        elif sub_row_dict is None and not any(m[0] == orig_name for m in missed):
            missed.append((orig_name, sub_name, "original not in recipe"))

    if not applied:
        raise RuntimeError("No substitutions applied: " + "; ".join(
            f"{o}->{s} ({why})" for o, s, why in missed))

    folder_id = bs.ensure_folder(conn, CLONE_FOLDER)
    now_s = str(int(dt.datetime.now().timestamp()))
    new_row = {k: orig[k] for k in orig.keys()}
    new_row.pop("_PERMID_", None)
    suffix = " + ".join(s for _, s, _ in applied)[:60]
    new_row.update({
        "_MOD_": now_s, "_CLOUDID_": 0, "_EXTRA_": 0,
        "F_R_NAME": f"{orig['F_R_NAME']} (afrit: {suffix})",
        "F_R_FOLDER_NAME": f"/{CLONE_FOLDER}/",
        "F_R_PARENT": folder_id,
        "F_R_DATE": int(dt.datetime.now().timestamp()),
        "Ingredients": bs.compact_json(ings),
    })
    # Don't carry over "measured" flags from the parent (this is a fresh design).
    for k in ("F_R_OG_MEASURED_SET", "F_R_FG_MEASURED_SET",
              "F_R_VOLUME_MEASURED_SET", "F_R_FINAL_VOL_MEASURED_SET"):
        if k in new_row:
            new_row[k] = 0

    cols = list(new_row.keys())
    ph = ",".join("?" for _ in cols)
    # Build the column-list string with explicit concatenation rather
    # than nested f-strings with backslashes — Python 3.10 / 3.11 don't
    # allow backslashes inside f-string expressions, and we claim
    # >=3.10 in pyproject.toml.
    quoted_cols = ",".join('"' + c + '"' for c in cols)
    cur.execute(
        f"INSERT INTO M_RECIPE ({quoted_cols}) VALUES ({ph})",
        [new_row[c] for c in cols],
    )
    new_id = cur.lastrowid
    conn.commit()
    new_row["_PERMID_"] = new_id
    return new_id, new_row["F_R_NAME"], applied, missed, new_row


# ---------------------------------------------------------------------------
# .bsmx serializer — matches BeerSmith's native archive format exactly
# ---------------------------------------------------------------------------

# Per-collection metadata BeerSmith expects on container wrappers (steps,
# Ingredients, AgeData). Values come from inspecting archive .bsmx files.
_CONTAINER = {
    "Ingredients": {"Type": "7405", "TID": "7182", "_XName": "Ingredients"},
    "steps":       {"Type": "7432", "TID": "7149", "_XName": "steps"},
    "AgeData":     {"Type": "7482", "TID": "7184", "_XName": "AgeData"},
}
_OUTER = {"Type": "7372", "TID": "7372", "_XName": "Recipes"}
_SCHEMA_ELEMENT = {bs.SCHEMA["grain"]: "Grain",
                    bs.SCHEMA["hops"]:  "Hops",
                    bs.SCHEMA["yeast"]: "Yeast",
                    bs.SCHEMA["misc"]:  "Misc",
                    bs.SCHEMA["mash_step"]: "MashStep"}
_JSON_OBJECT_COLS = {"F_R_STYLE", "F_R_MASH", "F_R_EQUIPMENT",
                      "F_R_BASE_GRAIN", "F_R_CARB", "F_R_AGE"}
_JSON_ARRAY_COLS = {"Ingredients", "AgeData"}
_DATE_COLS = {"F_R_DATE", "F_R_INV_DATE"}


def _bsmx_date(v) -> str:
    try:
        ts = int(v)
        if ts == 0:
            return "1970-01-01 00:00:00"
        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return "2026-01-01 00:00:00"


def _emit_container(parent, tag: str, items):
    import xml.etree.ElementTree as ET
    meta = _CONTAINER.get(tag, {"Type": "0", "TID": "0", "_XName": tag})
    elem = ET.SubElement(parent, tag)
    now = _bsmx_date(int(dt.datetime.now().timestamp()))
    for k, v in (("_PERMID_", "0"), ("_MOD_", now), ("Name", meta["_XName"]),
                  ("Type", meta["Type"]), ("Dirty", "1"), ("Owndata", "1"),
                  ("TID", meta["TID"]), ("Size", str(len(items or []))),
                  ("_XName", meta["_XName"]), ("Allocinc", "32")):
        ET.SubElement(elem, k).text = v
    data = ET.SubElement(elem, "Data")
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        item_tag = _SCHEMA_ELEMENT.get(str(item.get("_Schema_", "")),
                                        tag.rstrip("s") or "Item")
        _emit_object(data, item, item_tag)
    for k, v in (("_TExpanded", "1"), ("TExtra", "0"), ("TxLog", "0"),
                  ("PermCount", "0"), ("TxCount", "0"), ("TxTable", "0"),
                  ("TxPath", "")):
        ET.SubElement(elem, k).text = v


def _emit_object(parent, obj: dict, tag: str):
    import xml.etree.ElementTree as ET
    elem = ET.SubElement(parent, tag)
    for k, v in obj.items():
        if k == "_Schema_":
            continue
        if k == "_MOD_" and isinstance(v, str) and v.strip().isdigit():
            ET.SubElement(elem, k).text = _bsmx_date(v)
            continue
        if isinstance(v, str) and v.strip().startswith("["):
            try:
                parsed = json.loads(v)
                _emit_container(elem, k, parsed)
                continue
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(v, dict):
            _emit_object(elem, v, k)
        elif isinstance(v, list):
            _emit_container(elem, k, v)
        else:
            ET.SubElement(elem, k).text = bs.fmt(v)


def recipe_to_bsmx(row: dict) -> str:
    """Serialize a recipe row dict to BeerSmith .bsmx XML. Doing
    ``os.startfile(path)`` on the result opens the recipe directly in
    BeerSmith via the registered file association — no DB write, no restart."""
    import xml.etree.ElementTree as ET
    now = _bsmx_date(int(dt.datetime.now().timestamp()))
    root = ET.Element("Recipe")
    for k, v in (("_PERMID_", "0"), ("_MOD_", now), ("Name", _OUTER["_XName"]),
                  ("Type", _OUTER["Type"]), ("Dirty", "1"), ("Owndata", "0"),
                  ("TID", _OUTER["TID"]), ("Size", "1"),
                  ("_XName", _OUTER["_XName"]), ("Allocinc", "32")):
        ET.SubElement(root, k).text = v
    data = ET.SubElement(root, "Data")
    rec = ET.SubElement(data, "Recipe")
    skip = {"_PERMID_", "_CLOUDID_", "_EXTRA_"}
    for k in row.keys():
        if k in skip:
            continue
        v = row[k]
        if k == "_MOD_":
            ET.SubElement(rec, "_PERMID_").text = "0"
            ET.SubElement(rec, "_MOD_").text = _bsmx_date(v)
            continue
        if k in _DATE_COLS:
            ET.SubElement(rec, k).text = _bsmx_date(v); continue
        if k in _JSON_OBJECT_COLS and isinstance(v, str) and v.strip():
            try:
                _emit_object(rec, json.loads(v), k); continue
            except (json.JSONDecodeError, TypeError):
                pass
        if k in _JSON_ARRAY_COLS and isinstance(v, str) and v.strip():
            try:
                _emit_container(rec, k, json.loads(v)); continue
            except (json.JSONDecodeError, TypeError):
                pass
        ET.SubElement(rec, k).text = bs.fmt(v)
    for k, v in (("_TExpanded", "1"), ("TExtra", "0"), ("TxLog", "0"),
                  ("PermCount", "0"), ("TxCount", "0"), ("TxTable", "0"),
                  ("TxPath", "")):
        ET.SubElement(root, k).text = v
    return ET.tostring(root, encoding="unicode")
