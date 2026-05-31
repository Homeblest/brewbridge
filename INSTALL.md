# Installing brewbridge

brewbridge runs on Windows and macOS — the two platforms BeerSmith 4 ships
for. Pick your section below.

- [Windows: MSI install](#the-5-minute-install-windows-msi)
- [Windows: from source](#windows-from-source-python-developers)
- [macOS: .app install](#macos-app-install)
- [macOS: from source](#macos-from-source-python-developers)

> **macOS status (v0.1.0):** the codebase is platform-portable and the
> .app build pipeline is in place, but **none of the macOS build has
> been verified on real hardware yet.** If you're the first mac person
> through this door, please file issues — see the
> [macOS troubleshooting](#macos-troubleshooting) section.

---

## The 5-minute install (Windows MSI)

The MSI installs the binaries and adds them to PATH. A second one-time
command finishes per-user setup (URL handler, report template, library
profiles) — it has to be a second step because BeerSmith caches the DB
in memory and must be closed for those writes.

1. Download `brewbridge-<version>.msi` from [Releases](https://github.com/Homeblest/brewbridge/releases). Double-click → accept defaults.
2. **Close BeerSmith** if it's running.
3. Open a Command Prompt or PowerShell and run:

       brewbridge install

   This registers the `brewis://` URL handler, drops `BrewIsOrder.htm` into
   `%APPDATA%\BeerSmith4\Reports\`, adds the `Brew.is einfaldur` mash profile
   to `M_MASH`, and adds the `Reykjavík tap` water profile to `M_WATER`.
4. Open BeerSmith → **Tools → Options → Reports → Add Report…** → browse to `%APPDATA%\BeerSmith4\Reports\BrewIsOrder.htm` and import it as type **Recipe**. (BeerSmith requires this go through its own UI.)
5. Done. Launch **brewbridge tray** from the Start Menu → it sits in your system tray. Right-click → **Synca núna** to do your first catalog sync.

---

## From source (Python developers)

### Prerequisites

- Windows 10 or 11
- Python 3.10 or newer (3.12 recommended) — install from [python.org](https://www.python.org/downloads/), checking **"Add Python to PATH"**
- BeerSmith 4 installed at the default path

### Install

```powershell
# Clone and install in editable mode
git clone https://github.com/Homeblest/brewbridge.git
cd brewbridge
pip install -e .

# Install Chromium for the Recipe Machine driver (~150 MB, one-time)
playwright install chromium

# One-time setup: register URL protocol, install report template + library profiles
# Close BeerSmith first — the DB writes require exclusive access.
brewbridge install
```

The `brewbridge install` step adds:

- The Windows URL protocol handler so `brewis://order/<recipe>` links work from anywhere
- `BrewIsOrder.htm` in BeerSmith's user Reports folder
- The `Brew.is einfaldur` mash profile in your `M_MASH` library
- The `Reykjavík tap` water profile in your `M_WATER` library

You still need to **manually import** `BrewIsOrder.htm` through BeerSmith's UI:

1. Open BeerSmith → **Tools → Options → Reports**
2. **Add Report…** → browse to `%APPDATA%\BeerSmith4\Reports\BrewIsOrder.htm`
3. Set the report type to **Recipe** and save

BeerSmith requires this UI flow to register the template's metadata in its own `Reports.sqlite`; we can't do it cleanly from outside the app.

### First sync

```powershell
brewbridge sync
```

This fetches the brew.is /uppskriftir page, decodes the embedded product catalog, filters to in-stock brewing ingredients, and replaces the `(brew.is)`-tagged rows in your BeerSmith library. By default, runs in **brew.is-only mode** — purging non-brew.is library rows so what you see in BeerSmith is what you can actually buy.

Don't want that? Pass `--keep-builtins`.

### Daily use

```powershell
# Start the tray icon
brewbridge tray

# Or invoke commands directly
brewbridge order "Blæbrigði"           # render HTML shopping list
brewbridge order "Blæbrigði" --fill    # also open /uppskriftir pre-filled
brewbridge audit                       # check imported recipes for issues
brewbridge audit --fix                 # auto-fix yeast dates + rebuild mashes
```

The tray icon is the everyday entry point — right-click for sync, order, audit, and the data folder. The icon colour reflects sync state: 🟢 fresh, 🟡 stale (>24h), 🔴 last sync failed.

---

## macOS .app install

> Unverified on real hardware — see [macOS troubleshooting](#macos-troubleshooting).

1. Download `brewbridge-<version>.dmg` from [Releases](https://github.com/Homeblest/brewbridge/releases) (when published — until then build it yourself, see [from source](#macos-from-source-python-developers)).
2. Open the DMG and drag **brewbridge.app** to **/Applications**.
3. **First launch (because the .app isn't code-signed yet):** open Finder → Applications → **right-click `brewbridge.app` → Open** → confirm in the dialog that says "macOS cannot verify the developer". Double-click on subsequent launches works normally.
   - This is the same Gatekeeper workaround used by every unsigned macOS app. Code-signing + notarization is on the roadmap.
4. brewbridge appears in your menu bar (top-right of the screen, between the WiFi icon and your name). Click it for sync / order / audit.
5. **Close BeerSmith if it's open**, then open Terminal and run:
   ```bash
   ~/Applications/brewbridge.app/Contents/MacOS/brewbridge install
   # or, if you installed system-wide:
   /Applications/brewbridge.app/Contents/MacOS/brewbridge install
   ```
   This drops `BrewIsOrder.htm` into `~/Library/Application Support/BeerSmith4/Reports/`, adds the `Brew.is einfaldur` mash profile to `M_MASH`, and adds the `Reykjavík tap` water profile to `M_WATER`. The `brewis://` URL handler is registered automatically by Launch Services when the .app first runs — no separate registration step.
6. In BeerSmith → **Tools → Options → Reports → Add Report…** → browse to `~/Library/Application Support/BeerSmith4/Reports/BrewIsOrder.htm` and import as type **Recipe**.
7. Done. The menu-bar icon is the everyday entry point.

### Getting `brewbridge` on PATH for terminal use

If you'd like to type `brewbridge sync` from any terminal instead of typing the full `/Applications/brewbridge.app/Contents/MacOS/brewbridge sync`, add this line to your `~/.zshrc` (or `~/.bashrc`):

```bash
alias brewbridge='/Applications/brewbridge.app/Contents/MacOS/brewbridge'
```

Or `pip install brewbridge` for a real CLI (works alongside the .app).

---

## macOS from source (Python developers)

```bash
# Clone and install in editable mode
git clone https://github.com/Homeblest/brewbridge.git
cd brewbridge
pip install -e .

# Install Chromium for the Recipe Machine driver (~150 MB, one-time)
playwright install chromium

# One-time setup (close BeerSmith first):
brewbridge install
```

`brewbridge install` from a pure source install on macOS:
- Drops `BrewIsOrder.htm` into `~/Library/Application Support/BeerSmith4/Reports/`
- Adds mash + water profiles to BeerSmith.sqlite
- **Does NOT** register the `brewis://` URL handler — Launch Services only honours URL schemes declared by .app bundles, not bare CLI invocations. Build the .app (below) if you want the BeerSmith report's "Order" button to dispatch to brewbridge.

### Building the .app yourself

```bash
pip install pyinstaller pillow
./build/build.sh              # produces dist/brewbridge.app
./build/build.sh --dmg        # also produces dist/brewbridge-<v>.dmg (needs `brew install create-dmg`)
```

The build script is unsigned-by-default — see the `# code-sign` notes in `build/brewbridge-mac.spec` for the path to wiring up a Developer ID cert when you have one.

---

## Where brewbridge stores data

| Path | Contents |
|---|---|
| `%USERPROFILE%\.brewbridge\specs_reference.json` | Frozen snapshot of BeerSmith built-in ingredient specs (used for matching after brew.is-only purge) |
| `%USERPROFILE%\.brewbridge\orders\` | Generated HTML shopping lists, `.bsmx` recipe clones |
| `%USERPROFILE%\.brewbridge\last_sync.txt` | Timestamp + status for the tray icon's state colour |
| `%APPDATA%\BeerSmith4\brewbridge-backups\` | `BeerSmith.sqlite` backups taken before every write |
| `%APPDATA%\BeerSmith4\Reports\BrewIsOrder.htm` | The custom report template |

On **macOS** the equivalents are:

| Path | Contents |
|---|---|
| `~/.brewbridge/specs_reference.json` | Frozen snapshot of BeerSmith built-in ingredient specs |
| `~/.brewbridge/orders/` | Generated HTML shopping lists, `.bsmx` recipe clones |
| `~/.brewbridge/last_sync.txt` | Timestamp + status for the menu-bar icon's state colour |
| `~/Library/Application Support/BeerSmith4/brewbridge-backups/` | `BeerSmith.sqlite` backups |
| `~/Library/Application Support/BeerSmith4/Reports/BrewIsOrder.htm` | The custom report template |

## Uninstall

```powershell
brewbridge install --skip-db    # just to register the unregister path... no.
# (Manual for now:)
```

To remove cleanly:

```powershell
# Unregister the brewis:// protocol
reg delete HKCU\Software\Classes\brewis /f

# Remove the report template (and remove from BeerSmith via Tools→Options→Reports)
del "%APPDATA%\BeerSmith4\Reports\BrewIsOrder.htm"

# Uninstall the package
pip uninstall brewbridge
```

The `Brew.is einfaldur` mash profile and `Reykjavík tap` water profile remain in your BeerSmith library — delete them from BeerSmith's UI if you don't want them. Your `.brewbridge` folder under your user profile can be deleted manually.

## Troubleshooting

**"BeerSmith is running — close it before syncing."**  
Catalog sync and any DB-writing command refuse to run while BeerSmith is open. BeerSmith caches the entire database in memory and rewrites the file on its own save cycle, which clobbers changes made underneath it. Close BeerSmith, run the command, then reopen.

**Critical mash warnings on every recipe after sync**  
Your `Brew.is einfaldur` mash profile probably isn't in `M_MASH`. Run `brewbridge install --skip-db` then close BeerSmith and run `brewbridge install` (without `--skip-db`) again.

**"Yeast is 71 months old" warning after sync**  
The library `F_Y_PKG_DATE` is stamped fresh on every sync, but BeerSmith re-binds yeast from the library when a recipe opens. If you see this warning on an existing recipe, close BeerSmith and run `brewbridge audit --fix`.

**The `▶ Order at brew.is` button does nothing in BeerSmith**  
Click works in a browser? Then BeerSmith's embedded report viewer is stripping the custom protocol. Workaround: open the order page via the tray menu's **Panta uppskrift…** instead.

### macOS troubleshooting

**"brewbridge.app is damaged and can't be opened"**  
You downloaded the unsigned release. Right-click `brewbridge.app` → **Open** (instead of double-clicking) — Gatekeeper offers a one-time bypass dialog the first time. Subsequent launches via double-click work normally. (Long-term fix: code-signing + notarization — on the v0.2 roadmap.)

**brewis:// click in BeerSmith opens nothing**  
Launch Services has to scan the .app once before it'll route `brewis://` URLs to it. Quit and re-launch brewbridge.app from `/Applications/` once after install. If still broken, force a Launch Services refresh:
```bash
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \
    -kill -r -domain local -domain system -domain user
```
(this rescans every app's `CFBundleURLTypes` declarations).

**Menu-bar icon doesn't appear**  
Check Console.app for `pystray` exceptions. The macOS pystray backend is more fragile than the Windows one — if you're on macOS Sonoma+ and the icon is missing, try setting `LSUIElement` to `False` in `build/brewbridge-mac.spec` and rebuilding (this gives the app a Dock icon as a side-effect but the menu bar should show up).

**Can't find BeerSmith.sqlite on macOS**  
BeerSmith 4 on mac stores its data in `~/Library/Application Support/BeerSmith4/`. If yours is somewhere else, point brewbridge at it:
```bash
brewbridge install --db-path "/custom/location/BeerSmith.sqlite"
```
(`--db-path` is on the roadmap; for now edit `core/platform.py:beersmith_data_dir` if you need a non-standard location.)

---

## Building the MSI (maintainers)

The release MSI is produced from this repo. You need:

- Python 3.10+ with `pip install pyinstaller pillow pystray`
- WiX v4+ — one of:
      scoop install wixtoolset
      dotnet tool install --global wix

Then from the repo root:

```powershell
.\build\build.ps1
```

That runs PyInstaller (producing `dist\brewbridge\` with the two EXEs and
their shared runtime), walks the resulting tree to emit a WiX file
fragment (`build\wix\Harvested.wxs`), and invokes `wix build` to produce
`dist\brewbridge-<version>.msi`.

Flags:

- `-NoClean` — skip wiping `dist\` and `build\pyi\` (fast iteration after a known-good build)
- `-PyInstallerOnly` — stop after PyInstaller (useful when debugging the EXEs alone)

The MSI version tracks `__version__` in `src\brewbridge\__init__.py`
automatically; bump that and rerun to cut a new release.

## Building the .app (macOS, maintainers)

```bash
pip install pyinstaller pillow pystray
./build/build.sh                  # produces dist/brewbridge.app
./build/build.sh --dmg            # also dist/brewbridge-<v>.dmg (needs `brew install create-dmg`)
./build/build.sh --regen-icon     # regenerate build/icon.icns first
```

Flags:

- `--dmg` — also wrap the .app into a distributable DMG
- `--regen-icon` — recreate `build/icon.icns` from the Pillow drawing code (run after editing the tray icon)
- `--no-clean` — skip the wipe of `dist/` and `build/pyi/`

The .app's bundle version also tracks `__version__` automatically. As of v0.1.0 the .app is **not** code-signed or notarized — users see Gatekeeper's "developer cannot be verified" dialog on first launch and need to right-click → Open. Adding a Developer ID cert + notarization is one config switch in `build/brewbridge-mac.spec` (search for `codesign_identity`) plus an `xcrun notarytool` step in `build.sh`. Tracked under issue #29.
