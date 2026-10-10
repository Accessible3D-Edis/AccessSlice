"""Tests for profiles.py - preset library reading and key scoping.

Scope classification feeds the different_settings_to_system slots; a
wrong scope means Bambu Studio silently discards an edit, so both the
library-learned path and the no-library fallback are covered.

Runs fully offline against the fake library; a few integration checks
run extra only when a real Bambu Studio install is present.
"""

import json
import os
import shutil
import unittest

import support
import profiles


class FakeLibraryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sandbox = support.make_sandbox()
        cls.exe = str(support.make_fake_studio(cls.sandbox))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def test_list_presets_hides_non_instantiable_bases(self):
        names = profiles.list_presets(self.exe, "process")
        self.assertIn("0.20mm Standard @BBL H2C", names)
        self.assertIn("Test Strength @BBL H2C", names)
        self.assertNotIn("fdm_process_common", names,
                         "base profiles are building blocks, not presets")

    def test_resolve_merges_the_inheritance_chain(self):
        merged = profiles.resolve_preset(self.exe, "process",
                                         "Test Strength @BBL H2C")
        self.assertEqual(merged["wall_loops"], "6")      # child wins
        self.assertEqual(merged["enable_support"], "0")  # inherited
        self.assertNotIn("inherits", merged)             # meta dropped

    def test_resolve_unknown_preset_raises(self):
        with self.assertRaises(profiles.ProfileError):
            profiles.resolve_preset(self.exe, "process", "No Such Preset")

    def test_scope_learned_from_the_library(self):
        self.assertEqual(profiles.scope_of(self.exe, "wall_loops"),
                         "process")
        self.assertEqual(profiles.scope_of(self.exe, "nozzle_temperature"),
                         "filament")
        self.assertEqual(profiles.scope_of(self.exe, "machine_max_speed_x"),
                         "machine")


class FallbackScopeTests(unittest.TestCase):
    """No profile library readable: the prefix heuristic takes over."""

    def test_fallback_scopes(self):
        self.assertEqual(profiles.scope_of(None, "nozzle_temperature"),
                         "filament")
        self.assertEqual(profiles.scope_of(None, "fan_max_speed"),
                         "filament")
        self.assertEqual(profiles.scope_of(None, "machine_max_speed_x"),
                         "machine")
        self.assertEqual(profiles.scope_of(None, "nozzle_diameter"),
                         "machine")
        self.assertEqual(profiles.scope_of(None, "wall_loops"), "process")

    def test_no_library_lists_nothing_and_resolves_nothing(self):
        self.assertEqual(profiles.list_presets(None, "process"), [])
        with self.assertRaises(profiles.ProfileError):
            profiles.resolve_preset(None, "process", "anything")


@unittest.skipUnless(support.real_studio_exe(),
                     "real Bambu Studio not installed")
class RealStudioIntegrationTests(unittest.TestCase):
    """Read-only checks against the genuine installed profile library."""

    @classmethod
    def setUpClass(cls):
        cls.exe = support.real_studio_exe()

    def test_the_real_library_lists_machines(self):
        names = profiles.list_presets(self.exe, "machine")
        self.assertTrue(names, "no machine presets found in the real "
                               "library - did Studio's layout change?")

    def test_the_test_projects_profile_resolves(self):
        merged = profiles.resolve_preset(self.exe, "process",
                                         "0.20mm Standard @BBL H2C")
        self.assertIn("wall_loops", merged)

    def test_real_library_scopes_match_expectations(self):
        self.assertEqual(profiles.scope_of(self.exe, "nozzle_temperature"),
                         "filament")
        self.assertEqual(profiles.scope_of(self.exe, "wall_loops"),
                         "process")


class UserPresetTests(unittest.TestCase):
    """Presets the user saved in Studio: %APPDATA%\\BambuStudio\\user.

    They have no "instantiation" flag, and their "inherits" chains lead
    into the system library or into other user presets. Format probed on
    the real machine 2026-08-30 (user\\<account id>\\<scope>\\<name>.json
    with a "name" field).
    """

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.exe = str(support.make_fake_studio(self.sandbox))
        self._old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(self.sandbox / "AppData")
        self.addCleanup(self._restore_appdata)
        folder = (self.sandbox / "AppData" / "BambuStudio" / "user"
                  / "1234567890" / "filament")
        folder.mkdir(parents=True)
        (folder / "My Special PLA.json").write_text(json.dumps({
            "name": "My Special PLA",
            "inherits": "Bambu PLA Matte @BBL H2C",
            "nozzle_temperature": ["215"],
        }), encoding="utf-8")
        (folder / "My Special PLA hotter.json").write_text(json.dumps({
            "name": "My Special PLA hotter",
            "inherits": "My Special PLA",
            "fan_max_speed": ["80"],
        }), encoding="utf-8")

    def _restore_appdata(self):
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata

    def test_user_presets_are_listed_per_scope(self):
        self.assertEqual(profiles.list_user_presets("filament"),
                         ["My Special PLA", "My Special PLA hotter"])
        self.assertEqual(profiles.list_user_presets("machine"), [])

    def test_resolving_walks_from_user_into_the_system_library(self):
        merged = profiles.resolve_preset(self.exe, "filament",
                                         "My Special PLA")
        self.assertEqual(merged["nozzle_temperature"], ["215"])  # own
        self.assertEqual(merged["fan_max_speed"], ["100"])       # system

    def test_a_user_preset_may_inherit_another_user_preset(self):
        merged = profiles.resolve_preset(self.exe, "filament",
                                         "My Special PLA hotter")
        self.assertEqual(merged["fan_max_speed"], ["80"])
        self.assertEqual(merged["nozzle_temperature"], ["215"])

    def test_unknown_names_still_raise_a_friendly_error(self):
        with self.assertRaises(profiles.ProfileError):
            profiles.resolve_preset(self.exe, "filament", "No Such Preset")

    def test_no_user_folder_means_empty_lists_not_errors(self):
        os.environ["APPDATA"] = str(self.sandbox / "NoSuchAppData")
        self.assertEqual(profiles.list_user_presets("filament"), [])
        # System resolving is unaffected.
        merged = profiles.resolve_preset(self.exe, "filament",
                                         "Bambu PLA Matte @BBL H2C")
        self.assertEqual(merged["nozzle_temperature"], ["230"])


if __name__ == "__main__":
    unittest.main()
