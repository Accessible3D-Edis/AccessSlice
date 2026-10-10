"""backends.creality - the Creality Print backend (Orca family).

Built 2026-09-01 on the facts probed the same day (docs\\log.md):
Creality Print 7.2.2 writes the Bambu project format nearly verbatim,
its CLI slices a genuinely saved project headless, and the report
numbers live in the produced file's slice_info.config. What it cannot
do: import a raw model file on the command line (segfaults upstream),
so newjob stays None and the from-model-file flow is refused with
guidance. No conf-file printer switching either (printer_switch None):
the project itself carries the printer, and new projects are made in
Creality Print's own GUI once.
"""

import re
import subprocess
from pathlib import Path

import creality_compat
import creality_engine
import creality_lan
import creality_profiles
import noconsole
import preflight
import settings_catalog
import slicer_project

from backends import creality_texts
from backends.base import SlicerBackend

# Standard install locations; the folder name keeps its first series
# ("Creality Print 7.0") while the updater updates the exe in place
# (a 7.2.2 exe lives in the 7.0 folder on this machine), so version
# questions are answered by the exe or the files, never the folder.
_CANDIDATE_GLOBS = [
    (r"C:\Program Files\Creality", "Creality Print*"),
    (r"C:\Program Files (x86)\Creality", "Creality Print*"),
]

# "Creality K2 Plus 0.4 nozzle" -> model "Creality K2 Plus" (the part
# preset suffixes name: "0.20mm Standard @Creality K2 Plus 0.4 nozzle").
_NOZZLE_TAIL = re.compile(r"\s+\d+(\.\d+)?\s+nozzle\s*$")


class CrealityBackend(SlicerBackend):
    id = "creality"
    display_name = "Creality Print"
    supported = True
    exe_names = ("CrealityPrint.exe",)

    ProjectError = slicer_project.ProjectError
    catalog = settings_catalog
    profiles = creality_profiles
    engine = creality_engine
    newjob = None            # raw-model import segfaults upstream
    compat = creality_compat
    printer_switch = None    # no conf-file printer selection in v1
    lan = creality_lan       # direct K-series LAN send and status
    usb_wants_plain_gcode = True  # the K series reads .gcode from a drive
    preflight = preflight    # warnings before slicing (Orca-family keys)
    texts = creality_texts

    def open_project(self, path, slicer_exe=None):
        return slicer_project.ProjectSession(
            path, slicer_exe=slicer_exe,
            profiles_mod=creality_profiles,
            compat_mod=creality_compat)

    def detect_exe(self):
        for root, pattern in _CANDIDATE_GLOBS:
            root = Path(root)
            if not root.is_dir():
                continue
            for folder in sorted(root.glob(pattern), reverse=True):
                exe = folder / "CrealityPrint.exe"
                if exe.is_file():
                    return str(exe)
        return None

    def slicer_running(self, slicer_exe):
        exe_name = (Path(slicer_exe).name if slicer_exe
                    else "CrealityPrint.exe")
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                capture_output=True, text=True, timeout=10,
                **noconsole.hidden()).stdout
        except (OSError, subprocess.SubprocessError):
            return False  # cannot tell; do not block the user on a hunch
        return exe_name.lower() in out.lower()

    def preset_lists(self, slicer_exe, printer_name):
        """Creality preset names carry the printer model as a suffix,
        e.g. "Hyper PLA @Creality K2 Plus 0.4 nozzle"."""
        model = _NOZZLE_TAIL.sub("", printer_name or "").strip()

        def with_user(names, scope):
            return names + [nm for nm
                            in creality_profiles.list_user_presets(scope)
                            if nm not in names]

        def matching(scope):
            names = creality_profiles.list_presets(slicer_exe, scope)
            if model:
                hits = [nm for nm in names if f"@{model}" in nm]
                names = hits or names  # unknown model: show everything
            return with_user(names, scope)

        return {
            "process": matching("process"),
            "filament": matching("filament"),
            "machine": with_user(
                creality_profiles.list_presets(slicer_exe, "machine"),
                "machine"),
        }
