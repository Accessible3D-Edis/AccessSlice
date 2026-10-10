"""Printer control for the K series: direct WebSocket commands
(built 2026-09-12 on the Prusa control's pages, the day after that
merge). The vocabulary is the community's and unverified on the real
K2 Plus (it was off); these checks pin the CONTRACT AccessSlice sends
and how it reads the printer back, against the fake K2.

Covers

- the direct action table: every button builds its documented set
  params (nozzleTempControl, bedTempControl with num and val,
  boxTempControl, fan keys 0-100, lightSw, autohome words, a
  setPosition jog with the axis feed, setZOffset signed, feedrate and
  flow percent, gcodeCmd per line, feedInOrOut with box and slot),
  bad numbers refused in words,
- the Creality LAN module's new calls against the fake K2: set,
  pause, resume, stop, check_printer, cfs_info, list_files, delete,
  the position parser and the extra status lines,
- the direct job runner: a preheat sends its steps and reads the
  targets back, a home waits while the printer reports homing, a
  second command is refused while one runs, and
- the pages over real HTTP with Creality Print active: the control
  page in direct mode (no Calibration section, the CFS slots listed
  with load and retract buttons, Live Z, the fans), a light-on
  followed to done through /api/control, pause and stop of a running
  print through the direct calls, and the files page listing the
  printer's own paths with delete.
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
import creality_lan
import printer_control as pc
import server
from errors import ProjectError


def _fields(**values):
    return lambda name: str(values.get(name, "")).strip()


# Where the head is for the builder checks: the K2 Plus's rest after
# a home (motion_guard refuses moves toward the plate and sideways
# moves without a readable height).
AT = {"x": 175.5, "y": 175.8, "z": 8.0}


def _params(action, **values):
    _name, steps = pc.build_direct_action(action, _fields(**values),
                                          creality_lan, position=AT)
    return [s["params"] for s in steps]


class DirectActionTableTests(unittest.TestCase):

    def test_temperatures_and_fans(self):
        self.assertEqual(_params("preheat", preset="PLA"),
                         [{"nozzleTempControl": 220},
                          {"bedTempControl": {"num": 0, "val": 60}}])
        self.assertEqual(_params("preheat", preset="PETG", nozzle_only="yes"),
                         [{"nozzleTempControl": 240}])
        self.assertEqual(_params("preheat-custom", nozzle="200", bed="50",
                                 chamber="40"),
                         [{"nozzleTempControl": 200},
                          {"bedTempControl": {"num": 0, "val": 50}},
                          {"boxTempControl": 40}])
        self.assertEqual(_params("cooldown")[-1], {"fan": 0})
        self.assertEqual(_params("chamber", temp="45"),
                         [{"boxTempControl": 45}])
        self.assertEqual(_params("fan", percent="50"), [{"fan": 50}])
        self.assertEqual(_params("fan-aux", percent="100"),
                         [{"fanAuxiliary": 100}])
        self.assertEqual(_params("fan-case", percent="0"), [{"fanCase": 0}])

    def test_moves_home_and_live_z(self):
        self.assertEqual(_params("move", go="X+", step="10"),
                         [{"setPosition": "X10 F3000"}])
        self.assertEqual(_params("move", go="Z-", step="0,5"),
                         [{"setPosition": "Z-0.5 F600"}])
        self.assertEqual(_params("home", axes=""), [{"autohome": "X Y Z"}])
        self.assertEqual(_params("home", axes="XY"), [{"autohome": "X Y"}])
        self.assertEqual(_params("home", axes="Z"), [{"autohome": "Z"}])
        self.assertEqual(_params("goto", x="10", z="50"),
                         [{"gcodeCmd": "G90"},
                          {"gcodeCmd": "G1 X10 Z50 F3000"}])
        self.assertEqual(_params("zoffset", step="0.01", direction="closer"),
                         [{"setZOffset": "-0.01"}])
        self.assertEqual(_params("zoffset", step="0.05",
                                 direction="farther"),
                         [{"setZOffset": "+0.05"}])
        self.assertEqual(_params("motors-off"), [{"gcodeCmd": "M84"}])

    def test_filament_light_speed_and_gcode(self):
        self.assertEqual(_params("cfs-feed", slot="2"),
                         [{"feedInOrOut": {"boxId": 1, "materialId": 2,
                                           "isFeed": 1}}])
        self.assertEqual(_params("cfs-retract", box="0", slot="0"),
                         [{"feedInOrOut": {"boxId": 0, "materialId": 0,
                                           "isFeed": 0}}])
        self.assertEqual(_params("extrude", length="5"),
                         [{"gcodeCmd": "M83"}, {"gcodeCmd": "G1 E5 F300"},
                          {"gcodeCmd": "M82"}])
        self.assertEqual(_params("retract", length="3")[1],
                         {"gcodeCmd": "G1 E-3 F300"})
        self.assertEqual(_params("lights-on"), [{"lightSw": 1}])
        self.assertEqual(_params("lights-off"), [{"lightSw": 0}])
        self.assertEqual(_params("speed", percent="150"),
                         [{"setFeedratePct": 150}])
        self.assertEqual(_params("flow", percent="95"),
                         [{"setFlowratePct": 95}])
        self.assertEqual(_params("gcode", gcode="M114\n\nM115"),
                         [{"gcodeCmd": "M114"}, {"gcodeCmd": "M115"}])
        self.assertEqual(_params("snippet",
                                 snippet="Report current position (M114)"),
                         [{"gcodeCmd": "M114"}])
        # The beep stays buildable (the action table is shared) but is
        # off the K-series page: the K2 Plus made no sound (2026-09-15).
        self.assertEqual(_params("beep"), [{"gcodeCmd": "M300 S1000 P300"}])
        self.assertNotIn("beep", pc.DIRECT_FEATURES)
        self.assertNotIn("Beep (M300)", pc.DIRECT_SNIPPETS)

    def test_names_and_refusals(self):
        name, steps = pc.build_direct_action(
            "cfs-feed", _fields(slot="3"), creality_lan)
        self.assertEqual(name, "Load filament from CFS slot D")
        self.assertEqual(steps[0]["wait"], "feeding")
        name, _steps = pc.build_direct_action(
            "move", _fields(go="Y-", step="1"), creality_lan, position=AT)
        self.assertEqual(name, "Move Y 1 mm forward")
        with self.assertRaises(ProjectError) as ctx:
            pc.build_direct_action("nozzle", _fields(temp="400"),
                                   creality_lan)
        self.assertIn("between 0 and 350", str(ctx.exception))
        with self.assertRaises(ProjectError):
            pc.build_direct_action("cfs-feed", _fields(slot="7"),
                                   creality_lan)
        with self.assertRaises(ProjectError):
            pc.build_direct_action("load", _fields(), creality_lan)
        with self.assertRaises(ProjectError):
            pc.build_direct_action("gcode", _fields(gcode=""), creality_lan)

    def test_profiles(self):
        direct = pc.profile_for(creality_lan)
        self.assertEqual(direct["mode"], "direct")
        self.assertIn("cfs", direct["features"])
        self.assertIn("zoffset", direct["features"])
        self.assertNotIn("load", direct["features"])
        self.assertNotIn("mesh-level", direct["features"])
        self.assertEqual(direct["limits"]["nozzle"], (0, 350))
        import prusalink
        job = pc.profile_for(prusalink)
        self.assertEqual(job["mode"], "job")
        self.assertIn("load", job["features"])
        self.assertNotIn("cfs", job["features"])


class LanCallsTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fake = support.FakeK2Printer()
        cls.ip = cls.fake.ws_address()

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def setUp(self):
        self.fake.status["state"] = 1
        self.fake.gcodes.clear()

    def test_set_reads_the_printer_back(self):
        pushed = creality_lan.set_params(self.ip, {"nozzleTempControl": 200})
        self.assertEqual(pushed.get("targetNozzleTemp"), 200)
        self.assertEqual(self.fake.commands[-1],
                         {"method": "set",
                          "params": {"nozzleTempControl": 200}})

    def test_pause_resume_stop(self):
        creality_lan.pause_print(self.ip)
        self.assertEqual(self.fake.status["state"], 5)
        creality_lan.resume_print(self.ip)
        self.assertEqual(self.fake.status["state"], 1)
        creality_lan.stop_print(self.ip)
        self.assertEqual(self.fake.status["state"], 4)

    def test_check_printer_cfs_and_position(self):
        info = creality_lan.check_printer(self.ip)
        self.assertEqual(info["printer"], "Creality K series")
        boxes = creality_lan.cfs_info(self.ip)
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes[0]["type"], 1)     # the spool holder
        self.assertEqual(boxes[0]["materials"][0]["name"], "Generic TPU")
        self.assertEqual(boxes[0]["materials"][0]["color"], "#000000")
        self.assertEqual(boxes[1]["materials"][0]["name"], "Hyper PLA")
        self.assertEqual(boxes[1]["materials"][3]["type"], "TPU")
        self.assertEqual(creality_lan.position(
            {"curPosition": "X10.50 Y-2 Z0.30"}),
            {"x": 10.5, "y": -2.0, "z": 0.3})
        # The K2 Plus's own format, seen live 2026-09-15.
        self.assertEqual(creality_lan.position(
            {"curPosition": "X:175.50 Y:175.80 Z:-2.01"}),
            {"x": 175.5, "y": 175.8, "z": -2.01})
        self.assertEqual(creality_lan.position({}), {})
        lines = creality_lan.control_lines({
            "targetBoxTemp": 40, "lightSw": 1, "fan": 100,
            "fanAuxiliary": 0, "curPosition": "X1 Y2 Z3",
            "curFeedratePct": 150, "curFlowratePct": 100})
        self.assertEqual(lines, [
            "Chamber target 40 degrees.", "Light on.",
            "Fans: model fan 100 percent, side fan 0 percent.",
            "Head at X 1, Y 2, Z 3.", "Speed 150 percent."])
        self.assertEqual(creality_lan.control_lines({}), [])

    def test_files_list_and_delete(self):
        creality_lan.upload_gcode(self.fake.http_address(), "part.gcode",
                                 b"G28\n")
        entries = creality_lan.list_files(self.ip)
        self.assertEqual([e["display_name"] for e in entries],
                         ["part.gcode"])
        self.assertEqual(entries[0]["path"],
                         "/mnt/UDISK/printer_data/gcodes/part.gcode")
        self.assertIsInstance(entries[0]["size"], int)
        creality_lan.delete_file(self.ip, entries[0]["path"])
        self.assertEqual(self.fake.deleted,
                         ["/mnt/UDISK/printer_data/gcodes/part.gcode"])
        self.assertEqual(creality_lan.list_files(self.ip), [])

    def test_a_gcode_line_and_a_feed(self):
        creality_lan.send_gcode(self.ip, "M300 S1000 P300")
        self.assertEqual(self.fake.gcodes, ["M300 S1000 P300"])
        creality_lan.cfs_feed(self.ip, 1, 3, feed=True)
        self.assertEqual(self.fake.feeds[-1],
                         {"boxId": 1, "materialId": 3, "isFeed": 1})
        self.assertEqual(creality_lan.cfs_info(self.ip)[1]["materials"][3]
                         ["selected"], 1)


class DirectJobTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fake = support.FakeK2Printer()
        cls.ip = cls.fake.ws_address()

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def run_job(self, action, current=None, **values):
        # As the server does: the position read just before.
        merged = creality_lan.read_status(self.ip)
        name, steps = pc.build_direct_action(
            action, _fields(**values), creality_lan,
            position=creality_lan.position(merged))
        return pc.start_direct_job(current, creality_lan, self.ip, name,
                                   steps, poll_seconds=0.05, wait_seconds=2)

    def wait(self, job, timeout=20):
        deadline = time.monotonic() + timeout
        while job.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        return not job.busy

    def test_a_preheat_sends_its_steps_and_reads_back(self):
        job = self.run_job("preheat", preset="ABS")
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        messages = [e["message"] for e in job.events]
        self.assertIn("Sent: nozzle to 260 degrees.", messages)
        self.assertIn("The printer reports the nozzle target as 260 "
                      "degrees.", messages)
        self.assertIn("The printer reports the bed target as 100 degrees.",
                      messages)
        self.assertEqual(self.fake.status["targetNozzleTemp"], 260)
        snap = job.snapshot()
        self.assertTrue(snap["done"])
        self.assertFalse(snap["attention"])

    def test_a_home_waits_while_the_printer_reports_homing(self):
        self.fake.status["deviceState"] = 7    # homing, until the fake
        # clears it when it applies the autohome (like the real one
        # once the routine ends).
        job = self.run_job("home", axes="")
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        messages = " ".join(e["message"] for e in job.events)
        self.assertIn("Sent: home X Y Z.", messages)
        self.assertIn("Head now at X 175.5, Y 175.8, Z 8.", messages)

    def test_an_idle_printer_is_not_busy_so_a_home_does_not_wait(self):
        # State 0 is the K2 Plus's idle state (seen live 2026-09-15);
        # read as "busy" it made every waiting step sit out its ninety
        # seconds. deviceState 7 (homing) is the only busy signal.
        self.assertFalse(pc._busy(
            creality_lan, {"state": 0, "deviceState": 0}))
        self.assertTrue(pc._busy(
            creality_lan, {"state": 0, "deviceState": 7}))
        self.fake.status["state"] = 0
        self.fake.status["deviceState"] = 0
        job = self.run_job("home", axes="")
        self.assertTrue(self.wait(job, timeout=5))
        self.assertEqual(job.result, "finished", job.events)
        messages = " ".join(e["message"] for e in job.events)
        self.assertNotIn("waiting", messages)
        self.assertNotIn("Still homing", messages)

    def test_a_second_command_is_refused_while_one_runs(self):
        job = self.run_job("gcode", gcode="\n".join(["M114"] * 30))
        with self.assertRaises(ProjectError) as ctx:
            self.run_job("beep", current=job)
        self.assertIn("still running", str(ctx.exception))
        self.assertTrue(self.wait(job))
        self.assertEqual(self.fake.gcodes[-1], "M114")


class HttpFixture:
    httpd = thread = port = sandbox = fake = None
    old_config_path = old_appdata = None


H = HttpFixture


class ControlPagesDirectTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        fake_exe = support.make_fake_creality(H.sandbox)
        H.fake = support.FakeK2Printer()
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({
            "slicer": "creality", "slicer_exe": str(fake_exe),
            "projects_dir": str(projects_dir),
            "lan_printer_ips": {"creality": H.fake.ws_address()}})
        H.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(H.sandbox / "AppData")
        server.state["session"] = None
        server.state["control_job"] = None
        cls._old_poll = pc.POLL_SECONDS
        pc.POLL_SECONDS = 0.05
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
        pc.POLL_SECONDS = cls._old_poll
        server.state["session"] = None
        server.state["control_job"] = None
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

    def wait_done(self, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = json.loads(self.get("/api/control")[1])
            if data.get("done"):
                return data
            time.sleep(0.05)
        self.fail("the command never finished")

    def setUp(self):
        H.fake.status["state"] = 1
        job = server.state["control_job"]
        if job is not None:
            deadline = time.monotonic() + 20
            while job.busy and time.monotonic() < deadline:
                time.sleep(0.02)

    def test_the_control_page_in_direct_mode(self):
        status, body, _l = self.get("/")
        self.assertIn('href="/printer/control"', body)
        status, body, _l = self.get("/printer/control")
        self.assertEqual(status, 200)
        for heading in ("Connection", "The printer now", "The command",
                        "The print job now", "Preheat", "Filament",
                        "Move", "Temperature and fans",
                        "Printer settings and lights", "Any G-code"):
            self.assertIn(f"<h2>{heading}</h2>", body, heading)
        self.assertNotIn("<h2>Calibration</h2>", body)
        self.assertNotIn('id="control-key"', body)
        self.assertIn("<h3>CFS and spool</h3>", body)
        self.assertIn("Slot A</strong>: PLA, Creality Hyper PLA, colour "
                      "#FF0000, 80 percent left (loaded now)", body)
        self.assertIn("Slot C</strong>: empty", body)
        self.assertIn("Load from slot D", body)
        self.assertIn("Retract slot A", body)
        # The spool holder is its own row, never a second "Slot A"
        # (Edis, 2026-09-15: "both tpu and pla in slot a").
        self.assertIn("External spool holder</strong>: TPU, Generic TPU, "
                      "colour #000000, 100 percent left", body)
        self.assertEqual(body.count("Slot A</strong>"), 1)
        self.assertEqual(body.count("Load from the external spool"), 1)
        self.assertIn('name="box" value="0"', body)
        # No beep on the K series: the K2 Plus made no sound (2026-09-15).
        self.assertNotIn("Beep", body)
        self.assertIn("<h3>Live Z (first-layer tuning)</h3>", body)
        self.assertIn('action="/printer/control/zoffset"', body)
        self.assertIn("Set the side (auxiliary) fan", body)
        self.assertIn("Set the print speed", body)
        self.assertNotIn('action="/printer/control/load"', body)
        self.assertIn("Commands go straight to the printer", body)
        self.assertIn("The printer is printing.", body)

    def test_the_connection_form_without_a_password(self):
        path, msg = self.post("/printer/connection", ip=H.fake.ws_address())
        self.assertEqual(path, "/printer/control")
        self.assertIn("Creality K series", msg)
        self.assertIn("The address is remembered", msg)

    def test_a_light_on_followed_to_done(self):
        path, msg = self.post("/printer/control/lights-on")
        self.assertEqual(path, "/printer/control")
        self.assertIn("Sent: Light on", msg)
        data = self.wait_done()
        self.assertEqual(data["result"], "finished", data["events"])
        messages = [e["message"] for e in data["events"]]
        self.assertIn("Sent: light on.", messages)
        self.assertIn("The printer reports the light on.", messages)
        self.assertEqual(H.fake.status["lightSw"], 1)
        status, body, _l = self.get("/printer/control")
        self.assertIn("Last command: Light on", body)
        self.assertIn("Light on.", body)          # the status line

    def test_live_z_and_a_move(self):
        H.fake.status["state"] = 0       # idle: moves are refused mid-print
        self.addCleanup(H.fake.status.__setitem__, "state", 1)
        path, msg = self.post("/printer/control/zoffset", step="0.02",
                              direction="closer")
        self.assertIn("Sent: Nozzle 0.02 mm closer to the bed", msg)
        self.wait_done()
        self.assertEqual(H.fake.zoffsets[-1], "-0.02")
        path, msg = self.post("/printer/control/move", step="10", go="X+")
        self.assertIn("Sent: Move X 10 mm right", msg)
        data = self.wait_done()
        self.assertIn("Head now at X 185.5, Y 175.8, Z 8.",
                      [e["message"] for e in data["events"]])
        # A move that would end below the plate is AccessSlice's own
        # refusal now, before anything is sent (2026-09-15: the K2
        # Plus MADE a Z-10 from 8, driving the bed into the nozzle).
        path, msg = self.post("/printer/control/move", step="10", go="Z-")
        self.assertEqual(path, "/printer/control")
        self.assertIn("Nothing was sent. Move Z 10 mm down would take the "
                      "nozzle to Z -2, below the plate", msg)
        self.assertIn("the largest step down from here is 8 mm", msg)
        self.assertEqual(H.fake.status["curPosition"],
                         "X:185.50 Y:175.80 Z:8.00")
        # A move the printer refuses (past the far end) is said so,
        # with the position it kept.
        path, msg = self.post("/printer/control/move", step="200", go="X+")
        self.assertIn("Sent: Move X 200 mm right", msg)
        data = self.wait_done()
        messages = [e["message"] for e in data["events"]]
        self.assertIn("The head did not move: the printer refused X 385.5 "
                      "- past the far end of its travel, or not homed "
                      "since power-up. Head still at X 185.5, Y 175.8, "
                      "Z 8.", messages)
        path, msg = self.post("/printer/control/move", step="10", go="Z+")
        data = self.wait_done()
        self.assertIn("Head now at X 185.5, Y 175.8, Z 18.",
                      [e["message"] for e in data["events"]])

    def test_a_home_after_a_power_up_needs_the_hand_check(self):
        # Klipper reports a stale position and refuses the lift until
        # a home: without the tick the home is not sent; with it the
        # lift is tried and the home goes on.
        H.fake.status["state"] = 0
        self.addCleanup(H.fake.status.__setitem__, "state", 1)
        H.fake.unhomed = True
        self.addCleanup(setattr, H.fake, "unhomed", False)
        H.fake.status["curPosition"] = "X:0.00 Y:0.00 Z:0.00"
        H.fake.homes.clear()
        path, msg = self.post("/printer/control/home", axes="")
        self.assertIn("Sent: Home all axes", msg)
        data = self.wait_done()
        self.assertEqual(data["result"], "failed", data["events"])
        self.assertIn("Stopped before the home: the printer did not lift "
                      "the nozzle", data["events"][-1]["message"])
        self.assertEqual(H.fake.homes, [])
        path, msg = self.post("/printer/control/home", axes="",
                              clear="yes")
        data = self.wait_done()
        self.assertEqual(data["result"], "finished", data["events"])
        self.assertEqual(H.fake.homes, ["X Y Z"])

    def test_pause_stop_and_a_refusal_while_idle(self):
        H.fake.status["state"] = 1
        path, msg = self.post("/printer/control/pause")
        self.assertIn("paused", msg)
        self.assertEqual(H.fake.status["state"], 5)
        path, msg = self.post("/printer/control/resume")
        self.assertIn("resumed", msg)
        path, msg = self.post("/printer/control/stop")
        self.assertIn("Tick the confirmation", msg)
        path, msg = self.post("/printer/control/stop", confirm="yes")
        self.assertIn("stopped", msg)
        self.assertEqual(H.fake.status["state"], 4)
        path, msg = self.post("/printer/control/pause")
        self.assertIn("no running print to pause", msg)

    def test_the_files_page_names_the_printers_paths(self):
        creality_lan.upload_gcode(H.fake.http_address(), "cube.gcode",
                                 b"G28\n")
        status, body, _l = self.get("/printer/files")
        self.assertEqual(status, 200)
        self.assertIn("cube.gcode", body)
        self.assertIn('value="/mnt/UDISK/printer_data/gcodes/cube.gcode"',
                      body)
        path, msg = self.post("/printer/files/delete",
                              path="/mnt/UDISK/printer_data/gcodes/cube.gcode",
                              confirm="yes")
        self.assertIn("Deleted", msg)
        self.assertEqual(H.fake.deleted[-1],
                         "/mnt/UDISK/printer_data/gcodes/cube.gcode")


if __name__ == "__main__":
    unittest.main()
