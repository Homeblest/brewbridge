<#
.SYNOPSIS
    Build brewbridge MSI installer end-to-end.

.DESCRIPTION
    Pipeline (in order):
      1. Verify prerequisites (Python with pyinstaller, wix CLI on PATH).
      2. Clean previous build/dist directories (unless -NoClean).
      3. Run PyInstaller against build/brewbridge.spec, producing
         dist/brewbridge/ with brewbridge.exe + brewbridge-tray.exe
         and their shared runtime.
      4. Walk that tree (build/gen_harvest.py) and emit
         build/wix/Harvested.wxs — one <Component> per shipped file.
      5. Run `wix build` with both wxs files to produce
         dist/brewbridge-<Version>.msi.

    The Version is read from src/brewbridge/__init__.py so it stays in
    sync with the Python package version automatically.

.PARAMETER NoClean
    Skip the clean step. Useful for fast iteration after a successful
    first build (subsequent PyInstaller runs reuse cached artifacts).

.PARAMETER PyInstallerOnly
    Stop after PyInstaller — skip harvest + wix. For testing the EXEs
    without the MSI overhead.

.EXAMPLE
    .\build\build.ps1
    .\build\build.ps1 -NoClean
    .\build\build.ps1 -PyInstallerOnly
#>
[CmdletBinding()]
param(
    [switch]$NoClean,
    [switch]$PyInstallerOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "    $msg" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 1. Prereqs
# ---------------------------------------------------------------------------
Write-Step "Checking prerequisites"

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "python not on PATH. Install Python 3.10+ and try again."
}
Write-OK "python: $python"

# pyinstaller must be importable from the *same* python we'll run the spec under
$pyinstaller = & $python -c "import PyInstaller, sys; print(PyInstaller.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "pyinstaller not installed for $python. Run: pip install pyinstaller pillow"
}
Write-OK "pyinstaller: $pyinstaller"

# pillow needed for icon regeneration (gen_icon.py); not strictly required
# for the MSI build itself since the .ico is committed, but warn if missing.
$pillow = & $python -c "import PIL; print(PIL.__version__)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-OK "pillow: $pillow"
}

if (-not $PyInstallerOnly) {
    $wix = (Get-Command wix -ErrorAction SilentlyContinue).Source
    if (-not $wix) {
        throw @"
wix not on PATH. Install with one of:
    scoop install wixtoolset
    dotnet tool install --global wix
Then re-open your shell.
"@
    }
    Write-OK "wix: $wix"

    # WiX v4+ extensions must be installed before use; `-ext` on the build
    # command no longer auto-fetches them. `extension add -g` is idempotent
    # — running it on an already-installed extension just reports the
    # current version and exits 0.
    & wix extension add -g WixToolset.UI.wixext 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install WiX UI extension. Try manually: wix extension add -g WixToolset.UI.wixext"
    }
    Write-OK "wix extension WixToolset.UI.wixext: ready"
}

# Pull version from src/brewbridge/__init__.py so MSI version tracks package version
$versionLine = Select-String -Path "src\brewbridge\__init__.py" -Pattern '^__version__\s*=\s*"([^"]+)"'
if (-not $versionLine) {
    throw "Could not find __version__ in src/brewbridge/__init__.py"
}
$version = $versionLine.Matches[0].Groups[1].Value
Write-OK "package version: $version"

# ---------------------------------------------------------------------------
# 2. Clean
# ---------------------------------------------------------------------------
if (-not $NoClean) {
    Write-Step "Cleaning previous build artifacts"
    foreach ($dir in @("build\__pycache__", "build\brewbridge", "dist", "*.spec.bak")) {
        if (Test-Path $dir) {
            Remove-Item -Recurse -Force $dir
            Write-OK "removed $dir"
        }
    }
}

# ---------------------------------------------------------------------------
# 3. PyInstaller
# ---------------------------------------------------------------------------
Write-Step "Running PyInstaller"
& $python -m PyInstaller --clean --noconfirm --distpath dist --workpath build\pyi build\brewbridge.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$distDir = Join-Path $repoRoot "dist\brewbridge"
if (-not (Test-Path "$distDir\brewbridge.exe") -or -not (Test-Path "$distDir\brewbridge-tray.exe")) {
    throw "PyInstaller succeeded but expected EXEs not found in $distDir"
}
$fileCount = (Get-ChildItem -Recurse -File $distDir).Count
$sizeMB = [math]::Round((Get-ChildItem -Recurse -File $distDir | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-OK "dist tree: $fileCount files, $sizeMB MB"

if ($PyInstallerOnly) {
    Write-Step "PyInstallerOnly: stopping before WiX"
    Write-Host "Output: $distDir"
    exit 0
}

# ---------------------------------------------------------------------------
# 4. Harvest
# ---------------------------------------------------------------------------
Write-Step "Generating WiX harvest from dist tree"
$harvested = "build\wix\Harvested.wxs"
& $python build\gen_harvest.py $distDir $harvested
if ($LASTEXITCODE -ne 0) { throw "Harvest generation failed" }
$harvestSize = (Get-Item $harvested).Length
Write-OK "$harvested ($harvestSize bytes)"

# ---------------------------------------------------------------------------
# 5. wix build
# ---------------------------------------------------------------------------
Write-Step "Building MSI"
$msi = "dist\brewbridge-$version.msi"
$wixArgs = @(
    "build",
    "build\wix\brewbridge.wxs",
    "build\wix\Harvested.wxs",
    "-arch", "x64",
    "-d", "Version=$version",
    "-d", "SourceDir=dist\brewbridge",
    "-ext", "WixToolset.UI.wixext",
    "-o", $msi
)
& wix @wixArgs
if ($LASTEXITCODE -ne 0) { throw "wix build failed" }

if (-not (Test-Path $msi)) {
    throw "wix reported success but $msi was not produced"
}

$msiSize = [math]::Round((Get-Item $msi).Length / 1MB, 1)
Write-Step "Done"
Write-Host "    MSI: $msi ($msiSize MB)" -ForegroundColor Green
Write-Host ""
Write-Host "    Test install:    msiexec /i `"$msi`"" -ForegroundColor Gray
Write-Host "    Test uninstall:  msiexec /x `"$msi`"" -ForegroundColor Gray
