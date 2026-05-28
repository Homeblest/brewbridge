"""brew.is catalog: fetch + decode the Nuxt 3 payload embedded in /uppskriftir.

brew.is is an OpenCart store behind a Nuxt 3 frontend. The full product catalog
(currently ~780 products) is shipped as a `__NUXT_DATA__` script tag in
``devalue`` flat-array format on the /uppskriftir page. No paid API key, no
public REST endpoint — just one HTML fetch per sync.

Public functions:
    fetch_payload(url=URL) -> str
        Fetch the raw HTML.

    parse_products(html) -> tuple[list[Product], dict[int, str]]
        Resolve the devalue payload into a list of Product dicts plus a
        mapping of category id -> category name.

    in_stock(products, ingredient_filter=None) -> Iterator[Product]
        Yield only products with positive stock quantity (brew.is's
        "checkmark = in stock"), optionally filtered to a brewing-ingredient
        category set.

Product is a plain dict with keys: product_id, sku, name, description, price,
quantity, status, image, weight, categories (list[int]), keyword, ...
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from html import unescape
from typing import Iterable, Iterator

URL = "https://www.brew.is/uppskriftir"

# Markers used by Nuxt's devalue format to mark wrapped/reactive references
_MARKERS = frozenset({"ShallowReactive", "Reactive", "Ref",
                       "ShallowRef", "EmptyRef", "NuxtError"})


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_payload(url: str = URL, timeout: int = 60) -> str:
    """Fetch the /uppskriftir HTML. Always decode as UTF-8 — brew.is serves
    UTF-8 but the response headers don't always declare it explicitly."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (brewbridge)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Devalue resolver
# ---------------------------------------------------------------------------

def _resolve(data: list, idx: int, depth: int = 0, seen: frozenset[int] | None = None):
    """Walk Nuxt's devalue flat-array format, materialising the object at ``idx``.

    The format: a single JSON array where each entry is either a primitive,
    a list of references (resolved recursively), or a dict mapping keys to
    references. Some entries are special-case wrappers like
    ``["ShallowReactive", 1]`` which forward to the underlying index.
    """
    if seen is None:
        seen = frozenset()
    if not isinstance(idx, int):
        return idx
    if idx in seen or depth > 80:
        return None
    seen = seen | {idx}
    v = data[idx]
    if isinstance(v, list):
        if v and isinstance(v[0], str) and v[0] in _MARKERS:
            return _resolve(data, v[1], depth + 1, seen)
        return [_resolve(data, x, depth + 1, seen) for x in v]
    if isinstance(v, dict):
        return {k: _resolve(data, val, depth + 1, seen) for k, val in v.items()}
    return v


def parse_products(html: str) -> tuple[list[dict], dict[int, str]]:
    """Extract ``__NUXT_DATA__``, resolve the devalue payload, and return
    (products, category-id -> category-name)."""
    m = re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError(
            "Could not find __NUXT_DATA__ in the /uppskriftir page. "
            "Site structure may have changed — open an issue with the page HTML."
        )
    data = json.loads(m.group(1))
    root = _resolve(data, 0) or {}
    state = root.get("state", {}) or {}
    products = state.get("$sproducts") or []
    cats_node = state.get("$scategories") or []
    cat_names: dict[int, str] = {}
    _collect_category_names(cats_node, cat_names)
    return products, cat_names


def _collect_category_names(node, out: dict[int, str]) -> None:
    if isinstance(node, dict):
        cid = node.get("category_id", node.get("id"))
        if cid is not None and "name" in node:
            out[cid] = node["name"]
        for v in node.values():
            _collect_category_names(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_category_names(v, out)


# ---------------------------------------------------------------------------
# Category classification (brew.is ingredient categories -> BeerSmith type)
# ---------------------------------------------------------------------------

def normalise(s: str) -> str:
    """Lowercase, strip accents/diacritics, replace ð/þ/æ with ASCII digraphs,
    keep only [a-z0-9 ]. Used for matching category names and ingredients."""
    if not s:
        return ""
    s = unescape(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ð", "d").replace("þ", "th").replace("æ", "ae")
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Equipment-only items that occasionally land in ingredient categories on brew.is
EQUIPMENT_TOKENS = (
    "segulhraera", "segulhrera", "whipper", "stirrer",
    "steinn", "micron", "syrefnissteinn", "surefnissteinn",
)


def _is_equipment(name: str) -> bool:
    n = normalise(name)
    return any(tok in n for tok in EQUIPMENT_TOKENS)


def category_type(category_name: str) -> str | None:
    """Map a brew.is category name to a BeerSmith ingredient type
    (grain/hops/yeast/misc) or None for equipment/recipe categories."""
    n = normalise(category_name)
    if n == "ger":                                       # NOT "gerjunarilat" (fermenters)
        return "yeast"
    if "humla" in n or "amerisk" in n or "evropsk" in n:
        return "hops"
    if "korn" in n or "crystal" in n or "karamell" in n or "rista" in n or "malt" in n:
        return "grain"
    if "baetiefni" in n or "krydd" in n or "bragdefni" in n:
        return "misc"
    return None


def classify_product(product: dict, cat_names: dict[int, str]) -> str | None:
    """Determine the BeerSmith ingredient type for a product, or None if it's
    equipment / a recipe kit / outside our scope. Picks the most-specific
    type when a product belongs to multiple categories."""
    if _is_equipment(product.get("name", "")):
        return None
    if "thurrmalt" in normalise(product.get("name", "")) or \
       "dme" in normalise(product.get("name", "")):
        # Dry malt extract is filed under "Ger" on brew.is but belongs in grain.
        return "grain"
    types: set[str] = set()
    for cid in product.get("categories") or []:
        t = category_type(cat_names.get(cid, ""))
        if t:
            types.add(t)
    for preferred in ("yeast", "hops", "grain", "misc"):
        if preferred in types:
            return preferred
    return None


# ---------------------------------------------------------------------------
# Pack-size parsing (brew.is encodes pack sizes in product names)
# ---------------------------------------------------------------------------

# Longer unit tokens come first so "100gr" matches "gr" not "g"
_AMOUNT_RE = re.compile(
    r"(\d+[.,]?\d*)\s*(gramm|pakkn|kg|gr|ml|stk|pk|g|l)\b", re.I
)


def parse_pack_amount(name: str) -> tuple[tuple[float, str] | None, str]:
    """Pull a pack size like "50gr" / "1 kg" / "11,5 g" off the product name.
    Returns ((quantity, unit), clean_name_without_pack)."""
    m = _AMOUNT_RE.search(name)
    if not m:
        return None, name
    qty = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    base = (name[: m.start()] + name[m.end():]).strip(" -/,")
    return (qty, unit), base


def pack_to_grams(amount: tuple[float, str] | None) -> float | None:
    """Convert a (qty, unit) tuple from ``parse_pack_amount`` to grams.
    Returns None if amount is None or the unit isn't a weight."""
    if not amount:
        return None
    qty, unit = amount
    unit = unit.lower()
    if unit == "kg":
        return qty * 1000.0
    if unit in ("g", "gr", "gramm"):
        return qty
    return None  # ml / stk / pk — not a weight


# ---------------------------------------------------------------------------
# In-stock filter
# ---------------------------------------------------------------------------

def in_stock(products: Iterable[dict], cat_names: dict[int, str] | None = None,
             ingredient_only: bool = True) -> Iterator[dict]:
    """Yield products with positive ``quantity`` (brew.is's checkmark).
    When ``ingredient_only=True``, also drop anything that doesn't classify
    as grain/hops/yeast/misc."""
    cat_names = cat_names or {}
    for p in products:
        q = p.get("quantity")
        if not isinstance(q, (int, float)) or q <= 0:
            continue
        if ingredient_only:
            t = classify_product(p, cat_names)
            if not t:
                continue
            p = {**p, "_bs_type": t}
        yield p
