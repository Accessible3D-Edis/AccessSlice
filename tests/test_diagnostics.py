"""Tests for diagnostics.py - the plain-text activity log.

The log path hangs off config_store.CONFIG_PATH, so pointing that into
the sandbox keeps these tests away from the real AppData folder.
"""

import shutil
import unittest

import support
import config_store
import diagnostics


class DiagnosticsTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self._old = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = self.sandbox / "config.json"
        self.addCleanup(setattr, config_store, "CONFIG_PATH", self._old)

    def test_log_appends_dated_lines_in_order(self):
        diagnostics.log("first event")
        diagnostics.log("second event")
        text = diagnostics.read_recent()
        self.assertIn("first event", text)
        self.assertIn("second event", text)
        self.assertLess(text.index("first event"),
                        text.index("second event"),
                        "entries must read oldest to newest")

    def test_log_exception_records_context_and_traceback(self):
        try:
            raise ValueError("the broken thing")
        except ValueError as e:
            diagnostics.log_exception("Rendering the Quality tab", e)
        text = diagnostics.read_recent()
        self.assertIn("UNEXPECTED ERROR - Rendering the Quality tab", text)
        self.assertIn("ValueError: the broken thing", text)
        self.assertIn("Traceback", text)

    def test_read_recent_without_a_log_is_empty(self):
        self.assertEqual(diagnostics.read_recent(), "")

    def test_clear_empties_the_log(self):
        diagnostics.log("something")
        diagnostics.clear()
        self.assertEqual(diagnostics.read_recent(), "")

    def test_rotation_keeps_the_file_bounded(self):
        line = "x" * 200
        for i in range(3000):
            diagnostics.log(f"{i} {line}")
        size = diagnostics.log_path().stat().st_size
        self.assertLess(size, diagnostics._MAX_BYTES + 100_000,
                        "log grew far past the rotation limit")
        text = diagnostics.read_recent(max_lines=10_000)
        self.assertIn("2999", text, "the newest entries must survive")

    def test_logging_never_raises_even_with_an_unwritable_path(self):
        config_store.CONFIG_PATH = (
            self.sandbox / "not-a-dir.txt" / "config.json")
        (self.sandbox / "not-a-dir.txt").write_text("a file, not a dir",
                                                    encoding="utf-8")
        diagnostics.log("this write must fail silently")
        diagnostics.clear()
        self.assertEqual(diagnostics.read_recent(), "")


if __name__ == "__main__":
    unittest.main()
