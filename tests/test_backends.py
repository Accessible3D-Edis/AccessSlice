"""The backend registry and the Bambu backend's five-part surface."""

import shutil
import unittest

import support  # noqa: F401  (sys.path + sandboxed APPDATA)

import backends
import config_store
import engine_driver
import newjob
import profiles
import settings_catalog
import studio_config
from slicer_project import ProjectError


class TestRegistry(unittest.TestCase):
    def test_bambu_backend_found_by_id(self):
        backend = backends.get_backend("bambu")
        self.assertEqual(backend.id, "bambu")
        self.assertEqual(backend.display_name, "Bambu Studio")
        self.assertTrue(backend.supported)

    def test_unknown_id_refused_in_plain_words(self):
        with self.assertRaises(backends.BackendError) as ctx:
            backends.get_backend("cura")
        self.assertIn("no support", str(ctx.exception))
        self.assertIn("Bambu Studio", str(ctx.exception))

    def test_registry_matches_the_picker(self):
        # Every SLICERS entry marked supported must have a backend
        # whose display name matches the picker's, and every backend
        # must appear in the picker - the two lists cannot drift.
        picker = {sid: (name, ok) for sid, name, ok in config_store.SLICERS}
        for sid in backends.supported_ids():
            self.assertIn(sid, picker)
            name, ok = picker[sid]
            self.assertTrue(ok, f"{sid} has a backend but is disabled")
            self.assertEqual(backends.get_backend(sid).display_name, name)
        for sid, (name, ok) in picker.items():
            if ok:
                self.assertIn(sid, backends.supported_ids(),
                              f"{sid} enabled in the picker, no backend")


class TestBambuSurface(unittest.TestCase):
    """The five parts are present and are the proven modules."""

    def setUp(self):
        self.backend = backends.get_backend("bambu")

    def test_part1_project_format(self):
        self.assertIs(self.backend.ProjectError, ProjectError)
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        project = support.copy_test_project(sandbox)
        session = self.backend.open_project(project)
        self.assertTrue(session.objects())

    def test_part2_catalog(self):
        self.assertIs(self.backend.catalog, settings_catalog)
        self.assertTrue(self.backend.catalog.TABS)
        self.assertTrue(callable(self.backend.catalog.label_for))

    def test_part3_profiles(self):
        self.assertIs(self.backend.profiles, profiles)
        self.assertTrue(callable(self.backend.profiles.list_presets))

    def test_part4_engine_driver(self):
        self.assertIs(self.backend.engine, engine_driver)
        self.assertIs(self.backend.newjob, newjob)
        self.assertIs(self.backend.printer_switch, studio_config)
        self.assertEqual(self.backend.compat.series("02.08.02.61"),
                         "02.08")

    def test_part4_detect_exe_is_config_stores(self):
        # Same answer as the function it wraps (None on this machine
        # or a real path - either way they must agree).
        self.assertEqual(self.backend.detect_exe(),
                         config_store.detect_bambu_exe())

    def test_part3_preset_lists_filter_by_printer(self):
        # The Bambu naming grammar (moved out of the HTTP layer):
        # process and filament filtered to the printer's model token,
        # machine never filtered.
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        exe = support.make_fake_studio(sandbox)
        lists = self.backend.preset_lists(
            exe, "Bambu Lab H2C 0.4 nozzle")
        self.assertIn("0.20mm Standard @BBL H2C", lists["process"])
        self.assertNotIn("0.20mm Standard @BBL X1C", lists["process"])
        self.assertIn("Bambu PLA Matte @BBL H2C", lists["filament"])
        self.assertNotIn("Bambu PLA Matte @BBL X1C", lists["filament"])
        self.assertIn("Bambu Lab X1 Carbon 0.4 nozzle",
                      lists["machine"])

    def test_part3_preset_lists_unknown_model_shows_everything(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        exe = support.make_fake_studio(sandbox)
        lists = self.backend.preset_lists(exe, "Some Future Printer")
        self.assertIn("0.20mm Standard @BBL H2C", lists["process"])
        self.assertIn("0.20mm Standard @BBL X1C", lists["process"])

    def test_part4_engine_seam_still_monkeypatchable(self):
        # Tests patch engine_driver.build_command; the backend must
        # see the patch (it reaches the module at call time).
        original = engine_driver.build_command
        try:
            engine_driver.build_command = lambda *a, **k: ["patched"]
            self.assertEqual(
                self.backend.engine.build_command("x", "y", 0, "z"),
                ["patched"])
        finally:
            engine_driver.build_command = original


if __name__ == "__main__":
    unittest.main()
