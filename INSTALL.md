# Installing brewbridge on Windows

## The 5-minute install (MSI — when released)

1. Download `brewbridge-<version>.msi` from [Releases](https://github.com/hjaltileifsson/brewbridge/releases).
2. Double-click. Accept the defaults.
3. The installer registers the `brewis://` URL handler, drops the BeerSmith report template into place, adds the `Brew.is einfaldur` mash profile and `Reykjavík tap` water profile to your BeerSmith library, and pins **brewbridge tray** to your Start Menu.
4. Open BeerSmith → **Tools → Options → Reports → Add Report…** → browse to `%APPDATA%\BeerSmith4\Reports\BrewIsOrder.htm` and import it as type **Recipe**. (BeerSmith requires this go through its own UI.)
5. Done. Launch **brewbridge** from the Start Menu → it sits in your system tray.

Right-click the tray icon → **Synca núna** to do your first catalog sync.

---

## From source (Python developers)

### Prerequisites

- Windows 10 or 11
- Python 3.10 or newer (3.12 recommended) — install from [python.org](https://www.python.org/downloads/), checking **"Add Python to PATH"**
- BeerSmith 4 installed at the default path

### Install

```powershell
# Clone and install in editable mode
git clone https://github.com/hjaltileifsson/brewbridge.git
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

## Where brewbridge stores data

| Path | Contents |
|---|---|
| `%USERPROFILE%\.brewbridge\specs_reference.json` | Frozen snapshot of BeerSmith built-in ingredient specs (used for matching after brew.is-only purge) |
| `%USERPROFILE%\.brewbridge\orders\` | Generated HTML shopping lists, `.bsmx` recipe clones |
| `%USERPROFILE%\.brewbridge\last_sync.txt` | Timestamp + status for the tray icon's state colour |
| `%APPDATA%\BeerSmith4\brewbridge-backups\` | `BeerSmith.sqlite` backups taken before every write |
| `%APPDATA%\BeerSmith4\Reports\BrewIsOrder.htm` | The custom report template |

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
