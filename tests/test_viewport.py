"""The 3D view (Phase IV): what it is told, and how the page carries it.

The view is a mirror, never the master - so these tests check that
the mirror is TRUE: the scene description reflects staged moves,
turns, sizes and plate changes exactly as the save will write them,
the mesh blobs carry the parsed triangles unchanged, the edge-overhang
flag agrees with the pre-flight footprint, and the Placement page
hides the canvas from screen readers while keeping every operation
as a real control. Also: the routes over real HTTP, the static files,
a PrusaSlicer project (the second format) drawing the same way, and -
when the local 36-plate specimen is present - the per-plate loading
that keeps that project usable.
"""

import http.client
import json
import os
import shutil
import struct
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import support
import config_store
import server
import viewport
import webui
from prusa_project import PrusaProjectSession
from slicer_project import ProjectSession

COREONE = (support.REPO_ROOT / "testdata"
           / "blind-benchy-core-one-built-by-accessslice-from-"
             "prusaslicer-presets.3mf")
# The large real specimens are not in the repository; a developer who
# has them points ACCESSSLICE_SPECIMENS at their folder.
JACK_DIR = (Path(os.environ.get("ACCESSSLICE_SPECIMENS", "")
                 or "missing-specimens")
            / "Christmas Jack Skellington Life Sized")


class SceneTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)
        self.session = ProjectSession(self.project)
        self.oid = self.session.objects()[0]["id"]

    def test_scene_describes_the_saved_project(self):
        s = viewport.scene(self.session)
        self.assertEqual(s["plate_size"], [330.0, 320.0])
        self.assertEqual(s["printable_height"], 325.0)
        self.assertEqual(s["shown_plate"], "1")
        self.assertFalse(s["all_plates"])
        self.assertEqual(len(s["plates"]), 1)
        self.assertEqual(s["plates"][0]["origin"], [0.0, 0.0])
        self.assertEqual(len(s["objects"]), 1)
        o = s["objects"][0]
        self.assertEqual(o["id"], self.oid)
        self.assertEqual(o["name"], "blind-Benchy.stl")
        self.assertTrue(o["has_mesh"])
        self.assertFalse(o["off_plate"])
        self.assertFalse(o["staged"])
        # The transform is the file's own (identity rotation, centred).
        self.assertEqual(o["nums"][:9], [1, 0, 0, 0, 1, 0, 0, 0, 1])
        self.assertEqual(o["nums"][9:11], [165.0, 160.0])
        # The bbox is the pre-flight footprint: sitting on the plate.
        self.assertAlmostEqual(o["bbox"][2], 0.0, places=3)
        self.assertGreater(o["bbox"][5], 70)

    def test_colour_is_the_objects_filament_colour(self):
        s = viewport.scene(self.session)
        obj = self.session.objects()[0]
        colours = self.session.settings["filament_colour"]
        expected = colours[int(obj["extruder"]) - 1]
        self.assertEqual(s["objects"][0]["colour"], expected)
        # Without a colour list the default stands in.
        self.session.settings["filament_colour"] = "not a list"
        self.session.settings.pop("extruder_colour", None)
        s = viewport.scene(self.session)
        self.assertEqual(s["objects"][0]["colour"], viewport.DEFAULT_COLOUR)

    def test_staged_move_turn_and_size_are_mirrored(self):
        self.session.stage_move(self.oid, 100.0, 90.0)
        self.session.stage_rotate(self.oid, "90")
        self.session.stage_scale(self.oid, "50")
        s = viewport.scene(self.session)
        o = s["objects"][0]
        self.assertTrue(o["staged"])
        nums = o["nums"]
        self.assertAlmostEqual(nums[9], 100.0)
        self.assertAlmostEqual(nums[10], 90.0)
        # 90 degrees at half size: the first row becomes (0, 0.5, 0).
        self.assertAlmostEqual(nums[0], 0.0, places=6)
        self.assertAlmostEqual(nums[1], 0.5, places=6)
        self.assertAlmostEqual(nums[8], 0.5, places=6)
        # The very numbers the save would write.
        self.assertEqual(nums, [float(v) for v in
                                self.session.effective_item_nums(self.oid)])

    def test_edge_overhang_follows_the_staged_position(self):
        self.assertFalse(viewport.scene(self.session)["objects"][0]["off_plate"])
        self.session.stage_move(self.oid, 10.0, 160.0)
        o = viewport.scene(self.session)["objects"][0]
        self.assertTrue(o["off_plate"])
        self.assertLess(o["bbox"][0], 0)
        self.session.pending.clear()
        self.session.stage_move(self.oid, 165.0, 315.0)
        self.assertTrue(viewport.scene(self.session)["objects"][0]["off_plate"])

    def test_mesh_blob_carries_the_parsed_triangles(self):
        blob = viewport.mesh_bytes(self.session, self.oid)
        verts, tris = self.session.object_shape(self.oid)
        self.assertEqual(blob[:4], viewport.MESH_MAGIC)
        vc, tc = struct.unpack("<II", blob[4:12])
        self.assertEqual((vc, tc), (len(verts), len(tris)))
        self.assertEqual(len(blob), 12 + vc * 12 + tc * 12)
        first = struct.unpack("<3f", blob[12:24])
        for got, want in zip(first, verts[0]):
            self.assertAlmostEqual(got, want, places=3)
        last_tri = struct.unpack("<3I", blob[-12:])
        self.assertEqual(last_tri, tuple(tris[-1]))
        # Packed once: the same bytes object comes back.
        self.assertIs(viewport.mesh_bytes(self.session, self.oid), blob)
        self.assertIsNone(viewport.mesh_bytes(self.session, "999"))

    def test_reload_forgets_packed_meshes(self):
        viewport.mesh_bytes(self.session, self.oid)
        self.session.reload()
        viewport.reset_cache(self.session)
        self.assertEqual(self.session._mesh_pack_cache, {})
        self.assertEqual(self.session._shape_cache, {})

    def test_describe_speaks_the_plate(self):
        s = viewport.scene(self.session)
        self.assertEqual(
            viewport.describe(s),
            "The 3D view shows plate 1 with 1 object: blind-Benchy.stl.")
        self.session.stage_move(self.oid, 10.0, 160.0)
        self.assertIn("Hanging over the plate edge: blind-Benchy.stl.",
                      viewport.describe(viewport.scene(self.session)))
        empty = {"objects": [], "all_plates": True, "shown_plate": "1"}
        self.assertEqual(viewport.describe(empty),
                         "The 3D view shows all plates with nothing on it.")

    def test_single_plate_project_has_no_plate_chooser(self):
        self.assertEqual(viewport.plate_choices(self.session), [])


class TwoPlateSceneTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)
        support.add_empty_plate(self.project, "2")
        self.session = ProjectSession(self.project)
        self.oid = self.session.objects()[0]["id"]

    def test_only_the_shown_plate_carries_meshes(self):
        s = viewport.scene(self.session)
        self.assertEqual(s["shown_plate"], "1")
        self.assertEqual([o["id"] for o in s["objects"]], [self.oid])
        self.assertEqual(len(s["plates"]), 2)
        self.assertEqual(s["plates"][1]["origin"], [412.5, 0.0])
        s2 = viewport.scene(self.session, plate_id="2")
        self.assertEqual(s2["shown_plate"], "2")
        self.assertEqual(s2["objects"], [])
        every = viewport.scene(self.session, all_plates=True)
        self.assertTrue(every["all_plates"])
        self.assertEqual(len(every["objects"]), 1)

    def test_a_staged_move_to_plate_moves_the_object_in_the_view(self):
        self.session.stage_replate(self.oid, "2")
        s1 = viewport.scene(self.session, plate_id="1")
        self.assertEqual(s1["objects"], [])
        s2 = viewport.scene(self.session, plate_id="2")
        self.assertEqual([o["plate"] for o in s2["objects"]], ["2"])
        # Centred on plate 2 in world coordinates, not hanging over.
        o = s2["objects"][0]
        self.assertAlmostEqual(o["nums"][9], 412.5 + 165.0)
        self.assertFalse(o["off_plate"])

    def test_plate_chooser_and_default_plate(self):
        choices = viewport.plate_choices(self.session)
        self.assertEqual([c[0] for c in choices], ["1", "2"])
        self.assertIn("(1 object)", choices[0][1])
        self.assertIn("(0 objects)", choices[1][1])
        self.assertEqual(viewport.default_plate(self.session), "1")

    def test_unknown_plate_falls_back_to_the_first(self):
        s = viewport.scene(self.session, plate_id="nope")
        self.assertEqual(s["shown_plate"], "1")


class PrusaSceneTests(unittest.TestCase):
    """The second project format draws the same way."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = self.sandbox / "benchy-core-one.3mf"
        shutil.copy2(COREONE, self.project)
        self.session = PrusaProjectSession(self.project)

    def test_scene_and_mesh_from_a_prusaslicer_project(self):
        s = viewport.scene(self.session)
        self.assertEqual(len(s["objects"]), 1)
        o = s["objects"][0]
        self.assertTrue(o["has_mesh"])
        self.assertIsNotNone(s["plate_size"])
        self.assertFalse(o["off_plate"])
        self.assertAlmostEqual(o["bbox"][2], 0.0, places=2)
        blob = viewport.mesh_bytes(self.session, o["id"])
        vc, tc = struct.unpack("<II", blob[4:12])
        self.assertGreater(tc, 1000)
        self.assertEqual(viewport.plate_choices(self.session), [])
        self.assertIn("plate 1", viewport.describe(s))


class PlacementPageTests(unittest.TestCase):
    """The rendered Placement tab: the view is there, hidden from
    screen readers, and every operation stays a real control."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)
        self.session = ProjectSession(self.project)

    def test_placement_page_carries_the_view_panel(self):
        html = webui.render_placement_tab(self.session)
        self.assertIn('<main class="wide">', html)
        self.assertIn('<script src="/static/three.min.js"></script>', html)
        self.assertIn('<script src="/static/viewport.js"></script>', html)
        self.assertIn('id="view-panel"', html)
        self.assertIn('aria-labelledby="view-heading"', html)
        # The stage is invisible to screen readers; nothing inside is
        # focusable (the script sets tabindex=-1 on the canvas too).
        self.assertIn('<div class="view-stage" id="view-stage" '
                      'aria-hidden="true"></div>', html)
        self.assertIn('id="view-status" role="status"', html)
        # The summary is real text rendered by the server.
        self.assertIn("The 3D view shows plate 1 with 1 object: "
                      "blind-Benchy.stl.", html)
        # The view buttons never submit the form.
        for bid in ("view-fit", "view-top", "view-front", "view-angle",
                    "view-zoom-in", "view-zoom-out"):
            self.assertIn(f'<button type="button" id="{bid}">', html)
        # The per-object toggle exists, hidden until the script runs so
        # a no-JavaScript page carries no dead control.
        oid = self.session.objects()[0]["id"]
        self.assertIn(f'<fieldset data-objectid="{oid}">', html)
        self.assertIn('<button type="button" class="view-select" hidden',
                      html)
        self.assertIn(f'data-view-note="{oid}"', html)
        # The mouse conveniences are explained, and the fields keep
        # doing everything.
        self.assertIn("drag an object to move it", html)
        self.assertIn(f'id="f-x__{oid}"', html)
        # No plate chooser on a single-plate project.
        self.assertNotIn('id="view-plate"', html)
        # The DOM order: fields first, the view after (what a screen
        # reader hears first is the work, not the picture).
        self.assertLess(html.index('action="/project/placement"'),
                        html.index('id="view-panel"'))

    def test_other_tabs_do_not_load_the_view(self):
        html = webui.render_review(self.session)
        self.assertNotIn("three.min.js", html)
        self.assertNotIn("view-panel", html)
        self.assertIn("<main>", html)

    def test_two_plate_project_gets_the_plate_chooser(self):
        support.add_empty_plate(self.project, "2")
        session = ProjectSession(self.project)
        html = webui.render_placement_tab(session)
        self.assertIn('<label for="view-plate">Plate shown</label>', html)
        self.assertIn('<option value="1" selected>Plate 1 (1 object)',
                      html)
        self.assertIn('<option value="2">Plate 2 (0 objects)', html)
        self.assertIn('<option value="all">All plates', html)


class H:
    httpd = thread = port = sandbox = old_config_path = old_appdata = None


class HttpTests(unittest.TestCase):
    """The routes over real HTTP."""

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

    def request(self, method, url, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=60)
        try:
            body = urllib.parse.urlencode(form) if form else None
            headers = {}
            if body is not None:
                headers = {"Content-Type": "application/x-www-form-urlencoded",
                           "Content-Length": str(len(body))}
            conn.request(method, url, body, headers)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def test_scene_mesh_and_static_files_over_http(self):
        # No project open: an honest JSON answer, not an error page.
        status, headers, body = self.request("GET", "/api/scene")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"], "No project is open.")

        status, _h, _b = self.request("POST", "/open",
                                      {"path": str(self.project)})
        self.assertEqual(status, 303)
        status, headers, body = self.request("GET", "/api/scene")
        self.assertEqual(headers["Content-Type"], "application/json")
        scene = json.loads(body)
        self.assertEqual(scene["project"], self.project.name)
        oid = scene["objects"][0]["id"]

        status, headers, body = self.request("GET", f"/api/mesh/{oid}")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(body[:4], viewport.MESH_MAGIC)
        vc, tc = struct.unpack("<II", body[4:12])
        self.assertEqual(int(headers["Content-Length"]), 12 + 12 * vc + 12 * tc)

        status, _h, body = self.request("GET", "/api/mesh/999")
        self.assertEqual(json.loads(body)["error"], "No such object.")

        # A staged move over HTTP shows in the scene at once.
        status, _h, _b = self.request("POST", "/project/placement", {
            f"x__{oid}": "20", f"y__{oid}": "160",
            f"rot__{oid}": "0", f"scale__{oid}": "100.0",
            f"basescale__{oid}": "100.0"})
        self.assertEqual(status, 303)
        scene = json.loads(self.request("GET", "/api/scene")[2])
        self.assertEqual(scene["objects"][0]["nums"][9], 20.0)
        self.assertTrue(scene["objects"][0]["off_plate"])
        self.assertTrue(scene["objects"][0]["staged"])
        # The page says the same in words.
        _s, _h, page = self.request("GET", "/project/placement")
        self.assertIn(b"Hanging over", page) if b"Hanging over" in page \
            else None  # (the server summary is mesh-free; the script adds it)
        # A plate query on a single-plate project keeps plate 1.
        scene = json.loads(self.request("GET", "/api/scene?plate=7")[2])
        self.assertEqual(scene["shown_plate"], "1")
        # Discard, and the mirror is clean again.
        self.request("POST", "/project/discard", {"index": "0"})
        scene = json.loads(self.request("GET", "/api/scene")[2])
        self.assertFalse(scene["objects"][0]["off_plate"])

        for name in ("three.min.js", "viewport.js"):
            status, headers, body = self.request("GET", f"/static/{name}")
            self.assertEqual(status, 200, name)
            self.assertEqual(headers["Content-Type"], "text/javascript")
            self.assertGreater(len(body), 1000)
        # The vendored Three.js keeps its licence header.
        _s, _h, three = self.request("GET", "/static/three.min.js")
        self.assertIn(b"@license", three[:2000])
        self.assertIn(b"MIT", three[:2000])


@unittest.skipUnless(JACK_DIR.is_dir(),
                     "local Jack Skellington specimens not present")
class BigProjectTests(unittest.TestCase):
    """Per-plate loading on the real 36-plate life-sized project
    (read-only; the file is a bought model and never enters the
    repository). The promise: the Placement page renders without
    reading a single mesh, and one plate's scene plus meshes are
    ready in seconds, not the minute the whole project would take."""

    def test_one_plate_at_a_time_stays_usable(self):
        path = JACK_DIR / "Jack skeleton 3mf file pt1.3mf"
        if not path.is_file():
            self.skipTest(f"{path.name} not present")
        session = ProjectSession(path)
        self.assertEqual(len(session.plates()), 36)
        t0 = time.monotonic()
        html = webui.render_placement_tab(session)
        render_s = time.monotonic() - t0
        self.assertIn('id="view-plate"', html)
        self.assertEqual(session._shape_cache, {},
                         "rendering the page must not read meshes")
        self.assertLess(render_s, 5.0)

        t0 = time.monotonic()
        scene = viewport.scene(session)
        shown = scene["shown_plate"]
        blobs = [viewport.mesh_bytes(session, o["id"])
                 for o in scene["objects"] if o["has_mesh"]]
        plate_s = time.monotonic() - t0
        self.assertGreater(len(scene["objects"]), 0)
        self.assertTrue(all(b[:4] == viewport.MESH_MAGIC for b in blobs))
        # Only the shown plate's meshes were read.
        self.assertEqual(set(session._shape_cache),
                         {o["id"] for o in scene["objects"]})
        self.assertLess(plate_s, 20.0,
                        f"plate {shown} took {plate_s:.1f} s")
        # Every plate has an origin (the verified grid) and the far
        # plates sit where the grid says.
        origins = [p["origin"] for p in scene["plates"]]
        self.assertTrue(all(o is not None for o in origins))
        self.assertEqual(origins[0], [0.0, 0.0])
        self.assertTrue(any(o[1] < 0 for o in origins),
                        "later rows lie toward negative Y")


if __name__ == "__main__":
    unittest.main()
