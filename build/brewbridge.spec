# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for brewbridge.

Builds two EXEs into ONE shared --onedir output:

    dist/brewbridge/
    ├── brewbridge.exe          (console, CLI entry point)
    ├── brewbridge-tray.exe     (windowed, pystray tray icon)
    ├── _internal/              (shared Python runtime + DLLs + data)
    └── ...

The two analyses are MERGE-d so they share the bundled Python runtime,
DLLs, and data — without MERGE each EXE would ship its own copy of
python3X.dll, tcl, tk, PIL, etc, doubling the install size.

Build with::

    pyinstaller --clean --noconfirm build/brewbridge.spec

Output ends up in ``dist/brewbridge/``. The WiX build harvests every
file in that tree into the MSI.
"""
import os

# --- paths ---------------------------------------------------------------
# PyInstaller resolves relative paths in spec files against SPECPATH (the
# directory containing this .spec), not the cwd. Anchor everything to the
# repo root (which is the parent of SPECPATH = "build/") so the spec works
# from any invocation directory.
REPO = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(REPO, "src", "brewbridge")
ICON = os.path.join(REPO, "build", "icon.ico")

# --- shared hidden imports ----------------------------------------------
# Anything reached only by string ("from .core import sync") needs to be
# listed so PyInstaller's static analyzer picks it up. Everything in
# brewbridge.core is reachable through CLI dispatch in __main__.
_HIDDEN = [
    "brewbridge",
    "brewbridge.__main__",
    "brewbridge.setup",
    "brewbridge.tray",
    "brewbridge.core",
    "brewbridge.core.audit",
    "brewbridge.core.beersmith",
    "brewbridge.core.catalog",
    "brewbridge.core.matching",
    "brewbridge.core.orders",
    "brewbridge.core.recipes",
    "brewbridge.core.sync",
]

# --- CLI analysis (brewbridge.exe) --------------------------------------
cli_a = Analysis(
    [os.path.join(SRC, "__main__.py")],
    pathex=[os.path.join(REPO, "src")],
    binaries=[],
    # Ship brewbridge/assets/ inside the bundle. core/sync.py's
    # `_BUNDLED_REF` looks for assets/specs_reference.json relative to
    # the brewbridge package directory; PyInstaller has to put the
    # file at that same relative path for the lookup to resolve when
    # the user's live BeerSmith library is already empty.
    datas=[(os.path.join(SRC, "assets"), os.path.join("brewbridge", "assets"))],
    hiddenimports=_HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Playwright was excluded in 0.1.0–0.1.3 to keep MSI size down,
        # under the assumption that users would `pip install playwright`
        # themselves. That broke the headline "fill Chromium with the
        # form" feature for MSI users (no pip available, the import
        # failed silently inside fill_recipe_machine). Now bundled —
        # adds ~10 MB to the MSI for the Python package. The actual
        # Chromium browser binary (~150 MB) is still downloaded
        # post-install via `brewbridge install` (calls
        # `playwright install chromium` if the user's cache is empty).
    ],
    noarchive=False,
)

# --- Tray analysis (brewbridge-tray.exe) --------------------------------
tray_a = Analysis(
    [os.path.join(SRC, "tray.py")],
    pathex=[os.path.join(REPO, "src")],
    binaries=[],
    datas=[(os.path.join(SRC, "assets"), os.path.join("brewbridge", "assets"))],
    hiddenimports=_HIDDEN + [
        # pystray loads its Windows backend by string name
        "pystray._win32",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# --- merge shared dependencies ------------------------------------------
# After MERGE, tray_a will reference shared binaries/datas from cli_a
# instead of duplicating them. The first tuple is the "primary"; the
# second-onward share its bundle.
MERGE(
    (cli_a, "brewbridge", "brewbridge.exe"),
    (tray_a, "brewbridge-tray", "brewbridge-tray.exe"),
)

# --- CLI EXE -------------------------------------------------------------
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="brewbridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # CLI keeps the console so `brewbridge sync` prints
    disable_windowed_traceback=False,
    icon=ICON,
)

# --- Tray EXE ------------------------------------------------------------
tray_pyz = PYZ(tray_a.pure, tray_a.zipped_data)
tray_exe = EXE(
    tray_pyz,
    tray_a.scripts,
    [],
    exclude_binaries=True,
    name="brewbridge-tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI: no console window flashes when Start Menu fires it
    disable_windowed_traceback=False,
    icon=ICON,
)

# --- single COLLECT pulling both EXEs + shared deps ---------------------
coll = COLLECT(
    cli_exe,
    cli_a.binaries,
    cli_a.zipfiles,
    cli_a.datas,
    tray_exe,
    tray_a.binaries,
    tray_a.zipfiles,
    tray_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="brewbridge",
)
