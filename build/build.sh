#!/usr/bin/env bash
#
# Build brewbridge.app for macOS.
#
# Pipeline:
#   1. Verify Python + pyinstaller + Pillow are present.
#   2. Optionally regenerate icon.icns (if --regen-icon).
#   3. Run PyInstaller against build/brewbridge-mac.spec.
#   4. Optionally wrap dist/brewbridge.app into dist/brewbridge-<v>.dmg
#      (skipped unless `create-dmg` is on PATH and --dmg is passed).
#
# What this script does NOT do:
#   * Code-sign the .app with a Developer ID cert.
#   * Notarize. (See INSTALL.md for the right-click → Open workaround.)
#
# Usage:
#   ./build/build.sh                 # build .app, no DMG
#   ./build/build.sh --dmg           # build .app + DMG
#   ./build/build.sh --regen-icon    # regenerate icon.icns first
#   ./build/build.sh --no-clean      # don't wipe dist/ first
#
# UNVERIFIED on real macOS hardware — written without a Mac available.
# First Mac contributor: please open issues against
# https://github.com/Homeblest/brewbridge for anything that breaks.

set -euo pipefail

# Resolve script + repo dirs regardless of cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO}"

# Color helpers (no-op if NO_COLOR set or not a TTY)
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    CYAN=$'\033[36m'
    GREEN=$'\033[32m'
    GRAY=$'\033[90m'
    RESET=$'\033[0m'
else
    CYAN="" ; GREEN="" ; GRAY="" ; RESET=""
fi

step()  { printf '\n%s==>%s %s\n'  "${CYAN}"  "${RESET}" "$*"; }
ok()    { printf '    %s%s%s\n'    "${GREEN}" "$*"        "${RESET}"; }
hint()  { printf '    %s%s%s\n'    "${GRAY}"  "$*"        "${RESET}"; }

# Arg parsing — small enough to roll by hand, no getopts needed
WANT_DMG=0
REGEN_ICON=0
CLEAN=1
for arg in "$@"; do
    case "$arg" in
        --dmg)         WANT_DMG=1 ;;
        --regen-icon)  REGEN_ICON=1 ;;
        --no-clean)    CLEAN=0 ;;
        -h|--help)
            sed -n '2,28p' "${BASH_SOURCE[0]}"
            exit 0 ;;
        *)
            echo "unknown arg: $arg" >&2
            exit 2 ;;
    esac
done

# Sanity: we only run on macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "build.sh is macOS-only. On Windows, run build/build.ps1." >&2
    echo "Detected uname: $(uname)" >&2
    exit 1
fi

# -----------------------------------------------------------------------
# 1. Prereqs
# -----------------------------------------------------------------------
step "Checking prerequisites"

PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
    echo "python3 not on PATH. Install with: brew install python@3.12" >&2
    exit 1
fi
ok "python: $(command -v "${PYTHON}")"

if ! "${PYTHON}" -c "import PyInstaller" 2>/dev/null; then
    echo "pyinstaller not installed for ${PYTHON}. Run:" >&2
    echo "    ${PYTHON} -m pip install pyinstaller pillow pystray" >&2
    exit 1
fi
ok "pyinstaller: $(${PYTHON} -c 'import PyInstaller; print(PyInstaller.__version__)')"

if ! "${PYTHON}" -c "import PIL" 2>/dev/null; then
    echo "Pillow not installed for ${PYTHON}." >&2
    exit 1
fi
ok "pillow: $(${PYTHON} -c 'import PIL; print(PIL.__version__)')"

VERSION=$(${PYTHON} -c "import sys; sys.path.insert(0,'src'); import brewbridge; print(brewbridge.__version__)")
ok "package version: ${VERSION}"

# -----------------------------------------------------------------------
# 2. Regenerate icon if requested
# -----------------------------------------------------------------------
if [[ ${REGEN_ICON} -eq 1 ]]; then
    step "Regenerating icon.icns"
    "${PYTHON}" build/gen_icns.py
fi

if [[ ! -f build/icon.icns ]]; then
    echo "build/icon.icns missing. Run with --regen-icon, or:" >&2
    echo "    ${PYTHON} build/gen_icns.py" >&2
    exit 1
fi

# -----------------------------------------------------------------------
# 3. Clean
# -----------------------------------------------------------------------
if [[ ${CLEAN} -eq 1 ]]; then
    step "Cleaning previous build artifacts"
    for d in build/pyi dist/brewbridge dist/brewbridge.app dist/*.dmg; do
        if [[ -e "$d" ]]; then
            rm -rf "$d"
            ok "removed $d"
        fi
    done
fi

# -----------------------------------------------------------------------
# 4. PyInstaller
# -----------------------------------------------------------------------
step "Running PyInstaller"
"${PYTHON}" -m PyInstaller \
    --clean --noconfirm \
    --distpath dist \
    --workpath build/pyi \
    build/brewbridge-mac.spec

APP="dist/brewbridge.app"
if [[ ! -d "${APP}" ]]; then
    echo "PyInstaller succeeded but ${APP} not produced." >&2
    exit 1
fi

APP_SIZE_MB=$(du -sm "${APP}" | cut -f1)
ok ".app produced: ${APP} (${APP_SIZE_MB} MB)"

# -----------------------------------------------------------------------
# 5. Optional DMG
# -----------------------------------------------------------------------
if [[ ${WANT_DMG} -eq 1 ]]; then
    if ! command -v create-dmg >/dev/null 2>&1; then
        echo "--dmg requested but create-dmg not installed. Install:" >&2
        echo "    brew install create-dmg" >&2
        exit 1
    fi
    step "Building DMG"
    DMG="dist/brewbridge-${VERSION}.dmg"
    rm -f "${DMG}"
    create-dmg \
        --volname "brewbridge ${VERSION}" \
        --volicon "build/icon.icns" \
        --window-pos 200 120 \
        --window-size 600 320 \
        --icon-size 100 \
        --icon "brewbridge.app" 150 150 \
        --hide-extension "brewbridge.app" \
        --app-drop-link 450 150 \
        --skip-jenkins \
        "${DMG}" \
        "${APP}"
    DMG_SIZE_MB=$(du -sm "${DMG}" | cut -f1)
    ok "DMG produced: ${DMG} (${DMG_SIZE_MB} MB)"
fi

# -----------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------
step "Done"
echo "    ${APP}"
[[ ${WANT_DMG} -eq 1 ]] && echo "    dist/brewbridge-${VERSION}.dmg"
echo ""
hint "First-run on a fresh machine (unsigned .app):"
hint "    Right-click brewbridge.app → Open → confirm in dialog"
hint "    (Gatekeeper blocks double-click for unsigned apps; right-click"
hint "    bypasses once and remembers the choice.)"
