"""The send paths on the slice report (rebuilt 2026-09-07).

Edis's first real Prusa run ended at the final step: "Open the sliced
file in PrusaSlicer" opened the G-code viewer, which cannot send, and
the file's path was hard to find. Now the report offers, in order:
straight to the printer over LAN (PrusaLink for the Core One, with
the password remembered), a verified copy to a USB drive or any
folder, the slicer's window with the PROJECT (the viewer demoted to
"only look"), and File Explorer with the file selected. Covers,
hermetically (fake PrusaSlicer install, fake engine, fake PrusaLink
on 127.0.0.1, %APPDATA% and the config in a sandbox):

- the report page's new offers and the demoted viewer,
- the PrusaLink send over real HTTP: upload and start on the fake
  printer, address and key remembered in config, a wrong key and a
  missing key answered in words on the report, the plate check,
- the copy to a folder, to a listed drive, a missing folder, and the
  K-series copy unpacking plain G-code,
- the printer status page through the remembered key, and
- the usbcopy module on its own.
"""

import http.client
import os
import shutil
import sys
import threading
import unittest
import urllib.parse
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import support
import config_store
import prusa_engine
import prusalink
import server
import usbcopy
from errors import ProjectError

COREONE = (support.TEST_PROJECT.parent
           / "blind-benchy-core-one-built-by-accessslice-from-"
           "prusaslicer-presets.3mf")


class UsbCopyTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)

    def test_removable_drives_answers_a_list_of_dicts(self):
        drives = usbcopy.removable_drives()
        self.assertIsInstance(drives, list)
        for d in drives:
            self.assertIn("path", d)
            self.assertIn("text", d)
            self.assertTrue(d["path"].endswith("\\"))

    def test_copy_verified_by_size(self):
        out = self.sandbox / "benchy.bgcode"
        out.write_bytes(b"GCDE" * 1000)
        folder = self.sandbox / "drive"
        folder.mkdir()
        copied = usbcopy.copy_to(out, folder)
        self.assertEqual(copied, folder / "benchy.bgcode")
        self.assertEqual(copied.read_bytes(), b"GCDE" * 1000)

    def test_missing_folder_in_words(self):
        out = self.sandbox / "benchy.bgcode"
        out.write_bytes(b"x")
        with self.assertRaises(ProjectError) as ctx:
            usbcopy.copy_to(out, self.sandbox / "nope")
        self.assertIn("Nothing was copied", str(ctx.exception))

    def test_extracting_copy_writes_plain_gcode(self):
        out = self.sandbox / "cube.gcode.3mf"
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("Metadata/plate_1.gcode", "G28\n")
        folder = self.sandbox / "drive"
        folder.mkdir()
        import creality_lan
        copied = usbcopy.copy_to(out, folder,
                                 extract=creality_lan.extract_gcode)
        self.assertEqual(copied.name, "cube.gcode")
        self.assertEqual(copied.read_bytes(), b"G28\n")

    def test_after_copy_note_reminds_to_eject_drives_only(self):
        self.assertIn("Eject", usbcopy.after_copy_note("E:\\"))
        self.assertIn("Eject", usbcopy.after_copy_note("E:"))
        self.assertNotIn("Eject", usbcopy.after_copy_note(
            str(self.sandbox)))


class HttpFixture:
    httpd = thread = port = sandbox = script = printer = None
    old_config_path = old_appdata = None


H = HttpFixture


class SendPathsOverHttpTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        cls.project = projects_dir / "benchy.3mf"
        shutil.copy2(COREONE, cls.project)
        fake_exe = support.make_fake_prusa(H.sandbox)
        H.script = support.make_fake_prusa_engine(H.sandbox)
        H.printer = support.FakePrusaLinkPrinter()
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({"slicer": "prusa", "slicer_exe": str(fake_exe),
                           "projects_dir": str(projects_dir)})
        H.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(H.sandbox / "AppData")
        import prusa_profiles
        prusa_profiles._bundle_cache.clear()
        prusa_profiles._scope_cache.clear()
        server.state["session"] = None
        server.state["slice_job"] = None
        # No real name lookup after the send's hello (a miss on mDNS
        # takes seconds); the fake printer has no info call anyway.
        cls._old_resolve = prusalink._resolve
        prusalink._resolve = lambda name: ""
        H.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        H.port = H.httpd.server_address[1]
        H.thread = threading.Thread(target=H.httpd.serve_forever,
                                    daemon=True)
        H.thread.start()
        # One fake slice for the whole class: the report needs a file.
        cls._original_build = prusa_engine.build_command
        script = H.script
        prusa_engine.build_command = \
            lambda exe, project, plate, out: \
            [sys.executable, str(script), out, "success"]
        cls._request("POST", "/open", {"path": str(cls.project)})
        cls._request("POST", "/project/slice", {})
        job = server.state["slice_job"]
        job.wait(60)
        assert job.status == "done", job.report
        cls.out = job.output_file()

    @classmethod
    def tearDownClass(cls):
        prusa_engine.build_command = cls._original_build
        prusalink._resolve = cls._old_resolve
        if H.httpd:
            H.httpd.shutdown()
            H.httpd.server_close()
        H.printer.close()
        server.state["session"] = None
        server.state["slice_job"] = None
        server.state["watcher"].stop()
        config_store.CONFIG_PATH = H.old_config_path
        if H.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = H.old_appdata
        shutil.rmtree(H.sandbox, ignore_errors=True)

    @classmethod
    def _request(cls, method, url, form=None):
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

    def request(self, method, url, form=None):
        return self._request(method, url, form)

    def setUp(self):
        H.printer.refuse_start = False
        self._old_drives = usbcopy.removable_drives
        self._old_reveal = usbcopy.reveal
        self.addCleanup(self._restore)

    def _restore(self):
        usbcopy.removable_drives = self._old_drives
        usbcopy.reveal = self._old_reveal

    def _msg(self, location):
        query = urllib.parse.urlparse(location).query
        return urllib.parse.parse_qs(query).get("msg", [""])[0]

    # ------------------------------------------------- the report

    def test_report_offers_the_paths_in_order_and_demotes_the_viewer(self):
        usbcopy.removable_drives = lambda: []
        status, body, _l = self.request("GET", "/project/slice-report")
        self.assertEqual(status, 200)
        lan = body.index("Straight to the printer - no slicer window")
        usb = body.index("Or copy it to a USB drive")
        slicer = body.index("Or send through PrusaSlicer")
        self.assertLess(lan, usb)
        self.assertLess(usb, slicer)
        self.assertIn('id="printer-key"', body)
        self.assertIn("PrusaLink password (API key)", body)
        self.assertIn("Settings, Network, PrusaLink", body)
        self.assertIn("Open the project in PrusaSlicer", body)
        self.assertIn("Only look: open the sliced file", body)
        self.assertIn("Show the file in File Explorer", body)
        self.assertIn("No USB drive is plugged in right now", body)
        self.assertNotIn("For USB or SD-card printing", body)
        # The viewer button is never the primary action.
        viewer = body[body.index("Only look") - 200:body.index("Only look")]
        self.assertNotIn('class="primary"', viewer)

    def test_report_lists_the_drives_windows_sees(self):
        usbcopy.removable_drives = lambda: [
            {"path": "E:\\", "label": "PRUSA", "free": 1 << 30,
             "text": "E: (PRUSA, 1.0 GB free)"}]
        status, body, _l = self.request("GET", "/project/slice-report")
        self.assertIn("USB drives plugged in right now", body)
        self.assertIn('value="E:\\" checked', body)
        self.assertIn("E: (PRUSA, 1.0 GB free)", body)

    # --------------------------------------------- PrusaLink send

    def test_send_over_prusalink_uploads_starts_and_remembers(self):
        # The file THIS send started is the one to check: the fake
        # printer keeps every upload of the class, and the file name
        # carries the minute, so an earlier test's upload from the
        # previous minute is a second "benchy_" name (seen 2026-09-09).
        starts_before = len(H.printer.starts)
        status, _b, loc = self.request("POST", "/project/send-lan", {
            "ip": H.printer.address(), "key": support.FAKE_PRUSALINK_KEY,
            "plate_empty": "yes"})
        self.assertEqual(status, 303)
        self.assertTrue(loc.startswith("/printer"), loc)
        self.assertIn("asked it to start", self._msg(loc))
        self.assertEqual(len(H.printer.starts), starts_before + 1)
        name = H.printer.starts[-1]
        self.assertTrue(name.startswith("benchy_"), name)
        self.assertTrue(name.endswith(".bgcode"))
        self.assertIn(name, H.printer.uploads)
        self.assertEqual(H.printer.uploads[name], self.out.read_bytes())
        self.assertEqual(H.printer.starts[-1], name)
        saved = config_store.load()
        self.assertEqual(saved["lan_printer_ips"]["prusa"],
                         H.printer.address())
        self.assertEqual(saved["lan_printer_keys"]["prusa"],
                         support.FAKE_PRUSALINK_KEY)
        # The next report offers both back, prefilled.
        status, body, _l = self.request("GET", "/project/slice-report")
        self.assertIn(f'value="{H.printer.address()}"', body)
        self.assertIn(f'value="{support.FAKE_PRUSALINK_KEY}"', body)

    def test_a_printer_that_would_ignore_the_start_is_refused_first(self):
        # The lan part's pre_send_problem (a K2 mid-print) is asked
        # before any upload;
        # its words land on the report. Proven here through the Prusa
        # fixture with a stand-in, since the route is shared.
        import prusalink
        before = len(H.printer.uploads)
        prusalink.pre_send_problem = lambda ip, key=None: (
            "Nothing was sent: the printer is printing (Cykel.gcode). "
            "Wait for it to finish, or stop it on the printer, then send "
            "again.")
        try:
            status, _b, loc = self.request("POST", "/project/send-lan", {
                "ip": H.printer.address(), "key": support.FAKE_PRUSALINK_KEY,
                "plate_empty": "yes"})
        finally:
            del prusalink.pre_send_problem
        self.assertEqual(status, 303)
        self.assertTrue(loc.startswith("/project/slice-report"), loc)
        self.assertIn("printing", self._msg(loc))
        self.assertIn("Cykel.gcode", self._msg(loc))
        self.assertEqual(len(H.printer.uploads), before)

    def test_wrong_key_is_refused_before_any_upload(self):
        before = len(H.printer.uploads)
        status, _b, loc = self.request("POST", "/project/send-lan", {
            "ip": H.printer.address(), "key": "wrong",
            "plate_empty": "yes"})
        self.assertEqual(status, 303)
        self.assertTrue(loc.startswith("/project/slice-report"), loc)
        self.assertIn("refused the PrusaLink password", self._msg(loc))
        self.assertEqual(len(H.printer.uploads), before)

    def test_missing_key_and_missing_plate_check_are_asked_for(self):
        status, _b, loc = self.request("POST", "/project/send-lan", {
            "ip": H.printer.address(), "key": "", "plate_empty": "yes"})
        self.assertIn("PrusaLink password", self._msg(loc))
        status, _b, loc = self.request("POST", "/project/send-lan", {
            "ip": H.printer.address(), "key": support.FAKE_PRUSALINK_KEY})
        self.assertIn("build plate is empty", self._msg(loc))

    def test_busy_printer_keeps_the_upload_and_says_so(self):
        H.printer.refuse_start = True
        before = len(H.printer.uploads)
        status, _b, loc = self.request("POST", "/project/send-lan", {
            "ip": H.printer.address(), "key": support.FAKE_PRUSALINK_KEY,
            "plate_empty": "yes"})
        self.assertTrue(loc.startswith("/project/slice-report"), loc)
        self.assertIn("busy", self._msg(loc))
        self.assertEqual(len(H.printer.uploads), before + 1)

    def test_printer_status_page_uses_the_remembered_key(self):
        config = config_store.load()
        config["lan_printer_ips"] = {"prusa": H.printer.address()}
        config["lan_printer_keys"] = {"prusa": support.FAKE_PRUSALINK_KEY}
        config_store.save(config)
        status, body, _l = self.request("GET", "/printer")
        self.assertEqual(status, 200)
        self.assertIn("The printer is printing.", body)
        self.assertIn("Current job: cube.bgcode.", body)
        self.assertIn('id="printer-line"', body)  # a job runs: live
        status, body, _l = self.request("GET", "/api/printer")
        self.assertIn('"phase": "printing"', body)

    # ------------------------------------------------ the USB copy

    def test_copy_to_a_typed_folder(self):
        folder = H.sandbox / "stick"
        folder.mkdir(exist_ok=True)
        status, _b, loc = self.request("POST", "/project/copy-usb",
                                       {"folder": str(folder)})
        self.assertEqual(status, 303)
        self.assertTrue(loc.startswith("/project/slice-report"), loc)
        self.assertIn("Copied benchy.bgcode", self._msg(loc))
        self.assertEqual((folder / "benchy.bgcode").read_bytes(),
                         self.out.read_bytes())

    def test_copy_to_a_listed_drive(self):
        drive = H.sandbox / "drive_e"
        drive.mkdir(exist_ok=True)
        status, _b, loc = self.request("POST", "/project/copy-usb",
                                       {"drive": str(drive), "folder": ""})
        self.assertIn("Copied benchy.bgcode", self._msg(loc))
        self.assertTrue((drive / "benchy.bgcode").is_file())

    def test_copy_nowhere_or_to_a_missing_folder_in_words(self):
        status, _b, loc = self.request("POST", "/project/copy-usb",
                                       {"drive": "", "folder": ""})
        self.assertIn("Nothing was copied", self._msg(loc))
        status, _b, loc = self.request("POST", "/project/copy-usb", {
            "folder": str(H.sandbox / "not-there")})
        self.assertTrue(loc.startswith("/project/slice-report"), loc)
        self.assertIn("Nothing was copied", self._msg(loc))

    def test_show_in_explorer_is_asked_not_launched_here(self):
        shown = []
        usbcopy.reveal = lambda path: shown.append(Path(path))
        status, _b, loc = self.request("POST", "/project/show-sliced")
        self.assertEqual(status, 303)
        self.assertEqual(shown, [self.out])
        self.assertIn("File Explorer", self._msg(loc))


if __name__ == "__main__":
    unittest.main()
