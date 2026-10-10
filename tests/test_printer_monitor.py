"""Print monitoring: the printer page keeps watching (built 2026-09-02).

/printer used to be one read. Now, while a job runs, the page polls
/api/printer every half minute (app.js), refreshes the status list,
reads a short progress line into a polite live region, and announces
the end of the print in an alert region. Covers

- the phase words for the K family's state codes (only code 1 seen
  live so far; the rest per the community integrations),
- /api/printer over real HTTP against the fake K2: the running job
  as JSON, the finished job flagged done, a stopped job too, and a
  printer that has gone away answered in words rather than an error
  page, and
- the /printer page carrying the live region only while a job runs.
"""

import http.client
import json
import os
import shutil
import threading
import unittest
from http.server import ThreadingHTTPServer

import support
import config_store
import creality_lan
import server


class PhaseWordTests(unittest.TestCase):

    def test_known_codes_map_to_phases(self):
        for code, phase in ((0, "idle"), (1, "printing"), (2, "finished"),
                            (3, "finished"), (4, "stopped"), (5, "paused")):
            self.assertEqual(creality_lan.job_phase({"state": code}), phase)
            self.assertEqual(creality_lan.job_phase({"state": str(code)}),
                             phase)

    def test_unknown_or_missing_codes_are_unknown_not_guessed(self):
        self.assertEqual(creality_lan.job_phase({"state": 9}), "unknown")
        self.assertEqual(creality_lan.job_phase({}), "unknown")
        self.assertEqual(creality_lan.job_phase({"state": "x"}), "unknown")
        self.assertEqual(creality_lan.job_phase({"deviceState": 1}),
                         "printing")

    def test_done_phases(self):
        self.assertEqual(creality_lan.DONE_PHASES,
                         ("finished", "stopped", "idle"))

    def test_code_0_is_idle_with_the_last_job_still_shown(self):
        # Seen live 2026-09-15 on the K2 Plus, a week after its last job:
        # state 0, that job's name, 99 percent and 23 seconds left still
        # in the status, nozzle cold, no target, no error. The page said
        # "busy" and a running job; it must say idle and "Last job".
        merged = {"state": 0, "deviceState": 0, "printProgress": 99,
                  "printLeftTime": 23, "printJobTime": 0, "layer": 0,
                  "TotalLayer": 0, "nozzleTemp": 29.85,
                  "targetNozzleTemp": 0, "bedTemp0": 27.16,
                  "printFileName": "/mnt/UDISK/printer_data/gcodes/"
                                   "bambulab_spool_wide_modded_v2.gcode"}
        self.assertEqual(creality_lan.job_phase(merged), "idle")
        lines = creality_lan.status_lines(merged)
        self.assertEqual(lines[0], "The printer is idle.")
        self.assertIn("Last job: bambulab_spool_wide_modded_v2.gcode, "
                      "reached 99 percent.", lines)
        text = " ".join(lines)
        self.assertNotIn("Current job", text)
        self.assertNotIn("Progress:", text)
        self.assertNotIn("Time left", text)
        self.assertIn("Nozzle 30 degrees.", lines)
        # A finished or stopped job reads the same way; a running one
        # keeps the running-job lines.
        lines = creality_lan.status_lines({"state": 4, "printProgress": 40,
                                           "printFileName": "/x/a.gcode"})
        self.assertIn("Last job: a.gcode, reached 40 percent.", lines)
        lines = creality_lan.status_lines({"state": 1, "printProgress": 40,
                                           "printLeftTime": 60,
                                           "printFileName": "/x/a.gcode"})
        self.assertIn("Current job: a.gcode.", lines)
        self.assertIn("Progress: 40 percent.", lines)

    def test_code_3_is_idle_after_a_finished_print(self):
        # Seen live 2026-09-08: the previous job at 100 %, its name still
        # shown, nozzle cold, no error, unchanged after OK on the screen.
        lines = creality_lan.status_lines({"state": 3, "printProgress": 100,
                                           "printFileName": "/x/Cykel.gcode"})
        self.assertIn("idle after finishing", lines[0])
        self.assertIn("Cykel.gcode", " ".join(lines))

    def test_pre_send_problem_words(self):
        old = creality_lan.read_status
        try:
            creality_lan.read_status = lambda ip, timeout=8: {
                "state": 1, "printFileName": "/mnt/x/Cykel.gcode"}
            text = creality_lan.pre_send_problem("1.2.3.4")
            self.assertIn("Nothing was sent", text)
            self.assertIn("printing", text)
            self.assertIn("Cykel.gcode", text)
            creality_lan.read_status = lambda ip, timeout=8: {"state": 5}
            self.assertIn("paused", creality_lan.pre_send_problem("1.2.3.4"))
            for idle in ({"state": 0}, {"state": 2}, {"state": 3},
                         {"state": 4}, {}, {"state": "x"}):
                creality_lan.read_status = lambda ip, timeout=8, d=idle: d
                self.assertIsNone(creality_lan.pre_send_problem("1.2.3.4"))

            def unreachable(ip, timeout=8):
                raise OSError("no route")
            creality_lan.read_status = unreachable
            self.assertIsNone(creality_lan.pre_send_problem("1.2.3.4"))
        finally:
            creality_lan.read_status = old


class HttpFixture:
    httpd = thread = port = sandbox = printer = None
    old_config_path = old_appdata = None


H = HttpFixture


class MonitorHttpTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        fake_exe = support.make_fake_creality(H.sandbox)
        H.printer = support.FakeK2Printer()
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({
            "slicer": "creality", "slicer_exe": str(fake_exe),
            "projects_dir": str(projects_dir),
            "lan_printer_ips": {"creality": H.printer.ws_address()}})
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
        if H.printer:
            H.printer.close()
        server.state["session"] = None
        server.state["watcher"].stop()
        config_store.CONFIG_PATH = H.old_config_path
        if H.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = H.old_appdata
        shutil.rmtree(H.sandbox, ignore_errors=True)

    def get(self, url):
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=60)
        try:
            conn.request("GET", url)
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8")
        finally:
            conn.close()

    def test_a_running_job_over_the_api_and_the_live_page(self):
        H.printer.status["state"] = 1
        status, body = self.get("/api/printer")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["phase"], "printing")
        self.assertFalse(data["done"])
        self.assertIn("The printer is printing.", data["lines"])
        self.assertIn("Progress: 66 percent.", data["lines"])
        self.assertIn("Progress: 66 percent.", data["text"])
        status, page = self.get("/printer")
        self.assertEqual(status, 200)
        self.assertIn('id="printer-line"', page)
        self.assertIn('data-printer-poll="30"', page)
        self.assertIn('id="printer-alert"', page)
        self.assertIn("Progress: 66 percent.", page)

    def test_a_finished_job_is_flagged_done_and_the_page_stops_watching(self):
        H.printer.status["state"] = 2
        try:
            data = json.loads(self.get("/api/printer")[1])
            self.assertTrue(data["ok"])
            self.assertEqual(data["phase"], "finished")
            self.assertTrue(data["done"])
            self.assertIn("The printer is finished.", data["lines"])
            status, page = self.get("/printer")
            self.assertEqual(status, 200)
            self.assertNotIn('id="printer-line"', page)
            self.assertIn("Check again", page)
        finally:
            H.printer.status["state"] = 1

    def test_an_idle_printer_is_done_and_the_page_does_not_watch(self):
        H.printer.status["state"] = 0
        try:
            data = json.loads(self.get("/api/printer")[1])
            self.assertTrue(data["ok"])
            self.assertEqual(data["phase"], "idle")
            self.assertTrue(data["done"])
            self.assertIn("The printer is idle.", data["lines"])
            self.assertNotIn("Progress: 66 percent.", data["lines"])
            status, page = self.get("/printer")
            self.assertEqual(status, 200)
            self.assertNotIn('id="printer-line"', page)
            self.assertIn("Last job:", page)
        finally:
            H.printer.status["state"] = 1

    def test_a_stopped_job_is_done_too(self):
        H.printer.status["state"] = 4
        try:
            data = json.loads(self.get("/api/printer")[1])
            self.assertEqual(data["phase"], "stopped")
            self.assertTrue(data["done"])
        finally:
            H.printer.status["state"] = 1

    def test_a_vanished_printer_is_said_in_words(self):
        status, body = self.get("/api/printer?ip=127.0.0.1:1")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertFalse(data["ok"])
        self.assertIn("Could not reach the printer", data["error"])


if __name__ == "__main__":
    unittest.main()
