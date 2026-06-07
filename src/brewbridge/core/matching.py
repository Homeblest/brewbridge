"""Ingredient matching — maps a recipe's ingredient name to the best brew.is
catalog product, with brewing-family awareness so a noble hop never matches an
American C hop and a base pilsner malt never matches a roasted malt.

Three layers:

1. **Phrase / token normalisation.** Lowercase, strip accents, substitute known
   Icelandic phrases ("maltað hveiti" -> "wheat malt", "cara pils" ->
   "carapils", ...), translate stray Icelandic tokens, dedupe consecutive
   duplicates. Applied symmetrically to BOTH sides of similarity scoring so
   tokenisation differences (Cara-Pils vs CaraPils) don't blow up scores.

2. **Family / bucket classifiers.** Each ingredient type has a brewing family
   taxonomy. Hops: noble / english / american_c / nz_aus / high_alpha. Grain:
   base_pilsner / base_pale_ale / vienna / munich / caramel_light/med/dark /
   roasted / flaked / sugar / extract. Yeast: clean_ale / english_ale / lager /
   belgian / saison / kveik / sour / hefe.

3. **Similarity + family penalty.** ``match_product`` scores every catalog
   candidate by name + alt-name similarity, then multiplies by 0.55 if the
   candidate is in a different brewing family from the target. Wrong-family
   candidates that scored higher by raw text similarity get pushed below
   threshold so a same-family pick wins.

When the best in-family match still falls below threshold, the caller treats
the ingredient as unmatched and ``find_substitutes`` proposes alternatives.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from html import unescape

# ---------------------------------------------------------------------------
# Phrase aliases & token translations
# ---------------------------------------------------------------------------

# Multi-word phrase aliases applied BEFORE token-level translation so e.g.
# "Maltað Hveiti" cleanly becomes "wheat malt" instead of the duplicate-laden
# token-by-token "malted wheat wheat".  Longest phrase wins.
PHRASE_ALIASES = {
    # Icelandic phrases -> English brewing terms
    "maltad hveiti":           "wheat malt",
    "maltadur hveiti":         "wheat malt",
    "maltad rugur":            "rye malt",
    "maltadur rugur":          "rye malt",
    "ristad bygg":             "roasted barley",
    "ristadur bygg":           "roasted barley",
    "byggflogur":              "flaked barley",
    "hveitiflogur":            "flaked wheat",
    "haframjol flaked oats":   "flaked oats",
    "haframjol":               "flaked oats",
    "hrisgrjonaflogur":        "flaked rice",
    "hrisgrjonahydi":          "rice hulls",
    # Cara-malt unification — collapse "Cara-Pils", "cara pils", "carapils"
    # to a single token so similarity scoring isn't punished by hyphen splits.
    "cara pils dextrine":      "carapils dextrine",
    "cara pils":               "carapils",
    "cara foam":               "carafoam",
    "cara hell":               "carahell",
    "cara red":                "carared",
    "cara amber":              "caraamber",
    "cara aroma":              "caraaroma",
    "cara munich":             "caramunich",
    "cara fa":                 "carafa",
    "carapils dextrine":       "carapils",

    # ---- Fermentis brand-to-product unification ----
    # brew.is sells dry yeasts under their parent-brand name ("Fermentis
    # S-04 11.5gr"), but BeerSmith's built-in library names the same
    # products by Fermentis's sub-brand and descriptor ("SafAle English
    # Ale"). Without these aliases the brew.is rows can't bind to any
    # built-in spec, so every imported recipe that uses dry yeast lands
    # with attenuation=0 and BeerSmith's ABV calculator returns zero.
    #
    # Mappings below are the documented Fermentis product codes; verified
    # against brew.is's catalog 2026-06.
    "fermentis s 04":         "safale english ale",       # SafAle S-04
    "fermentis us 05":        "safale american",          # SafAle US-05
    "fermentis s 33":         "safbrew ale",              # Safbrew S-33
    "fermentis t 58":         "safbrew specialty ale",    # Safbrew T-58
    "fermentis w 34 70":      "saflager german lager",    # Saflager W-34/70
    "fermentis be 256":       "safbrew abbaye belgian",   # Safbrew BE-256 (Abbaye)
    "fermentis w 68":         "safbrew wheat",            # Safbrew W-68

    # ---- Grain pack/origin-suffix stripping ----
    # brew.is occasionally tags products with origin or "(aromatic)" or
    # pack-size in the name; BeerSmith's library doesn't. Same brewing
    # character — peel the noise.
    "melanoidin aromatic":     "melanoidin",
    "ultra pils":              "pilsen",                  # Dingemans Ultra Pils
}

# Single-token translations applied after phrase aliases
TOKEN_TRANSLATIONS = {
    "byggflogur": "flaked barley", "bygg": "barley",
    "hrisgrjonaflogur": "flaked rice", "hrisgrjonahydi": "rice hulls",
    "hrisgrjona": "rice", "hrisgrjon": "rice",
    "hveitiflogur": "flaked wheat", "hveiti": "wheat",
    "maisflogur": "flaked maize", "mais": "maize",
    "haframjol": "flaked oats", "hafrar": "oats",
    "maltadur": "malted", "maltad": "malted",
    "rugur": "rye", "rug": "rye",
    "ristad": "roasted", "ristadur": "roasted",
    "reykt": "smoked", "beyki": "beech",
    "thurrmalt": "dry malt extract", "dextrosi": "dextrose",
    "gifs": "gypsum", "mjolkursyra": "lactic acid", "eplasyra": "malic acid",
    "ylliblom": "elderflower", "appelsinuborkur": "orange peel", "borkur": "peel",
    "greip": "grapefruit", "krydd": "spice", "bragdefni": "flavor",
}


# ---------------------------------------------------------------------------
# Normalisation pipeline
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    """Lowercase + strip diacritics + replace ð/þ/æ. Always first step."""
    if not s:
        return ""
    s = unescape(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ð", "d").replace("þ", "th").replace("æ", "ae")
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def translate(name: str) -> str:
    """Full normalisation: norm -> phrase aliases (longest first) -> token
    translation -> dedupe consecutive duplicates."""
    s = norm(name)
    for phrase in sorted(PHRASE_ALIASES, key=len, reverse=True):
        if phrase in s:
            s = s.replace(phrase, PHRASE_ALIASES[phrase])
    tokens = [TOKEN_TRANSLATIONS.get(t, t) for t in s.split()]
    # Dedupe while preserving first-occurrence order. Handles both consecutive
    # duplicates (from token translations) and parenthetical English mirrors
    # like "Maltað Hveiti (Wheat)" -> "wheat malt wheat" -> "wheat malt".
    seen, deduped = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return " ".join(deduped)


def similarity(a: str, b: str) -> float:
    """Symmetric name similarity using the full translate pipeline on BOTH
    sides. Combines Jaccard token overlap with SequenceMatcher ratio, plus a
    +0.15 bonus when one normalised name is a substring of the other."""
    na, nb = translate(a), translate(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    bonus = 0.15 if (na in nb or nb in na) else 0.0
    return min(1.0, max(jacc, ratio) + bonus)


# ---------------------------------------------------------------------------
# Family / bucket classifiers
# ---------------------------------------------------------------------------

HOP_FAMILIES = {
    "noble":      ["saaz", "tettnang", "hallertau", "spalt", "hersbrucker", "mittelfru"],
    "english":    ["fuggle", "golding", "ekg", "willamette", "challenger",
                   "target", "northdown", "bramling", "pilgrim", "first gold"],
    "american_c": ["cascade", "centennial", "amarillo", "columbus", "chinook",
                   "simcoe", "citra", "mosaic", "idaho 7", "idaho #7", "007",
                   "el dorado", "calypso", "ahtanum", "tomahawk", "warrior"],
    "nz_aus":     ["nelson", "motueka", "galaxy", "nectaron", "vic secret",
                   "riwaka", "wai-iti"],
    "high_alpha": ["magnum", "columbus", "warrior", "summit", "apollo",
                   "northern brewer", "nugget", "bravo"],
}

# Order matters — saison/kveik/sour checked first so e.g. BE-134 Saison isn't
# read as Belgian. Specific subtypes win.
YEAST_FAMILIES = {
    "saison":      ["saison", "farmhouse", "m29", "be-134", "3724"],
    "kveik":       ["kveik", "voss", "ebbegaden", "stalljen", "eitrheim",
                    "hornindal", "lutra"],
    "sour":        ["philly sour", "berliner", "sourvisiae", "lacto", "lambic",
                    "brett"],
    "hefe":        ["hefeweizen", "weizen", "weihen", "wb-06", "3068"],
    "belgian":     ["abbaye", "abbey", "be-256", "t-58", "m31", "tripel",
                    "dubbel", "trappist", "high gravity", "3787", "wlp530",
                    "wlp540"],
    "english_ale": ["s-04", "s04", "english ale", "windsor", "london", "m15",
                    "empire ale", "esb", "1968", "1469"],
    "lager":       ["w-34/70", "w34/70", "saflager", "s-23", "s-189", "m84",
                    "m54", "bohemian", "californian lager", "w-68", "2124",
                    "2278"],
    "clean_ale":   ["us-05", "us05", "nottingham", "verdant", "pomona", "house",
                    "safale american", "bry-97", "american ale", "k-97",
                    "new england", "1056", "wlp001", "1450"],
}

FAMILY_LABEL = {
    "noble":         "noble (evrópsk)",
    "english":       "ensk",
    "american_c":    "amerísk citrus",
    "nz_aus":        "Nýja-Sjáland/Ástralía",
    "high_alpha":    "há-alfa beiskja",
    "clean_ale":     "hreint amerískt ölger",
    "english_ale":   "enskt ölger",
    "lager":         "lager",
    "belgian":       "belgískt",
    "saison":        "saison",
    "hefe":          "hveitiöl",
    "kveik":         "kveik",
    "sour":          "súrger",
    "base_pilsner":  "pilsner-malt",
    "base_pale_ale": "pale ale-malt",
    "vienna":        "vínar-malt",
    "munich":        "munich-malt",
    "caramel_light": "ljós karamellu",
    "caramel_med":   "miðlungs karamellu",
    "caramel_dark":  "dökk karamellu",
    "roasted":       "ristað/sortulit",
    "flaked":        "flögur/aukamalt",
    "sugar":         "einfaldur sykur",
    "extract":       "maltútdráttur",
    "unknown":       "óþekkt",
}


def hop_families(name: str) -> set[str]:
    n = name.lower()
    return {fam for fam, kws in HOP_FAMILIES.items() if any(k in n for k in kws)} or {"unknown"}


def yeast_family(name: str, product_id: str = "") -> str:
    n = (name + " " + (product_id or "")).lower()
    for fam, kws in YEAST_FAMILIES.items():
        if any(k in n for k in kws):
            return fam
    return "unknown"


def grain_bucket(name: str, color: float | None) -> str:
    n = (name or "").lower()
    # Pure simple sugars (ferment ~100% dry, no body) — not interchangeable
    # with malt extracts (which add body + sweetness).
    if any(k in n for k in ("sugar", "sykur", "sucrose", "candi", "honey",
                             "molasses", "dextrose", "dextrósi", "syrup",
                             "sýróp", "treacle", "lactose")):
        return "sugar"
    # Concentrated wort — interchangeable among themselves but never with sugar
    if any(k in n for k in ("dry malt extract", "dme", "lme",
                             "liquid malt extract", "malt extract",
                             "þurrmalt", "thurrmalt")):
        return "extract"
    # Flaked / torrified adjuncts (not interchangeable with base malt)
    if any(k in n for k in ("flake", "flögur", "flogur", "rolled",
                             "torrified", "haframjöl", "haframjol",
                             "hýði", "hydi", "hulls")):
        return "flaked"
    # Roasted / black malts (name-based; some lack a high color number)
    if any(k in n for k in ("roasted barley", "ristað bygg", "ristad bygg",
                             "black malt", "chocolate", "carafa")):
        return "roasted"
    # Dextrin / Cara-Pils / Cara-Foam — these contain "pils" but are crystal,
    # NOT base malt. Check before pilsner branch so they don't sneak in.
    if any(k in n for k in ("carapils", "cara pils", "cara-pils",
                             "carafoam", "cara-foam", "dextrine", "dextrin")):
        return "caramel_light"
    # Wheat malts — distinct grain family. Can't substitute for base barley
    # malt without changing the recipe character (mouthfeel, haze, head).
    # Both English ("wheat", "weizen", "weiss") and Icelandic ("hveiti")
    # forms; Pilsen-wheat-blends still match "pils" first which is fine —
    # those are sold as base-pilsner-equivalents.
    if any(k in n for k in ("wheat", "hveiti", "weizen", "weiss", "weisse")):
        return "wheat_malt"
    # Smoked / peated malts (rauchmalz, peated, beech-smoked). Strong
    # phenolic flavor; pairing them with a recipe asking for clean wheat
    # malt is the classic matching-fail case this bucket is here to
    # prevent. Both English and Icelandic forms.
    if any(k in n for k in ("peated", "smoked", "smoke", "rauch",
                             "reykt", "reyk")):
        return "smoked"
    # Rye malts — also distinct family. Slick mouthfeel, spicy notes,
    # not interchangeable with base or wheat.
    if any(k in n for k in (" rye", "rye ", "rye malt", "rugur",
                             "rúgur", "ruge", "rúg")):
        return "rye"
    if "pils" in n or "lager malt" in n:
        return "base_pilsner"
    if any(k in n for k in ("pale ale", "maris", "two row", "2-row", "2 row",
                             "plumage", "golden promise", "halcyon")):
        return "base_pale_ale"
    if "vienna" in n:
        return "vienna"
    if "munich" in n:
        return "munich"
    if color is None or color < 4:
        return "base_pale_ale"
    if color < 30:
        return "caramel_light"
    if color < 80:
        return "caramel_med"
    if color < 200:
        return "caramel_dark"
    return "roasted"


# ---------------------------------------------------------------------------
# Family-aware matching
# ---------------------------------------------------------------------------

MATCH_THRESHOLD = 0.55
CROSS_FAMILY_PENALTY = 0.55     # multiplier applied to wrong-family candidates


def _f(x, default: float | None = None) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _target_family(ing_name: str, ing_type: str, ing_meta: dict | None,
                    alt_name: str | None):
    """Return (target_family_set, target_bucket, target_yeast_family)."""
    meta = ing_meta or {}
    if ing_type == "hops":
        return hop_families(ing_name), None, None
    if ing_type == "grain":
        return None, grain_bucket(ing_name, _f(meta.get("F_G_COLOR"))), None
    if ing_type == "yeast":
        pid = meta.get("F_Y_PRODUCT_ID", "") or (alt_name or "")
        return None, None, yeast_family(ing_name, pid)
    return None, None, None


def match_product(
    ing_name: str,
    ing_type: str,
    catalog: dict[str, list[dict]],
    *,
    alt_name: str | None = None,
    ing_meta: dict | None = None,
) -> tuple[dict | None, float, dict | None]:
    """Return ``(best_match, score, raw_best)``.

    ``best_match`` is the catalog product if it passes ``MATCH_THRESHOLD``,
    else None. ``raw_best`` is always the top candidate (useful for "did you
    mean?" hints). Cross-family candidates get a 0.55 score multiplier so a
    same-family pick wins even at lower raw similarity.

    ``ing_meta`` carries the embedded ingredient's properties (color / alpha /
    attenuation) used for family classification.
    """
    targets = [ing_name] + ([alt_name] if alt_name else [])
    t_fams, t_bucket, t_yfam = _target_family(ing_name, ing_type, ing_meta, alt_name)

    best, score = None, 0.0
    for p in catalog.get(ing_type, []):
        cands = [p["name"]]
        if p.get("alt_name") and p["alt_name"] != p["name"]:
            cands.append(p["alt_name"])
        s = max(similarity(t, c) for t in targets for c in cands)
        # Cross-family penalty (applied only when both sides are classifiable)
        if ing_type == "hops" and t_fams and "unknown" not in t_fams:
            cfams = hop_families(p["name"])
            if cfams and "unknown" not in cfams and not (t_fams & cfams):
                s *= CROSS_FAMILY_PENALTY
        elif ing_type == "grain" and t_bucket:
            cbucket = grain_bucket(p["name"], _f(p.get("F_G_COLOR")))
            if cbucket != t_bucket:
                s *= CROSS_FAMILY_PENALTY
        elif ing_type == "yeast" and t_yfam and t_yfam != "unknown":
            cyfam = yeast_family(p["name"], p.get("sku", ""))
            if cyfam != t_yfam and cyfam != "unknown":
                s *= CROSS_FAMILY_PENALTY
        if s > score:
            best, score = p, s
    accepted = best if score >= MATCH_THRESHOLD else None
    return accepted, score, best


# ---------------------------------------------------------------------------
# Substitute search (used when a recipe ingredient can't match in stock)
# ---------------------------------------------------------------------------

def find_substitutes(
    blocker: dict,
    catalog: dict[str, list[dict]],
    max_n: int = 3,
) -> list[dict]:
    """Suggest up to ``max_n`` in-stock substitutes for a blocked ingredient.

    ``blocker`` is a dict with keys: type ('grain'|'hops'|'yeast'|'misc'),
    name (str), ing (the embedded ingredient JSON dict for property lookup).

    Each result: ``{'prod': catalog_product, 'note': str}`` where note is a
    short Icelandic pros/cons line (family + numeric comparison).
    """
    t = blocker["type"]
    ing = blocker.get("ing") or {}
    blocked_name = blocker["name"]
    pool = [p for p in catalog.get(t, [])
            if p.get("stock") and p["stock"] > 0
            and p["name"].lower() != blocked_name.lower()]

    if t == "hops":
        return _subs_hops(blocked_name, ing, pool, max_n)
    if t == "yeast":
        return _subs_yeast(blocked_name, ing, pool, max_n)
    if t == "grain":
        return _subs_grain(blocked_name, ing, pool, max_n)
    return []                                  # no heuristic for misc


def _subs_hops(name, ing, pool, max_n):
    b_fams = hop_families(name)
    b_alpha = _f(ing.get("F_H_ALPHA"))
    scored = []
    for p in pool:
        c_fams = hop_families(p["name"])
        shared = b_fams & c_fams
        if not shared:
            continue
        c_alpha = _f(p.get("F_H_ALPHA"))
        alpha_d = abs((c_alpha or 0) - (b_alpha or 0))
        scored.append((shared, alpha_d, c_alpha, p))
    scored.sort(key=lambda x: (-len(x[0]), x[1]))
    out = []
    for shared, alpha_d, c_alpha, p in scored[:max_n]:
        fam = next(iter(shared))
        out.append({"prod": p, "note": _hop_note(b_alpha, c_alpha, fam, alpha_d)})
    return out


def _subs_yeast(name, ing, pool, max_n):
    b_fam = yeast_family(name, ing.get("F_Y_PRODUCT_ID", ""))
    if b_fam == "unknown":
        return []
    b_att = ((_f(ing.get("F_Y_MIN_ATTENUATION"), 0)
              + _f(ing.get("F_Y_MAX_ATTENUATION"), 0)) / 2) or None
    scored = []
    for p in pool:
        c_fam = yeast_family(p["name"], p.get("sku", ""))
        if c_fam != b_fam:
            continue
        c_att = ((_f(p.get("F_Y_MIN_ATTENUATION"), 0)
                  + _f(p.get("F_Y_MAX_ATTENUATION"), 0)) / 2) or None
        scored.append((abs((c_att or 0) - (b_att or 0)), c_att, p))
    scored.sort(key=lambda x: x[0])
    return [{"prod": p, "note": _yeast_note(b_att, c_att, b_fam)}
            for _, c_att, p in scored[:max_n]]


def _subs_grain(name, ing, pool, max_n):
    b_color = _f(ing.get("F_G_COLOR"))
    b_bucket = grain_bucket(name, b_color)
    scored = []
    for p in pool:
        c_color = _f(p.get("F_G_COLOR"))
        if grain_bucket(p["name"], c_color) != b_bucket:
            continue
        scored.append((abs((c_color or 0) - (b_color or 0)), c_color, p))
    scored.sort(key=lambda x: x[0])
    return [{"prod": p, "note": _grain_note(b_color, c_color, b_bucket)}
            for _, c_color, p in scored[:max_n]]


def _hop_note(b_alpha, c_alpha, fam, alpha_d):
    label = FAMILY_LABEL.get(fam, fam)
    bits = [f"sami flokkur ({label})"]
    if c_alpha is not None and b_alpha:
        if alpha_d < 1.0:
            bits.append(f"sambærileg beiskja (~{c_alpha:g}% alfa)")
        elif c_alpha > b_alpha:
            bits.append(f"sterkari ({c_alpha:g}% vs {b_alpha:g}% alfa — minnka magn)")
        else:
            bits.append(f"mildari ({c_alpha:g}% vs {b_alpha:g}% alfa — auka magn)")
    return "; ".join(bits)


def _yeast_note(b_att, c_att, fam):
    label = FAMILY_LABEL.get(fam, fam)
    bits = [f"sami flokkur ({label})"]
    if c_att and b_att:
        d = c_att - b_att
        if abs(d) < 3:
            bits.append(f"sambærileg gerjun (~{c_att:.0f}%)")
        elif d > 0:
            bits.append(f"þurrari ({c_att:.0f}% vs {b_att:.0f}% gerjun)")
        else:
            bits.append(f"sætari ({c_att:.0f}% vs {b_att:.0f}% gerjun)")
    return "; ".join(bits)


def _grain_note(b_color, c_color, bucket):
    label = FAMILY_LABEL.get(bucket, bucket)
    bits = [f"sami flokkur ({label})"]
    if c_color is not None and b_color is not None:
        d = c_color - b_color
        if abs(d) < 5:
            bits.append(f"sambærilegur litur ({c_color:g}°L vs {b_color:g}°L)")
        elif d > 0:
            bits.append(f"dekkri ({c_color:g}°L vs {b_color:g}°L)")
        else:
            bits.append(f"ljósari ({c_color:g}°L vs {b_color:g}°L)")
    return "; ".join(bits)
