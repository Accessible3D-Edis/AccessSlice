"""The Creality Print backend: format read, engine driver, presets.

Built on the real-machine probes of 2026-09-01 (docs\\log.md); the
committed specimen is Edis's own Dodecagon plant cover, saved from
Creality Print 7.2.2.
"""

import shutil
import sys
import time
import unittest

import support

import backends
import creality_compat
import creality_engine

SPECIMEN = (support.REPO_ROOT / "testdata"
            / "dodecagon-plant-cover-saved-from-creality-print.3mf")


class TestBackendSurface(unittest.TestCase):
    def setUp(self):
        self.backend = backends.get_backend("creality")

    def test_identity_and_parts(self):
        self.assertEqual(self.backend.display_name, "Creality Print")
        self.assertTrue(self.backend.supported)
        self.assertIsNone(self.backend.newjob)
        self.assertIsNone(self.backend.printer_switch)
        self.assertIs(self.backend.engine, creality_engine)
        self.assertIn("import", self.backend.texts.SHORTCUTS)

    def test_opens_the_committed_specimen(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        project = sandbox / "dodecagon.3mf"
        shutil.copy2(SPECIMEN, project)
        session = self.backend.open_project(project)
        self.assertEqual(len(session.objects()), 1)
        self.assertIn("Creality K2 Plus 0.4 nozzle",
                      " ".join(session.summary()))
        # 7.2 is the verified series - no version caution, only the
        # honest note about the absent changed-settings bookkeeping.
        self.assertEqual(len(session.structure_notes), 1)
        self.assertIn("bookkeeping", session.structure_notes[0])

    def test_preset_lists_filter_by_model(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        exe = support.make_fake_creality(sandbox)
        lists = self.backend.preset_lists(
            exe, "Creality K2 Plus 0.4 nozzle")
        self.assertIn("0.20mm Standard @Creality K2 Plus 0.4 nozzle",
                      lists["process"])
        self.assertNotIn(
            "0.20mm Standard @Creality Ender-3 V3 0.4 nozzle",
            lists["process"])
        self.assertIn("Hyper PLA @Creality K2 Plus 0.4 nozzle",
                      lists["filament"])
        self.assertIn("Creality Ender-3 V3 0.4 nozzle",
                      lists["machine"])

    def test_session_scope_of_uses_creality_library(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        exe = support.make_fake_creality(sandbox)
        project = sandbox / "dodecagon.3mf"
        shutil.copy2(SPECIMEN, project)
        session = self.backend.open_project(project, slicer_exe=exe)
        self.assertEqual(session.scope_of("nozzle_temperature"),
                         "filament")
        self.assertEqual(session.scope_of("wall_loops"), "process")


class TestPerSlicerShortLists(unittest.TestCase):
    """Edis's 2026-09-01 finding: with Creality active, the Start
    page offered only Bambu printers. The learned short lists are
    per-slicer now; the old plain-list config format reads as
    Bambu's."""

    def setUp(self):
        import server
        self.server = server

    def test_old_list_format_belongs_to_bambu(self):
        config = {"slicer": "bambu",
                  "known_printers": ["Bambu Lab H2C 0.4 nozzle"]}
        self.assertEqual(self.server.known(config, "known_printers"),
                         ["Bambu Lab H2C 0.4 nozzle"])
        config["slicer"] = "creality"
        self.assertEqual(self.server.known(config, "known_printers"),
                         [])

    def test_learning_stays_with_the_active_slicer(self):
        config = {"slicer": "creality",
                  "known_printers": ["Bambu Lab H2C 0.4 nozzle"]}
        self.server.learn(config, "known_printers",
                          ["Creality K2 Plus 0.4 nozzle"])
        self.assertEqual(self.server.known(config, "known_printers"),
                         ["Creality K2 Plus 0.4 nozzle"])
        config["slicer"] = "bambu"
        self.assertEqual(self.server.known(config, "known_printers"),
                         ["Bambu Lab H2C 0.4 nozzle"])


class TestStartAndHomeFindings(unittest.TestCase):
    """Edis's 2026-09-01 findings, minutes after the first: an empty
    printer short list rendered NOTHING instead of explaining itself,
    and the home page never said which slicer is active."""

    def test_empty_printer_list_explains_itself(self):
        import webui
        html = webui.render_start("Creality Print", [], "")
        self.assertIn("Which printer is this job for?", html)
        self.assertIn("No printers are on your Creality Print short "
                      "list yet", html)

    def test_home_names_the_active_slicer(self):
        import webui
        html = webui.render_home({}, [], slicer_name="Creality Print")
        self.assertIn("Active slicer", html)
        self.assertIn("Creality Print", html)

    def test_home_offers_printer_status_for_lan_backends_only(self):
        # Edis, 2026-09-08, after closing the program mid-print: "If I
        # now wanted to go back to see the progress, can I do so?" The
        # Printer page needs only the remembered address, so Home
        # offers it whenever the slicer's printer can be asked.
        import webui
        with_lan = webui.render_home({}, [], slicer_name="Creality Print",
                                     printer=True)
        self.assertIn('href="/printer"', with_lan)
        self.assertIn("Printer status", with_lan)
        without = webui.render_home({}, [], slicer_name="Bambu Studio",
                                    printer=False)
        self.assertNotIn('href="/printer"', without)
        # The server derives the flag from the backend's lan part.
        self.assertIsNotNone(backends.get_backend("creality").lan)
        self.assertIsNone(backends.get_backend("bambu").lan)


class TestPathNormalizing(unittest.TestCase):
    """Edis's 2026-09-01 finding: pasting the desktop shortcut
    ("Creality Print 7.1.lnk") as the slicer path gave WinError 193
    at launch. Paths are the app's job to format."""

    def test_shortcut_resolves_to_its_target(self):
        import config_store
        path, note = config_store.normalize_exe_path(
            r'"C:\Users\Public\Desktop\Creality Print 7.1.lnk"',
            resolver=lambda p: r"C:\Real\CrealityPrint.exe")
        self.assertEqual(path, r"C:\Real\CrealityPrint.exe")
        self.assertIn("shortcut", note)

    def test_unresolvable_shortcut_says_so(self):
        import config_store
        path, note = config_store.normalize_exe_path(
            r"C:\gone.lnk", resolver=lambda p: None)
        self.assertEqual(path, r"C:\gone.lnk")
        self.assertIn("could not be resolved", note)

    def test_quotes_and_spaces_are_stripped(self):
        import config_store
        path, note = config_store.normalize_exe_path(
            '  "C:\\Program Files\\X\\x.exe"  ')
        self.assertEqual(path, "C:\\Program Files\\X\\x.exe")
        self.assertEqual(note, "")

    def test_backends_name_their_programs(self):
        self.assertIn("CrealityPrint.exe",
                      backends.get_backend("creality").exe_names)
        self.assertIn("bambu-studio.exe",
                      backends.get_backend("bambu").exe_names)


class TestCompat(unittest.TestCase):
    def test_series_parses_the_application_string(self):
        self.assertEqual(
            creality_compat.series("Creality_Print V7.2.2.5483 Release"),
            "7.2")
        self.assertEqual(creality_compat.series("7.2.2.5483"), "7.2")
        self.assertEqual(creality_compat.series("BambuStudio-02.08"), "")

    def test_verified_series_stays_quiet(self):
        self.assertIsNone(creality_compat.unverified_note(
            "Creality_Print V7.2.2.5483 Release", "This project"))

    def test_unverified_series_gets_a_caution(self):
        note = creality_compat.unverified_note(
            "Creality_Print V8.0.1.100 Release", "This project")
        self.assertIn("8.0", note)
        self.assertIn("not been verified", note)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox,
                        ignore_errors=True)
        self.project = self.sandbox / "dodecagon.3mf"
        shutil.copy2(SPECIMEN, self.project)
        self.script = support.make_fake_creality_engine(self.sandbox)
        self._original = creality_engine.build_command

    def tearDown(self):
        creality_engine.build_command = self._original

    def _patch(self, behaviour):
        script = self.script
        creality_engine.build_command = \
            lambda exe, project, plate, out: \
            [sys.executable, str(script), out, behaviour]

    def test_success_report_from_slice_info(self):
        self._patch("success")
        job = creality_engine.SliceJob("exe", self.project)
        job.wait(30)
        self.assertEqual(job.status, "done")
        r = job.report
        self.assertTrue(r["ok"])
        self.assertEqual(r["total_time_s"], 10111.0)
        self.assertAlmostEqual(r["total_grams"], 132.52)
        self.assertEqual(r["plates"][0]["filaments"][0]["slot"], 1)
        self.assertEqual(r["plates"][0]["features"], [])
        self.assertEqual(r["warnings"], [])
        self.assertTrue(job.output_file())

    def test_failure_quotes_the_stdout_reason(self):
        self._patch("fail")
        job = creality_engine.SliceJob("exe", self.project)
        job.wait(30)
        self.assertEqual(job.status, "failed")
        self.assertIn("Nothing to be sliced",
                      job.report["error_string"])
        self.assertEqual(job.report["return_code"], 127)
        self.assertIsNone(job.output_file())

    def test_crash_reports_honestly(self):
        self._patch("crash")
        job = creality_engine.SliceJob("exe", self.project)
        job.wait(30)
        self.assertEqual(job.status, "failed")
        self.assertIn("without an error message",
                      job.report["error_string"])

    def test_cancel_still_works_through_the_subclass(self):
        self._patch("hang")
        job = creality_engine.SliceJob("exe", self.project)
        time.sleep(0.5)
        job.cancel()
        job.wait(30)
        self.assertEqual(job.status, "cancelled")


class TestLan(unittest.TestCase):
    """The direct K-series LAN send - built 2026-09-01 after the UIA
    probe proved Creality Print's GUI unautomatable by name. The fake
    printer mirrors the probed contract; read_status was ALSO proven
    against Edis's real K2 Plus mid-print the same evening."""

    @classmethod
    def setUpClass(cls):
        cls.printer = support.FakeK2Printer()

    @classmethod
    def tearDownClass(cls):
        cls.printer.close()

    def setUp(self):
        import creality_lan
        self.lan = creality_lan

    def test_backend_carries_the_lan_part(self):
        self.assertIs(backends.get_backend("creality").lan, self.lan)
        self.assertIsNone(backends.get_backend("bambu").lan)

    def test_safe_gcode_name(self):
        name = self.lan.safe_gcode_name('bad\\na/me: "x"')
        self.assertTrue(name.endswith(".gcode"))
        for ch in '\\/:"':
            self.assertNotIn(ch, name)

    def test_extract_gcode(self):
        import zipfile as zf
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        path = sandbox / "out.gcode.3mf"
        with zf.ZipFile(path, "w") as z:
            z.writestr("Metadata/plate_1.gcode", "G28\nG1 X10\n")
        entry, data = self.lan.extract_gcode(path)
        self.assertEqual(entry, "Metadata/plate_1.gcode")
        self.assertEqual(data, b"G28\nG1 X10\n")

    def test_cfs_mode_gcode_gets_a_caution_on_the_report(self):
        # OPEN ISSUE 2026-09-08: a CFS-mode job started from AccessSlice
        # printed only a few dots (the start carries no slot mapping).
        import zipfile as zf
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        cfs = sandbox / "cfs.gcode.3mf"
        with zf.ZipFile(cfs, "w") as z:
            z.writestr("Metadata/plate_1.gcode",
                       "; HEADER_BLOCK_START\n; multicolor_method = 1 \n"
                       "M8200 L I0\nG1 E1\n")
        plain = sandbox / "plain.gcode.3mf"
        with zf.ZipFile(plain, "w") as z:
            z.writestr("Metadata/plate_1.gcode",
                       "; multicolor_method = 0 \nG1 E1\n")
        text = self.lan.send_warning(cfs)
        self.assertIn("CFS", text)
        self.assertIn("few dots", text)
        self.assertIsNone(self.lan.send_warning(plain))
        self.assertIsNone(self.lan.send_warning(sandbox / "missing.3mf"))

    def test_upload_reaches_the_printer(self):
        self.lan.upload_gcode(self.printer.http_address(),
                              "cube test.gcode", b"G28\nDATA")
        (path, body), = [(p, b) for p, b
                         in self.printer.uploads.items()
                         if "cube" in p]
        self.assertIn("/upload/cube%20test.gcode", path)
        self.assertIn(b"G28\nDATA", body)
        self.assertIn(b'name="file"', body)

    def test_read_status_merges_and_acks_heartbeat(self):
        merged = self.lan.read_status(self.printer.ws_address(),
                                      timeout=6)
        self.assertEqual(merged.get("state"), 1)
        self.assertEqual(merged.get("printProgress"), 66)
        # The "ok" ack is on the wire before read_status returns, but
        # the fake printer's reader thread may not have PROCESSED it
        # yet - wait for it instead of racing it.
        deadline = time.monotonic() + 3
        while self.printer.acks < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(self.printer.acks, 1)

    def test_start_print_names_the_path_the_printer_lists(self):
        # The fake lists uploads under /mnt/UDISK, like the K2 Plus.
        self.lan.upload_gcode(self.printer.http_address(),
                              "spool wide.gcode", b"G28\n")
        before = len(self.printer.commands)
        self.lan.start_print(self.printer.ws_address(), "spool wide.gcode",
                             timeout=6)
        sets = [c for c in self.printer.commands[before:]
                if c.get("method") == "set"]
        self.assertTrue(sets)
        self.assertEqual(
            sets[-1]["params"]["opGcodeFile"],
            "printprt:/mnt/UDISK/printer_data/gcodes/spool wide.gcode")

    def test_start_print_falls_back_to_the_current_jobs_folder(self):
        # Not in the listing: the folder of the printer's current job
        # (the fake's status names /usr/data, the K1 layout) wins over
        # the built-in guess.
        before = len(self.printer.commands)
        self.lan.start_print(self.printer.ws_address(), "cube.gcode",
                             timeout=6)
        sets = [c for c in self.printer.commands[before:]
                if c.get("method") == "set"]
        self.assertEqual(
            sets[-1]["params"]["opGcodeFile"],
            "printprt:/usr/data/printer_data/gcodes/cube.gcode")

    def test_status_lines_speak_plainly(self):
        lines = " ".join(self.lan.status_lines({
            "state": 1, "printProgress": 66, "printLeftTime": 240,
            "nozzleTemp": 219.2, "bedTemp0": 49.99,
            "layer": 93, "TotalLayer": 148,
            "printFileName": "/x/cube.gcode"}))
        self.assertIn("printing", lines)
        self.assertIn("66 percent", lines)
        self.assertIn("Nozzle 219 degrees", lines)
        self.assertIn("Bed 50 degrees", lines)
        self.assertIn("Layer 93 of 148", lines)
        self.assertIn("cube.gcode", lines)

    def test_saved_device_address_reads_the_slicers_device_list(self):
        # Creality Print's deviceInfo.json (shape verified on the
        # real 7.2.2 install) - so nobody ever reads an IP off the
        # printer's screen. Written under the SANDBOXED %APPDATA%.
        import json as _json
        import os
        base = (support.Path(os.environ["APPDATA"]) / "Creality"
                / "Creality Print" / "7.0")
        base.mkdir(parents=True, exist_ok=True)
        info = base / "deviceInfo.json"
        info.write_text(_json.dumps({
            "current_device": {"mac": "AABB"},
            "groups": [{"group": "g", "list": [
                {"address": "192.0.2.7", "mac": "CCDD",
                 "model": "F008"},
                {"address": "192.0.2.9", "mac": "AABB",
                 "model": "F008"},
            ]}],
        }), encoding="utf-8")
        self.addCleanup(info.unlink)
        # The current device's mac wins over list order.
        self.assertEqual(self.lan.saved_device_address(), "192.0.2.9")

    def test_unreachable_printer_fails_in_plain_words(self):
        with self.assertRaises(self.lan.ProjectError) as ctx:
            self.lan.read_status("127.0.0.1:1", timeout=2)
        self.assertIn("Could not reach the printer",
                      str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
