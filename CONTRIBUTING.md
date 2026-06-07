# Contributing to brewbridge

Bug reports, fixes, and supplier-engine extensions are all welcome. The
codebase is small enough (~3 KLOC Python + ~600 lines wxs/spec) that
finding your way around shouldn't take long.

## Dev setup

```bash
git clone https://github.com/Homeblest/brewbridge.git
cd brewbridge
python -m pip install -e .[dev]    # editable install + pytest + ruff

# Playwright is a runtime dep for the Recipe Machine driver (only used
# when --fill is requested). Skip if you're not iterating on that path.
playwright install chromium

python -m pytest tests/            # 135 tests; should be <1s
```

Editable install means `brewbridge <subcommand>` works against your
working tree. Useful for ordinary iteration; the MSI build is only
needed when you want to test the frozen-binary code path (URL handler
registration, tray singleton, etc).

## Architecture cheat sheet

- **`core/catalog.py`** — brew.is Nuxt-payload scraper. Knows how to
  decode the embedded product catalog from `__NUXT_DATA__` and classify
  each product as grain / hops / yeast / misc.
- **`core/matching.py`** — ingredient matcher. Phrase aliases (Icelandic
  → English), token translations, grain buckets, hop & yeast families,
  cross-family substitution scoring. Lots of brewing-domain knowledge
  encoded here.
- **`core/sync.py`** — the daily-sync pipeline. Fetches the catalog,
  matches each product against the frozen spec reference, writes the
  managed `(brew.is)` library rows. Also handles the pre/post snapshot
  for the "now orderable" notification.
- **`core/orders.py`** — order-sheet generation, blockers, substitution
  UI, Playwright form-fill.
- **`core/recipes.py`** — brew.is recipe import + recipe cloning with
  ingredient substitution.
- **`core/audit.py`** — periodic sanity checks + auto-fixes for imported
  recipes (yeast dates, yeast attenuation, mash profile rebinding).
- **`core/doctor.py`** — read-only install verifier behind `brewbridge
  doctor`.
- **`core/platform.py`** — Windows / macOS abstraction. `is_windows()`,
  `open_path()`, `beersmith_data_dir()`, etc.
- **`core/beersmith.py`** — BeerSmith.sqlite read/write. Encodes the
  format quirks: compact JSON with no whitespace, weights in ounces,
  yeast / mash re-binding on recipe open, etc.
- **`tray.py`** — pystray system-tray UI.
- **`setup.py`** — `brewbridge install` one-time setup: URL protocol,
  report template, mash + water profiles, daily-sync scheduled task.

## Schema quirks worth knowing

BeerSmith.sqlite has several invariants the matcher / writer pipeline
relies on. Don't change these without reading the related code:

- **Compact JSON, no whitespace.** Embedded ingredient blobs use
  `{"key":"value"}` not `{"key": "value"}`. BeerSmith's reader silently
  drops mash steps if there are spaces.
- **Unescaped inner quotes in F_R_MASH's `steps` field.** That's the
  one place BeerSmith's compact-JSON format leaks raw quote chars
  into a string-value. Bytes have to be exactly right.
- **Weights in ounces; volumes in fluid ounces.** Recipe imports must
  convert kg → oz (×35.27396), not lb (×2.20462).
- **Yeast attenuation is a min/max range in the library** (F_Y_MIN_/
  F_Y_MAX_ATTENUATION) but a single value in the embedded recipe copy
  (F_Y_ATTENUATION). Push library → recipe with the midpoint.
- **BeerSmith re-binds yeast and mash specs from the library when a
  recipe opens.** So library data has to stay fresh; recipe-embedded
  copies are best-effort caches.
- **Recipe ingredients live in a column literally called `Ingredients`
  on M_RECIPE.** Mixed case, no `F_` prefix. The other recipe columns
  follow the `F_R_*` convention.

## Adding a new supplier

The architecture supports multiple suppliers but only brew.is is
implemented today. To add one:

1. Drop a new module under `src/brewbridge/suppliers/<your_supplier>.py`.
   It exports `fetch_payload()`, `parse_products()`, `in_stock()`,
   `classify_product()` — same shape as `core/catalog.py` (which
   currently implements brew.is directly because we never broke it
   out).
2. Refactor `core/catalog.py` to be a thin shim that dispatches to the
   right supplier module.
3. Add supplier-specific phrase aliases to `core/matching.py` if needed
   for naming conventions.

If you actually do this, expect to spend ~half a day on shape-matching
because the existing code is brew.is-shaped and the shim needs to
preserve every call signature `core/sync.py` and `core/orders.py`
expect.

## Tests

```bash
python -m pytest tests/           # all tests
python -m pytest tests/test_matching.py -v   # one module
python -m pytest tests/ -k wheat            # by keyword
```

Tests are pure-Python with in-memory SQLite fixtures or monkey-patched
`sys.platform`. No BeerSmith install required.

CI (GitHub Actions) runs the suite on Windows + Ubuntu × Python 3.10 /
3.11 / 3.12 on every push and PR, plus `ruff check`.

## Coding style

- Black-ish formatting, line length 100 (configured in pyproject.toml).
- Type hints encouraged but not enforced (no mypy in CI yet).
- Comments answer "why" not "what". The format quirks above are good
  examples — every weird thing in the codebase has a comment explaining
  the brewing or BeerSmith reason for it.
- Commit messages: lead with a short imperative summary, follow with a
  prose paragraph or two explaining the *why*. The existing log is
  unusually verbose by design — those messages are the changelog.

## Building the MSI

You need WiX v4+ on PATH (`scoop install wixtoolset` or
`dotnet tool install --global wix`).

```powershell
.\build\build.ps1
# produces dist\brewbridge-X.Y.Z.msi
```

Bump `__version__` in `src/brewbridge/__init__.py` and in
`pyproject.toml` (keep them in sync) before each release — at the same
version, MSI MajorUpgrade doesn't fire and successive installs stack
in Add/Remove Programs.

## Reporting bugs

Open an issue at https://github.com/Homeblest/brewbridge/issues. If
you can paste:

- output of `brewbridge doctor`,
- contents of `~/.brewbridge/tray.log` (if the tray was involved),
- the most recent `~/.brewbridge/sync_*.txt` report,

…that's usually enough context to triage.
