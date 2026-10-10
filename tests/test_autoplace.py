"""Automatic placement: mesh reading, auto-arrange and auto-orient.

The 2026-08-28 feature round, born from a real failed print: two parts
imported into Studio landed on top of each other. Covers

- the geometry module's maths (transform conventions, the rotation
  builder, the shelf packer, the lie-direction chooser) on synthetic
  shapes,
- the real Benchy test project end to end: mesh parsing, arranging a
  stacked pair apart, auto-orienting a tilted object back down, and
  the scale command now keeping the bottom on the plate, and
- the web plumbing: the Placement tab's buttons and the server's
  handling of them, over real HTTP.
"""

import http.client
import math
import os
import re
import shutil
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

import support
import config_store
import edit_bambu_3mf as engine
import geometry
import server
import webui
from slicer_project import ProjectError, ProjectSession


# ------------------------------------------------- synthetic fixtures


def box_mesh(w, d, h):
    """A closed box centred on the origin, normals facing outward."""
    x, y, z = w / 2, d / 2, h / 2
    verts = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
             (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    tris = [(0, 2, 1), (0, 3, 2),          # bottom (-z)
            (4, 5, 6), (4, 6, 7),          # top (+z)
            (0, 1, 5), (0, 5, 4),          # front (-y)
            (2, 3, 7), (2, 7, 6),          # back (+y)
            (0, 4, 7), (0, 7, 3),          # left (-x)
            (1, 2, 6), (1, 6, 5)]          # right (+x)
    return verts, tris


def rotated(verts, rows9):
    return [geometry.rotate_point(rows9, v) for v in verts]


class TransformMathTests(unittest.TestCase):

    def test_row_vector_convention_rotates_counterclockwise(self):
        # Rz 90 degrees in row-vector form, as stage_rotate builds it.
        rz = [0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        self.assertAlmostEqual(
            geometry.rotate_point(rz, (1.0, 0.0, 0.0))[1], 1.0)
        full = rz + [10.0, 20.0, 30.0]
        wx, wy, wz = geometry.transform_point(full, (1.0, 0.0, 0.0))
        self.assertAlmostEqual((wx, wy, wz), (10.0, 21.0, 30.0))

    def test_rotation_taking_maps_and_stays_rigid(self):
        cases = [((0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),
                 ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0)),   # straight flip
                 ((0.6, 0.0, 0.8), (0.0, 0.0, -1.0)),
                 ((0.0, 0.0, -1.0), (0.0, 0.0, -1.0))]  # nothing to do
        for frm, to in cases:
            q = geometry.rotation_taking(frm, to)
            got = geometry.rotate_point(q, frm)
            for g, t in zip(got, to):
                self.assertAlmostEqual(g, t, places=9)
            # Rigid: Q x Q-transposed is the identity.
            qt = [q[3 * c + r] for r in range(3) for c in range(3)]
            eye = geometry.matmul9(q, qt)
            for i, v in enumerate(eye):
                self.assertAlmostEqual(v, 1.0 if i in (0, 4, 8) else 0.0,
                                       places=9)

    def test_matmul9_composes_in_application_order(self):
        rz = geometry.rotation_taking((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        rx = geometry.rotation_taking((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        both = geometry.matmul9(rz, rx)
        got = geometry.rotate_point(both, (1.0, 0.0, 0.0))
        for g, t in zip(got, (0.0, 0.0, 1.0)):
            self.assertAlmostEqual(g, t, places=9)


class DownDirectionTests(unittest.TestCase):

    def test_a_flat_box_stays_as_it_lies(self):
        verts, tris = box_mesh(20, 30, 10)
        self.assertIsNone(geometry.best_down_direction(verts, tris))

    def test_a_box_on_its_side_flips_onto_its_biggest_face(self):
        # 20 x 10 footprint, biggest faces (20 x 30) point along y.
        verts, tris = box_mesh(20, 10, 30)
        down = geometry.best_down_direction(verts, tris)
        self.assertIsNotNone(down)
        self.assertAlmostEqual(abs(down[1]), 1.0, places=6)
        q = geometry.rotation_taking(down, (0.0, 0.0, -1.0))
        bb = geometry.bbox(rotated(verts, q))
        self.assertAlmostEqual(bb[5] - bb[2], 10.0, places=6)

    def test_a_box_balancing_on_an_edge_falls_flat(self):
        verts, tris = box_mesh(20, 20, 20)
        tilt = geometry.rotation_taking(
            (0.0, 0.0, -1.0),
            (0.0, math.sin(math.radians(45)), -math.cos(math.radians(45))))
        leaning = rotated(verts, tilt)
        down = geometry.best_down_direction(leaning, tris)
        self.assertIsNotNone(down)
        q = geometry.rotation_taking(down, (0.0, 0.0, -1.0))
        bb = geometry.bbox(rotated(leaning, q))
        self.assertAlmostEqual(bb[5] - bb[2], 20.0, places=4)


class ArrangeRectsTests(unittest.TestCase):

    def overlap_free(self, sizes, spots, gap):
        for i in range(len(sizes)):
            for j in range(i + 1, len(sizes)):
                dx = (abs(spots[i][0] - spots[j][0])
                      - (sizes[i][0] + sizes[j][0]) / 2)
                dy = (abs(spots[i][1] - spots[j][1])
                      - (sizes[i][1] + sizes[j][1]) / 2)
                self.assertGreaterEqual(
                    max(dx, dy), gap - 1e-6,
                    f"footprints {i} and {j} are too close")

    def test_footprints_get_separate_spots_inside_the_plate(self):
        sizes = [(60, 40), (30, 30), (80, 20), (10, 90)]
        spots = geometry.arrange_rects(sizes, 256, 256)
        self.assertEqual(len(spots), len(sizes))
        self.overlap_free(sizes, spots, geometry.ARRANGE_GAP_MM)
        for (cx, cy), (w, d) in zip(spots, sizes):
            self.assertGreaterEqual(cx - w / 2,
                                    geometry.ARRANGE_MARGIN_MM - 1e-6)
            self.assertGreaterEqual(cy - d / 2,
                                    geometry.ARRANGE_MARGIN_MM - 1e-6)
            self.assertLessEqual(cx + w / 2,
                                 256 - geometry.ARRANGE_MARGIN_MM + 1e-6)
            self.assertLessEqual(cy + d / 2,
                                 256 - geometry.ARRANGE_MARGIN_MM + 1e-6)

    def test_a_plate_that_cannot_hold_everything_says_so(self):
        self.assertIsNone(geometry.arrange_rects(
            [(200, 200), (200, 200)], 256, 256))
        self.assertIsNone(geometry.arrange_rects([(300, 10)], 256, 256))

    def test_the_keepout_corner_is_avoided(self):
        spots = geometry.arrange_rects([(100, 100)], 200, 200,
                                       keepout=(60, 60))
        (cx, cy) = spots[0]
        # Centred would collide with the front-left corner; the bundle
        # slides deeper instead.
        self.assertGreaterEqual(cy - 50, 60 - 1e-6)


# --------------------------------------------- the real test project


def world_bbox(session, objectid):
    eff = session.effective_item_nums(objectid)
    verts = session.object_shape(objectid)[0]
    return geometry.bbox([geometry.transform_point(eff, v)
                          for v in verts])


def add_second_object(project_path, new_id="3"):
    """A second build object sharing the Benchy's mesh, stacked at the
    same spot - exactly the failed-print shape this round fixes."""
    entries = engine.load_entries(project_path)
    model = entries[engine.MODEL_ENTRY].decode("utf-8")
    res = re.search(r"[ \t]*<object id=\"2\".*?</object>\n", model, re.S)
    block = (res.group(0).replace('id="2"', f'id="{new_id}"')
             .replace("00000001", "00000009")
             .replace("00010000-b206", "00010009-b206"))
    model = model[:res.end()] + block + model[res.end():]
    item = re.search(r"[ \t]*<item objectid=\"2\"[^>]*/>\n", model)
    it = (item.group(0).replace('objectid="2"', f'objectid="{new_id}"')
          .replace("00000002", "0000000a"))
    model = model[:item.end()] + it + model[item.end():]
    entries[engine.MODEL_ENTRY] = model.encode("utf-8")

    ms = entries[engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
    obj_block = (f'  <object id="{new_id}">\n'
                 '    <metadata key="name" value="second-part.stl"/>\n'
                 '    <metadata key="extruder" value="1"/>\n'
                 '  </object>\n')
    plate = re.search(r"[ \t]*<plate>", ms)
    ms = ms[:plate.start()] + obj_block + ms[plate.start():]
    inst = ('    <model_instance>\n'
            f'      <metadata key="object_id" value="{new_id}"/>\n'
            '      <metadata key="instance_id" value="0"/>\n'
            '      <metadata key="identify_id" value="95"/>\n'
            '    </model_instance>\n')
    end = ms.find("</plate>")
    ms = ms[:end] + inst + "  " + ms[end:]
    entries[engine.MODEL_SETTINGS_ENTRY] = ms.encode("utf-8")
    engine.save_entries(project_path, entries)


class ProjectCase(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)


class MeshReadingTests(ProjectCase):

    def test_the_benchy_mesh_reads_and_sits_exactly_on_the_plate(self):
        s = ProjectSession(self.project)
        verts, tris = s.object_shape("2")
        self.assertEqual(len(verts), 47311)
        self.assertEqual(len(tris), 94745)
        bb = world_bbox(s, "2")
        # Studio drops objects onto the plate; the same zero coming out
        # of our own transform maths validates the whole convention.
        self.assertAlmostEqual(bb[2], 0.0, places=4)
        # And the mesh is centred where the transform says the object is.
        self.assertAlmostEqual((bb[0] + bb[3]) / 2, 165.0, places=3)
        self.assertAlmostEqual((bb[1] + bb[4]) / 2, 160.0, places=3)

    def test_an_object_without_a_mesh_reads_as_none(self):
        s = ProjectSession(self.project)
        self.assertIsNone(s.object_shape("99"))


class AutoOrientTests(ProjectCase):

    def test_the_saved_benchy_keeps_the_lie_its_designer_chose(self):
        s = ProjectSession(self.project)
        self.assertFalse(s.stage_orient("2"))
        self.assertEqual(s.pending, [])

    def test_a_tilted_object_is_stood_back_up_resting_on_the_plate(self):
        entries = engine.load_entries(self.project)
        model = entries[engine.MODEL_ENTRY].decode("utf-8")
        old = 'transform="1 0 0 0 1 0 0 0 1 165 160 38.4231987"'
        self.assertIn(old, model)
        entries[engine.MODEL_ENTRY] = model.replace(
            old, 'transform="1 0 0 0 0 1 0 -1 0 165 160 38.4231987"'
        ).encode("utf-8")
        engine.save_entries(self.project, entries)

        s = ProjectSession(self.project)
        before = world_bbox(s, "2")
        self.assertTrue(s.stage_orient("2"))
        # Staged view and saved file must agree.
        after = world_bbox(s, "2")
        self.assertAlmostEqual(after[2], 0.0, places=6)
        self.assertAlmostEqual((after[0] + after[3]) / 2,
                               (before[0] + before[3]) / 2, places=6)
        self.assertAlmostEqual((after[1] + after[4]) / 2,
                               (before[1] + before[4]) / 2, places=6)
        # A stable flat lie, not the tipped-over one it started in.
        html = webui.render_placement_tab(s)
        self.assertIn("An automatic turn (most printable side "
                      "down) is staged, not saved.", html)
        s.save()
        fresh = ProjectSession(self.project)
        bb = world_bbox(fresh, "2")
        for got, want in zip(bb, after):
            self.assertAlmostEqual(got, want, places=6)

    def test_orienting_twice_stages_nothing_new(self):
        s = ProjectSession(self.project)
        self.assertFalse(s.stage_orient("2"))
        self.assertFalse(s.stage_orient("2"))
        self.assertEqual(s.pending, [])


class ScaleKeepsTheBottomTests(ProjectCase):

    def test_a_doubled_object_still_rests_on_the_plate(self):
        s = ProjectSession(self.project)
        self.assertTrue(s.stage_scale("2", 200))
        self.assertAlmostEqual(world_bbox(s, "2")[2], 0.0, places=6)
        s.save()
        fresh = ProjectSession(self.project)
        bb = world_bbox(fresh, "2")
        self.assertAlmostEqual(bb[2], 0.0, places=6)
        self.assertAlmostEqual(bb[5] - bb[2], 2 * 76.846, delta=0.1)


class AutoArrangeTests(ProjectCase):

    def setUp(self):
        super().setUp()
        add_second_object(self.project)

    def test_two_stacked_objects_get_their_own_spots(self):
        s = ProjectSession(self.project)
        objs = s.objects()
        self.assertEqual(len(objs), 2)
        self.assertEqual(objs[0]["x"], objs[1]["x"])  # truly stacked
        staged, notes = s.stage_arrange()
        self.assertEqual(staged, 2)
        self.assertEqual(notes, [])
        b1, b2 = world_bbox(s, "2"), world_bbox(s, "3")
        gap = max(b1[0] - b2[3], b2[0] - b1[3],
                  b1[1] - b2[4], b2[1] - b1[4])
        self.assertGreaterEqual(gap, geometry.ARRANGE_GAP_MM - 1e-6)
        pw, pd = s.plate_size()
        for b in (b1, b2):
            self.assertGreaterEqual(b[0], geometry.ARRANGE_MARGIN_MM - 1e-6)
            self.assertGreaterEqual(b[1], geometry.ARRANGE_MARGIN_MM - 1e-6)
            self.assertLessEqual(b[3], pw - geometry.ARRANGE_MARGIN_MM + 1e-6)
            self.assertLessEqual(b[4], pd - geometry.ARRANGE_MARGIN_MM + 1e-6)
        s.save()
        fresh = ProjectSession(self.project)
        for oid, staged_bb in (("2", b1), ("3", b2)):
            for got, want in zip(world_bbox(fresh, oid), staged_bb):
                self.assertAlmostEqual(got, want, places=6)

    def test_an_object_without_a_mesh_is_placed_with_an_honest_note(self):
        # A third build item with no mesh at all (assembled externally).
        entries = engine.load_entries(self.project)
        model = entries[engine.MODEL_ENTRY].decode("utf-8")
        item = re.search(r"[ \t]*<item objectid=\"2\"[^>]*/>\n", model)
        it = (item.group(0).replace('objectid="2"', 'objectid="7"')
              .replace("00000002", "0000000b"))
        entries[engine.MODEL_ENTRY] = (
            model[:item.end()] + it + model[item.end():]).encode("utf-8")
        engine.save_entries(self.project, entries)

        s = ProjectSession(self.project)
        staged, notes = s.stage_arrange()
        # An object may happen to be packed to the spot it already has,
        # so the staged count can be lower than the object count - what
        # matters is that no two footprints end up together.
        self.assertGreaterEqual(staged, 2)
        self.assertEqual(len(notes), 1)
        self.assertIn("could not be read", notes[0])
        self.assertIn("50 by 50", notes[0])
        boxes = []
        for o in s.objects():
            if s.object_shape(o["id"]):
                boxes.append(world_bbox(s, o["id"]))
            else:
                eff = s.effective_item_nums(o["id"])
                boxes.append((eff[9] - 25, eff[10] - 25, 0.0,
                              eff[9] + 25, eff[10] + 25, 0.0))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                gap = max(a[0] - b[3], b[0] - a[3],
                          a[1] - b[4], b[1] - a[4])
                self.assertGreaterEqual(
                    gap, geometry.ARRANGE_GAP_MM - 1e-6,
                    f"objects {i} and {j} are too close")

    def test_the_placement_tab_offers_the_automatic_commands(self):
        s = ProjectSession(self.project)
        html = webui.render_placement_tab(s)
        self.assertIn('name="arrange" value="1"', html)
        self.assertIn('name="orient" value="2"', html)
        self.assertIn('name="orient" value="3"', html)
        self.assertIn('name="orient_plate" value="1"', html)


# ------------------------------------------------------ over real HTTP


class HttpFixture:
    httpd = thread = port = sandbox = None
    old_config_path = old_appdata = None


H = HttpFixture


class AutoPlacementHttpTests(unittest.TestCase):
    """The button plumbing end to end: browser-shaped POSTs."""

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        cls.project = support.copy_test_project(projects_dir)
        fake_exe = support.make_fake_studio(H.sandbox)
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({"slicer": "bambu", "slicer_exe": str(fake_exe),
                           "projects_dir": str(projects_dir)})
        H.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(H.sandbox / "AppData")
        server.state["session"] = None
        H.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        H.port = H.httpd.server_address[1]
        H.thread = threading.Thread(target=H.httpd.serve_forever,
                                    daemon=True)
        H.thread.start()

    @classmethod
    def tearDownClass(cls):
        if H.httpd:
            H.httpd.shutdown()
            H.httpd.server_close()
        server.state["session"] = None
        server.state["watcher"].stop()
        config_store.CONFIG_PATH = H.old_config_path
        if H.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = H.old_appdata
        shutil.rmtree(H.sandbox, ignore_errors=True)

    def post(self, url, form):
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=30)
        try:
            body = urllib.parse.urlencode(form)
            conn.request("POST", url, body, {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body))})
            resp = conn.getresponse()
            return resp.status, resp.getheader("Location") or ""
        finally:
            conn.close()

    def test_the_buttons_travel_from_page_to_staging(self):
        status, _loc = self.post("/open", {"path": str(self.project)})
        self.assertEqual(status, 303)
        # The lone Benchy is already centred, and arranging says so.
        status, loc = self.post("/project/placement", {"arrange": "1"})
        self.assertEqual(status, 303)
        msg = urllib.parse.unquote(loc.split("msg=", 1)[1])
        self.assertIn("already sits", msg)
        # Auto-orienting the whole plate reports the honest verdict too.
        status, loc = self.post("/project/placement", {"orient_plate": "1"})
        self.assertEqual(status, 303)
        msg = urllib.parse.unquote(loc.split("msg=", 1)[1])
        self.assertIn("already lies well", msg)
        # And nothing was staged by either command.
        self.assertEqual(server.state["session"].pending, [])


if __name__ == "__main__":
    unittest.main()
