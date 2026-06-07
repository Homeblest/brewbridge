"""Tests for the spec_reference.json resolution ladder.

The contract:

    1. If ~/.brewbridge/specs_reference.json exists AND has rows → use it.
    2. Else snapshot the user's live BeerSmith library (non-(brew.is)
       rows) → use that AND persist it locally.
    3. Else fall back to the bundled package asset → use that AND
       persist it locally so step 1 hits next time.

Why this is worth pinning: the first version of this function had a
bug where step 1 always won, even on an empty file — which is exactly
the bug the user hit (matched specs 0/112). The fix added the
"is_empty_snapshot" guard. Easy to forget the guard during a refactor;
this test catches that.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from brewbridge.core import sync as bb_sync
from brewbridge.core import beersmith as bs


def _make_conn_with(library_rows: dict[str, list[tuple[str, ...]]]):
    """Build an in-memory SQLite with the bare minimum schema for
    load_spec_reference: M_GRAIN/M_HOPS/M_YEAST/M_MISC each with their
    name column + _PERMID_, populated with given rows."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for t, (table, name_col, _) in bs.LIBRARY_TABLE.items():
        # bs.SCHEMA gives the schema code; we just need a stand-in table.
        conn.execute(f"CREATE TABLE {table} (_PERMID_ INTEGER, {name_col} TEXT)")
        for i, (name,) in enumerate(library_rows.get(t, [])):
            conn.execute(f"INSERT INTO {table} VALUES (?, ?)", (i + 1, name))
    conn.commit()
    return conn


def test_existing_populated_snapshot_wins(tmp_path, monkeypatch):
    """If ~/.brewbridge/specs_reference.json has rows, just use it.
    Don't snapshot the live DB, don't touch the bundled asset."""
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "REF_PATH", tmp_path / "specs_reference.json")

    existing = {"grain": [{"F_G_NAME": "Maris Otter"}],
                "hops":  [{"F_H_NAME": "Cascade"}],
                "yeast": [],
                "misc":  []}
    (tmp_path / "specs_reference.json").write_text(
        json.dumps(existing), encoding="utf-8")

    # Live DB has different content — should NOT be consulted.
    conn = _make_conn_with({"grain": [("Pilsner",)], "hops": [("Saaz",)]})
    result = bb_sync.load_spec_reference(conn)
    assert result == existing


def test_empty_snapshot_is_ignored_and_live_db_is_used(tmp_path, monkeypatch):
    """The bug we hit in production: a 0/0/0/0 snapshot file shouldn't
    short-circuit the lookup. Re-snapshot from the live DB."""
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "REF_PATH", tmp_path / "specs_reference.json")

    empty = {"grain": [], "hops": [], "yeast": [], "misc": []}
    (tmp_path / "specs_reference.json").write_text(
        json.dumps(empty), encoding="utf-8")

    conn = _make_conn_with({"grain": [("Pilsner",)], "hops": [("Saaz",)]})
    result = bb_sync.load_spec_reference(conn)
    # Should now contain Pilsner + Saaz from the live DB
    assert len(result["grain"]) == 1
    assert result["grain"][0]["F_G_NAME"] == "Pilsner"
    assert len(result["hops"]) == 1
    assert result["hops"][0]["F_H_NAME"] == "Saaz"

    # AND the snapshot file should have been overwritten so step 1
    # hits next time.
    persisted = json.loads((tmp_path / "specs_reference.json").read_text(
        encoding="utf-8"))
    assert len(persisted["grain"]) == 1


def test_bundled_asset_fallback_when_everything_empty(tmp_path, monkeypatch):
    """No local file, live DB empty (already purged) → use bundled
    fallback. Persist it locally for next time."""
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "REF_PATH", tmp_path / "specs_reference.json")

    bundled = tmp_path / "bundled.json"
    bundled.write_text(json.dumps({
        "grain": [{"F_G_NAME": "Bundled Pale"}],
        "hops":  [{"F_H_NAME": "Bundled Citra"}],
        "yeast": [{"F_Y_NAME": "Bundled US-05"}],
        "misc":  [],
    }), encoding="utf-8")
    monkeypatch.setattr(bb_sync, "_BUNDLED_REF", bundled)

    # Live DB is empty — simulates a user whose library was already
    # purged by an old version of brewbridge.
    conn = _make_conn_with({})
    result = bb_sync.load_spec_reference(conn)
    assert result["grain"][0]["F_G_NAME"] == "Bundled Pale"

    # Persisted locally so future runs are faster
    assert (tmp_path / "specs_reference.json").exists()


def test_missing_bundled_asset_raises_explicitly(tmp_path, monkeypatch):
    """If nothing's available anywhere, fail loud — silent zero-match
    is the bug we're avoiding."""
    monkeypatch.setattr(bb_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bb_sync, "REF_PATH", tmp_path / "specs_reference.json")
    monkeypatch.setattr(bb_sync, "_BUNDLED_REF", tmp_path / "does_not_exist.json")

    conn = _make_conn_with({})
    with pytest.raises(RuntimeError, match="No spec reference available"):
        bb_sync.load_spec_reference(conn)


def test_bundled_asset_actually_ships():
    """Belt-and-suspenders: verify the committed asset file really exists
    and has meaningful row counts. Catches a misconfigured wheel/MSI
    that forgets to bundle the file."""
    assert bb_sync._BUNDLED_REF.exists(), (
        f"bundled asset missing: {bb_sync._BUNDLED_REF}")
    data = json.loads(bb_sync._BUNDLED_REF.read_text(encoding="utf-8"))
    # Generous lower bounds — the real file has 142/464/538/129 but if
    # someone replaces it with a sparser snapshot we want the test to
    # still pass as long as the file is meaningfully populated.
    assert len(data["grain"]) > 50
    assert len(data["hops"]) > 100
    assert len(data["yeast"]) > 50
