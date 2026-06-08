"""brewbridge — sync brew.is in-stock ingredients into BeerSmith 4 and order
recipes via the /uppskriftir Recipe Machine.

Public modules:
    brewbridge.core.beersmith   — BeerSmith.sqlite read/write, format quirks
    brewbridge.core.catalog     — brew.is catalog (Nuxt scraper)
    brewbridge.core.matching    — ingredient matcher (family-aware)
    brewbridge.core.sync        — daily catalog sync into M_GRAIN/HOPS/YEAST/MISC
    brewbridge.core.orders      — HTML shopping list + Playwright form-fill
    brewbridge.core.recipes     — brew.is recipe import
    brewbridge.core.audit       — recipe sanity audit + auto-fix
    brewbridge.tray             — system-tray icon

Entry points:
    `brewbridge`         — CLI (sync / order / recipes / audit / install)
    `brewbridge-tray`    — system-tray app
"""
__version__ = "0.1.14"
