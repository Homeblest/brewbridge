# Changelog

All notable changes to brewbridge. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/spec/v2.0.0.html).

## [0.1.2] — 2026-06-07

### Added

- `brewbridge doctor` subcommand — read-only install verifier. Checks the
  full chain in one shot: PATH, brewis:// URL handler, BeerSmith.sqlite
  reachability, BrewIsOrder.htm template, mash + water profiles in
  BeerSmith, specs_reference.json populated, daily-sync scheduled task,
  last-sync state. Each check pass/fail with a specific remediation hint.
- Daily-sync Windows Scheduled Task — `brewbridge install` now registers
  `BrewbridgeSync` to run `brewbridge sync` daily at 06:00. Closes the
  "I forgot to sync" gap; the legacy `brewis-beersmith` setup had this
  but the rewrite hadn't ported it yet.
- Tray singleton — `brewbridge-tray` refuses to launch a second instance.
  Uses a Windows named mutex (`brewbridge_tray_singleton_v1`) so the
  second invocation logs the rejection and exits cleanly. Belt-and-
  suspenders against accidental Start-Menu double-clicks.
- Tray exception logging — any unhandled exception in the tray's
  `_run_in_thread` callbacks now writes to `~/.brewbridge/tray.log`
  instead of disappearing into a stderr that doesn't exist in the
  windowed PyInstaller bundle. Failure notifications point users at
  the log path.
- GitHub Actions CI — `pytest` on Windows + Ubuntu × Python 3.10/3.11/3.12,
  plus `ruff` lint.
- CHANGELOG.md, CONTRIBUTING.md, SECURITY.md.

### Fixed

- **Order routing for recipe versions.** Clicking the "▶ Order at brew.is"
  button on a recipe like "Bölvað Bull v3" used to route to "Bölvað Bull"
  (the original) because `mm.similarity` clamps at 1.0 and the +0.15
  substring bonus tied the prefix match with the exact match. iteration
  order then picked the wrong one. `find_recipe` now does an exact
  normalised-name match before falling back to fuzzy similarity.
- **Tray picker spawning duplicate tray icons + not opening Chromium.**
  `subprocess.Popen([sys.executable, "-m", "brewbridge", "order", url])`
  in the frozen tray invoked `brewbridge-tray.exe -m brewbridge order …`,
  which ignored argv and just opened another tray. Now detects frozen
  mode and invokes the sibling `brewbridge.exe` with the URL.
- **Dingemans Wheat showing out-of-stock when it isn't** (the user's
  /p/1189 case). brew.is products can reference category IDs that aren't
  in `$scategories` (orphan IDs). `classify_product` now falls back to a
  name + description heuristic for products with zero known categories.
  Tightly scoped: products in known-but-not-ingredient categories
  (Uppskriftir, Bjórgerðartæki, Mælitæki, …) are still left alone.
- **MSI version stacking.** Reinstalling at the same `Version` left the
  old install behind because MajorUpgrade only fires on a higher version.
  Discipline now: bump `__version__` per release; verified with the
  0.1.1 → 0.1.2 upgrade replacing cleanly in one step.
- **`build.ps1` failing on PyInstaller's stderr output.**
  `$ErrorActionPreference = "Stop"` turned every PyInstaller INFO line
  into a NativeCommandError. Wrapped the PyInstaller + wix invocations
  in a local `ErrorActionPreference = "Continue"` scope; we still check
  `$LASTEXITCODE` explicitly so real failures aren't missed.
- **`audit --fix` schema mismatch on yeast attenuation.** BeerSmith
  stores yeast attenuation as a min/max range (`F_Y_MIN_ATTENUATION` /
  `F_Y_MAX_ATTENUATION`) in M_YEAST but recipes use a single
  `F_Y_ATTENUATION`. `fix_yeast_attenuation` now reads min/max and
  writes the midpoint.
- **Fermentis dry yeasts unmatched in spec library.** brew.is sells
  "Fermentis S-04 / US-05 / W-34/70 / BE-256 / T-58 / S-33 / W-68";
  BeerSmith's built-in library names them by sub-brand ("SafAle English
  Ale", "Saflager German Lager", …). Added phrase aliases mapping each
  Fermentis product code to its canonical sub-brand name.
- **0/N spec matches on first sync.** `load_spec_reference` short-
  circuited on the existence of `~/.brewbridge/specs_reference.json`
  but didn't check whether the file had rows. An empty snapshot from a
  prior run kept hitting silently. Added is-empty guard + bundled fallback
  (`src/brewbridge/assets/specs_reference.json`, 1.3 MB) so fresh
  installs always have spec data to match against.
- **`last_sync.txt` only written from the tray.** CLI / cron / scheduled-
  task syncs left the file stale, so the tray icon often reported the
  last *tray* sync rather than the last sync of any kind. Moved the
  state write into `sync.run()` itself.
- **Grain bucket misclassification.** `grain_bucket` lumped wheat malt,
  peated malt, and pilsner all into `base_pale_ale` (color-fallback),
  so the cross-family penalty didn't fire. Added explicit `wheat_malt`,
  `smoked`, `rye` buckets.
- **Frozen-build relative import crash.** `__main__.py` and `tray.py`
  used relative imports (`from . import setup`) that worked under
  `python -m brewbridge` but raised `ImportError` when PyInstaller ran
  them as bare entry scripts. Converted to absolute imports.
- **MSI INSERTing synthetic match-helper "name" column.** The matcher
  added a `"name"` key to candidate dicts during scoring; that key
  flowed through `_row_for` into the INSERT and BeerSmith's schema has
  no such column. `_row_for` now filters the matched dict against the
  template's column list.

### Changed

- Recipe lookup is now exact-match-first. Same call site, fuzzy fallback
  unchanged for typo tolerance.
- Order HTML now shows substitutes for blocked ingredients with brewing
  context (alpha %, attenuation, family), and offers a one-click "make
  a clone with these subs applied" button.

## [0.1.0] — 2026-05-30

Initial release. Core feature set:

- Daily brew.is catalog sync into BeerSmith 4's M_GRAIN / M_HOPS /
  M_YEAST / M_MISC libraries, tagged `(brew.is)`.
- brew.is recipe import (22 recipes) with BJCP styles, mash profiles,
  and full grain / hop / yeast bills.
- One-click ordering: BeerSmith report button → `brewis://order/<name>`
  → either renders an HTML shopping list or opens brew.is's
  `/uppskriftir` Recipe Machine pre-filled via Playwright Chromium.
- Substitution engine for blocked ingredients (hops by family, yeast
  by family, grain by color bucket).
- Try-before-you-buy: clone the recipe with chosen substitutions into
  BeerSmith and view OG/IBU/colour shift.
- Icelandic UI throughout.
- Windows MSI installer (PyInstaller + WiX v4).
- macOS .app build pipeline structurally complete (untested on real
  hardware as of v0.1.0).
