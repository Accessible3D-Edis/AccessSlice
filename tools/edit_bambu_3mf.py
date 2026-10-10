#!/usr/bin/env python3
"""edit_bambu_3mf.py - inspect and edit Bambu Studio project 3MF files as text.

Why this exists
---------------
A Bambu Studio project 3MF is a zip archive of XML and JSON. Everything the
GUI hides behind mouse-only panels is plain text inside it, so a screen-reader
user can inspect and change a print job without fighting the GUI:

  3D/3dmodel.model                 object placement (build transforms)
  Metadata/model_settings.config   per-object extruder, per-plate filament map
  Metadata/project_settings.config every slicer setting as one JSON object

Workflow: import the model into Bambu Studio once, save the project (this
creates the genuine structure that Studio trusts and keeps placement for),
CLOSE Studio, edit the file with this tool, reopen, slice, send.

Hard-won facts this tool encodes (a tactile map tile, 2026-08-24):
- Studio re-arranges foreign 3MFs/STLs to plate centre on open; only a
  genuine Studio-saved project keeps its placement.
- On dual-nozzle printers (H2C) the automatic filament grouping ignores
  object position and AMS state; forcing a nozzle needs the per-plate
  manual map (filament_map_mode="Manual" + filament_maps).
- Zip entry names must use forward slashes; PowerShell's Compress-Archive
  and .NET Framework's CreateFromDirectory write backslashes and corrupt
  the file. Python's zipfile does it correctly, which this tool relies on.

Commands
--------
  info      FILE                      summary: objects, positions, extruders,
                                      filament map, key print settings
  move      FILE [--x N] [--y N] [--z N] [--dx N] [--dy N] [--objectid ID]
                                      set or shift an object's centre (mm)
  set-filament-map FILE --map "1 1 2 1" [--mode Manual]
                                      per-plate filament->extruder map
                                      (1 = left nozzle, 2 = right nozzle,
                                      one number per project filament)
  set-extruder FILE --objectid ID --extruder N
                                      which project filament an object uses
  get-setting  FILE KEY               read one project_settings value
  set-setting  FILE KEY VALUE         write one project_settings value
                                      (comma-separate list values)
  list-settings FILE [SUBSTRING]      list setting keys, optionally filtered

Every mutating command writes a backup next to the file first
(<name>.pre-edit-backup.3mf), then verifies the rewritten archive
structurally. Pass --no-backup to skip the backup.

All output is plain text, one fact per line, screen-reader friendly.
"""

import argparse
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

MODEL_ENTRY = "3D/3dmodel.model"
MODEL_SETTINGS_ENTRY = "Metadata/model_settings.config"
PROJECT_SETTINGS_ENTRY = "Metadata/project_settings.config"

# A build <item> in 3dmodel.model: transform is 12 numbers, 3x3 rotation
# then the translation x y z, which for Bambu is the object centre on the
# plate in millimetres.
ITEM_RE = re.compile(
    r'<item\b(?P<attrs>[^>]*?)objectid="(?P<objectid>\d+)"'
    r'(?P<attrs2>[^>]*?)transform="(?P<transform>[^"]+)"'
)


# ---------------------------------------------------------------- zip layer


def load_entries(path):
    """Read the whole archive into an ordered {name: bytes} dict."""
    entries = {}
    with zipfile.ZipFile(path, "r") as z:
        for name in z.namelist():
            entries[name] = z.read(name)
    return entries


def save_entries(path, entries):
    """Write entries back as a zip with forward-slash names (3MF spec)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name.replace("\\", "/"), data)
    Path(path).write_bytes(buf.getvalue())


def xml_error(z, name):
    """None when zip entry `name` is well-formed XML, else expat's reason.

    Streamed in 1 MiB pieces: expat refuses a single buffer over 1 GiB
    ("out of memory: line 1, column 0" at once, whatever RAM is free),
    and a 23-million-triangle mesh is a 1.7 GB 3dmodel.model. Building
    no tree also keeps memory flat. Namespace-aware like ElementTree,
    so an unbound prefix is still caught."""
    import pyexpat

    parser = pyexpat.ParserCreate(namespace_separator="}")
    try:
        with z.open(name) as f:
            while chunk := f.read(1 << 20):
                parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except pyexpat.ExpatError as e:
        return str(e)
    return None


def verify(path, expected_names):
    """Structural check after writing. Returns a list of problem strings."""
    problems = []
    with zipfile.ZipFile(path, "r") as z:
        names = z.namelist()
        if sorted(names) != sorted(expected_names):
            problems.append("entry list changed unexpectedly")
        for name in names:
            if "\\" in name:
                problems.append(f"backslash in entry name: {name}")
        for name in (MODEL_ENTRY, MODEL_SETTINGS_ENTRY):
            if name in names:
                error = xml_error(z, name)
                if error:
                    problems.append(f"{name} is not well-formed XML: {error}")
        if PROJECT_SETTINGS_ENTRY in names:
            try:
                json.loads(z.read(PROJECT_SETTINGS_ENTRY))
            except ValueError as e:
                problems.append(f"{PROJECT_SETTINGS_ENTRY} is not valid JSON: {e}")
    return problems


def backup_then_save(path, entries, no_backup):
    """Backup, save, verify; exit non-zero on verification failure."""
    path = Path(path)
    if not no_backup:
        bak = path.with_name(path.stem + ".pre-edit-backup.3mf")
        shutil.copy2(path, bak)
        print(f"backup written: {bak.name}")
    save_entries(path, entries)
    problems = verify(path, list(entries.keys()))
    if problems:
        for p in problems:
            print(f"VERIFY FAILED: {p}")
        sys.exit(1)
    print(f"saved and verified: {path.name}")


# ------------------------------------------------------------- text helpers


def get_text(entries, name):
    if name not in entries:
        sys.exit(f"error: archive has no {name} - is this a Bambu project 3MF? "
                 "Open the model in Bambu Studio and save the project once first.")
    return entries[name].decode("utf-8")


def find_items(model_xml):
    """Yield (objectid, [12 floats]) for each build item."""
    for m in ITEM_RE.finditer(model_xml):
        nums = [float(v) for v in m.group("transform").split()]
        yield m.group("objectid"), nums


def fmt_num(v):
    """Format a float without trailing zeros (165.0 -> 165)."""
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def object_names(model_settings_xml):
    """{object id: name} from model_settings.config."""
    names = {}
    for m in re.finditer(
        r'<object id="(\d+)">.*?<metadata key="name" value="([^"]*)"',
        model_settings_xml,
        re.S,
    ):
        names[m.group(1)] = m.group(2)
    return names


# ------------------------------------------------------------------ commands


def cmd_info(args):
    entries = load_entries(args.file)
    model_xml = get_text(entries, MODEL_ENTRY)
    ms_xml = get_text(entries, MODEL_SETTINGS_ENTRY)
    names = object_names(ms_xml)

    print(f"file: {Path(args.file).name}")

    for objectid, nums in find_items(model_xml):
        x, y, z = nums[9], nums[10], nums[11]
        name = names.get(objectid, "(unnamed)")
        print(f"object {objectid}: {name}")
        print(f"  centre position: x {fmt_num(x)}  y {fmt_num(y)}  z {fmt_num(z)} mm")
        rot = nums[:9]
        if rot == [1, 0, 0, 0, 1, 0, 0, 0, 1]:
            print("  rotation: none")
        else:
            print(f"  rotation matrix: {' '.join(fmt_num(v) for v in rot)}")

    for m in re.finditer(
        r'<object id="(\d+)">\s*<metadata key="name" value="[^"]*"/>\s*'
        r'<metadata key="extruder" value="(\d+)"/>',
        ms_xml,
    ):
        print(f"object {m.group(1)}: uses project filament {m.group(2)}")

    mode = re.search(r'key="filament_map_mode" value="([^"]*)"', ms_xml)
    fmap = re.search(r'key="filament_maps" value="([^"]*)"', ms_xml)
    if mode and fmap:
        print(f"filament map mode: {mode.group(1)}")
        nozzle = {"1": "left nozzle", "2": "right nozzle"}
        parts = fmap.group(1).split()
        for i, v in enumerate(parts, start=1):
            print(f"  project filament {i} -> extruder {v} ({nozzle.get(v, 'unknown')})")
    else:
        print("filament map: not present (single-nozzle project, or plate "
              "metadata missing - save from Bambu Studio once to create it)")

    if PROJECT_SETTINGS_ENTRY in entries:
        ps = json.loads(entries[PROJECT_SETTINGS_ENTRY])
        for key in ("printer_settings_id", "print_settings_id", "layer_height",
                    "sparse_infill_density", "wall_loops", "enable_support"):
            if key in ps:
                print(f"setting {key}: {ps[key]}")
    else:
        print("project settings: not present (Studio will use its current presets)")


def cmd_move(args):
    entries = load_entries(args.file)
    model_xml = get_text(entries, MODEL_ENTRY)
    items = list(find_items(model_xml))
    if not items:
        sys.exit("error: no build items found in 3dmodel.model")

    if args.objectid:
        items = [it for it in items if it[0] == args.objectid]
        if not items:
            sys.exit(f"error: no build item with objectid {args.objectid}")
    elif len(items) > 1:
        ids = ", ".join(it[0] for it in items)
        sys.exit(f"error: several objects present ({ids}) - pick one with --objectid")

    objectid, nums = items[0]
    old = nums[9:12]
    new = list(old)
    if args.x is not None:
        new[0] = args.x
    if args.y is not None:
        new[1] = args.y
    if args.z is not None:
        new[2] = args.z
    if args.dx:
        new[0] += args.dx
    if args.dy:
        new[1] += args.dy

    if new == old:
        print("nothing to change - give --x/--y/--z or --dx/--dy")
        return

    old_tf = " ".join(
        fmt_num(v) if i < 9 else v_str
        for i, (v, v_str) in enumerate(zip(nums, [fmt_num(v) for v in nums]))
    )
    # Rebuild the exact transform string from the parsed numbers is fragile
    # against formatting differences, so instead replace only inside the
    # matched item for this objectid.
    def repl(m):
        if m.group("objectid") != objectid:
            return m.group(0)
        tf_nums = [float(v) for v in m.group("transform").split()]
        tf_nums[9:12] = new
        new_tf = " ".join(fmt_num(v) for v in tf_nums)
        return m.group(0).replace(m.group("transform"), new_tf)

    new_xml, count = ITEM_RE.subn(repl, model_xml)
    if new_xml == model_xml:
        sys.exit("error: transform replacement produced no change")
    entries[MODEL_ENTRY] = new_xml.encode("utf-8")

    print(f"object {objectid} centre: "
          f"x {fmt_num(old[0])} -> {fmt_num(new[0])}  "
          f"y {fmt_num(old[1])} -> {fmt_num(new[1])}  "
          f"z {fmt_num(old[2])} -> {fmt_num(new[2])}")
    backup_then_save(args.file, entries, args.no_backup)


def cmd_set_filament_map(args):
    entries = load_entries(args.file)
    ms_xml = get_text(entries, MODEL_SETTINGS_ENTRY)
    new_map = " ".join(args.map.replace(",", " ").split())
    if not all(v in ("1", "2") for v in new_map.split()):
        sys.exit("error: map values must be 1 (left nozzle) or 2 (right nozzle)")

    def set_meta(xml, key, value):
        pat = re.compile(f'(<metadata key="{key}" value=")[^"]*(")')
        if pat.search(xml):
            return pat.sub(rf"\g<1>{value}\g<2>", xml), "updated"
        # Key missing: insert right after the plater_id line inside <plate>.
        anchor = re.search(r'(<metadata key="plater_id" value="[^"]*"/>)', xml)
        if not anchor:
            sys.exit("error: no <plate> metadata found - save the project from "
                     "Bambu Studio once so the plate structure exists")
        insert = anchor.group(1) + f'\n    <metadata key="{key}" value="{value}"/>'
        return xml.replace(anchor.group(1), insert), "inserted"

    ms_xml, what_mode = set_meta(ms_xml, "filament_map_mode", args.mode)
    ms_xml, what_map = set_meta(ms_xml, "filament_maps", new_map)
    entries[MODEL_SETTINGS_ENTRY] = ms_xml.encode("utf-8")

    print(f"filament_map_mode {what_mode}: {args.mode}")
    print(f"filament_maps {what_map}: {new_map}")
    nozzle = {"1": "left nozzle", "2": "right nozzle"}
    for i, v in enumerate(new_map.split(), start=1):
        print(f"  project filament {i} -> extruder {v} ({nozzle[v]})")
    backup_then_save(args.file, entries, args.no_backup)


def cmd_set_extruder(args):
    entries = load_entries(args.file)
    ms_xml = get_text(entries, MODEL_SETTINGS_ENTRY)
    pat = re.compile(
        rf'(<object id="{args.objectid}">.*?<metadata key="extruder" value=")\d+(")',
        re.S,
    )
    new_xml, count = pat.subn(rf"\g<1>{args.extruder}\g<2>", ms_xml)
    if count == 0:
        sys.exit(f"error: object {args.objectid} with an extruder key not found")
    entries[MODEL_SETTINGS_ENTRY] = new_xml.encode("utf-8")
    print(f"object {args.objectid}: extruder (project filament) set to {args.extruder}")
    backup_then_save(args.file, entries, args.no_backup)


def cmd_get_setting(args):
    entries = load_entries(args.file)
    ps = json.loads(get_text(entries, PROJECT_SETTINGS_ENTRY))
    if args.key not in ps:
        sys.exit(f"error: no setting named {args.key} "
                 f"(try: list-settings {Path(args.file).name} {args.key})")
    value = ps[args.key]
    if isinstance(value, list):
        print(f"{args.key}: {', '.join(str(v) for v in value)}  "
              f"(list, one value per filament/extruder)")
    else:
        print(f"{args.key}: {value}")


def cmd_set_setting(args):
    entries = load_entries(args.file)
    ps = json.loads(get_text(entries, PROJECT_SETTINGS_ENTRY))
    if args.key not in ps:
        sys.exit(f"error: no setting named {args.key} - refusing to invent new "
                 "keys (Bambu would ignore or reject them)")
    old = ps[args.key]
    if isinstance(old, list):
        new = [v.strip() for v in args.value.split(",")]
        if len(new) != len(old):
            print(f"note: {args.key} had {len(old)} values, you gave {len(new)}")
    else:
        new = args.value
    ps[args.key] = new
    entries[PROJECT_SETTINGS_ENTRY] = json.dumps(
        ps, indent=4, ensure_ascii=False
    ).encode("utf-8")
    print(f"{args.key}: {old} -> {new}")
    backup_then_save(args.file, entries, args.no_backup)


def cmd_list_settings(args):
    entries = load_entries(args.file)
    ps = json.loads(get_text(entries, PROJECT_SETTINGS_ENTRY))
    keys = sorted(k for k in ps if not args.filter or args.filter.lower() in k.lower())
    if not keys:
        print("no matching settings")
        return
    for k in keys:
        v = ps[k]
        if isinstance(v, list):
            shown = ", ".join(str(x) for x in v[:4]) + (" ..." if len(v) > 4 else "")
        else:
            shown = str(v)
        if len(shown) > 60:
            shown = shown[:57] + "..."
        print(f"{k}: {shown}")
    print(f"({len(keys)} settings)")


# --------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(
        description="Inspect and edit Bambu Studio project 3MF files as text. "
                    "Close Bambu Studio before editing a file it has open.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(fn=fn)
        sp.add_argument("file", help="path to the .3mf project file")
        return sp

    add("info", cmd_info, help="summarise placement, filaments and key settings")

    sp = add("move", cmd_move, help="set or shift an object's centre position (mm)")
    sp.add_argument("--objectid", help="build item objectid (needed if several objects)")
    sp.add_argument("--x", type=float, help="absolute centre X")
    sp.add_argument("--y", type=float, help="absolute centre Y")
    sp.add_argument("--z", type=float, help="absolute centre Z (rarely needed)")
    sp.add_argument("--dx", type=float, default=0, help="shift X by this much")
    sp.add_argument("--dy", type=float, default=0, help="shift Y by this much")
    sp.add_argument("--no-backup", action="store_true")

    sp = add("set-filament-map", cmd_set_filament_map,
             help="per-plate filament-to-nozzle map (dual-nozzle printers)")
    sp.add_argument("--map", required=True,
                    help='one value per project filament, e.g. "1 1 2 1"; '
                         "1 = left nozzle, 2 = right nozzle")
    sp.add_argument("--mode", default="Manual",
                    help='map mode, default "Manual" (the only one that sticks)')
    sp.add_argument("--no-backup", action="store_true")

    sp = add("set-extruder", cmd_set_extruder,
             help="which project filament an object prints with")
    sp.add_argument("--objectid", required=True)
    sp.add_argument("--extruder", required=True, type=int,
                    help="project filament number (1-based)")
    sp.add_argument("--no-backup", action="store_true")

    sp = add("get-setting", cmd_get_setting, help="read one slicer setting")
    sp.add_argument("key")

    sp = add("set-setting", cmd_set_setting, help="change one slicer setting")
    sp.add_argument("key")
    sp.add_argument("value", help="new value; comma-separate list values")
    sp.add_argument("--no-backup", action="store_true")

    sp = add("list-settings", cmd_list_settings, help="list slicer setting keys")
    sp.add_argument("filter", nargs="?", help="only keys containing this text")

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
