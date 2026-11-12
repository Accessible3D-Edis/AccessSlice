"""Tests for newjob - creating a project from a model file (Phase II).

Hermetic tests use the two-object fixture support.add_duplicate_object
builds (the shape the engine's merge produces) and the fake merge
script; the integration test runs the REAL engine end to end - donor +
STL -> created project -> headless slice - because "a CLI-created H2C
project slices" is exactly the claim upstream could silently break
(the -66 class of failure, see the 2026-08-31 log entry).
"""

import shutil
import sys
import unittest
from pathlib import Path

import support
import engine_driver
import newjob
from slicer_project import ProjectError, ProjectSession


class RemoveObjectsTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.project = support.copy_test_project(self.sandbox)
        self.orig_id = support.add_duplicate_object(self.project)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_removes_exactly_the_named_object(self):
        self.assertEqual(len(ProjectSession(self.project).objects()), 2)
        newjob.remove_objects(self.project, [self.orig_id])
        session = ProjectSession(self.project)
        self.assertEqual([o["name"] for o in session.objects()],
                         ["second-model.stl"])
        # The shared mesh file must survive - the duplicate uses it.
        self.assertIsNotNone(session.object_shape(
            session.objects()[0]["id"]))

    def test_unknown_ids_change_nothing(self):
        newjob.remove_objects(self.project, ["99999"])
        self.assertEqual(len(ProjectSession(self.project).objects()), 2)


class FindDonorTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.project = support.copy_test_project(self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_finds_the_matching_printer(self):
        junk = self.sandbox / "junk.3mf"
        junk.write_text("not a zip at all")
        gone = self.sandbox / "deleted.3mf"
        donor = newjob.find_donor([junk, gone, self.project],
                                  "Bambu Lab H2C 0.4 nozzle")
        self.assertEqual(donor, self.project)

    def test_no_project_for_that_printer_means_none(self):
        self.assertIsNone(newjob.find_donor(
            [self.project], "Bambu Lab X1 Carbon 0.4 nozzle"))


class UniqueTargetTests(unittest.TestCase):

    def test_never_clobbers(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        first = newjob.unique_target(sandbox, "part.stl")
        self.assertEqual(first.name, "part.3mf")
        first.write_bytes(b"x")
        self.assertEqual(newjob.unique_target(sandbox, "part.stl").name,
                         "part (2).3mf")


class CreateProjectTests(unittest.TestCase):
    """create_project against the fake merge (real removal, real
    arrange, real save pipeline - only the engine call is a stand-in)."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.donor = support.copy_test_project(self.sandbox,
                                               name="donor.3mf")
        # What the engine's merge would hand back: the donor's object
        # (carrying the marker name the marked donor copy gave it) +
        # the new model.
        self.merged = support.copy_test_project(self.sandbox,
                                                name="merged-fixture.3mf")
        newjob.mark_objects(self.merged)
        support.add_duplicate_object(self.merged)
        self.script = support.make_fake_merge(self.sandbox)
        self.model = self.sandbox / "second-model.stl"
        self.model.write_bytes(b"solid fake")
        self._orig = newjob.build_merge_command
        self.behaviour = "success"
        newjob.build_merge_command = (
            lambda exe, donor, models, out:
            [sys.executable, str(self.script), str(self.merged), out,
             self.behaviour])

    def tearDown(self):
        newjob.build_merge_command = self._orig
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_creates_a_one_object_project_on_donor_settings(self):
        target = self.sandbox / "created.3mf"
        notes = newjob.create_project("fake.exe", self.donor,
                                      [self.model], target)
        self.assertTrue(target.is_file())
        self.assertIn("Created created.3mf", notes[0])
        session = ProjectSession(target)
        self.assertEqual([o["name"] for o in session.objects()],
                         ["second-model.stl"])
        self.assertEqual(session.settings.get("printer_settings_id"),
                         "Bambu Lab H2C 0.4 nozzle",
                         "the donor's settings are the starting point")
        # The app's own arrange centred the model on the plate.
        o = session.objects()[0]
        plate = session.plate_size()
        self.assertTrue(abs(o["x"] - plate[0] / 2) < 40
                        and abs(o["y"] - plate[1] / 2) < 40,
                        f"expected near plate centre, got {o['x']}, "
                        f"{o['y']}")

    def test_engine_failure_is_quoted_in_plain_words(self):
        self.behaviour = "fail"
        target = self.sandbox / "created.3mf"
        with self.assertRaises(ProjectError) as ctx:
            newjob.create_project("fake.exe", self.donor,
                                  [self.model], target)
        self.assertIn("not found", str(ctx.exception))
        self.assertFalse(target.exists())

    def test_new_model_survives_when_the_engine_swaps_ids(self):
        # The real engine sometimes writes the NEW model under the id
        # the donor's object had (braille plaque donor, 2026-11-09):
        # removing by the donor's old id deleted the new model and
        # kept the old object - the user report. By name it holds.
        merged = support.copy_test_project(self.sandbox,
                                           name="swapped-fixture.3mf")
        donor_id = ProjectSession(self.donor).objects()[0]["id"]
        support.add_duplicate_object(
            merged, new_name=newjob.DONOR_MARKER + donor_id)
        support.rename_object(merged, donor_id, "second-model.stl")
        self.merged = merged
        target = self.sandbox / "created.3mf"
        newjob.create_project("fake.exe", self.donor, [self.model], target)
        objects = ProjectSession(target).objects()
        self.assertEqual([(o["id"], o["name"]) for o in objects],
                         [(donor_id, "second-model.stl")])

    def test_unrecognisable_donor_objects_stop_the_build(self):
        # Nothing carries the marker: AccessSlice cannot tell old from
        # new, so it refuses instead of opening a mixed project.
        merged = support.copy_test_project(self.sandbox,
                                           name="unmarked-fixture.3mf")
        support.add_duplicate_object(merged)
        self.merged = merged
        target = self.sandbox / "created.3mf"
        with self.assertRaises(ProjectError) as ctx:
            newjob.create_project("fake.exe", self.donor,
                                  [self.model], target)
        self.assertIn("cannot tell them apart", str(ctx.exception))
        self.assertFalse(target.exists())

    def test_a_model_the_merge_left_on_no_plate_lands_on_plate_one(self):
        # The real engine writes a centred model that nearly fills the
        # plate without plate membership (169 mm case, A1 mini,
        # 2026-11-12); the project then could not be sliced or sent.
        support.drop_membership(self.merged, "102")
        target = self.sandbox / "created.3mf"
        newjob.create_project("fake.exe", self.donor, [self.model], target)
        session = ProjectSession(target)
        self.assertEqual(session.unplaced_objects(), [])
        self.assertEqual(session.plates()[0]["object_ids"], ["102"])

    def test_the_donor_project_itself_is_never_changed(self):
        before = self.donor.read_bytes()
        newjob.create_project("fake.exe", self.donor, [self.model],
                              self.sandbox / "created.3mf")
        self.assertEqual(self.donor.read_bytes(), before)

    def test_an_engine_crash_is_retried(self):
        self.behaviour = "crash-once"
        target = self.sandbox / "created.3mf"
        newjob.create_project("fake.exe", self.donor, [self.model], target)
        self.assertEqual([o["name"] for o in
                          ProjectSession(target).objects()],
                         ["second-model.stl"])

    def test_repeated_crashes_end_in_plain_words(self):
        self.behaviour = "crash"
        target = self.sandbox / "created.3mf"
        with self.assertRaises(ProjectError) as ctx:
            newjob.create_project("fake.exe", self.donor,
                                  [self.model], target)
        self.assertIn(f"{newjob.MERGE_ATTEMPTS} times in a row",
                      str(ctx.exception))
        self.assertFalse(target.exists())


class MarkObjectsTests(unittest.TestCase):

    def test_every_object_is_marked_and_parts_keep_their_names(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        project = support.copy_test_project(sandbox)
        support.add_duplicate_object(project)
        self.assertEqual(newjob.mark_objects(project), 2)
        objects = ProjectSession(project).objects()
        self.assertEqual(sorted(o["name"] for o in objects),
                         sorted(newjob.DONOR_MARKER + o["id"]
                                for o in objects))
        import edit_bambu_3mf as engine
        ms = engine.load_entries(project)[
            engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
        self.assertIn('<metadata key="name" value="blind-Benchy.stl"/>',
                      ms, "part names are left alone")


@unittest.skipUnless(support.real_studio_exe(),
                     "real Bambu Studio not installed")
class RealNewJobIntegrationTests(unittest.TestCase):
    """Donor + real STL -> real merge -> the created project SLICES.

    This pins the whole new-job contract against the real engine: the
    merge flags, object names surviving the merge (the removal relies
    on them), and - the part upstream breaks for H2-series - that the
    resulting project is actually sliceable (rc 0, not -66)."""

    def test_created_h2c_project_slices_for_real(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        donor = support.copy_test_project(sandbox, name="donor.3mf")
        # A real STL, extracted from the committed project's own mesh.
        source = ProjectSession(donor)
        shape = source.object_shape(source.objects()[0]["id"])
        self.assertIsNotNone(shape)
        stl = support.write_stl(*shape, sandbox / "extracted-model.stl")

        target = sandbox / "from-model.3mf"
        notes = newjob.create_project(support.real_studio_exe(), donor,
                                      [stl], target)
        self.assertTrue(target.is_file(), notes)
        session = ProjectSession(target)
        self.assertEqual([o["name"] for o in session.objects()],
                         ["extracted-model.stl"])

        job = engine_driver.SliceJob(support.real_studio_exe(), target)
        job.wait(600)
        self.assertEqual(job.status, "done",
                         msg=f"report: {job.report}")
        self.assertGreater(job.report["total_grams"], 10)
