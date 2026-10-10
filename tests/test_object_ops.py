"""Copies, removal, adding a model file, new plates and the spill
arrange - Edis's asks of 2026-09-08 ("if I have an object but I want
2 or 3 or 4"; the three spool halves that could not share one plate).

Everything is staged, reviewed and written through the same safe
save as every other change. What is pinned here:

- a copy is a full object in the Bambu family (its own mesh file,
  rels entry, model_settings block, plate membership, assemble
  lines) and an inline object in a PrusaSlicer project; copies are
  numbered past the ones that exist; the written file verifies and
  every copy's shape reads back;
- copies and added models are laid out with the rest of the plate,
  spilling onto NEW plates when they do not fit (Bambu family) or
  refused in words with the sizes (PrusaSlicer);
- removal drops the object everywhere (and an unreferenced mesh file),
  refuses the last object, and drops the object's other staged
  changes;
- a new plate is created at save time, the object centred on it, the
  grid stays reliable, and objects staying on plates that the grown
  grid moves follow their plate;
- the honest arrange message states the footprints and the
  arithmetic; the spill arrange moves what does not fit;
- the Placement tab carries the controls and the routes stage them.
"""

import http.client
import os
import re
import shutil
import sys
import threading
import unittest
import urllib.parse
import zipfile
from http.server import ThreadingHTTPServer

import support
import config_store
import geometry
import newjob
import object_ops
import prusa_format
import prusa_newjob
import server
import viewport
import webui
from errors import ProjectError
from prusa_project import PrusaProjectSession
from slicer_project import ProjectSession
import edit_bambu_3mf as engine

COREONE = (support.REPO_ROOT / "testdata"
           / "blind-benchy-core-one-built-by-accessslice-from-prusaslicer-presets.3mf")


def _no_overlap(session):
    objs = session.objects()
    boxes = []
    for o in objs:
        (w, d), (ox, oy), _n = session._footprint(o["id"])
        boxes.append((o["x"] + ox, o["y"] + oy, w, d))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if (abs(a[0] - b[0]) < (a[2] + b[2]) / 2
                    and abs(a[1] - b[1]) < (a[3] + b[3]) / 2):
                return False
    return True


def _local(session, objectid):
    o = next(o for o in session.objects() if o["id"] == objectid)
    origin = session.plate_origin(session.plate_of_object(objectid))
    return o["x"] - origin[0], o["y"] - origin[1]


class BambuObjectOps(unittest.TestCase):
    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.path = support.copy_test_project(self.sandbox)
        self.s = ProjectSession(self.path)

    def _zip(self):
        return zipfile.ZipFile(self.path)

    def test_copies_are_full_objects_laid_out_apart(self):
        count, notes = self.s.stage_copy("2", 3)
        self.assertEqual(count, 3)
        change = self.s.pending[-1]
        self.assertEqual(change["kind"], "copy")
        self.assertEqual(change["label"], "Copies of blind-Benchy.stl")
        self.assertEqual(len(change["placements"]), 3)
        self.s.save()
        objs = self.s.objects()
        self.assertEqual([o["name"] for o in objs], [
            "blind-Benchy.stl", "blind-Benchy.stl (copy 1)",
            "blind-Benchy.stl (copy 2)", "blind-Benchy.stl (copy 3)"])
        self.assertEqual(len({o["id"] for o in objs}), 4)
        self.assertTrue(_no_overlap(self.s))
        for o in objs:
            shape = self.s.object_shape(o["id"])
            self.assertTrue(shape and len(shape[0]) > 1000, o["name"])
        self.assertEqual(engine.verify(self.path, list(self.s.entries)), [])
        with self._zip() as z:
            names = z.namelist()
            self.assertEqual(
                sorted(n for n in names if n.startswith("3D/Objects/")),
                [f"3D/Objects/object_{i}.model" for i in range(1, 5)])
            rels = z.read("3D/_rels/3dmodel.model.rels").decode()
            self.assertEqual(rels.count("<Relationship "), 4)
            ms = z.read("Metadata/model_settings.config").decode()
            self.assertEqual(ms.count("<model_instance>"), 4)
            self.assertEqual(ms.count("<assemble_item"), 8)
            self.assertEqual(ms.count('<object id="'), 4)
            model = z.read("3D/3dmodel.model").decode()
            self.assertEqual(len(set(re.findall(r'p:UUID="([^"]+)"', model))),
                             model.count("p:UUID="))
        # The 3D view lists all four with their meshes.
        scene = viewport.scene(self.s, all_plates=True)
        self.assertEqual(len(scene["objects"]), 4)

    def test_copy_numbering_continues_past_existing_copies(self):
        self.s.stage_copy("2", 1)
        self.s.save()
        self.s.stage_copy("2", 2)
        self.s.save()
        self.assertEqual(
            sorted(o["name"] for o in self.s.objects())[1:],
            ["blind-Benchy.stl (copy 1)", "blind-Benchy.stl (copy 2)",
             "blind-Benchy.stl (copy 3)"])

    def test_copy_input_is_checked_in_words(self):
        for bad in ("0", "21", "x", ""):
            with self.assertRaises(ProjectError) as ctx:
                self.s.stage_copy("2", bad)
            self.assertIn("copies", str(ctx.exception))
        with self.assertRaises(ProjectError):
            self.s.stage_copy("99", 1)
        self.s.stage_copy("2", 1)
        with self.assertRaises(ProjectError) as ctx:
            self.s.stage_copy("2", 1)
        self.assertIn("already staged", str(ctx.exception))

    def test_remove_drops_everything_and_keeps_the_last_object(self):
        with self.assertRaises(ProjectError) as ctx:
            self.s.stage_remove("2")
        self.assertIn("last object", str(ctx.exception))
        self.s.stage_copy("2", 1)
        self.s.save()
        copy_id = self.s.objects()[1]["id"]
        # Other staged changes of the object go with the removal.
        self.s.stage_rotate(copy_id, "45")
        self.assertTrue(self.s.stage_remove(copy_id))
        self.assertEqual([c["kind"] for c in self.s.pending], ["remove"])
        self.assertFalse(self.s.stage_remove(copy_id))
        self.assertIn(copy_id, self.s.removed_ids())
        with self.assertRaises(ProjectError):
            self.s.stage_copy(copy_id, 1)
        self.s.save()
        self.assertEqual([o["id"] for o in self.s.objects()], ["2"])
        self.assertEqual(engine.verify(self.path, list(self.s.entries)), [])
        with self._zip() as z:
            names = z.namelist()
            self.assertEqual([n for n in names if n.startswith("3D/Objects/")],
                             ["3D/Objects/object_1.model"])
            rels = z.read("3D/_rels/3dmodel.model.rels").decode()
            self.assertEqual(rels.count("<Relationship "), 1)
            ms = z.read("Metadata/model_settings.config").decode()
            self.assertEqual(ms.count("<model_instance>"), 1)
            self.assertEqual(ms.count("<assemble_item"), 2)

    def test_new_plate_is_created_at_save_and_the_grid_stays_reliable(self):
        self.s.stage_copy("2", 1)
        self.s.save()
        copy_id = self.s.objects()[1]["id"]
        slot = self.s.stage_new_plate(copy_id)
        self.assertEqual(slot, 0)
        change = self.s.pending[-1]
        self.assertEqual((change["kind"], change["new"]),
                         ("replate_new", "a new plate"))
        layout = self.s.planned_layout()
        stride = self.s.plate_grid()["stride"]
        self.assertEqual(layout[("new", 0)], (stride, 0.0))
        eff = self.s.effective_item_nums(copy_id)
        size = self.s.plate_size()
        self.assertAlmostEqual(eff[9], stride + size[0] / 2, places=3)
        # The 3D view draws the planned plate as an outline.
        scene = viewport.scene(self.s, all_plates=True)
        self.assertTrue(any(p["id"] == "new-0" for p in scene["plates"]))
        self.s.save()
        plates = self.s.plates()
        self.assertEqual([(p["id"], p["object_ids"]) for p in plates],
                         [("1", ["2"]), ("2", [copy_id])])
        grid = self.s.plate_grid()
        self.assertTrue(grid["reliable"])
        lx, ly = _local(self.s, copy_id)
        self.assertAlmostEqual(lx, size[0] / 2, places=3)
        self.assertAlmostEqual(ly, size[1] / 2, places=3)
        self.assertEqual(engine.verify(self.path, list(self.s.entries)), [])
        with self._zip() as z:
            ms = z.read("Metadata/model_settings.config").decode()
            self.assertEqual(ms.count("<plate>"), 2)
            self.assertIn('key="plater_id" value="2"', ms)
            # The dual-nozzle map keys travel to the new plate.
            self.assertEqual(ms.count('key="filament_map_mode"'), 2)

    def test_objects_follow_their_plate_when_the_grid_grows(self):
        # Four plates with one object each (two columns); the fifth
        # plate makes three columns, moving plates 3 and 4.
        self.s.stage_copy("2", 4)
        self.s.save()
        ids = [o["id"] for o in self.s.objects()]
        for oid in ids[1:4]:
            self.s.stage_new_plate(oid)
            self.s.save()
        self.assertEqual(self.s.plate_grid()["cols"], 2)
        before = {oid: _local(self.s, oid) for oid in ids}
        self.s.stage_new_plate(ids[4])
        # Preview: the objects on plates 3 and 4 already read shifted.
        stride = self.s.plate_grid()["stride"]
        eff3 = self.s.effective_item_nums(ids[2])
        self.assertAlmostEqual(eff3[9], 2 * stride + before[ids[2]][0],
                               places=3)
        self.s.save()
        grid = self.s.plate_grid()
        self.assertEqual((grid["cols"], grid["reliable"]), (3, True))
        size = self.s.plate_size()
        for oid in ids:
            lx, ly = _local(self.s, oid)
            self.assertTrue(0 <= lx <= size[0] and 0 <= ly <= size[1],
                            (oid, lx, ly))
            if oid == ids[4]:
                continue  # moved on purpose: centred on the new plate
            self.assertAlmostEqual(lx, before[oid][0], places=3)
            self.assertAlmostEqual(ly, before[oid][1], places=3)
        self.assertEqual(self.s.plate_of_object(ids[4]), "5")

    def test_copies_that_do_not_fit_spill_onto_new_plates(self):
        self.s.stage_scale("2", "400")
        self.s.save()
        count, notes = self.s.stage_copy("2", 3)
        self.assertTrue(any("new plate" in n for n in notes), notes)
        change = self.s.pending[-1]
        self.assertTrue(all(isinstance(k, tuple)
                            for k, _x, _y in change["placements"]))
        self.assertIn("on new plates", change["new"])
        self.s.save()
        plates = self.s.plates()
        self.assertEqual(len(plates), 4)
        self.assertTrue(all(len(p["object_ids"]) == 1 for p in plates))
        self.assertTrue(self.s.plate_grid()["reliable"])
        size = self.s.plate_size()
        for o in self.s.objects():
            lx, ly = _local(self.s, o["id"])
            self.assertTrue(0 <= lx <= size[0] and 0 <= ly <= size[1])
        self.assertEqual(engine.verify(self.path, list(self.s.entries)), [])

    def test_arrange_refusal_states_the_sizes(self):
        self.s.stage_copy("2", 1)
        self.s.save()
        for o in self.s.objects():
            self.s.stage_scale(o["id"], "400")
        self.s.save()
        with self.assertRaises(ProjectError) as ctx:
            self.s.stage_arrange()
        text = str(ctx.exception)
        self.assertIn("Footprints:", text)
        self.assertIn("side by side need", text)
        self.assertIn("spills onto new plates", text)
        # The spill arrange stages one object onto a new plate.
        staged, notes = self.s.stage_arrange_spill()
        self.assertEqual(
            sorted(c["kind"] for c in self.s.pending
                   if c["kind"] in ("move", "replate_new")),
            ["move", "replate_new"])
        self.s.save()
        self.assertEqual(len(self.s.plates()), 2)
        self.assertTrue(self.s.plate_grid()["reliable"])

    def test_add_model_imports_the_engines_object_and_cleans_up(self):
        merged = support.copy_test_project(self.sandbox, name="merged.3mf")
        support.add_duplicate_object(merged)
        script = support.make_fake_merge(self.sandbox)
        model = self.sandbox / "second-model.stl"
        model.write_bytes(b"solid fake")
        original = newjob.build_merge_command
        newjob.build_merge_command = (
            lambda exe, donor, models, out:
            [sys.executable, str(script), str(merged), out, "success"])
        self.addCleanup(setattr, newjob, "build_merge_command", original)
        names, notes = self.s.stage_add_model(str(model), "fake.exe")
        self.assertEqual(names, ["second-model.stl"])
        change = self.s.pending[-1]
        self.assertEqual(change["kind"], "add_model")
        source = change["source"]
        self.assertTrue(os.path.isfile(source))
        # Discarding cleans the staging file; staging again works.
        self.s.discard_pending(len(self.s.pending) - 1)
        self.assertFalse(os.path.exists(source))
        self.s.stage_add_model(str(model), "fake.exe")
        source = self.s.pending[-1]["source"]
        self.s.save()
        self.assertFalse(os.path.exists(source))
        objs = self.s.objects()
        self.assertEqual([o["name"] for o in objs],
                         ["blind-Benchy.stl", "second-model.stl"])
        self.assertTrue(self.s.object_shape(objs[1]["id"]))
        self.assertTrue(_no_overlap(self.s))
        self.assertEqual(engine.verify(self.path, list(self.s.entries)), [])

    def test_add_model_needs_a_real_file_and_a_slicer(self):
        with self.assertRaises(ProjectError):
            self.s.stage_add_model(str(self.sandbox / "missing.stl"), "x")
        model = self.sandbox / "m.stl"
        model.write_bytes(b"solid")
        with self.assertRaises(ProjectError) as ctx:
            self.s.stage_add_model(str(model), "")
        self.assertIn("Settings", str(ctx.exception))


class PrusaObjectOps(unittest.TestCase):
    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.path = self.sandbox / "core-one.3mf"
        shutil.copy2(COREONE, self.path)
        self.s = PrusaProjectSession(self.path)

    def test_copies_and_removal_in_the_inline_format(self):
        oid = self.s.objects()[0]["id"]
        self.s.stage_copy(oid, 2)
        self.s.save()
        objs = self.s.objects()
        self.assertEqual([o["name"] for o in objs], [
            "blind-benchy.stl", "blind-benchy.stl (copy 1)",
            "blind-benchy.stl (copy 2)"])
        self.assertEqual(len({o["id"] for o in objs}), 3)
        self.assertTrue(_no_overlap(self.s))
        for o in objs:
            self.assertTrue(self.s.object_shape(o["id"]))
        entries = prusa_format.object_entries(self.s.ms_xml)
        self.assertEqual(len(entries), 3)
        self.assertEqual(self.s._verify(list(self.s.entries),
                                        len(self.s.settings)), [])
        self.s.stage_remove(objs[1]["id"])
        self.s.save()
        self.assertEqual([o["id"] for o in self.s.objects()],
                         [objs[0]["id"], objs[2]["id"]])
        self.assertEqual(len(prusa_format.object_entries(self.s.ms_xml)), 2)

    def test_one_bed_means_no_new_plates_and_honest_refusals(self):
        oid = self.s.objects()[0]["id"]
        with self.assertRaises(ProjectError) as ctx:
            self.s.stage_new_plate(oid)
        self.assertIn("single bed", str(ctx.exception))
        self.s.stage_scale(oid, "400")
        self.s.save()
        with self.assertRaises(ProjectError) as ctx:
            self.s.stage_copy(oid, 1)
        text = str(ctx.exception)
        self.assertIn("Footprints:", text)
        self.assertNotIn("new plates", text)

    def test_add_model_through_the_fake_export(self):
        script = support.make_fake_prusa_engine(self.sandbox)
        entries = engine.load_entries(COREONE)
        del entries[prusa_format.CONFIG_ENTRY]
        fixture = self.sandbox / "cli-export.3mf"
        engine.save_entries(fixture, entries)
        original = prusa_newjob.build_export_command
        prusa_newjob.build_export_command = (
            lambda exe, models, out, centre:
            [sys.executable, str(script), out, "export", str(fixture)])
        self.addCleanup(setattr, prusa_newjob, "build_export_command",
                        original)
        stl = self.sandbox / "another.stl"
        stl.write_bytes(b"\0" * 84)
        names, _notes = self.s.stage_add_model(str(stl), "fake.exe")
        self.assertEqual(len(names), 1)
        self.s.save()
        objs = self.s.objects()
        self.assertEqual(len(objs), 2)
        self.assertTrue(_no_overlap(self.s))
        self.assertEqual(self.s._verify(list(self.s.entries),
                                        len(self.s.settings)), [])


# ------------------------------------------------------- over HTTP

class F:
    httpd = None
    port = None
    sandbox = None
    project = None
    old_config = None
    old_appdata = None


def setUpModule():
    F.sandbox = support.make_sandbox()
    projects = F.sandbox / "projects"
    projects.mkdir()
    F.project = support.copy_test_project(projects)
    fake_exe = support.make_fake_studio(F.sandbox)
    F.old_config = config_store.CONFIG_PATH
    config_store.CONFIG_PATH = F.sandbox / "config.json"
    config_store.save({"slicer": "bambu", "slicer_exe": str(fake_exe),
                       "projects_dir": str(projects),
                       "known_printers": ["Bambu Lab H2C 0.4 nozzle"]})
    F.old_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(F.sandbox / "AppData")
    support.write_studio_conf(os.environ["APPDATA"], {
        "app": {}, "presets": {"machine": "Bambu Lab H2C 0.4 nozzle"}})
    server.state["session"] = None
    F.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    F.port = F.httpd.server_address[1]
    threading.Thread(target=F.httpd.serve_forever, daemon=True).start()


def tearDownModule():
    F.httpd.shutdown()
    F.httpd.server_close()
    server.state["session"] = None
    config_store.CONFIG_PATH = F.old_config
    if F.old_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = F.old_appdata
    shutil.rmtree(F.sandbox, ignore_errors=True)


def request(method, url, form=None):
    conn = http.client.HTTPConnection("127.0.0.1", F.port, timeout=30)
    try:
        if form is None:
            conn.request(method, url)
        else:
            conn.request(method, url, body=urllib.parse.urlencode(form),
                         headers={"Content-Type":
                                  "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode("utf-8")
    finally:
        conn.close()


def msg_of(headers):
    q = urllib.parse.urlparse(headers.get("Location", "")).query
    return urllib.parse.parse_qs(q).get("msg", [""])[0]


class PlacementRoutes(unittest.TestCase):
    def setUp(self):
        server.state["session"] = None
        shutil.copy2(support.TEST_PROJECT, F.project)
        status, _h, _b = request("POST", "/open", {"path": str(F.project)})
        self.assertEqual(status, 303)

    def test_the_tab_carries_the_new_controls(self):
        _s, _h, body = request("GET", "/project/placement")
        self.assertIn('name="copies__2"', body)
        self.assertIn('name="copy" value="2"', body)
        self.assertIn('name="remove" value="2"', body)
        self.assertIn('value="new"', body)
        self.assertIn("A new plate (added when you save)", body)
        self.assertIn('name="arrange_spill"', body)
        self.assertIn('name="addmodel__1"', body)
        self.assertIn('name="add_model" value="1"', body)

    def test_copy_stages_and_review_lists_it(self):
        status, headers, _b = request("POST", "/project/placement", {
            "copy": "2", "copies__2": "2", "x__2": "165", "y__2": "160"})
        self.assertEqual(status, 303)
        self.assertIn("Staged 2 copies of blind-Benchy.stl", msg_of(headers))
        _s, _h, body = request("GET", "/project/review")
        self.assertIn("Copies of blind-Benchy.stl", body)
        self.assertIn("2 more, placed by arranging", body)
        _s, _h, body = request("GET", "/project/placement")
        self.assertIn("2 copies are staged, not saved", body)

    def test_bad_copy_count_is_a_message_not_a_crash(self):
        status, headers, _b = request("POST", "/project/placement", {
            "copy": "2", "copies__2": "many"})
        self.assertEqual(status, 303)
        self.assertIn("Nothing staged", msg_of(headers))
        self.assertIn("whole number", msg_of(headers))

    def test_remove_refuses_the_last_object_in_words(self):
        status, headers, _b = request("POST", "/project/placement", {
            "remove": "2"})
        self.assertEqual(status, 303)
        self.assertIn("last object", msg_of(headers))

    def test_move_to_a_new_plate_and_back(self):
        status, headers, _b = request("POST", "/project/placement", {
            "replate__2": "new", "x__2": "165", "y__2": "160",
            "rot__2": "0", "scale__2": "100", "basescale__2": "100"})
        self.assertEqual(status, 303)
        self.assertIn("1 change(s) staged", msg_of(headers))
        _s, _h, body = request("GET", "/project/placement")
        self.assertIn("a move to a new plate is staged", body)
        self.assertIn('value="new" selected', body)
        _s, _h, review = request("GET", "/project/review")
        self.assertIn("a new plate", review)
        # Choosing the current plate again drops it.
        status, headers, _b = request("POST", "/project/placement", {
            "replate__2": "1", "x__2": "165", "y__2": "160",
            "rot__2": "0", "scale__2": "100", "basescale__2": "100"})
        self.assertIn("1 change(s) staged", msg_of(headers))
        self.assertEqual(server.state["session"].pending, [])

    def test_add_model_over_http_through_the_fake_merge(self):
        merged = support.copy_test_project(F.sandbox, name="merged-http.3mf")
        support.add_duplicate_object(merged)
        script = support.make_fake_merge(F.sandbox)
        model = F.sandbox / "second-model.stl"
        model.write_bytes(b"solid fake")
        original = newjob.build_merge_command
        newjob.build_merge_command = (
            lambda exe, donor, models, out:
            [sys.executable, str(script), str(merged), out, "success"])
        self.addCleanup(setattr, newjob, "build_merge_command", original)
        status, headers, _b = request("POST", "/project/placement", {
            "add_model": "1", "addmodel__1": str(model)})
        self.assertEqual(status, 303)
        self.assertIn("second-model.stl will be added", msg_of(headers))
        status, headers, _b = request("POST", "/project/save", {
            "return_to": "/project/placement"})
        self.assertEqual(status, 303)
        _s, _h, body = request("GET", "/project/placement")
        self.assertIn("second-model.stl", body)
        self.assertEqual(len(server.state["session"].objects()), 2)

    def test_empty_model_path_is_asked_for(self):
        status, headers, _b = request("POST", "/project/placement", {
            "add_model": "1", "addmodel__1": ""})
        self.assertIn("full path", msg_of(headers))


if __name__ == "__main__":
    unittest.main()
