"""Tests for orders.find_recipe — preventing the v1-shadows-v3 bug.

The bug: when a recipe family has names like "Bölvað Bull" / "v2" / "v3",
clicking the order button on v3 used to route to the original "Bölvað Bull"
because `mm.similarity` clamps at 1.0 — the +0.15 substring bonus pushes
prefix matches up to a tie with the genuine exact match, and iteration
order broke the tie wrongly.

Fix: exact normalised-name match takes priority over similarity. These
tests pin that contract.
"""
from __future__ import annotations

import sqlite3

from brewbridge.core import orders


def _setup(rows: list[tuple[int, str]]) -> sqlite3.Connection:
    """In-memory DB with the M_RECIPE columns find_recipe actually
    queries. Returns connection with row_factory set."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE M_RECIPE (_PERMID_ INTEGER PRIMARY KEY, F_R_NAME TEXT)"
    )
    for permid, name in rows:
        conn.execute("INSERT INTO M_RECIPE VALUES (?, ?)", (permid, name))
    conn.commit()
    return conn


def test_exact_match_wins_over_prefix_sibling():
    """The canonical bug: 'Bölvað Bull' is a prefix of 'Bölvað Bull v3',
    so similarity() ties them at 1.0. With the lower _PERMID_ inserted
    first, the original used to win lookups for v3. Exact-match-first
    fixes it."""
    conn = _setup([
        (41, "Bölvað Bull"),
        (42, "Bölvað Bull v2"),
        (43, "Bölvað Bull v3"),
    ])
    assert orders.find_recipe(conn, "Bölvað Bull v3")["_PERMID_"] == 43
    assert orders.find_recipe(conn, "Bölvað Bull v2")["_PERMID_"] == 42
    # The unsuffixed name should still find the unsuffixed recipe — we
    # haven't broken that case by adding the exact-match step.
    assert orders.find_recipe(conn, "Bölvað Bull")["_PERMID_"] == 41


def test_exact_match_is_case_and_diacritic_insensitive():
    """URL-encoded names from BeerSmith reports can arrive with stripped
    diacritics or weird casing. norm() handles both."""
    conn = _setup([(1, "Bölvað Bull v3")])
    # Lowercase, no diacritics — all should hit the v3 recipe
    assert orders.find_recipe(conn, "bolvad bull v3")["_PERMID_"] == 1
    assert orders.find_recipe(conn, "BÖLVAÐ BULL V3")["_PERMID_"] == 1
    assert orders.find_recipe(conn, "Bolvad Bull V3")["_PERMID_"] == 1


def test_numeric_id_takes_absolute_priority():
    """A pure numeric ident still routes by permid even if a recipe
    name happens to match the digits in some weird way."""
    conn = _setup([
        (42, "Recipe Forty-Two"),
        (43, "42"),  # name is literally "42"
    ])
    # ident=42 means permid 42, not the recipe named "42"
    assert orders.find_recipe(conn, 42)["_PERMID_"] == 42
    # Likewise the string "42"
    assert orders.find_recipe(conn, "42")["_PERMID_"] == 42


def test_fuzzy_fallback_still_works_for_partial_typos():
    """If no exact match exists, similarity falls back. This must still
    work — the fix shouldn't break typo tolerance."""
    conn = _setup([(1, "Belgian Blonde")])
    # Slight misspelling — exact match fails, similarity catches it
    r = orders.find_recipe(conn, "Belgian Blone")
    assert r is not None and r["_PERMID_"] == 1


def test_no_match_returns_none():
    """Below the 0.6 threshold and no exact match → None, so the
    caller can present an honest error rather than a wrong-recipe
    surprise."""
    conn = _setup([(1, "Saison DuBle")])
    assert orders.find_recipe(conn, "completely different name xyz") is None
