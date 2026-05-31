"""brewbridge CLI.

::

    brewbridge sync              # mirror brew.is catalog into BeerSmith library
    brewbridge order <recipe>    # render shopping list (HTML) for a recipe
    brewbridge order <recipe> --fill   # also open /uppskriftir pre-filled
    brewbridge audit             # check imported recipes for issues
    brewbridge audit --fix       # auto-fix yeast dates + rebuild mashes
    brewbridge install           # one-time setup (URL protocol, report template)
    brewbridge tray              # launch the system-tray icon

Also accepts ``brewis://order/<id>[/cart]`` and ``brewis://clone/<id>?sub=...``
URLs directly (so the registered Windows URL handler can route through one
binary).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _reconfigure_stdout():
    # Windows console (cp1252) chokes on Icelandic characters; UTF-8 + replace
    # keeps us safe regardless of where output ends up.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def cmd_sync(args):
    from .core import sync
    res = sync.run(purge_builtins=not args.keep_builtins)
    print(f"\n  inserted: {sum(res.inserted.values())} rows "
          f"(grain {res.inserted['grain']}, hops {res.inserted['hops']}, "
          f"yeast {res.inserted['yeast']}, misc {res.inserted['misc']})")
    print(f"  matched specs: {res.matched} / {res.products}")
    print(f"  backup: {res.backup}")
    print(f"  report: {res.report_path}")


def cmd_order(args):
    import datetime as dt
    import re
    import webbrowser
    from .core import beersmith as bs
    from .core import orders

    ident, uri_mode, swaps = orders.parse_uri(args.target)
    conn = bs.open_db()
    r = orders.find_recipe(conn, ident)
    if not r:
        sys.exit(f"Engin uppskrift fannst sem passar við {ident!r}")

    if uri_mode == "clone" or args.clone:
        _handle_clone(conn, r, swaps or _parse_swap_args(args.clone or []))
        return

    catalog = orders.load_catalog(conn)
    lines, nomatch, pantry = orders.build_order(r, catalog)
    batch_l = (r["F_R_OLD_VOL"] or 0) / 33.814
    html = orders.render_html(r["F_R_NAME"], round(batch_l, 1), lines, nomatch,
                              r["_PERMID_"], catalog=catalog, pantry=pantry)
    out_dir = Path.home() / ".brewbridge" / "orders"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", r["F_R_NAME"])[:40]
    out = out_dir / f"{safe}_{dt.datetime.now():%Y%m%d_%H%M%S}.html"
    out.write_text(html, encoding="utf-8")
    print(f"Uppskrift: {r['F_R_NAME']} (id {r['_PERMID_']}, ~{batch_l:.1f} L)")
    print(f"Pöntun: {len(lines)} vörur, {len(nomatch)} hráefni án samsvörunar")
    print(f"Yfirlit: {out}")

    if args.fill or uri_mode == "fill":
        blockers = orders.compute_blockers(lines, nomatch, catalog)
        if blockers:
            print(f"\nVantar {len(blockers)} hráefni — ekki hægt að panta sjálfvirkt.")
            print(f"Opna yfirlit í vafra í staðinn: {out}")
            webbrowser.open(f"file:///{out.as_posix()}")
            return
        orders.fill_recipe_machine(lines)
    elif args.open:
        webbrowser.open(f"file:///{out.as_posix()}")


def _parse_swap_args(swaps_list: list[str]) -> list[tuple[str, str]]:
    out = []
    for s in swaps_list or []:
        if ">>" in s:
            a, b = s.split(">>", 1)
            out.append((a.strip(), b.strip()))
    return out


def _handle_clone(conn, recipe_row, swaps):
    import re
    import datetime as dt
    from .core import beersmith as bs
    from .core import platform as bb_platform
    from .core import recipes

    if not swaps:
        print("Engar staðgöngur tilgreindar — ekkert að gera.")
        return
    try:
        new_id, new_name, applied, missed, new_row = recipes.clone_recipe(
            conn, recipe_row["_PERMID_"], swaps)
    except RuntimeError as e:
        sys.exit(f"Afritun mistókst: {e}")
    print(f"\nBúið til afrit: {new_name}")
    for o, s, n in applied:
        print(f"  {o}  ->  {s}" + (f" (×{n} sinnum)" if n > 1 else ""))
    for o, s, why in missed:
        print(f"  [sleppt] {o} -> {s}  ({why})")

    # Write a .bsmx and hand it to BeerSmith's file association
    out_dir = Path.home() / ".brewbridge" / "orders"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", new_name)[:60]
    path = out_dir / f"{safe}.bsmx"
    path.write_text(recipes.recipe_to_bsmx(new_row), encoding="utf-8")
    print(f"\nBsmx skrá: {path}")
    try:
        bb_platform.open_path(path)
        print("Opnuð í BeerSmith.")
    except (AttributeError, FileNotFoundError, OSError):
        # OSError covers subprocess.run(check=True) failures on mac/linux;
        # AttributeError covers ancient Python without startfile; the
        # fallback message lets the user resolve it manually.
        print("Gat ekki opnað í BeerSmith. Tvísmelltu á .bsmx skrána handvirkt.")


def cmd_audit(args):
    from .core import audit
    res = audit.run(fix=args.fix)
    by_cat = {}
    for i in res.issues:
        by_cat.setdefault(i.category, []).append(i)
    print(f"=== Audit of {res.recipes_checked} recipes ===")
    for cat in ("mash", "match", "yeast_date", "color"):
        items = by_cat.get(cat, [])
        if items:
            print(f"\n--- {cat.upper()} ({len(items)}) ---")
            for it in sorted(items, key=lambda x: (x.severity, x.recipe)):
                print(f"  [{it.severity}] {it.recipe}: {it.message}")
    if args.fix:
        ly, ry = res.yeast_dates_fixed
        print(f"\nBackup: {res.backup}")
        print(f"Fixed yeast dates: {ly} library, {ry} recipes")
        print(f"Rebuilt mash for {res.mashes_rebuilt} recipes")
    elif not args.fix:
        n_auto = len(by_cat.get("yeast_date", [])) + len(by_cat.get("mash", []))
        if n_auto:
            print(f"\n{n_auto} auto-fixable issues. Re-run with --fix.")


def cmd_install(args):
    from . import setup as bb_setup
    print("brewbridge setup")
    try:
        s = bb_setup.install_all(skip_db=args.skip_db)
    except RuntimeError as e:
        sys.exit(f"  install failed: {e}")
    print(f"  brewis:// handler   -> {s['protocol_command']}")
    print(f"  Report template     -> {s['report_template']}")
    if "mash_profile_added" in s:
        print(f"  Brew.is einfaldur (M_MASH)   -> "
              f"{'added' if s['mash_profile_added'] else 'already present'}")
        print(f"  Reykjavík tap (M_WATER)      -> "
              f"{'added' if s['water_profile_added'] else 'already present'}")
    print()
    print("Next steps:")
    print("  1. Open BeerSmith -> Tools -> Options -> Reports -> Add Report.")
    print(f"     Browse to {s['report_template']} and import as type 'Recipe'.")
    print("  2. Run `brewbridge sync` to populate the brew.is ingredient library.")
    print("  3. Run `brewbridge tray` (or pin a shortcut to it) for everyday use.")


def cmd_tray(args):
    from . import tray
    tray.main()


def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdout()
    argv = argv if argv is not None else sys.argv[1:]

    # brewis:// URLs are passed as the first positional from the URL handler.
    # Route them straight into `order` so we don't need separate handlers.
    if argv and argv[0].startswith("brewis://"):
        argv = ["order", argv[0]]

    ap = argparse.ArgumentParser(prog="brewbridge", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="mirror brew.is catalog into BeerSmith")
    s.add_argument("--keep-builtins", action="store_true",
                   help="don't purge non-(brew.is) library rows")
    s.set_defaults(func=cmd_sync)

    o = sub.add_parser("order", help="render shopping list for a recipe")
    o.add_argument("target", help="recipe id, recipe name, or brewis:// URL")
    o.add_argument("--fill", action="store_true",
                   help="open /uppskriftir in Chromium and pre-fill the form")
    o.add_argument("--clone", nargs="+", metavar="ORIG>>SUB",
                   help="create a clone with one or more substitutions")
    o.add_argument("--open", action="store_true", default=True,
                   help="open the HTML page in your browser")
    o.add_argument("--no-open", dest="open", action="store_false")
    o.set_defaults(func=cmd_order)

    a = sub.add_parser("audit", help="check imported recipes")
    a.add_argument("--fix", action="store_true",
                   help="apply auto-fixes (yeast dates + mash rebuild)")
    a.set_defaults(func=cmd_audit)

    i = sub.add_parser("install", help="register URL protocol + install profiles")
    i.add_argument("--skip-db", action="store_true",
                   help="skip the M_MASH / M_WATER inserts (BeerSmith.sqlite "
                        "is locked / you've already done them)")
    i.set_defaults(func=cmd_install)

    t = sub.add_parser("tray", help="launch the system-tray icon")
    t.set_defaults(func=cmd_tray)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
