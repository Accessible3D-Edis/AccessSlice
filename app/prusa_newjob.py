"""prusa_newjob.py - a PrusaSlicer project from a model file, no GUI.

Facts (probed 2026-09-02, PrusaSlicer 2.9.6, docs\\log.md): the CLI
exports a model to a 3MF (`--export-3mf --output x.3mf model.stl`,
with `--center X,Y` to put it on the bed), but NEVER writes the
Metadata/Slic3r_PE.config settings entry - even with `--load` or the
three profile names given. Only the GUI writes it. So AccessSlice
writes it itself, from one of two sources:

- a DONOR: the newest project already saved for the chosen printer
  donates its settings verbatim (the same donor design as Bambu's
  new-job flow - "the settings of your newest saved project"), or
- the PRESETS: with no donor yet, the printer preset's own default
  print and filament profiles, resolved from PrusaSlicer's bundle,
  merged into one config the way PrusaSlicer's `--save` lays it out.
  That is what makes a Core One job possible from nothing.

The object gets the model file's bare name (the CLI writes the full
path), and the result is verified by re-reading it as a project.
"""

import re
import subprocess
from pathlib import Path

import edit_bambu_3mf as engine
import prusa_format
import prusa_profiles
import build_job
from errors import ProjectError
from newjob import MODEL_SUFFIXES, unique_target  # noqa: F401
from prusa_engine import engine_exe

# As long as a slice may take (engine_driver.TIMEOUT_S): PrusaSlicer
# needs about five minutes just to import a 23-million-triangle STL
# (Skyward Dragon, 2026-09-25), and the old 300 s stopped it.
EXPORT_TIMEOUT_S = 60 * 60

# The "donor" that means: build the settings from the presets, for a
# named printer. A Path (never an existing file) so the server's flow
# and logging (donor.name) treat it like any donor.
_PRESET_PREFIX = "(PrusaSlicer presets for "


def preset_donor(printer_name):
    return Path(f"{_PRESET_PREFIX}{printer_name})")


def preset_donor_printer(donor_path):
    """The printer name a preset donor stands for, else None."""
    name = Path(donor_path).name if donor_path is not None else ""
    if name.startswith(_PRESET_PREFIX) and name.endswith(")"):
        return name[len(_PRESET_PREFIX):-1]
    return None


def _project_printer(path):
    """printer_settings_id of a PrusaSlicer project, or None."""
    try:
        import zipfile
        with zipfile.ZipFile(path) as z:
            if prusa_format.CONFIG_ENTRY not in z.namelist():
                return None
            text = z.read(prusa_format.CONFIG_ENTRY).decode(
                "utf-8", "replace")
    except (OSError, zipfile.BadZipFile):
        return None
    m = re.search(r"^; printer_settings_id = (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def find_donor(candidates, printer_name):
    """The newest saved PrusaSlicer project for this printer, else the
    preset donor - a Core One job never needs a GUI-made project."""
    for path in candidates:
        path = Path(path)
        if not path.is_file():
            continue
        if _project_printer(path) == printer_name:
            return path
    return preset_donor(printer_name)


DEFAULTS_FILE = Path(__file__).with_name("prusa_defaults.ini")


def builtin_defaults():
    """PrusaSlicer's built-in defaults for every setting key, captured
    from `prusa-slicer-console.exe --save` with no presets (2.9.6).
    {key: raw value}; empty when the file is missing."""
    try:
        text = DEFAULTS_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    sections = prusa_profiles._parse_ini_sections(text)
    return dict(sections.get((None, None), {}))


def config_from_presets(slicer_exe, printer_name):
    """(header, settings) merged from the printer preset and its
    default print and filament profiles - the flat namespace a
    project stores."""
    try:
        print_name, filament_name = prusa_profiles.printer_defaults(
            slicer_exe, printer_name)
        machine = prusa_profiles.resolve_preset(slicer_exe, "machine",
                                                printer_name)
        process = (prusa_profiles.resolve_preset(slicer_exe, "process",
                                                 print_name)
                   if print_name else {})
        filament = (prusa_profiles.resolve_preset(slicer_exe, "filament",
                                                  filament_name)
                    if filament_name else {})
    except prusa_profiles.ProfileError as e:
        raise ProjectError(str(e))
    # Presets in the bundle carry only what they override; PrusaSlicer
    # fills the rest from its built-in defaults when it loads them. So
    # does this: app\\prusa_defaults.ini is the engine's own answer to
    # `--save` with no presets (2.9.6), laid under the three presets.
    merged = {}
    for values in (builtin_defaults(), process, filament, machine):
        for key, value in values.items():
            merged[key] = prusa_format.split_value(key, value)
    merged["print_settings_id"] = print_name
    merged["printer_settings_id"] = printer_name
    merged["filament_settings_id"] = [filament_name]
    settings = {k: merged[k] for k in sorted(merged)}
    version = prusa_profiles.installed_version()
    header = ["; generated by AccessSlice from PrusaSlicer "
              f"{version + ' ' if version else ''}presets", ""]
    return header, settings


def config_from_donor(donor_path):
    try:
        entries = engine.load_entries(donor_path)
        text = entries[prusa_format.CONFIG_ENTRY].decode("utf-8")
    except (OSError, KeyError, UnicodeDecodeError) as e:
        raise ProjectError(
            f"The settings of {Path(donor_path).name} could not be "
            f"read ({e}).")
    return prusa_format.parse_config(text)


def build_export_command(exe, models, out_name, centre):
    """The CLI invocation (split out so tests can stand in a fake)."""
    cmd = [engine_exe(exe), "--export-3mf", "--output", out_name]
    if centre:
        cmd += ["--center", f"{centre[0]:g},{centre[1]:g}"]
    return cmd + [str(m) for m in models]


def create_project(exe, donor_path, model_paths, target_path):
    """Build target_path from the models; returns spoken notes."""
    target = Path(target_path)
    models = [Path(m) for m in model_paths]
    for m in models:
        if not m.is_file():
            raise ProjectError(f"The model file {m} was not found.")
    preset_printer = preset_donor_printer(donor_path)
    if preset_printer:
        header, settings = config_from_presets(exe, preset_printer)
    elif donor_path is None:
        raise ProjectError("No settings source was given for the "
                           "new project.")
    else:
        header, settings = config_from_donor(donor_path)
    size = prusa_format.bed_size(settings)
    centre = (size[0] / 2, size[1] / 2) if size else None
    out_dir = target.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_name = target.stem + ".building.3mf"
    cmd = build_export_command(exe, models, tmp_name, centre)
    build_job.stage("PrusaSlicer is reading the model and building the "
                    "project.")
    try:
        proc = build_job.run(cmd, cwd=str(out_dir), capture_output=True,
                             text=True, timeout=EXPORT_TIMEOUT_S,
                             stdin=subprocess.DEVNULL)
    except build_job.BuildCancelled:
        (out_dir / tmp_name).unlink(missing_ok=True)
        raise
    except subprocess.TimeoutExpired:
        raise ProjectError(
            "PrusaSlicer's engine was still building the project after "
            f"{EXPORT_TIMEOUT_S // 60} minutes and was stopped. A very "
            "detailed model can be too big to build; a model with fewer "
            "triangles builds faster.")
    except (OSError, subprocess.SubprocessError) as e:
        raise ProjectError(
            f"PrusaSlicer's engine could not be run to build the "
            f"project ({e}). Check the slicer path in Settings.")
    tmp = out_dir / tmp_name
    if proc.returncode != 0 or not tmp.is_file():
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        reason = tail[-1] if tail else f"exit code {proc.returncode}"
        raise ProjectError(
            f"PrusaSlicer's engine did not build the project: {reason}")
    build_job.stage("Checking the built project.")
    try:
        entries = engine.load_entries(tmp)
        model_config = entries[prusa_format.MODEL_CONFIG_ENTRY].decode(
            "utf-8")
    except (OSError, KeyError, UnicodeDecodeError) as e:
        tmp.unlink(missing_ok=True)
        raise ProjectError(
            f"The built project could not be read back ({e}).")
    entries[prusa_format.MODEL_CONFIG_ENTRY] = \
        prusa_format.rename_objects_to_basenames(model_config).encode("utf-8")
    entries[prusa_format.CONFIG_ENTRY] = prusa_format.serialise_config(
        header, settings).encode("utf-8")
    engine.save_entries(target, entries)
    tmp.unlink(missing_ok=True)
    problems = _verify(target, entries)
    if problems:
        target.unlink(missing_ok=True)
        raise ProjectError(
            "The built project failed its check and was not kept: "
            + "; ".join(problems))
    names = ", ".join(m.name for m in models)
    source = ("PrusaSlicer's own presets for "
              f"{settings.get('printer_settings_id', 'the printer')}"
              if preset_printer
              else f"the settings of {Path(donor_path).name}")
    return [f"Created {target.name} from {names} on {source}, placed "
            "at the centre of the bed."]


def _verify(path, entries):
    import xml.etree.ElementTree as ET
    problems = engine.verify(path, list(entries.keys()))
    try:
        import zipfile
        with zipfile.ZipFile(path) as z:
            ET.fromstring(z.read(prusa_format.MODEL_CONFIG_ENTRY))
            header, settings = prusa_format.parse_config(
                z.read(prusa_format.CONFIG_ENTRY).decode("utf-8"))
            if "printer_settings_id" not in settings:
                problems.append("the settings entry names no printer")
    except Exception as e:
        problems.append(f"could not re-read the project: {e}")
    return problems
