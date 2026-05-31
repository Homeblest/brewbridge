"""Generate ``build/icon.icns`` from the tray's icon-drawing code.

macOS's icon container format is ``.icns`` (Apple Icon Image) rather than
Windows' ``.ico``. It demands a specific size set — 16, 32, 64, 128, 256,
512, and 1024 px square — so retina (2×) variants are present without
the OS having to scale at runtime.

Pillow writes .icns natively if you pass the sizes parameter, the same
shape as gen_icon.py. Output is committed so a fresh checkout can build
the .app without first running Python with Pillow installed.

Usage::

    python build/gen_icns.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from brewbridge.tray import _make_icon_image  # noqa: E402


def main():
    # The macOS Human Interface Guidelines call for these specific sizes
    # (1x and 2x variants of every used display size). The Finder picks
    # the smallest size that's at-least-as-big as it needs; without 1024
    # px, retina previews look soft.
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    images = [_make_icon_image("ok", size=s) for s in sizes]
    out = ROOT / "build" / "icon.icns"
    # Pillow's .icns writer expects the largest image first; remaining
    # sizes go through append_images. The "icns" format itself stores
    # each size as a separate sub-resource, matching macOS expectations.
    images[-1].save(
        out,
        format="ICNS",
        append_images=images[:-1],
    )
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(sizes)} frames)")


if __name__ == "__main__":
    main()
