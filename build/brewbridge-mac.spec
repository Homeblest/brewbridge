# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for brewbridge on macOS.

Produces ``dist/brewbridge.app`` — a single .app bundle that:

  * runs as a status-bar (tray) app via pystray's _darwin backend,
  * registers ``brewis://`` URL handling via Info.plist CFBundleURLTypes,
  * dispatches Launch Services URL events to the existing brewbridge CLI
    routing in ``__main__.py`` via PyInstaller's ``argv_emulation``
    hook (URL events arrive as argv, no Cocoa code needed).

⚠ This spec only builds on macOS. PyInstaller doesn't cross-compile —
the bundle format, code-signing, and Mach-O binaries require running on
Darwin. Building from this spec on Windows will fail with a "BUNDLE only
supported on macOS" error.

Build with::

    python -m PyInstaller --clean --noconfirm build/brewbridge-mac.spec

Output: ``dist/brewbridge.app``.

UNVERIFIED on real hardware. Written without Mac access. First Mac
contributor: please paste any errors you hit and we'll iterate. Likely
trouble spots:
  * pystray._darwin needs PyObjC bundled — added to hiddenimports.
  * argv_emulation needs to be True so brewis:// URL events become argv.
  * LSUIElement=1 hides from Dock (tray-only app); some pystray issues
    on macOS Sonoma+ may require this to be False instead — try toggling.
"""
import os

# --- paths ---------------------------------------------------------------
# PyInstaller resolves relative paths against SPECPATH (the directory of
# this .spec file). Anchor everything to repo root.
REPO = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(REPO, "src", "brewbridge")
ICON = os.path.join(REPO, "build", "icon.icns")

# --- version (read from package, keeps Info.plist in sync) --------------
def _read_version() -> str:
    init_path = os.path.join(SRC, "__init__.py")
    with open(init_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("__version__"):
                return line.split('"')[1]
    return "0.0.0"

VERSION = _read_version()

# --- shared hidden imports ----------------------------------------------
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
    "brewbridge.core.platform",
    "brewbridge.core.recipes",
    "brewbridge.core.sync",
    # pystray loads its Cocoa backend by string lookup
    "pystray._darwin",
    # PyObjC dependencies pulled by pystray._darwin's AppKit calls.
    # Listed explicitly because PyInstaller's static analysis sometimes
    # misses lazily-loaded PyObjC bindings.
    "AppKit",
    "Foundation",
    "objc",
]

# --- Analysis ------------------------------------------------------------
# Single entry: __main__.py. The same dispatch handles `brewbridge sync`,
# `brewbridge tray`, and bare `brewis://...` argv (the URL handler path).
# When Launch Services fires the .app with a brewis URL, argv_emulation
# (set on BUNDLE below) translates the Apple Event to sys.argv so the
# existing __main__.main routing in __main__.py just works.
a = Analysis(
    [os.path.join(SRC, "__main__.py")],
    pathex=[os.path.join(REPO, "src")],
    binaries=[],
    datas=[],
    hiddenimports=_HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Same as Windows: Playwright is huge and the Chromium download
        # is a separate post-install step. Bundling would add ~200 MB.
        "playwright",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# --- EXE (the Mach-O binary inside the .app's MacOS/) -------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="brewbridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # tray app — no terminal window
    disable_windowed_traceback=False,
    target_arch=None,         # let PyInstaller pick (universal2 if both archs available)
    codesign_identity=None,   # skip signing for v0.1; users right-click → Open
    entitlements_file=None,
    icon=ICON,
    # When Launch Services activates this .app for a brewis:// URL,
    # macOS by default delivers the URL via an Apple Event (kInternetEventClass).
    # PyInstaller's macOS bootloader has a hook that intercepts those
    # events at startup and translates them into sys.argv entries — so
    # the same __main__.py routing that handles `brewbridge brewis://...`
    # on Windows works unchanged on macOS. This flag is the on-switch.
    argv_emulation=True,
)

# --- COLLECT (the .app's Resources/, Frameworks/, etc) ------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="brewbridge",
)

# --- BUNDLE (the .app wrapper) ------------------------------------------
# BUNDLE() takes the COLLECT output and wraps it in a .app directory
# structure. info_plist gets merged with PyInstaller's defaults — every
# key we set here overrides PyInstaller's default for that key.
app = BUNDLE(
    coll,
    name="brewbridge.app",
    icon=ICON,
    bundle_identifier="is.brew.brewbridge",
    version=VERSION,
    info_plist={
        # User-facing display
        "CFBundleName": "brewbridge",
        "CFBundleDisplayName": "brewbridge",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,

        # Status-bar / tray app — no Dock icon. If we ever want a normal
        # foreground app instead, flip this to False or remove it.
        "LSUIElement": True,

        # Retina-aware drawing
        "NSHighResolutionCapable": True,

        # Minimum macOS — pystray + Python 3.10 happily run on 10.15,
        # but 11 is a safe modern floor. Adjust down if anyone reports
        # needing older support.
        "LSMinimumSystemVersion": "11.0",

        # The headline integration: declare we handle brewis:// URLs so
        # Launch Services routes clicked links to this app. The
        # CFBundleURLName is arbitrary but must be present; convention
        # is reverse-DNS.
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "is.brew.brewbridge.order",
                "CFBundleURLSchemes": ["brewis"],
                "CFBundleTypeRole": "Viewer",
            }
        ],

        # Copyright shown in About box
        "NSHumanReadableCopyright": "© 2026 Hjalti Leifsson · MIT licensed",
    },
)
