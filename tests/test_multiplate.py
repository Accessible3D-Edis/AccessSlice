"""Tests for multi-plate support.

Two levels of ground truth: a synthetic two-plate copy of the
committed test project (built by support.add_empty_plate, always
available), and - when present on this machine - the two real
Studio-saved Jack Skellington projects with 36 and 21 plates that the
grid math was cracked against (2026-08-27: stride = plate width x
1.25, ceil(sqrt(n)) columns, membership in model_instance blocks).
"""

import json
import os
import shutil
import unittest
from pathlib import Path

import support
import webui
import edit_bambu_3mf as engine
from slicer_project import ProjectError, ProjectSession

# The large real specimens are not in the repository; a developer who
# has them points ACCESSSLICE_SPECIMENS at their folder.
JACK_DIR = (Path(os.environ.get("ACCESSSLICE_SPECIMENS", "")
                 or "missing-specimens")
            / "Christmas Jack Skellington Life Sized")


class TwoPlateCase(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)
        support.add_empty_plate(self.project, "2")

    def session(self):
        return ProjectSession(self.project)


class PlateModelTests(TwoPlateCase):

    def test_plates_and_membership_read_correctly(self):
        s = self.session()
        plates = s.plates()
        self.assertEqual([p["id"] for p in plates], ["1", "2"])
        self.assertEqual(plates[0]["object_ids"], ["2"])
        self.assertEqual(plates[1]["object_ids"], [])
        self.assertEqual(s.plate_of_object("2"), "1")
        self.assertIsNone(s.plate_of_object("99"))

    def test_the_grid_matches_the_verified_studio_layout(self):
        s = self.session()
        grid = s.plate_grid()
        self.assertTrue(grid["reliable"])
        # H2C plate is 330 wide: stride = 330 x 1.25.
        self.assertAlmostEqual(grid["stride"], 412.5)
        self.assertEqual(s.plate_origin("1"), (0.0, 0.0))
        origin2 = s.plate_origin("2")
        self.assertAlmostEqual(origin2[0], 412.5)
        self.assertAlmostEqual(origin2[1], 0.0)

    def test_summary_counts_the_plates_and_names_the_empty_one(self):
        s = self.session()
        self.assertIn("2 build plates - see Placement",
                      " ".join(s.summary()))
        # The fixture's plate 2 is empty: the only plate note says so.
        notes = [n for n in s.structure_notes if "plate" in n]
        self.assertEqual(len(notes), 1)
        self.assertIn("plate 2 has nothing on it", notes[0])

    def test_an_unreadable_layout_is_announced_and_blocks_replating(self):
        entries = engine.load_entries(self.project)
        settings = json.loads(entries[engine.PROJECT_SETTINGS_ENTRY])
        settings["printable_area"] = []
        entries[engine.PROJECT_SETTINGS_ENTRY] = json.dumps(
            settings).encode("utf-8")
        engine.save_entries(self.project, entries)
        s = self.session()
        self.assertFalse(s.plate_grid()["reliable"])
        notes = " ".join(s.structure_notes)
        self.assertIn("could not be read reliably", notes)
        with self.assertRaises(ProjectError):
            s.stage_replate("2", "2")


class ReplateTests(TwoPlateCase):

    def test_moving_to_the_same_plate_is_a_noop(self):
        self.assertFalse(self.session().stage_replate("2", "1"))

    def test_unknown_plate_is_refused(self):
        with self.assertRaises(ProjectError):
            self.session().stage_replate("2", "9")

    def test_replate_moves_membership_and_recentres_the_object(self):
        s = self.session()
        self.assertTrue(s.stage_replate("2", "2"))
        s.save()
        fresh = ProjectSession(self.project)
        self.assertEqual(fresh.plate_of_object("2"), "2")
        plates = {p["id"]: p for p in fresh.plates()}
        self.assertEqual(plates["1"]["object_ids"], [])
        self.assertEqual(plates["2"]["object_ids"], ["2"])
        obj = fresh.objects()[0]
        self.assertAlmostEqual(obj["x"], 412.5 + 165.0, places=4)
        self.assertAlmostEqual(obj["y"], 160.0, places=4)
        # The relocated membership block keeps its identify_id.
        ms = engine.load_entries(self.project)[
            engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
        self.assertIn("identify_id", ms)

    def test_replate_round_trip_returns_home(self):
        s = self.session()
        s.stage_replate("2", "2")
        s.save()
        s.stage_replate("2", "1")
        s.save()
        fresh = ProjectSession(self.project)
        self.assertEqual(fresh.plate_of_object("2"), "1")
        obj = fresh.objects()[0]
        self.assertAlmostEqual(obj["x"], 165.0, places=4)
        self.assertAlmostEqual(obj["y"], 160.0, places=4)


class EmptyPlateTests(TwoPlateCase):
    """Removing empty plates (a user's send stopped on one, 2026-11)
    and the one-plate copy a single-plate slice is made from."""

    def entry_names(self):
        return set(engine.load_entries(self.project))

    def test_the_empty_plate_is_found(self):
        self.assertEqual(self.session().empty_plates(), ["2"])

    def test_a_trailing_empty_plate_goes_and_nothing_moves(self):
        s = self.session()
        before = s.objects()[0]
        lines = s.remove_empty_plates()
        self.assertIn("Removed the empty plate 2", lines[0])
        fresh = ProjectSession(self.project)
        self.assertEqual([p["id"] for p in fresh.plates()], ["1"])
        after = fresh.objects()[0]
        self.assertAlmostEqual(after["x"], before["x"], places=4)
        self.assertAlmostEqual(after["y"], before["y"], places=4)
        self.assertEqual(fresh.empty_plates(), [])
        self.assertTrue(any("removing empty plates" in b["name"]
                            for b in fresh.list_backups()),
                        "the backup is listed on the Backups tab")

    def test_an_empty_first_plate_goes_and_the_next_moves_up(self):
        s = self.session()
        s.stage_replate("2", "2")
        s.save()
        s = ProjectSession(self.project)
        self.assertEqual(s.empty_plates(), ["1"])
        s.remove_empty_plates()
        fresh = ProjectSession(self.project)
        plates = fresh.plates()
        self.assertEqual([p["id"] for p in plates], ["1"])
        self.assertEqual(plates[0]["object_ids"], ["2"])
        obj = fresh.objects()[0]
        # Back at plate 1's place in the grid: the plate centre.
        self.assertAlmostEqual(obj["x"], 165.0, places=4)
        self.assertAlmostEqual(obj["y"], 160.0, places=4)
        # The cover picture the package points at is still there.
        self.assertIn("Metadata/plate_1.png", self.entry_names())

    def test_refused_while_changes_are_staged(self):
        s = self.session()
        s.stage_move("2", 100.0, 100.0)
        with self.assertRaises(ProjectError):
            s.remove_empty_plates()

    def test_a_single_plate_project_has_nothing_to_remove(self):
        project = support.copy_test_project(self.sandbox, name="one.3mf")
        s = ProjectSession(project)
        self.assertEqual(s.empty_plates(), [])
        self.assertIn("no empty plates", s.remove_empty_plates()[0])

    def test_the_send_page_offers_the_removal(self):
        html = webui.render_send(self.session(), "Bambu Studio", False)
        self.assertIn("/project/remove-empty-plates", html)
        self.assertIn("Plate 2 is empty", html)
        placement = webui.render_placement_tab(self.session())
        self.assertIn("Remove the empty plates", placement)

    def test_one_plate_copy_holds_only_that_plate(self):
        import slicer_project
        s = self.session()
        s.stage_copy("2", "1")
        s.save()
        s = ProjectSession(self.project)
        first = s.objects()[0]["id"]
        s.stage_replate(first, "2")
        s.save()
        s = ProjectSession(self.project)
        on_two = next(p for p in s.plates() if p["id"] == "2")["object_ids"]
        before = s.read_bytes() if hasattr(s, "read_bytes") else             self.project.read_bytes()
        copy = slicer_project.one_plate_copy(
            self.project, "2", self.sandbox / "copy.3mf")
        c = ProjectSession(copy)
        self.assertEqual([p["id"] for p in c.plates()], ["1"])
        self.assertEqual(sorted(o["id"] for o in c.objects()),
                         sorted(on_two))
        o = c.objects()[0]
        self.assertTrue(0 <= o["x"] <= 330 and 0 <= o["y"] <= 320,
                        "the plate moved to the grid's origin")
        self.assertEqual(self.project.read_bytes(), before,
                         "the project itself is untouched")

    def test_one_plate_copy_refuses_an_empty_plate(self):
        import slicer_project
        with self.assertRaises(ProjectError):
            slicer_project.one_plate_copy(self.project, "2",
                                          self.sandbox / "copy.3mf")


class PerPlateFilamentMapTests(TwoPlateCase):

    def test_each_plate_keeps_its_own_map(self):
        s = self.session()
        # Both plates start with the copied map in Auto mode.
        self.assertEqual(s.filament_map("1"),
                         ("Auto For Flush", ["1", "2", "1", "1"]))
        self.assertEqual(s.filament_map("2"),
                         ("Auto For Flush", ["1", "2", "1", "1"]))
        self.assertTrue(s.stage_filament_map(["2", "2", "1", "1"],
                                             plate_id="2"))
        s.save()
        fresh = ProjectSession(self.project)
        self.assertEqual(fresh.filament_map("2"),
                         ("Manual", ["2", "2", "1", "1"]))
        self.assertEqual(fresh.filament_map("1"),
                         ("Auto For Flush", ["1", "2", "1", "1"]),
                         "plate 1's map must be untouched")

    def test_unknown_plate_is_refused(self):
        with self.assertRaises(ProjectError):
            self.session().stage_filament_map(["1", "1", "1", "1"],
                                              plate_id="9")


class RenderingTests(TwoPlateCase):

    def test_placement_groups_by_plate_with_local_coordinates(self):
        html = webui.render_placement_tab(self.session())
        self.assertIn("Plate 1", html)
        self.assertIn("Plate 2", html)
        self.assertIn("Nothing on this plate", html)
        self.assertIn("Move to plate", html)
        self.assertIn("Positions are given per plate", html)

    def test_filaments_tab_renders_one_map_section_per_plate(self):
        html = webui.render_filaments_tab(self.session())
        self.assertIn("on plate 1", html)
        self.assertIn("on plate 2", html)

    def test_send_page_mentions_the_plate_choice(self):
        html = webui.render_send(self.session(), "Bambu Studio", False)
        self.assertIn("2 build plates", html)


@unittest.skipUnless(JACK_DIR.is_dir(),
                     "local Jack Skellington specimens not present")
class RealMultiPlateSpecimenTests(unittest.TestCase):
    """Read-only checks against the two real 2.1-era projects the grid
    math was cracked from. Never modified - opened straight from disk."""

    def facts(self, name):
        path = JACK_DIR / f"Jack skeleton 3mf file {name}.3mf"
        if not path.is_file():
            self.skipTest(f"{path.name} not present")
        return ProjectSession(path)

    def test_pt2_reads_completely(self):
        s = self.facts("pt2")
        self.assertEqual(len(s.plates()), 21)
        self.assertEqual(len(s.objects()), 27)
        grid = s.plate_grid()
        self.assertTrue(grid["reliable"])
        self.assertEqual(grid["cols"], 5)
        self.assertAlmostEqual(grid["stride"], 320.0)
        # Every object belongs to a plate.
        for o in s.objects():
            self.assertIsNotNone(s.plate_of_object(o["id"]))

    def test_pt1_reads_completely(self):
        s = self.facts("pt1")
        self.assertEqual(len(s.plates()), 36)
        self.assertEqual(len(s.objects()), 65)
        grid = s.plate_grid()
        self.assertTrue(grid["reliable"])
        self.assertEqual(grid["cols"], 6)
        self.assertAlmostEqual(grid["stride"], 320.0)

    def test_pt2_renders_the_placement_tab(self):
        html = webui.render_placement_tab(self.facts("pt2"))
        self.assertIn("Plate 21", html)
        self.assertIn("Move to plate", html)


if __name__ == "__main__":
    unittest.main()
