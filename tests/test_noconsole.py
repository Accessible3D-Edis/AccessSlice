"""Helper programs run without a terminal window of their own.

AccessSlice is built without a console, so on Windows every
console-subsystem helper it starts (prusa-slicer-console.exe,
tasklist, powershell) would otherwise open a black window in front
of the program with its output scrolling in it - seen by Edis on
2026-09-24. These tests pin the flag and, by reading the source,
that every launch site passes it - so a new subprocess call cannot
quietly bring the window back.
"""

import ast
import subprocess
import sys
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import noconsole  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app"

# The two programs the user is meant to see: the slicer itself and
# File Explorer. Everything else is a helper and must be windowless.
VISIBLE_LAUNCHES = {
    ("server.py", "launch_slicer"),
    ("usbcopy.py", "reveal"),
}


def _launch_calls(path):
    """(enclosing function name, call node) for every subprocess.run /
    subprocess.Popen call in the file."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a file's own escape warnings
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "subprocess"
                    and sub.func.attr in ("run", "Popen", "call",
                                          "check_output", "check_call")):
                found.append((node.name, sub))
    return found


def _passes_hidden(call):
    for kw in call.keywords:
        if (kw.arg is None and isinstance(kw.value, ast.Call)
                and ast.unparse(kw.value.func) == "noconsole.hidden"):
            return True
    return False


class HiddenFlagTests(unittest.TestCase):

    def test_windows_gets_the_no_window_flag(self):
        if sys.platform != "win32":
            self.skipTest("Windows-only flag")
        self.assertEqual(noconsole.hidden(),
                         {"creationflags": subprocess.CREATE_NO_WINDOW})

    def test_the_flag_is_accepted_by_a_real_launch(self):
        if sys.platform != "win32":
            self.skipTest("Windows-only flag")
        out = subprocess.run(
            ["cmd", "/c", "echo", "quiet"], capture_output=True, text=True,
            timeout=30, **noconsole.hidden())
        self.assertEqual(out.stdout.strip(), "quiet")

    def test_other_platforms_get_nothing(self):
        real = sys.platform
        try:
            sys.platform = "linux"
            self.assertEqual(noconsole.hidden(), {})
        finally:
            sys.platform = real


class EveryLaunchSiteTests(unittest.TestCase):

    def test_every_helper_launch_is_windowless(self):
        missing = []
        seen_visible = set()
        for path in sorted(APP.rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "noconsole.py":
                continue
            for func, call in _launch_calls(path):
                key = (path.name, func)
                if key in VISIBLE_LAUNCHES:
                    seen_visible.add(key)
                    continue
                if not _passes_hidden(call):
                    missing.append(f"{path.relative_to(APP)}:{call.lineno} "
                                   f"in {func}")
        self.assertEqual(missing, [],
                         "helper launches without noconsole.hidden()")
        # The allow-list must stay honest: an entry that no longer
        # launches anything should be removed, not left to hide a
        # future call.
        self.assertEqual(seen_visible, VISIBLE_LAUNCHES)

    def test_the_sites_that_matter_are_covered(self):
        """The launches that showed the window: the PrusaSlicer engine
        (engine_driver, and build_job for every new-job build), the
        running-check (tasklist) and the shortcut resolver
        (powershell)."""
        expected = {
            ("engine_driver.py", "_run"),
            ("build_job.py", "run"),
            ("config_store.py", "resolve_shortcut"),
            ("studio_config.py", "studio_running"),
            ("prusa.py", "slicer_running"),
        }
        covered = set()
        for path in APP.rglob("*.py"):
            for func, call in _launch_calls(path):
                if _passes_hidden(call):
                    covered.add((path.name, func))
        self.assertTrue(expected <= covered, expected - covered)


if __name__ == "__main__":
    unittest.main()
