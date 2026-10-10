"""Pre-flight warnings: the supports check and the head-crash class
(both built 2026-09-02).

Edis's request of 2026-09-01: warn BEFORE a slice is wasted, from the
settings and the real meshes, in plain words with real numbers, never
blocking. Covers

- the overhang measuring itself on synthetic boxes (a resting box has
  no overhang, a floating one has its whole bottom, a tilted one is
  counted or not depending on the threshold, scale counts),
- the session-level check on the three committed real projects - the
  H2C Benchy (Bambu Studio), the X1C cane with its per-object supports
  override (Bambu Studio) and the K2 Plus Dodecagon (Creality Print) -
  through both backends, with staged edits, per-object overrides, an
  unreadable threshold and a missing mesh all handled honestly, and
- the Slice-and-send page over real HTTP: the list above the Slice now
  button, the spoken "nothing to warn about", and no list at all while
  changes are still staged.
- check 2, by-object clearances: the footprint maths (convex outline,
  gap between outlines), two Benchies on the H2C in every layout
  (stacked, apart sideways, apart front to back, one shrunk under
  the rod, the lid limit, different plates, missing numbers), and
  the K2 Plus Dodecagon through Creality Print with its older
  clearance-radius spelling.
- check 3, the cheap same-family set: floating and sunk objects (and
  auto-orient now setting them down, over HTTP too), taller than the
  printer prints, the real cane's 20-square-millimetre foot with the
  brim-dependent fix and the raft exemption, and spiral vase with
  two objects on a plate.
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
import backends
import config_store
import edit_bambu_3mf as engine
import geometry
import preflight
import server
from slicer_project import ProjectSession

TESTDATA = support.TEST_PROJECT.parent
CANE = TESTDATA / "cane-per-object-overrides-saved-from-bambu-studio.3mf"
DODECAGON = TESTDATA / "dodecagon-plant-cover-saved-from-creality-print.3mf"
CANE_ID = "2"   # the cane is object 2, as Studio numbered it

IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


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


def tilted_about_x(degrees):
    """Row-vector rotation about the x axis."""
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [1.0, 0.0, 0.0, 0.0, c, s, 0.0, -s, c]


def resting(rows9, verts):
    """Transform numbers that put the rotated mesh's lowest point at
    z = 0 (how Studio drops an object onto the plate)."""
    low = min(geometry.rotate_point(rows9, v)[2] for v in verts)
    return rows9 + [0.0, 0.0, -low]


class OverhangMeasureTests(unittest.TestCase):

    def test_a_box_resting_on_the_plate_has_no_overhang(self):
        verts, tris = box_mesh(20, 20, 10)
        r = geometry.overhang_report(verts, tris, IDENTITY + [0, 0, 5], 30)
        self.assertEqual(r["area"], 0.0)
        self.assertAlmostEqual(r["total"], 2 * 400 + 4 * 200)

    def test_a_floating_box_hangs_by_its_whole_bottom(self):
        verts, tris = box_mesh(20, 20, 10)
        r = geometry.overhang_report(verts, tris, IDENTITY + [0, 0, 15], 30)
        self.assertAlmostEqual(r["area"], 400.0)
        self.assertAlmostEqual(r["flattest"], 0.0, places=6)
        self.assertAlmostEqual(r["lowest"], 10.0)
        self.assertAlmostEqual(r["highest"], 10.0)

    def test_a_tilted_underside_counts_only_below_the_threshold(self):
        verts, tris = box_mesh(20, 20, 10)
        gentle = resting(tilted_about_x(20), verts)   # bottom at 20 deg
        r = geometry.overhang_report(verts, tris, gentle, 30)
        self.assertAlmostEqual(r["area"], 400.0)
        self.assertAlmostEqual(r["flattest"], 20.0, places=5)
        self.assertAlmostEqual(r["lowest"], 0.0, places=6)
        steep = resting(tilted_about_x(40), verts)    # bottom at 40 deg
        self.assertEqual(geometry.overhang_report(
            verts, tris, steep, 30)["area"], 0.0)
        # The same lie IS overhang under a 45-degree threshold.
        self.assertAlmostEqual(geometry.overhang_report(
            verts, tris, steep, 45)["area"], 400.0)

    def test_scale_in_the_transform_counts(self):
        verts, tris = box_mesh(20, 20, 10)
        doubled = [2.0, 0, 0, 0, 2.0, 0, 0, 0, 2.0, 0, 0, 30]
        r = geometry.overhang_report(verts, tris, doubled, 30)
        self.assertAlmostEqual(r["area"], 1600.0)

    def test_no_faces_means_no_report(self):
        self.assertIsNone(geometry.overhang_report([], [], IDENTITY + [0, 0, 0], 30))


class ProjectCase(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)


def texts(session, check=None):
    return [w["text"] for w in preflight.warnings(session)
            if check is None or w["check"] == check]


class BenchySupportsCheckTests(ProjectCase):
    """The H2C Benchy: supports ON as saved, 43 square centimetres of
    overhang at its 30-degree threshold once they are off."""

    def test_supports_on_means_no_warning(self):
        s = ProjectSession(self.project)
        self.assertEqual(s.settings["enable_support"], "1")
        self.assertEqual(texts(s), [])

    def test_supports_off_names_the_object_with_real_numbers(self):
        s = ProjectSession(self.project)
        s.stage_setting("enable_support", "0")
        found = preflight.warnings(s)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["check"], "supports")
        self.assertEqual(found[0]["objectid"], "2")
        text = found[0]["text"]
        self.assertTrue(text.startswith("blind-Benchy.stl: supports are off"))
        self.assertIn("about 43 square centimetres", text)
        self.assertIn("less than 30 degrees from horizontal, your overhang "
                      "angle threshold", text)
        self.assertIn("fully horizontal", text)
        self.assertIn("from the plate up to 75 mm above it", text)
        self.assertIn("see the Support tab", text)
        self.assertNotIn("(plate", text)   # a single plate is not named

    def test_a_per_object_override_wins_over_the_project_value(self):
        s = ProjectSession(self.project)
        s.stage_setting("enable_support", "0")
        s.stage_object_setting("2", "enable_support", "1")
        self.assertEqual(texts(s), [])
        # And the other way round: project on, object off.
        s = ProjectSession(self.project)
        s.stage_object_setting("2", "enable_support", "0")
        self.assertEqual(len(texts(s)), 1)

    def test_the_threshold_from_the_file_drives_the_numbers(self):
        s = ProjectSession(self.project)
        s.stage_setting("enable_support", "0")
        s.stage_setting("support_threshold_angle", "60")
        text = texts(s)[0]
        self.assertIn("less than 60 degrees from horizontal", text)
        # A wider threshold catches more of the hull than 30 did.
        self.assertIn("about 67 square centimetres", text)

    def test_an_unreadable_threshold_falls_back_to_the_rule_of_thumb(self):
        s = ProjectSession(self.project)
        s.stage_setting("enable_support", "0")
        s.stage_setting("support_threshold_angle", "nonsense")
        text = texts(s)[0]
        self.assertIn("less than 45 degrees from horizontal, the usual "
                      "rule of thumb", text)

    def test_an_object_without_a_mesh_gets_no_claim(self):
        entries = engine.load_entries(self.project)
        for name in [n for n in entries if n.startswith("3D/Objects/")]:
            del entries[name]
        engine.save_entries(self.project, entries)
        s = ProjectSession(self.project)
        s.stage_setting("enable_support", "0")
        self.assertIsNone(s.object_shape("2"))
        self.assertEqual(texts(s), [])

    def test_reports_are_cached_per_placement(self):
        s = ProjectSession(self.project)
        s.stage_setting("enable_support", "0")
        preflight.warnings(s)
        per_placement = len(s._preflight_cache)   # one entry per report
        self.assertGreaterEqual(per_placement, 1)
        preflight.warnings(s)
        self.assertEqual(len(s._preflight_cache), per_placement)
        s.stage_move("2", 100, 100)
        preflight.warnings(s)
        self.assertEqual(len(s._preflight_cache), 2 * per_placement)
        s.reload()
        self.assertEqual(s._preflight_cache, {})


class CaneOverrideSpecimenTests(unittest.TestCase):
    """The X1C cane: project-wide supports OFF, but the object carries
    its own enable_support = 1 override - Studio-saved, so this is the
    real shape of the case. Its overhang is under a square centimetre
    yet two percent of the whole cane, so the share rule speaks."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = self.sandbox / "cane.3mf"
        shutil.copy2(CANE, self.project)

    def test_the_saved_override_keeps_the_cane_quiet(self):
        s = ProjectSession(self.project)
        self.assertEqual(s.settings["enable_support"], "0")
        self.assertEqual(s.object_overrides(CANE_ID).get("enable_support"), "1")
        self.assertEqual(texts(s, "supports"), [])

    def test_removing_the_override_speaks_in_square_millimetres(self):
        s = ProjectSession(self.project)
        s.stage_object_setting(CANE_ID, "enable_support", None)
        found = texts(s, "supports")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("cane.stl: supports are off"))
        self.assertIn("about 95 square millimetres", found[0])
        self.assertIn("between 17 and 120 mm above the plate", found[0])


class CrealityDodecagonTests(unittest.TestCase):
    """The K2 Plus Dodecagon through the Creality backend: supports
    off as saved, and honestly nothing to warn about - the cover has
    no underside in the air."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = self.sandbox / "dodecagon.3mf"
        shutil.copy2(DODECAGON, self.project)

    def test_both_backends_carry_the_same_preflight_part(self):
        self.assertIs(backends.get_backend("creality").preflight, preflight)
        self.assertIs(backends.get_backend("bambu").preflight, preflight)

    def test_the_creality_project_is_checked_with_the_same_keys(self):
        s = backends.get_backend("creality").open_project(self.project)
        self.assertEqual(s.settings["enable_support"], "0")
        self.assertEqual(texts(s), [])
        report = preflight.object_overhang(s, s.objects()[0]["id"], 30)
        self.assertLess(report["area"], 1.0)
        self.assertGreater(report["total"], 1000.0)


# ------------------------------------------------------------- HTTP


class HttpFixture:
    httpd = thread = port = sandbox = None
    old_config_path = old_appdata = None


H = HttpFixture


class SendPageTests(unittest.TestCase):
    """The warnings list on the Slice-and-send page, over real HTTP."""

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
            return resp.status, resp.read().decode("utf-8")
        finally:
            conn.close()

    def test_the_send_page_speaks_the_check_in_every_state(self):
        status, _ = self.request("POST", "/open", {"path": str(self.project)})
        self.assertEqual(status, 303)
        # Supports on as saved: the section is there and says so.
        status, body = self.request("GET", "/project/send")
        self.assertEqual(status, 200)
        self.assertIn("Warnings before slicing", body)
        self.assertIn("Nothing to warn about", body)
        self.assertLess(body.index("Warnings before slicing"),
                        body.index("btn-slice-now"))
        # A staged change: no slicing, no pre-flight list either.
        session = server.state["session"]
        session.stage_setting("enable_support", "0")
        status, body = self.request("GET", "/project/send")
        self.assertIn("Save before slicing", body)
        self.assertNotIn("Warnings before slicing", body)
        # Saved with supports off: the Benchy is named, numbers and all,
        # above the Slice now button.
        session.save()
        status, body = self.request("GET", "/project/send")
        self.assertEqual(status, 200)
        self.assertIn("blind-Benchy.stl: supports are off", body)
        self.assertIn("about 43 square centimetres", body)
        self.assertIn("see the Support tab", body)
        self.assertLess(body.index("blind-Benchy.stl: supports are off"),
                        body.index("btn-slice-now"))
        self.assertNotIn("Nothing to warn about", body)

    def test_auto_orient_over_http_sets_a_floating_object_down(self):
        floating = support.copy_test_project(H.sandbox / "projects",
                                             name="floating.3mf")
        lift_object_in_file(floating, "2", 12)
        status, _ = self.request("POST", "/open", {"path": str(floating)})
        self.assertEqual(status, 303)
        status, body = self.request("GET", "/project/send")
        self.assertIn("floats 12 mm above the plate", body)
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=60)
        try:
            form = urllib.parse.urlencode({"orient": "2"})
            conn.request("POST", "/project/placement", form, {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(form))})
            resp = conn.getresponse()
            location = resp.getheader("Location") or ""
        finally:
            conn.close()
        msg = urllib.parse.unquote(location.split("msg=", 1)[1])
        self.assertIn("already lies well but was off the plate - it will "
                      "be set down onto the plate", msg)
        status, body = self.request("GET", "/project/placement")
        self.assertIn("Setting it down onto the plate is staged", body)


# --------------------------------------- check 2: the head-crash class


class FootprintMathTests(unittest.TestCase):

    def test_the_hull_drops_inner_points_and_runs_counter_clockwise(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (2, 3)]
        self.assertEqual(geometry.convex_hull_xy(square),
                         [(0, 0), (10, 0), (10, 10), (0, 10)])

    def test_gaps_between_outlines(self):
        a = geometry.convex_hull_xy([(0, 0), (10, 0), (10, 10), (0, 10)])
        beside = geometry.convex_hull_xy([(20, 0), (30, 0), (30, 10), (20, 10)])
        self.assertAlmostEqual(geometry.hull_gap(a, beside), 10.0)
        overlapping = geometry.convex_hull_xy(
            [(5, 5), (15, 5), (15, 15), (5, 15)])
        self.assertEqual(geometry.hull_gap(a, overlapping), 0.0)
        diagonal = geometry.convex_hull_xy(
            [(20, 20), (30, 20), (30, 30), (20, 30)])
        self.assertAlmostEqual(geometry.hull_gap(a, diagonal),
                               math.sqrt(200))
        inside = geometry.convex_hull_xy([(4, 4), (6, 4), (6, 6), (4, 6)])
        self.assertEqual(geometry.hull_gap(a, inside), 0.0)


def checks(session):
    return [w["check"] for w in preflight.warnings(session)]


class ClearanceCheckTests(ProjectCase):
    """Two Benchies on the H2C (77 mm tall each; the printer's
    numbers inside the file: rod clearance 47.4 mm, lid 201 mm,
    clearance radius 96 mm, nozzle-to-rod distance 50 mm)."""

    def setUp(self):
        super().setUp()
        support.add_duplicate_object(self.project)   # id 102 on top of 2

    def test_by_layer_printing_never_warns(self):
        s = ProjectSession(self.project)
        self.assertEqual(s.settings["print_sequence"], "by layer")
        self.assertEqual(checks(s), [])

    def test_stacked_tall_pair_by_object_hits_rod_and_radius(self):
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        found = preflight.warnings(s)
        self.assertEqual([w["check"] for w in found], ["rod", "radius"])
        rod, radius = found[0]["text"], found[1]["text"]
        self.assertIn("both taller than the 47 mm clearance under the "
                      "printhead's gantry rod (77 and 77 mm)", rod)
        self.assertIn("whichever is finished first", rod)
        self.assertIn("touch or overlap", radius)
        self.assertIn("at least 96 mm between objects", radius)
        self.assertIn("Placement tab", radius)

    def test_apart_sideways_clears_the_radius_but_not_the_rod_band(self):
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        s.stage_move("102", 365, 160)      # 200 mm to the right, same depth
        self.assertEqual(checks(s), ["rod"])

    def test_apart_front_to_back_clears_the_rod_band_only(self):
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        s.stage_move("102", 165, 300)      # 140 mm further back
        found = preflight.warnings(s)
        self.assertEqual([w["check"] for w in found], ["radius"])
        self.assertIn("are only 63 mm apart", found[0]["text"])

    def test_a_short_neighbour_gets_the_print_last_wording(self):
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        s.stage_move("102", 365, 160)
        s.stage_scale("102", 50)           # 38 mm: under the rod
        found = preflight.warnings(s)
        self.assertEqual([w["check"] for w in found], ["rod"])
        text = found[0]["text"]
        self.assertTrue(text.startswith("blind-Benchy.stl is 77 mm tall"))
        self.assertIn("while printing second-model.stl, which lies within "
                      "50 mm of it front to back - unless blind-Benchy.stl "
                      "is printed last", text)
        self.assertIn("print order", text)

    def test_the_lid_rule_names_each_object_over_the_limit(self):
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        s.stage_move("102", 165, 300)
        s.stage_setting("extruder_clearance_height_to_lid", "60")
        found = preflight.warnings(s)
        self.assertEqual([w["check"] for w in found],
                         ["lid", "lid", "radius"])
        self.assertIn("77 mm tall, but by-object printing on this printer "
                      "allows at most 60 mm", found[0]["text"])

    def test_objects_on_different_plates_are_never_paired(self):
        support.add_empty_plate(self.project, "2")
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        s.stage_replate("102", "2")
        self.assertEqual(checks(s), [])

    def test_missing_clearance_numbers_make_no_claim(self):
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        for key in ("extruder_clearance_height_to_rod",
                    "extruder_clearance_height_to_lid",
                    "extruder_clearance_max_radius"):
            s.stage_setting(key, "unknown")
        self.assertEqual(checks(s), [])

    def test_supports_and_clearance_warnings_list_together(self):
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        s.stage_setting("enable_support", "0")
        s.stage_move("102", 365, 160)
        self.assertEqual(checks(s), ["supports", "supports", "rod"])


class CrealityClearanceTests(unittest.TestCase):
    """The K2 Plus Dodecagon through Creality Print: 113 mm tall, the
    K2's lid clearance 118 mm, and the older extruder_clearance_radius
    spelling - the key table's second spelling in use."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = self.sandbox / "dodecagon.3mf"
        shutil.copy2(DODECAGON, self.project)
        self.session = backends.get_backend("creality").open_project(
            self.project)

    def test_one_object_under_the_lid_is_quiet_by_object(self):
        self.session.stage_setting("print_sequence", "by object")
        self.assertEqual(checks(self.session), [])
        self.assertEqual(preflight._number(
            self.session, preflight.ORCA_FAMILY_KEYS["radius"]), 64.0)

    def test_the_lid_rule_reads_the_k2_numbers(self):
        self.session.stage_setting("print_sequence", "by object")
        self.session.stage_setting("extruder_clearance_height_to_lid", "100")
        found = preflight.warnings(self.session)
        self.assertEqual([w["check"] for w in found], ["lid"])
        self.assertIn("Dodecagon plant cover.stl: 113 mm tall, but by-object "
                      "printing on this printer allows at most 100 mm",
                      found[0]["text"])


# ------------------------------------ check 3: the cheap same-family set


def lift_object_in_file(project_path, objectid, dz):
    """Rewrite one build item's transform in the file so the object
    floats (dz > 0) or sinks (dz < 0) - a placement the slicer never
    saw, as a file edited elsewhere could carry."""
    entries = engine.load_entries(project_path)
    model = entries[engine.MODEL_ENTRY].decode("utf-8")
    m = re.search(rf'(<item objectid="{objectid}"[^>]*transform=")([^"]*)(")',
                  model)
    nums = [float(v) for v in m.group(2).split()]
    nums[11] += dz
    new = m.group(1) + " ".join(f"{v:g}" for v in nums) + m.group(3)
    model = model[:m.start()] + new + model[m.end():]
    entries[engine.MODEL_ENTRY] = model.encode("utf-8")
    engine.save_entries(project_path, entries)


class PlacementCheckTests(ProjectCase):
    """Floating, sunk, too tall, tiny contact - on the Benchy, the
    cane and the Dodecagon."""

    def test_the_saved_benchy_stands_firmly_within_height(self):
        s = ProjectSession(self.project)
        self.assertEqual(preflight.placement_warnings(s), [])

    def test_a_floating_object_is_named_with_its_height(self):
        lift_object_in_file(self.project, "2", 12)
        s = ProjectSession(self.project)
        found = preflight.placement_warnings(s)
        self.assertEqual([w["check"] for w in found], ["floating"])
        self.assertIn("blind-Benchy.stl floats 12 mm above the plate",
                      found[0]["text"])
        self.assertIn("Auto-orient it on the Placement tab", found[0]["text"])

    def test_a_sunk_object_is_named_too(self):
        lift_object_in_file(self.project, "2", -3)
        s = ProjectSession(self.project)
        found = preflight.placement_warnings(s)
        self.assertEqual([w["check"] for w in found], ["sunk"])
        self.assertIn("sinks 3 mm into the plate", found[0]["text"])

    def test_auto_orient_sets_a_floating_object_down(self):
        lift_object_in_file(self.project, "2", 12)
        s = ProjectSession(self.project)
        self.assertTrue(s.stage_orient("2"))
        self.assertTrue(s.orient_is_drop("2"))
        eff = s.effective_item_nums("2")
        bb = geometry.bbox([geometry.transform_point(eff, v)
                            for v in s.object_shape("2")[0]])
        self.assertAlmostEqual(bb[2], 0.0, places=4)
        self.assertEqual(preflight.placement_warnings(s), [])
        # The rotation part is untouched: a drop is not a turn.
        self.assertEqual(eff[:9], s._object_nums("2")[:9])
        change = [c for c in s.pending if c["kind"] == "orient"][0]
        self.assertEqual(change["old"], "12.0 mm above the plate")
        self.assertEqual(change["new"], "resting on the plate")

    def test_auto_orient_on_a_well_placed_object_still_stages_nothing(self):
        s = ProjectSession(self.project)
        self.assertFalse(s.stage_orient("2"))
        self.assertFalse(s.orient_is_drop("2"))
        self.assertEqual(s.pending, [])

    def test_too_tall_reads_the_printer_height(self):
        s = ProjectSession(self.project)
        s.stage_setting("printable_height", "60")
        found = preflight.placement_warnings(s)
        self.assertEqual([w["check"] for w in found], ["too_tall"])
        self.assertIn("reaches 77 mm, but this printer prints up to 60 mm",
                      found[0]["text"])

    def test_tiny_contact_on_the_real_cane(self):
        project = self.sandbox / "cane.3mf"
        shutil.copy2(CANE, project)
        s = ProjectSession(project)
        found = preflight.placement_warnings(s)
        self.assertEqual([w["check"] for w in found], ["contact"])
        text = found[0]["text"]
        self.assertIn("cane.stl stands on only about 20 square "
                      "millimetres of plate (its outline covers about 1.3 "
                      "square centimetres) and is 124 mm tall", text)
        self.assertIn("The brim is set to automatic", text)
        self.assertIn("auto-orient the object on the Placement tab", text)
        # The fix follows the brim setting...
        s.stage_setting("brim_type", "no_brim")
        self.assertIn("Add a brim (Adhesion tab).",
                      preflight.placement_warnings(s)[0]["text"])
        s.stage_setting("brim_type", "outer_only")
        self.assertIn("A brim is already on - a raft",
                      preflight.placement_warnings(s)[0]["text"])
        # ...and a raft IS the contact, so the warning goes away.
        s.stage_setting("raft_layers", "2")
        self.assertEqual(preflight.placement_warnings(s), [])

    def test_the_dodecagon_through_creality_is_quiet(self):
        project = self.sandbox / "dodecagon.3mf"
        shutil.copy2(DODECAGON, project)
        s = backends.get_backend("creality").open_project(project)
        self.assertEqual(preflight.placement_warnings(s), [])


class SpiralCheckTests(ProjectCase):

    def setUp(self):
        super().setUp()
        support.add_duplicate_object(self.project)

    def test_two_objects_by_layer_in_spiral_mode_warn(self):
        s = ProjectSession(self.project)
        s.stage_setting("spiral_mode", "1")
        found = preflight.spiral_warnings(s)
        self.assertEqual(len(found), 1)
        self.assertIn("Spiral vase mode is on, but the plate holds 2 objects "
                      "(blind-Benchy.stl and second-model.stl) printed by "
                      "layer", found[0]["text"])
        self.assertIn("print by object", found[0]["text"])

    def test_by_object_hands_over_to_the_clearance_rules(self):
        s = ProjectSession(self.project)
        s.stage_setting("spiral_mode", "1")
        s.stage_setting("print_sequence", "by object")
        self.assertEqual(preflight.spiral_warnings(s), [])
        self.assertIn("rod", checks(s))

    def test_one_object_per_plate_is_fine(self):
        support.add_empty_plate(self.project, "2")
        s = ProjectSession(self.project)
        s.stage_setting("spiral_mode", "1")
        s.stage_replate("102", "2")
        self.assertEqual(preflight.spiral_warnings(s), [])

    def test_spiral_off_never_warns(self):
        s = ProjectSession(self.project)
        self.assertEqual(preflight.spiral_warnings(s), [])


# ------------------------- the lever rule and per-plate print sequence


def set_plate_metadata(project_path, plate_id, key, value):
    """Add one metadata line to a plate block, the way Bambu Studio
    writes its per-plate settings (format from Studio's source; no
    saved specimen with such a line exists on this machine yet)."""
    entries = engine.load_entries(project_path)
    ms = entries[engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
    anchor = f'<metadata key="plater_id" value="{plate_id}"/>'
    assert ms.count(anchor) == 1
    ms = ms.replace(anchor, anchor + f'\n    <metadata key="{key}" '
                    f'value="{value}"/>', 1)
    entries[engine.MODEL_SETTINGS_ENTRY] = ms.encode("utf-8")
    engine.save_entries(project_path, entries)


class LeverRuleTests(ProjectCase):
    """A part much taller than its foot is wide sways as the head
    passes. The Benchy is 77 mm on a 4.9 square cm foot, ratio 3.5 -
    fine at the real limit of 8, over a limit lowered to 3."""

    def test_the_benchy_is_within_the_real_limit(self):
        s = ProjectSession(self.project)
        self.assertEqual(preflight.placement_warnings(s), [])

    def test_a_lowered_limit_speaks_the_ratio(self):
        s = ProjectSession(self.project)
        original = preflight.LEVER_RATIO
        preflight.LEVER_RATIO = 3.0
        try:
            found = preflight.placement_warnings(s)
        finally:
            preflight.LEVER_RATIO = original
        self.assertEqual([w["check"] for w in found], ["lever"])
        text = found[0]["text"]
        self.assertIn("blind-Benchy.stl is 77 mm tall on a foot of about "
                      "4.9 square centimetres - about 3 times taller than "
                      "its foot is wide", text)
        self.assertIn("Printing it slower helps too", text)

    def test_a_tiny_foot_keeps_the_tiny_wording_not_the_lever(self):
        project = self.sandbox / "cane.3mf"
        shutil.copy2(CANE, project)
        s = ProjectSession(project)
        self.assertEqual([w["check"] for w in preflight.placement_warnings(s)],
                         ["contact"])


class PerPlateSequenceTests(ProjectCase):
    """Bambu Studio's per-plate print sequence and spiral mode, read
    from the plate block and winning over the project-wide value for
    that plate only."""

    def setUp(self):
        super().setUp()
        support.add_duplicate_object(self.project)

    def test_plates_read_absent_settings_as_none(self):
        s = ProjectSession(self.project)
        plate = s.plates()[0]
        self.assertIsNone(plate["print_sequence"])
        self.assertIsNone(plate["spiral_mode"])

    def test_a_plate_level_by_object_switches_the_clearance_rules_on(self):
        set_plate_metadata(self.project, "1", "print_sequence", "by object")
        s = ProjectSession(self.project)
        self.assertEqual(s.plates()[0]["print_sequence"], "by object")
        self.assertEqual(s.settings["print_sequence"], "by layer")
        self.assertEqual(checks(s), ["rod", "radius"])

    def test_a_plate_level_by_layer_wins_over_a_project_by_object(self):
        set_plate_metadata(self.project, "1", "print_sequence", "by layer")
        s = ProjectSession(self.project)
        s.stage_setting("print_sequence", "by object")
        self.assertEqual(checks(s), [])

    def test_a_plate_level_spiral_mode_is_honoured(self):
        set_plate_metadata(self.project, "1", "spiral_mode", "1")
        s = ProjectSession(self.project)
        self.assertEqual(s.settings["spiral_mode"], "0")
        self.assertEqual(checks(s), ["spiral"])
        # And a plate-level off silences a project-wide on.
        other = support.copy_test_project(self.sandbox, name="other.3mf")
        support.add_duplicate_object(other)
        set_plate_metadata(other, "1", "spiral_mode", "0")
        s = ProjectSession(other)
        s.stage_setting("spiral_mode", "1")
        self.assertEqual(checks(s), [])


if __name__ == "__main__":
    unittest.main()
