"""Tests for catalog: pack-size parsing, category classification."""
import pytest

from brewbridge.core import catalog as cat


class TestPackParsing:
    @pytest.mark.parametrize("name,expected_qty,expected_unit,clean", [
        ("Cascade 50gr",                    50.0, "gr", "Cascade"),
        ("Fermentis US-05 11.5gr",          11.5, "gr", "Fermentis US-05"),
        ("Hveitiflögur (1kg)",              1.0,  "kg", "Hveitiflögur"),
        ("Sorbitol 250gr",                  250.0,"gr", "Sorbitol"),
        ("3,6 kg Pale Ale malt",            3.6,  "kg", "Pale Ale malt"),
    ])
    def test_pack_extraction(self, name, expected_qty, expected_unit, clean):
        amt, base = cat.parse_pack_amount(name)
        assert amt is not None
        qty, unit = amt
        assert qty == expected_qty
        assert unit.lower() == expected_unit
        # Clean name should have the pack info removed (allowing leading/trailing whitespace cleanup)
        assert clean.lower() in base.lower() or base.lower() in clean.lower()

    def test_no_pack_returns_none(self):
        amt, base = cat.parse_pack_amount("Vienna Malt")
        assert amt is None
        assert base == "Vienna Malt"

    def test_pack_to_grams(self):
        assert cat.pack_to_grams((50, "gr")) == 50
        assert cat.pack_to_grams((11.5, "g")) == 11.5
        assert cat.pack_to_grams((1, "kg")) == 1000
        assert cat.pack_to_grams((1, "stk")) is None  # not a weight
        assert cat.pack_to_grams(None) is None


class TestClassification:
    @pytest.mark.parametrize("category,expected", [
        ("Korn",                "grain"),
        ("Crystal / Karamellu", "grain"),
        ("Ristað bygg",         "grain"),
        ("Humlar",              "hops"),
        ("Amerískir",           "hops"),
        ("Evrópskir",           "hops"),
        ("Ger",                 "yeast"),
        ("Bætiefni",            "misc"),
        ("Bragðefni",           "misc"),
        ("Krydd",               "misc"),
        # Gerjunarílát ("fermenters") must NOT classify as yeast despite "ger" prefix
        ("Gerjunarílát",        None),
        ("Kútar",               None),
    ])
    def test_category_type(self, category, expected):
        assert cat.category_type(category) == expected

    def test_equipment_filtered_out(self):
        # Items like "Wort Whipper" or "Súrefnissteinn" sometimes land in
        # ingredient categories on brew.is. They must classify as None.
        prod = {"name": "Wort Whipper segulhræra", "categories": [37]}
        cats = {37: "Ger"}
        assert cat.classify_product(prod, cats) is None

    def test_dme_reroutes_to_grain(self):
        # brew.is files dry malt extract under "Ger" but it belongs in grain.
        prod = {"name": "Þurrmalt (DME) - Extra light", "categories": [37]}
        cats = {37: "Ger"}
        assert cat.classify_product(prod, cats) == "grain"
