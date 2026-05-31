"""Order page + brew.is Recipe Machine driver.

For a given BeerSmith recipe id, walk its Ingredients, map each line to a
brew.is product (using the (brew.is)-tagged library as the catalog source,
with family-aware fuzzy matching as the fallback), round up to whole packs
where applicable, and render a one-page HTML shopping list.

When everything's matched and in stock, the page's main CTA is a live
``brewis://order/<id>/cart`` link that triggers ``fill_recipe_machine`` —
Playwright opens /uppskriftir and drops the order text into the textarea.

When something's blocking (missing from brew.is or insufficient stock), the
substitution engine kicks in: each blocker shows alternatives in the same
brewing family with pros/cons in Icelandic. Each substitute has its own
"Búa til afrit" (create clone) link, plus a single multi-clone button at the
bottom for picking several swaps at once.

Plain household sugars are treated as "pantry" items (Úr eldhúsi) — assumed
on hand at home, never blocking the order.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import math
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import beersmith as bs
from . import matching as mm

UPPSKRIFTIR_URL = "https://www.brew.is/uppskriftir"

# Pantry items — assumed-on-hand household goods that brew.is doesn't sell.
PANTRY_KEYWORDS = (
    "sugar, table", "table sugar", "sucrose", "white sugar", "cane sugar",
    "beet sugar", "granulated sugar",
    "hvítur sykur", "hvitur sykur", "borðsykur", "bordsykur",
    "venjulegur sykur", "strásykur", "strasykur",
)


# ---------------------------------------------------------------------------
# Catalog (parses (brew.is) library entries + their notes)
# ---------------------------------------------------------------------------

_NOTE_FIELD = re.compile(r"(SKU|Stock qty|Price|Pack)\s*:\s*([^|]+?)(?=\s*\||$)", re.I)
_NOTE_URL   = re.compile(r"https://www\.brew\.is/p/(\S+)")


def _int(s, default=0):
    try:
        return int(re.sub(r"[^\d-]", "", (s or "").split()[0]))
    except (ValueError, IndexError):
        return default


def _pack_to_g(s):
    if not s:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|gr|g)\b", s, re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v * (1000 if m.group(2).lower() == "kg" else 1)


def _parse_notes(notes: str) -> dict:
    out = {}
    for m in _NOTE_FIELD.finditer(notes or ""):
        out[m.group(1).lower()] = m.group(2).strip()
    u = _NOTE_URL.search(notes or "")
    if u:
        out["keyword"] = u.group(1)
    return out


# Extra spec columns loaded into the catalog so the matcher and substitution
# engine can compare brewing properties.
_EXTRA_SPECS = {
    "grain": ["F_G_COLOR", "F_G_YIELD", "F_G_TYPE", "F_G_ORIGIN"],
    "hops":  ["F_H_ALPHA", "F_H_BETA", "F_H_TYPE", "F_H_ORIGIN"],
    "yeast": ["F_Y_MIN_ATTENUATION", "F_Y_MAX_ATTENUATION",
               "F_Y_FLOCCULATION", "F_Y_MIN_TEMP", "F_Y_MAX_TEMP", "F_Y_LAB"],
    "misc":  ["F_M_TYPE"],
}


def load_catalog(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Return ``{'grain': [...], 'hops': [...], 'yeast': [...], 'misc': [...]}``,
    one dict per (brew.is)-tagged library row, with notes-parsed metadata and
    extra spec columns for matching/substitution."""
    cat: dict[str, list[dict]] = {t: [] for t in bs.LIBRARY_TABLE}
    for t, (table, name_col, notes_col) in bs.LIBRARY_TABLE.items():
        extras = _EXTRA_SPECS[t]
        cols = ", ".join([name_col + " AS name", notes_col + " AS notes",
                          "_PERMID_"] + extras)
        for r in conn.execute(
            f"SELECT {cols} FROM {table} WHERE {name_col} LIKE ?", (f"%{bs.TAG}",)
        ):
            meta = _parse_notes(r["notes"])
            display = r["name"].replace(bs.TAG, "").strip()
            entry = {
                "permid": r["_PERMID_"], "type": t,
                "name": display, "tagged_name": r["name"],
                "alt_name": mm.translate(display),
                "sku": meta.get("sku", "-"),
                "keyword": meta.get("keyword", ""),
                "pack_g": _pack_to_g(meta.get("pack")),
                "price_isk": _int(meta.get("price")),
                "stock": _int(meta.get("stock qty")),
                "notes": r["notes"] or "",
            }
            for k in extras:
                entry[k] = r[k]
            cat[t].append(entry)
    return cat


# ---------------------------------------------------------------------------
# Order building
# ---------------------------------------------------------------------------

_SCHEMA_TYPE = {bs.SCHEMA["grain"]: "grain", bs.SCHEMA["hops"]: "hops",
                bs.SCHEMA["yeast"]: "yeast", bs.SCHEMA["misc"]: "misc"}
_AMOUNT_FIELD = {"grain": "F_G_AMOUNT", "hops": "F_H_AMOUNT",
                  "yeast": "F_Y_AMOUNT", "misc": "F_M_AMOUNT"}
_NAME_FIELD = {"grain": "F_G_NAME", "hops": "F_H_NAME",
                "yeast": "F_Y_NAME", "misc": "F_M_NAME"}


def is_pantry(name: str, t: str) -> bool:
    if t != "grain":
        return False
    n = (name or "").lower()
    return any(k in n for k in PANTRY_KEYWORDS)


@dataclass
class OrderLine:
    type: str
    match: dict
    sources: list = field(default_factory=list)    # [{name, label}, ...]
    total_grams: float = 0.0
    total_packs: int = 0
    match_score: float = 1.0
    packs: int | None = None                        # None = sold by weight


def build_order(recipe: sqlite3.Row, catalog: dict[str, list[dict]]):
    """Walk the recipe's Ingredients, aggregate by matched product, and round
    each weight-sold item up to whole packs.

    Returns ``(lines, nomatch, pantry)`` where ``lines`` is matched products
    ready to order; ``nomatch`` is per-original-name (with suggestion for
    "did you mean"); ``pantry`` is household items we don't expect from
    brew.is.
    """
    ings = json.loads(recipe["Ingredients"]) if recipe["Ingredients"] else []
    bucket: dict[int, OrderLine] = {}
    nomatch: list[dict] = []
    pantry: list[dict] = []

    for i in ings:
        t = _SCHEMA_TYPE.get(i.get("_Schema_"))
        if not t:
            continue
        name = i[_NAME_FIELD[t]]
        amount = float(i[_AMOUNT_FIELD[t]])
        grams = None if t == "yeast" else amount * 28.3495231       # oz -> g
        yeast_pk = max(1, int(math.ceil(amount))) if t == "yeast" else None

        # Build a human-readable per-addition label
        if t == "hops":
            use = i.get("F_H_USE", "0")
            mins = int(float(i.get("F_H_BOIL_TIME", "0") or 0))
            tag = {"0": f"suða {mins}m", "1": "þurrhumlun",
                    "3": "fyrir suðu", "4": f"whirlpool {mins}m"}.get(use, f"notkun {use}")
            label = f"{grams:.0f} g @ {tag}"
        elif t == "yeast":
            label = f"{yeast_pk} pk"
        elif grams >= 1000:
            label = f"{grams/1000:.2f} kg"
        else:
            label = f"{grams:.0f} g"

        if is_pantry(name, t):
            pantry.append({"name": name, "grams": grams})
            continue

        alt = i.get("F_Y_PRODUCT_ID") if t == "yeast" else None
        prod, score, suggestion = mm.match_product(
            name, t, catalog, alt_name=alt, ing_meta=i)
        if not prod:
            nomatch.append({"type": t, "name": name, "label": label,
                            "score": score, "suggestion": suggestion, "ing": i})
            continue
        key = prod["permid"]
        if key not in bucket:
            bucket[key] = OrderLine(type=t, match=prod, match_score=score)
            bucket[key].first_ing = i      # type: ignore[attr-defined]
        L = bucket[key]
        L.sources.append({"name": name, "label": label})
        if t == "yeast":
            L.total_packs += yeast_pk
        else:
            L.total_grams += grams
        L.match_score = min(L.match_score, score)

    lines = []
    for L in bucket.values():
        p = L.match
        if L.type == "yeast":
            L.packs = max(1, L.total_packs)
        elif p["pack_g"]:
            L.packs = max(1, math.ceil(L.total_grams / p["pack_g"] - 1e-6))
        else:
            L.packs = None
        lines.append(L)
    order_t = {"grain": 0, "hops": 1, "yeast": 2, "misc": 3}
    lines.sort(key=lambda r: (order_t[r.type], -(r.total_grams or r.total_packs or 0)))
    return lines, nomatch, pantry


def line_cost(L: OrderLine) -> int:
    p = L.match
    price = p.get("price_isk") or 0
    if not price:
        return 0
    if L.type == "yeast":
        return L.packs * price
    if L.packs:
        return L.packs * price
    return round(price * L.total_grams / 1000)


# ---------------------------------------------------------------------------
# Blockers + substitution surface
# ---------------------------------------------------------------------------

def newly_orderable(pre: dict[int, dict],
                     post: dict[int, dict]) -> list[tuple[int, str]]:
    """Diff two ``all_recipe_blockers`` snapshots.

    Returns ``[(permid, name), ...]`` sorted by name for every recipe that:
      * existed in *both* snapshots,
      * was *blocked* in ``pre`` (non-empty blockers),
      * is *now fully orderable* in ``post`` (empty blockers).

    A recipe that picked up a different blocker in the new sync (still
    blocked, just by something else) is NOT returned — only the full
    blocked→orderable flip counts. A recipe that was already orderable
    pre-sync also isn't returned (no flip).
    """
    flipped: list[tuple[int, str]] = []
    for rid, p in post.items():
        if rid not in pre:
            continue                           # newly-added recipe — no diff
        if pre[rid]["blockers"] and not p["blockers"]:
            flipped.append((rid, p["name"]))
    flipped.sort(key=lambda x: x[1])
    return flipped


def all_recipe_blockers(conn: sqlite3.Connection,
                         catalog: dict[str, list[dict]]) -> dict[int, dict]:
    """Snapshot every recipe's blocker set against ``catalog``.

    Returns ``{permid: {"name": str, "blockers": frozenset[str]}}``. An
    empty ``blockers`` value means the recipe is fully orderable against
    the supplied catalog. ``frozenset`` makes the values hashable so
    callers can do set-style diffs cheaply.

    Used by :func:`brewbridge.core.sync.run` to compute which recipes
    flip from blocked to orderable between the pre-sync and post-sync
    catalog states — that diff is what feeds the "now orderable" tray
    notification.
    """
    out: dict[int, dict] = {}
    for r in conn.execute(
        "SELECT * FROM M_RECIPE ORDER BY F_R_NAME"
    ):
        lines, nomatch, _pantry = build_order(r, catalog)
        blockers = compute_blockers(lines, nomatch, catalog)
        out[r["_PERMID_"]] = {
            "name": r["F_R_NAME"],
            "blockers": frozenset(b["name"] for b in blockers),
        }
    return out


def compute_blockers(lines: list[OrderLine], nomatch: list[dict],
                     catalog: dict[str, list[dict]] | None = None) -> list[dict]:
    """One entry per blocked ingredient (deduped by name). When ``catalog`` is
    given, each entry carries ``substitutes`` = list of in-stock alternatives
    from the same brewing family."""
    seen: set[str] = set()
    out: list[dict] = []

    def add(name, reason, t=None, ing=None):
        if name in seen:
            return
        seen.add(name)
        entry = {"name": name, "reason": reason, "type": t, "ing": ing or {}}
        if catalog is not None and t:
            entry["substitutes"] = mm.find_substitutes(entry, catalog)
        out.append(entry)

    for L in lines:
        p = L.match
        stock = p.get("stock")
        if L.packs and stock is not None and L.packs > stock:
            add(p["name"], f"aðeins {stock} í birgðum, þarf {L.packs}",
                t=L.type, ing=getattr(L, "first_ing", None))
    for nm in nomatch:
        add(nm["name"], "ekki til hjá brew.is",
            t=nm["type"], ing=nm.get("ing"))
    return out


# ---------------------------------------------------------------------------
# Recipe Machine text + Playwright driver
# ---------------------------------------------------------------------------

def textarea_line(L: OrderLine) -> str:
    """One line for brew.is's /uppskriftir textarea: ``AMOUNT NAME``. Grain by
    kg, hops/yeast/misc by whole pack count."""
    p = L.match
    if L.type == "yeast":
        return f"{L.packs} {p['name']}"
    if L.packs:
        return f"{L.packs} {p['name']}"
    return f"{L.total_grams/1000:g} {p['name']}"


def fill_recipe_machine(lines: list[OrderLine], *, headless: bool = False) -> bool:
    """Drive Playwright: open /uppskriftir, drop the order into the textarea.
    Leaves the browser open so the user reviews and clicks Næsta skref."""
    text = "\n".join(textarea_line(L) for L in lines if L.match)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && "
              "playwright install chromium")
        return False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=40)
        ctx = browser.new_context(locale="is-IS",
                                  viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        page.goto(UPPSKRIFTIR_URL, wait_until="networkidle")
        page.wait_for_timeout(1200)
        ta = page.locator("textarea").first
        ta.wait_for(state="visible", timeout=15000)
        ta.fill(text)
        page.wait_for_timeout(400)
        print("Uppskrift sett í Uppskriftavélina. "
              "Yfirfarðu og smelltu á Næsta skref.")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# HTML order page rendering
# ---------------------------------------------------------------------------

def _need(L: OrderLine) -> str:
    if L.type == "yeast":
        return f"{L.total_packs} pk"
    g = L.total_grams
    return f"{g/1000:.2f} kg" if g >= 1000 else f"{g:.0f} g"


def _buy(L: OrderLine) -> str:
    p = L.match
    if L.type == "yeast":
        return f"{L.packs} × pk"
    if L.packs:
        pg = p["pack_g"]
        unit = f"{pg:g} g" if pg < 1000 else f"{pg/1000:g} kg"
        return f"{L.packs} × {unit}"
    return f"{L.total_grams/1000:.2f} kg (í lausu)"


# Full HTML template — see ``render_html`` for usage. Keeping this inline
# rather than templating a file because it's small and tightly coupled.
_HTML = """<!doctype html>
<html lang="is"><head><meta charset="utf-8">
<title>brew.is pöntun — {recipe}</title>
<style>
 body{{font-family:Segoe UI,sans-serif;margin:2em;background:#fafafa;color:#222}}
 h1{{margin-bottom:0.2em}} .meta{{color:#666;margin-bottom:1.5em}}
 table{{width:100%;border-collapse:collapse;background:white;
        box-shadow:0 1px 3px rgba(0,0,0,.08);border-radius:6px;overflow:hidden}}
 th,td{{padding:8px 12px;border-bottom:1px solid #eee;text-align:left;font-size:14px}}
 th{{background:#f0f0f0;font-weight:600}}
 tr.warn{{background:#fff7e0}} tr.bad{{background:#ffe0e0}}
 .pantry-banner{{background:#fff7e0;border-left:4px solid #f0c040;padding:8px 14px;
        border-radius:6px;margin:1em 0;font-size:14px}}
 .block-banner{{background:#ffe0e0;border-left:4px solid #c00;padding:12px 16px;
        border-radius:6px;margin:1em 0}}
 .block-banner ul{{margin:8px 0 4px 0;padding-left:1.5em}}
 .block-banner li{{margin:6px 0}}
 .sub-hint{{margin:4px 0 6px 0;padding:6px 10px;background:rgba(255,255,255,0.6);
        border-radius:4px;font-size:13px}}
 .sub-hint ul.subs{{margin:4px 0 0 0;padding-left:1em;list-style:none}}
 .sub-hint.no-sub{{color:#a00;font-style:italic}}
 .muted{{color:#888;font-size:0.9em}}
 .cta{{display:inline-block;background:#1a7ed6;color:white;padding:10px 18px;
        border-radius:6px;text-decoration:none;font-weight:600;margin-top:1.5em}}
 .cta:hover{{background:#1466ad}}
 .cta.disabled{{background:#bbb;color:#fff;cursor:not-allowed;pointer-events:none;
        font-weight:600}}
 a.clone-btn{{display:inline-block;background:#28a745;color:white;
        padding:1px 8px;border-radius:4px;text-decoration:none;
        font-size:12px;margin-left:6px;font-weight:600}}
 a.clone-btn:hover{{background:#218838}}
 button.multi-clone-btn{{background:#28a745;color:white;padding:10px 18px;border:none;
        border-radius:6px;font-weight:600;cursor:pointer;font-size:14px;
        margin-top:0.6em;display:inline-block}}
 button.multi-clone-btn:hover{{background:#218838}}
 .total{{font-size:1.3em;margin-top:1em}}
 .small{{color:#888;font-size:12px}}
</style></head><body>
<h1>{recipe}</h1>
<div class="meta">Lögun {batch} L · útbúið {generated}</div>
<table>
<thead><tr><th>Tegund</th><th>brew.is vara&nbsp;/ íbætingar</th><th>Þörf</th>
<th>Kaupa</th><th>Samsvörun</th><th>Birgðir</th><th style="text-align:right">Verð</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="total">Áætlað samtals: <b>{total} kr</b></div>
{pantry_banner}
{block_banner}
{cta}
</body></html>"""


def render_html(recipe_name: str, batch_l: float, lines: list[OrderLine],
                nomatch: list[dict], recipe_id: int,
                catalog: dict[str, list[dict]] | None = None,
                pantry: list[dict] | None = None) -> str:
    rows: list[str] = []
    total = 0
    type_is = {"grain": "korn", "hops": "humlar", "yeast": "ger", "misc": "annað"}
    for L in lines:
        p = L.match
        cost = line_cost(L)
        total += cost
        warn = ' class="warn"' if L.match_score < 0.85 else ""
        stock_w = ""
        if p.get("stock") is not None and L.packs and L.packs > p["stock"]:
            stock_w = ' class="bad"'
        link = (f'<a href="https://www.brew.is/p/{p["keyword"]}" target="_blank">'
                f'{_html.escape(p["name"])}</a>' if p.get("keyword")
                else _html.escape(p["name"]))
        sources = "<br>".join(f'<span class="src">{_html.escape(s["label"])}</span>'
                              for s in L.sources)
        rows.append(
            f'<tr{warn}><td>{type_is[L.type]}</td>'
            f'<td>{link}<div class="small">{sources}</div></td>'
            f'<td>{_need(L)}</td><td>{_buy(L)}</td>'
            f'<td>{L.match_score*100:.0f}%</td>'
            f'<td{stock_w}>{p.get("stock","?")}</td>'
            f'<td style="text-align:right">{cost:,} kr</td></tr>')
    for nm in nomatch:
        rows.append(
            f'<tr class="bad"><td>{type_is.get(nm["type"], nm["type"])}</td>'
            f'<td>{_html.escape(nm["name"])}<div class="small">'
            f'{_html.escape(nm["label"])}</div></td>'
            f'<td colspan=5><a href="#blockers" class="muted">'
            f'Sjá staðgöngur að neðan &darr;</a></td></tr>')

    blockers = compute_blockers(lines, nomatch, catalog) if catalog else []
    block_html = _render_blockers(blockers, recipe_id) if blockers else ""
    cta_html = (f'<a class="cta" href="brewis://order/{recipe_id}/cart">'
                f'Opna á brew.is (fylla Uppskriftavélina)</a>'
                if not blockers else
                '<span class="cta disabled" title="Vantar hráefni — sjá ofan">'
                'Ekki hægt að panta sjálfvirkt</span>')
    pantry_html = _render_pantry(pantry or [])

    return _HTML.format(
        recipe=_html.escape(recipe_name),
        batch=batch_l, total=f"{total:,}", recipe_id=recipe_id,
        rows="\n".join(rows),
        pantry_banner=pantry_html, block_banner=block_html, cta=cta_html,
        generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def _render_pantry(pantry: list[dict]) -> str:
    if not pantry:
        return ""
    bits = []
    for p in pantry:
        g = p["grams"] or 0
        label = f"{g/1000:.2f} kg" if g >= 1000 else f"{g:.0f} g"
        bits.append(f"{label} {_html.escape(p['name'])}")
    return (
        f'<div class="pantry-banner"><b>Úr eldhúsi:</b> {" · ".join(bits)} '
        f'<span class="muted">— sykur er gert ráð fyrir í eldhúsi, '
        f'brew.is selur hann ekki.</span></div>'
    )


def _render_blockers(blockers: list[dict], recipe_id: int) -> str:
    items: list[str] = []
    any_subs = False
    sub_count = 0
    for bidx, b in enumerate(blockers):
        subs = b.get("substitutes") or []
        if subs:
            any_subs = True
            sub_count += 1
            sub_lis: list[str] = []
            for sidx, s in enumerate(subs):
                pname = s["prod"]["name"]
                encoded = urllib.parse.quote(f"{b['name']}>>{pname}", safe="")
                clone_url = f'brewis://clone/{recipe_id}?sub={encoded}'
                checked = " checked" if sidx == 0 else ""
                name_label = f'<b>{_html.escape(pname)}</b>'
                if s["prod"].get("keyword"):
                    name_label = (f'<a href="https://www.brew.is/p/'
                                  f'{s["prod"]["keyword"]}" target="_blank">'
                                  f'{name_label}</a>')
                sub_lis.append(
                    f'<li><label>'
                    f'<input type="radio" name="sub-{bidx}" value="{encoded}"{checked}> '
                    f'{name_label}: {_html.escape(s["note"])} '
                    f'<span class="muted">({s["prod"]["stock"]} til)</span>'
                    f'</label> '
                    f'<a class="clone-btn" href="{clone_url}">Búa til afrit</a></li>')
            sub_block = (f'<div class="sub-hint">Hægt að skipta út fyrir:'
                         f'<ul class="subs">{"".join(sub_lis)}</ul></div>')
        else:
            sub_block = ('<div class="sub-hint no-sub">'
                          'Engin hentug staðganga til hjá brew.is.</div>')
        items.append(
            f'<li><b>{_html.escape(b["name"])}</b> — '
            f'{_html.escape(b["reason"])}{sub_block}</li>')

    headline = ("<b>Þessa uppskrift er ekki hægt að brugga með birgðum brew.is núna</b>"
                if not any_subs
                else "<b>Ekki er hægt að panta sjálfvirkt</b>")
    tail = ("Engin staðganga til — íhugaðu aðra uppskrift eða annan birgi."
            if not any_subs
            else "Veldu staðgöngu eða lagaðu uppskriftina í BeerSmith.")
    multi = ""
    if sub_count >= 2:
        multi = (
            f'<button type="button" class="multi-clone-btn" '
            f'onclick="multiClone({recipe_id})">'
            f'Búa til afrit með völdum staðgöngum '
            f'({sub_count} af {len(blockers)} hráefnum)</button>'
            f'<script>function multiClone(rid){{'
            f'var r=document.querySelectorAll(\'input[name^="sub-"]:checked\');'
            f'if(!r.length)return;'
            f'var p=[].map.call(r,function(x){{return "sub="+x.value;}}).join("&");'
            f'window.location.href="brewis://clone/"+rid+"?"+p;}}</script>')
    return (
        f'<div id="blockers" class="block-banner">{headline} — '
        f'{len(blockers)} hráefni vantar eða er ekki nóg af hjá brew.is:'
        f'<ul>{"".join(items)}</ul>{tail}{multi}</div>'
    )


# ---------------------------------------------------------------------------
# URL parsing for the brewis:// protocol
# ---------------------------------------------------------------------------

def parse_uri(s: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Return ``(ident, mode, swaps)`` for a brewis:// URL or bare id/name.

    - ``brewis://order/<id>``        -> mode 'report', no swaps
    - ``brewis://order/<id>/cart``   -> mode 'fill'
    - ``brewis://clone/<id>?sub=A>>B`` -> mode 'clone', swaps=[(A,B),...]
    """
    if s.startswith("brewis://"):
        u = urllib.parse.urlparse(s)
        parts = (u.netloc + "/" + u.path.lstrip("/")).strip("/").split("/")
        if len(parts) >= 2:
            action, ident = parts[0], urllib.parse.unquote(parts[1])
            if action == "clone":
                swaps = []
                for raw in urllib.parse.parse_qs(u.query).get("sub", []):
                    if ">>" in raw:
                        a, b = raw.split(">>", 1)
                        swaps.append((urllib.parse.unquote(a),
                                      urllib.parse.unquote(b)))
                return ident, "clone", swaps
            if action == "order":
                mode = "fill" if (len(parts) >= 3 and parts[2] == "cart") else "report"
                return ident, mode, []
    return s, "report", []


# ---------------------------------------------------------------------------
# Recipe lookup helper
# ---------------------------------------------------------------------------

def find_recipe(conn: sqlite3.Connection, ident: str | int) -> sqlite3.Row | None:
    """Look up a recipe by _PERMID_ (if numeric) or fuzzy name match."""
    if str(ident).isdigit():
        r = conn.execute("SELECT * FROM M_RECIPE WHERE _PERMID_=?",
                         (int(ident),)).fetchone()
        if r:
            return r
    needle = mm.norm(str(ident))
    best, score = None, 0.0
    for r in conn.execute("SELECT * FROM M_RECIPE"):
        s = mm.similarity(needle, r["F_R_NAME"])
        if s > score:
            best, score = r, s
    return best if score > 0.6 else None
