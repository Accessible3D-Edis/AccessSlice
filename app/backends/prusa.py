"""backends.prusa - the PrusaSlicer backend (the second project format).

Built 2026-09-02 on the facts probed the same day (docs\\log.md):
PrusaSlicer 2.9.6, the Core One installed with its HF0.4 nozzle
variant, real 2.9.4-saved projects on disk, a Core One project
assembled from the CLI's own presets sliced headless. The first
backend outside the Orca family: its own format (prusa_format,
prusa_project), its own catalog (prusa_catalog), its own preset
library reader (prusa_profiles), engine driver (prusa_engine) and
project builder (prusa_newjob). No conf-file printer switching
(printer_switch None): the project carries the printer, and the
Start page's printer choice feeds the project builder. The LAN send
(prusalink, added 2026-09-07) talks to the Core One over PrusaLink
with the password from the printer's screen, remembered in config.

The Prusa printer AccessSlice is tested on is the Core One.
Preset lists follow what is installed in PrusaSlicer, so the Core
One is what appears.
"""

import subprocess
from pathlib import Path

import noconsole
import prusa_catalog
import prusa_compat
import prusa_engine
import prusa_newjob
import prusa_profiles
import preflight
import prusa_project
import prusalink

from backends import prusa_texts
from backends.base import SlicerBackend

_CANDIDATES = [
    r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
    r"C:\Program Files (x86)\Prusa3D\PrusaSlicer\prusa-slicer.exe",
]

# The pre-flight checks read settings through a key table; these are
# PrusaSlicer's names for the same facts.
PREFLIGHT_KEYS = {
    "enable_support": "support_material",
    "threshold": "support_material_threshold",
    "sequence": "complete_objects",
    "height_to_rod": "extruder_clearance_height",
    "height_to_lid": "__none__",
    "dist_to_rod": "__none__",
    "radius": "extruder_clearance_radius",
    "spiral": "spiral_vase",
    "printable_height": "max_print_height",
    "brim_type": "brim_type",
    "raft_layers": "raft_layers",
}


class _PrusaPreflight:
    """preflight with PrusaSlicer's keys. complete_objects is "1"/"0"
    where the Orca family says "by object", so the sequence value is
    translated before the shared checks see it."""

    @staticmethod
    def warnings(session):
        return preflight.warnings(_SequenceView(session), PREFLIGHT_KEYS)


class _SequenceView:
    """A thin view of the session whose complete_objects reads as the
    Orca-family "by object" / "by layer" words."""

    def __init__(self, session):
        self._s = session

    def effective_setting(self, key):
        value = self._s.effective_setting(key)
        if key == "complete_objects":
            return (preflight.BY_OBJECT if str(value).strip() == "1"
                    else "by layer")
        return value

    def __getattr__(self, name):
        return getattr(self._s, name)


class PrusaBackend(SlicerBackend):
    id = "prusa"
    display_name = "PrusaSlicer"
    supported = True
    exe_names = ("prusa-slicer.exe", "prusa-slicer-console.exe")

    ProjectError = prusa_project.ProjectError
    catalog = prusa_catalog
    profiles = prusa_profiles
    engine = prusa_engine
    newjob = prusa_newjob
    compat = prusa_compat
    printer_switch = None
    lan = prusalink   # direct Core One send and status over PrusaLink
    preflight = _PrusaPreflight
    texts = prusa_texts

    def open_project(self, path, slicer_exe=None):
        return prusa_project.PrusaProjectSession(path, slicer_exe=slicer_exe)

    def detect_exe(self):
        for candidate in _CANDIDATES:
            if Path(candidate).is_file():
                return candidate
        return None

    def slicer_running(self, slicer_exe):
        exe_name = (Path(slicer_exe).name if slicer_exe
                    else "prusa-slicer.exe")
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                capture_output=True, text=True, timeout=10,
                **noconsole.hidden()).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return exe_name.lower() in out.lower()

    def preset_lists(self, slicer_exe, printer_name):
        """Print and filament presets carry the printer model as a
        suffix ("0.20mm SPEED @COREONE HF0.4", "Prusament PLA @COREONE
        HF0.4"); the model token comes from the printer preset itself.
        Machine presets are the printers installed in PrusaSlicer."""
        model = variant = ""
        try:
            resolved = prusa_profiles.resolve_preset(
                slicer_exe, "machine", printer_name)
            model = resolved.get("printer_model", "")
            variant = resolved.get("printer_variant", "")
        except prusa_profiles.ProfileError:
            pass

        def with_user(names, scope):
            return names + [nm for nm in prusa_profiles.list_user_presets(scope)
                            if nm not in names]

        def matching(scope):
            try:
                names = prusa_profiles.list_presets(slicer_exe, scope)
            except prusa_profiles.ProfileError:
                names = []
            if model:
                hits = [nm for nm in names if f"@{model}" in nm]
                if variant:
                    # This nozzle's own presets, plus the nozzle-agnostic
                    # ones ("... @COREONE" with nothing after the model).
                    close = [nm for nm in hits
                             if nm.endswith(f"@{model} {variant}")
                             or nm.endswith(f"@{model}")]
                    hits = close or hits
                names = hits or names
            return with_user(names, scope)

        try:
            machines = prusa_profiles.list_presets(slicer_exe, "machine")
        except prusa_profiles.ProfileError:
            machines = []
        return {
            "process": matching("process"),
            "filament": matching("filament"),
            "machine": with_user(machines, "machine"),
        }
