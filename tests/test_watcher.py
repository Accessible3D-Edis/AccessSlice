"""Tests for watcher.py - noticing freshly saved projects.

The watcher polls once a second and waits for file sizes to settle, so
these tests genuinely take a few seconds each. They stay in the suite
because the watcher drives the whole guided flow.
"""

import shutil
import time
import unittest

import support
from watcher import FolderWatcher


def wait_for_found(watcher, timeout=10.0):
    """The found path, or None once the timeout passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _watching, found = watcher.status()
        if found:
            return found
        time.sleep(0.2)
    return None


class WatcherTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.watcher = FolderWatcher()
        self.addCleanup(self.watcher.stop)

    def test_a_file_saved_after_arming_is_found(self):
        self.watcher.arm(self.sandbox)
        (self.sandbox / "fresh-project.3mf").write_bytes(b"x" * 2048)
        found = wait_for_found(self.watcher)
        self.assertIsNotNone(found, "watcher never noticed the new file")
        self.assertTrue(found.endswith("fresh-project.3mf"))

    def test_pre_existing_files_do_not_count(self):
        (self.sandbox / "old-project.3mf").write_bytes(b"x" * 2048)
        self.watcher.arm(self.sandbox)
        self.assertIsNone(wait_for_found(self.watcher, timeout=3.5),
                          "a file present before arming was reported "
                          "as new")

    def test_backup_files_are_ignored(self):
        self.watcher.arm(self.sandbox)
        (self.sandbox / "job.pre-edit-backup.3mf").write_bytes(b"x" * 2048)
        self.assertIsNone(wait_for_found(self.watcher, timeout=3.5),
                          "a .pre-edit-backup.3mf was reported as a "
                          "new project")

    def test_resaving_an_existing_file_is_found(self):
        target = self.sandbox / "job.3mf"
        target.write_bytes(b"x" * 1024)
        # Arm with the file already present, then "save" it again later.
        self.watcher.arm(self.sandbox)
        time.sleep(1.5)   # ensure the new mtime clears the 0.5 s guard
        target.write_bytes(b"y" * 4096)
        found = wait_for_found(self.watcher)
        self.assertIsNotNone(found, "an updated save of an existing "
                                    "file was not noticed")


if __name__ == "__main__":
    unittest.main()
