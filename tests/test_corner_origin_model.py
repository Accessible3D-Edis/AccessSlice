"""A model whose own origin is a corner, and a model that nearly fills
the plate (user report, 2026-11-14, A1 mini).

A 176 x 176 mm terrain tile on a 180 x 180 mm plate. The STL has its
corner at its origin, and the project keeps that offset in the part's
component transform (88, 88), so the build item's translation is NOT
the model's centre. Two bugs followed:

- "Centre on the plate" put the item translation at the plate centre,
  so the tile's front-left corner landed there and the rest hung off
  the plate. Placement now shows, takes and centres the model's REAL
  centre (its bounding box's), in typed fields, the Centre button and
  the 3D view's drag alike.
- Arrange (also run by every new job) refused the tile: it kept 5 mm
  from each plate edge, and 176 + 10 > 180. The margin is now a
  preference: a layout that only fits without it still gets made,
  with a note that a brim or skirt may not fit.

The Benchy test project stands in for the tile: its part is shifted
the way the tile's is, so the same offset arithmetic is exercised.
"""

import http.client
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
from slicer_project import ProjectSession

SHIFT = (60.0, 40.0)


def shift_part(project_path, dx, dy):
    """Move the Benchy's mesh inside its object by (dx, dy) and the
    build item back by the same amount: the model stays exactly where
    it was on the plate, but its item translation no longer is its
    centre - the corner-origin shape of the user's file."""
    entries = engine.load_entries(project_path)
    model = entries[engine.MODEL_ENTRY].decode("utf-8")
    model, n = re.subn(
        r'(<component [^>]*transform="1 0 0 0 1 0 0 0 1 )0 0 (0")',
        rf"\g<1>{dx:g} {dy:g} \g<2>", model)
    assert n == 1, "component transform not found"
    model, n = re.subn(
        r'(<item objectid="2"[^>]*transform="1 0 0 0 1 0 0 0 1 )'
        r'165 160 ',
        rf"\g<1>{165 - dx:g} {160 - dy:g} ", model)
    assert n == 1, "item transform not found"
    entries[engine.MODEL_ENTRY] = model.encode("utf-8")
    engine.save_entries(project_path, entries)


def world_centre(session, objectid):
    eff = session.effective_item_nums(objectid)
    bb = geometry.bbox([geometry.transform_point(eff, v)
                        for v in session.object_shape(objectid)[0]])
    return (bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2


class ArrangeMarginTests(unittest.TestCase):

    def test_a_model_that_only_fits_without_the_margin_is_centred(self):
        self.assertEqual(geometry.arrange_rects([(176, 176)], 180, 180),
                         [(90.0, 90.0)])

    def test_what_fits_inside_the_margin_still_keeps_it(self):
        spots = geometry.arrange_rects([(100, 100)], 180, 180)
        self.assertEqual(spots, [(90.0, 90.0)])
        spots = geometry.arrange_rects([(80, 80), (80, 80)], 180, 180)
        for (cx, _cy) in spots:
            self.assertGreaterEqual(cx - 40, geometry.ARRANGE_MARGIN_MM)

    def test_larger_than_the_plate_is_still_refused(self):
        self.assertIsNone(geometry.arrange_rects([(181, 100)], 180, 180))
        self.assertIsNone(geometry.arrange_rects([(100, 100), (100, 100)],
                                                 180, 180))


class OffsetSessionTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)
        shift_part(self.project, *SHIFT)

    def test_the_offset_is_read_from_the_part(self):
        s = ProjectSession(self.project)
        o = s.objects()[0]
        self.assertAlmostEqual(o["x"], 165 - SHIFT[0])
        _size, (ox, oy), readable = s.object_footprint("2")
        self.assertTrue(readable)
        self.assertAlmostEqual(ox, SHIFT[0])
        self.assertAlmostEqual(oy, SHIFT[1])
        # The model itself still stands where it always stood.
        cx, cy = world_centre(s, "2")
        self.assertAlmostEqual(cx, 165)
        self.assertAlmostEqual(cy, 160)

    def test_the_placement_page_shows_the_real_centre(self):
        s = ProjectSession(self.project)
        body = webui.render_placement_tab(s)
        self.assertIn('name="x__2" value="165"', body)
        self.assertIn('name="y__2" value="160"', body)
        self.assertIn("Currently at the centre of the plate.", body)

    def test_arrange_centres_the_real_shape(self):
        s = ProjectSession(self.project)
        s.pending.append({"kind": "move", "objectid": "2",
                          "old": (105.0, 120.0), "new": (20.0, 20.0),
                          "label": "x", "source": None})
        s.stage_arrange()
        cx, cy = world_centre(s, "2")
        self.assertAlmostEqual(cx, 165)
        self.assertAlmostEqual(cy, 160)


class PlacementFormTests(unittest.TestCase):
    """The Placement form over real HTTP, on the shifted project."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox = support.make_sandbox()
        projects_dir = cls.sandbox / "projects"
        projects_dir.mkdir()
        cls.project = support.copy_test_project(projects_dir)
        shift_part(cls.project, *SHIFT)
        fake_exe = support.make_fake_studio(cls.sandbox)
        cls.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = cls.sandbox / "config.json"
        config_store.save({"slicer": "bambu", "slicer_exe": str(fake_exe),
                           "projects_dir": str(projects_dir)})
        cls.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(cls.sandbox / "AppData")
        server.state["session"] = None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever,
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.state["session"] = None
        server.state["watcher"].stop()
        config_store.CONFIG_PATH = cls.old_config_path
        if cls.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = cls.old_appdata
        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def post(self, url, form):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=30)
        try:
            body = urllib.parse.urlencode(form)
            conn.request("POST", url, body, {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body))})
            resp = conn.getresponse()
            loc = resp.getheader("Location") or ""
            return resp.status, urllib.parse.unquote_plus(loc)
        finally:
            conn.close()

    def setUp(self):
        status, _loc = self.post("/open", {"path": str(self.project)})
        self.assertEqual(status, 303)
        server.state["session"].pending.clear()

    def session(self):
        return server.state["session"]

    def test_an_untouched_form_stages_nothing(self):
        _status, loc = self.post("/project/placement",
                                 {"x__2": "165", "y__2": "160"})
        self.assertIn("Nothing changed", loc)
        self.assertEqual(self.session().pending, [])

    def test_a_typed_centre_is_where_the_model_lands(self):
        self.post("/project/placement", {"x__2": "100", "y__2": "120"})
        moves = [c for c in self.session().pending if c["kind"] == "move"]
        self.assertEqual(moves[0]["new"],
                         (100.0 - SHIFT[0], 120.0 - SHIFT[1]))
        cx, cy = world_centre(self.session(), "2")
        self.assertAlmostEqual(cx, 100)
        self.assertAlmostEqual(cy, 120)

    def test_centre_puts_the_shape_not_the_anchor_on_the_centre(self):
        self.post("/project/placement",
                  {"x__2": "100", "y__2": "120"})
        self.post("/project/placement",
                  {"centre": "2", "x__2": "100", "y__2": "120"})
        cx, cy = world_centre(self.session(), "2")
        self.assertAlmostEqual(cx, 165)
        self.assertAlmostEqual(cy, 160)

    def test_an_edge_past_the_plate_warns(self):
        # Centre 20 mm from the right edge; the Benchy is 50 mm wide.
        _status, loc = self.post("/project/placement",
                                 {"x__2": "310", "y__2": "160"})
        self.assertIn("outside the 330 by 320 mm plate", loc)


class EdgeNoteTests(unittest.TestCase):

    def test_close_to_the_edge_is_named(self):
        s = ProjectSession(support.TEST_PROJECT)
        notes = s.edge_notes([("tile", (176, 176), (90, 90))], (180, 180))
        self.assertEqual(len(notes), 1)
        self.assertIn("tile", notes[0])
        self.assertIn("brim or skirt", notes[0])
        self.assertEqual(
            s.edge_notes([("tile", (100, 100), (90, 90))], (180, 180)), [])


class OffPlateWordingTests(unittest.TestCase):

    def test_an_edge_past_the_plate_warns_though_the_centre_is_on_it(self):
        text = webui._describe_position(178, 178, (180, 180), (176, 176))
        self.assertIn("part of it is outside the build plate", text)

    def test_a_centred_tile_reads_as_centred_without_warning(self):
        text = webui._describe_position(90, 90, (180, 180), (176, 176))
        self.assertEqual(text, "Currently at the centre of the plate.")


if __name__ == "__main__":
    unittest.main()
