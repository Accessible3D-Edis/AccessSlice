"""backends.bambu - the Bambu Studio backend, first of the five parts.

A thin facade over the modules that already implement Bambu support.
Deliberately thin: the parts stay importable and monkeypatchable as
flat modules (tests patch engine_driver.build_command and
newjob.build_merge_command; the facade reaches them at call time), and
the standalone tools\\edit_bambu_3mf.py CLI keeps working unchanged.
"""

import compat
import config_store
import engine_driver
import newjob
import preflight
import profiles
import settings_catalog
import studio_config
import slicer_project

from backends import bambu_texts
from backends.base import SlicerBackend


class BambuBackend(SlicerBackend):
    id = "bambu"
    display_name = "Bambu Studio"
    supported = True
    exe_names = ("bambu-studio.exe", "BambuStudio.exe")

    ProjectError = slicer_project.ProjectError
    catalog = settings_catalog
    profiles = profiles
    engine = engine_driver
    newjob = newjob
    compat = compat
    printer_switch = studio_config
    preflight = preflight    # warnings before slicing (Orca-family keys)
    texts = bambu_texts

    def open_project(self, path, slicer_exe=None):
        return slicer_project.ProjectSession(path, slicer_exe=slicer_exe)

    def preset_lists(self, slicer_exe, printer_name):
        """Bambu preset names carry their printer as a suffix, e.g.
        "Bambu PLA Matte @BBL H2C". The model token ("H2C") comes from
        the printer name ("Bambu Lab H2C 0.4 nozzle")."""
        model = ""
        if printer_name.startswith("Bambu Lab "):
            rest = printer_name[len("Bambu Lab "):].split()
            model = rest[0] if rest else ""

        def with_user(names, scope):
            # The user's own saved presets are never filtered away:
            # their names rarely carry a printer suffix, and they are
            # few.
            return names + [nm for nm
                            in profiles.list_user_presets(scope)
                            if nm not in names]

        def matching(scope):
            names = profiles.list_presets(slicer_exe, scope)
            if model:
                hits = [nm for nm in names if f"@BBL {model}" in nm]
                names = hits or names  # unknown model: show everything
            return with_user(names, scope)

        return {
            "process": matching("process"),
            "filament": matching("filament"),
            "machine": with_user(
                profiles.list_presets(slicer_exe, "machine"), "machine"),
        }

    def detect_exe(self):
        return config_store.detect_bambu_exe()

    def slicer_running(self, slicer_exe):
        return studio_config.studio_running(slicer_exe)
