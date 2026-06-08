# Changelog

All notable changes to brewbridge. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/spec/v2.0.0.html).

## [0.1.8] — 2026-06-08

### Fixed

- **No more cmd-window flash when clicking the order button.** Windows
  was firing `brewbridge.exe` (built with `console=True` for terminal
  output) as the `brewis://` URL handler, which made a console window
  briefly pop up and close for every click. Added a sibling
  `brewbridge-url.exe` (`console=False`, same code path) and pointed
  the URL handler registration at it. The CLI `brewbridge.exe` stays
  console-y for `brewbridge sync` etc. from a terminal. Tray's
  internal subprocess calls also use the windowed sibling.

  Effect: clicking the order button in BeerSmith now goes silently
  from BeerSmith → brew.is form pre-filled, no flicker.

  MSI grew slightly (~5 MB) for the additional binary.

## [0.1.7] — 2026-06-08

### Fixed

- **Icelandic toast wording was nonsense.** The verb stem "samrú-"
  ("samrúmd" / "samrúmir" / "samrúmningur") doesn't exist in
  Icelandic — I'd invented it as a translation of "sync." The 0.1.6
  notifications used these made-up forms throughout. Now uses the
  correct verb **uppfæra** ("to update") and the noun **uppfærsla**
  ("an update"), which is what Icelandic software actually uses for
  catalog refreshes. Also removed mixed English/Icelandic
  ("Brew.is sync started…" / "Brew.is sync complete.") so the
  notifications read consistently in one language.

  New wording:
    - Title (success): `brew.is uppfærsla — tókst`
    - Title (failure): `brew.is uppfærsla — mistókst`
    - Title (BeerSmith open): `brew.is uppfærsla — bíður eftir BeerSmith`
    - Body (success): `112 hráefni á lager hjá brew.is, þar af 85 með nákvæm brugggildi.`
    - Body (failure): `Uppfærsla mistókst: <reason>`

## [0.1.6] — 2026-06-08

### Added

- **Windows toast notifications now include the actual reason** when
  sync fails, instead of the previous "Sync failed — see tray.log"
  stub. Reason is the underlying exception message (e.g. "BeerSmith
  is running — close it before syncing", or whatever the live error
  was), truncated to a single sentence. Full traceback still goes
  to `~/.brewbridge/tray.log` for diagnosis.

- **Success notification now includes concrete numbers** instead of
  the generic "Brew.is sync complete." stub. Format:
  `112 hráefni samrúmd, 85 með stillingum af 112.` When recipes flip
  from blocked → orderable, a second line names the top 3 with a
  `(+N)` overflow indicator.

### Changed

- `_run_in_thread`'s `on_done` callback signature changed from
  `Callable[[bool], None]` (success boolean) to
  `Callable[[Exception | None], None]` so the callback has the
  exception object to pull `str(err)` from. Internal API — no
  external callers affected.

## [0.1.5] — 2026-06-07

Tray UX polish — fixes two papercuts users hit immediately on their
first real use of v0.1.4.

### Fixed

- **Tray icon stuck red after a successful CLI/cron sync.** The
  previous design only refreshed the icon inside `_action_sync.done()`,
  so any sync that ran outside the tray (CLI invocation, scheduled
  task, external script) left the icon stale until the user clicked
  Synca núna themselves. A background thread now polls
  `~/.brewbridge/last_sync.txt`'s mtime every 30 s and re-renders the
  icon when the state file changes. The watcher swallows any error
  silently — losing one poll is fine, killing the tray over it isn't.

- **"BeerSmith is running" failure was unfriendly.** Clicking Synca
  núna with BeerSmith open used to invoke sync, hit the RuntimeError,
  and surface "Sync failed — see tray.log" to the user. The tray now
  checks `bs.is_running()` upfront and shows a friendly Icelandic
  notification ("Vinsamlegast lokaðu BeerSmith áður en þú samrúmir...")
  asking them to close BeerSmith. No log dig required.

## [0.1.4] — 2026-06-07

**First release where the headline feature — "click order in BeerSmith,
brew.is Recipe Machine opens pre-filled" — actually works end-to-end
for MSI users.** Earlier releases shipped with the click broken in
ways that only surfaced under real-world testing.

### Fixed

- **BeerSmith report button now fires `brewis://order/<NAME>/cart`
  instead of `brewis://order/<NAME>`** — so a single click in the
  report viewer goes straight to fill mode (Chromium opens with the
  brew.is form pre-filled). The old URL went to "report mode" which
  generated an HTML order sheet; that sheet had its own CTA at
  `brewis://order/<id>/cart` but **Chrome / Edge silently block
  custom-protocol activations from `file://` origins** for security
  reasons, so the click was a no-op. The HTML order sheet is still
  generated for users who want to review the order; clicking the
  BeerSmith button is now the action path, not the preview path.

- **Playwright is now bundled in the MSI.** Earlier versions excluded
  it to keep the MSI under 20 MB, on the assumption users would
  `pip install playwright` themselves. MSI users don't have pip, and
  `fill_recipe_machine`'s ImportError fallback printed to a console
  that flashes and disappears. MSI is now 49.8 MB (up from 17.9 MB).

- **`PLAYWRIGHT_BROWSERS_PATH` is now pinned consistently** to
  `%LOCALAPPDATA%\ms-playwright` in both the install side
  (`brewbridge install` runs `playwright install chromium`) and the
  runtime side (`fill_recipe_machine`). Without this, bundled
  Playwright looked at `C:\Program Files\brewbridge\_internal\...\
  .local-browsers\` (read-only, doesn't exist) while the install
  step had downloaded Chromium to the user cache — the two halves
  disagreed and the launch failed with `BrowserType.launch:
  Executable doesn't exist`.

### Added

- **`brewbridge install` now installs Playwright's Chromium browser**
  via the Python entry point (`playwright.__main__:main`). One-time
  ~150 MB download into the user's standard Playwright cache. Surfaces
  in the install summary as `Playwright Chromium -> installed (or
  already present)`. Skipped on non-Windows.

- **HTML order sheet CTA now has a https:// fallback** so users who
  hit it via the tray's "Sýna innkaupalista (HTML)" picker still have
  a path forward when Chrome blocks the protocol activation. Styled
  as a subtle secondary link, not a button.

### Known limitations carried forward

- The MSI is still unsigned — Windows SmartScreen warns on first launch.
- macOS path still untested on real hardware.
- No bulk recipe import; users create recipes themselves in BeerSmith.

## [0.1.3] — 2026-06-07

### Changed (breaking-ish)

- **`brewbridge sync` is now non-destructive by default.** Previously the
  first sync wiped every non-(brew.is) ingredient row, which silently
  destroyed years of custom hop/yeast/grain entries for users coming
  from an established BeerSmith setup. New default: keep all existing
  library rows untouched, just add/refresh the (brew.is)-tagged ones.
- New `brewbridge sync --brew-is-only` flag opts back into the
  destructive mode for users who want their library to only show what's
  buyable at brew.is. Backup is always taken first.
- The old `--keep-builtins` flag is now a no-op (kept so scripts using
  it don't break).

### Added

- README quickstart rewritten for non-Python users — 7-step walkthrough
  from MSI download to first order, no jargon.

### Fixed

- Sync output now includes `deleted: N rows` line in brew.is-only mode
  so the destructive action is unambiguous.

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
