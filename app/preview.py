"""preview.py - the sliced preview's parse, once per sliced file.

Reading a full plate's G-code takes a few seconds (a Benchy: 930,000
lines, about 3 s; PrusaSlicer's binary format adds its decoding), so
the parse runs once in a background thread and both the page and the
drawing script read the finished result. The page refreshes itself
while the work runs - no JavaScript needed for that - and says what
went wrong if the file could not be read.

One entry is kept: the newest sliced file's chosen plate. A new
slice, or another plate, simply replaces it.
"""

import threading
import time

import toolpath

# Above this many segments the travel moves are left out of what is
# drawn (they double the payload and add little), and the page says so.
MAX_SEGMENTS_WITH_TRAVEL = 3_000_000


class PreviewCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.entry = None

    def ensure(self, output_path, plate, plate_size=None):
        """The entry for (file, plate): started if new, else as it is."""
        key = (str(output_path), str(plate))
        with self._lock:
            entry = self.entry
            if entry is not None and entry["key"] == key:
                return entry
            entry = {
                "key": key,
                "path": str(output_path),
                "plate": str(plate),
                "status": "working",
                "error": "",
                "facts": None,
                "payload": None,
                "started": time.monotonic(),
                "seconds": None,
            }
            self.entry = entry
        thread = threading.Thread(
            target=self._work, args=(entry, plate_size), daemon=True)
        thread.start()
        return entry

    @staticmethod
    def _work(entry, plate_size):
        try:
            tp = toolpath.read(entry["path"], entry["plate"])
            facts = toolpath.facts(tp, plate_size)
            with_travel = tp.segments <= MAX_SEGMENTS_WITH_TRAVEL
            facts["travel_drawn"] = with_travel
            payload = toolpath.payload(tp, include_travel=with_travel)
            entry["facts"] = facts
            entry["payload"] = payload
            entry["seconds"] = round(time.monotonic() - entry["started"], 1)
            entry["status"] = "ready" if tp.layers else "failed"
            if not tp.layers:
                entry["error"] = ("No layers were found in the G-code, so "
                                  "there is nothing to preview.")
        except Exception as exc:  # any read failure is reported in words
            entry["error"] = str(exc) or exc.__class__.__name__
            entry["status"] = "failed"

    def reset(self):
        with self._lock:
            self.entry = None

    def current(self, output_path, plate):
        """The entry when it is for this file and plate, else None."""
        entry = self.entry
        if entry and entry["key"] == (str(output_path), str(plate)):
            return entry
        return None
