"""Switching slicers on the Settings page brings that slicer's own
program path with it.

Edis, 2026-09-09: "When I want to change slicer, for instance from
prusa to creality, it should automatically switch the path to where
creality is. Right now, when I switch, it still has the prusa
location placed and I need manually to type the address in again."

Two halves: config_store remembers slicer -> path at every save and
offers it back (exe_for); the save treats the OLD slicer's path in
the field as empty when another slicer is chosen, so the new
slicer's remembered or detected path takes its place. The page puts
every slicer's path on its radio (data-exe) for the script that swaps
the field live; the save works the same without script. Runs over
real HTTP against the sandboxed server, with stub slicers that live
nowhere detection looks, so detection is stubbed where a test needs
it found or not found.
"""

import http.client
import os
import shutil
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from unittest import mock

import support
import backends
import config_store
import server


class F:
    httpd = None
    port = None
    sandbox = None
    old_config = None
    old_appdata = None
    projects_dir = None
    studio = None
    prusa = None
    creality = None


def setUpModule():
    F.sandbox = support.make_sandbox()
    F.projects_dir = F.sandbox / "projects"
    F.projects_dir.mkdir()
    F.studio = str(support.make_fake_studio(F.sandbox))
    F.prusa = str(support.make_fake_prusa(F.sandbox))
    F.creality = str(support.make_fake_creality(F.sandbox))
    F.old_config = config_store.CONFIG_PATH
    config_store.CONFIG_PATH = F.sandbox / "config.json"
    F.old_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(F.sandbox / "AppData")
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
    conn = http.client.HTTPConnection("127.0.0.1", F.port, timeout=10)
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


def on_prusa(**extra):
    """A user who set up PrusaSlicer, and once before that Creality
    Print - both paths remembered."""
    config = {"slicer": "prusa", "slicer_exe": F.prusa,
              "projects_dir": str(F.projects_dir), "tour_seen": True,
              "slicer_exes": {"prusa": F.prusa, "creality": F.creality}}
    config.update(extra)
    config_store.save(config)
    return config


def no_detection():
    """Every backend finds nothing in the standard places."""
    return [mock.patch.object(backends.get_backend(i), "detect_exe",
                              return_value=None)
            for i in backends.supported_ids()]


class TestExeMemory(unittest.TestCase):
    def test_the_remembered_path_wins_while_the_file_exists(self):
        cfg = {"slicer_exes": {"creality": F.creality}}
        self.assertEqual(config_store.exe_for(cfg, "creality",
                                              lambda: "detected"),
                         F.creality)

    def test_a_vanished_remembered_path_falls_back_to_detection(self):
        cfg = {"slicer_exes": {"creality": r"C:\gone\CrealityPrint.exe"}}
        self.assertEqual(config_store.exe_for(cfg, "creality",
                                              lambda: "detected"),
                         "detected")
        self.assertEqual(config_store.exe_for(cfg, "creality",
                                              lambda: None), "")

    def test_the_active_slicers_path_counts_before_the_table_exists(self):
        cfg = {"slicer": "prusa", "slicer_exe": F.prusa}
        self.assertEqual(config_store.exe_for(cfg, "prusa", lambda: None),
                         F.prusa)
        self.assertEqual(config_store.exe_for(cfg, "creality",
                                              lambda: None), "")

    def test_remember_exe_keeps_the_other_entries(self):
        cfg = {"slicer_exes": {"prusa": F.prusa}}
        config_store.remember_exe(cfg, "creality", F.creality)
        self.assertEqual(cfg["slicer_exes"],
                         {"prusa": F.prusa, "creality": F.creality})


class TestSwitchOverHttp(unittest.TestCase):
    def setUp(self):
        self._patches = no_detection()
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_switching_to_creality_with_prusas_path_in_the_field(self):
        on_prusa()
        status, headers, _b = request("POST", "/setup", {
            "slicer": "creality", "slicer_exe": F.prusa,
            "projects_dir": str(F.projects_dir)})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/?msg="), headers)
        self.assertIn("switched to Creality Print at",
                      urllib.parse.unquote(headers["Location"]))
        saved = config_store.load()
        self.assertEqual(saved["slicer"], "creality")
        self.assertEqual(saved["slicer_exe"], F.creality)
        self.assertEqual(saved["slicer_exes"]["prusa"], F.prusa)

    def test_and_back_to_prusa_brings_its_own_path_again(self):
        on_prusa(slicer="creality", slicer_exe=F.creality)
        status, headers, _b = request("POST", "/setup", {
            "slicer": "prusa", "slicer_exe": F.creality,
            "projects_dir": str(F.projects_dir)})
        self.assertEqual(status, 303)
        self.assertEqual(config_store.load()["slicer_exe"], F.prusa)

    def test_the_new_slicers_detected_path_is_taken_when_nothing_is_remembered(self):
        on_prusa(slicer_exes={"prusa": F.prusa})
        with mock.patch.object(backends.get_backend("creality"),
                               "detect_exe", return_value=F.creality):
            status, headers, _b = request("POST", "/setup", {
                "slicer": "creality", "slicer_exe": F.prusa,
                "projects_dir": str(F.projects_dir)})
        self.assertEqual(status, 303)
        saved = config_store.load()
        self.assertEqual(saved["slicer_exe"], F.creality)
        self.assertEqual(saved["slicer_exes"]["creality"], F.creality)

    def test_an_unknown_new_slicer_is_refused_in_words_not_saved_wrong(self):
        on_prusa(slicer_exes={"prusa": F.prusa})
        status, headers, _b = request("POST", "/setup", {
            "slicer": "creality", "slicer_exe": F.prusa,
            "projects_dir": str(F.projects_dir)})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/setup?msg="))
        self.assertIn("was not found at that path",
                      urllib.parse.unquote(headers["Location"]))
        self.assertEqual(config_store.load()["slicer"], "prusa")
        self.assertEqual(config_store.load()["slicer_exe"], F.prusa)

    def test_a_path_typed_for_the_new_slicer_is_kept_as_typed(self):
        on_prusa(slicer_exes={"prusa": F.prusa})
        status, headers, _b = request("POST", "/setup", {
            "slicer": "creality", "slicer_exe": F.creality,
            "projects_dir": str(F.projects_dir)})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/?msg="))
        self.assertEqual(config_store.load()["slicer_exe"], F.creality)
        self.assertEqual(config_store.load()["slicer_exes"]["creality"],
                         F.creality)

    def test_the_same_slicer_saved_again_keeps_its_path(self):
        on_prusa()
        status, headers, _b = request("POST", "/setup", {
            "slicer": "prusa", "slicer_exe": F.prusa,
            "projects_dir": str(F.projects_dir)})
        self.assertEqual(status, 303)
        self.assertEqual(config_store.load()["slicer_exe"], F.prusa)

    def test_the_page_carries_every_slicers_path_on_its_radio(self):
        on_prusa()
        status, _h, body = request("GET", "/setup")
        self.assertEqual(status, 200)
        self.assertIn(f'value="prusa" checked data-exe="{F.prusa}" '
                      'data-name="PrusaSlicer"', body)
        self.assertIn(f'value="creality" data-exe="{F.creality}" '
                      'data-name="Creality Print"', body)
        self.assertIn('value="bambu" data-exe="" data-name="Bambu Studio"',
                      body)
        self.assertIn('id="exe-note" aria-live="polite"', body)
        self.assertIn('id="slicer_exe" name="slicer_exe"\n           '
                      f'value="{F.prusa}"', body)


if __name__ == "__main__":
    unittest.main()
