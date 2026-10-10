"""prusa_profiles.py - PrusaSlicer's preset library, read as text.

PrusaSlicer keeps its system presets in ONE vendor bundle per vendor:
%APPDATA%\\PrusaSlicer\\vendor\\PrusaResearch.ini (the copy the
program updates by itself; the install's resources\\profiles folder
holds the shipped one). It is an INI file whose sections are
[print:NAME], [filament:NAME], [printer:NAME] and [printer_model:ID];
presets inherit with "inherits = A; B" (several parents allowed, in
order), and names starting with "*" are abstract building blocks the
GUI never shows. The user's own presets are single files without a
section header in %APPDATA%\\PrusaSlicer\\{print,filament,printer}\\,
each carrying its own "inherits" back into the bundle.

PrusaSlicer.ini records which printer models and nozzle variants the
user INSTALLED ("[vendor:PrusaResearch]" section, "model:COREONE =
HF0.4"); the machine list follows that, exactly as the GUI does.

Same surface as profiles.py so ProjectSession and the backend reach
either library the same way: library_root, user_library_root,
list_presets, list_user_presets, resolve_preset, scope_of,
ProfileError. Probed 2026-09-02 on PrusaSlicer 2.9.6 (docs\\log.md).
"""

import os
import re
from pathlib import Path

VENDOR = "PrusaResearch"

SCOPE_SECTIONS = {"process": "print", "filament": "filament",
                  "machine": "printer"}


class ProfileError(Exception):
    """A preset could not be found or resolved; message in plain words."""


# ------------------------------------------------------- locations


def user_library_root():
    """%APPDATA%\\PrusaSlicer, or None when APPDATA is unset."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "PrusaSlicer"


def library_root(slicer_exe):
    """The folder holding the vendor bundle, or None.

    The user's updated copy wins; the install's shipped copy is the
    fallback (a fresh install before the first configuration wizard).
    """
    root = user_library_root()
    if root and (root / "vendor" / f"{VENDOR}.ini").is_file():
        return root / "vendor"
    if slicer_exe:
        shipped = Path(slicer_exe).parent / "resources" / "profiles"
        if (shipped / f"{VENDOR}.ini").is_file():
            return shipped
    return None


def bundle_path(slicer_exe):
    root = library_root(slicer_exe)
    return (root / f"{VENDOR}.ini") if root else None


# --------------------------------------------------------- parsing

_SECTION_RE = re.compile(r"^\[([^:\]]+):(.*)\]\s*$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_]+)\s*=\s?(.*)$")

_bundle_cache = {}


def _parse_ini_sections(text):
    """{(kind, name): {key: value}} for a bundle; a headerless user
    preset file lands under (None, None)."""
    sections = {}
    current = (None, None)
    sections[current] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = (m.group(1).strip(), m.group(2).strip())
            sections.setdefault(current, {})
            continue
        km = _KEY_RE.match(line)
        if km:
            sections[current][km.group(1)] = km.group(2)
    return sections


def _bundle(slicer_exe):
    """The parsed vendor bundle, cached per file version."""
    path = bundle_path(slicer_exe)
    if path is None:
        raise ProfileError(
            "PrusaSlicer's preset library (the PrusaResearch vendor "
            "bundle) was not found - run PrusaSlicer's configuration "
            "wizard once, or check the slicer path in Settings.")
    try:
        stamp = (path.stat().st_mtime_ns, path.stat().st_size)
    except OSError:
        stamp = None
    cached = _bundle_cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    sections = _parse_ini_sections(
        path.read_text(encoding="utf-8", errors="replace"))
    _bundle_cache[path] = (stamp, sections)
    return sections


def installed_version(root=None):
    """PrusaSlicer's own version from its ini ("version = 2.9.6"),
    or "" when unknown."""
    root = user_library_root() if root is None else root
    if root is None:
        return ""
    try:
        text = (root / "PrusaSlicer.ini").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r"^version = (\S+)", text, re.M)
    return m.group(1) if m else ""


def installed_models(root=None):
    """{model id: [variants]} the user installed, from PrusaSlicer.ini;
    empty when the file is missing (then nothing is filtered)."""
    root = user_library_root() if root is None else root
    if root is None:
        return {}
    ini = root / "PrusaSlicer.ini"
    try:
        text = ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    result = {}
    inside = False
    for line in text.splitlines():
        if line.strip().startswith("["):
            inside = line.strip() == f"[vendor:{VENDOR}]"
            continue
        if not inside:
            continue
        m = re.match(r"model:([A-Za-z0-9_.+-]+)\s*=\s*(.*)$", line.strip())
        if m:
            result[m.group(1)] = [v.strip() for v in m.group(2).split(";")
                                  if v.strip()]
    return result


# --------------------------------------------------------- listing


def list_presets(slicer_exe, scope, installed=None):
    """Concrete system preset names of a scope, in bundle order.

    Machine presets are limited to the printer models and nozzle
    variants installed in PrusaSlicer (its own GUI does the same);
    with no record of installed models, every printer is listed.
    """
    kind = SCOPE_SECTIONS[scope]
    sections = _bundle(slicer_exe)
    if installed is None:
        installed = installed_models()
    names = []
    for (skind, name), values in sections.items():
        if skind != kind or not name or name.startswith("*"):
            continue
        if kind == "printer" and installed:
            resolved = _resolve(sections, kind, name, set())
            model = resolved.get("printer_model", "")
            variant = resolved.get("printer_variant", "")
            if model not in installed:
                continue
            if installed[model] and variant not in installed[model]:
                continue
        names.append(name)
    return names


def list_user_presets(scope, root=None):
    """The user's own saved presets of a scope (file stems)."""
    root = user_library_root() if root is None else root
    if root is None:
        return []
    folder = root / SCOPE_SECTIONS[scope]
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.ini"))


# -------------------------------------------------------- resolving


def _resolve(sections, kind, name, seen, depth=0):
    """A section with its inherits chain merged in (parents first)."""
    if depth > 20 or name in seen:
        raise ProfileError(
            f"The preset {name} inherits in a loop or too deeply.")
    values = sections.get((kind, name))
    if values is None:
        raise ProfileError(f"No {kind} preset named {name} exists in "
                           "PrusaSlicer's library.")
    result = {}
    parents = values.get("inherits", "")
    for parent in [p.strip() for p in parents.split(";") if p.strip()]:
        result.update(_resolve(sections, kind, parent, seen | {name},
                               depth + 1))
    result.update({k: v for k, v in values.items() if k != "inherits"})
    return result


def resolve_preset(slicer_exe, scope, name, root=None):
    """{key: value} of a preset with inheritance resolved - system
    first, then the user's own file of that name (whose "inherits"
    points back into the bundle). Values are strings, exactly as the
    library spells them."""
    kind = SCOPE_SECTIONS[scope]
    sections = _bundle(slicer_exe)
    if (kind, name) in sections:
        values = _resolve(sections, kind, name, set())
    else:
        root = user_library_root() if root is None else root
        path = (root / kind / f"{name}.ini") if root else None
        if path is None or not path.is_file():
            raise ProfileError(
                f"No {kind} preset named {name} was found in "
                "PrusaSlicer's library or among your own presets.")
        own = _parse_ini_sections(
            path.read_text(encoding="utf-8", errors="replace"))
        own = own.get((None, None), {})
        values = {}
        parent = own.get("inherits", "").strip()
        if parent:
            values.update(_resolve(sections, kind, parent, set()))
        values.update({k: v for k, v in own.items() if k != "inherits"})
    # Bookkeeping the bundle carries but a project never stores.
    for meta in ("compatible_printers", "compatible_printers_condition",
                 "compatible_prints", "compatible_prints_condition",
                 "renamed_from", "alias", "instantiation"):
        values.pop(meta, None)
    return values


def printer_defaults(slicer_exe, printer_name):
    """(print preset, filament preset) a printer preset names as its
    defaults - what a fresh project for it starts from."""
    values = resolve_preset(slicer_exe, "machine", printer_name)
    print_name = values.get("default_print_profile", "").strip()
    filament = values.get("default_filament_profile", "").strip()
    filament = [f.strip().strip('"') for f in filament.split(";")
                if f.strip()]
    return print_name, (filament[0] if filament else "")


# ----------------------------------------------------------- scopes

_scope_cache = {}

# When a key is in no bundle section (a project from a newer version,
# or no bundle at all), its name decides.
_MACHINE_PREFIXES = (
    "printer_", "nozzle_", "extruder_", "retract_", "bed_shape",
    "max_print_height", "gcode_", "machine_", "host_", "print_host",
    "printhost_", "thumbnails", "z_offset", "use_", "start_gcode",
    "end_gcode", "before_layer_gcode", "layer_gcode", "toolchange_gcode",
    "between_objects_gcode", "color_change_gcode", "pause_print_gcode",
    "template_custom_gcode", "silent_mode", "remaining_times",
    "binary_gcode", "min_layer_height", "max_layer_height",
    "single_extruder_multi_material", "wipe", "deretract_speed",
    "variable_layer_height", "default_print_profile",
    "default_filament_profile", "travel_", "autoemit_", "physical_",
    "cooling_tube", "parking_pos", "extra_loading_move", "high_current",
    "bed_custom", "prefer_clockwise", "emit_machine",
)
_FILAMENT_PREFIXES = (
    "filament_", "temperature", "first_layer_temperature",
    "bed_temperature", "first_layer_bed_temperature", "fan_",
    "min_fan_speed", "max_fan_speed", "bridge_fan_speed",
    "disable_fan_first_layers", "full_fan_speed_layer", "cooling",
    "slowdown_below_layer_time", "min_print_speed", "extrusion_multiplier",
    "start_filament_gcode", "end_filament_gcode", "idle_temperature",
    "chamber_", "enable_dynamic_fan_speeds", "overhang_fan_speed",
    "compatible_", "extruder_colour",
)


def scope_of(slicer_exe, key):
    """'process' | 'filament' | 'machine' for a settings key: the
    section kind the bundle lists it under, else by its name."""
    try:
        path = bundle_path(slicer_exe)
    except Exception:
        path = None
    if path is not None:
        index = _scope_cache.get(path)
        if index is None:
            index = {}
            try:
                sections = _bundle(slicer_exe)
            except ProfileError:
                sections = {}
            # print first, then filament, then printer: a key present in
            # several kinds (rare bookkeeping keys) counts as the first.
            for kind, scope in (("print", "process"),
                                ("filament", "filament"),
                                ("printer", "machine")):
                for (skind, _name), values in sections.items():
                    if skind == kind:
                        for k in values:
                            index.setdefault(k, scope)
            _scope_cache[path] = index
        if key in index:
            return index[key]
    if key.startswith(_MACHINE_PREFIXES):
        return "machine"
    if key.startswith(_FILAMENT_PREFIXES):
        return "filament"
    return "process"
