"""Tests for sync._price_per_unit — brew.is per-pack price → BeerSmith
per-unit price.

The unit matters a lot: BeerSmith computes recipe cost as
amount(oz) × price, so weight-sold ingredients (grain, hops) must be
priced per ounce. Getting it wrong is a 16×/35× cost error — the same
class of bug as the early grain-weight import mistake, hence these
tests pin the exact arithmetic.
"""
from __future__ import annotations


from brewbridge.core import sync as bb_sync
from brewbridge.core import beersmith as bs


# OZ_PER_G sanity — if this constant ever changes the expected values
# below need to move with it.
def test_oz_per_g_constant():
    assert abs(bs.OZ_PER_G - 0.0352739619) < 1e-9


def test_hops_per_ounce():
    # "Amarillo 50gr" at 600 ISK/pack. 50 g = 1.7637 oz → 340.2 ISK/oz.
    price = bb_sync._price_per_unit("hops", 600, (50.0, "gr"))
    assert abs(price - 600 / (50 * bs.OZ_PER_G)) < 0.01
    assert abs(price - 340.2) < 0.5


def test_packaged_grain_per_ounce():
    # "Byggflögur 1kg" at 850 ISK/pack. 1 kg = 35.27 oz → 24.1 ISK/oz.
    price = bb_sync._price_per_unit("grain", 850, (1.0, "kg"))
    assert abs(price - 850 / bs.OZ_PER_KG) < 0.01
    assert abs(price - 24.1) < 0.2


def test_loose_grain_priced_per_kg():
    # Loose-sold grain (pack=None) — brew.is prices per kg. 830 ISK/kg
    # → 830 / 35.27 = 23.53 ISK/oz.
    price = bb_sync._price_per_unit("grain", 830, None)
    assert abs(price - 830 / bs.OZ_PER_KG) < 0.01
    assert abs(price - 23.53) < 0.2


def test_yeast_priced_per_packet():
    # Yeast amount is in packets, not weight — price passes through as-is.
    assert bb_sync._price_per_unit("yeast", 990, (11.5, "gr")) == 990.0
    # Even with no pack info, yeast price is the per-packet price.
    assert bb_sync._price_per_unit("yeast", 990, None) == 990.0


def test_misc_left_unpriced():
    # Misc amount units are too variable to convert safely — return 0
    # rather than risk a wildly wrong figure.
    assert bb_sync._price_per_unit("misc", 1300, (100.0, "gr")) == 0.0
    assert bb_sync._price_per_unit("misc", 1300, None) == 0.0


def test_zero_or_missing_price_returns_zero():
    assert bb_sync._price_per_unit("grain", 0, (1.0, "kg")) == 0.0
    assert bb_sync._price_per_unit("grain", None, (1.0, "kg")) == 0.0
    assert bb_sync._price_per_unit("hops", "", (50.0, "gr")) == 0.0


def test_cost_roundtrips_to_pack_price():
    """A recipe using exactly one pack's worth of grain should cost
    (about) the pack price — proves the per-oz conversion is
    self-consistent with BeerSmith's amount(oz) × price model."""
    pack_price = 850          # 1 kg pack
    per_oz = bb_sync._price_per_unit("grain", pack_price, (1.0, "kg"))
    amount_oz = 1.0 * bs.OZ_PER_KG       # using one full kg
    computed_cost = amount_oz * per_oz
    assert abs(computed_cost - pack_price) < 0.01
