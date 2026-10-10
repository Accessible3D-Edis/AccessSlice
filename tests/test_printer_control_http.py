"""The printer control pages over real HTTP (2026-09-12): the Prusa
backend against the simulated Core One (support.FakeCoreOnePrinter).

Covers

- the home card and the status page's link to the control page,
- the control page rendering with the printer's state, every section
  and its forms, and the connection form saving the address and
  password into the config after a real hello to the printer,
- a preheat posted from the page: the redirect's words, the job
  followed through /api/control until done, the targets set on the
  printer, the events on the page afterwards, and the command file
  removed from the drive,
- a bad number refused in words on the control page (never the home
  page), the stop button wanting its confirmation, pause / resume /
  stop reaching a running print, and a command refused in words while
  the printer prints,
- the files page: the listing, the start with its plate-empty
  confirmation, the delete with its confirmation, the upload from a
  path on this PC, and a path trick refused, and
- a slicer without printer control sent home in words.
"""

import http.client
import json
import os
import shutil
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

import support
import config_store
import printer_control
import prusalink
import server


class HttpFixture:
    httpd = thread = port = sandbox = fake = None
    old_config_path = old_appdata = None


H = HttpFixture


class ControlPagesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        fake_exe = support.make_fake_prusa(H.sandbox)
        H.fake = support.FakeCoreOnePrinter(speed=40.0)
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({
            "slicer": "prusa", "slicer_exe": str(fake_exe),
            "projects_dir": str(projects_dir),
            "lan_printer_ips": {"prusa": H.fake.address()},
            "lan_printer_keys": {"prusa": support.FAKE_PRUSALINK_KEY}})
        H.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(H.sandbox / "AppData")
        server.state["session"] = None
        server.state["control_job"] = None
        cls._old_poll = printer_control.POLL_SECONDS
        printer_control.POLL_SECONDS = 0.05
        # The name lookup after a connection test never touches the
        # real network here (a miss on mDNS takes seconds); the one
        # test of the name-remembering patches it to resolve.
        cls._old_resolve = prusalink._resolve
        prusalink._resolve = lambda name: ""
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
        if H.fake:
            H.fake.close()
        printer_control.POLL_SECONDS = cls._old_poll
        prusalink._resolve = cls._old_resolve
        server.state["session"] = None
        server.state["control_job"] = None
        server.state["watcher"].stop()
        config_store.CONFIG_PATH = H.old_config_path
        if H.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = H.old_appdata
        shutil.rmtree(H.sandbox, ignore_errors=True)

    @property
    def printer(self):
        return H.fake.printer

    def request(self, method, url, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=60)
        try:
            body = urllib.parse.urlencode(form, doseq=True) if form else None
            headers = {}
            if body is not None:
                headers = {"Content-Type":
                           "application/x-www-form-urlencoded",
                           "Content-Length": str(len(body))}
            conn.request(method, url, body, headers)
            resp = conn.getresponse()
            return (resp.status, resp.read().decode("utf-8"),
                    resp.getheader("Location") or "")
        finally:
            conn.close()

    def get(self, url):
        return self.request("GET", url)

    def post(self, url, **form):
        status, body, location = self.request("POST", url, form)
        self.assertEqual(status, 303, body[:300])
        query = urllib.parse.urlparse(location).query
        return (urllib.parse.urlparse(location).path,
                urllib.parse.parse_qs(query).get("msg", [""])[0])

    def wait_done(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = json.loads(self.get("/api/control")[1])
            if data.get("done"):
                return data
            time.sleep(0.05)
        self.fail("the command never finished")

    def setUp(self):
        self.printer.wait_for_state("IDLE", "FINISHED", "STOPPED",
                                    timeout=10)
        job = server.state["control_job"]
        if job is not None:
            deadline = time.monotonic() + 30
            while job.busy and time.monotonic() < deadline:
                time.sleep(0.02)

    # ------------------------------------------------------ pages

    def test_home_and_status_page_link_to_the_control_page(self):
        status, body, _l = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn('href="/printer/control"', body)
        self.assertIn("Control the printer", body)
        status, body, _l = self.get("/printer")
        self.assertEqual(status, 200)
        self.assertIn('href="/printer/control"', body)
        self.assertIn('href="/printer/files"', body)

    def test_the_control_page_renders_every_section(self):
        status, body, _l = self.get("/printer/control")
        self.assertEqual(status, 200)
        self.assertIn("<h1>Control the printer</h1>", body)
        for heading in ("Connection", "The printer now", "The command",
                        "The print job now", "Preheat", "Filament",
                        "Move", "Temperature and fans", "Calibration",
                        "Printer settings and lights", "Any G-code"):
            self.assertIn(f"<h2>{heading}</h2>", body, heading)
        self.assertIn("The printer is ", body)
        self.assertIn('action="/printer/control/preheat"', body)
        self.assertIn('action="/printer/control/move"', body)
        self.assertIn('name="go" value="Z-"', body)
        self.assertIn('id="control-key"', body)
        self.assertIn("A password is remembered", body)
        self.assertTrue("No command has been sent" in body
                        or "Last command: " in body)
        self.assertNotIn('id="control-line"', body)
        # The filament type is a word field, not a number field.
        self.assertIn('id="load-filament" name="filament" value="PLA" '
                      'size="8"', body)
        for preset in ("PLA", "PETG", "ASA"):
            self.assertIn(f'<option value="{preset}">', body)
        self.assertIn("Beep test", body)

    def test_the_connection_form_checks_and_remembers(self):
        path, msg = self.post("/printer/connection", ip=H.fake.address(),
                              key=support.FAKE_PRUSALINK_KEY)
        self.assertEqual(path, "/printer/control")
        self.assertIn("Prusa CORE One", msg)
        self.assertIn("remembered", msg)
        saved = config_store.load()
        self.assertEqual(saved["lan_printer_ips"]["prusa"], H.fake.address())
        self.assertEqual(saved["lan_printer_keys"]["prusa"],
                         support.FAKE_PRUSALINK_KEY)
        # An empty password field keeps the remembered one.
        path, msg = self.post("/printer/connection", ip=H.fake.address(),
                              key="")
        self.assertIn("remembered", msg)
        # A wrong one is refused before anything is saved.
        path, msg = self.post("/printer/connection", ip=H.fake.address(),
                              key="wrong")
        self.assertEqual(path, "/printer/control")
        self.assertIn("refused the PrusaLink password", msg)
        self.assertEqual(config_store.load()["lan_printer_keys"]["prusa"],
                         support.FAKE_PRUSALINK_KEY)

    def test_a_working_address_is_remembered_by_the_printers_name(self):
        # The Core One's address changed under AccessSlice on
        # 2026-09-21 (the router's DHCP), so a numeric address that
        # works is swapped for the printer's own name when the name
        # resolves to it - the name follows the printer.
        host, port = H.fake.address().split(":")
        name = f"simulated-core-one.local:{port}"
        prusalink._resolve = (lambda n: host
                              if n == "simulated-core-one.local" else "")
        try:
            path, msg = self.post("/printer/connection",
                                  ip=H.fake.address(), key="")
        finally:
            prusalink._resolve = lambda n: ""
        self.assertEqual(path, "/printer/control")
        self.assertIn("Prusa CORE One", msg)
        self.assertIn(f"remembered by its own name, {name}", msg)
        self.assertIn("follows the printer", msg)
        self.assertIn("password is remembered too", msg)
        self.assertEqual(config_store.load()["lan_printer_ips"]["prusa"],
                         name)
        # The page shows the name in the address field.
        _status, body, _loc = self.request("GET", "/printer/control")
        self.assertIn(f'value="{name}"', body)
        # A name that does not resolve is left alone: the numeric
        # address is remembered again, with the plain words.
        path, msg = self.post("/printer/connection", ip=H.fake.address(),
                              key="")
        self.assertIn("The address and password are remembered", msg)
        self.assertEqual(config_store.load()["lan_printer_ips"]["prusa"],
                         H.fake.address())

    # --------------------------------------------------- commands

    def test_a_preheat_from_the_page_runs_and_is_followed(self):
        path, msg = self.post("/printer/control/preheat", preset="PETG")
        self.assertEqual(path, "/printer/control")
        self.assertIn("Sent: Preheat PETG", msg)
        status, body, _l = self.get("/printer/control")
        self.assertIn("Running now: Preheat PETG", body)
        self.assertIn('id="control-line"', body)
        self.assertIn('data-control-poll="2"', body)
        self.assertIn('id="control-alert"', body)
        data = self.wait_done()
        self.assertEqual(data["result"], "finished", data["events"])
        self.assertEqual(data["name"], "Preheat PETG")
        kinds = [e["kind"] for e in data["events"]]
        self.assertEqual(kinds[:2], ["started", "uploaded"])
        self.assertIn("cleaned", kinds)
        self.assertEqual(self.printer.target["nozzle"], 230)
        self.assertEqual(self.printer.target["bed"], 85)
        self.assertNotIn("usb/accessslice/preheat_petg.gcode",
                         self.printer.files)
        status, body, _l = self.get("/printer/control")
        self.assertIn("Last command: Preheat PETG", body)
        self.assertIn("Preheat PETG: done.", body)
        self.assertNotIn('id="control-line"', body)
        self.post("/printer/control/cooldown")
        self.wait_done()
        self.assertEqual(self.printer.target["nozzle"], 0)

    def test_a_move_from_the_one_move_form(self):
        path, msg = self.post("/printer/control/move", step="0,5", go="X+")
        self.assertIn("Sent: Move X 0.5 mm right", msg)
        self.wait_done()
        self.assertEqual(self.printer.axis["x"], 0.5)
        self.post("/printer/control/home", axes="")
        self.wait_done()
        self.assertEqual(self.printer.axis["x"], 0.0)

    def test_the_guard_on_the_page(self):
        # A jog that would end below the plate is refused before any
        # file goes to the printer (the 2026-09-15 plate damage).
        self.printer.axis["z"] = 8.0
        path, msg = self.post("/printer/control/move", step="10", go="Z-")
        self.assertEqual(path, "/printer/control")
        self.assertIn("Nothing was sent. Move Z 10 mm down would take the "
                      "nozzle to Z -2, below the plate", msg)
        self.assertEqual(self.printer.axis["z"], 8.0)
        # A home from a low nozzle is lifted first, and the page says
        # so; the job's file carries the lift lines.
        self.printer.axis["z"] = 1.0
        path, msg = self.post("/printer/control/home", axes="")
        self.assertIn("Sent: Home all axes", msg)
        data = self.wait_done()
        self.assertEqual(data["result"], "finished", data["events"])
        messages = [e["message"] for e in data["events"]]
        self.assertIn("Lifted the nozzle to 5 mm before the home, because "
                      "the printer homes X and Y before Z and a low nozzle "
                      "would be dragged over the plate.", messages)
        self.assertEqual(self.printer.axis["z"], 5.0)
        # A sideways move with the nozzle on the plate is refused.
        self.printer.axis["z"] = 0.2
        path, msg = self.post("/printer/control/move", step="10", go="X+")
        self.assertIn("would scrape it. Move Z up to at least 1 mm first",
                      msg)
        self.printer.axis["z"] = 5.0
        # The page explains the rules and has the hand-check tick.
        status, body, _l = self.get("/printer/control")
        self.assertIn('name="clear"', body)
        self.assertIn("The nozzle is clear of the plate", body)
        self.assertIn("in words, any move that would end below the plate",
                      body)

    def test_typed_gcode_and_a_snippet(self):
        before = self.printer.beeps
        path, msg = self.post("/printer/control/gcode",
                              gcode="M300 S1000 P300\n\nM300 S500 P100\n")
        self.assertIn("Sent: Your G-code (2 lines)", msg)
        self.wait_done()
        self.assertEqual(self.printer.beeps, before + 2)
        path, msg = self.post("/printer/control/snippet",
                              snippet="Beep test")
        self.assertIn("Sent: Beep test", msg)
        self.wait_done()
        self.assertEqual(self.printer.beeps, before + 3)

    def test_problems_come_back_to_the_control_page_in_words(self):
        path, msg = self.post("/printer/control/nozzle", temp="abc")
        self.assertEqual(path, "/printer/control")
        self.assertIn("must be a number", msg)
        path, msg = self.post("/printer/control/stop")
        self.assertEqual(path, "/printer/control")
        self.assertIn("Tick the confirmation", msg)
        path, msg = self.post("/printer/control/explode")
        self.assertIn("knows no printer action", msg)
        path, msg = self.post("/printer/control/pause")
        self.assertIn("no running print to pause", msg)

    def test_pause_resume_and_stop_reach_a_running_print(self):
        self.printer.upload("long.gcode",
                            b'M862.3 P "COREONE"\n' + b"G4 P1\n" * 400)
        self.assertIsNone(self.printer.start("long.gcode"))
        try:
            path, msg = self.post("/printer/control/pause")
            self.assertEqual(path, "/printer/control")
            self.assertIn("paused", msg)
            self.assertTrue(self.printer.wait_for_state("PAUSED"))
            path, msg = self.post("/printer/control/resume")
            self.assertIn("resumed", msg)
            self.assertTrue(self.printer.wait_for_state("PRINTING"))
            # A command while printing: refused in words by the job.
            path, msg = self.post("/printer/control/beep")
            self.assertIn("Sent: Beep", msg)
            data = self.wait_done()
            self.assertEqual(data["result"], "failed")
            self.assertIn("printing", data["text"])
            path, msg = self.post("/printer/control/stop", confirm="yes")
            self.assertIn("stopped", msg)
            self.assertTrue(self.printer.wait_for_state("STOPPED"))
        finally:
            if self.printer.state not in ("STOPPED", "FINISHED", "IDLE"):
                self.printer.job_call(self.printer.job["id"], "stop")
            self.printer.delete("long.gcode")

    # ------------------------------------------------------ files

    def test_the_files_page_lists_starts_deletes_and_uploads(self):
        status, body, _l = self.get("/printer/files")
        self.assertEqual(status, 200)
        self.assertIn("<h1>Files on the printer</h1>", body)
        self.assertIn("example_print.gcode", body)
        self.assertIn('action="/printer/files/start"', body)
        self.assertIn("I checked: the build plate is empty", body)
        # Start without the confirmation: refused.
        path, msg = self.post("/printer/files/start",
                              path="example_print.gcode")
        self.assertEqual(path, "/printer/files")
        self.assertIn("Nothing was started", msg)
        # Upload from this PC into a folder, then list the folder.
        source = H.sandbox / "part.gcode"
        source.write_bytes(b'M862.3 P "COREONE"\nM300 S1000 P300\n')
        path, msg = self.post("/printer/files/upload", file=str(source),
                              folder="")
        self.assertEqual(path, "/printer/files")
        self.assertIn("Uploaded part_", msg)
        uploaded = next(k for k in self.printer.files
                        if k.startswith("usb/part_"))
        name = uploaded[len("usb/"):]
        status, body, _l = self.get("/printer/files")
        self.assertIn(name, body)
        # Start it with the confirmation: the status page follows.
        before = self.printer.beeps
        path, msg = self.post("/printer/files/start", path=name,
                              plate_empty="yes")
        self.assertEqual(path, "/printer")
        self.assertIn("Asked the printer to print", msg)
        self.assertTrue(self.printer.wait_for_state("FINISHED"))
        self.assertEqual(self.printer.beeps, before + 1)
        # Delete wants its confirmation, then works.
        path, msg = self.post("/printer/files/delete", path=name)
        self.assertIn("Nothing was deleted", msg)
        path, msg = self.post("/printer/files/delete", path=name,
                              confirm="yes")
        self.assertIn("Deleted", msg)
        self.assertNotIn(uploaded, self.printer.files)
        # Refusals in words.
        path, msg = self.post("/printer/files/upload", file=str(
            H.sandbox / "missing.gcode"))
        self.assertIn("There is no file", msg)
        path, msg = self.post("/printer/files/upload", file=str(
            config_store.CONFIG_PATH))
        self.assertIn("Only .gcode and .bgcode", msg)
        path, msg = self.post("/printer/files/delete", path="../x",
                              confirm="yes")
        self.assertIn("Choose a file", msg)
        status, body, location = self.get("/printer/files?path=../x")
        self.assertEqual(status, 303)
        self.assertIn("not a folder", urllib.parse.unquote(location))

    # ------------------------------------------------- other slicer

    def test_a_slicer_without_control_is_sent_home_in_words(self):
        config = config_store.load()
        old = dict(config)
        config["slicer"] = "orca"
        config["slicer_exe"] = str(support.make_fake_orca(H.sandbox))
        config_store.save(config)
        try:
            status, body, location = self.get("/printer/control")
            self.assertEqual(status, 303)
            self.assertIn("not available", urllib.parse.unquote(location))
            self.assertIn("Orca", urllib.parse.unquote(location))
            status, body, location = self.get("/")
            self.assertNotIn('href="/printer/control"', body)
        finally:
            config_store.save(old)


if __name__ == "__main__":
    unittest.main()
