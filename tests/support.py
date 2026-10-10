"""support.py - shared fixtures for the AccessSlice test suite.

Everything here keeps the tests HERMETIC:

- app modules are imported from the repository's app\\ folder,
- every file a test touches lives in a fresh temporary sandbox,
- a FAKE Bambu Studio install (executable stub + miniature profile
  library) stands in for the real one, so no test depends on Bambu
  Studio being installed,
- AccessSlice's config path and %APPDATA% are pointed INTO the sandbox,
  so no test can ever read or write the user's real configuration or
  the real BambuStudio.conf.

No test may launch a real program or touch the network beyond
127.0.0.1.
"""

import atexit
import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
TOOLS_DIR = REPO_ROOT / "tools"
TEST_PROJECT = (REPO_ROOT / "testdata"
                / "blind-benchy-test-project-saved-from-bambu-studio.3mf")

for d in (APP_DIR, TOOLS_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

# The WHOLE suite runs with %APPDATA% pointed into a throwaway folder
# from the first `import support` on, so no test - however it is
# written - can ever read or write the user's real BambuStudio.conf,
# Studio user presets, or AccessSlice diagnostics log. Tests that need
# a specific APPDATA fixture still override it themselves.
_GLOBAL_APPDATA = Path(tempfile.mkdtemp(prefix="accessslice-appdata-"))
os.environ["APPDATA"] = str(_GLOBAL_APPDATA)
atexit.register(shutil.rmtree, _GLOBAL_APPDATA, ignore_errors=True)


def make_sandbox():
    """A fresh temp folder the caller must remove (or use SandboxCase)."""
    return Path(tempfile.mkdtemp(prefix="accessslice-test-"))


def copy_test_project(sandbox, name="blind-benchy.3mf"):
    """A private copy of the committed test project inside the sandbox."""
    target = sandbox / name
    shutil.copy2(TEST_PROJECT, target)
    return target


# ------------------------------------------------- fake Bambu Studio

# A miniature but structurally faithful profile library: presets with
# inheritance, instantiation flags, and per-scope folders. The preset
# names mirror the ones in the committed test project so remapping and
# filtering behave as they do against a real install.

_FAKE_LIBRARY = {
    "machine": {
        "fdm_machine_common": {
            "instantiation": "false",
            "machine_max_speed_x": "500",
            "printable_area": ["0x0", "330x0", "330x320", "0x320"],
        },
        "Bambu Lab H2C 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_machine_common",
            "nozzle_diameter": ["0.4"],
        },
        "Bambu Lab X1 Carbon 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_machine_common",
            "nozzle_diameter": ["0.4"],
        },
    },
    "process": {
        "fdm_process_common": {
            "instantiation": "false",
            "wall_loops": "2", "sparse_infill_density": "15%",
            "enable_support": "0",
        },
        "0.20mm Standard @BBL H2C": {
            "instantiation": "true", "inherits": "fdm_process_common",
        },
        "0.20mm Standard @BBL X1C": {
            "instantiation": "true", "inherits": "fdm_process_common",
        },
        "Test Strength @BBL H2C": {
            "instantiation": "true", "inherits": "fdm_process_common",
            "wall_loops": "6", "sparse_infill_density": "25%",
        },
    },
    "filament": {
        "fdm_filament_pla": {
            "instantiation": "false",
            "nozzle_temperature": ["220"], "fan_max_speed": ["100"],
        },
        "Bambu PLA Matte @BBL H2C": {
            "instantiation": "true", "inherits": "fdm_filament_pla",
            "nozzle_temperature": ["230"],
        },
        "Bambu PLA Matte @BBL X1C": {
            "instantiation": "true", "inherits": "fdm_filament_pla",
        },
    },
}


def make_fake_studio(sandbox):
    """A stub bambu-studio.exe with a mini profile library beside it.

    Returns the exe path (a plain empty file - tests never run it).
    """
    root = sandbox / "FakeBambuStudio"
    exe = root / "bambu-studio.exe"
    root.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    bbl = root / "resources" / "profiles" / "BBL"
    for scope, presets in _FAKE_LIBRARY.items():
        folder = bbl / scope
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in presets.items():
            (folder / f"{name}.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8")
    return exe


def write_studio_conf(appdata_dir, data):
    """Write a BambuStudio.conf exactly the way Studio does.

    JSON body, CRLF on disk, trailing "# MD5 checksum <HEX>" line whose
    digest covers the LF-ended JSON with no trailing newline (the format
    cracked on 2026-08-26). Returns the conf path.
    """
    body_lf = json.dumps(data, indent=4, ensure_ascii=False)
    digest = hashlib.md5(body_lf.encode("utf-8")).hexdigest().upper()
    conf = Path(appdata_dir) / "BambuStudio" / "BambuStudio.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_bytes((body_lf.replace("\n", "\r\n")
                      + f"\r\n# MD5 checksum {digest}\r\n").encode("utf-8"))
    return conf


def add_empty_plate(project_path, new_plate_id="2"):
    """Give a project copy a second, empty build plate.

    Duplicates the first plate's block in model_settings.config with a
    new plater_id and its model_instance entries stripped - the shape
    Studio produces for a freshly added plate.
    """
    import re
    import edit_bambu_3mf as engine
    entries = engine.load_entries(project_path)
    ms = entries[engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
    m = re.search(r"[ \t]*<plate>.*?</plate>", ms, re.S)
    copy = re.sub(r'(key="plater_id" value=")[^"]*(")',
                  rf"\g<1>{new_plate_id}\g<2>", m.group(0))
    copy = re.sub(r"[ \t]*<model_instance>.*?</model_instance>\n?", "",
                  copy, flags=re.S)
    ms = ms[:m.end()] + "\n" + copy + ms[m.end():]
    entries[engine.MODEL_SETTINGS_ENTRY] = ms.encode("utf-8")
    engine.save_entries(project_path, entries)


# ---------------------------------------------------- fake engine CLI

# A stand-in for the slicer's headless engine: a Python script the
# tests run instead of bambu-studio.exe (engine_driver.build_command is
# patched to invoke it). It mimics the REAL CLI contract probed
# 2026-08-31: result.json written into the working directory on
# success AND failure, the printable file only on success, stdout
# empty. Behaviours: success, fail (an off-plate error), silent (ends
# without writing result.json), hang (sleeps until killed - for
# cancel and timeout tests).

_FAKE_ENGINE_SCRIPT = r'''
import json, sys, time

out_name, behaviour = sys.argv[1], sys.argv[2]
if behaviour == "hang":
    time.sleep(600)
    sys.exit(0)
if behaviour == "silent":
    sys.exit(0)
if behaviour == "fail":
    json.dump({"return_code": -50,
               "error_string": "An object is laid over the boundary "
                               "of plate."},
              open("result.json", "w"))
    sys.exit(0)
json.dump({
    "return_code": 0, "error_string": "Success.",
    "layer_height": 0.2, "plate_index": 0,
    "sliced_plates": [{
        "id": 1,
        "total_predication": 10502.27,
        "feature_type_times": {"Outer wall": 1951.09,
                               "Support": 1498.63,
                               "Sparse infill": 53.07,
                               "Brim": 0.4},
        "filaments": [{"id": 2, "filament_id": "GFG02",
                       "total_used_g": 37.87}],
        "objects": [{"id": 5, "name": "blind-Benchy.stl",
                     "triangle_count": 94745}],
        "warning_message": "",
    }],
}, open("result.json", "w"))
open(out_name, "wb").write(b"PK fake gcode 3mf")
'''


def make_fake_engine(sandbox):
    """Write the fake engine script; returns its path.

    Use with a patched engine_driver.build_command, e.g.:
        engine_driver.build_command = lambda exe, project, plate, out: \
            [sys.executable, str(script), out, "success"]
    """
    script = Path(sandbox) / "fake_engine.py"
    script.write_text(_FAKE_ENGINE_SCRIPT, encoding="utf-8")
    return script


# ---------------------------------------------- fake Creality Print

# Mini Creality vendor library, same shapes as the real 7.2.2 install
# (verified 2026-09-01): vendor folder "Creality", preset names with
# "@Creality <model> <nozzle> nozzle" suffixes.

_FAKE_CREALITY_LIBRARY = {
    "machine": {
        "fdm_creality_common": {
            "instantiation": "false",
            "printable_area": ["0x0", "350x0", "350x350", "0x350"],
        },
        "Creality K2 Plus 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_creality_common",
            "nozzle_diameter": ["0.4"],
        },
        "Creality Ender-3 V3 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_creality_common",
            "nozzle_diameter": ["0.4"],
        },
    },
    "process": {
        "fdm_process_creality_common": {
            "instantiation": "false", "wall_loops": "2",
            "sparse_infill_density": "15%",
        },
        "0.20mm Standard @Creality K2 Plus 0.4 nozzle": {
            "instantiation": "true",
            "inherits": "fdm_process_creality_common",
        },
        "0.20mm Standard @Creality Ender-3 V3 0.4 nozzle": {
            "instantiation": "true",
            "inherits": "fdm_process_creality_common",
        },
    },
    "filament": {
        "fdm_filament_pla_creality": {
            "instantiation": "false",
            "nozzle_temperature": ["220"],
        },
        "Hyper PLA @Creality K2 Plus 0.4 nozzle": {
            "instantiation": "true",
            "inherits": "fdm_filament_pla_creality",
        },
        "Hyper PLA @Creality Ender-3 V3 0.4 nozzle": {
            "instantiation": "true",
            "inherits": "fdm_filament_pla_creality",
        },
    },
}


def make_fake_creality(sandbox):
    """A stub CrealityPrint.exe with a mini Creality vendor library."""
    root = sandbox / "FakeCrealityPrint"
    exe = root / "CrealityPrint.exe"
    root.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    vendor = root / "resources" / "profiles" / "Creality"
    for scope, presets in _FAKE_CREALITY_LIBRARY.items():
        folder = vendor / scope
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in presets.items():
            (folder / f"{name}.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8")
    return exe


# The fake Creality engine mirrors the REAL 7.2.2 contract (probed
# 2026-09-01): no result.json; success = exit 0 plus a .gcode.3mf
# whose Metadata/slice_info.config carries the numbers; failure =
# non-zero exit with the reason as the last meaningful stdout line.

_FAKE_CREALITY_ENGINE_SCRIPT = r'''
import sys, time, zipfile

out_name, behaviour = sys.argv[1], sys.argv[2]
if behaviour == "hang":
    time.sleep(600)
    sys.exit(0)
if behaviour == "fail":
    print("[2026-09-01 13:27:25.991761] [0x00005004] [error]   "
          "plate 1: Nothing to be sliced, Either the print is empty "
          "or no object is fully inside the print volume before "
          "apply.")
    print("")
    print("Slic3r::CLI::run found error, exit")
    sys.exit(127)
if behaviour == "crash":
    sys.exit(139)
with zipfile.ZipFile(out_name, "w") as z:
    z.writestr("Metadata/slice_info.config", """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="prediction" value="10111"/>
    <metadata key="weight" value="132.52"/>
    <metadata key="outside" value="false"/>
    <metadata key="support_used" value="false"/>
    <object identify_id="77" name="Dodecagon plant cover.stl" skipped="false" />
    <filament id="1" tray_info_idx="01001" type="PLA" color="#56DF3D" used_m="44.43" used_g="132.52" />
  </plate>
</config>""")
    z.writestr("Metadata/plate_1.gcode", "G28\n")
print("[2026-09-01 13:22:12.664577] [0x00001648] [error]   "
      "Slic3r::Print::export_gcode end")
sys.exit(0)
'''


def make_fake_creality_engine(sandbox):
    """Write the fake Creality engine script; returns its path.

    Use with a patched creality_engine.build_command, e.g.:
        creality_engine.build_command = \
            lambda exe, project, plate, out: \
                [sys.executable, str(script), out, "success"]
    """
    script = Path(sandbox) / "fake_creality_engine.py"
    script.write_text(_FAKE_CREALITY_ENGINE_SCRIPT, encoding="utf-8")
    return script


# -------------------------------------------- fake K-series printer

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_FAKE_PRINTER_STATUS = {
    "state": 1, "printProgress": 66, "printLeftTime": 240,
    "nozzleTemp": 219.2, "targetNozzleTemp": 220,
    "bedTemp0": 49.99, "targetBedTemp0": 50,
    "layer": 93, "TotalLayer": 148,
    "printFileName": "/usr/data/printer_data/gcodes/cube.gcode",
    # The K2 Plus's position format (live 2026-09-15), at its rest
    # after a home: the plate centre, 8 mm above the plate.
    "curPosition": "X:175.50 Y:175.80 Z:8.00",
}


class FakeK2Printer:
    """A miniature stock K-series printer on 127.0.0.1: the HTTP
    upload endpoint and the port-9999-style JSON WebSocket, faithful
    to the probed contract (heartbeat pushed and 'ok'-acked, status
    pushed on connect and on every get, set commands recorded)."""

    def __init__(self, status=None):
        self.uploads = {}    # url path -> raw POST body
        self.commands = []   # every JSON the client sent
        self.acks = 0        # heartbeat "ok" answers received
        self.status = dict(status or _FAKE_PRINTER_STATUS)
        self.gcodes = []     # every gcodeCmd line, in order
        self.feeds = []      # every feedInOrOut dict
        self.deleted = []    # every deleteprt path
        self.zoffsets = []   # every setZOffset value
        self.homes = []      # every autohome value
        # After a power-up Klipper refuses every move until a home,
        # and the reported position is stale; set True to model it.
        self.unhomed = False
        # The K2 Plus with a CFS, as boxsInfo lists it (live shape
        # of 2026-09-15): box 0 type 1 is the external spool holder
        # with its one remembered material, box 1 type 0 the CFS.
        self.boxes = {"materialBoxs": [
            {"id": 0, "type": 1, "state": 0,
             "materials": [
                 {"id": 0, "type": "TPU", "vendor": "Generic",
                  "name": "Generic TPU", "color": "#0000000",
                  "state": 1, "selected": 0, "percent": 100}]},
            {"id": 1, "type": 0, "temp": 28, "humidity": 35,
             "materials": [
                 {"id": 0, "type": "PLA", "vendor": "Creality",
                  "name": "Hyper PLA", "color": "#FF0000", "state": 1,
                  "selected": 1, "percent": 80},
                 {"id": 1, "type": "PETG", "vendor": "Creality",
                  "name": "CR-PETG", "color": "#0000FF", "state": 1,
                  "selected": 0, "percent": 55},
                 {"id": 2, "type": "", "vendor": "", "name": "",
                  "color": "", "state": 0, "selected": 0, "percent": 0},
                 {"id": 3, "type": "TPU", "vendor": "Creality",
                  "name": "CR-TPU", "color": "#00FF00", "state": 1,
                  "selected": 0, "percent": 100}]}]}

        parent = self

        class _Upload(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                parent.uploads[self.path] = self.rfile.read(length)
                data = b'{"code": 200}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Upload)
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()

        self._ws = socket.socket()
        self._ws.bind(("127.0.0.1", 0))
        self._ws.listen(2)
        threading.Thread(target=self._ws_loop, daemon=True).start()

    def http_address(self):
        return f"127.0.0.1:{self.httpd.server_address[1]}"

    def ws_address(self):
        return f"127.0.0.1:{self._ws.getsockname()[1]}"

    def close(self):
        self.httpd.shutdown()
        try:
            self._ws.close()
        except OSError:
            pass

    # -------------------------------------------------- ws internals

    def _ws_loop(self):
        while True:
            try:
                conn, _addr = self._ws.accept()
            except OSError:
                return
            try:
                self._serve(conn)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _serve(self, conn):
        conn.settimeout(10)
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(4096)
            if not chunk:
                return
            request += chunk
        key = ""
        for line in request.decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(hashlib.sha1(
            (key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode("ascii"))
        # The real printer pushes without being asked.
        self._send_text(conn, json.dumps({"ModeCode": "heart_beat"}))
        self._send_text(conn, json.dumps(self.status))
        reader = conn.makefile("rb")
        while True:
            header = reader.read(2)
            if len(header) < 2:
                return
            opcode = header[0] & 0x0F
            length = header[1] & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", reader.read(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", reader.read(8))
            if header[1] & 0x80:
                mask = reader.read(4)
                payload = bytes(b ^ mask[i % 4] for i, b in
                                enumerate(reader.read(length)))
            else:
                payload = reader.read(length)
            if opcode == 0x8:
                return
            if opcode != 0x1:
                continue
            text = payload.decode("utf-8", "replace")
            if text == "ok":
                self.acks += 1
                continue
            try:
                data = json.loads(text)
            except ValueError:
                continue
            self.commands.append(data)
            if data.get("method") == "get":
                params = data.get("params") or {}
                if "reqGcodeFile" in params:
                    # The K2 Plus lists uploads under /mnt/UDISK.
                    names = [urllib.parse.unquote(p.split("/upload/", 1)[1])
                             for p in self.uploads if "/upload/" in p]
                    self._send_text(conn, json.dumps({"retGcodeFileInfo2": [
                        {"name": n, "type": 8, "size": len(self.uploads[p]),
                         "path": "/mnt/UDISK/printer_data/gcodes/" + n}
                        for p, n in zip(
                            [p for p in self.uploads if "/upload/" in p],
                            names)]}))
                elif "boxsInfo" in params:
                    self._send_text(conn, json.dumps(
                        {"boxsInfo": self.boxes}))
                else:
                    self._send_text(conn, json.dumps(self.status))
            elif data.get("method") == "set":
                self._apply_set(data.get("params") or {})
                self._send_text(conn, json.dumps(self.status))

    def _apply_set(self, params):
        """What the community documents the real printer doing with
        each set key, reflected in the pushed status."""
        s = self.status
        for key, value in params.items():
            if key == "nozzleTempControl":
                s["targetNozzleTemp"] = value
            elif key == "bedTempControl":
                s["targetBedTemp0"] = (value.get("val") if isinstance(
                    value, dict) else value)
            elif key == "boxTempControl":
                s["targetBoxTemp"] = value
            elif key in ("fan", "fanAuxiliary", "fanCase", "lightSw"):
                s[key] = value
            elif key == "pause":
                s["state"] = 5 if value else 1
            elif key == "stop":
                s["state"] = 4
            elif key == "autohome":
                s["deviceState"] = 0
                # The real K2 Plus rests at the plate centre about
                # 8 mm above the plate after a home (read live
                # 2026-09-15: X:175.50 Y:175.80, Z 7.99 before the
                # jog that read -2.01).
                s["curPosition"] = "X:175.50 Y:175.80 Z:8.00"
                self.homes.append(str(value))
            elif key == "setPosition":
                import re as _re2
                pos = {a: float(v) for a, v in _re2.findall(
                    r"([XYZ]):?(-?\d+(?:\.\d+)?)",
                    str(s.get("curPosition") or "X0 Y0 Z0"))}
                m = _re2.match(r"([XYZ])(-?\d+(?:\.\d+)?)", str(value))
                # Like the real K2 Plus's Klipper (stepper_z
                # position_min -10): a Z move that would end below
                # -10 is ignored without a word; one that ends between
                # 0 and -10 IS MADE - the bed is driven into the
                # nozzle (2026-09-15). Above 350 is ignored too. The
                # position is written with colons.
                if m:
                    axis, delta = m.group(1), float(m.group(2))
                    target = pos.get(axis, 0.0) + delta
                    low = -10.0 if axis == "Z" else 0.0
                    if not self.unhomed and low <= target <= 350.0:
                        pos[axis] = target
                s["curPosition"] = " ".join(
                    f"{a}:{pos.get(a, 0.0):.2f}" for a in "XYZ")
            elif key == "setZOffset":
                self.zoffsets.append(value)
            elif key == "setFeedratePct":
                s["curFeedratePct"] = value
            elif key == "setFlowratePct":
                s["curFlowratePct"] = value
            elif key == "gcodeCmd":
                self.gcodes.append(str(value))
            elif key == "feedInOrOut":
                self.feeds.append(dict(value))
                slot = int(value.get("materialId", 0))
                box_id = int(value.get("boxId", 1))
                for box in self.boxes["materialBoxs"]:
                    for m in box["materials"]:
                        m["selected"] = 1 if (box["id"] == box_id
                                              and m["id"] == slot
                                              and value.get("isFeed")) else 0
            elif key == "opGcodeFile" and str(value).startswith(
                    "deleteprt:"):
                path = str(value)[len("deleteprt:"):]
                self.deleted.append(path)
                name = path.rsplit("/", 1)[-1]
                for p in list(self.uploads):
                    if p.endswith("/upload/" + urllib.parse.quote(name)) \
                            or p.endswith("/upload/" + name):
                        del self.uploads[p]

    @staticmethod
    def _send_text(conn, text):
        payload = text.encode("utf-8")
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x81, n)
        elif n < 65536:
            header = struct.pack("!BBH", 0x81, 126, n)
        else:
            header = struct.pack("!BBQ", 0x81, 127, n)
        conn.sendall(header + payload)


def add_duplicate_object(project_path, new_id="102",
                         new_name="second-model.stl"):
    """Duplicate the project's first object under a new id and name.

    Produces the two-object shape the new-job merge creates (donor
    object + added model) without needing the real CLI. The duplicate
    shares the original's mesh file, which the app's parser accepts.
    Returns the ORIGINAL object's id (the one a new-job would remove).
    """
    import re
    import edit_bambu_3mf as engine
    entries = engine.load_entries(project_path)

    model = entries[engine.MODEL_ENTRY].decode("utf-8")
    obj = re.search(r"[ \t]*<object id=\"(\d+)\"[^>]*>.*?</object>\n?",
                    model, re.S)
    orig_id = obj.group(1)
    copy = obj.group(0).replace(f'id="{orig_id}"', f'id="{new_id}"', 1)
    model = model[:obj.end()] + copy + model[obj.end():]
    item = re.search(r"[ \t]*<item objectid=\"" + re.escape(orig_id)
                     + r"\"[^>]*/>\n?", model)
    copy = item.group(0).replace(f'objectid="{orig_id}"',
                                 f'objectid="{new_id}"')
    model = model[:item.end()] + copy + model[item.end():]
    entries[engine.MODEL_ENTRY] = model.encode("utf-8")

    ms = entries[engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
    block = re.search(r"[ \t]*<object id=\"" + re.escape(orig_id)
                      + r"\">.*?</object>\n?", ms, re.S)
    copy = block.group(0).replace(f'<object id="{orig_id}">',
                                  f'<object id="{new_id}">', 1)
    copy = re.sub(r'(key="name" value=")[^"]*(")',
                  rf"\g<1>{new_name}\g<2>", copy)
    ms = ms[:block.end()] + copy + ms[block.end():]
    inst = re.search(r"[ \t]*<model_instance>.*?</model_instance>\n?",
                     ms, re.S)
    copy = inst.group(0).replace(f'key="object_id" value="{orig_id}"',
                                 f'key="object_id" value="{new_id}"')
    copy = re.sub(r'(key="identify_id" value=")[^"]*(")',
                  r"\g<1>463\g<2>", copy)
    ms = ms[:inst.end()] + copy + ms[inst.end():]
    entries[engine.MODEL_SETTINGS_ENTRY] = ms.encode("utf-8")

    engine.save_entries(project_path, entries)
    return orig_id


# A stand-in for the engine's merge-export (newjob.build_merge_command
# is patched to run it): copies a prepared "merged" fixture into place
# and writes result.json, honouring the real contract.

_FAKE_MERGE_SCRIPT = r'''
import json, shutil, sys

fixture, out_name, behaviour = sys.argv[1], sys.argv[2], sys.argv[3]
if behaviour == "fail":
    json.dump({"return_code": -3,
               "error_string": "The input files to the slicer are "
                               "not found."},
              open("result.json", "w"))
    sys.exit(0)
shutil.copy2(fixture, out_name)
json.dump({"return_code": 0, "error_string": "Success."},
          open("result.json", "w"))
'''


def make_fake_merge(sandbox):
    """Write the fake merge script; returns its path."""
    script = Path(sandbox) / "fake_merge.py"
    script.write_text(_FAKE_MERGE_SCRIPT, encoding="utf-8")
    return script


def write_stl(vertices, triangles, path):
    """A minimal binary STL from a parsed mesh (for integration tests)."""
    import struct
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(triangles)))
        for a, b, c in triangles:
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for idx in (a, b, c):
                f.write(struct.pack("<3f", *vertices[idx]))
            f.write(struct.pack("<H", 0))
    return path


# Where a real Bambu Studio would be; integration tests skip without it.
def real_studio_exe():
    import config_store
    return config_store.detect_bambu_exe()


# ------------------------------------------------ fake PrusaSlicer

# A miniature vendor bundle in PrusaSlicer's own INI shape: abstract
# "*common*" parents, multi-parent inheritance, the Core One with its
# defaults, and an XL that is NOT installed (so the installed-models
# filter has something to hide).
_FAKE_PRUSA_BUNDLE = """# Fake PrusaResearch bundle for the AccessSlice tests
[vendor]
name = Prusa Research

[printer_model:COREONE]
name = Prusa CORE One
variants = HF0.4; 0.4
technology = FFF

[printer_model:XL5IS]
name = Original Prusa XL - 5T
variants = 0.4
technology = FFF

[print:*common*]
layer_height = 0.2
perimeters = 2
fill_density = 15%
fill_pattern = grid
support_material = 0
support_material_threshold = 35
brim_type = no_brim
complete_objects = 0

[print:*speed*]
inherits = *common*
perimeter_speed = 200

[print:0.20mm SPEED @COREONE HF0.4]
inherits = *speed*
compatible_printers_condition = printer_model=="COREONE"
perimeters = 3

[print:0.20mm QUALITY @COREONE HF0.4]
inherits = *common*
layer_height = 0.15

[print:0.20mm SPEED @XL 0.4]
inherits = *speed*

[filament:*common*]
temperature = 215
first_layer_temperature = 220
bed_temperature = 60
first_layer_bed_temperature = 60
filament_type = PLA
filament_colour = #FF8000
extrusion_multiplier = 1

[filament:Prusament PLA @COREONE HF0.4]
inherits = *common*
temperature = 225

[filament:Generic PLA @COREONE]
inherits = *common*

[filament:Prusament PETG @COREONE HF0.4]
inherits = *common*
filament_type = PETG
temperature = 240

[filament:Prusament PLA @XL]
inherits = *common*

[printer:*common*]
bed_shape = 0x0,250x0,250x220,0x220
max_print_height = 270
nozzle_diameter = 0.4
binary_gcode = 1
gcode_flavor = marlin2
extruder_clearance_height = 33
extruder_clearance_radius = 75
retract_length = 0.7

[printer:Prusa CORE One HF0.4 nozzle]
inherits = *common*
printer_model = COREONE
printer_variant = HF0.4
default_print_profile = 0.20mm SPEED @COREONE HF0.4
default_filament_profile = "Prusament PLA @COREONE HF0.4"

[printer:Prusa CORE One 0.4 nozzle]
inherits = *common*
printer_model = COREONE
printer_variant = 0.4
default_print_profile = 0.20mm QUALITY @COREONE HF0.4
default_filament_profile = "Generic PLA @COREONE"

[printer:Original Prusa XL 0.4 nozzle]
inherits = *common*
printer_model = XL5IS
printer_variant = 0.4
bed_shape = 0x0,360x0,360x360,0x360
"""

_FAKE_PRUSA_INI = """# fake PrusaSlicer.ini
version = 2.9.6

[presets]
printer = Prusa CORE One HF0.4 nozzle

[vendor:PrusaResearch]
model:COREONE = HF0.4; 0.4
"""


def make_fake_prusa(sandbox):
    """A stub PrusaSlicer install plus a mini library under the
    sandbox's AppData (point %APPDATA% at sandbox/AppData). Returns
    the GUI exe path (the console exe sits beside it)."""
    root = Path(sandbox) / "FakePrusaSlicer"
    root.mkdir(parents=True, exist_ok=True)
    (root / "prusa-slicer-console.exe").write_bytes(b"")
    exe = root / "prusa-slicer.exe"
    exe.write_bytes(b"")
    appdata = Path(sandbox) / "AppData" / "PrusaSlicer"
    (appdata / "vendor").mkdir(parents=True, exist_ok=True)
    (appdata / "vendor" / "PrusaResearch.ini").write_text(
        _FAKE_PRUSA_BUNDLE, encoding="utf-8")
    (appdata / "PrusaSlicer.ini").write_text(_FAKE_PRUSA_INI,
                                             encoding="utf-8")
    for scope in ("print", "filament", "printer"):
        (appdata / scope).mkdir(exist_ok=True)
    (appdata / "print" / "My draft.ini").write_text(
        "# generated by PrusaSlicer 2.9.6\n"
        "inherits = 0.20mm SPEED @COREONE HF0.4\n"
        "layer_height = 0.3\n", encoding="utf-8")
    return exe


# The fake engine speaks PrusaSlicer's two contracts this app uses:
# --export-gcode (writes a tiny binary or ASCII G-code with real
# metadata blocks, or refuses like the real engine) and --export-3mf
# (copies a prepared model-only 3MF into place, as the real CLI does
# without ever writing the settings entry).
_FAKE_PRUSA_ENGINE_SCRIPT = r'''
import shutil, struct, sys, time, zlib

out_name, behaviour = sys.argv[1], sys.argv[2]
PRINTER = b"printer_model=COREONE\nobjects_info={\"objects\":[{\"name\":\"C:/models/blind-benchy.stl\",\"polygon\":[[1,1]]}]}\n"
PRINT = (b"filament used [mm]=9187.48\nfilament used [g]=27.40\n"
         b"total filament used [g]=27.40\n"
         b"estimated printing time (normal mode)=1h 35m 19s\n")

def block(btype, comp, data, params=b"\x00\x00"):
    payload = zlib.compress(data) if comp == 1 else data
    head = struct.pack("<HHI", btype, comp, len(data))
    if comp:
        head += struct.pack("<I", len(payload))
    return head + params + payload + struct.pack("<I", 0)

if behaviour == "export":
    shutil.copy(sys.argv[3], out_name)
    print("File exported to " + out_name)
    sys.exit(0)
if behaviour == "hang":
    time.sleep(600)
    sys.exit(0)
if behaviour == "fail":
    print("All objects are outside of the print volume.")
    sys.exit(1)
if behaviour == "crash":
    sys.exit(3)
for i in range(3):
    print(f"{30 * (i + 1)} => Slicing stage {i + 1}")
if behaviour == "ascii":
    with open(out_name, "wb") as f:
        f.write(b"; generated by PrusaSlicer 2.9.6\n"
                b"; printer_model = COREONE\n"
                b"; objects_info = {\"objects\":[{\"name\":\"blind-benchy.stl\"}]}\n"
                b"G28\nG1 X1\n"
                b"; filament used [g] = 27.40\n"
                b"; total filament used [g] = 27.40\n"
                b"; estimated printing time (normal mode) = 1h 35m 19s\n")
else:
    data = (b"GCDE" + struct.pack("<IH", 1, 1)
            + block(0, 0, b"Producer=PrusaSlicer 2.9.6\n")
            + block(3, 0, PRINTER)
            + block(4, 1, PRINT)
            + block(2, 1, b"layer_height=0.2\n")
            + block(1, 3, b"\x00" * 16, params=b"\x02\x00"))
    with open(out_name, "wb") as f:
        f.write(data)
print("Slicing result exported to " + out_name)
'''


def make_fake_prusa_engine(sandbox):
    """Write the fake PrusaSlicer engine script; returns its path.

    Use with patched builders, e.g.:
        prusa_engine.build_command = \
            lambda exe, project, plate, out: \
                [sys.executable, str(script), out, "success"]
        prusa_newjob.build_export_command = \
            lambda exe, models, out, centre: \
                [sys.executable, str(script), out, "export", fixture]
    """
    script = Path(sandbox) / "fake_prusa_engine.py"
    script.write_text(_FAKE_PRUSA_ENGINE_SCRIPT, encoding="utf-8")
    return script


def real_prusa_exe():
    """The real PrusaSlicer, or None (integration tests skip)."""
    path = Path(r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe")
    return path if path.is_file() else None


# ------------------------------------ fake OrcaSlicer and Anycubic

def make_fake_orca(sandbox):
    """A stub orca-slicer.exe with the mini Bambu library under
    resources\\profiles\\BBL plus a second, NOT installed vendor
    (Custom), and Orca's config folder under sandbox\\AppData with
    BBL as the one installed vendor and one user preset. Point
    %APPDATA% at sandbox\\AppData."""
    root = Path(sandbox) / "FakeOrcaSlicer"
    exe = root / "orca-slicer.exe"
    root.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    profiles_dir = root / "resources" / "profiles"
    for scope, presets in _FAKE_LIBRARY.items():
        folder = profiles_dir / "BBL" / scope
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in presets.items():
            (folder / f"{name}.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8")
    custom = profiles_dir / "Custom" / "machine"
    custom.mkdir(parents=True, exist_ok=True)
    (custom / "My Custom Printer 0.4 nozzle.json").write_text(json.dumps({
        "instantiation": "true", "nozzle_diameter": ["0.4"],
        "printable_area": ["0x0", "220x0", "220x220", "0x220"]}),
        encoding="utf-8")
    appdata = Path(sandbox) / "AppData" / "OrcaSlicer"
    (appdata / "system" / "BBL").mkdir(parents=True, exist_ok=True)
    user = appdata / "user" / "default"
    for scope in ("machine", "process", "filament"):
        (user / scope).mkdir(parents=True, exist_ok=True)
    (user / "process" / "My Orca draft.json").write_text(json.dumps({
        "name": "My Orca draft", "type": "process", "from": "User",
        "inherits": "0.20mm Standard @BBL X1C", "wall_loops": "5"}),
        encoding="utf-8")
    return exe


_FAKE_ANYCUBIC_LIBRARY = {
    "machine": {
        "fdm_anycubic_common": {
            "instantiation": "false",
            "printable_area": ["0x0", "250x0", "250x250", "0x250"],
            "printable_height": "250",
        },
        "Anycubic Kobra S1 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_anycubic_common",
            "nozzle_diameter": ["0.4"],
            "default_print_profile":
                "0.20mm Standard @Anycubic Kobra S1 0.4 nozzle",
            "default_filament_profile":
                ["Generic PLA @Anycubic Kobra S1 0.4 nozzle"],
        },
        "Anycubic Kobra 3 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_anycubic_common",
            "nozzle_diameter": ["0.4"],
        },
    },
    "process": {
        "fdm_process_anycubic_common": {
            "instantiation": "false", "wall_loops": "2",
            "sparse_infill_density": "15%", "layer_height": "0.2",
        },
        "0.20mm Standard @Anycubic Kobra S1 0.4 nozzle": {
            "instantiation": "true",
            "inherits": "fdm_process_anycubic_common", "wall_loops": "3",
        },
        "0.20mm Standard @Anycubic Kobra 3 0.4 nozzle": {
            "instantiation": "true",
            "inherits": "fdm_process_anycubic_common",
        },
    },
    "filament": {
        "fdm_filament_anycubic_pla": {
            "instantiation": "false", "nozzle_temperature": ["210"],
            "filament_type": ["PLA"],
        },
        "Generic PLA @Anycubic Kobra S1 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_filament_anycubic_pla",
        },
        "Anycubic PLA @Anycubic Kobra 3 0.4 nozzle": {
            "instantiation": "true", "inherits": "fdm_filament_anycubic_pla",
            "nozzle_temperature": ["215"],
        },
    },
}


def make_fake_anycubic(sandbox):
    """A stub AnycubicSlicerNext.exe with a mini Anycubic library and
    NO config folder (the program has never been launched)."""
    root = Path(sandbox) / "FakeAnycubic"
    exe = root / "AnycubicSlicerNext.exe"
    root.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    for scope, presets in _FAKE_ANYCUBIC_LIBRARY.items():
        folder = root / "resources" / "profiles" / "Anycubic" / scope
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in presets.items():
            (folder / f"{name}.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8")
    return exe


def real_orca_exe():
    path = Path(r"C:\Program Files\OrcaSlicer\orca-slicer.exe")
    return path if path.is_file() else None


def real_anycubic_exe():
    path = Path(r"C:\Program Files\AnycubicSlicerNext\AnycubicSlicerNext.exe")
    return path if path.is_file() else None


# ------------------------------------------------ fake PrusaLink

FAKE_PRUSALINK_KEY = "abcd1234"

_FAKE_PRUSALINK_STATUS = {
    "printer": {"state": "PRINTING", "temp_nozzle": 214.9,
                "target_nozzle": 215, "temp_bed": 60.1,
                "target_bed": 60, "axis_z": 12.4, "flow": 100,
                "speed": 100, "fan_hotend": 5000, "fan_print": 6500},
    "job": {"id": 7, "progress": 41.5, "time_remaining": 1800,
            "time_printing": 1300},
}


class FakePrusaLinkPrinter:
    """A miniature PrusaLink server on 127.0.0.1, faithful to the
    OpenAPI contract AccessSlice uses: X-Api-Key checked on every
    call (401 otherwise), PUT /api/v1/files/usb/<name> stores the
    bytes and records the headers, POST on the same path records a
    start (409 when the fake is set busy), GET /api/v1/status and
    /api/v1/job answer the status given, GET /api/version answers
    like a Buddy printer."""

    def __init__(self, status=None, key=FAKE_PRUSALINK_KEY):
        self.uploads = {}    # file name -> bytes
        self.headers = {}    # file name -> dict of upload headers
        self.starts = []     # file names asked to print
        self.refuse_start = False
        self.key = key
        self.status = json.loads(json.dumps(
            status or _FAKE_PRUSALINK_STATUS))
        parent = self

        class _H(BaseHTTPRequestHandler):
            def _auth(self):
                if self.headers.get("X-Api-Key") == parent.key:
                    return True
                self._reply(401, b'{"message":"Unauthorized"}')
                return False

            def _reply(self, code, body=b""):
                self.send_response(code)
                if body:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _name(self):
                prefix = "/api/v1/files/usb/"
                if not self.path.startswith(prefix):
                    return None
                from urllib.parse import unquote
                return unquote(self.path[len(prefix):])

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/api/version":
                    self._reply(200, json.dumps({
                        "api": "2.0.0", "version": "6.4.0",
                        "printer": "Prusa CORE One", "text":
                        "PrusaLink", "firmware": "6.4.0",
                        "capabilities": {"upload-by-put": True},
                    }).encode())
                elif self.path == "/api/v1/status":
                    self._reply(200, json.dumps(parent.status).encode())
                elif self.path == "/api/v1/job":
                    job = parent.status.get("job")
                    if not job:
                        self._reply(404, b'{"message":"No job"}')
                        return
                    self._reply(200, json.dumps(dict(
                        job, state=parent.status["printer"]["state"],
                        file={"name": "CUBE~1.BGC",
                              "display_name": "cube.bgcode",
                              "path": "/usb"})).encode())
                else:
                    self._reply(404, b'{"message":"Not found"}')

            def do_PUT(self):
                if not self._auth():
                    return
                name = self._name()
                if name is None:
                    self._reply(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                parent.uploads[name] = self.rfile.read(length)
                parent.headers[name] = {k: v for k, v
                                        in self.headers.items()}
                self._reply(201)

            def do_POST(self):
                if not self._auth():
                    return
                name = self._name()
                if name is None or name not in parent.uploads:
                    self._reply(404, b'{"message":"No such file"}')
                    return
                if parent.refuse_start:
                    self._reply(409, b'{"message":"Printer is busy"}')
                    return
                parent.starts.append(name)
                self._reply(204)

            def log_message(self, fmt, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()

    def address(self):
        return f"127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()


# ---------------------------------------------- simulated Core One
#
# A fake Core One that BEHAVES: temperatures move toward their
# targets, a started file runs line by line with delays, load
# filament stops for the knob like the real firmware (2026-09-09),
# and the firmware's 170 degree standby after a load or unload is
# reproduced. Ported 2026-09-12 from the Prusa Accessible Control
# program's simulator, behind a PrusaLink-shaped HTTP server, so
# printer_control's command jobs and the control pages are tested
# end to end without a printer. speed > 1 makes everything faster.

import re as _re
import time as _time


class SimulatedCoreOne:

    def __init__(self, speed=20.0):
        self.speed = speed
        self.lock = threading.RLock()
        self.state = "IDLE"
        self.temp = {"nozzle": 24.0, "bed": 23.0, "chamber": 25.0}
        self.target = {"nozzle": 0.0, "bed": 0.0, "chamber": 0.0}
        # The nozzle starts clear of the plate, as after the
        # printer's own home (it lifts after the nozzle touch; the
        # Core One's exact rest height is not recorded).
        self.axis = {"x": 0.0, "y": 0.0, "z": 5.0}
        self.fan_print = 0
        self.files = {"usb/example_print.gcode": b"; example\nG28\n"}
        self.folders = {"usb"}
        self.job = None
        self.job_counter = 100
        self.prompt = None
        self.executed = []      # every G-code line run, in order
        self.beeps = 0
        self.filament_loaded = True
        self.screen_message = ""
        self.deleted = []
        self._queue = []
        self._wait_until = 0.0
        self._wait_temp = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()

    # -------------------------------------------------- simulation

    def _loop(self):
        last = _time.monotonic()
        while not self._stop.is_set():
            _time.sleep(0.02)
            now = _time.monotonic()
            dt = (now - last) * self.speed
            last = now
            with self.lock:
                self._step_temps(dt)
                self._step_job()

    def _step_temps(self, dt):
        rates = {"nozzle": 6.0, "bed": 1.5, "chamber": 0.3}
        ambient = {"nozzle": 24.0, "bed": 23.0, "chamber": 25.0}
        for key in self.temp:
            goal = self.target[key] if self.target[key] > 0 else ambient[key]
            diff = goal - self.temp[key]
            step = rates[key] * dt
            if abs(diff) <= step:
                self.temp[key] = goal
            else:
                self.temp[key] += step if diff > 0 else -step

    def _step_job(self):
        if self.job is None or self.state not in ("PRINTING", "BUSY"):
            return
        now = _time.monotonic()
        if self._wait_temp is not None:
            key, goal = self._wait_temp
            if abs(self.temp[key] - goal) > 2.0:
                return
            self._wait_temp = None
        if now < self._wait_until:
            return
        if not self._queue:
            self._finish_job("FINISHED")
            return
        line = self._queue.pop(0)
        self._execute(line)
        total = max(1, self.job["total_lines"])
        self.job["progress"] = round(
            100.0 * (total - len(self._queue)) / total, 1)

    def _delay(self, seconds):
        self._wait_until = _time.monotonic() + seconds / self.speed

    def _execute(self, line):
        code = line.split(";", 1)[0].strip()
        if not code:
            return
        cmd = code.split()[0].upper()
        args = _gcode_args(code)
        if cmd == "M117":
            self.screen_message = code[4:].strip()
        elif cmd in ("M104", "M109"):
            self.target["nozzle"] = args.get("S", 0.0)
            if cmd == "M109" and self.target["nozzle"] > 0:
                self._wait_temp = ("nozzle", self.target["nozzle"])
        elif cmd in ("M140", "M190"):
            self.target["bed"] = args.get("S", 0.0)
            if cmd == "M190" and self.target["bed"] > 0:
                self._wait_temp = ("bed", self.target["bed"])
        elif cmd in ("M141", "M191"):
            self.target["chamber"] = args.get("S", 0.0)
        elif cmd == "M106":
            self.fan_print = int(round(args.get("S", 255) * 100 / 255))
        elif cmd == "M107":
            self.fan_print = 0
        elif cmd == "G28":
            for axis in "xyz":
                if (not any(a in args for a in "XYZ")
                        or axis.upper() in args):
                    self.axis[axis] = 5.0 if axis == "z" else 0.0
            self._delay(2.0)
        elif cmd in ("G0", "G1"):
            relative = self.job.get("relative", False)
            for axis in "xyz":
                if axis.upper() in args:
                    value = args[axis.upper()]
                    self.axis[axis] = (self.axis[axis] + value
                                       if relative else value)
            self._delay(0.5)
        elif cmd == "G91":
            self.job["relative"] = True
        elif cmd == "G90":
            self.job["relative"] = False
        elif cmd == "M701":
            self.filament_loaded = True
            self._delay(3.0)
            self.prompt = "Is the filament colour correct?"
            self.state = "ATTENTION"
            # Like the real firmware: the load dialog leaves 170.
            self.target["nozzle"] = 170.0
        elif cmd == "M702":
            self.filament_loaded = False
            self._delay(3.0)
            self.target["nozzle"] = 170.0
        elif cmd == "M1701":
            self.filament_loaded = True
            self._delay(2.0)
        elif cmd == "M300":
            self.beeps += 1
        elif cmd in ("G29", "G162", "G64", "G30"):
            self._delay(3.0)
        elif cmd == "M112":
            self._finish_job("ERROR")
        elif cmd == "M50":
            self._delay(4.0)
            self.prompt = "Self test finished"
            self.state = "ATTENTION"
        else:
            self._delay(0.1)
        self.executed.append(code)

    def _finish_job(self, final_state):
        self.state = final_state
        if self.job is not None:
            self.job["state"] = final_state
        self._queue = []
        self._wait_temp = None
        self.prompt = None

    # ----------------------------------------------- the API side

    def version(self):
        return {"api": "2.0.0", "version": "2.1.2", "printer":
                "Prusa CORE One", "text": "PrusaLink", "firmware":
                "6.8.1", "capabilities": {"upload-by-put": True}}

    def info(self):
        return {"name": "Simulated CORE One", "location": "Desk",
                "nozzle_diameter": 0.4, "min_extrusion_temp": 170,
                "serial": "SIM000001", "hostname": "simulated-core-one",
                "mmu": False, "sd_ready": True, "farm_mode": False}

    def status(self):
        with self.lock:
            status = {"printer": {
                "state": self.state,
                "temp_nozzle": round(self.temp["nozzle"], 1),
                "target_nozzle": self.target["nozzle"],
                "temp_bed": round(self.temp["bed"], 1),
                "target_bed": self.target["bed"],
                "temp_chamber": round(self.temp["chamber"], 1),
                "target_chamber": self.target["chamber"],
                "axis_x": round(self.axis["x"], 2),
                "axis_y": round(self.axis["y"], 2),
                "axis_z": round(self.axis["z"], 2),
                "flow": 100, "speed": 100,
                "fan_hotend": 0 if self.temp["nozzle"] < 50 else 5000,
                "fan_print": self.fan_print,
                "status_printer": {"ok": self.state != "ERROR",
                                   "message": self.prompt or "OK"},
            }, "storage": {"name": "usb", "path": "/usb/",
                           "read_only": False,
                           "free_space": 8_000_000_000}}
            # Like the real printer, the last job stays reported
            # (with its final state) until the next one starts.
            if self.job is not None:
                status["job"] = {
                    "id": self.job["id"], "progress": self.job["progress"],
                    "time_printing": int(_time.monotonic()
                                         - self.job["started"]),
                    "time_remaining": max(0, len(self._queue))}
            return status

    def current_job(self):
        with self.lock:
            if self.job is None:
                return None
            return {"id": self.job["id"], "state": self.job["state"],
                    "progress": self.job["progress"],
                    "time_printing": int(_time.monotonic()
                                         - self.job["started"]),
                    "time_remaining": max(0, len(self._queue)),
                    "file": {"name": self.job["file"].rsplit("/", 1)[-1],
                             "display_name": self.job["file"].rsplit(
                                 "/", 1)[-1],
                             "path": "/usb/" + self.job["file"]}}

    def listing(self, path=""):
        """The folder dict PrusaLink answers, or None for no such
        folder (a file's own dict when path names a file)."""
        with self.lock:
            prefix = f"usb/{path.strip('/')}".rstrip("/")
            if prefix not in self.folders:
                if prefix in self.files:
                    return {"name": prefix.rsplit("/", 1)[-1],
                            "type": "PRINT_FILE",
                            "size": len(self.files[prefix]),
                            "refs": {"download": "/" + prefix}}
                return None
            children, seen = [], set()
            for key in list(self.files) + list(self.folders):
                if not key.startswith(prefix + "/"):
                    continue
                name = key[len(prefix) + 1:].split("/", 1)[0]
                if name in seen:
                    continue
                seen.add(name)
                full = f"{prefix}/{name}"
                if full in self.folders:
                    children.append({"name": name, "display_name": name,
                                     "type": "FOLDER"})
                else:
                    children.append({"name": name.upper()[:8],
                                     "display_name": name,
                                     "type": "PRINT_FILE",
                                     "size": len(self.files[full]),
                                     "m_timestamp": int(_time.time())})
            return {"name": prefix.rsplit("/", 1)[-1], "type": "FOLDER",
                    "children": children}

    def upload(self, path, data):
        with self.lock:
            key = f"usb/{path.strip('/')}"
            self.folders.add(key.rsplit("/", 1)[0])
            self.files[key] = data

    def make_folder(self, path):
        with self.lock:
            self.folders.add(f"usb/{path.strip('/')}")

    def start(self, path):
        """None on success, else the HTTP code to answer."""
        with self.lock:
            key = f"usb/{path.strip('/')}"
            if key not in self.files:
                return 404
            if self.state in ("PRINTING", "PAUSED", "ATTENTION", "BUSY"):
                return 409
            self.job_counter += 1
            lines = self.files[key].decode("utf-8", "replace").splitlines()
            self._queue = list(lines)
            self.job = {"id": self.job_counter, "state": "PRINTING",
                        "progress": 0.0, "started": _time.monotonic(),
                        "file": path.strip("/"), "total_lines": len(lines)}
            self.state = "PRINTING"
            self._wait_until = _time.monotonic() + 1.0 / self.speed
            if not any("M862.3" in line for line in lines):
                self.prompt = ("G-code compatibility warning: printer "
                               "model not specified. Continue?")
                self.state = "ATTENTION"
            return None

    def delete(self, path):
        with self.lock:
            key = f"usb/{path.strip('/')}"
            if (self.job and self.job["file"] == path.strip("/")
                    and self.state in ("PRINTING", "PAUSED", "ATTENTION")):
                return 409
            if key in self.files:
                del self.files[key]
                self.deleted.append(path.strip("/"))
            elif key in self.folders:
                self.folders.discard(key)
            else:
                return 404
            return None

    def job_call(self, job_id, verb):
        """pause | resume | stop | continue: None or an HTTP code."""
        with self.lock:
            if self.job is None or self.job["id"] != job_id:
                return 404
            if verb == "continue":
                return 400   # the real firmware has no such call
            if verb == "pause":
                if self.state != "PRINTING":
                    return 409
                self.state = self.job["state"] = "PAUSED"
            elif verb == "resume":
                if self.state != "PAUSED":
                    return 409
                self.state = self.job["state"] = "PRINTING"
            elif verb == "stop":
                self._finish_job("STOPPED")
            return None

    def press_knob(self):
        """What the person at the printer does to confirm."""
        with self.lock:
            if self.state == "ATTENTION":
                self.prompt = None
                self.state = "PRINTING"
                if self.job is not None:
                    self.job["state"] = "PRINTING"

    def wait_for_state(self, *states, timeout=15):
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if self.state in states:
                return True
            _time.sleep(0.02)
        return False


_GCODE_ARG = _re.compile(r"([A-Z])(-?\d+(?:\.\d+)?)")


def _gcode_args(code):
    parts = code.split(None, 1)
    if len(parts) < 2:
        return {}
    rest = _re.sub(r'"[^"]*"', "", parts[1])
    return {m.group(1): float(m.group(2)) for m in _GCODE_ARG.finditer(rest)}


class FakeCoreOnePrinter:
    """The simulated Core One behind PrusaLink's HTTP shape, on
    127.0.0.1: X-Api-Key checked on every call, the version, info,
    status and job reads, folder listings, uploads (a trailing slash
    makes a folder), starts, deletes, and the job calls (pause,
    resume, stop; continue answers 400 like the real firmware).
    .printer is the simulator itself, for the tests' eyes."""

    def __init__(self, speed=20.0, key=FAKE_PRUSALINK_KEY):
        self.printer = SimulatedCoreOne(speed=speed)
        self.key = key
        parent = self

        class _H(BaseHTTPRequestHandler):
            def _auth(self):
                if self.headers.get("X-Api-Key") == parent.key:
                    return True
                self._reply(401, {"message": "Unauthorized"})
                return False

            def _reply(self, code, body=None):
                data = (json.dumps(body).encode() if body is not None
                        else b"")
                self.send_response(code)
                if data:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def _file(self):
                prefix = "/api/v1/files/usb/"
                path = self.path.split("?", 1)[0]
                if path == prefix.rstrip("/"):
                    return ""
                if not path.startswith(prefix):
                    return None
                return urllib.parse.unquote(path[len(prefix):])

            def _job(self):
                m = _re.fullmatch(r"/api/v1/job/(\d+)(?:/(\w+))?",
                                  self.path)
                return (int(m.group(1)), m.group(2)) if m else None

            def do_GET(self):
                if not self._auth():
                    return
                p = parent.printer
                if self.path == "/api/version":
                    return self._reply(200, p.version())
                if self.path == "/api/v1/info":
                    return self._reply(200, p.info())
                if self.path == "/api/v1/status":
                    return self._reply(200, p.status())
                if self.path == "/api/v1/job":
                    job = p.current_job()
                    if job is None or job["state"] not in (
                            "PRINTING", "PAUSED", "ATTENTION", "BUSY"):
                        return self._reply(404, {"message": "No job"})
                    return self._reply(200, job)
                path = self._file()
                if path is not None:
                    listing = p.listing(path)
                    if listing is None:
                        return self._reply(404, {"message": "Not found"})
                    return self._reply(200, listing)
                self._reply(404, {"message": "Not found"})

            def do_PUT(self):
                if not self._auth():
                    return
                job = self._job()
                if job:
                    code = parent.printer.job_call(job[0], job[1])
                    return self._reply(code or 204,
                                       {"message": "refused"}
                                       if code else None)
                path = self._file()
                if not path:
                    return self._reply(404)
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length)
                if path.endswith("/"):
                    parent.printer.make_folder(path)
                else:
                    parent.printer.upload(path, data)
                self._reply(201)

            def do_POST(self):
                if not self._auth():
                    return
                path = self._file()
                if not path:
                    return self._reply(404)
                code = parent.printer.start(path)
                self._reply(code or 204,
                            {"message": "Printer is busy"} if code == 409
                            else {"message": "No such file"} if code
                            else None)

            def do_DELETE(self):
                if not self._auth():
                    return
                job = self._job()
                if job:
                    code = parent.printer.job_call(job[0], "stop")
                    return self._reply(code or 204,
                                       {"message": "refused"}
                                       if code else None)
                path = self._file()
                if not path:
                    return self._reply(404)
                code = parent.printer.delete(path)
                self._reply(code or 204,
                            {"message": "refused"} if code else None)

            def log_message(self, fmt, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()

    def address(self):
        return f"127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.printer.close()
        self.httpd.shutdown()
