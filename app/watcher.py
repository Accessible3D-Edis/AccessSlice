"""watcher.py - notices when a project 3MF is saved into the watched folder.

Used by the guided import flow: AccessSlice opens the slicer, the user
imports a model and saves the project into their projects folder, and the
moment the file lands here we spot it, so the UI can open it automatically.

Implementation: simple polling (every second) of the folder's *.3mf files
by modification time. Polling is deliberate - it is stdlib-only, immune to
the half-written-file races filesystem events suffer from, and a 1-second
delay is imperceptible next to a human saving a file. Backup files the
editing engine writes (*.pre-edit-backup.3mf) are ignored.
"""

import threading
import time
from pathlib import Path


class FolderWatcher:
    """Watches one folder for new or updated .3mf files."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._found = None       # Path of the detected project, once seen
        self._folder = None

    # ------------------------------------------------------------- control

    def arm(self, folder):
        """Start watching. Snapshot current state first, so only files
        saved AFTER arming count as 'the new project'."""
        self.stop()
        self._folder = Path(folder)
        self._found = None
        self._stop.clear()
        baseline = self._snapshot()
        self._thread = threading.Thread(
            target=self._run, args=(baseline,), daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def status(self):
        """(watching: bool, found: str path or None)."""
        with self._lock:
            found = str(self._found) if self._found else None
        watching = self._thread is not None and self._thread.is_alive()
        return watching, found

    # ------------------------------------------------------------ internals

    def _snapshot(self):
        """{path: mtime} for every candidate 3MF currently in the folder."""
        result = {}
        try:
            for p in self._folder.glob("*.3mf"):
                if p.name.endswith(".pre-edit-backup.3mf"):
                    continue
                try:
                    result[p] = p.stat().st_mtime
                except OSError:
                    pass
        except OSError:
            pass
        return result

    def _run(self, baseline):
        while not self._stop.is_set():
            time.sleep(1.0)
            current = self._snapshot()
            for path, mtime in current.items():
                if path not in baseline or mtime > baseline[path] + 0.5:
                    # A new or freshly saved project. Wait until its size
                    # stops changing, so we never read a half-written zip.
                    if self._settled(path):
                        with self._lock:
                            self._found = path
                        return

    @staticmethod
    def _settled(path, checks=3, interval=0.4):
        """True once the file size is stable across several checks."""
        last = -1
        for _ in range(checks):
            try:
                size = path.stat().st_size
            except OSError:
                return False
            if size == last and size > 0:
                return True
            last = size
            time.sleep(interval)
        return True
