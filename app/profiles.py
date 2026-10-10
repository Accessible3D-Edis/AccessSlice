"""profiles.py - Bambu Studio's own preset library, read from its install.

Bambu Studio ships every system preset as plain JSON under
<install dir>\resources\profiles\BBL\ in three folders:

  process\   print profiles ("0.20mm Standard @BBL H2C", ...)
  filament\  filament presets ("Bambu PLA Matte @BBL H2C", ...)
  machine\   printer definitions ("Bambu Lab H2C 0.4 nozzle", ...)

Presets inherit from base profiles via their "inherits" field (a preset
name, which is also its file name). Resolving a preset means walking that
chain and merging child values over parent values.

This module also answers a question the save pipeline depends on: which
SCOPE does a setting key belong to - process, filament, or machine? The
project file's different_settings_to_system list (see slicer_project.py)
has one slot per scope instance, and a changed key must be registered in
the right slot or Bambu Studio silently discards the change. Scope is
learned from the profile library itself: every key seen in filament
profiles is filament-scope, machine profiles machine-scope, process
profiles process-scope. A small fallback heuristic covers machines
without a readable profile library.
"""

import functools
import json
import os
from pathlib import Path

# Keys in profile JSONs that are preset bookkeeping, not slicer settings.
META_KEYS = {
    "type", "name", "inherits", "from", "setting_id", "instantiation",
    "description", "filament_id", "filament_settings_id", "print_settings_id",
    "printer_settings_id", "printer_model", "printer_variant",
    "compatible_printers", "compatible_printers_condition",
    "compatible_prints", "compatible_prints_condition",
    "upward_compatible_machine", "default_print_profile",
    "default_filament_profile",
}

SCOPE_FOLDERS = {
    "process": "process",
    "filament": "filament",
    "machine": "machine",
}


# Sentinel: "use the Bambu default" - distinct from an explicit None
# (a per-slicer user library that genuinely does not exist).
_DEFAULT = object()


def library_root(slicer_exe, vendor="BBL"):
    """The vendor profile folder for a slicer executable, or None.

    All Orca-family slicers ship system presets the same way -
    <install>\\resources\\profiles\\<vendor>\\{process,filament,machine}
    - only the vendor folder name differs (Bambu "BBL", Creality Print
    "Creality"). Per-slicer wrapper modules pass their vendor.
    """
    if not slicer_exe:
        return None
    root = Path(slicer_exe).parent / "resources" / "profiles" / vendor
    return root if root.is_dir() else None


@functools.lru_cache(maxsize=8)
def _folder_index(folder_str):
    """{preset name: file path} for one profile folder (cached)."""
    index = {}
    folder = Path(folder_str)
    for f in folder.glob("*.json"):
        index[f.stem] = f
    return index


# ----------------------------------------------------- user presets
#
# Presets the user saved in Bambu Studio live under
# %APPDATA%\BambuStudio\user\<account id>\{process,filament,machine}\
# as JSON files with a "name" and an "inherits" chain that usually
# leads back into the system library. They have no "instantiation"
# flag - a saved user preset is by definition pickable.


def user_library_root():
    """%APPDATA%\\BambuStudio\\user, or None when it does not exist."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    root = Path(appdata) / "BambuStudio" / "user"
    return root if root.is_dir() else None


def _user_index(scope, root=_DEFAULT):
    """{preset name: file path} across every account folder, freshly
    scanned - the folder only changes while the slicer runs, but a
    stale cache would hide a preset saved minutes ago. root: the
    user library to scan (default: Bambu Studio's)."""
    if root is _DEFAULT:
        root = user_library_root()
    index = {}
    if not root:
        return index
    for account in sorted(root.iterdir()):
        folder = account / SCOPE_FOLDERS[scope]
        if not folder.is_dir():
            continue
        for f in folder.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            index[str(data.get("name") or f.stem)] = f
    return index


def list_user_presets(scope, root=_DEFAULT):
    """The names of the user's own saved presets of one scope, sorted."""
    return sorted(_user_index(scope, root))


def list_presets(slicer_exe, scope, vendor="BBL"):
    """All instantiable preset names of one scope, sorted."""
    root = library_root(slicer_exe, vendor)
    if not root:
        return []
    names = []
    for name, path in _folder_index(str(root / SCOPE_FOLDERS[scope])).items():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Base profiles (instantiation false) are building blocks, not
        # presets a user can pick.
        if str(data.get("instantiation", "")).lower() == "true":
            names.append(name)
    return sorted(names)


def resolve_preset(slicer_exe, scope, name, _depth=0, vendor="BBL",
                   user_root=_DEFAULT):
    """Merge a preset's full inheritance chain into one {key: value} dict.

    Child values win over parent values. Bookkeeping keys are dropped.
    Raises ProfileError if the preset or an ancestor cannot be read.
    """
    if _depth > 10:
        raise ProfileError(f"Inheritance chain too deep for {name}.")
    # The user's own saved presets are checked first; the slicers refuse
    # to save a user preset under a system name, so the two never clash.
    path = _user_index(scope, user_root).get(name)
    if not path:
        root = library_root(slicer_exe, vendor)
        if not root:
            raise ProfileError(
                "The slicer's profile library was not found next to the "
                "slicer program - check the slicer path on the Settings "
                "page."
            )
        index = _folder_index(str(root / SCOPE_FOLDERS[scope]))
        path = index.get(name)
    if not path:
        raise ProfileError(f"No {scope} preset named {name} in the library "
                           "or among your own saved presets.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProfileError(f"Could not read preset {name}: {e}")

    parent_name = data.get("inherits")
    merged = {}
    if parent_name:
        merged.update(resolve_preset(slicer_exe, scope, parent_name,
                                     _depth + 1, vendor=vendor,
                                     user_root=user_root))
    for key, value in data.items():
        if key not in META_KEYS:
            merged[key] = value
    return merged


class ProfileError(Exception):
    """Raised for anything the UI should show as a friendly error."""


# --------------------------------------------------------- key scopes


# Fallback when no profile library is readable. Prefix-based, matching
# Bambu/Orca naming conventions closely enough for the diff bookkeeping.
_FALLBACK_FILAMENT_PREFIXES = (
    "filament_", "nozzle_temperature", "fan_", "temperature_vitrification",
    "slow_down_", "close_fan_", "full_fan_speed_layer", "additional_cooling",
    "overhang_fan_", "cool_plate_temp", "eng_plate_temp", "hot_plate_temp",
    "textured_plate_temp", "supertack_plate_temp", "reduce_fan_stop_start_freq",
    "pressure_advance", "enable_pressure_advance", "chamber_temperature",
    "activate_air_filtration", "during_print_exhaust_fan_speed",
    "complete_print_exhaust_fan_speed", "required_nozzle_HRC",
    "impact_strength_z", "counter_", "circle_compensation_speed",
)
_FALLBACK_MACHINE_PREFIXES = (
    "machine_", "printable_", "printer_", "nozzle_diameter", "extruder_",
    "retraction_", "retract_", "z_hop", "wipe", "deretraction_",
    "nozzle_volume", "nozzle_type", "bed_", "best_object_pos", "grab_length",
    "cooling_chamber", "change_filament_gcode", "time_lapse_gcode",
    "head_wrap_detect_zone", "auxiliary_fan", "support_air_filtration",
    "support_chamber_temp_control", "scan_first_layer", "upward_compatible_",
    "default_nozzle_volume_type", "gcode_flavor", "silent_mode",
    "scale_pa_with_flow", "single_extruder_multi_material", "template_custom_gcode",
    "thumbnail_size", "use_relative_e_distances", "extruder_clearance_",
    "fan_speedup_time", "fan_kickstart", "hotend_cooling_rate", "hotend_heating_rate",
)


@functools.lru_cache(maxsize=8)
def _scopes_from_library(root_str):
    """{key: scope} learned by scanning every profile file once."""
    scopes = {}
    root = Path(root_str)
    # machine and filament are scanned LAST so they win ties: a few keys
    # appear in more than one folder and the diff slots that matter most
    # for correctness are the filament and machine ones.
    for scope in ("process", "machine", "filament"):
        folder = root / SCOPE_FOLDERS[scope]
        for f in folder.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for key in data:
                if key not in META_KEYS:
                    scopes[key] = scope
    return scopes


def scope_of(slicer_exe, key, vendor="BBL"):
    """'process' | 'filament' | 'machine' for a settings key."""
    root = library_root(slicer_exe, vendor)
    if root:
        scopes = _scopes_from_library(str(root))
        if key in scopes:
            return scopes[key]
    for prefix in _FALLBACK_FILAMENT_PREFIXES:
        if key.startswith(prefix):
            return "filament"
    for prefix in _FALLBACK_MACHINE_PREFIXES:
        if key.startswith(prefix):
            return "machine"
    return "process"
