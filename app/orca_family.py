"""orca_family.py - the shared backend for OrcaSlicer and its forks.

OrcaSlicer, Anycubic Slicer Next (and Creality Print before them) are
one family: the Bambu Studio project format, the same JSON preset
library layout (<install>\\resources\\profiles\\<Vendor>\\{machine,
process,filament}, inheritance chains), the same command line
(--slice N --export-3mf, --load-settings / --load-filaments
--export-3mf model.stl), and a produced .gcode.3mf whose
Metadata/slice_info.config carries the report - exactly what the
Creality engine driver already reads. What differs per slicer is
small and lives in a Flavour: the program name and install paths,
which vendor folders hold its printers, where its user presets live,
how its files stamp their version, and the words the pages speak.

Facts probed 2026-09-02 (docs\\log.md):

- OrcaSlicer 2.4.2: refuses Bambu Studio 2.8-saved projects outright
  ("File Version 2.8.2.61 not supported by current cli version
  2.4.2"); with --allow-newer-file it then trips on Bambu 2.8 values
  it does not know. So it slices ITS OWN projects. Its files stamp
  Application "BambuStudio-02.06.00.51" (a compatibility string) plus
  a separate <metadata name="OrcaSlicer">2.4.2</metadata>. Installed
  vendors are the folders under %APPDATA%\\OrcaSlicer\\system\\ (BBL,
  Custom, OrcaFilamentLibrary on Edis's machine - no H2C profile in
  2.4.2, the X1C yes); user presets under
  %APPDATA%\\OrcaSlicer\\user\\default\\{scope}\\*.json.
- Anycubic Slicer Next 1.4.1.2: one vendor (Anycubic, 51 Kobra
  machines), no config folder until first launched; files stamp
  Application "BambuStudio-1.4.1.2". Its export leaves the object
  at the origin unless --arrange 1 is given, and even then half
  below the bed - so the builder sets every object down itself.
- Both build a project from presets in about a second and slice the
  Benchy headless in 10-21 s, IF the preset JSONs handed to
  --load-settings are fully resolved (inheritance flattened) and
  carry the header keys the loader checks: type, name, from,
  instantiation, and compatible_printers naming the printer.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import build_job
import edit_bambu_3mf as engine
import geometry
import newjob
import profiles
from errors import ProjectError
from newjob import MODEL_SUFFIXES, unique_target  # noqa: F401
from slicer_project import ProjectSession

# As long as a slice may take (engine_driver.TIMEOUT_S): PrusaSlicer
# needs about five minutes just to import a 23-million-triangle STL
# (Skyward Dragon, 2026-09-25), and the old 300 s stopped it.
EXPORT_TIMEOUT_S = 60 * 60
_NOZZLE_TAIL = re.compile(r"\s+\d+(\.\d+)?\s+nozzle\s*$")


class Flavour:
    """What one Orca-family slicer needs said about itself."""

    def __init__(self, id, display_name, exe_names, candidates,
                 appdata_names, default_vendors, version_marker,
                 verified_series, verified_text, texts):
        self.id = id
        self.display_name = display_name
        self.exe_names = tuple(exe_names)
        self.candidates = list(candidates)
        self.appdata_names = list(appdata_names)   # under %APPDATA%
        self.default_vendors = list(default_vendors)
        # ("metadata", "OrcaSlicer") - a model metadata name holding the
        # version; or ("application", "BambuStudio-1.") - a prefix of
        # the Application string, version after it.
        self.version_marker = version_marker
        self.verified_series = tuple(verified_series)
        self.verified_text = verified_text
        self.texts = texts

    def appdata_root(self):
        """The slicer's own folder under %APPDATA%, or None."""
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        for name in self.appdata_names:
            root = Path(appdata) / name
            if root.is_dir():
                return root
        return None


# ------------------------------------------------------- profiles


class FamilyProfiles:
    """profiles.py's machinery over the vendors this slicer has
    installed, plus its user presets. Same surface as profiles.py."""

    ProfileError = profiles.ProfileError
    META_KEYS = profiles.META_KEYS
    SCOPE_FOLDERS = profiles.SCOPE_FOLDERS

    def __init__(self, flavour):
        self.flavour = flavour

    def vendors(self, slicer_exe):
        """Installed vendors (the folders under the config's system\\
        folder), else the flavour's defaults, else every vendor
        shipped - only those with a profile folder in the install."""
        shipped = Path(slicer_exe).parent / "resources" / "profiles" \
            if slicer_exe else None
        root = self.flavour.appdata_root()
        found = []
        if root and (root / "system").is_dir():
            found = sorted(p.name for p in (root / "system").iterdir()
                           if p.is_dir())
        if not found:
            found = list(self.flavour.default_vendors)
        if shipped and shipped.is_dir():
            found = [v for v in found if (shipped / v).is_dir()] or [
                p.name for p in sorted(shipped.iterdir())
                if p.is_dir() and (p / "machine").is_dir()][:1]
        return found

    def library_root(self, slicer_exe):
        for vendor in self.vendors(slicer_exe):
            root = profiles.library_root(slicer_exe, vendor)
            if root is not None:
                return root
        return None

    def user_library_root(self):
        root = self.flavour.appdata_root()
        if root and (root / "user").is_dir():
            return root / "user"
        return None

    def list_presets(self, slicer_exe, scope):
        names = []
        for vendor in self.vendors(slicer_exe):
            try:
                for name in profiles.list_presets(slicer_exe, scope,
                                                  vendor=vendor):
                    if name not in names:
                        names.append(name)
            except profiles.ProfileError:
                continue
        return names

    def list_user_presets(self, scope):
        return profiles.list_user_presets(scope, root=self.user_library_root())

    def resolve_preset(self, slicer_exe, scope, name):
        last = None
        for vendor in self.vendors(slicer_exe):
            try:
                return profiles.resolve_preset(
                    slicer_exe, scope, name, vendor=vendor,
                    user_root=self.user_library_root())
            except profiles.ProfileError as e:
                last = e
        raise profiles.ProfileError(
            str(last) if last else
            f"No preset library was found for {self.flavour.display_name}.")

    def scope_of(self, slicer_exe, key):
        vendors = self.vendors(slicer_exe)
        return profiles.scope_of(slicer_exe, key,
                                 vendor=vendors[0] if vendors else "BBL")


# --------------------------------------------------------- compat


class FamilyCompat:
    """Version cautions from the flavour's own version stamp."""

    def __init__(self, flavour):
        self.flavour = flavour
        self.VERIFIED_SERIES = flavour.verified_series
        self.VERIFIED_TEXT = flavour.verified_text

    def series(self, version_or_app):
        text = (version_or_app or "").strip()
        kind, marker = self.flavour.version_marker
        if kind == "metadata" and text.startswith(marker + "-"):
            text = text[len(marker) + 1:]
        elif kind == "application" and text.startswith(marker):
            text = text[len("BambuStudio-"):]
        elif kind == "application" or kind == "metadata":
            return ""      # another program's stamp: no claim
        parts = text.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0])}.{int(parts[1])}"
        return ""

    def unverified_note(self, version_or_app, what):
        found = self.series(version_or_app)
        if not found or found in self.VERIFIED_SERIES:
            return None
        return (f"{what} comes from {self.flavour.display_name} version "
                f"{found}, which AccessSlice has not been verified "
                f"against yet (verified: {self.VERIFIED_TEXT}). "
                "Everything is still checked structurally on every "
                "save, but if a value looks wrong, stop and report it.")


# --------------------------------------------------------- newjob

_PRESET_PREFIX = "("


def preset_donor(flavour, printer_name):
    return Path(f"({flavour.display_name} presets for {printer_name})")


def preset_donor_printer(flavour, donor_path):
    name = Path(donor_path).name if donor_path is not None else ""
    prefix = f"({flavour.display_name} presets for "
    if name.startswith(prefix) and name.endswith(")"):
        return name[len(prefix):-1]
    return None


def build_from_presets_command(exe, machine_json, process_json,
                               filament_json, models, out_name):
    """The CLI invocation (split out so tests can stand in a fake)."""
    return [str(exe), "--load-settings", f"{machine_json};{process_json}",
            "--load-filaments", str(filament_json), "--arrange", "1",
            "--export-3mf", out_name] + [str(m) for m in models]


class FamilyNewJob:
    """Project creation: a donor project through the Bambu merge path
    (the family's CLI merges models into a project the way Bambu's
    does), or - with no donor yet - from the printer's own default
    presets, fully resolved and written as temporary preset files."""

    MODEL_SUFFIXES = MODEL_SUFFIXES

    def __init__(self, flavour, family_profiles):
        self.flavour = flavour
        self.profiles = family_profiles

    @staticmethod
    def unique_target(projects_dir, model_path):
        return unique_target(projects_dir, model_path)

    def find_donor(self, candidates, printer_name):
        donor = newjob.find_donor(candidates, printer_name)
        return donor if donor is not None else preset_donor(
            self.flavour, printer_name)

    def create_project(self, exe, donor_path, model_paths, target_path):
        printer = preset_donor_printer(self.flavour, donor_path)
        if printer is None:
            return newjob.create_project(exe, donor_path, model_paths,
                                         target_path)
        return self._create_from_presets(exe, printer, model_paths,
                                         target_path)

    def _default_profiles(self, exe, printer_name):
        machine = self.profiles.resolve_preset(exe, "machine", printer_name)
        process = str(machine.get("default_print_profile") or "").strip()
        filament = machine.get("default_filament_profile") or ""
        if isinstance(filament, list):
            filament = filament[0] if filament else ""
        filament = str(filament).strip().strip('"')
        # Two naming conventions: the fork vendors suffix presets with
        # the full model name ("@Anycubic Kobra S1 0.4 nozzle"), Bambu's
        # own with a short token ("@BBL X1C" for "Bambu Lab X1 Carbon").
        model = _NOZZLE_TAIL.sub("", printer_name).strip()
        tokens = [f"@{model}"]
        if printer_name.startswith("Bambu Lab "):
            rest = printer_name[len("Bambu Lab "):].split()
            if rest:
                tokens.append(f"@BBL {rest[0]}")

        def pick(scope, prefer):
            names = [n for n in self.profiles.list_presets(exe, scope)
                     if any(t in n for t in tokens)]
            for want in prefer:
                for n in names:
                    if want in n:
                        return n
            return names[0] if names else ""

        if not process:
            process = pick("process", ("0.20mm Standard", "0.20mm", "0.2mm"))
        if not filament:
            filament = pick("filament", ("PLA Basic", "Generic PLA", "PLA"))
        if not process or not filament:
            raise ProjectError(
                f"{self.flavour.display_name} names no default print or "
                f"filament profile for {printer_name}, so a project cannot "
                "be built from presets. Save one project from "
                f"{self.flavour.display_name} for this printer first.")
        return machine, process, filament

    def _create_from_presets(self, exe, printer_name, model_paths,
                             target_path):
        target = Path(target_path)
        models = [Path(m) for m in model_paths]
        for m in models:
            if not m.is_file():
                raise ProjectError(f"The model file {m} was not found.")
        try:
            machine, process_name, filament_name = self._default_profiles(
                exe, printer_name)
            process = self.profiles.resolve_preset(exe, "process",
                                                   process_name)
            filament = self.profiles.resolve_preset(exe, "filament",
                                                    filament_name)
        except profiles.ProfileError as e:
            raise ProjectError(str(e))
        staging = Path(tempfile.mkdtemp(prefix="accessslice-newjob-"))
        try:
            files = {}
            for kind, name, values in (("machine", printer_name, machine),
                                       ("process", process_name, process),
                                       ("filament", filament_name, filament)):
                data = dict(values)
                data.pop("inherits", None)
                data["type"] = kind
                data["name"] = name
                data.setdefault("from", "system")
                data.setdefault("instantiation", "true")
                if kind != "machine":
                    data["compatible_printers"] = [printer_name]
                path = staging / f"{kind}.json"
                path.write_text(json.dumps(data, indent=1), encoding="utf-8")
                files[kind] = path
            out_name = "built.3mf"
            cmd = build_from_presets_command(
                exe, files["machine"], files["process"], files["filament"],
                models, out_name)
            build_job.stage(
                f"{self.flavour.display_name} is reading the model and "
                "building the project.")
            try:
                proc = build_job.run(
                    cmd, cwd=str(staging), capture_output=True, text=True,
                    timeout=EXPORT_TIMEOUT_S, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise ProjectError(
                    f"{self.flavour.display_name}'s engine was still "
                    "building the project after "
                    f"{EXPORT_TIMEOUT_S // 60} minutes and was stopped. "
                    "A very detailed model can be too big to build; a "
                    "model with fewer triangles builds faster.")
            except (OSError, subprocess.SubprocessError) as e:
                raise ProjectError(
                    f"{self.flavour.display_name}'s engine could not be "
                    f"run to build the project ({e}). Check the slicer "
                    "path in Settings.")
            built = staging / out_name
            if proc.returncode != 0 or not built.is_file():
                lines = [ln.strip() for ln in (proc.stdout or "").splitlines()
                         if ln.strip() and "found error" not in ln]
                reason = lines[-1] if lines else f"exit code {proc.returncode}"
                raise ProjectError(
                    f"{self.flavour.display_name}'s engine did not build "
                    f"the project: {reason}")
            build_job.stage("Setting the model down on the plate and "
                            "checking the project.")
            entries = engine.load_entries(built)
            entries[engine.MODEL_ENTRY] = set_objects_down(
                entries[engine.MODEL_ENTRY].decode("utf-8"), entries
            ).encode("utf-8")
            engine.save_entries(target, entries)
        finally:
            for p in staging.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                staging.rmdir()
            except OSError:
                pass
        problems = engine.verify(target, list(entries.keys()))
        if not problems:
            try:
                ProjectSession(target)
            except ProjectError as e:
                problems.append(str(e))
        if problems:
            target.unlink(missing_ok=True)
            raise ProjectError(
                "The built project failed its check and was not kept: "
                + "; ".join(problems))
        names = ", ".join(m.name for m in models)
        return [f"Created {target.name} from {names} on "
                f"{self.flavour.display_name}'s own presets for "
                f"{printer_name} ({process_name}, {filament_name}), "
                "arranged on the plate."]


def set_objects_down(model_xml, entries):
    """Every build item's transform adjusted so its mesh rests on the
    plate (z of the lowest point = 0). Items whose mesh cannot be read
    are left alone."""
    def fix(m):
        objectid = m.group("objectid")
        nums = [float(v) for v in m.group("transform").split()]
        if len(nums) != 12:
            return m.group(0)
        shape = geometry.shape_of_object(model_xml, entries, objectid)
        if not shape:
            return m.group(0)
        low = min(geometry.transform_point(nums, v)[2] for v in shape[0])
        if abs(low) < 0.01:
            return m.group(0)
        nums[11] -= low
        text = " ".join(engine.fmt_num(v) for v in nums)
        return (m.group(0)[:m.start("transform") - m.start()]
                + text + m.group(0)[m.end("transform") - m.start():])
    return engine.ITEM_RE.sub(fix, model_xml)
