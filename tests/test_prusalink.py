"""PrusaLink: the Core One send straight from AccessSlice (built
2026-09-07).

Edis's first real Prusa run ended at "Open the sliced file in
PrusaSlicer", which opens the G-code VIEWER - a dead end. This module
talks to the printer's own HTTP server instead, the way PrusaSlicer's
Ctrl+Shift+G does. Covers, against a fake PrusaLink on 127.0.0.1:

- the password (API key) sent as X-Api-Key and refused in words when
  wrong,
- the upload as a PUT with the RFC 8941 Overwrite / Print-After-Upload
  headers, the start as a POST, a busy printer's 409 in words,
- the status merged flat and spoken plainly, phases for the monitor,
- PrusaSlicer's own physical-printer record read for the address and
  the key (under the sandboxed %APPDATA%),
- the Prusa backend carrying the module as its lan part, and
- the address that is remembered (2026-09-21, after the Core One's
  address changed under AccessSlice): the printer's own name when it
  resolves to the address that worked, the typed address otherwise,
  and the unreachable words that say an address may have changed.
"""

import os
import shutil
import socket
import unittest
from pathlib import Path
from unittest import mock

import support
import backends
import prusalink
from errors import ProjectError


class FakePrinterTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.printer = support.FakePrusaLinkPrinter()

    @classmethod
    def tearDownClass(cls):
        cls.printer.close()

    def setUp(self):
        self.ip = self.printer.address()
        self.key = support.FAKE_PRUSALINK_KEY
        self.printer.refuse_start = False

    def test_backend_carries_the_lan_part(self):
        self.assertIs(backends.get_backend("prusa").lan, prusalink)
        self.assertTrue(prusalink.NEEDS_KEY)

    def test_check_printer_reads_the_version(self):
        info = prusalink.check_printer(self.ip, self.key)
        self.assertEqual(info["printer"], "Prusa CORE One")
        self.assertTrue(info["capabilities"]["upload-by-put"])

    def test_wrong_key_is_refused_in_words(self):
        with self.assertRaises(ProjectError) as ctx:
            prusalink.check_printer(self.ip, "nope")
        self.assertIn("password", str(ctx.exception))
        self.assertIn("Nothing was sent", str(ctx.exception))

    def test_unreachable_printer_in_words(self):
        with self.assertRaises(ProjectError) as ctx:
            prusalink.check_printer("127.0.0.1:1", self.key, timeout=2)
        words = str(ctx.exception)
        self.assertIn("Could not reach", words)
        # A numeric address may simply have changed: the words say
        # so and name the way out (the printer's name, .local).
        self.assertIn("address can change", words)
        self.assertIn(".local", words)

    def test_a_name_nobody_answers_to_is_said_as_that(self):
        error = mock.Mock()
        error.reason = socket.gaierror(11001, "getaddrinfo failed")
        words = prusalink._unreachable_words("no-such-printer.local",
                                             error)
        self.assertIn("Nothing on this network answers to the name "
                      "no-such-printer.local", words)
        self.assertNotIn("address can change", words)
        # A name that resolved but did not answer: the plain words,
        # without the changed-address advice (the name follows the
        # printer already).
        words = prusalink._unreachable_words("core-one.local:80",
                                             OSError("timed out"))
        self.assertIn("Could not reach the printer at core-one.local:80",
                      words)
        self.assertNotIn("address can change", words)

    def test_stable_address_prefers_the_printers_name_when_it_resolves(self):
        host, port = self.ip.split(":")
        info = {"hostname": "Simulated-Core-One", "serial": "SIM1"}
        with mock.patch.object(prusalink, "read_info", return_value=info), \
                mock.patch.object(prusalink, "_resolve",
                                  side_effect=lambda n: host
                                  if n == "simulated-core-one.local"
                                  else "") as resolve:
            self.assertEqual(prusalink.stable_address(self.ip, self.key),
                             f"simulated-core-one.local:{port}")
            resolve.assert_called_once_with("simulated-core-one.local")
            # A typed name is kept as typed, no lookup made.
            resolve.reset_mock()
            self.assertEqual(
                prusalink.stable_address("my-printer.local", self.key),
                "my-printer.local")
            resolve.assert_not_called()

    def test_stable_address_keeps_the_address_otherwise(self):
        # The name does not resolve (mDNS blocked, or another
        # printer answers to it): the address that worked stays.
        with mock.patch.object(prusalink, "read_info",
                               return_value={"hostname": "core"}), \
                mock.patch.object(prusalink, "_resolve",
                                  return_value="127.0.0.2"):
            self.assertEqual(prusalink.stable_address(self.ip, self.key),
                             self.ip)
        # No name in the info block, a name with odd characters, or
        # a printer that will not say (the simple fake has no info
        # call): the address, never an error.
        for info in ({}, {"hostname": ""}, {"hostname": "bad name/x"}):
            with mock.patch.object(prusalink, "read_info",
                                   return_value=info), \
                    mock.patch.object(prusalink, "_resolve") as resolve:
                self.assertEqual(
                    prusalink.stable_address(self.ip, self.key), self.ip)
                resolve.assert_not_called()
        with mock.patch.object(prusalink, "_resolve") as resolve:
            self.assertEqual(prusalink.stable_address(self.ip, self.key),
                             self.ip)
            resolve.assert_not_called()

    def test_upload_is_a_put_with_the_documented_headers(self):
        prusalink.upload_gcode(self.ip, "benchy 1.bgcode", b"BGCODE!",
                               key=self.key)
        self.assertEqual(self.printer.uploads["benchy 1.bgcode"],
                         b"BGCODE!")
        h = self.printer.headers["benchy 1.bgcode"]
        self.assertEqual(h.get("Overwrite"), "?1")
        self.assertEqual(h.get("Print-After-Upload"), "?0")
        self.assertEqual(h.get("Content-Type"), "application/octet-stream")

    def test_start_print_posts_the_same_path(self):
        prusalink.upload_gcode(self.ip, "go.bgcode", b"x", key=self.key)
        prusalink.start_print(self.ip, "go.bgcode", key=self.key)
        self.assertIn("go.bgcode", self.printer.starts)

    def test_busy_printer_says_so_and_keeps_the_file(self):
        prusalink.upload_gcode(self.ip, "wait.bgcode", b"x", key=self.key)
        self.printer.refuse_start = True
        with self.assertRaises(ProjectError) as ctx:
            prusalink.start_print(self.ip, "wait.bgcode", key=self.key)
        self.assertIn("busy", str(ctx.exception))
        self.assertIn("USB drive", str(ctx.exception))

    def test_read_status_merges_printer_job_and_file(self):
        merged = prusalink.read_status(self.ip, key=self.key)
        self.assertEqual(merged["state"], "PRINTING")
        self.assertEqual(merged["progress"], 41.5)
        self.assertEqual(merged["time_remaining"], 1800)
        self.assertEqual(merged["file_name"], "cube.bgcode")
        self.assertEqual(prusalink.job_phase(merged), "printing")

    def test_status_lines_speak_plainly(self):
        text = " ".join(prusalink.status_lines(
            prusalink.read_status(self.ip, key=self.key)))
        self.assertIn("The printer is printing.", text)
        self.assertIn("Current job: cube.bgcode.", text)
        self.assertIn("Progress: 42 percent.", text)
        self.assertIn("Time left: about", text)
        self.assertIn("Nozzle 215 degrees (target 215).", text)
        self.assertIn("Bed 60 degrees (target 60).", text)


class PhaseAndNameTests(unittest.TestCase):

    def test_phases(self):
        for state, phase in (("PRINTING", "printing"), ("PAUSED", "paused"),
                             ("BUSY", "busy"), ("ATTENTION", "attention"),
                             ("FINISHED", "finished"), ("STOPPED", "stopped"),
                             ("ERROR", "stopped"), ("IDLE", "idle"),
                             ("READY", "idle"), ("idle", "idle")):
            self.assertEqual(prusalink.job_phase({"state": state}), phase)
        self.assertEqual(prusalink.job_phase({}), "unknown")
        self.assertEqual(prusalink.job_phase({"state": "WEIRD"}), "unknown")
        for phase in ("finished", "stopped", "idle"):
            self.assertIn(phase, prusalink.DONE_PHASES)
        for phase in ("printing", "paused", "busy", "attention"):
            self.assertNotIn(phase, prusalink.DONE_PHASES)

    def test_safe_name_keeps_the_suffix_and_drops_odd_characters(self):
        name = prusalink.safe_gcode_name('bad\\na/me: "x"',
                                         Path("out/benchy.bgcode"))
        self.assertTrue(name.endswith(".bgcode"))
        for ch in '\\/:" ':
            self.assertNotIn(ch, name)
        self.assertTrue(prusalink.safe_gcode_name(
            "a", Path("x.gcode")).endswith(".gcode"))
        self.assertTrue(prusalink.safe_gcode_name("a").endswith(".bgcode"))

    def test_extract_gcode_is_a_plain_read(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        path = sandbox / "benchy.bgcode"
        path.write_bytes(b"GCDE-data")
        self.assertEqual(prusalink.extract_gcode(path),
                         ("benchy.bgcode", b"GCDE-data"))
        with self.assertRaises(ProjectError):
            prusalink.extract_gcode(sandbox / "missing.bgcode")


class SavedRecordTests(unittest.TestCase):
    """PrusaSlicer's physical printer record, under a sandboxed
    %APPDATA% - the real one is never read by a test."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(self.sandbox)
        self.addCleanup(self._restore)
        self.folder = self.sandbox / "PrusaSlicer" / "physical_printer"

    def _restore(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old

    def test_nothing_saved_means_empty_strings(self):
        self.assertEqual(prusalink.saved_device_address(), "")
        self.assertEqual(prusalink.saved_api_key(), "")

    def test_record_gives_address_and_key(self):
        self.folder.mkdir(parents=True)
        (self.folder / "Core One.ini").write_text(
            "host_type = prusalink\n"
            "print_host = http://127.0.0.1:8080/\n"
            "printhost_apikey = secret99\n"
            "preset_name = Prusa CORE One HF0.4 nozzle\n",
            encoding="utf-8")
        self.assertEqual(prusalink.saved_device_address(),
                         "127.0.0.1:8080")
        self.assertEqual(prusalink.saved_api_key(), "secret99")

    def test_octoprint_records_are_ignored(self):
        self.folder.mkdir(parents=True)
        (self.folder / "Other.ini").write_text(
            "host_type = octoprint\nprint_host = 127.0.0.2\n"
            "printhost_apikey = k\n", encoding="utf-8")
        self.assertEqual(prusalink.saved_device_address(), "")
        self.assertEqual(prusalink.saved_api_key(), "")


if __name__ == "__main__":
    unittest.main()
