"""slicer_project.py - the open project and its pending (staged) changes.

This module wraps the proven text-editing engine (tools/edit_bambu_3mf.py)
in a ProjectSession class the web interface can talk to:

- load a Bambu project 3MF and expose its facts in plain data
  (objects with positions, filament map, every slicer setting),
- STAGE edits without touching the file (so the user can review
  everything in one place before anything is written), and
- SAVE: apply all staged edits, back the file up first, verify the
  result structurally, and restore the backup if verification fails.

The engine module is imported, not shelled out to, so both the command
line tool and AccessSlice share one implementation of the risky parts
(zip handling with forward slashes, structural verification).
"""

import datetime
import json
import math
import re
import shutil
import sys
from pathlib import Path

# The engine lives in the repository's tools folder, one level up.
_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import compat  # noqa: E402
import settings_catalog  # noqa: E402
import edit_bambu_3mf as engine  # noqa: E402  (path set up just above)
import geometry  # noqa: E402
import object_ops  # noqa: E402
import profiles  # noqa: E402

# The friendly-error class lives in the slicer-neutral errors module;
# re-exported here because this is where everyone historically got it.
from errors import ProjectError  # noqa: E402,F401


# ------------------------------------------------------------------ session


class ProjectSession:
    # The Bambu family has numbered plates that can be added to;
    # PrusaSlicer has one bed (its session says False).
    supports_plates = True
    MAX_COPIES = 20
    """One open project 3MF plus its staged-but-unsaved changes.

    Staged changes live in self.pending, a list of dicts. Each has:
      kind   - "setting" | "preset" | "move" | "rotate" | "scale" |
               "orient" | "replate" | "extruder" | "object_setting" |
               "filament_map"
      label  - plain-language description for the review screen
      old / new - values, for showing "from -> to"
      plus kind-specific fields (key, objectid, values...).
    """

    # The keys the 100%-infill question works on, and the pattern
    # that is safe at 100% (Bambu Studio's GUI calls it Rectilinear).
    PATTERN_KEY = "sparse_infill_pattern"
    DENSITY_KEY = "sparse_infill_density"
    SOLID_PATTERN = "zig-zag"

    def __init__(self, path, slicer_exe=None, profiles_mod=None,
                 compat_mod=None, catalog_mod=None):
        self.path = Path(path)
        if not self.path.is_file():
            raise ProjectError(f"File not found: {self.path}")
        # Needed to classify setting keys by scope (process / filament /
        # machine) and to read the slicer's preset library.
        self.slicer_exe = slicer_exe
        # Which slicer's profile library and version knowledge apply.
        # Defaults are Bambu's; an Orca-family backend passes its own
        # modules with the same surfaces (backends reuse this session).
        self._profiles = profiles_mod or profiles
        self._compat = compat_mod or compat
        # The settings catalog (labels, tabs, control types) the
        # pages render this project with - Bambu's unless a backend
        # with its own key names (PrusaSlicer) passes another.
        self.catalog = catalog_mod or settings_catalog
        self.pending = []
        # The editor page whose form staged the current request's
        # changes ("/project/filaments", ...). The server sets it per
        # POST; each staged change records it so a later Discard on the
        # Review tab can send the user back where the change came from.
        self.stage_source = None
        self._load()

    def scope_of(self, key):
        """'process' | 'filament' | 'machine' for a settings key,
        answered by this session's own slicer's profile library."""
        return self._profiles.scope_of(self.slicer_exe, key)

    def _load(self):
        """Read the archive and parse the three text entries we edit."""
        try:
            self.entries = engine.load_entries(self.path)
        except Exception as e:
            raise ProjectError(f"Could not read the file as a 3MF archive: {e}")
        # Parsed meshes, per object id (see object_shape). Reset on
        # every (re)load so a reloaded file is re-read honestly.
        self._shape_cache = {}
        # Pre-flight overhang reports per (object, placement,
        # threshold) - see preflight.py. Reset with the meshes.
        self._preflight_cache = {}

        for entry in (engine.MODEL_ENTRY, engine.MODEL_SETTINGS_ENTRY,
                      engine.PROJECT_SETTINGS_ENTRY):
            if entry not in self.entries:
                raise ProjectError(
                    "This file is missing "
                    f"{entry}, so it is not a saved Bambu Studio project. "
                    "Open the model in Bambu Studio, save the project once "
                    "(Ctrl+Shift+S), and try again."
                )

        try:
            self.model_xml = self.entries[engine.MODEL_ENTRY].decode("utf-8")
            self.ms_xml = self.entries[
                engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
        except UnicodeDecodeError as e:
            raise ProjectError(
                "A text entry inside this project is damaged (not valid "
                f"UTF-8: {e}). Open the model in Bambu Studio and save "
                "the project again to get a clean file."
            )
        try:
            self.settings = json.loads(
                self.entries[engine.PROJECT_SETTINGS_ENTRY]
            )
        except ValueError as e:
            raise ProjectError(f"project_settings.config is not valid JSON: {e}")
        if not isinstance(self.settings, dict):
            raise ProjectError(
                "project_settings.config does not hold a settings object, "
                "so this is not a normal Bambu Studio project. Save the "
                "project from Bambu Studio once and try again."
            )
        # Remember what was read, so is_stale() can tell when something
        # else (usually Bambu Studio) rewrites the file behind our back.
        try:
            st = self.path.stat()
            self._loaded_stat = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._loaded_stat = None
        self._structure_check()

    def _structure_check(self):
        """Sanity-check the file's shape ONCE at load time.

        AccessSlice was built against projects saved by Bambu Studio 2.8
        for the H2C and X1C. A project outside those assumptions must
        say so in plain words up front - never crash on some tab later,
        and never be misread silently. The findings land in
        self.structure_notes (shown on every editor page and spoken
        when the project opens); self.application records which program
        wrote the file, for the diagnostics log.
        """
        notes = []
        m = re.search(r'<metadata name="Application">([^<]*)</metadata>',
                      self.model_xml)
        self.application = m.group(1) if m else "(unknown application)"
        # OrcaSlicer stamps a Bambu-compatible Application string and
        # its own version in a separate metadata line; the latter is
        # the truth about which program wrote the file.
        orca = re.search(r'<metadata name="OrcaSlicer">([^<]*)</metadata>',
                         self.model_xml)
        if orca and orca.group(1).strip():
            self.application = f"OrcaSlicer-{orca.group(1).strip()}"

        # An unverified (usually: freshly updated) Studio version is a
        # caution, never a refusal - actual breakage fails loudly via
        # the structural checks regardless of what the version claims.
        caution = self._compat.unverified_note(self.application,
                                               "This project")
        if caution:
            notes.append("Project note: " + caution)

        # The preset library moving or vanishing is how a slicer update
        # (or a wrong slicer path) most often shows itself.
        if (self.slicer_exe
                and self._profiles.library_root(self.slicer_exe) is None):
            notes.append(
                "Project note: the slicer's preset library was not found "
                "next to the slicer program, so preset switching is "
                "unavailable. Check the slicer path in Settings - a "
                "slicer update may have moved things.")

        nplates = len(self.plates())
        if nplates > 1 and not self.plate_grid()["reliable"]:
            notes.append(
                f"Project note: this project has {nplates} build "
                "plates, but their layout could not be read reliably - "
                "positions are shown in raw coordinates and moving "
                "objects between plates is disabled for this project.")

        items = list(engine.find_items(self.model_xml))
        malformed = sum(1 for _id, nums in items if len(nums) < 12)
        if malformed:
            notes.append(
                f"Project note: {malformed} object position(s) could not "
                "be read and will not appear on the Placement tab.")
        if not items:
            notes.append(
                "Project note: no printable objects were found in this "
                "project - the Placement and per-object filament "
                "controls will be empty. Import a model in Bambu Studio "
                "and save again if that is unexpected.")

        printer = self.settings.get("printer_settings_id")
        if not printer:
            notes.append(
                "Project note: the project does not name its printer, "
                "so preset lists cannot be filtered to your machine.")

        # Dual-nozzle machines need the filament map metadata; a single-
        # nozzle project legitimately has none, so only they warn.
        if (printer and any(t in printer for t in ("H2C", "H2D"))
                and not self.filament_map()[1]):
            notes.append(
                "Project note: this dual-nozzle project has no "
                "filament-to-nozzle map yet. Saving a mapping from the "
                "Filaments tab will create it.")

        if not isinstance(self.settings.get("different_settings_to_system"),
                          list):
            notes.append(
                "Project note: the file's changed-settings bookkeeping "
                "is missing; AccessSlice will rebuild it on the first "
                "save.")

        # The Filaments tab writes whole per-filament blocks; a list
        # whose length does not divide among the filaments would be
        # skipped silently there, so say it here instead.
        n = self.filament_count()
        odd = [key for key, *_rest in self.catalog.FILAMENTS
               if isinstance(self.settings.get(key), list) and n
               and len(self.settings[key]) % n]
        if odd:
            notes.append(
                "Project note: these per-filament settings have an "
                "unexpected number of values and can only be edited "
                "slot by slot on their own pages: " + ", ".join(odd) + ".")

        self.structure_notes = notes

    # ------------------------------------------------------------ questions

    def objects(self):
        """All printable objects: id, name, centre position, filament."""
        names = engine.object_names(self.ms_xml)
        # The extruder line's position in the object block is not fixed:
        # per-object overrides are inserted between name and extruder by
        # Studio (alphabetical), so parse each block order-independently.
        extruders = {}
        for m in re.finditer(r'<object id="(\d+)">(.*?)</object>',
                             self.ms_xml, re.S):
            head = m.group(2).split("<part", 1)[0]
            e = re.search(r'<metadata key="extruder" value="(\d+)"/>',
                          head)
            if e:
                extruders[m.group(1)] = e.group(1)
        result = []
        for objectid, nums in engine.find_items(self.model_xml):
            if len(nums) < 12:
                continue  # unreadable transform; announced at load time
            result.append({
                "id": objectid,
                "name": names.get(objectid, "(unnamed object)"),
                "x": nums[9], "y": nums[10], "z": nums[11],
                "extruder": extruders.get(objectid),
            })
        return result

    # Object-block metadata that is structure, not setting overrides.
    _OBJECT_META_NOT_SETTINGS = {"name", "extruder"}

    def _object_block_head(self, objectid, xml=None):
        """(regex match of the whole block, its object-level metadata
        region) - the region before the first <part>, which is where
        per-object setting overrides live (verified against a real
        Studio-saved specimen, 2026-08-27)."""
        xml = self.ms_xml if xml is None else xml
        m = re.search(rf'<object id="{objectid}">(.*?)</object>', xml, re.S)
        if not m:
            return None, ""
        return m, m.group(1).split("<part", 1)[0]

    def object_overrides(self, objectid):
        """{key: value} of this object's SAVED per-object overrides.

        Bambu Studio stores them as plain metadata lines in the
        object's model_settings.config block - the same block that
        holds name and extruder. An override replaces the project-wide
        value for that object only.
        """
        _m, head = self._object_block_head(objectid)
        pairs = re.findall(r'<metadata key="([^"]+)" value="([^"]*)"/>',
                           head)
        return {k: v for k, v in pairs
                if k not in self._OBJECT_META_NOT_SETTINGS}

    def effective_object_overrides(self, objectid):
        """Saved overrides with staged object edits applied on top."""
        result = self.object_overrides(objectid)
        for c in self.pending:
            if (c["kind"] == "object_setting"
                    and c["objectid"] == objectid):
                if c["new"] is None:
                    result.pop(c["key"], None)
                else:
                    result[c["key"]] = c["new"]
        return result

    def stage_object_setting(self, objectid, key, new_value):
        """Stage one per-object override; new_value None removes it.

        Only keys that exist in the project settings can be overridden
        (AccessSlice never invents keys), and per-filament list keys
        cannot be object overrides - Studio's own object panel offers
        scalars only.
        """
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        name = objs[objectid]["name"]
        current = self.object_overrides(objectid)

        def drop_staged():
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "object_setting"
                                    and c["objectid"] == objectid
                                    and c["key"] == key)]

        if new_value is None:
            if key in current:
                change = {"kind": "object_setting", "objectid": objectid,
                          "key": key, "old": current[key], "new": None,
                          "label": f"Remove the "
                          f"{self.catalog.label_for(key)} override "
                                   f"from {name}"}
            elif key in self.effective_object_overrides(objectid):
                # Only staged, never saved: dropping the staged change
                # IS the removal.
                drop_staged()
                return True
            else:
                drop_staged()
                return False
        else:
            new_value = str(new_value)
            if key not in self.settings and key not in current:
                raise ProjectError(
                    f"No setting named {key} exists in this project, so "
                    "it cannot be overridden per object.")
            if isinstance(self.settings.get(key), list):
                raise ProjectError(
                    f"{key} is a per-filament list setting and cannot "
                    "be overridden per object.")
            old = current.get(key)
            if new_value == old:
                drop_staged()
                return False
            shown_old = old if old is not None else (
                f"project value {self.settings.get(key)}")
            change = {"kind": "object_setting", "objectid": objectid,
                      "key": key, "old": shown_old, "new": new_value,
                      "label": f"{self.catalog.label_for(key)} "
                      f"for {name} only"}
        self._replace_pending(
            lambda c: (c["kind"] == "object_setting"
                       and c["objectid"] == objectid
                       and c["key"] == key),
            change)
        return True

    # -------------------------------------------------------------- plates

    def plates(self):
        """The project's build plates, in file order: id, name, locked,
        and the object ids that live on each (from the model_instance
        blocks - the authoritative membership record)."""
        result = []
        for m in re.finditer(r'<plate>(.*?)</plate>', self.ms_xml, re.S):
            block = m.group(1)
            pid = re.search(r'key="plater_id" value="([^"]*)"', block)
            name = re.search(r'key="plater_name" value="([^"]*)"', block)
            locked = re.search(r'key="locked" value="([^"]*)"', block)
            ids = re.findall(r'key="object_id" value="(\d+)"', block)
            # Bambu Studio's per-plate settings (its plate dialog: print
            # sequence, spiral mode, bed type...) are written as plain
            # metadata lines in the plate block, by the same code path
            # as the bookkeeping keys above. Key names follow Studio's
            # source; no saved specimen with them exists on this machine
            # yet (2026-09-02), so absent keys simply read as None.
            seq = re.search(r'key="print_sequence" value="([^"]*)"', block)
            spiral = re.search(r'key="spiral_mode" value="([^"]*)"', block)
            result.append({
                "id": pid.group(1) if pid else "",
                "name": name.group(1) if name else "",
                "locked": (locked.group(1) == "true") if locked else False,
                "object_ids": ids,
                "print_sequence": seq.group(1) if seq else None,
                "spiral_mode": spiral.group(1) if spiral else None,
            })
        return result

    def plate_of_object(self, objectid):
        """The plate id an object lives on, or None."""
        for plate in self.plates():
            if objectid in plate["object_ids"]:
                return plate["id"]
        return None

    def plate_grid(self):
        """How the plates tile in world coordinates.

        Bambu Studio lays plates in a grid: stride = plate width x 1.25
        (verified: 320 for 256 mm plates in two real multi-plate
        projects, 2026-08-27), rows going toward negative Y, with
        ceil(sqrt(plate count)) columns. The column count is validated
        against the objects' actual positions; when the best fit still
        leaves most objects outside their plate, the grid is marked
        unreliable and everything that depends on plate origins says
        so instead of guessing.

        Returns {"stride", "cols", "reliable"}.
        """
        size = self.plate_size()
        plates = self.plates()
        n = max(len(plates), 1)
        if not size:
            return {"stride": 0.0, "cols": 1, "reliable": n == 1}
        stride = size[0] * 1.25
        if n == 1:
            return {"stride": stride, "cols": 1, "reliable": True}

        world = {o["id"]: (o["x"], o["y"]) for o in self.objects()}
        index_of = {p["id"]: i for i, p in enumerate(plates)}

        def fit(cols):
            good = total = 0
            for plate in plates:
                i = index_of[plate["id"]]
                ox, oy = (i % cols) * stride, -(i // cols) * stride
                for oid in plate["object_ids"]:
                    if oid not in world:
                        continue
                    total += 1
                    lx, ly = world[oid][0] - ox, world[oid][1] - oy
                    if (-30 <= lx <= size[0] + 30
                            and -30 <= ly <= size[1] + 30):
                        good += 1
            return good, total

        # Studio's own layout is ceil(sqrt(n)) columns; that stays the
        # answer unless the objects' positions prove another count
        # STRICTLY better (plates without objects cannot distinguish
        # layouts, so ties must never override the known rule).
        best_cols = max(1, math.ceil(math.sqrt(n)))
        best_good, total = fit(best_cols)
        for cols in range(1, 13):
            if cols == best_cols:
                continue
            good, _total = fit(cols)
            if good > best_good:
                best_cols, best_good = cols, good
        reliable = total == 0 or best_good >= total * 0.6
        return {"stride": stride, "cols": best_cols,
                "reliable": reliable}

    def plate_origin(self, plate_id):
        """The world position of a plate's front-left corner, or None
        when the grid is unreliable."""
        grid = self.plate_grid()
        if not grid["reliable"] or not grid["stride"]:
            return None
        for i, plate in enumerate(self.plates()):
            if plate["id"] == plate_id:
                return ((i % grid["cols"]) * grid["stride"],
                        -(i // grid["cols"]) * grid["stride"])
        return None

    def stage_replate(self, objectid, target_plate_id):
        """Stage moving an object to another plate, centred on it.

        Membership (the model_instance block) and the world position
        are both rewritten at save time, matching how Studio lays the
        grid out. Refused when the grid is unreliable - better honest
        than an object teleported somewhere wrong.
        """
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        plate_ids = [p["id"] for p in self.plates()]
        if target_plate_id not in plate_ids:
            raise ProjectError(f"No plate {target_plate_id} in this "
                               "project.")
        current = self.plate_of_object(objectid)
        if current is None:
            raise ProjectError(
                f"{objs[objectid]['name']} has no plate membership entry "
                "in the file - save the project from Bambu Studio once "
                "and try again.")
        if current == target_plate_id:
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "replate"
                                    and c["objectid"] == objectid)]
            return False
        if self.plate_origin(target_plate_id) is None:
            raise ProjectError(
                "The plate layout of this project could not be read "
                "reliably, so moving between plates is disabled for it.")
        self._replace_pending(
            lambda c: c["kind"] == "replate" and c["objectid"] == objectid,
            {"kind": "replate", "objectid": objectid,
             "old": f"plate {current or 'unknown'}",
             "new": f"plate {target_plate_id}",
             "target": target_plate_id,
             "label": f"Plate of {objs[objectid]['name']}"})
        return True

    def filament_map(self, plate_id=None):
        """(mode, [map values]) for one plate (default: the first).

        Each plate carries its own filament-to-nozzle map metadata (or
        none at all - older Studio versions and single-nozzle projects
        simply lack the keys).
        """
        block = self._plate_block(plate_id)
        if block is None:
            return None, []
        mode = re.search(r'key="filament_map_mode" value="([^"]*)"', block)
        fmap = re.search(r'key="filament_maps" value="([^"]*)"', block)
        if mode and fmap:
            return mode.group(1), fmap.group(1).split()
        return None, []

    def _plate_block(self, plate_id=None, xml=None):
        """One plate's XML block content, or None. Default: first plate."""
        xml = self.ms_xml if xml is None else xml
        for m in re.finditer(r'<plate>(.*?)</plate>', xml, re.S):
            if plate_id is None:
                return m.group(1)
            pid = re.search(r'key="plater_id" value="([^"]*)"', m.group(1))
            if pid and pid.group(1) == plate_id:
                return m.group(1)
        return None

    def filament_count(self):
        """How many project filaments this project has."""
        for key in ("filament_settings_id", "filament_type",
                    "filament_colour"):
            value = self.settings.get(key)
            if isinstance(value, list) and value:
                return len(value)
        fmap = self.filament_map()[1]
        return len(fmap) if fmap else 1

    def filament_names(self):
        """Human names of the project filaments, best effort - with a
        staged filament preset already showing its name."""
        ids = self.effective_setting("filament_settings_id") or []
        types = self.effective_setting("filament_type") or []
        count = max(len(ids), len(types), len(self.filament_map()[1]))
        names = []
        for i in range(count):
            label = ids[i] if i < len(ids) else ""
            ftype = types[i] if i < len(types) else ""
            names.append(label or ftype or f"Filament {i + 1}")
        return names

    def plate_size(self):
        """(width, depth) in mm from printable_area, or None."""
        area = self.settings.get("printable_area")
        if isinstance(area, list) and area:
            try:
                xs, ys = zip(*(map(float, corner.split("x")) for corner in area))
                return max(xs), max(ys)
            except (ValueError, TypeError):
                pass
        return None

    def summary(self):
        """Plain-language facts for the editor header, one per line."""
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
        bed = self.settings.get("curr_bed_type")
        if bed:
            lines.append(f"Build plate: {bed}")
        nplates = len(self.plates())
        if nplates > 1:
            lines.append(f"{nplates} build plates - see Placement")
        mode, fmap = self.filament_map()
        if fmap:
            lines.append(f"Filament to nozzle mapping mode: {mode}")
        return lines

    # -------------------------------------------------------------- staging

    def _replace_pending(self, match_fn, change):
        """Replace an existing staged change on the same target, then add."""
        self.pending = [c for c in self.pending if not match_fn(c)]
        change["source"] = self.stage_source
        self.pending.append(change)

    def stage_setting(self, key, new_value):
        """Stage one project_settings value. Lists come in as lists of str."""
        if key not in self.settings:
            raise ProjectError(
                f"No setting named {key} exists in this project. AccessSlice "
                "never invents new keys, because the slicer would ignore or "
                "reject them."
            )
        old = self.settings[key]
        if isinstance(old, list):
            if not isinstance(new_value, list):
                raise ProjectError(f"{key} is a per-filament list setting.")
            new = [str(v) for v in new_value]
            if len(new) != len(old):
                raise ProjectError(
                    f"{key} needs exactly {len(old)} values (one per "
                    f"filament slot); got {len(new)}."
                )
        else:
            new = str(new_value)
        if new == old:
            # Unchanged: make sure no stale staged edit for this key stays.
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "setting" and c["key"] == key)]
            return False
        self._replace_pending(
            lambda c: c["kind"] == "setting" and c["key"] == key,
            {"kind": "setting", "key": key, "old": old, "new": new,
             "label": f"Setting {key}"},
        )
        return True

    def stage_move(self, objectid, x, y):
        """Stage a new centre position (mm) for one object."""
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        o = objs[objectid]
        if abs(o["x"] - x) < 1e-6 and abs(o["y"] - y) < 1e-6:
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "move"
                                    and c["objectid"] == objectid)]
            return False
        self._replace_pending(
            lambda c: c["kind"] == "move" and c["objectid"] == objectid,
            {"kind": "move", "objectid": objectid,
             "old": (o["x"], o["y"]), "new": (x, y),
             "label": f"Position of {o['name']}"},
        )
        return True

    def _object_nums(self, objectid):
        """The 12 transform numbers of an object's build item, or None."""
        for oid, nums in engine.find_items(self.model_xml):
            if oid == objectid and len(nums) >= 12:
                return nums
        return None

    def object_transform_facts(self, objectid):
        """Rotation and size of an object, in plain facts.

        The 3MF item transform is a row-vector matrix: nine rotation/
        scale numbers then the translation. Returns
        {"scale_percent", "z_rotation_degrees", "simple"} - simple
        means the transform is just a vertical-axis rotation plus a
        uniform scale, so the two numbers tell the whole story; for a
        3D-rotated (or mirrored) object z_rotation_degrees is None and
        only the size (from the determinant) is reported.
        """
        nums = self._object_nums(objectid)
        if not nums:
            return None
        a, b, c, d, e, f, g, h, i = nums[0:9]
        det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
        scale = abs(det) ** (1.0 / 3.0) if det else 0.0
        tol = max(1e-4 * max(scale, 1e-9), 1e-9)
        simple = (det > 0 and abs(c) < tol and abs(f) < tol
                  and abs(g) < tol and abs(h) < tol
                  and abs(i - scale) < tol)
        degrees = math.degrees(math.atan2(b, a)) if simple else None
        return {"scale_percent": scale * 100.0,
                "z_rotation_degrees": degrees, "simple": simple}

    def object_shape(self, objectid):
        """The object's mesh (vertices, triangles) in its own item
        coordinates, read from the 3MF's geometry entries; None when
        no readable mesh exists. Cached - meshes are big and never
        edited, so one parse per open project."""
        if objectid not in self._shape_cache:
            try:
                self._shape_cache[objectid] = geometry.shape_of_object(
                    self.model_xml, self.entries, objectid)
            except Exception:
                # An unreadable mesh must degrade a command honestly,
                # never crash a page.
                self._shape_cache[objectid] = None
        return self._shape_cache[objectid]

    def _transform_step(self, change, nums):
        """One staged placement change applied to 12 transform numbers.

        The single implementation both the save pipeline and
        effective_item_nums use, so what a preview promises is exactly
        what the save writes.
        """
        kind = change["kind"]
        nums = list(nums)
        if kind == "move":
            nums[9], nums[10] = change["new"]
        elif kind == "rotate":
            # Row-vector convention: an extra turn about the object's
            # own vertical axis is M' = M x Rz, translation untouched.
            rad = math.radians(change["degrees"])
            c, s = math.cos(rad), math.sin(rad)
            for i in (0, 3, 6):
                x, y = nums[i], nums[i + 1]
                nums[i], nums[i + 1] = x * c - y * s, x * s + y * c
        elif kind == "scale":
            nums = [v * change["factor"] for v in nums[0:9]] + nums[9:12]
            # Keep the object's bottom at its height instead of growing
            # it half into (or off) the plate; staged with the change
            # when the mesh was readable.
            if change.get("new_z") is not None:
                nums[11] = change["new_z"]
        elif kind == "orient":
            # Auto-orient stages the finished 12 numbers outright.
            nums = list(change["nums"])
        elif kind == "replate":
            origin = self.effective_origin(change["target"])
            size = self.plate_size()
            if origin is None or not size:
                raise ProjectError(
                    "The plate layout of this project could not be read "
                    "reliably, so moving between plates is disabled for "
                    "it.")
            nums[9] = origin[0] + size[0] / 2
            nums[10] = origin[1] + size[1] / 2
        elif kind == "replate_new":
            origin = self.effective_origin(("new", change["slot"]))
            size = self.plate_size()
            if origin is None or not size:
                raise ProjectError(
                    "The plate layout of this project could not be read "
                    "reliably, so a new plate cannot be placed for it.")
            local = change.get("local") or (size[0] / 2, size[1] / 2)
            nums[9] = origin[0] + local[0]
            nums[10] = origin[1] + local[1]
        return nums

    def effective_item_nums(self, objectid, skip=None):
        """The object's 12 transform numbers as they WILL be once the
        currently staged changes are saved (replayed in staging order).
        skip: optional predicate for pending changes to leave out."""
        nums = self._object_nums(objectid)
        if not nums:
            return None
        nums = list(nums[:12])
        replated = False
        for c in self.pending:
            if c.get("objectid") != objectid or (skip and skip(c)):
                continue
            if c["kind"] in ("move", "rotate", "scale", "orient",
                             "replate", "replate_new"):
                nums = self._transform_step(c, nums)
                replated = replated or c["kind"].startswith("replate")
        if not replated:
            # A planned new plate can change the plate grid's column
            # count; objects staying on their plate follow it.
            dx, dy = self._plate_shift(self.plate_of_object(objectid))
            nums[9] += dx
            nums[10] += dy
        return nums

    def stage_rotate(self, objectid, degrees):
        """Stage rotating an object by degrees around the vertical
        axis, about its own centre (positive = counterclockwise seen
        from above). Relative on purpose: 'turn it 45 degrees' is
        always meaningful, even for objects with a 3D rotation. A
        newly staged rotation replaces a previously staged one."""
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        try:
            degrees = float(degrees)
        except (TypeError, ValueError):
            raise ProjectError(
                f"The rotation for {objs[objectid]['name']} must be a "
                "number of degrees.")
        if abs(degrees) < 1e-9 or abs(degrees) % 360 < 1e-9:
            # 0 is the field's untouched default, so it must never drop
            # an already staged rotation - discard on the review page
            # is the way to cancel one.
            return False
        facts = self.object_transform_facts(objectid)
        old = (f"{facts['z_rotation_degrees']:.1f} degrees"
               if facts and facts["z_rotation_degrees"] is not None
               else "as placed")
        self._replace_pending(
            lambda c: c["kind"] == "rotate" and c["objectid"] == objectid,
            {"kind": "rotate", "objectid": objectid, "degrees": degrees,
             "old": old, "new": f"{degrees:g} degrees further",
             "label": f"Rotation of {objs[objectid]['name']}"})
        return True

    def stage_scale(self, objectid, target_percent):
        """Stage scaling an object to target_percent of its imported
        size (100 = as imported). Absolute, so the field can simply
        show and accept the size."""
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        try:
            target = float(target_percent)
        except (TypeError, ValueError):
            raise ProjectError(
                f"The size for {objs[objectid]['name']} must be a "
                "number of percent (100 is the original size).")
        if target <= 0:
            raise ProjectError(
                f"The size for {objs[objectid]['name']} must be a "
                "positive percentage.")
        facts = self.object_transform_facts(objectid)
        if not facts or facts["scale_percent"] <= 0:
            raise ProjectError(
                f"The current size of {objs[objectid]['name']} could "
                "not be read, so it cannot be scaled here.")
        current = facts["scale_percent"]
        if abs(target - current) < 0.05:
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "scale"
                                    and c["objectid"] == objectid)]
            return False
        factor = target / current
        # Scaling multiplies the rotation part about the item's origin,
        # which is normally the object's CENTRE - without a correction
        # the bottom would sink into (or float above) the plate. With a
        # readable mesh, keep the bottom exactly where it is.
        new_z = None
        shape = self.object_shape(objectid)
        eff = self.effective_item_nums(
            objectid, skip=lambda c: c["kind"] == "scale")
        if shape and eff:
            m2, m5, m8 = eff[2], eff[5], eff[8]
            min_rot_z = min(x * m2 + y * m5 + z * m8
                            for x, y, z in shape[0])
            bottom = min_rot_z + eff[11]
            new_z = bottom - factor * min_rot_z
        self._replace_pending(
            lambda c: c["kind"] == "scale" and c["objectid"] == objectid,
            {"kind": "scale", "objectid": objectid,
             "factor": factor, "new_z": new_z,
             "old": f"{current:.1f}%", "new": f"{target:g}%",
             "label": f"Size of {objs[objectid]['name']}"})
        return True

    def stage_orient(self, objectid):
        """Stage turning an object so its most printable side lies on
        the plate: the biggest flat area goes down unless that costs
        too much unsupported overhang (the same trade every slicer's
        auto-orient makes). The turned object keeps its spot on the
        plate and rests exactly on it. Returns True when a better lie
        was staged, False when the object already lies as well as it
        can. Raises ProjectError when the mesh is unreadable.

        Since 2026-09-02 an object that already lies well but floats
        above or sinks into the plate is dropped onto it instead
        (staged with drop_only = True, so the messages can say so) -
        the pre-flight floating/sunk warning points here."""
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        name = objs[objectid]["name"]
        shape = self.object_shape(objectid)
        if shape is None:
            raise ProjectError(
                f"The shape of {name} could not be read from the "
                "file, so it cannot be turned automatically.")
        # A re-run replaces the staged turn, so compute WITHOUT it.
        eff = self.effective_item_nums(
            objectid, skip=lambda c: c["kind"] == "orient")
        if eff is None:
            raise ProjectError(
                f"The placement of {name} could not be read, so it "
                "cannot be turned automatically.")
        verts, tris = shape
        m, t = eff[0:9], eff[9:12]
        oriented = [geometry.rotate_point(m, v) for v in verts]
        down = geometry.best_down_direction(oriented, tris)
        if down is None:
            bottom = geometry.bbox(oriented)[2] + t[2]
            if abs(bottom) > 0.05:
                where = (f"{bottom:.1f} mm above the plate" if bottom > 0
                         else f"{-bottom:.1f} mm into the plate")
                self._replace_pending(
                    lambda c: c["kind"] == "orient"
                    and c["objectid"] == objectid,
                    {"kind": "orient", "objectid": objectid,
                     "nums": list(m) + [t[0], t[1],
                                       t[2] - bottom],
                     "drop_only": True, "old": where,
                     "new": "resting on the plate",
                     "label": f"Height above the plate of {name}"})
                return True
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "orient"
                                    and c["objectid"] == objectid)]
            return False
        q = geometry.rotation_taking(down, (0.0, 0.0, -1.0))
        new_m = geometry.matmul9(m, q)
        turned = [geometry.rotate_point(new_m, v) for v in verts]
        bb_old = geometry.bbox(oriented)
        bb_new = geometry.bbox(turned)
        # Same centre on the plate as before, resting on the plate.
        new_nums = new_m + [
            (bb_old[0] + bb_old[3]) / 2 + t[0] - (bb_new[0] + bb_new[3]) / 2,
            (bb_old[1] + bb_old[4]) / 2 + t[1] - (bb_new[1] + bb_new[4]) / 2,
            -bb_new[2],
        ]
        self._replace_pending(
            lambda c: c["kind"] == "orient" and c["objectid"] == objectid,
            {"kind": "orient", "objectid": objectid, "nums": new_nums,
             "old": "as it lies now", "new": "most printable side down",
             "label": f"Lying direction of {name}"})
        return True

    def orient_is_drop(self, objectid):
        """True when the staged automatic turn for objectid only drops
        it onto the plate (its lie was already fine)."""
        return any(c["kind"] == "orient" and c["objectid"] == objectid
                   and c.get("drop_only") for c in self.pending)

    def stage_arrange(self, plate_id=None):
        """Stage a fresh spot for every object on one plate so nothing
        overlaps: biggest first, at least ARRANGE_GAP_MM of air between
        objects, the whole layout centred on the plate. Heights are
        left alone. Returns (staged_count, notes) - notes are honest
        remarks to speak alongside the result. Raises ProjectError
        when the objects cannot all fit."""
        size = self.plate_size()
        if not size:
            raise ProjectError(
                "The plate size of this printer profile could not be "
                "read, so automatic arranging is unavailable.")
        plates = self.plates()
        plate_of = {oid: p["id"] for p in plates
                    for oid in p["object_ids"]}
        for c in self.pending:
            if c["kind"] == "replate":
                plate_of[c["objectid"]] = c["target"]
        removed = self.removed_ids()
        objs = [o for o in self.objects() if o["id"] not in removed]
        if len(plates) > 1:
            if plate_id is None:
                plate_id = plates[0]["id"]
            origin = self.plate_origin(plate_id)
            if origin is None:
                raise ProjectError(
                    "The plate layout of this project could not be read "
                    "reliably, so automatic arranging is disabled for "
                    "it.")
            group = [o for o in objs if plate_of.get(o["id"]) == plate_id]
        else:
            # Single plate: every object, even one whose membership
            # record is missing (seen in externally assembled files).
            origin = (0.0, 0.0)
            group = objs
        if not group:
            raise ProjectError("There is nothing on this plate to "
                               "arrange.")
        sizes, offsets, notes = [], [], []
        for o in group:
            shape = self.object_shape(o["id"])
            eff = self.effective_item_nums(o["id"])
            if shape and eff:
                bb = geometry.bbox([geometry.transform_point(eff, v)
                                    for v in shape[0]])
                sizes.append((bb[3] - bb[0], bb[4] - bb[1]))
                # Where the true centre sits relative to the transform's
                # translation - moves place the translation, not the
                # bounding box.
                offsets.append(((bb[0] + bb[3]) / 2 - eff[9],
                                (bb[1] + bb[4]) / 2 - eff[10]))
            else:
                sizes.append((50.0, 50.0))
                offsets.append((0.0, 0.0))
                notes.append(f"The shape of {o['name']} could not be "
                             "read, so a 50 by 50 mm footprint was "
                             "assumed for it.")
        spots = geometry.arrange_rects(sizes, size[0], size[1],
                                       keepout=self._front_left_keepout())
        if spots is None:
            named = [(o["name"], s) for o, s in zip(group, sizes)]
            raise ProjectError(
                f"The {len(group)} objects do not all fit on one plate "
                f"with {geometry.ARRANGE_GAP_MM:g} mm of air between "
                "them. " + self._size_sentence(named, size)
                + (" Use the button that spills onto new plates, move "
                   "some to another plate, or scale something down."
                   if self.supports_plates else
                   " Remove some, or scale something down."))
        staged = 0
        for o, (cx, cy), (ox, oy) in zip(group, spots, offsets):
            if self.stage_move(o["id"], origin[0] + cx - ox,
                               origin[1] + cy - oy):
                staged += 1
        if self.settings.get("print_sequence") == "by object":
            notes.append(
                "This project prints object by object; automatic "
                "arranging leaves only "
                f"{geometry.ARRANGE_GAP_MM:g} mm of air, which is not "
                "enough for the printhead to pass finished objects - "
                "check the layout in the slicer before printing.")
        return staged, notes


    # ------------------------------------------- planned plates (new)

    def _referenced_slots(self):
        """The new-plate slots staged changes refer to, in rank order.
        A slot is a plate that will exist after the save; slots are
        ranked so a discarded change never leaves a gap."""
        slots = set()
        for c in self.pending:
            if c["kind"] == "replate_new":
                slots.add(c["slot"])
            for key, _lx, _ly in c.get("placements", []):
                if isinstance(key, tuple):
                    slots.add(key[1])
        return sorted(slots)

    def planned_layout(self):
        """Plate origins as they will be after the save: existing plate
        ids and ("new", slot) keys. None when the saved grid is
        unreliable (nothing plate-related is allowed then)."""
        grid = self.plate_grid()
        if not grid["reliable"]:
            return None
        plates = self.plates()
        slots = self._referenced_slots()
        keys = [p["id"] for p in plates] + [("new", s) for s in slots]
        n = len(keys)
        if n <= 1:
            return {k: (0.0, 0.0) for k in keys}
        stride = grid["stride"]
        if not stride:
            return None
        cols = grid["cols"] if not slots else max(
            1, math.ceil(math.sqrt(n)))
        return {k: ((i % cols) * stride, -(i // cols) * stride)
                for i, k in enumerate(keys)}

    def effective_origin(self, plate_key):
        layout = self.planned_layout()
        if layout is None:
            return None
        return layout.get(plate_key)

    def _plate_shift(self, plate_id):
        """How far a plate moves between the saved grid and the
        planned one (zero unless staged new plates change the column
        count)."""
        if plate_id is None or not self._referenced_slots():
            return (0.0, 0.0)
        old = self.plate_origin(plate_id)
        new = self.effective_origin(plate_id)
        if old is None or new is None:
            return (0.0, 0.0)
        return (new[0] - old[0], new[1] - old[1])

    def _new_slot(self):
        slots = self._referenced_slots()
        return (slots[-1] + 1) if slots else 0

    def stage_new_plate(self, objectid, local=None, slot=None):
        """Stage moving an object onto a plate that does not exist yet
        (created at save time, after the last plate). local: plate-
        local centre; None centres it. Returns the slot used."""
        if not self.supports_plates:
            raise ProjectError(
                "This slicer has a single bed, so there is no other "
                "plate to move to.")
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        if self.planned_layout() is None:
            raise ProjectError(
                "The plate layout of this project could not be read "
                "reliably, so plates cannot be added to it.")
        if self.plate_of_object(objectid) is None:
            raise ProjectError(
                f"{objs[objectid]['name']} has no plate membership entry "
                "in the file - save the project from the slicer once "
                "and try again.")
        if slot is None:
            slot = self._new_slot()
        self.pending = [c for c in self.pending
                        if not (c["kind"] in ("replate", "replate_new")
                                and c["objectid"] == objectid)]
        self.pending.append({
            "kind": "replate_new", "objectid": objectid, "slot": slot,
            "local": local,
            "old": f"plate {self.plate_of_object(objectid)}",
            "new": "a new plate",
            "label": f"Plate of {objs[objectid]['name']}"})
        return slot

    # ------------------------------------- copies, removal, add model

    def _footprint(self, objectid, nums=None):
        """((width, depth), (offset x, offset y), note): the object's
        plate footprint at its effective transform, and how far the
        bounding-box centre sits from the item translation."""
        shape = self.object_shape(objectid)
        eff = nums if nums is not None else self.effective_item_nums(objectid)
        if shape and eff:
            bb = geometry.bbox([geometry.transform_point(eff, v)
                                for v in shape[0]])
            return ((bb[3] - bb[0], bb[4] - bb[1]),
                    ((bb[0] + bb[3]) / 2 - eff[9],
                     (bb[1] + bb[4]) / 2 - eff[10]), None)
        name = next((o["name"] for o in self.objects()
                     if o["id"] == objectid), objectid)
        return ((50.0, 50.0), (0.0, 0.0),
                f"The shape of {name} could not be read, so a 50 by 50 "
                "mm footprint was assumed for it.")

    def removed_ids(self):
        return {c["objectid"] for c in self.pending if c["kind"] == "remove"}

    def _plate_members(self, plate_id):
        """Objects on a plate after staged replates, minus removals."""
        plates = self.plates()
        plate_of = {oid: p["id"] for p in plates for oid in p["object_ids"]}
        for c in self.pending:
            if c["kind"] == "replate":
                plate_of[c["objectid"]] = c["target"]
            elif c["kind"] == "replate_new":
                plate_of[c["objectid"]] = ("new", c["slot"])
        removed = self.removed_ids()
        if len(plates) <= 1 and plate_id in (None, "all",
                                             plates[0]["id"] if plates else None):
            return [o for o in self.objects()
                    if o["id"] not in removed
                    and not isinstance(plate_of.get(o["id"]), tuple)]
        return [o for o in self.objects()
                if plate_of.get(o["id"]) == plate_id
                and o["id"] not in removed]

    def _size_sentence(self, named_sizes, size):
        """The honest arithmetic behind a refusal to arrange."""
        parts = [f"{n} {w:g} by {d:g} mm" for n, (w, d) in named_sizes]
        widest = sorted((w for _n, (w, _d) in named_sizes), reverse=True)
        text = ("Footprints: " + "; ".join(parts) + f". The plate is "
                f"{size[0]:g} by {size[1]:g} mm.")
        if len(widest) >= 2:
            need = widest[0] + widest[1] + geometry.ARRANGE_GAP_MM
            text += (f" The two widest side by side need {need:g} mm "
                     f"including {geometry.ARRANGE_GAP_MM:g} mm of air.")
        return text

    def _plan_layout(self, plate_id, members, extra_sizes, spill):
        """Arrange members (objects) plus extra footprints on one
        plate, spilling onto new plates when allowed. Returns
        (per-item [(plate_key, lx, ly)], member offsets, notes)."""
        size = self.plate_size()
        if not size:
            raise ProjectError(
                "The plate size of this printer profile could not be "
                "read, so automatic placing is unavailable.")
        sizes, offsets, notes, named = [], [], [], []
        for o in members:
            (w, d), off, note = self._footprint(o["id"])
            sizes.append((w, d))
            offsets.append(off)
            named.append((o["name"], (w, d)))
            if note:
                notes.append(note)
        for name, (w, d) in extra_sizes:
            sizes.append((w, d))
            named.append((name, (w, d)))
        keepout = self._front_left_keepout()
        if spill and self.supports_plates:
            try:
                laid = object_ops.spill_arrange(
                    sizes, size[0], size[1], keepout, geometry.arrange_rects)
            except ProjectError as e:
                raise ProjectError(str(e) + " "
                                   + self._size_sentence(named, size))
            base = self._new_slot()
            placements = []
            for idx, spot in laid:
                key = plate_id if idx == 0 else ("new", base + idx - 1)
                placements.append((key, spot[0], spot[1]))
            used = max(idx for idx, _s in laid) if laid else 0
            if used:
                notes.append(f"{used} new plate(s) will be added for what "
                             "does not fit.")
        else:
            spots = geometry.arrange_rects(sizes, size[0], size[1],
                                           keepout=keepout)
            if spots is None:
                raise ProjectError(
                    f"The {len(sizes)} objects do not all fit on one "
                    f"plate with {geometry.ARRANGE_GAP_MM:g} mm of air "
                    "between them. " + self._size_sentence(named, size)
                    + (" Use the button that spills onto new plates, "
                       "move some to another plate, or scale something "
                       "down." if self.supports_plates else
                       " Remove some, or scale something down."))
            placements = [(plate_id, cx, cy) for cx, cy in spots]
        return placements, offsets, notes

    def _stage_member_placements(self, members, placements, offsets):
        """Stage moves / new-plate moves for existing objects."""
        staged = 0
        for o, (key, cx, cy), (ox, oy) in zip(members, placements, offsets):
            if isinstance(key, tuple):
                self.stage_new_plate(o["id"], local=(cx - ox, cy - oy),
                                     slot=key[1])
                staged += 1
                continue
            origin = self.effective_origin(key) or (0.0, 0.0)
            # Stage relative to the SAVED grid: moves store world
            # coordinates that effective_item_nums shifts like any
            # other staying object.
            saved = self.plate_origin(key) or origin
            if self.stage_move(o["id"], saved[0] + cx - ox,
                               saved[1] + cy - oy):
                staged += 1
        return staged

    def stage_arrange_spill(self, plate_id=None):
        """Arrange one plate, moving what does not fit onto new plates.
        Returns (staged count, notes)."""
        plates = self.plates()
        if plate_id in (None, "all"):
            plate_id = plates[0]["id"] if plates else None
        members = self._plate_members(plate_id)
        if not members:
            raise ProjectError("There is nothing on this plate to "
                               "arrange.")
        placements, offsets, notes = self._plan_layout(
            plate_id, members, [], spill=True)
        staged = self._stage_member_placements(members, placements, offsets)
        return staged, notes

    def stage_copy(self, objectid, count):
        """Stage `count` copies of an object, laid out with the rest of
        its plate (spilling onto new plates where the format allows).
        Returns (copies, notes)."""
        try:
            count = int(str(count).strip())
        except ValueError:
            raise ProjectError("The number of copies must be a whole "
                               "number.")
        if count < 1 or count > self.MAX_COPIES:
            raise ProjectError(
                f"The number of copies must be between 1 and "
                f"{self.MAX_COPIES}.")
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        if objectid in self.removed_ids():
            raise ProjectError(
                f"{objs[objectid]['name']} is staged for removal - "
                "discard that first to copy it.")
        for c in self.pending:
            if c["kind"] == "copy" and c["objectid"] == objectid:
                raise ProjectError(
                    f"Copies of {objs[objectid]['name']} are already "
                    "staged - save or discard those first.")
        plate_id = self.plate_of_object(objectid)
        plates = self.plates()
        if plate_id is None and len(plates) <= 1:
            plate_id = plates[0]["id"] if plates else None
        if plate_id is None:
            raise ProjectError(
                f"{objs[objectid]['name']} has no plate membership entry "
                "in the file - save the project from the slicer once "
                "and try again.")
        members = self._plate_members(plate_id)
        (w, d), offset, note = self._footprint(objectid)
        name = objs[objectid]["name"]
        # Numbering continues past copies that already exist, so two
        # rounds of copying never produce two "(copy 1)".
        base = re.sub(r" \(copy \d+\)$", "", name)
        taken = [int(m.group(1)) for o in objs.values()
                 for m in [re.match(re.escape(base) + r" \(copy (\d+)\)$",
                                    o["name"])] if m]
        start = (max(taken) if taken else 0) + 1
        copy_names = [f"{base} (copy {start + k})" for k in range(count)]
        extra = [(n, (w, d)) for n in copy_names]
        placements, offsets, notes = self._plan_layout(
            plate_id, members, extra, spill=True)
        if note:
            notes.append(note)
        self._stage_member_placements(
            members, placements[:len(members)], offsets)
        copy_places = placements[len(members):]
        new_plates = len({k for k, _x, _y in copy_places
                          if isinstance(k, tuple)})
        self.pending.append({
            "kind": "copy", "objectid": objectid, "count": count,
            "names": copy_names,
            "placements": copy_places, "offset": offset,
            "old": "1 of it", "new": f"{count} more, placed by arranging"
            + (f" ({new_plates} on new plates)" if new_plates else ""),
            "label": f"Copies of {name}"})
        return count, notes

    def stage_remove(self, objectid):
        """Stage removing an object; its other staged changes go too.
        The last object cannot be removed (the slicers refuse an
        empty project)."""
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        if objectid in self.removed_ids():
            return False
        if len(objs) - len(self.removed_ids()) <= 1:
            raise ProjectError(
                f"{objs[objectid]['name']} is the last object; a project "
                "cannot be saved with no object in it. Add or copy "
                "something first, or start a new print job instead.")
        self.pending = [c for c in self.pending
                        if c.get("objectid") != objectid]
        self.pending.append({
            "kind": "remove", "objectid": objectid,
            "old": "in the project", "new": "removed",
            "label": f"Remove {objs[objectid]['name']}"})
        return True

    def _staging_dir(self):
        folder = self.backup_dir() / "staging"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _build_import_source(self, model_path, exe):
        """A temporary project holding just the new model, built the
        same way a new print job is (the slicer's own importer, this
        project's settings as the base)."""
        import newjob
        target = self._staging_dir() / (
            f"import-{Path(model_path).stem}-{len(self.pending)}.3mf")
        newjob.create_project(exe, self.path, [model_path], target)
        return target

    def _open_source(self, path):
        return type(self)(path)

    def stage_add_model(self, model_path, exe, plate_id=None):
        """Stage adding a model file's object(s) to a plate. The
        slicer's importer runs now (so a bad file is heard about
        right away); the objects are copied in at save time.
        Returns (names, notes)."""
        model_path = Path(str(model_path).strip().strip('"'))
        if not model_path.is_file():
            raise ProjectError(f"The model file {model_path} was not "
                               "found.")
        if not exe:
            raise ProjectError("The slicer program path is not set - "
                               "see Settings.")
        self._ensure_fresh("adding a model")
        plates = self.plates()
        if plate_id in (None, "all", ""):
            plate_id = plates[0]["id"] if plates else None
        if plate_id is None:
            raise ProjectError("This project has no plate to add to.")
        source = self._build_import_source(model_path, exe)
        src = self._open_source(source)
        src_objs = src.objects()
        if not src_objs:
            raise ProjectError("The model file produced no object.")
        members = self._plate_members(plate_id)
        extra, offsets_src = [], []
        for o in src_objs:
            (w, d), off, _note = src._footprint(o["id"])
            extra.append((o["name"], (w, d)))
            offsets_src.append(off)
        placements, offsets, notes = self._plan_layout(
            plate_id, members, extra, spill=True)
        self._stage_member_placements(
            members, placements[:len(members)], offsets)
        new_places = placements[len(members):]
        self.pending.append({
            "kind": "add_model", "source": str(source),
            "source_ids": [o["id"] for o in src_objs],
            "names": [o["name"] for o in src_objs],
            "offsets": offsets_src, "placements": new_places,
            "old": "not in the project",
            "new": "added and placed by arranging",
            "label": f"Add {model_path.name}"})
        return [o["name"] for o in src_objs], notes

    def _drop_import_source(self, change):
        if change.get("kind") == "add_model":
            try:
                Path(change["source"]).unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------ save-time application

    def _apply_object_ops(self, entries, model_xml, ms_xml, slot_to_id):
        """Copies, removals and imports, in staging order, plus the
        placements of new objects; the Bambu-family implementation."""
        t = {"entries": entries, "model": model_xml, "ms": ms_xml}
        names = {o["id"]: o["name"] for o in self.objects()}

        def key_id(key):
            return slot_to_id[key[1]] if isinstance(key, tuple) else key

        for c in self.pending:
            if c["kind"] == "copy":
                copy_names = c.get("names") or [
                    f"{names.get(c['objectid'], 'object')} (copy {k})"
                    for k in range(1, c["count"] + 1)]
                first_key = c["placements"][0][0]
                t, new_ids = object_ops.copy_bambu(
                    t, t, c["objectid"], c["count"], key_id(first_key),
                    lambda k, ns=copy_names: ns[k - 1])
                for oid, (key, cx, cy) in zip(new_ids, c["placements"]):
                    if key_id(key) != key_id(first_key):
                        t["ms"] = object_ops.move_instance_bambu(
                            t["ms"], oid, key_id(key))
                    origin = self.effective_origin(key) or (0.0, 0.0)
                    ox, oy = c["offset"]
                    t["model"] = object_ops.set_item_translation(
                        t["model"], oid, origin[0] + cx - ox,
                        origin[1] + cy - oy)
            elif c["kind"] == "remove":
                t = object_ops.remove_bambu(t, c["objectid"])
            elif c["kind"] == "add_model":
                src_session = self._open_source(Path(c["source"]))
                src = {"entries": src_session.entries,
                       "model": src_session.model_xml,
                       "ms": src_session.ms_xml}
                for sid, name, off, (key, cx, cy) in zip(
                        c["source_ids"], c["names"], c["offsets"],
                        c["placements"]):
                    t, new_ids = object_ops.copy_bambu(
                        t, src, sid, 1, key_id(key), lambda k, n=name: n)
                    origin = self.effective_origin(key) or (0.0, 0.0)
                    t["model"] = object_ops.set_item_translation(
                        t["model"], new_ids[0], origin[0] + cx - off[0],
                        origin[1] + cy - off[1])
        return t["entries"], t["model"], t["ms"]

    def _apply_new_plates(self, ms_xml):
        """Create every planned plate; returns (ms_xml, {slot: id})."""
        slot_to_id = {}
        for slot in self._referenced_slots():
            ms_xml, pid = object_ops.add_plate_bambu(ms_xml)
            slot_to_id[slot] = pid
        return ms_xml, slot_to_id

    def _apply_plate_shifts(self, model_xml):
        """Objects staying on a plate whose origin moved in the planned
        grid follow it (the same rule effective_item_nums applies)."""
        if not self._referenced_slots():
            return model_xml
        replated = {c["objectid"] for c in self.pending
                    if c["kind"] in ("replate", "replate_new")}
        for o in self.objects():
            if o["id"] in replated or o["id"] in self.removed_ids():
                continue
            dx, dy = self._plate_shift(self.plate_of_object(o["id"]))
            if dx or dy:
                model_xml = object_ops.shift_item(model_xml, o["id"], dx, dy)
        return model_xml

    def _apply_replate_new(self, model_xml, ms_xml, change, slot_to_id):
        pid = slot_to_id[change["slot"]]
        model_xml = self._apply_item_transform(
            model_xml, change["objectid"],
            lambda nums: self._transform_step(change, nums),
            "move to a new plate")
        ms_xml = object_ops.move_instance_bambu(
            ms_xml, change["objectid"], pid)
        return model_xml, ms_xml

    def _front_left_keepout(self):
        """(width, depth) of a front-left corner the printer keeps for
        itself (bed_exclude_area - a cutter or wiper parks there), or
        None. Only the common front-left-corner shape is understood."""
        area = self.settings.get("bed_exclude_area")
        if not isinstance(area, list) or not area:
            return None
        try:
            xs, ys = zip(*(map(float, corner.split("x"))
                           for corner in area))
        except (ValueError, TypeError):
            return None
        size = self.plate_size()
        if (min(xs) < 1 and min(ys) < 1 and max(xs) > 0 and max(ys) > 0
                and size and max(xs) < size[0] / 2
                and max(ys) < size[1] / 2):
            return (max(xs), max(ys))
        return None

    def stage_extruder(self, objectid, filament_number):
        """Stage which project filament an object prints with."""
        objs = {o["id"]: o for o in self.objects()}
        if objectid not in objs:
            raise ProjectError(f"No object with id {objectid}.")
        old = objs[objectid]["extruder"]
        new = str(filament_number)
        if old == new:
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "extruder"
                                    and c["objectid"] == objectid)]
            return False
        self._replace_pending(
            lambda c: c["kind"] == "extruder" and c["objectid"] == objectid,
            {"kind": "extruder", "objectid": objectid, "old": old, "new": new,
             "label": f"Filament used by {objs[objectid]['name']}"},
        )
        return True

    def stage_filament_map(self, new_map, plate_id=None):
        """Stage one plate's filament-to-nozzle map (dual-nozzle
        printers). Default plate: the first.

        new_map is a list of "1"/"2" strings, one per project filament.
        Staging the map always sets mode to Manual - the only mode where
        Studio respects the mapping (hard-won H2C fact).
        """
        if not all(v in ("1", "2") for v in new_map):
            raise ProjectError("Map values must be 1 (left) or 2 (right).")
        plates = self.plates()
        if plate_id is None:
            plate_id = plates[0]["id"] if plates else None
        elif plate_id not in {p["id"] for p in plates}:
            raise ProjectError(f"No plate {plate_id} in this project.")
        old_mode, old_map = self.filament_map(plate_id)
        if old_map == new_map and old_mode == "Manual":
            self.pending = [c for c in self.pending
                            if not (c["kind"] == "filament_map"
                                    and c.get("plate_id") == plate_id)]
            return False
        label = "Filament to nozzle mapping"
        if len(plates) > 1:
            label += f" on plate {plate_id}"
        self._replace_pending(
            lambda c: (c["kind"] == "filament_map"
                       and c.get("plate_id") == plate_id),
            {"kind": "filament_map", "plate_id": plate_id,
             "old": old_map, "new": list(new_map), "label": label},
        )
        return True

    def stage_preset(self, scope, name, filament_index=None):
        """Stage switching to a system preset from the slicer's library.

        scope: "process" (print profile), "machine" (printer), or
        "filament" with filament_index (0-based slot).
        The preset is resolved from Bambu Studio's own profile files at
        staging time, so a bad name fails here with a clear message, not
        at save time.
        """
        try:
            values = self._profiles.resolve_preset(self.slicer_exe,
                                                   scope, name)
        except self._profiles.ProfileError as e:
            raise ProjectError(str(e))
        if scope == "filament":
            ids = self.settings.get("filament_settings_id") or []
            i = filament_index
            if i is None or not (0 <= i < max(len(ids), 1)):
                raise ProjectError("Which filament slot to change is unclear.")
            old = ids[i] if i < len(ids) else "(none)"
            if old == name:
                self.pending = [
                    c for c in self.pending
                    if not (c["kind"] == "preset" and c["scope"] == "filament"
                            and c.get("filament_index") == i)]
                return False
            label = f"Filament {i + 1} preset"
        elif scope == "process":
            old = self.settings.get("print_settings_id", "(none)")
            if old == name:
                self.pending = [c for c in self.pending
                                if not (c["kind"] == "preset"
                                        and c["scope"] == "process")]
                return False
            label = "Print profile preset"
        elif scope == "machine":
            old = self.settings.get("printer_settings_id", "(none)")
            if old == name:
                self.pending = [c for c in self.pending
                                if not (c["kind"] == "preset"
                                        and c["scope"] == "machine")]
                return False
            label = "Printer preset"
        else:
            raise ProjectError(f"Unknown preset scope {scope}.")

        self._replace_pending(
            lambda c: (c["kind"] == "preset" and c["scope"] == scope
                       and c.get("filament_index") == filament_index),
            {"kind": "preset", "scope": scope, "name": name,
             "filament_index": filament_index, "values": values,
             "old": old, "new": name, "label": label},
        )
        return True

    def discard_pending(self, index):
        """Drop one staged change; returns it, or None for a bad index."""
        if 0 <= index < len(self.pending):
            change = self.pending.pop(index)
            self._drop_import_source(change)
            return change
        return None

    def discard_many(self, indexes):
        """Drop several staged changes at once; returns their labels.

        Indexes are positions in self.pending as the Review page showed
        them; deleting from the highest down keeps the rest valid.
        """
        labels = []
        for i in sorted(set(indexes), reverse=True):
            if 0 <= i < len(self.pending):
                labels.append(self.pending[i]["label"])
                self._drop_import_source(self.pending[i])
                del self.pending[i]
        return list(reversed(labels))

    def effective_setting(self, key):
        """Current value with any staged edit applied - what the UI shows.

        Staged PRESET switches are overlaid too, using the same
        shape-adapting logic the save uses - otherwise every tab keeps
        showing the old numbers until the save, which reads as "my
        profile choice did nothing" (Edis, 2026-08-26: walls still said
        2 after he picked the Strength profile).
        """
        value = self.settings.get(key)
        n = self.filament_count()
        for c in self.pending:
            if c["kind"] == "setting" and c["key"] == key:
                value = c["new"]
            elif (c["kind"] == "preset" and key in c["values"]
                    and value is not None):
                # Copy lists so the overlay never mutates saved state.
                tmp = {key: list(value) if isinstance(value, list)
                       else value}
                self._write_preset_values(tmp, {key: c["values"][key]},
                                          c.get("filament_index"), n)
                value = tmp[key]
        return value

    def effective_extruder(self, objectid, saved=None):
        """Which project filament an object prints with, staged edit
        applied. saved: the file's value when the caller has it."""
        value = saved
        for c in self.pending:
            if c["kind"] == "extruder" and c["objectid"] == objectid:
                value = c["new"]
        return value

    def effective_objects(self):
        """objects() with every staged per-object filament choice
        applied - what the chooser shows. Without this the chooser
        snapped back to the file's slot after every choice (Edis,
        2026-09-21: he moved the vase from the PLA slot to the PETG
        slot and the page kept showing PLA until Bambu Studio proved
        the save had taken)."""
        objs = []
        for o in self.objects():
            o = dict(o)
            o["extruder"] = self.effective_extruder(o["id"], o["extruder"])
            objs.append(o)
        return objs

    def staged_preset(self, scope, filament_index=None):
        """The staged preset name for a scope, or None if unchanged."""
        for c in self.pending:
            if (c["kind"] == "preset" and c["scope"] == scope
                    and c.get("filament_index") == filament_index):
                return c["name"]
        return None

    # Sparse infill patterns that print correctly at 100% density.
    # Bambu Studio's dialog fires for the others; "zig-zag" is what its
    # GUI calls Rectilinear (verified in a real project, 2026-08-26).
    SOLID_OK_PATTERNS = {"zig-zag", "alignedrectilinear", "concentric",
                         "line"}

    def infill_conflict(self):
        """Bambu Studio's 100%-infill conflict, or None.

        Studio pops a dialog the moment sparse infill density is set to
        100% with a pattern that cannot print solid: "<Pattern> infill
        pattern doesn't support 100% density. Switch to rectilinear
        pattern?" - Yes switches the pattern, No resets the density.
        Evaluated over EFFECTIVE values so staged edits count too.
        Returns {"pattern": ..., "density": ...} while the conflict
        exists.
        """
        density = str(self.effective_setting("sparse_infill_density")
                      or "")
        pattern = str(self.effective_setting("sparse_infill_pattern")
                      or "")
        try:
            dense = float(density.rstrip("% "))
        except ValueError:
            return None
        if dense >= 100 and pattern and pattern not in self.SOLID_OK_PATTERNS:
            return {"pattern": pattern, "density": density}
        return None

    def unit_conflict(self):
        """A staged mm-or-% value needing Studio's unit question, or None.

        Bambu Studio pops "Parameter validation: <key> - Is it N% or
        N mm? YES for N%, NO for N mm." when a large bare number is
        typed into an mm-or-% field (screenshot-verified with 10000 in
        max_travel_detour_distance, 2026-08-26; the exact GUI threshold
        is not public - above 100 a percentage becomes the likelier
        intent, so that is the trigger here). A change the user has
        already answered for carries unit_confirmed and is not asked
        about again.
        """
        for c in self.pending:
            if (c["kind"] == "setting"
                    and c["key"] in self.catalog.MM_OR_PERCENT_KEYS
                    and not c.get("unit_confirmed")):
                value = str(c["new"]).strip()
                if value.endswith("%"):
                    continue
                try:
                    number = float(value)
                except ValueError:
                    continue
                if number > 100:
                    return {"key": c["key"], "value": value}
        return None

    def answer_unit(self, key, as_percent):
        """Apply the unit-question answer to the staged change for key."""
        for c in self.pending:
            if c["kind"] == "setting" and c["key"] == key:
                if as_percent:
                    c["new"] = str(c["new"]).strip() + "%"
                c["unit_confirmed"] = True
                return c["new"]
        return None

    def default_infill_density(self):
        """The current print profile's own density - what Studio's "No -
        reset density to default non 100% value" resets to."""
        name = (self.staged_preset("process")
                or self.settings.get("print_settings_id"))
        if name and self.slicer_exe:
            try:
                resolved = self._profiles.resolve_preset(
                    self.slicer_exe, "process", name)
                value = resolved.get("sparse_infill_density")
                if value:
                    return str(value[0] if isinstance(value, list)
                               else value)
            except self._profiles.ProfileError:
                pass
        return "15%"

    def advice(self):
        """Warnings Bambu Studio's GUI would pop for the current values.

        Studio shows these as dialogs while editing; AccessSlice states
        them in result messages and on the editor pages instead. Only
        rules verified against Studio's behaviour belong here. Evaluated
        over the EFFECTIVE values (staged edits included), so a warning
        appears the moment a combination goes bad and clears the moment
        it is fixed.
        """
        notes = []
        conflict = self.infill_conflict()
        if conflict:
            notes.append(
                f"{conflict['pattern'].capitalize()} infill pattern "
                "doesn't support 100% density.")
        return notes

    # ---------------------------------------------------- stale-file guard

    def is_stale(self):
        """True when the file on disk is no longer what was loaded.

        The classic case: the project is open here AND in Bambu Studio,
        and Studio saved. Writing over that save would silently destroy
        it, so everything that writes (save, restore) refuses while
        stale, every editor page shows a warning, and reload() is the
        way forward.
        """
        try:
            st = self.path.stat()
        except OSError:
            return True   # gone or unreadable: definitely not our view
        return (st.st_mtime_ns, st.st_size) != self._loaded_stat

    def _ensure_fresh(self, doing_what):
        if self.is_stale():
            raise ProjectError(
                f"The project file changed on disk after AccessSlice "
                f"read it - probably saved from Bambu Studio. To avoid "
                f"destroying that newer version, {doing_what} was NOT "
                "done. Press 'Reload the file from disk' on any editor "
                "tab to pick up the newer version first."
            )

    def reload(self):
        """Re-read the file from disk, dropping staged changes.

        Staged changes describe edits to the version that was loaded;
        after the file changed underneath, applying them blind could do
        something different from what the user reviewed. Predictable
        and honest wins: they are dropped, and the result lines say so.
        """
        dropped = [c["label"] for c in self.pending]
        self.pending = []
        self._load()
        # Meshes and their derived reports belong to the old file.
        self._shape_cache = {}
        self._preflight_cache = {}
        lines = [f"Reloaded {self.path.name} from disk."]
        if dropped:
            lines.append(
                f"{len(dropped)} staged change(s) were discarded because "
                "they described the older version: "
                + ", ".join(dropped) + ".")
        return lines

    # -------------------------------------------------------------- backups

    # How many backups are kept per project; older ones are deleted.
    BACKUP_KEEP = 10

    # Filenames say in words what they are (screen-reader browsing):
    # "blind-benchy - before save 2026-08-27 14.32.05.3mf". Dots in the
    # time because Windows filenames cannot contain colons.
    _BACKUP_STAMP_RE = re.compile(
        r" - before (save|restore) "
        r"(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})(?: \((\d+)\))?\.3mf$")

    def backup_dir(self):
        """Backups live in a subfolder beside the project, so the
        projects folder itself stays a clean list of projects."""
        return self.path.parent / "backups"

    def _backup_marker(self):
        return self.path.stem + " - before "

    def list_backups(self):
        """This project's backups, newest first, as plain data:
        {"name", "path", "version_saved"} - version_saved is when the
        version INSIDE the backup was last written (its mtime, which
        copy2 preserves), the fact a user cares about."""
        folder = self.backup_dir()
        found = []
        if folder.is_dir():
            for p in folder.iterdir():
                m = self._BACKUP_STAMP_RE.search(p.name)
                if (p.is_file() and m
                        and p.name.startswith(self._backup_marker())):
                    # Key: the stamp, then the same-second counter, so
                    # two backups taken within one second stay ordered.
                    found.append(((m.group(2), int(m.group(3) or 1)), p))
        found.sort(key=lambda pair: pair[0], reverse=True)
        result = []
        for _key, p in found:
            when = datetime.datetime.fromtimestamp(p.stat().st_mtime)
            result.append({"name": p.name, "path": p,
                           "version_saved": f"{when:%Y-%m-%d %H:%M}"})
        return result

    def _write_backup(self, reason):
        """Copy the CURRENT file into the backups folder and prune.

        reason is "save" or "restore" - it becomes part of the name.
        Raises ProjectError (nothing has been touched yet) on failure.
        """
        folder = self.backup_dir()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        target = folder / (f"{self.path.stem} - before {reason} "
                           f"{stamp}.3mf")
        counter = 2
        while target.exists():   # two actions in the same second
            target = folder / (f"{self.path.stem} - before {reason} "
                               f"{stamp} ({counter}).3mf")
            counter += 1
        try:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, target)
        except OSError as e:
            raise ProjectError(
                f"Could not write the safety backup ({e}), so nothing "
                "was changed. Is the folder read-only or the disk full?"
            )
        self._prune_backups()
        return target

    def _prune_backups(self):
        """Delete the oldest backups beyond BACKUP_KEEP (best effort)."""
        for entry in self.list_backups()[self.BACKUP_KEEP:]:
            try:
                entry["path"].unlink()
            except OSError:
                pass

    def restore_backup(self, backup_name):
        """Swap the project file for one of its backups.

        The current version is backed up FIRST ("before restore"), so a
        restore can itself be undone. Staged changes are dropped - they
        described edits to a version that is no longer in the file.
        Returns plain-language result lines.
        """
        if "/" in backup_name or "\\" in backup_name:
            raise ProjectError("That is not one of this project's backups.")
        target = self.backup_dir() / backup_name
        if (not backup_name.startswith(self._backup_marker())
                or not self._BACKUP_STAMP_RE.search(backup_name)
                or not target.is_file()):
            raise ProjectError(
                "That backup was not found - the Backups page shows the "
                "ones that exist.")
        self._ensure_fresh("restoring")
        undo = self._write_backup("restore")
        dropped = len(self.pending)
        try:
            shutil.copy2(target, self.path)
            self.pending = []
            self._load()
        except (OSError, ProjectError) as e:
            shutil.copy2(undo, self.path)
            self._load()
            raise ProjectError(
                f"The backup could not be restored ({e}). The project "
                "is unchanged.")
        lines = [
            f"Restored the version from {backup_name}.",
            f"The version you had until now is kept as {undo.name}, so "
            "this restore can be undone the same way.",
        ]
        if dropped:
            lines.append(f"{dropped} staged change(s) were discarded - "
                         "they belonged to the replaced version.")
        return lines

    # --------------------------------------------------------------- saving

    def save(self):
        """Apply all staged changes and write the file safely.

        Order of operations, mirroring the proven engine behaviour:
        1. Apply every staged change to in-memory copies.
        2. Copy the current file to <name>.pre-edit-backup.3mf.
        3. Write the archive with forward-slash entry names.
        4. Verify structurally (entry list, XML well-formed, JSON valid).
        5. If verification fails, RESTORE the backup and report - the
           project on disk is never left broken.

        Returns a list of plain-language result lines for announcing.
        """
        if not self.pending:
            return ["No changes to save."]
        self._ensure_fresh("saving")

        model_xml = self.model_xml
        ms_xml = self.ms_xml
        # Deep copy: preset application edits inner lists, and the live
        # session state must stay untouched until the save is verified.
        settings = json.loads(json.dumps(self.settings))
        # The per-scope "differs from the system preset" bookkeeping.
        # Bambu Studio rebuilds a project's settings from the preset IDs
        # plus ONLY the keys registered here - an edited value that is
        # not registered is silently thrown away on open. One slot per
        # scope instance: [process, filament 1..n, machine].
        diff = self._diff_slots(settings)
        n = self.filament_count()
        entries_work = dict(self.entries)
        ms_xml, slot_to_id = self._apply_new_plates(ms_xml)

        for change in self.pending:
            if change["kind"] == "setting":
                settings[change["key"]] = change["new"]
                self._register_diff(diff, n, change)
            elif change["kind"] == "replate_new":
                model_xml, ms_xml = self._apply_replate_new(
                    model_xml, ms_xml, change, slot_to_id)
            elif change["kind"] == "preset":
                self._apply_preset(settings, diff, n, change)
            elif change["kind"] in ("move", "rotate", "scale", "orient"):
                model_xml = self._apply_transform_change(model_xml, change)
            elif change["kind"] == "extruder":
                ms_xml = self._apply_extruder(ms_xml, change)
            elif change["kind"] == "object_setting":
                ms_xml = self._apply_object_setting(ms_xml, change)
            elif change["kind"] == "filament_map":
                ms_xml = self._apply_filament_map(ms_xml, change)
            elif change["kind"] == "replate":
                model_xml, ms_xml = self._apply_replate(
                    model_xml, ms_xml, change)

        # Objects staying put follow their plate when the grid grew;
        # then the copies, removals and imports (which place their new
        # objects with the planned origins directly).
        model_xml = self._apply_plate_shifts(model_xml)
        entries_work, model_xml, ms_xml = self._apply_object_ops(
            entries_work, model_xml, ms_xml, slot_to_id)

        settings["different_settings_to_system"] = [
            ";".join(sorted(slot)) for slot in diff
        ]

        entries = entries_work
        entries[engine.MODEL_ENTRY] = model_xml.encode("utf-8")
        entries[engine.MODEL_SETTINGS_ENTRY] = ms_xml.encode("utf-8")
        entries[engine.PROJECT_SETTINGS_ENTRY] = json.dumps(
            settings, indent=4, ensure_ascii=False
        ).encode("utf-8")

        # The current version goes into the backups folder first; every
        # failure path below restores from this copy.
        backup = self._write_backup("save")
        try:
            engine.save_entries(self.path, entries)
        except OSError as e:
            # Most likely the file is locked - Bambu Studio has it open.
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
                f"open in Bambu Studio, close it there and save again. "
                + restored
            )
        problems = engine.verify(self.path, list(entries.keys()))
        if problems:
            shutil.copy2(backup, self.path)
            raise ProjectError(
                "Verification failed, so the original file was restored "
                "untouched. Problems: " + "; ".join(problems)
            )

        count = len(self.pending)
        for change in self.pending:
            self._drop_import_source(change)
        self.pending = []
        self._load()  # re-read from disk so the UI shows the saved truth
        plural = "s" if count != 1 else ""
        return [
            f"Saved and verified: {count} change{plural} written to "
            f"{self.path.name}.",
            f"The previous version is kept in the backups folder as "
            f"{backup.name} - see the Backups tab.",
        ]

    # ------------------------------------- preset-diff bookkeeping helpers

    def _diff_slots(self, settings):
        """different_settings_to_system as a list of sets of key names.

        Expected layout: slot 0 = print process, slots 1..n = one per
        project filament, last slot = machine. A missing or wrong-length
        list is normalised, keeping what can be kept.
        """
        n = self.filament_count()
        raw = settings.get("different_settings_to_system")
        if not isinstance(raw, list):
            raw = []
        slots = [set(s.split(";")) - {""} if isinstance(s, str) else set()
                 for s in raw]
        while len(slots) < n + 2:
            slots.append(set())
        return slots[: n + 2]

    def _register_diff(self, diff, n, change):
        """Register one changed key in the right diff slot(s)."""
        key = change["key"]
        if key == "different_settings_to_system":
            return  # editing the bookkeeping itself, hands off
        scope = self.scope_of(key)
        if scope == "machine":
            diff[n + 1].add(key)
        elif scope == "filament":
            for i in self._affected_filaments(n, change["old"],
                                              change["new"]):
                diff[1 + i].add(key)
        else:
            diff[0].add(key)

    @staticmethod
    def _affected_filaments(n, old, new):
        """Which filament slots (0-based) a list edit touched.

        Filament lists come in two shapes: one value per filament
        (length n), or one block of k values per filament (length k*n,
        e.g. the H2C's three nozzle-variant values). Anything else, or a
        scalar, conservatively marks every filament.
        """
        if (isinstance(old, list) and isinstance(new, list)
                and len(old) == len(new) and n > 0
                and len(old) % n == 0):
            k = len(old) // n
            changed = {pos // k for pos in range(len(old))
                       if old[pos] != new[pos]}
            if changed:
                return sorted(changed)
        return list(range(n))

    def _apply_preset(self, settings, diff, n, change):
        """Switch to a system preset: set its ID, write its resolved
        values into the project, and clear that scope's diff slot (the
        project now matches the system preset again)."""
        scope, name, values = change["scope"], change["name"], change["values"]
        if scope == "process":
            settings["print_settings_id"] = name
            diff[0] = set()
            self._write_preset_values(settings, values, None, n)
        elif scope == "machine":
            settings["printer_settings_id"] = name
            diff[n + 1] = set()
            self._write_preset_values(settings, values, None, n)
        elif scope == "filament":
            i = change["filament_index"]
            ids = settings.get("filament_settings_id")
            if isinstance(ids, list) and 0 <= i < len(ids):
                ids[i] = name
            diff[1 + i] = set()
            self._write_preset_values(settings, values, i, n)

    @staticmethod
    def _write_preset_values(settings, values, filament_index, n):
        """Copy resolved preset values into the project settings, adapting
        shapes (profile values are scalars or short lists; project values
        are scalars, per-filament lists, or per-filament blocks).

        Never invents keys: only keys already in the project are touched.
        Studio itself trusts the preset ID + diff list over these values,
        so an imperfect shape mapping here can only affect what
        AccessSlice displays, never the print.
        """
        for key, pvalue in values.items():
            if key not in settings:
                continue
            current = settings[key]
            plist = [str(v) for v in (pvalue if isinstance(pvalue, list)
                                      else [pvalue])]
            if not plist:
                continue
            if filament_index is not None and isinstance(current, list):
                length = len(current)
                if length == n:
                    current[filament_index] = plist[0]
                elif n > 0 and length % n == 0:
                    k = length // n
                    block = plist if len(plist) == k else [plist[0]] * k
                    current[filament_index * k:(filament_index + 1) * k] = block
            elif filament_index is not None:
                # Scalar filament key: shared across filaments; latest wins.
                settings[key] = plist[0]
            elif isinstance(current, list):
                if len(plist) == len(current):
                    settings[key] = plist
                else:
                    settings[key] = [plist[0]] * len(current)
            else:
                settings[key] = plist[0]

    # The three _apply_* helpers mirror the engine's command functions but
    # work on strings and raise ProjectError instead of printing/exiting.

    def _apply_item_transform(self, model_xml, objectid, fn, what):
        """Rewrite one object's 12-number item transform via fn."""
        hit = []

        def repl(m):
            if m.group("objectid") != objectid:
                return m.group(0)
            nums = [float(v) for v in m.group("transform").split()]
            if len(nums) < 12:
                return m.group(0)
            hit.append(True)
            new_tf = " ".join(engine.fmt_num(v) for v in fn(nums))
            return m.group(0).replace(m.group("transform"), new_tf)

        new_xml = engine.ITEM_RE.sub(repl, model_xml)
        if not hit:
            raise ProjectError(
                f"Could not {what} object {objectid}: its placement "
                "entry was not found.")
        return new_xml

    def _apply_transform_change(self, model_xml, change):
        # One implementation of the placement maths for previews and
        # saves alike: the shared _transform_step.
        return self._apply_item_transform(
            model_xml, change["objectid"],
            lambda nums: self._transform_step(change, nums),
            change["kind"])

    def _apply_extruder(self, ms_xml, change):
        # Same storage as a per-object override (a metadata line in the
        # object's block), so the same block-scoped editor applies -
        # including creating the block for an object that has none.
        return self._apply_object_setting(ms_xml, {
            "objectid": change["objectid"], "key": "extruder",
            "new": change["new"]})

    @staticmethod
    def _xml_attr(value):
        """Escape a string for use inside a double-quoted XML attribute."""
        return (str(value).replace("&", "&amp;").replace("<", "&lt;")
                .replace('"', "&quot;"))

    def _apply_object_setting(self, ms_xml, change):
        """Set, change, or remove one per-object override metadata line.

        The line lives in the object's block, before its <part>
        children, formatted exactly like Studio's own writes. An object
        with no model_settings block at all (seen with externally
        assembled projects) gets a minimal block created before the
        first plate.
        """
        objectid, key = change["objectid"], change["key"]
        m, head = self._object_block_head(objectid, ms_xml)
        if m is None:
            if change["new"] is None:
                return ms_xml   # nothing saved to remove
            plate = re.search(r"[ \t]*<plate>", ms_xml)
            if not plate:
                raise ProjectError(
                    "No plate structure found - save the project from "
                    "Bambu Studio once and try again.")
            block = (f'  <object id="{objectid}">\n'
                     f'    <metadata key="{key}" '
                     f'value="{self._xml_attr(change["new"])}"/>\n'
                     f'  </object>\n')
            return ms_xml[:plate.start()] + block + ms_xml[plate.start():]

        content = m.group(1)
        head, sep, tail = content.partition("<part")
        line_re = re.compile(
            rf'<metadata key="{re.escape(key)}" value="[^"]*"/>')
        if change["new"] is None:
            new_head = re.sub(
                rf'\n?[ \t]*<metadata key="{re.escape(key)}" '
                rf'value="[^"]*"/>', "", head)
            if new_head == head:
                return ms_xml   # already absent
        elif line_re.search(head):
            new_head = line_re.sub(
                f'<metadata key="{key}" '
                f'value="{self._xml_attr(change["new"])}"/>', head)
        else:
            line = (f'\n    <metadata key="{key}" '
                    f'value="{self._xml_attr(change["new"])}"/>')
            name_m = re.search(r'<metadata key="name" value="[^"]*"/>',
                               head)
            if name_m:
                new_head = (head[:name_m.end()] + line
                            + head[name_m.end():])
            else:
                new_head = line + head
        return (ms_xml[:m.start(1)] + new_head + sep + tail
                + ms_xml[m.end(1):])

    def _plate_block_match(self, ms_xml, plate_id):
        """The regex match of one plate's block, or None."""
        for m in re.finditer(r'<plate>(.*?)</plate>', ms_xml, re.S):
            if plate_id is None:
                return m
            pid = re.search(r'key="plater_id" value="([^"]*)"',
                            m.group(1))
            if pid and pid.group(1) == plate_id:
                return m
        return None

    def _apply_filament_map(self, ms_xml, change):
        value = " ".join(change["new"])
        m = self._plate_block_match(ms_xml, change.get("plate_id"))
        if m is None:
            raise ProjectError(
                "No plate metadata found - save the project from "
                "Bambu Studio once so the plate structure exists."
            )
        block = m.group(1)

        def set_meta(text, key, val):
            pat = re.compile(f'(<metadata key="{key}" value=")[^"]*(")')
            if pat.search(text):
                return pat.sub(rf"\g<1>{val}\g<2>", text)
            anchor = re.search(
                r'(<metadata key="plater_id" value="[^"]*"/>)', text)
            if not anchor:
                raise ProjectError(
                    "No plate metadata found - save the project from "
                    "Bambu Studio once so the plate structure exists."
                )
            return text.replace(
                anchor.group(1),
                anchor.group(1)
                + f'\n    <metadata key="{key}" value="{val}"/>',
            )

        block = set_meta(block, "filament_map_mode", "Manual")
        block = set_meta(block, "filament_maps", value)
        return ms_xml[:m.start(1)] + block + ms_xml[m.end(1):]

    def _apply_replate(self, model_xml, ms_xml, change):
        """Move an object to another plate: recentre its world position
        on the target plate and relocate its model_instance block."""
        objectid, target = change["objectid"], change["target"]
        model_xml = self._apply_item_transform(
            model_xml, objectid,
            lambda nums: self._transform_step(change, nums),
            "move to a plate")

        inst = None
        for m in re.finditer(r'[ \t]*<model_instance>.*?</model_instance>\n?',
                             ms_xml, re.S):
            if re.search(rf'key="object_id" value="{objectid}"/',
                         m.group(0)):
                inst = m
                break
        if inst is None:
            raise ProjectError(
                f"Object {objectid} has no plate membership entry - "
                "save the project from Bambu Studio once and try again.")
        block_text = inst.group(0).strip()
        ms_xml = ms_xml[:inst.start()] + ms_xml[inst.end():]
        tm = self._plate_block_match(ms_xml, target)
        if tm is None:
            raise ProjectError(f"No plate {target} in this project.")
        return model_xml, (ms_xml[:tm.end(1)]
                           + "  " + block_text + "\n  "
                           + ms_xml[tm.end(1):])
