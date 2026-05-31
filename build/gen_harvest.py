"""Walk the PyInstaller dist tree and emit a WiX fragment.

The fragment is a single ``<ComponentGroup Id="ProductFiles">`` containing
one ``<Component>`` per file, each with stable IDs and deterministic GUIDs.
That ComponentGroup is referenced from ``brewbridge.wxs``.

Why generate vs hand-write or use ``wix harvest``:
    * the dist tree has ~150 files and the list changes whenever
      PyInstaller, Python, or Pillow updates;
    * ``wix harvest dir`` exists but its CLI surface has shifted across
      WiX 4 / 5 / 6 — generating ourselves removes that moving target;
    * deterministic GUIDs (UUID5 from the relative path) keep upgrade
      tracking sane: same path => same GUID across builds, so MSI sees
      file replacements as file replacements, not deletes+adds.

Brewbridge.exe and brewbridge-tray.exe get marked with the named anchors
``brewbridge.exe.cli`` and ``brewbridge.exe.tray`` so the wxs Shortcut /
PATH definitions can reference them with ``[#brewbridge.exe.tray]``.

Usage::

    python build/gen_harvest.py <dist_dir> <output_wxs>
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

# UUID5 namespace seed — fixed per project. Combined with each file's
# relative path it produces a stable GUID for that file across rebuilds.
NS = uuid.UUID("8b9a4d4c-5e1f-4e7a-9f3a-2c4d8e9a1b22")


def _id_from_path(rel: Path) -> str:
    """Convert a relative path into a WiX-safe identifier.

    WiX identifiers must start with a letter or underscore and may only
    contain letters, digits, and underscores. We prefix with ``f_`` to
    guarantee a valid start char and substitute everything else with
    underscores. Long paths get hashed-and-prefixed to stay under WiX's
    72-char identifier limit while remaining stable.

    Important: ``+`` and ``-`` get encoded distinctly (``_plus_`` /
    ``_minus_``) before the general non-alnum sweep, because Python's
    bundled tzdata has paired filenames like ``Etc/GMT+2`` and
    ``Etc/GMT-2``. Mapping both to ``_`` collapsed them to the same ID
    and produced "Duplicate Component" errors from wix.
    """
    # Distinguish + and - before the catch-all underscore sub.
    s = str(rel).replace("+", "_plus_").replace("-", "_minus_")
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", s)
    candidate = "f_" + cleaned
    if len(candidate) <= 72:
        return candidate
    # Long path: keep a readable suffix + 8-char hash so the ID stays
    # human-glanceable in build logs while staying unique.
    short_hash = uuid.uuid5(NS, str(rel)).hex[:8]
    tail = cleaned[-40:]
    return f"f_{short_hash}_{tail}"


def _wxs_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


# Files that the wxs file references by name. We need to expose stable
# anchor IDs (`Id="brewbridge.exe.tray"` etc) on those File elements so
# `[#brewbridge.exe.tray]` resolves correctly in <Shortcut Target=...>.
NAMED_FILES = {
    "brewbridge.exe": "brewbridge.exe.cli",
    "brewbridge-tray.exe": "brewbridge.exe.tray",
}


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: gen_harvest.py <dist_dir> <output_wxs>")
    dist = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    if not dist.is_dir():
        sys.exit(f"not a directory: {dist}")

    # Group files by their containing directory so each Directory element
    # nests its children correctly.
    files_by_dir: dict[Path, list[Path]] = {}
    for f in sorted(dist.rglob("*")):
        if f.is_file():
            files_by_dir.setdefault(f.parent.relative_to(dist), []).append(f)

    # ---- emit ----
    # We emit a <DirectoryRef Id="INSTALLFOLDER"> here rather than re-
    # declaring INSTALLFOLDER inside its own <StandardDirectory>. The
    # primary declaration lives in brewbridge.wxs; declaring it twice
    # (once there, once here) is a "Duplicate Directory" error in WiX 4+.
    # DirectoryRef extends the existing one with nested children.
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">',
        '  <Fragment>',
        '    <DirectoryRef Id="INSTALLFOLDER">',
    ]

    # Track open <Directory> nesting so we can close in reverse order.
    # We sort dir paths by depth so parents appear before children.
    dir_ids: dict[Path, str] = {Path("."): "INSTALLFOLDER"}
    component_count = 0

    DIR_INDENT = "    "

    def dir_id(rel_dir: Path) -> str:
        if rel_dir in dir_ids:
            return dir_ids[rel_dir]
        parts = [_id_from_path(Path(p)) for p in rel_dir.parts]
        did = "dir_" + "_".join(parts)
        if len(did) > 72:
            short_hash = uuid.uuid5(NS, "dir:" + str(rel_dir)).hex[:8]
            did = f"dir_{short_hash}_{rel_dir.parts[-1][:30]}"
            did = re.sub(r"[^A-Za-z0-9_]", "_", did)
        dir_ids[rel_dir] = did
        return did

    # Emit nested <Directory> elements so the wxs structure mirrors the dist
    # tree. For each parent dir, recurse into its immediate children — by
    # truncating other dirs' parts to (len(rel.parts) + 1) we collapse deep
    # paths to the next-level child set.
    def emit_dirs(rel: Path, depth: int) -> None:
        immediate = sorted({
            Path(*d.parts[: len(rel.parts) + 1])
            for d in files_by_dir
            if len(d.parts) > len(rel.parts)
            and (rel == Path(".") or d.parts[:len(rel.parts)] == rel.parts)
        })
        for child in immediate:
            pad = DIR_INDENT + "  " * depth
            cid = dir_id(child)
            lines.append(f'{pad}<Directory Id="{cid}" Name="{_wxs_escape(child.parts[-1])}">')
            emit_dirs(child, depth + 1)
            lines.append(f'{pad}</Directory>')

    emit_dirs(Path("."), 1)
    lines.append('    </DirectoryRef>')
    lines.append('')

    # Now the ComponentGroup — one component per file. Each component
    # references its file's directory via Directory= attribute.
    # We track every emitted Component / File ID and bail out loudly on
    # collision — wix's own error for this case is a wall of duplicates
    # spanning hundreds of lines, much harder to read than a single
    # source-side message pointing at the colliding paths.
    seen_comp: dict[str, str] = {}
    seen_file: dict[str, str] = {}
    lines.append('    <ComponentGroup Id="ProductFiles">')
    for rel_dir in sorted(files_by_dir):
        did = dir_id(rel_dir) if rel_dir != Path(".") else "INSTALLFOLDER"
        for f in files_by_dir[rel_dir]:
            rel = f.relative_to(dist)
            comp_id = "c_" + _id_from_path(rel)[2:]  # strip "f_" then re-prefix
            if len(comp_id) > 72:
                short_hash = uuid.uuid5(NS, "comp:" + str(rel)).hex[:8]
                comp_id = f"c_{short_hash}_{rel.parts[-1][:30]}"
                comp_id = re.sub(r"[^A-Za-z0-9_]", "_", comp_id)
            file_id = NAMED_FILES.get(rel.name) or _id_from_path(rel)
            file_guid = str(uuid.uuid5(NS, str(rel))).upper()

            if comp_id in seen_comp:
                sys.exit(f"ID collision: Component {comp_id!r} produced by both "
                         f"{seen_comp[comp_id]!r} and {str(rel)!r}. "
                         f"Extend _id_from_path() to disambiguate.")
            if file_id in seen_file:
                sys.exit(f"ID collision: File {file_id!r} produced by both "
                         f"{seen_file[file_id]!r} and {str(rel)!r}. "
                         f"Extend _id_from_path() to disambiguate.")
            seen_comp[comp_id] = str(rel)
            seen_file[file_id] = str(rel)

            # Source path relative to where wix build is invoked from (the
            # repo root). $(var.SourceDir) is set on the wix CLI line.
            source = f"$(var.SourceDir)\\{rel}".replace("/", "\\")

            lines.append(f'      <Component Id="{comp_id}" Directory="{did}" Guid="{{{file_guid}}}">')
            lines.append(f'        <File Id="{file_id}" Source="{_wxs_escape(source)}" KeyPath="yes" />')
            lines.append(f'      </Component>')
            component_count += 1
    lines.append('    </ComponentGroup>')
    lines.append('  </Fragment>')
    lines.append('</Wix>')

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    file_total = sum(len(v) for v in files_by_dir.values())
    print(f"wrote {out} ({component_count} components, {file_total} files)")


if __name__ == "__main__":
    main()
