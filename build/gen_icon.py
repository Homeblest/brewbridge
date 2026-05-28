"""Generate ``build/icon.ico`` from the tray's icon-drawing code.

Run once whenever the tray icon changes. Output is committed so a fresh
checkout can build the MSI without first running Python.

Usage::

    python build/gen_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from brewbridge.tray import _make_icon_image  # noqa: E402


def main():
    # Render each size separately so the small-icon variants get the
    # font sized to taste rather than letting Windows down-sample a 256 px
    # bitmap. The 16/32 px renders use the bitmap fallback font where
    # truetype is too thin, so they stay legible at taskbar sizes.
    sizes = [16, 32, 48, 64, 128, 256]
    images = [_make_icon_image("ok", size=s) for s in sizes]
    # Pillow's .ico writer takes the first image and emits the others as
    # additional frames if `sizes=` is set on save. We bundle every frame.
    out = ROOT / "build" / "icon.ico"
    images[-1].save(
        out,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(sizes)} frames)")


if __name__ == "__main__":
    main()
