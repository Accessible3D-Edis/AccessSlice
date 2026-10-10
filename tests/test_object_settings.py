"""Tests for per-object setting overrides.

Ground truth is the committed specimen (in testdata):
cane-per-object-overrides-saved-from-bambu-studio.3mf,
saved from real Bambu Studio 2.8 on 2026-08-27 with three overrides set
through the GUI: sparse infill 33% (plus the skeleton/skin companion
keys Studio writes with it), wall loops 5, supports enabled - all on
object id 2.
"""

import re
import shutil
import unittest

import support
import edit_bambu_3mf as engine
from slicer_project import ProjectError, ProjectSession

SPECIMEN = (support.REPO_ROOT / "testdata"
            / "cane-per-object-overrides-saved-from-bambu-studio.3mf")

ITEM_LINE = ('<item objectid="2" '
             'p:UUID="00000002-b1ec-4553-aec9-835e5b724bb4" '
             'transform="1 0 0 0 1 0 0 0 1 165 160 38.4231987" '
             'printable="1"/>')


class SpecimenCase(unittest.TestCase):
    """Base: sandbox copies of the specimen and the plain test project."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.specimen = self.sandbox / "specimen.3mf"
        shutil.copy2(SPECIMEN, self.specimen)
        self.plain = support.copy_test_project(self.sandbox)


class ReadingTests(SpecimenCase):

    def test_the_specimen_overrides_read_exactly_as_studio_wrote_them(self):
        s = ProjectSession(self.specimen)
        self.assertEqual(s.object_overrides("2"), {
            "enable_support": "1",
            "sparse_infill_density": "33%",
            "skeleton_infill_density": "33%",
            "skin_infill_density": "33%",
            "wall_loops": "5",
        })
        # name and extruder are structure, never overrides.
        obj = s.objects()[0]
        self.assertEqual(obj["name"], "cane.stl")
        self.assertEqual(obj["extruder"], "1")

    def test_a_plain_project_has_no_overrides(self):
        s = ProjectSession(self.plain)
        self.assertEqual(s.object_overrides("2"), {})


class StagingTests(SpecimenCase):

    def test_stage_change_and_effective_overlay(self):
        s = ProjectSession(self.specimen)
        self.assertTrue(s.stage_object_setting("2", "wall_loops", "4"))
        self.assertEqual(s.effective_object_overrides("2")["wall_loops"],
                         "4")
        self.assertEqual(s.object_overrides("2")["wall_loops"], "5",
                         "staging must not touch the loaded state")
        # Back to the saved value clears the staged change.
        self.assertFalse(s.stage_object_setting("2", "wall_loops", "5"))
        self.assertEqual(s.pending, [])

    def test_same_value_is_a_noop(self):
        s = ProjectSession(self.specimen)
        self.assertFalse(s.stage_object_setting("2", "wall_loops", "5"))

    def test_unknown_key_and_list_key_are_refused(self):
        s = ProjectSession(self.plain)
        with self.assertRaises(ProjectError):
            s.stage_object_setting("2", "no_such_key", "1")
        with self.assertRaises(ProjectError):
            s.stage_object_setting("2", "nozzle_temperature", "220")

    def test_unknown_object_is_refused(self):
        s = ProjectSession(self.plain)
        with self.assertRaises(ProjectError):
            s.stage_object_setting("99", "wall_loops", "3")

    def test_stage_remove_and_remove_of_absent_key(self):
        s = ProjectSession(self.specimen)
        self.assertTrue(s.stage_object_setting("2", "wall_loops", None))
        self.assertNotIn("wall_loops", s.effective_object_overrides("2"))
        self.assertFalse(s.stage_object_setting("2", "brim_type", None),
                         "removing an override that is not set is a noop")


class SaveTests(SpecimenCase):

    def test_new_override_saves_without_touching_globals(self):
        s = ProjectSession(self.plain)
        diff_before = list(s.settings["different_settings_to_system"])
        self.assertTrue(s.stage_object_setting("2", "wall_loops", "7"))
        lines = s.save()
        self.assertIn("Saved and verified", lines[0])
        fresh = ProjectSession(self.plain)
        self.assertEqual(fresh.object_overrides("2")["wall_loops"], "7")
        self.assertEqual(fresh.settings["wall_loops"], "2",
                         "the global value must be untouched")
        self.assertEqual(fresh.settings["different_settings_to_system"],
                         diff_before,
                         "object overrides never enter the preset-diff "
                         "bookkeeping")
        # The written line matches Studio's own format, in the object
        # block right after the name metadata.
        ms = engine.load_entries(self.plain)[
            engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
        self.assertRegex(ms, re.compile(
            r'<object id="2">\s*<metadata key="name" value="[^"]*"/>'
            r'\s*<metadata key="wall_loops" value="7"/>', re.S))

    def test_changing_an_existing_override_saves(self):
        s = ProjectSession(self.specimen)
        s.stage_object_setting("2", "wall_loops", "4")
        s.save()
        fresh = ProjectSession(self.specimen)
        overrides = fresh.object_overrides("2")
        self.assertEqual(overrides["wall_loops"], "4")
        self.assertEqual(overrides["sparse_infill_density"], "33%",
                         "other overrides must be untouched")

    def test_removing_an_override_saves(self):
        s = ProjectSession(self.specimen)
        s.stage_object_setting("2", "sparse_infill_density", None)
        s.save()
        fresh = ProjectSession(self.specimen)
        overrides = fresh.object_overrides("2")
        self.assertNotIn("sparse_infill_density", overrides)
        self.assertEqual(overrides["skeleton_infill_density"], "33%",
                         "the session removes only the named key - "
                         "companions are the server layer's job")
        self.assertEqual(overrides["wall_loops"], "5")

    def test_override_on_an_object_without_a_settings_block(self):
        # Externally assembled projects can have build items with no
        # model_settings block at all - a minimal block gets created.
        entries = engine.load_entries(self.plain)
        model = entries[engine.MODEL_ENTRY].decode("utf-8")
        model = model.replace(
            ITEM_LINE,
            ITEM_LINE + "\n  "
            + ITEM_LINE.replace('objectid="2"', 'objectid="3"')
                       .replace("00000002", "00000003"))
        entries[engine.MODEL_ENTRY] = model.encode("utf-8")
        engine.save_entries(self.plain, entries)

        s = ProjectSession(self.plain)
        self.assertTrue(s.stage_object_setting("3", "enable_support", "1"))
        s.save()
        fresh = ProjectSession(self.plain)
        self.assertEqual(fresh.object_overrides("3"),
                         {"enable_support": "1"})
        self.assertEqual(fresh.object_overrides("2"), {},
                         "the existing object must be untouched")


if __name__ == "__main__":
    unittest.main()
