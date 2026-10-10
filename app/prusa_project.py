"""prusa_project.py - a live editing session on a PrusaSlicer project.

PrusaProjectSession is ProjectSession with the FORMAT swapped out:
the settings entry is INI-style text (prusa_format), per-object data
lives in Slic3r_PE_model.config, there is exactly one build plate
and no filament-to-nozzle map. Everything else - staging, the review
tab, the save pipeline with backup and restore, the stale-file guard,
transforms, auto-orient, auto-arrange, per-object overrides, presets
- is the proven base class, unchanged. What this file overrides is
exactly what the base class reads or writes in Bambu's own shape.

Per-extruder settings are lists here too (prusa_format splits them),
so the Filaments tab and the per-slot editors work unchanged; a Core
One project simply has one slot everywhere.
"""

import shutil
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import edit_bambu_3mf as engine
import prusa_catalog
import prusa_compat
import prusa_format
import prusa_profiles
from errors import ProjectError
from slicer_project import ProjectSession


class PrusaProjectSession(ProjectSession):

    # The keys the 100%-infill question works on (base: Bambu's).
    PATTERN_KEY = "fill_pattern"
    DENSITY_KEY = "fill_density"
    SOLID_PATTERN = "rectilinear"
    # Infill patterns PrusaSlicer prints correctly at 100% density; its
    # GUI pops the same "switch to rectilinear?" question for the rest.
    SOLID_OK_PATTERNS = {"rectilinear", "alignedrectilinear", "concentric",
                         "monotonic", "monotoniclines", "line"}

    _OBJECT_META_NOT_SETTINGS = {"name", "extruder"}
    supports_plates = False   # one bed

    def __init__(self, path, slicer_exe=None):
        super().__init__(path, slicer_exe=slicer_exe,
                         profiles_mod=prusa_profiles,
                         compat_mod=prusa_compat,
                         catalog_mod=prusa_catalog)

    # ---------------------------------------------------------- loading

    def _load(self):
        try:
            self.entries = engine.load_entries(self.path)
        except Exception as e:
            raise ProjectError(
                f"Could not read the file as a 3MF archive: {e}")
        self._shape_cache = {}
        self._preflight_cache = {}
        for entry in (engine.MODEL_ENTRY, prusa_format.CONFIG_ENTRY,
                      prusa_format.MODEL_CONFIG_ENTRY):
            if entry not in self.entries:
                raise ProjectError(
                    f"This file is missing {entry}, so it is not a saved "
                    "PrusaSlicer project (a plain 3MF model has no "
                    "settings inside). Open the model in PrusaSlicer, "
                    "save the project once (Ctrl+S), and try again - or "
                    "start from the model file on the Start page.")
        try:
            self.model_xml = self.entries[engine.MODEL_ENTRY].decode("utf-8")
            self.ms_xml = self.entries[
                prusa_format.MODEL_CONFIG_ENTRY].decode("utf-8")
            config_text = self.entries[
                prusa_format.CONFIG_ENTRY].decode("utf-8")
        except UnicodeDecodeError as e:
            raise ProjectError(
                "A text entry inside this project is damaged (not valid "
                f"UTF-8: {e}). Open the model in PrusaSlicer and save the "
                "project again to get a clean file.")
        self.config_header, self.settings = prusa_format.parse_config(
            config_text)
        if not self.settings:
            raise ProjectError(
                "The settings entry of this project holds no settings, "
                "so this is not a normal PrusaSlicer project. Save the "
                "project from PrusaSlicer once and try again.")
        try:
            st = self.path.stat()
            self._loaded_stat = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._loaded_stat = None
        self._structure_check()

    def _structure_check(self):
        notes = []
        self.application = (prusa_format.generator_line(self.config_header)
                            or "(unknown application)")
        caution = self._compat.unverified_note(self.application,
                                               "This project")
        if caution:
            notes.append("Project note: " + caution)
        if (self.slicer_exe
                and self._profiles.library_root(self.slicer_exe) is None):
            notes.append(
                "Project note: PrusaSlicer's preset library was not "
                "found, so preset switching is unavailable. Run "
                "PrusaSlicer's configuration wizard once, or check the "
                "slicer path in Settings.")
        items = list(engine.find_items(self.model_xml))
        malformed = sum(1 for _id, nums in items if len(nums) < 12)
        if malformed:
            notes.append(
                f"Project note: {malformed} object position(s) could not "
                "be read and will not appear on the Placement tab.")
        if not items:
            notes.append(
                "Project note: no printable objects were found in this "
                "project - the Placement and per-object controls will "
                "be empty.")
        multi = [e["name"] for e in prusa_format.object_entries(
            self.ms_xml).values() if e["instances"] > 1]
        if multi:
            notes.append(
                "Project note: " + ", ".join(multi)
                + (" is" if len(multi) == 1 else " are")
                + " printed as several copies (instances); AccessSlice "
                "reads and moves the first copy only.")
        if not self.settings.get("printer_settings_id"):
            notes.append(
                "Project note: the project does not name its printer, "
                "so preset lists cannot be filtered to your machine.")
        self.structure_notes = notes

    # ---------------------------------------------------------- objects

    def objects(self):
        """All printable objects: id, name, centre, extruder. With
        several instances of one object, the first item counts."""
        meta = prusa_format.object_entries(self.ms_xml)
        result, seen = [], set()
        for objectid, nums in engine.find_items(self.model_xml):
            if len(nums) < 12 or objectid in seen:
                continue
            seen.add(objectid)
            entry = meta.get(objectid, {})
            result.append({
                "id": objectid,
                "name": entry.get("name") or f"object {objectid}",
                "x": nums[9], "y": nums[10], "z": nums[11],
                "extruder": entry.get("overrides", {}).get("extruder"),
            })
        return result

    def object_overrides(self, objectid):
        entry = prusa_format.object_entries(self.ms_xml).get(objectid)
        if not entry:
            return {}
        return {k: v for k, v in entry["overrides"].items()
                if k not in self._OBJECT_META_NOT_SETTINGS}

    # ----------------------------------------------------------- plates

    def plates(self):
        """PrusaSlicer has one bed; every object lives on it."""
        return [{
            "id": "1", "name": "Bed", "locked": False,
            "object_ids": [o["id"] for o in self.objects()],
            "print_sequence": None, "spiral_mode": None,
        }]

    def plate_grid(self):
        return {"stride": 0.0, "cols": 1, "reliable": True}

    def plate_origin(self, plate_id):
        return (0.0, 0.0)

    def stage_replate(self, objectid, target_plate_id):
        raise ProjectError(
            "A PrusaSlicer project has a single bed, so there is no "
            "other plate to move to.")

    def planned_layout(self):
        return {"1": (0.0, 0.0)}

    def _build_import_source(self, model_path, exe):
        import prusa_newjob
        target = self._staging_dir() / (
            f"import-{Path(model_path).stem}-{len(self.pending)}.3mf")
        prusa_newjob.create_project(exe, self.path, [model_path], target)
        return target

    def _apply_new_plates(self, ms_xml):
        return ms_xml, {}

    def _apply_plate_shifts(self, model_xml):
        return model_xml

    def _apply_object_ops(self, entries, model_xml, ms_xml, slot_to_id):
        """Copies, removals and imports for the inline-mesh format."""
        import object_ops
        t = {"entries": entries, "model": model_xml, "ms": ms_xml}
        names = {o["id"]: o["name"] for o in self.objects()}
        for c in self.pending:
            if c["kind"] == "copy":
                copy_names = c.get("names") or [
                    f"{names.get(c['objectid'], 'object')} (copy {k})"
                    for k in range(1, c["count"] + 1)]
                t, new_ids = object_ops.copy_prusa(
                    t, t, c["objectid"], c["count"],
                    lambda k, ns=copy_names: ns[k - 1])
                ox, oy = c["offset"]
                for oid, (_key, cx, cy) in zip(new_ids, c["placements"]):
                    t["model"] = object_ops.set_item_translation(
                        t["model"], oid, cx - ox, cy - oy)
            elif c["kind"] == "remove":
                t = object_ops.remove_prusa(t, c["objectid"])
            elif c["kind"] == "add_model":
                src_session = self._open_source(Path(c["source"]))
                src = {"entries": src_session.entries,
                       "model": src_session.model_xml,
                       "ms": src_session.ms_xml}
                for sid, name, off, (_key, cx, cy) in zip(
                        c["source_ids"], c["names"], c["offsets"],
                        c["placements"]):
                    t, new_ids = object_ops.copy_prusa(
                        t, src, sid, 1, lambda k, n=name: n)
                    t["model"] = object_ops.set_item_translation(
                        t["model"], new_ids[0], cx - off[0], cy - off[1])
        return t["entries"], t["model"], t["ms"]

    def filament_map(self, plate_id=None):
        return None, []

    def stage_filament_map(self, new_map, plate_id=None):
        raise ProjectError(
            "PrusaSlicer projects have no filament-to-nozzle map.")

    def plate_size(self):
        return prusa_format.bed_size(self.settings)

    def _front_left_keepout(self):
        return None

    def filament_count(self):
        for key in ("filament_settings_id", "filament_type",
                    "nozzle_diameter", "temperature"):
            value = self.settings.get(key)
            if isinstance(value, list) and value:
                return len(value)
        return 1

    def filament_names(self):
        ids = self.effective_setting("filament_settings_id") or []
        types = self.effective_setting("filament_type") or []
        count = max(len(ids), len(types), 1)
        names = []
        for i in range(count):
            label = ids[i] if i < len(ids) else ""
            ftype = types[i] if i < len(types) else ""
            names.append(label or ftype or f"Filament {i + 1}")
        return names

    def summary(self):
        lines = []
        objs = self.objects()
        plural = "s" if len(objs) != 1 else ""
        lines.append(f"{len(objs)} object{plural}: "
                     + ", ".join(o["name"] for o in objs))
        printer = self.settings.get("printer_settings_id")
        staged = self.staged_preset("machine")
        if staged and staged != printer:
            lines.append(f"Printer: {staged} (staged, not saved)")
        elif printer:
            lines.append(f"Printer: {printer}")
        profile = self.settings.get("print_settings_id")
        staged = self.staged_preset("process")
        if staged and staged != profile:
            lines.append(f"Print profile: {staged} (staged, not saved)")
        elif profile:
            lines.append(f"Print profile: {profile}")
        size = self.plate_size()
        if size:
            lines.append(f"Bed: {size[0]:g} by {size[1]:g} mm")
        return lines

    # ------------------------------------------------------- questions

    def infill_conflict(self):
        density = str(self.effective_setting(self.DENSITY_KEY) or "")
        pattern = str(self.effective_setting(self.PATTERN_KEY) or "")
        try:
            dense = float(density.rstrip("% "))
        except ValueError:
            return None
        if dense >= 100 and pattern and pattern not in self.SOLID_OK_PATTERNS:
            return {"pattern": pattern, "density": density}
        return None

    def default_infill_density(self):
        name = (self.staged_preset("process")
                or self.settings.get("print_settings_id"))
        if name and self.slicer_exe:
            try:
                resolved = self._profiles.resolve_preset(
                    self.slicer_exe, "process", name)
                value = resolved.get(self.DENSITY_KEY)
                if value:
                    return str(value)
            except self._profiles.ProfileError:
                pass
        return "15%"

    # ---------------------------------------------------------- saving

    def _apply_object_setting(self, ms_xml, change):
        try:
            return prusa_format.set_object_metadata(
                ms_xml, change["objectid"], change["key"], change["new"])
        except KeyError:
            raise ProjectError(
                f"Object {change['objectid']} has no settings block in "
                "this project - save it from PrusaSlicer once and try "
                "again.")

    def save(self):
        """The base class's safe save, writing PrusaSlicer's entries:
        the INI-style settings text and the model config XML. No
        changed-settings bookkeeping exists in this format - the
        values in the file are the truth."""
        if not self.pending:
            return ["No changes to save."]
        self._ensure_fresh("saving")

        model_xml = self.model_xml
        ms_xml = self.ms_xml
        settings = {k: (list(v) if isinstance(v, list) else v)
                    for k, v in self.settings.items()}
        n = self.filament_count()
        diff = [set() for _ in range(n + 2)]   # unused bookkeeping shape

        for change in self.pending:
            if change["kind"] == "setting":
                settings[change["key"]] = change["new"]
            elif change["kind"] == "preset":
                self._apply_preset(settings, diff, n, change)
            elif change["kind"] in ("move", "rotate", "scale", "orient"):
                model_xml = self._apply_transform_change(model_xml, change)
            elif change["kind"] == "extruder":
                ms_xml = self._apply_extruder(ms_xml, change)
            elif change["kind"] == "object_setting":
                ms_xml = self._apply_object_setting(ms_xml, change)
            elif change["kind"] in ("copy", "remove", "add_model"):
                continue  # applied together below
            else:
                raise ProjectError(
                    f"A staged change of kind {change['kind']} cannot be "
                    "written into a PrusaSlicer project.")

        entries, model_xml, ms_xml = self._apply_object_ops(
            dict(self.entries), model_xml, ms_xml, {})
        entries[engine.MODEL_ENTRY] = model_xml.encode("utf-8")
        entries[prusa_format.MODEL_CONFIG_ENTRY] = ms_xml.encode("utf-8")
        entries[prusa_format.CONFIG_ENTRY] = prusa_format.serialise_config(
            self.config_header, settings).encode("utf-8")

        backup = self._write_backup("save")
        try:
            engine.save_entries(self.path, entries)
        except OSError as e:
            try:
                shutil.copy2(backup, self.path)
                restored = ("The original file was restored from the "
                            "backup, so nothing is lost.")
            except OSError:
                restored = (f"Restoring the backup also failed - the "
                            f"last good version is still in the "
                            f"backups folder as {backup.name}.")
            raise ProjectError(
                f"The project file could not be written ({e}). If it is "
                "open in PrusaSlicer, close it there and save again. "
                + restored)
        problems = self._verify(list(entries.keys()), len(settings))
        if problems:
            shutil.copy2(backup, self.path)
            raise ProjectError(
                "Verification failed, so the original file was restored "
                "untouched. Problems: " + "; ".join(problems))

        count = len(self.pending)
        for change in self.pending:
            self._drop_import_source(change)
        self.pending = []
        self._load()
        plural = "s" if count != 1 else ""
        return [
            f"Saved and verified: {count} change{plural} written to "
            f"{self.path.name}.",
            f"The previous version is kept in the backups folder as "
            f"{backup.name} - see the Backups tab.",
        ]

    def _verify(self, expected_names, expected_keys):
        """Structural check of the written file: entries unchanged,
        both XML entries well-formed, the settings text readable with
        every key still present."""
        problems = engine.verify(self.path, expected_names)
        try:
            with zipfile.ZipFile(self.path) as z:
                try:
                    ET.fromstring(z.read(prusa_format.MODEL_CONFIG_ENTRY))
                except ET.ParseError as e:
                    problems.append(
                        f"{prusa_format.MODEL_CONFIG_ENTRY} is not "
                        f"well-formed XML: {e}")
                _header, settings = prusa_format.parse_config(
                    z.read(prusa_format.CONFIG_ENTRY).decode("utf-8"))
                if len(settings) != expected_keys:
                    problems.append(
                        f"{prusa_format.CONFIG_ENTRY} reads back with "
                        f"{len(settings)} settings instead of "
                        f"{expected_keys}")
        except Exception as e:
            problems.append(f"could not re-read the project: {e}")
        return problems

