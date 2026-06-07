"""Tests for catalog.classify_product — specifically the orphan-category
fallback added after the Dingemans Wheat bug.

Background: brew.is's $scategories Nuxt payload lists ~50 categories,
but some products reference category IDs that aren't in that list
(seen with Dingemans Wheat at categories [83, 86]). Without a fallback,
those products were silently dropped from the synced catalog and users
saw "out of stock" for items that were actually in stock. With too
LOOSE a fallback (any product whose categories didn't bucket → text
heuristic), 56 false positives leaked in (recipes, brewing equipment,
fittings, cheesemaking supplies, …).

The fix: fallback ONLY when the product has zero known categories at
all. Products with known-but-non-ingredient categories (e.g.
"Uppskriftir" / "Bjórgerðartæki" / "Mælitæki") stay None.
"""
from __future__ import annotations

from brewbridge.core import catalog as cat


def test_orphan_category_with_grain_name_is_classified():
    """The canonical case: Dingemans Wheat at categories [83, 86] with
    neither category in cat_names. Name + description say "wheat malt"
    so the fallback returns 'grain'."""
    product = {
        "name": "Dingemans Wheat",
        "categories": [83, 86],
        "description": "<b>Dingemans Wheat Malt</b> er hágæða "
                       "hveitimalt sem eykur froðumyndun. "
                       "Korntegund: hveitimalt",
        "quantity": 1154,
    }
    cat_names = {}     # neither 83 nor 86 is in here
    assert cat.classify_product(product, cat_names) == "grain"


def test_known_non_ingredient_category_does_not_fall_through_to_text():
    """The 56-false-positive disaster: products in known-but-not-
    ingredient categories ("Uppskriftir" = recipe products listed in
    the store) must NOT fall through to text-based fallback even though
    their descriptions mention malt/hops/yeast tokens. They have a
    known category — it's deliberately not an ingredient category."""
    product = {
        "name": "Belgian Blonde",
        "categories": [38],   # Uppskriftir (recipes)
        "description": "Lítill Belgískur blonde með Pilsen malt, "
                       "Saaz hops og Fermentis BE-256 yeast.",
        "quantity": 1,
    }
    cat_names = {38: "Uppskriftir"}      # known category, not an ingredient
    assert cat.classify_product(product, cat_names) is None


def test_brewing_equipment_with_malt_in_description_is_excluded():
    """Equipment items mention 'malt' / 'brewing' in descriptions but
    live in known equipment categories (Bjórgerðartæki). Must stay
    None despite the suggestive text — the category is the source of
    truth."""
    product = {
        "name": "Brewzilla 35L Gen 4.1",
        "categories": [60],   # Bjórgerðartæki
        "description": "All-in-one bruggtæki fyrir maltgerð og soðun.",
        "quantity": 5,
    }
    cat_names = {60: "Bjórgerðartæki"}
    assert cat.classify_product(product, cat_names) is None


def test_category_classification_still_works_when_no_orphan():
    """Sanity: a product with a clearly-ingredient category still
    classifies via the category path. The fallback is OFF in this case."""
    product = {
        "name": "Pilsner (Weyermann)",
        "categories": [17],
        "description": "German pilsner malt",
        "quantity": 30,
    }
    cat_names = {17: "Korn og Malt"}     # Icelandic for grain/malt
    assert cat.classify_product(product, cat_names) == "grain"


def test_mixed_orphan_and_known_does_not_use_fallback():
    """If even ONE category is known (regardless of whether it's
    ingredient or not), the fallback stays off. Avoids the case where
    a recipe product accidentally lists an unknown decorative category
    alongside its real one."""
    product = {
        "name": "Some accessory",
        "categories": [83, 60],     # 83 unknown, 60 = brewing equipment
        "description": "Description mentions malt and hops.",
        "quantity": 1,
    }
    cat_names = {60: "Bjórgerðartæki"}
    assert cat.classify_product(product, cat_names) is None
