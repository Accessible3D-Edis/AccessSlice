"""The PACKAGED program: AccessSlice.exe as built by packaging\\build.py,
started headless (--serve) and driven over HTTP like a browser.

The rest of the suite imports the app from app\\ under the developer's
Python. This module proves the bundle that ships is complete: the exe
starts without a console, every static file and the Prusa defaults
travelled with it, the 3MF engine from tools\\ is importable inside
the bundle (opening a project uses it), the backends registry is
whole (the setup page offers every slicer), and the version resource
matches app\\version.py.

Skipped when no bundle exists (packaging\\dist\\AccessSlice\\
AccessSlice.exe) - a normal checkout has none. Hermetic all the same:
%APPDATA% for the exe points into a sandbox, the project is a private
copy of the committed test project, and the "slicer" is the empty
stub file the HTTP suite uses too (the exe never launches it).
"""

import http.client
import json
import os
import shutil
import socket
import subprocess
import time
import unittest
import urllib.parse

import support
from version import VERSION

EXE = support.REPO_ROOT / "packaging" / "dist" / "AccessSlice" / "AccessSlice.exe"
INTERNAL = EXE.parent / "_internal"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(EXE.is_file(),
                     "no packaged build (run packaging\\build.py first)")
class TestPackagedProgram(unittest.TestCase):
    proc = None
    port = None
    sandbox = None

    @classmethod
    def setUpClass(cls):
        cls.sandbox = support.make_sandbox()
        appdata = cls.sandbox / "AppData"
        projects = cls.sandbox / "projects"
        projects.mkdir()
        cls.project = support.copy_test_project(projects)
        fake_exe = support.make_fake_studio(cls.sandbox)
        (appdata / "AccessSlice").mkdir(parents=True)
        (appdata / "AccessSlice" / "config.json").write_text(json.dumps({
            "slicer": "bambu",
            "slicer_exe": str(fake_exe),
            "projects_dir": str(projects),
            "known_printers": ["Bambu Lab H2C 0.4 nozzle"],
        }), encoding="utf-8")
        env = dict(os.environ, APPDATA=str(appdata))
        cls.port = _free_port()
        cls.proc = subprocess.Popen([str(EXE), "--serve", str(cls.port)],
                                    env=env, stdin=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", cls.port)) == 0:
                    return
            if cls.proc.poll() is not None:
                break
            time.sleep(0.2)
        cls.tearDownClass()
        raise RuntimeError("AccessSlice.exe did not start serving")

    @classmethod
    def tearDownClass(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.kill()
            cls.proc.wait(10)
        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def request(self, method, url, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            if form is None:
                conn.request(method, url)
            else:
                conn.request(method, url, body=urllib.parse.urlencode(form),
                             headers={"Content-Type":
                                      "application/x-www-form-urlencoded"})
            resp = conn.getresponse()
            return (resp.status, dict(resp.getheaders()),
                    resp.read().decode("utf-8", errors="replace"))
        finally:
            conn.close()

    def test_01_bundle_carries_every_runtime_file(self):
        shipped = sorted(p.name for p in (INTERNAL / "static").iterdir())
        source = sorted(p.name for p in
                        (support.APP_DIR / "static").iterdir() if p.is_file())
        self.assertEqual(shipped, source)
        self.assertTrue((INTERNAL / "prusa_defaults.ini").is_file())
        self.assertTrue((INTERNAL / "webview" / "lib"
                         / "Microsoft.Web.WebView2.Core.dll").is_file())

    def test_02_home_renders_from_the_sandboxed_config(self):
        status, _h, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("AccessSlice", body)
        self.assertIn("Bambu Studio", body)

    def test_03_every_static_file_is_served(self):
        for name in sorted(p.name for p in (INTERNAL / "static").iterdir()):
            status, _h, body = self.request("GET", f"/static/{name}")
            self.assertEqual(status, 200, name)
            self.assertTrue(body, name)

    def test_04_setup_offers_every_backend(self):
        status, _h, body = self.request("GET", "/setup")
        self.assertEqual(status, 200)
        for slicer in ("Bambu Studio", "Creality Print", "OrcaSlicer",
                       "PrusaSlicer", "Anycubic Slicer Next"):
            self.assertIn(slicer, body)

    def test_05_open_project_uses_the_bundled_engine(self):
        status, headers, _b = self.request("POST", "/open",
                                           {"path": str(self.project)})
        self.assertEqual(status, 303)
        self.assertTrue(headers.get("Location", "").startswith("/project"))
        for tab in ("quality", "placement", "all"):
            status, _h, body = self.request("GET", f"/project/{tab}")
            self.assertEqual(status, 200, tab)
            self.assertIn("blind-benchy", body, tab)

    def test_06_diagnostics_names_the_version(self):
        status, _h, body = self.request("GET", "/diagnostics")
        self.assertEqual(status, 200)
        self.assertIn(f"AccessSlice version {VERSION}", body)
        self.assertIn("serving without a window", body)

    def test_08_signed_exactly_when_a_signing_configuration_exists(self):
        # The PE certificate table (data directory 4) is non-empty on a
        # signed file. PE32+: optional header at pe+24, directories at
        # +112, 8 bytes each.
        data = EXE.read_bytes()
        pe = int.from_bytes(data[0x3C:0x40], "little")
        entry = pe + 24 + 112 + 4 * 8
        size = int.from_bytes(data[entry + 4:entry + 8], "little")
        configured = (support.REPO_ROOT / "packaging" / "signing.json").is_file()
        if configured:
            self.assertGreater(size, 0, "signing.json exists but the "
                               "built exe carries no signature")
        else:
            self.assertEqual(size, 0, "the exe is signed although no "
                             "signing configuration exists")

    def test_07_exe_is_windowed_and_stamped(self):
        # The PE subsystem byte: 2 = GUI (no console), 3 = console.
        data = EXE.read_bytes()
        pe = int.from_bytes(data[0x3C:0x40], "little")
        subsystem = int.from_bytes(data[pe + 4 + 20 + 68:pe + 4 + 20 + 70],
                                   "little")
        self.assertEqual(subsystem, 2)
        self.assertIn(VERSION.encode("utf-16-le"), data)


if __name__ == "__main__":
    unittest.main()
