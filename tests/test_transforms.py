"""Tests for object rotation and scaling on the Placement tab.

The 3MF item transform is a row-vector matrix (nine rotation/scale
numbers, then the translation). Rotation is relative, about the
object's own centre around the vertical axis; scaling is absolute in
percent of the imported size (derived from the determinant).

The committed test project's object sits at identity rotation/scale at
(165, 160), which makes the expected matrices exact.
"""

import math
import shutil
import unittest

import support
import edit_bambu_3mf as engine
from slicer_project import ProjectError, ProjectSession


def raw_nums(path, objectid="2"):
    entries = engine.load_entries(path)
    model = entries[engine.MODEL_ENTRY].decode("utf-8")
    for oid, nums in engine.find_items(model):
        if oid == objectid:
            return nums
    return None


class TransformCase(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)

    def session(self):
        return ProjectSession(self.project)


class FactsTests(TransformCase):

    def test_the_untouched_object_reads_as_simple_identity(self):
        facts = self.session().object_transform_facts("2")
        self.assertTrue(facts["simple"])
        self.assertAlmostEqual(facts["scale_percent"], 100.0, places=3)
        self.assertAlmostEqual(facts["z_rotation_degrees"], 0.0, places=3)

    def test_a_3d_rotated_object_reports_size_but_no_z_angle(self):
        # Rotate 90 degrees about the X axis (row-vector Rx matrix).
        entries = engine.load_entries(self.project)
        model = entries[engine.MODEL_ENTRY].decode("utf-8")
        model = model.replace(
            'transform="1 0 0 0 1 0 0 0 1 165 160 38.4231987"',
            'transform="1 0 0 0 0 1 0 -1 0 165 160 38.4231987"')
        entries[engine.MODEL_ENTRY] = model.encode("utf-8")
        engine.save_entries(self.project, entries)
        facts = self.session().object_transform_facts("2")
        self.assertFalse(facts["simple"])
        self.assertIsNone(facts["z_rotation_degrees"])
        self.assertAlmostEqual(facts["scale_percent"], 100.0, places=3)

    def test_unknown_object_has_no_facts(self):
        self.assertIsNone(self.session().object_transform_facts("99"))


class StagingTests(TransformCase):

    def test_zero_rotation_and_current_size_are_noops(self):
        s = self.session()
        self.assertFalse(s.stage_rotate("2", "0"))
        self.assertFalse(s.stage_scale("2", "100"))
        self.assertEqual(s.pending, [])

    def test_restaging_replaces_instead_of_accumulating(self):
        s = self.session()
        s.stage_rotate("2", "45")
        s.stage_rotate("2", "90")
        rotations = [c for c in s.pending if c["kind"] == "rotate"]
        self.assertEqual(len(rotations), 1)
        self.assertEqual(rotations[0]["degrees"], 90.0)

    def test_bad_values_are_friendly_errors(self):
        s = self.session()
        with self.assertRaises(ProjectError):
            s.stage_rotate("2", "a bit")
        with self.assertRaises(ProjectError):
            s.stage_scale("2", "-50")
        with self.assertRaises(ProjectError):
            s.stage_rotate("99", "45")


class StagedVisibilityTests(TransformCase):
    """Edis's 2026-08-27 finding: staged rotations and scalings must be
    visible on the Placement card BEFORE the save, marked as staged."""

    def test_staged_transforms_show_on_the_placement_card(self):
        import webui
        s = self.session()
        s.stage_rotate("2", "45")
        s.stage_scale("2", "150")
        html = webui.render_placement_tab(s)
        self.assertIn("A turn by 45 degrees is staged, not saved", html)
        self.assertIn("saving makes it 45.0 degrees", html)
        self.assertIn("A size change to 150% is staged, not saved", html)
        # The size field prefills with the staged target, so re-posting
        # the untouched form never re-stages.
        self.assertIn('name="scale__2" value="150"', html)

    def test_saved_state_shows_plainly_without_staged_wording(self):
        import webui
        s = self.session()
        html = webui.render_placement_tab(s)
        self.assertNotIn("staged, not saved", html)
        self.assertIn("Currently rotated 0.0 degrees", html)


class SaveTests(TransformCase):

    def test_rotate_90_writes_the_exact_matrix(self):
        s = self.session()
        self.assertTrue(s.stage_rotate("2", "90"))
        s.save()
        nums = raw_nums(self.project)
        expected = [0, 1, 0, -1, 0, 0, 0, 0, 1, 165, 160, 38.4231987]
        for got, want in zip(nums, expected):
            self.assertAlmostEqual(got, want, places=6)
        facts = ProjectSession(self.project).object_transform_facts("2")
        self.assertAlmostEqual(facts["z_rotation_degrees"], 90.0,
                               places=3)
        self.assertAlmostEqual(facts["scale_percent"], 100.0, places=3)

    def test_scale_to_200_percent_keeps_position(self):
        s = self.session()
        self.assertTrue(s.stage_scale("2", "200"))
        s.save()
        nums = raw_nums(self.project)
        self.assertAlmostEqual(nums[0], 2.0, places=6)
        self.assertAlmostEqual(nums[4], 2.0, places=6)
        self.assertAlmostEqual(nums[8], 2.0, places=6)
        self.assertAlmostEqual(nums[9], 165.0, places=6)
        self.assertAlmostEqual(nums[10], 160.0, places=6)
        facts = ProjectSession(self.project).object_transform_facts("2")
        self.assertAlmostEqual(facts["scale_percent"], 200.0, places=2)

    def test_move_rotate_and_scale_combine_in_one_save(self):
        s = self.session()
        s.stage_move("2", 100.0, 120.0)
        s.stage_rotate("2", "45")
        s.stage_scale("2", "150")
        s.save()
        fresh = ProjectSession(self.project)
        obj = fresh.objects()[0]
        self.assertAlmostEqual(obj["x"], 100.0, places=4)
        self.assertAlmostEqual(obj["y"], 120.0, places=4)
        facts = fresh.object_transform_facts("2")
        self.assertTrue(facts["simple"])
        self.assertAlmostEqual(facts["z_rotation_degrees"], 45.0,
                               places=2)
        self.assertAlmostEqual(facts["scale_percent"], 150.0, places=2)

    def test_scaling_back_down_returns_to_the_original(self):
        s = self.session()
        s.stage_scale("2", "200")
        s.save()
        s.stage_scale("2", "100")
        s.save()
        facts = ProjectSession(self.project).object_transform_facts("2")
        self.assertAlmostEqual(facts["scale_percent"], 100.0, places=2)
        nums = raw_nums(self.project)
        self.assertAlmostEqual(nums[0], 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
