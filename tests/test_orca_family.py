"""The OrcaSlicer and Anycubic Slicer Next backends (built 2026-09-02).

One shared Orca-family implementation configured by a Flavour. Covers,
hermetically (stub installs with mini libraries under a sandbox
%APPDATA%):

- the two backends' identities, parts and registration,
- version stamps: Orca's separate "OrcaSlicer" metadata, Anycubic's
  Bambu-compatible Application string, cautions for other series and
  silence for another program's stamp,
- the preset library over installed vendors (Orca: the config's
  system folder decides; Anycubic: the shipped vendor), user presets,
  inheritance, scopes,
- the committed specimens (built by AccessSlice from each slicer's
  presets) opening cleanly with the right stamp and no notes,
- the project builder: default profiles by both naming conventions,
  the fake CLI export, objects set down on the plate, verification,
- preset lists for a Bambu-named and an Anycubic-named printer,
- every editor page over HTTP for an Orca project plus a fake slice
  to a report, and
- with the real programs installed: a real build and a real slice
  for each (about half a minute each).
"""

import http.client
import json
import os
import re
import shutil
import sys
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import support
import backends
import config_store
import creality_engine
import edit_bambu_3mf as engine
import geometry
import orca_family
import server
from errors import ProjectError
from slicer_project import ProjectSession

TESTDATA = support.TEST_PROJECT.parent
ORCA_SPECIMEN = (TESTDATA / "blind-benchy-x1c-built-by-accessslice-from-"
                 "orcaslicer-presets.3mf")
ANYCUBIC_SPECIMEN = (TESTDATA / "blind-benchy-kobra-s1-built-by-"
                     "accessslice-from-anycubic-presets.3mf")
X1C = "Bambu Lab X1 Carbon 0.4 nozzle"
KOBRA = "Anycubic Kobra S1 0.4 nozzle"


class SandboxCase(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.orca_exe = support.make_fake_orca(self.sandbox)
        self.any_exe = support.make_fake_anycubic(self.sandbox)
        self._old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(self.sandbox / "AppData")
        self.addCleanup(self._restore)
        self.orca = backends.get_backend("orca")
        self.anycubic = backends.get_backend("anycubic")

    def _restore(self):
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata


class BackendSurfaceTests(SandboxCase):

    def test_identities_parts_and_registration(self):
        for b, bid, name in ((self.orca, "orca", "OrcaSlicer"),
                             (self.anycubic, "anycubic",
                              "Anycubic Slicer Next")):
            self.assertEqual(b.id, bid)
            self.assertEqual(b.display_name, name)
            self.assertTrue(b.supported)
            self.assertIs(b.engine, creality_engine)
            self.assertIsNone(b.printer_switch)
            self.assertIsNone(b.lan)
            self.assertIn("slice", b.texts.SHORTCUTS)
            self.assertIn(bid, backends.supported_ids())
            self.assertIn((bid, name, True), config_store.SLICERS)
            # detect_exe looks at the real machine: a path or None.
            found = b.detect_exe()
            self.assertTrue(found is None or Path(found).is_file())
        self.assertNotEqual(self.orca.profiles, self.anycubic.profiles)

    def test_version_stamps(self):
        oc = self.orca.compat
        self.assertEqual(oc.series("OrcaSlicer-2.4.2"), "2.4")
        self.assertIsNone(oc.unverified_note("OrcaSlicer-2.4.2", "x"))
        self.assertIn("2.5", oc.unverified_note("OrcaSlicer-2.5.0", "x"))
        # Another program's stamp is no claim at all.
        self.assertEqual(oc.series("BambuStudio-02.08.02.61"), "")
        self.assertIsNone(oc.unverified_note("BambuStudio-02.08.02.61", "x"))
        ac = self.anycubic.compat
        self.assertEqual(ac.series("BambuStudio-1.4.1.2"), "1.4")
        self.assertIsNone(ac.unverified_note("BambuStudio-1.4.1.2", "x"))
        self.assertIn("1.5", ac.unverified_note("BambuStudio-1.5.0", "x"))
        self.assertEqual(ac.series("OrcaSlicer-2.4.2"), "")


class ProfileTests(SandboxCase):

    def test_orca_reads_the_installed_vendors(self):
        p = self.orca.profiles
        exe = str(self.orca_exe)
        self.assertEqual(p.vendors(exe), ["BBL"])   # Custom is not installed
        self.assertEqual(sorted(p.list_presets(exe, "machine")),
                         ["Bambu Lab H2C 0.4 nozzle", X1C])
        self.assertEqual(p.list_user_presets("process"), ["My Orca draft"])
        values = p.resolve_preset(exe, "process", "My Orca draft")
        self.assertEqual(values["wall_loops"], "5")
        self.assertEqual(p.scope_of(exe, "wall_loops"), "process")
        self.assertEqual(p.scope_of(exe, "nozzle_temperature"), "filament")
        self.assertIsNotNone(p.library_root(exe))
        with self.assertRaises(p.ProfileError):
            p.resolve_preset(exe, "machine", "No such printer")

    def test_orca_installed_vendors_grow_with_the_system_folder(self):
        (self.sandbox / "AppData" / "OrcaSlicer" / "system"
         / "Custom").mkdir()
        exe = str(self.orca_exe)
        self.assertEqual(self.orca.profiles.vendors(exe), ["BBL", "Custom"])
        self.assertIn("My Custom Printer 0.4 nozzle",
                      self.orca.profiles.list_presets(exe, "machine"))

    def test_anycubic_without_a_config_folder_uses_its_shipped_vendor(self):
        p = self.anycubic.profiles
        exe = str(self.any_exe)
        self.assertEqual(p.vendors(exe), ["Anycubic"])
        self.assertIsNone(p.user_library_root())
        self.assertEqual(p.list_user_presets("process"), [])
        self.assertEqual(sorted(p.list_presets(exe, "machine")),
                         ["Anycubic Kobra 3 0.4 nozzle", KOBRA])
        self.assertEqual(p.resolve_preset(exe, "process",
                                          "0.20mm Standard @Anycubic Kobra "
                                          "S1 0.4 nozzle")["wall_loops"], "3")


class SpecimenTests(SandboxCase):

    def test_the_orca_specimen_opens_with_its_own_stamp(self):
        s = self.orca.open_project(ORCA_SPECIMEN, slicer_exe=str(self.orca_exe))
        self.assertEqual(s.application, "OrcaSlicer-2.4.2")
        self.assertEqual(s.structure_notes, [])
        self.assertEqual(s.settings["printer_settings_id"], X1C)
        self.assertEqual(s.objects()[0]["name"], "blind-benchy.stl")
        self.assertEqual(s.plate_size(), (256.0, 256.0))
        self.assertIsInstance(s.settings["different_settings_to_system"], list)

    def test_the_anycubic_specimen_opens_with_its_own_stamp(self):
        s = self.anycubic.open_project(ANYCUBIC_SPECIMEN,
                                       slicer_exe=str(self.any_exe))
        self.assertEqual(s.application, "BambuStudio-1.4.1.2")
        self.assertEqual(s.structure_notes, [])
        self.assertEqual(s.settings["printer_settings_id"], KOBRA)
        self.assertEqual(s.plate_size(), (250.0, 250.0))

    def test_the_bambu_backend_sees_orcas_stamp_and_stays_quiet(self):
        s = backends.get_backend("bambu").open_project(ORCA_SPECIMEN)
        self.assertEqual(s.application, "OrcaSlicer-2.4.2")
        self.assertFalse(any("version" in n for n in s.structure_notes))


class BuilderTests(SandboxCase):

    def setUp(self):
        super().setUp()
        self.script = support.make_fake_prusa_engine(self.sandbox)
        # The CLI's export: the Orca specimen sunk 20 mm into the bed.
        entries = engine.load_entries(ORCA_SPECIMEN)
        model = entries[engine.MODEL_ENTRY].decode("utf-8")
        m = re.search(r'(<item objectid="\d+"[^>]*transform=")([^"]*)(")',
                      model)
        nums = [float(v) for v in m.group(2).split()]
        nums[11] -= 20
        model = (model[:m.start()] + m.group(1)
                 + " ".join(f"{v:g}" for v in nums) + m.group(3)
                 + model[m.end():])
        entries[engine.MODEL_ENTRY] = model.encode("utf-8")
        self.fixture = self.sandbox / "cli-export.3mf"
        engine.save_entries(self.fixture, entries)
        self._original = orca_family.build_from_presets_command
        self.addCleanup(setattr, orca_family, "build_from_presets_command",
                        self._original)
        script, fixture = self.script, self.fixture
        orca_family.build_from_presets_command = \
            lambda exe, m, p, f, models, out: \
            [sys.executable, str(script), out, "export", str(fixture)]
        self.stl = self.sandbox / "blind-benchy.stl"
        self.stl.write_bytes(b"\0" * 84)

    def test_default_profiles_by_both_naming_conventions(self):
        machine, process, filament = self.orca.newjob._default_profiles(
            str(self.orca_exe), X1C)
        self.assertEqual(process, "0.20mm Standard @BBL X1C")
        self.assertEqual(filament, "Bambu PLA Matte @BBL X1C")
        machine, process, filament = self.anycubic.newjob._default_profiles(
            str(self.any_exe), KOBRA)
        self.assertEqual(process, "0.20mm Standard @Anycubic Kobra S1 0.4 nozzle")
        self.assertEqual(filament, "Generic PLA @Anycubic Kobra S1 0.4 nozzle")

    def test_donor_search_and_the_preset_donor(self):
        donor = self.orca.newjob.find_donor([], X1C)
        self.assertEqual(orca_family.preset_donor_printer(
            self.orca.flavour, donor), X1C)
        self.assertIn("OrcaSlicer presets", donor.name)
        saved = self.sandbox / "saved.3mf"
        shutil.copy2(ORCA_SPECIMEN, saved)
        self.assertEqual(self.orca.newjob.find_donor([saved], X1C), saved)
        self.assertIsNone(orca_family.preset_donor_printer(
            self.orca.flavour, saved))

    def test_a_project_from_presets_is_set_down_on_the_plate(self):
        donor = self.orca.newjob.find_donor([], X1C)
        target = self.orca.newjob.unique_target(self.sandbox, self.stl)
        notes = self.orca.newjob.create_project(str(self.orca_exe), donor,
                                                [self.stl], target)
        self.assertIn("OrcaSlicer's own presets", notes[0])
        self.assertIn("0.20mm Standard @BBL X1C", notes[0])
        s = self.orca.open_project(target, slicer_exe=str(self.orca_exe))
        oid = s.objects()[0]["id"]
        eff = s.effective_item_nums(oid)
        bb = geometry.bbox([geometry.transform_point(eff, v)
                            for v in s.object_shape(oid)[0]])
        self.assertAlmostEqual(bb[2], 0.0, places=3)

    def test_set_objects_down_is_a_no_op_on_a_resting_object(self):
        entries = engine.load_entries(ORCA_SPECIMEN)
        model = entries[engine.MODEL_ENTRY].decode("utf-8")
        self.assertEqual(orca_family.set_objects_down(model, entries), model)

    def test_a_missing_model_is_refused(self):
        with self.assertRaises(ProjectError):
            self.orca.newjob.create_project(
                str(self.orca_exe), self.orca.newjob.find_donor([], X1C),
                [self.sandbox / "nope.stl"], self.sandbox / "x.3mf")


class PresetListTests(SandboxCase):

    def test_bambu_named_printer_uses_the_bbl_token(self):
        lists = self.orca.preset_lists(str(self.orca_exe), X1C)
        self.assertEqual(lists["process"],
                         ["0.20mm Standard @BBL X1C", "My Orca draft"])
        self.assertEqual(lists["filament"], ["Bambu PLA Matte @BBL X1C"])
        self.assertIn(X1C, lists["machine"])

    def test_anycubic_named_printer_uses_the_full_model_name(self):
        lists = self.anycubic.preset_lists(str(self.any_exe), KOBRA)
        self.assertEqual(lists["process"],
                         ["0.20mm Standard @Anycubic Kobra S1 0.4 nozzle"])
        self.assertEqual(lists["filament"],
                         ["Generic PLA @Anycubic Kobra S1 0.4 nozzle"])


# ---------------------------------------------------------------- HTTP


class HttpFixture:
    httpd = thread = port = sandbox = script = None
    old_config_path = old_appdata = None


H = HttpFixture


class OrcaOverHttpTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        cls.project = projects_dir / "benchy.3mf"
        shutil.copy2(ORCA_SPECIMEN, cls.project)
        fake_exe = support.make_fake_orca(H.sandbox)
        H.script = support.make_fake_creality_engine(H.sandbox)
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({"slicer": "orca", "slicer_exe": str(fake_exe),
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
            body = urllib.parse.urlencode(form, doseq=True) if form else None
            headers = {}
            if body is not None:
                headers = {"Content-Type": "application/x-www-form-urlencoded",
                           "Content-Length": str(len(body))}
            conn.request(method, url, body, headers)
            resp = conn.getresponse()
            return (resp.status, resp.read().decode("utf-8"),
                    resp.getheader("Location") or "")
        finally:
            conn.close()

    def test_every_page_and_a_fake_slice(self):
        status, _b, _l = self.request("POST", "/open",
                                      {"path": str(self.project)})
        self.assertEqual(status, 303)
        for url in ["/project/quality", "/project/strength", "/project/support",
                    "/project/speed", "/project/adhesion", "/project/others",
                    "/project/filaments", "/project/presets",
                    "/project/placement", "/project/all", "/project/review",
                    "/project/backups", "/project/send", "/project/object/2",
                    "/limits", "/", "/start"]:
            status, body, _l = self.request("GET", url)
            self.assertEqual(status, 200, url)
            self.assertNotIn("Traceback", body, url)
        status, body, _l = self.request("GET", "/project/send")
        self.assertIn("OrcaSlicer", body)
        self.assertIn("Ctrl+R", body)
        status, body, _l = self.request("GET", "/start")
        self.assertIn("Start from a model file", body)
        original = creality_engine.build_command
        script = H.script
        creality_engine.build_command = \
            lambda exe, project, plate, out: \
            [sys.executable, str(script), out, "success"]
        try:
            status, _b, loc = self.request("POST", "/project/slice", {})
            self.assertEqual(status, 303, loc)
            job = server.state["slice_job"]
            job.wait(60)
            self.assertEqual(job.status, "done", job.report)
            status, body, _l = self.request("GET", "/project/slice-report")
            self.assertEqual(status, 200)
            self.assertIn("benchy.gcode.3mf", body)
        finally:
            creality_engine.build_command = original


# ------------------------------------------------------ real programs


class RealFamilyIntegrationTests(unittest.TestCase):
    """Against the real OrcaSlicer / Anycubic Slicer Next installs and
    their real libraries (skipped without them): build a project from
    presets and slice it - about half a minute each."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)

    def _run(self, backend, exe, printer):
        stl = self.sandbox / "blind-benchy.stl"
        s = ProjectSession(ORCA_SPECIMEN)
        support.write_stl(*s.object_shape("2"), stl)
        if printer not in backend.profiles.list_presets(exe, "machine"):
            self.skipTest(f"{printer} is not installed in {backend.display_name}")
        donor = backend.newjob.find_donor([], printer)
        target = backend.newjob.unique_target(self.sandbox, stl)
        backend.newjob.create_project(exe, donor, [stl], target)
        built = backend.open_project(target, slicer_exe=exe)
        self.assertEqual(built.settings["printer_settings_id"], printer)
        job = backend.engine.SliceJob(exe, target)
        job.wait(600)
        self.assertEqual(job.status, "done", job.report)
        self.assertGreater(job.report["total_time_s"], 1800)
        self.assertGreater(job.report["total_grams"], 20)

    @unittest.skipUnless(support.real_orca_exe(), "OrcaSlicer not installed")
    def test_orca_builds_and_slices_for_real(self):
        self._run(backends.get_backend("orca"), str(support.real_orca_exe()),
                  X1C)

    @unittest.skipUnless(support.real_anycubic_exe(),
                         "Anycubic Slicer Next not installed")
    def test_anycubic_builds_and_slices_for_real(self):
        self._run(backends.get_backend("anycubic"),
                  str(support.real_anycubic_exe()), KOBRA)


if __name__ == "__main__":
    unittest.main()
