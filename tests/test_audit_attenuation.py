"""Test for fix_yeast_attenuation — the audit fix that pushes library
yeast attenuation into recipes whose embedded copy is still 0.

This is the fix for the bug where all 25 imported brew.is recipes
showed `YST0` in the recipe-audit survey: BeerSmith only re-binds
library values into recipes when a user OPENS each recipe in the UI,
so 25 recipes meant 25 manual opens. fix_yeast_attenuation does the
same thing programmatically.
"""
from __future__ import annotations

import json
import sqlite3

from brewbridge.core import audit, beersmith as bs


def _set_up_db(yeast_lib_rows: list[tuple],
               recipes: list[tuple[int, str, list[dict]]]):
    """Build an in-memory BeerSmith-shaped DB with given M_YEAST library
    rows and M_RECIPE rows.

    yeast_lib_rows: list of either
        (name, attenuation)               — convenience; sets min==max
        (name, min_atten, max_atten)      — explicit range

    recipes: list of (permid, folder, ingredients_list).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Real BeerSmith schema: attenuation stored as min/max range.
    # Recipe-embedded yeast uses a single F_Y_ATTENUATION; we compute
    # the midpoint when pushing library -> recipe.
    conn.execute("""CREATE TABLE M_YEAST (
        _PERMID_ INTEGER PRIMARY KEY,
        F_Y_NAME TEXT,
        F_Y_MIN_ATTENUATION REAL,
        F_Y_MAX_ATTENUATION REAL
    )""")
    conn.execute("""CREATE TABLE M_RECIPE (
        _PERMID_ INTEGER PRIMARY KEY,
        F_R_FOLDER_NAME TEXT,
        Ingredients TEXT,
        _MOD_ TEXT
    )""")
    for i, row in enumerate(yeast_lib_rows):
        if len(row) == 2:
            name, atten = row
            min_a, max_a = atten, atten
        else:
            name, min_a, max_a = row
        conn.execute(
            "INSERT INTO M_YEAST VALUES (?, ?, ?, ?)",
            (i + 1, name, min_a, max_a)
        )
    for permid, folder, ings in recipes:
        conn.execute(
            "INSERT INTO M_RECIPE VALUES (?, ?, ?, '0')",
            (permid, folder, json.dumps(ings))
        )
    conn.commit()
    return conn


def test_zero_attenuation_yeast_gets_rebound_from_library():
    """The canonical case: recipe imported with attenuation=0 because
    library was empty at import time. Library has the value now. Push
    it through."""
    conn = _set_up_db(
        yeast_lib_rows=[("SafAle English Ale (brew.is)", 75.0)],
        recipes=[(1, "/Brew.is/", [
            {"_Schema_": bs.SCHEMA["yeast"],
             "F_Y_NAME": "SafAle English Ale (brew.is)",
             "F_Y_ATTENUATION": "0"},
        ])],
    )
    fixed = audit.fix_yeast_attenuation(conn)
    assert fixed == 1

    # Verify the embedded value got updated
    raw = conn.execute(
        "SELECT Ingredients FROM M_RECIPE WHERE _PERMID_=1"
    ).fetchone()[0]
    yeast = json.loads(raw)[0]
    assert float(yeast["F_Y_ATTENUATION"]) > 0
    assert abs(float(yeast["F_Y_ATTENUATION"]) - 75.0) < 0.001


def test_recipe_with_non_brewis_tag_still_matches_library():
    """Embedded yeast might omit the (brew.is) tag (e.g. user copied a
    recipe). The library has the tagged variant. The match still works
    via the tag-stripped lookup."""
    conn = _set_up_db(
        yeast_lib_rows=[("Safale American (brew.is)", 81.0)],
        recipes=[(1, "/Brew.is/", [
            {"_Schema_": bs.SCHEMA["yeast"],
             "F_Y_NAME": "Safale American",
             "F_Y_ATTENUATION": "0"},
        ])],
    )
    fixed = audit.fix_yeast_attenuation(conn)
    assert fixed == 1


def test_non_zero_attenuation_is_respected_as_user_override():
    """If a recipe's embedded yeast already has a non-zero attenuation,
    we treat it as a deliberate user override and don't touch it —
    even if the library has a different value."""
    conn = _set_up_db(
        yeast_lib_rows=[("SafAle English Ale (brew.is)", 75.0)],
        recipes=[(1, "/Brew.is/", [
            {"_Schema_": bs.SCHEMA["yeast"],
             "F_Y_NAME": "SafAle English Ale (brew.is)",
             "F_Y_ATTENUATION": "82.5"},   # user dialed it up
        ])],
    )
    fixed = audit.fix_yeast_attenuation(conn)
    assert fixed == 0   # didn't touch anything

    raw = conn.execute(
        "SELECT Ingredients FROM M_RECIPE WHERE _PERMID_=1"
    ).fetchone()[0]
    yeast = json.loads(raw)[0]
    assert float(yeast["F_Y_ATTENUATION"]) == 82.5


def test_library_with_zero_attenuation_is_skipped():
    """If the library itself has attenuation=0 for a yeast, there's
    nothing useful to push — skip rather than zeroing a recipe.
    Important for the fresh-install case where the spec_reference
    snapshot hasn't kicked in yet."""
    conn = _set_up_db(
        yeast_lib_rows=[("Unmatched Yeast (brew.is)", 0.0)],
        recipes=[(1, "/Brew.is/", [
            {"_Schema_": bs.SCHEMA["yeast"],
             "F_Y_NAME": "Unmatched Yeast (brew.is)",
             "F_Y_ATTENUATION": "0"},
        ])],
    )
    fixed = audit.fix_yeast_attenuation(conn)
    assert fixed == 0


def test_empty_library_returns_zero_no_crash():
    """No usable library data at all. Don't crash, just no-op."""
    conn = _set_up_db(
        yeast_lib_rows=[],
        recipes=[(1, "/Brew.is/", [
            {"_Schema_": bs.SCHEMA["yeast"],
             "F_Y_NAME": "Whatever",
             "F_Y_ATTENUATION": "0"},
        ])],
    )
    assert audit.fix_yeast_attenuation(conn) == 0


def test_multiple_recipes_only_changed_ones_counted():
    """One recipe needs the fix, one already has correct attenuation.
    Only the changed one shows up in the count."""
    conn = _set_up_db(
        yeast_lib_rows=[
            ("SafAle English Ale (brew.is)", 75.0),
            ("Safale American (brew.is)", 81.0),
        ],
        recipes=[
            (1, "/Brew.is/", [
                {"_Schema_": bs.SCHEMA["yeast"],
                 "F_Y_NAME": "SafAle English Ale (brew.is)",
                 "F_Y_ATTENUATION": "0"},   # needs fix
            ]),
            (2, "/Brew.is/", [
                {"_Schema_": bs.SCHEMA["yeast"],
                 "F_Y_NAME": "Safale American (brew.is)",
                 "F_Y_ATTENUATION": "81.0"},   # already correct
            ]),
        ],
    )
    assert audit.fix_yeast_attenuation(conn) == 1


def test_library_range_is_averaged_into_recipe_midpoint():
    """The library stores attenuation as a min/max range. The recipe
    side wants a single value — we use the midpoint. Verifies the
    formula for a typical ale-yeast range (e.g. SafAle English Ale
    runs 71-75%; recipe should land at 73%)."""
    conn = _set_up_db(
        yeast_lib_rows=[("SafAle English Ale (brew.is)", 71.0, 75.0)],
        recipes=[(1, "/Brew.is/", [
            {"_Schema_": bs.SCHEMA["yeast"],
             "F_Y_NAME": "SafAle English Ale (brew.is)",
             "F_Y_ATTENUATION": "0"},
        ])],
    )
    assert audit.fix_yeast_attenuation(conn) == 1
    raw = conn.execute(
        "SELECT Ingredients FROM M_RECIPE WHERE _PERMID_=1"
    ).fetchone()[0]
    yeast = json.loads(raw)[0]
    assert abs(float(yeast["F_Y_ATTENUATION"]) - 73.0) < 0.01


def test_only_brewis_folder_is_touched():
    """Recipes outside /Brew.is/ (e.g. user's own personal recipes)
    are left strictly alone. We only manage imported brew.is recipes."""
    conn = _set_up_db(
        yeast_lib_rows=[("SafAle English Ale (brew.is)", 75.0)],
        recipes=[
            (1, "/My Recipes/", [          # not in /Brew.is/
                {"_Schema_": bs.SCHEMA["yeast"],
                 "F_Y_NAME": "SafAle English Ale (brew.is)",
                 "F_Y_ATTENUATION": "0"},
            ]),
        ],
    )
    assert audit.fix_yeast_attenuation(conn) == 0
