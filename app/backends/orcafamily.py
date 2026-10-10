"""backends.orcafamily - one SlicerBackend class for Orca-family forks.

OrcaSlicer and Anycubic Slicer Next are configured through a Flavour
(orca_family.Flavour); everything else is shared: the Bambu project
session with the flavour's profiles and compat, the Creality engine
driver (exit code + slice_info.config judgement, which is what these
CLIs produce), the preset-or-donor project builder, Bambu's settings
catalog and pre-flight key table.
"""

import subprocess
from pathlib import Path

import creality_engine
import noconsole
import orca_family
import preflight
import settings_catalog
import slicer_project

from backends.base import SlicerBackend

_NOZZLE_TAIL = orca_family._NOZZLE_TAIL


class OrcaFamilyBackend(SlicerBackend):

    ProjectError = slicer_project.ProjectError
    catalog = settings_catalog
    engine = creality_engine
    printer_switch = None
    lan = None
    preflight = preflight

    def __init__(self, flavour):
        self.flavour = flavour
        self.id = flavour.id
        self.display_name = flavour.display_name
        self.supported = True
        self.exe_names = flavour.exe_names
        self.profiles = orca_family.FamilyProfiles(flavour)
        self.compat = orca_family.FamilyCompat(flavour)
        self.newjob = orca_family.FamilyNewJob(flavour, self.profiles)
        self.texts = flavour.texts

    def open_project(self, path, slicer_exe=None):
        return slicer_project.ProjectSession(
            path, slicer_exe=slicer_exe,
            profiles_mod=self.profiles, compat_mod=self.compat)

    def detect_exe(self):
        for candidate in self.flavour.candidates:
            if Path(candidate).is_file():
                return candidate
        return None

    def slicer_running(self, slicer_exe):
        exe_name = (Path(slicer_exe).name if slicer_exe
                    else self.flavour.exe_names[0])
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                capture_output=True, text=True, timeout=10,
                **noconsole.hidden()).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return exe_name.lower() in out.lower()

    def preset_lists(self, slicer_exe, printer_name):
        """Two naming conventions live in this family: Bambu's
        "@BBL X1C" model token and the fork vendors' full model name
        ("@Anycubic Kobra S1 0.4 nozzle", "@Creality K2 Plus 0.4
        nozzle"). Try the full-name rule, then Bambu's, then show
        everything."""
        model = _NOZZLE_TAIL.sub("", printer_name or "").strip()
        bbl_token = ""
        if printer_name.startswith("Bambu Lab "):
            rest = printer_name[len("Bambu Lab "):].split()
            bbl_token = rest[0] if rest else ""

        def with_user(names, scope):
            return names + [nm for nm in self.profiles.list_user_presets(scope)
                            if nm not in names]

        def matching(scope):
            names = self.profiles.list_presets(slicer_exe, scope)
            hits = [nm for nm in names if model and f"@{model}" in nm]
            if not hits and bbl_token:
                hits = [nm for nm in names if f"@BBL {bbl_token}" in nm]
            return with_user(hits or names, scope)

        return {
            "process": matching("process"),
            "filament": matching("filament"),
            "machine": with_user(self.profiles.list_presets(slicer_exe,
                                                            "machine"),
                                 "machine"),
        }
