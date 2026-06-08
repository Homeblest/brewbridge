"""Tests for orders.is_pantry — items a brewer is assumed to have at home,
which must never block a brew.is order.

Pantry items show in the order sheet's "Úr eldhúsi" section instead of as
a buyable line or a blocker. Two groups: table sugars (everyone has them)
and priming/corn sugars (dextrose — the canonical priming sugar a
homebrewer keeps a bag of).

Regression context: a recipe calling for 120 g of dextrose used to block
the whole order ("ekki til hjá brew.is — engin hentug staðganga"), even
though every other ingredient was in stock and the brewer had dextrose at
home. Making dextrose pantry unblocks the order.
"""
from __future__ import annotations

import pytest

from brewbridge.core import orders


@pytest.mark.parametrize("name", [
    "Dextrose (corn sugar)",
    "Dextrose",
    "Corn Sugar (Dextrose)",
    "Priming Sugar",
    "Dextrósi - 1kg",
    "Þrúgusykur",
])
def test_dextrose_family_is_pantry(name):
    assert orders.is_pantry(name, "grain") is True


@pytest.mark.parametrize("name", [
    "Table Sugar (Sucrose)",
    "White Sugar",
    "Cane Sugar",
    "Borðsykur",
    "Strásykur",
])
def test_table_sugar_still_pantry(name):
    # The original pantry set must keep working after adding dextrose.
    assert orders.is_pantry(name, "grain") is True


@pytest.mark.parametrize("name", [
    "Pale Malt",
    "Maris Otter",
    "Cascade",                  # a hop, not a sugar
    "Carafa Special II",
    "Candi Sugar, Dark",        # specialty brewing sugar — NOT pantry
])
def test_real_ingredients_not_pantry(name):
    assert orders.is_pantry(name, "grain") is False


def test_pantry_only_applies_to_grain():
    # is_pantry is grain-only — a hop or misc named "sugar" shouldn't
    # be swallowed as pantry.
    assert orders.is_pantry("Sugar Hops", "hops") is False
    assert orders.is_pantry("Table Sugar (Sucrose)", "misc") is False
