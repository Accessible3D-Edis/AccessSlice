"""Tests for step_mesh - STEP models meshed for the slicer engines.

The engines' command lines refuse every STEP file (Bambu Studio 2.9,
OrcaSlicer, Creality Print - probed 2026-11-27), so AccessSlice meshes
STEP itself before the merge. The hermetic tests pin the OBJ reader,
the STL writer and the model-list swap without the CAD kernel; the
real tests (skipped without cascadio or the slicers) mesh the
committed spacer STEP and build and slice projects from it.

The spacer: a cylinder 20 mm across and 10 mm tall with an 8 mm hole,
made by OpenCASCADE and saved with an upper-case .STEP extension (as
SolidWorks writes them). Its volume is pi * (10^2 - 4^2) * 10 mm3.
"""

import math
import shutil
import struct
import sys
import unittest
from pathlib import Path

import support
import backends
import engine_driver
import newjob
import step_mesh
from errors import ProjectError
from slicer_project import ProjectSession

HAVE_CASCADIO = step_mesh.load_kernel() is not None

SPACER = support.REPO_ROOT / "testdata" / "spacer-20mm-saved-as-step.STEP"
SPACER_VOLUME = math.pi * (10 ** 2 - 4 ** 2) * 10
X1C = "Bambu Lab X1 Carbon 0.4 nozzle"
KOBRA = "Anycubic Kobra S1 0.4 nozzle"


def read_binary_stl(path):
    """(vertices of every facet as tuples, facet count) - for checks."""
    data = Path(path).read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    facets = []
    for i in range(count):
        values = struct.unpack_from("<12f", data, 84 + 50 * i)
        facets.append((values[3:6], values[6:9], values[9:12]))
    return facets, count, len(data)


def volume_and_bounds(facets):
    volume = 0.0
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for a, b, c in facets:
        volume += (a[0] * (b[1] * c[2] - b[2] * c[1])
                   - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
        for p in (a, b, c):
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
    return volume, lo, hi


def closed_edges(facets):
    """True when every edge is shared by exactly two facets."""
    edges = {}
    for facet in facets:
        keys = [tuple(round(v, 5) for v in p) for p in facet]
        for i in range(3):
            edge = tuple(sorted((keys[i], keys[(i + 1) % 3])))
            edges[edge] = edges.get(edge, 0) + 1
    return all(n == 2 for n in edges.values())


class SuffixTests(unittest.TestCase):

    def test_step_in_any_letter_case(self):
        for name in ("a.step", "a.STEP", "a.Stp", "a.stp"):
            self.assertTrue(step_mesh.is_step(name), name)
        for name in ("a.stl", "a.obj", "a.3mf", "step"):
            self.assertFalse(step_mesh.is_step(name), name)

    def test_the_start_page_still_offers_step(self):
        self.assertTrue({".step", ".stp"} <= newjob.MODEL_SUFFIXES)


class MissingKernelTests(unittest.TestCase):

    def test_a_missing_kernel_is_explained_in_plain_words(self):
        orig = step_mesh.load_kernel
        step_mesh.load_kernel = lambda: None
        try:
            with self.assertRaises(ProjectError) as ctx:
                step_mesh.step_to_stl(SPACER, "unused.stl")
        finally:
            step_mesh.load_kernel = orig
        self.assertIn("reads STEP files is missing", str(ctx.exception))


class ObjAndStlTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)

    def test_obj_faces_with_slashes_quads_and_negative_indices(self):
        obj = self.sandbox / "m.obj"
        obj.write_text(
            "# comment\ng part\n"
            "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
            "vn 0 0 1\n"
            "f 1/1/1 2/2/1 3/3/1\n"
            "f -4 -2 -1\n"
            "f 1//1 2//1 3//1 4//1\n", encoding="utf-8")
        vertices, triangles = step_mesh.read_obj(obj)
        self.assertEqual(len(vertices), 4)
        self.assertEqual(triangles, [(0, 1, 2), (0, 2, 3),
                                     (0, 1, 2), (0, 2, 3)])

    def test_binary_stl_holds_every_facet_with_its_normal(self):
        stl = self.sandbox / "m.stl"
        step_mesh.write_binary_stl(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], stl)
        data = stl.read_bytes()
        self.assertEqual(len(data), 84 + 50)
        self.assertEqual(struct.unpack_from("<I", data, 80)[0], 1)
        normal = struct.unpack_from("<3f", data, 84)
        self.assertEqual(normal, (0.0, 0.0, 1.0))
        self.assertFalse((self.sandbox / "m.stl.part").exists())


class MeshableModelsTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self._orig = step_mesh.step_to_stl
        self.calls = []

        def fake(step, stl):
            self.calls.append((Path(step), Path(stl)))
            Path(stl).write_bytes(b"fake")
            return Path(stl)
        step_mesh.step_to_stl = fake

    def tearDown(self):
        step_mesh.step_to_stl = self._orig

    def test_only_step_files_are_swapped_and_keep_their_names(self):
        stl = self.sandbox / "plain.stl"
        a = self.sandbox / "one" / "Axle.STEP"
        b = self.sandbox / "two" / "Axle.stp"
        result = step_mesh.meshable_models([stl, a, b],
                                           self.sandbox / "work")
        self.assertEqual(result[0], stl)
        self.assertEqual(result[1].name, "Axle.stl")
        self.assertEqual(result[2].name, "Axle.stl")
        self.assertNotEqual(result[1], result[2],
                            "same-named STEP files must not collide")
        self.assertEqual([c[0] for c in self.calls], [a, b])


class CreateProjectSendsAMeshTests(unittest.TestCase):
    """newjob.create_project hands the engine an STL, never the STEP
    (fake merge; the STEP conversion is a stand-in too)."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.donor = support.copy_test_project(self.sandbox,
                                               name="donor.3mf")
        self.merged = support.copy_test_project(self.sandbox,
                                                name="merged-fixture.3mf")
        newjob.mark_objects(self.merged)
        support.add_duplicate_object(self.merged)
        self.script = support.make_fake_merge(self.sandbox)
        self.model = self.sandbox / "Axle.STEP"
        self.model.write_text("ISO-10303-21;")
        self.sent = []
        self._orig_cmd = newjob.build_merge_command
        self._orig_mesh = step_mesh.step_to_stl

        def command(exe, donor, models, out):
            self.sent.extend(Path(m) for m in models)
            return [sys.executable, str(self.script),
                    str(self.merged), out, "success"]

        def mesh(step, stl):
            Path(stl).write_bytes(b"fake mesh")
            return Path(stl)
        newjob.build_merge_command = command
        step_mesh.step_to_stl = mesh

    def tearDown(self):
        newjob.build_merge_command = self._orig_cmd
        step_mesh.step_to_stl = self._orig_mesh

    def test_the_engine_gets_an_stl_named_after_the_step(self):
        target = self.sandbox / "Axle.3mf"
        notes = newjob.create_project("fake.exe", self.donor,
                                      [self.model], target)
        self.assertTrue(target.is_file())
        self.assertEqual([m.name for m in self.sent], ["Axle.stl"])
        self.assertIn("from Axle.STEP", notes[0])
        # The mesh lived in the staging folder, which is gone now.
        self.assertFalse(self.sent[0].exists())


@unittest.skipUnless(HAVE_CASCADIO, "cascadio not installed")
class RealMeshingTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)

    def test_spacer_meshes_closed_to_size_and_volume(self):
        stl = step_mesh.step_to_stl(SPACER, self.sandbox / "spacer.stl")
        facets, count, size = read_binary_stl(stl)
        self.assertEqual(size, 84 + 50 * count)
        self.assertGreater(count, 100)
        self.assertTrue(closed_edges(facets), "the mesh must be closed")
        volume, lo, hi = volume_and_bounds(facets)
        # Millimetres, not metres: 20 x 20 x 10 mm.
        for k, span in enumerate((20, 20, 10)):
            self.assertAlmostEqual(hi[k] - lo[k], span, places=2)
        # Facets cut corners on the curves: a hair under the true
        # volume, never more than half a percent.
        self.assertLess(abs(volume - SPACER_VOLUME) / SPACER_VOLUME, 0.005)
        self.assertFalse((self.sandbox / "spacer.obj").exists())

    def test_a_file_that_is_not_step_is_refused_in_plain_words(self):
        bad = self.sandbox / "broken.step"
        bad.write_text("this is not a STEP file")
        with self.assertRaises(ProjectError) as ctx:
            step_mesh.step_to_stl(bad, self.sandbox / "broken.stl")
        self.assertIn("broken.step could not be read as a STEP model",
                      str(ctx.exception))

    def test_a_folder_with_danish_letters_works(self):
        folder = self.sandbox / "Søren æble"
        folder.mkdir()
        step = folder / "afstandsstykke ø.STEP"
        shutil.copy2(SPACER, step)
        stl = step_mesh.step_to_stl(step, folder / "afstandsstykke ø.stl")
        self.assertGreater(read_binary_stl(stl)[1], 100)


@unittest.skipUnless(HAVE_CASCADIO, "cascadio not installed")
class RealStepJobTests(unittest.TestCase):
    """A STEP file -> a project the real engine slices (the user
    report: "The input model file to the slicer can not be parsed")."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)

    @unittest.skipUnless(support.real_studio_exe(),
                         "Bambu Studio not installed")
    def test_bambu_builds_and_slices_a_step_model(self):
        exe = support.real_studio_exe()
        donor = support.copy_test_project(self.sandbox, name="donor.3mf")
        target = self.sandbox / "spacer.3mf"
        newjob.create_project(exe, donor, [SPACER], target)
        session = ProjectSession(target)
        self.assertEqual([o["name"] for o in session.objects()],
                         ["spacer-20mm-saved-as-step.stl"])
        job = engine_driver.SliceJob(exe, target)
        job.wait(600)
        self.assertEqual(job.status, "done", msg=f"report: {job.report}")
        # About 2.6 cm3 of PLA if printed solid; walls and infill
        # make it a little less.
        self.assertGreater(job.report["total_grams"], 1.5)

    def _family(self, key, exe, printer):
        backend = backends.get_backend(key)
        if printer not in backend.profiles.list_presets(exe, "machine"):
            self.skipTest(f"{printer} is not installed in "
                          f"{backend.display_name}")
        donor = backend.newjob.find_donor([], printer)
        target = backend.newjob.unique_target(self.sandbox, SPACER)
        backend.newjob.create_project(exe, donor, [SPACER], target)
        built = backend.open_project(target, slicer_exe=exe)
        self.assertEqual(len(built.objects()), 1)
        job = backend.engine.SliceJob(exe, target)
        job.wait(600)
        self.assertEqual(job.status, "done", job.report)

    @unittest.skipUnless(support.real_orca_exe(), "OrcaSlicer not installed")
    def test_orca_builds_and_slices_a_step_model(self):
        self._family("orca", str(support.real_orca_exe()), X1C)

    @unittest.skipUnless(support.real_anycubic_exe(),
                         "Anycubic Slicer Next not installed")
    def test_anycubic_builds_and_slices_a_step_model(self):
        self._family("anycubic", str(support.real_anycubic_exe()), KOBRA)


if __name__ == "__main__":
    unittest.main()
