"""Tests for the post-sync "newly orderable" diff logic.

The function under test is :func:`brewbridge.core.orders.newly_orderable`
— pure (just dict diff arithmetic), so we exercise every branch of the
contract without needing a SQLite fixture or a brew.is mock.

The full pipeline (``all_recipe_blockers`` + ``newly_orderable`` called
back-to-back inside ``sync.run``) is exercised in integration manually;
unit-testing the SQLite-traversing piece would need a fully wired
BeerSmith.sqlite fixture and the value-for-effort isn't there.
"""
from brewbridge.core.orders import newly_orderable


def _state(items: dict[int, tuple[str, set[str]]]) -> dict[int, dict]:
    """Shorthand: {permid: (name, blocker_set)} → all_recipe_blockers shape."""
    return {
        rid: {"name": name, "blockers": frozenset(b)}
        for rid, (name, b) in items.items()
    }


def test_recipe_flipping_blocked_to_orderable_is_reported():
    pre = _state({1: ("Belgian Blonde", {"Wheat Malt"})})
    post = _state({1: ("Belgian Blonde", set())})
    assert newly_orderable(pre, post) == [(1, "Belgian Blonde")]


def test_recipe_already_orderable_is_not_reported():
    # Was orderable, still orderable — no flip, no notification.
    pre = _state({1: ("Pilsner", set())})
    post = _state({1: ("Pilsner", set())})
    assert newly_orderable(pre, post) == []


def test_recipe_still_blocked_is_not_reported():
    # Was blocked by Wheat Malt, now blocked by Nugget hops — different
    # blocker, but still blocked. No notification.
    pre = _state({1: ("Saison Duble", {"Wheat Malt"})})
    post = _state({1: ("Saison Duble", {"Nugget"})})
    assert newly_orderable(pre, post) == []


def test_recipe_newly_blocked_is_not_reported():
    # Edge case — recipe was orderable, now blocked. We're not announcing
    # bad news in this notification (that's a different feature).
    pre = _state({1: ("Cream Ale", set())})
    post = _state({1: ("Cream Ale", {"Liberty"})})
    assert newly_orderable(pre, post) == []


def test_new_recipe_added_since_pre_snapshot_is_ignored():
    # User created a new recipe in BeerSmith between syncs. We don't
    # have a pre-state for it, so we can't say whether it "flipped".
    # Skip — wait for the next sync to establish a baseline.
    pre = _state({1: ("Cream Ale", set())})
    post = _state({
        1: ("Cream Ale", set()),
        2: ("Just Imported", set()),       # new since last sync
    })
    assert newly_orderable(pre, post) == []


def test_recipe_removed_since_pre_snapshot_is_ignored():
    # User deleted a recipe in BeerSmith. It's gone from post; nothing
    # to notify about.
    pre = _state({
        1: ("Cream Ale", {"Wheat Malt"}),
        2: ("Deleted Later", {"Magnum"}),
    })
    post = _state({1: ("Cream Ale", set())})
    assert newly_orderable(pre, post) == [(1, "Cream Ale")]


def test_multiple_unlocks_are_sorted_by_name():
    pre = _state({
        1: ("Belgian Blonde",   {"Wheat Malt"}),
        2: ("APA",              {"Cascade"}),
        3: ("Saison Duble",     {"Wheat Malt", "Nugget"}),
        4: ("Already Orderable", set()),
    })
    post = _state({
        1: ("Belgian Blonde",   set()),
        2: ("APA",              set()),
        3: ("Saison Duble",     set()),
        4: ("Already Orderable", set()),
    })
    # Alphabetical by name — the order the tray notification will use.
    assert newly_orderable(pre, post) == [
        (4, "Already Orderable") if False else (2, "APA"),   # narrate sort
        (1, "Belgian Blonde"),
        (3, "Saison Duble"),
    ]


def test_partial_unlock_one_recipe_only():
    # Two recipes blocked, only one gets unlocked this sync. Only the
    # actually-unblocked one is reported.
    pre = _state({
        1: ("Wheat Beer",  {"Wheat Malt"}),
        2: ("IPA",         {"Citra"}),
    })
    post = _state({
        1: ("Wheat Beer",  set()),               # unlocked
        2: ("IPA",         {"Citra"}),           # still blocked
    })
    assert newly_orderable(pre, post) == [(1, "Wheat Beer")]


def test_empty_inputs_return_empty_list():
    assert newly_orderable({}, {}) == []
