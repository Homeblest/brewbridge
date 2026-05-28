"""Tests for the matcher: family classifiers, phrase aliases, cross-family
penalty, and substitution engine.

These are the behaviours that took us multiple iterations to get right —
locking them in so a future refactor doesn't regress."""
import pytest

from brewbridge.core import matching as mm


# ---------------------------------------------------------------------------
# Phrase aliases and translate pipeline
# ---------------------------------------------------------------------------

class TestTranslate:
    def test_iceland_phrases_unify_with_english(self):
        # "Maltað Hveiti" should become "wheat malt", not the token-by-token
        # "malted wheat wheat" that diluted match scores in v1.
        assert mm.translate("Maltað Hveiti") == "wheat malt"
        assert mm.translate("Maltað Hveiti (Wheat)") == "wheat malt"

    def test_cara_pils_unifies_regardless_of_spacing(self):
        # "Cara-Pils/Dextrine", "cara pils", and "carapils" must all collapse
        # to one token so similarity doesn't punish hyphen variants.
        assert mm.translate("Cara-Pils/Dextrine") == "carapils"
        assert mm.translate("CaraPils") == "carapils"
        assert mm.translate("cara pils") == "carapils"

    def test_token_translation(self):
        assert "barley" in mm.translate("Byggflögur").split()
        assert "rye" in mm.translate("Maltaður rúgur").split()

    def test_dedupes_consecutive_duplicate_tokens(self):
        # Phrase + token translations can produce duplicates — dedupe keeps
        # similarity scoring honest.
        assert "wheat wheat" not in mm.translate("Maltað Hveiti (Wheat)")


# ---------------------------------------------------------------------------
# Family classifiers
# ---------------------------------------------------------------------------

class TestFamilies:
    @pytest.mark.parametrize("hop,family", [
        ("Saaz",                    "noble"),
        ("Hallertauer Mittelfrueh", "noble"),
        ("Cascade",                 "american_c"),
        ("Citra",                   "american_c"),
        ("East Kent Goldings",      "english"),
        ("Fuggles",                 "english"),
        ("Galaxy",                  "nz_aus"),
        ("Nelson Sauvin",           "nz_aus"),
        ("Magnum",                  "high_alpha"),
    ])
    def test_hop_family_classification(self, hop, family):
        assert family in mm.hop_families(hop)

    @pytest.mark.parametrize("yeast,family", [
        ("Safale US-05",            "clean_ale"),
        ("Lallemand Nottingham",    "clean_ale"),
        ("Fermentis S-04",          "english_ale"),
        ("Wyeast 1968 London ESB",  "english_ale"),
        ("Saflager W-34/70",        "lager"),
        ("Lallemand Abbaye",        "belgian"),
        ("Wyeast 3787 Trappist",    "belgian"),
        ("Fermentis BE-134 Saison", "saison"),  # NOT belgian — BE-134 is saison
        ("Lallemand Voss Kveik",    "kveik"),
        ("Philly Sour",             "sour"),
        ("Hefeweizen 3068",         "hefe"),
    ])
    def test_yeast_family(self, yeast, family):
        assert mm.yeast_family(yeast) == family

    @pytest.mark.parametrize("grain,color,bucket", [
        # Base malts
        ("Pilsner",                  1.0,   "base_pilsner"),
        ("Dingemans Pilsen",         1.6,   "base_pilsner"),
        ("Pale Ale (Dingemans)",     3.3,   "base_pale_ale"),
        ("Maris Otter",              3.0,   "base_pale_ale"),
        # Cara-Pils is dextrin, NOT base pilsner, despite "pils" in name
        ("Cara-Pils/Dextrine",       2.0,   "caramel_light"),
        ("CaraPils",                 None,  "caramel_light"),
        # Sugars and extracts are separate buckets — never interchangeable
        ("Sugar, Table (Sucrose)",   None,  "sugar"),
        ("Þurrmalt (DME) - Extra",   None,  "extract"),
        # Crystals by color
        ("CaraAmber",                30.0,  "caramel_med"),
        ("Special B",                180.0, "caramel_dark"),
        # Vienna / Munich are their own buckets
        ("Vienna Malt",              3.5,   "vienna"),
        ("Munich I",                 7.1,   "munich"),
        # Roasted by name regardless of stored color
        ("Roasted Barley",           300.0, "roasted"),
        ("Carafa Special II",        415.0, "roasted"),
        # Flaked adjuncts
        ("Oats, Flaked",             1.0,   "flaked"),
        ("Byggflögur",               1.7,   "flaked"),
    ])
    def test_grain_bucket(self, grain, color, bucket):
        assert mm.grain_bucket(grain, color) == bucket


# ---------------------------------------------------------------------------
# Cross-family penalty in match_product
# ---------------------------------------------------------------------------

class TestCrossFamilyPenalty:
    def test_wheat_malt_does_not_match_peated_malt(self):
        # The canonical Saison-DuBle bug — "Wheat Malt, Bel" used to match
        # "Peated malt" at 0.64 because both share "malt". Phrase aliases +
        # cross-family penalty must keep wheat finding wheat.
        catalog = {"grain": [
            {"name": "Peated malt", "alt_name": "peated malt",
             "F_G_COLOR": 2.8},
            {"name": "Maltað Hveiti (Wheat)",
             "alt_name": mm.translate("Maltað Hveiti (Wheat)"),
             "F_G_COLOR": 2.4},
        ]}
        prod, score, _ = mm.match_product(
            "Wheat Malt, Bel", "grain", catalog,
            ing_meta={"F_G_COLOR": "2.0"})
        assert prod is not None
        assert "Hveiti" in prod["name"]
        assert score > 0.6

    def test_nugget_does_not_match_fuggles(self):
        # Nugget is high-alpha; Fuggles is English. Different families ->
        # cross-family penalty must push Fuggles below threshold.
        catalog = {"hops": [
            {"name": "Fuggles",  "alt_name": "fuggles",  "F_H_ALPHA": 4.5},
            {"name": "Magnum",   "alt_name": "magnum",   "F_H_ALPHA": 12.0},
        ]}
        prod, _, _ = mm.match_product("Nugget", "hops", catalog,
                                       ing_meta={"F_H_ALPHA": "12.0"})
        # Nugget should prefer Magnum (same high_alpha family) over Fuggles
        if prod:
            assert prod["name"] != "Fuggles"

    def test_sugar_does_not_match_dme(self):
        # Plain table sugar (ferments dry, no body) must NOT match DME
        # (which adds malt body) — separate buckets.
        catalog = {"grain": [
            {"name": "Þurrmalt (DME)",
             "alt_name": mm.translate("Þurrmalt (DME)"),
             "F_G_COLOR": 0.0},
        ]}
        prod, _, _ = mm.match_product(
            "Sugar, Table (Sucrose)", "grain", catalog,
            ing_meta={"F_G_COLOR": "1.0"})
        assert prod is None    # different bucket, no acceptable match


# ---------------------------------------------------------------------------
# Substitution engine
# ---------------------------------------------------------------------------

class TestSubstitutes:
    def test_hallertauer_suggests_saaz(self):
        # Real bug fix: Hallertauer Mittelfrueh not at brew.is -> noble family
        # substitute is Saaz (same family, similar alpha).
        catalog = {"hops": [
            {"name": "Saaz",    "stock": 9,  "F_H_ALPHA": 3.75, "keyword": "saaz"},
            {"name": "Cascade", "stock": 39, "F_H_ALPHA": 5.5,  "keyword": "cascade"},
        ]}
        subs = mm.find_substitutes(
            {"type": "hops", "name": "Hallertauer Mittelfrueh",
             "ing": {"F_H_ALPHA": "4.0"}},
            catalog,
        )
        assert subs, "expected at least one noble substitute"
        assert subs[0]["prod"]["name"] == "Saaz"

    def test_no_sub_when_no_in_family_candidate(self):
        # If brew.is has nothing in the same family, the engine honestly
        # returns []. The order page then escalates to "can't be brewed".
        catalog = {"yeast": [
            {"name": "Fermentis US-05", "stock": 100, "sku": "us-05",
             "F_Y_MIN_ATTENUATION": 73, "F_Y_MAX_ATTENUATION": 80,
             "keyword": "us-05"},
        ]}
        subs = mm.find_substitutes(
            {"type": "yeast", "name": "Wyeast 3068 Weihenstephan",
             "ing": {"F_Y_PRODUCT_ID": "3068",
                     "F_Y_MIN_ATTENUATION": "73", "F_Y_MAX_ATTENUATION": "77"}},
            catalog,
        )
        assert subs == []      # hefe has no in-stock candidate

    def test_substitute_carries_brewing_note(self):
        # The note must mention the same family AND an attenuation comparison.
        catalog = {"yeast": [
            {"name": "Lallemand Abbaye", "stock": 54, "sku": "",
             "F_Y_MIN_ATTENUATION": 73, "F_Y_MAX_ATTENUATION": 77,
             "keyword": "abbaye"},
        ]}
        subs = mm.find_substitutes(
            {"type": "yeast", "name": "Wyeast 3787 Trappist",
             "ing": {"F_Y_PRODUCT_ID": "3787",
                     "F_Y_MIN_ATTENUATION": "74", "F_Y_MAX_ATTENUATION": "82"}},
            catalog,
        )
        assert subs
        note = subs[0]["note"]
        assert "belgískt" in note     # family label
        assert "gerjun" in note       # attenuation comparison
