"""engine_driver.py - runs the slicer's engine headless (Bambu for now).

This is what turns AccessSlice from a companion into a slicer: the
user presses "Slice now", the slicer's own CLI does the work
invisibly, and the result comes back as a plain-language report.

Facts the driver is built on (probed 2026-08-30 and 2026-08-31 with
Bambu Studio 2.8.2, recorded in docs\\log.md):

- bambu-studio.exe is a GUI-subsystem binary: stdout is ALWAYS empty
  and the process exit code is meaningless (a failed run returned 127
  where result.json said -2). result.json in the working directory is
  the only truth - it is written on success AND failure, always with
  return_code and error_string, and with sliced_plates only when the
  slice worked.
- --export-3mf must be a RELATIVE filename with the working directory
  set to the output folder (an absolute path fails with -13 after an
  otherwise successful slice).
- --slice 0 slices every plate; --slice N slices plate N alone.
- A Benchy takes ~45 seconds; big plates take minutes.

Each run gets its own folder under `sliced` next to the project file
(the backups-folder pattern), holding result.json, the printable
.gcode.3mf, and the captured CLI output. The folder name says what and
when, so a folder listing reads like a history. Old runs are pruned,
newest KEEP_RUNS kept, exactly like backups.

Never act uninvited: nothing here runs except from the user pressing
"Slice now"; a job can be cancelled at any time and gives up on its
own after TIMEOUT_S.
"""

import datetime
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import noconsole

# One slice can legitimately take many minutes on a full plate; an
# hour means something is wrong.
TIMEOUT_S = 60 * 60
KEEP_RUNS = 10
RESULT_NAME = "result.json"
CLI_LOG_NAME = "cli-output.txt"


def build_command(exe, project_path, plate, out_name):
    """The CLI invocation. plate 0 = all plates, N = that plate only."""
    return [str(exe), "--slice", str(plate),
            "--export-3mf", out_name, str(project_path)]


class SliceJob:
    """One headless slice run, in a background thread.

    status is one of: running, done, failed, cancelled, timeout.
    report holds the parsed result once the run ends (also for
    failures - the engine's own error_string lives there).
    """

    def __init__(self, exe, project_path, plate=0, on_finish=None):
        self.exe = str(exe)
        self.project_path = Path(project_path)
        self.plate = int(plate)
        self.status = "running"
        self.report = None
        self._on_finish = on_finish
        self.started_at = datetime.datetime.now()
        self._t0 = time.monotonic()
        self._elapsed_final = None
        self._process = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()

        stamp = self.started_at.strftime("%Y-%m-%d %H.%M.%S")
        stem = self.project_path.stem
        self.out_dir = (self.project_path.parent / "sliced"
                        / f"{stamp} {stem}")
        self.out_name = f"{stem}.gcode.3mf"

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ queries

    def elapsed(self):
        """Whole seconds since the job started (frozen once it ends)."""
        if self._elapsed_final is not None:
            return self._elapsed_final
        return int(time.monotonic() - self._t0)

    def output_file(self):
        """Path of the printable .gcode.3mf, or None if never produced."""
        path = self.out_dir / self.out_name
        return path if path.is_file() else None

    def describe(self):
        """One plain-language line for the progress live region."""
        e = self.elapsed()
        if self.status == "running":
            return (f"Slicing... {e} second{'s' if e != 1 else ''} "
                    "elapsed.")
        if self.status == "done":
            return f"Slicing finished after {e} seconds."
        if self.status == "cancelled":
            return "Slicing was cancelled."
        if self.status == "timeout":
            return (f"Slicing gave up after {TIMEOUT_S // 60} minutes - "
                    "something is wrong; see the report.")
        problem = (self.report or {}).get("error_string") or \
            (self.report or {}).get("problem") or "unknown problem"
        return f"Slicing failed: {problem}"

    # ------------------------------------------------------------ control

    def cancel(self):
        """Stop the engine; the run folder keeps whatever was written."""
        self._cancel.set()
        with self._lock:
            proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    # ---------------------------------------------------------- internals

    def _build_command(self):
        """The CLI invocation. Reaches the module function at call
        time so tests' monkeypatching of build_command keeps working;
        another engine's SliceJob subclass overrides this."""
        return build_command(self.exe, self.project_path, self.plate,
                             self.out_name)

    def _run(self):
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            cmd = self._build_command()
            log_path = self.out_dir / CLI_LOG_NAME
            with open(log_path, "wb") as log:
                log.write((" ".join(cmd) + "\r\n\r\n").encode("utf-8"))
                log.flush()
                with self._lock:
                    self._process = subprocess.Popen(
                        cmd, cwd=str(self.out_dir),
                        stdout=log, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, **noconsole.hidden())
                try:
                    self._process.wait(timeout=TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=30)
                    self._finish("timeout", {
                        "problem": "The slicer did not finish within "
                                   f"{TIMEOUT_S // 60} minutes and was "
                                   "stopped."})
                    return
            if self._cancel.is_set():
                self._finish("cancelled", {
                    "problem": "Cancelled before the slicer finished."})
                return
            self._finish_from_result()
        except OSError as e:
            self._finish("failed", {
                "problem": f"The slicer engine could not be started "
                           f"({e}). Check the slicer path on the "
                           "Settings page."})

    def _finish_from_result(self):
        """Judge the run by result.json - the exit code lies (GUI app)."""
        result_path = self.out_dir / RESULT_NAME
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._finish("failed", {
                "problem": "The slicer engine ended without writing its "
                           "result file, so the outcome is unknown. Its "
                           f"captured output is in {self.out_dir}."})
            return
        report = parse_result(raw)
        report["out_dir"] = str(self.out_dir)
        if report["ok"] and not self.output_file():
            report["ok"] = False
            report["error_string"] = (
                "The engine reported success but the printable file is "
                "missing from the output folder.")
        self._finish("done" if report["ok"] else "failed", report)

    def wait(self, timeout=None):
        """Block until the job's thread has ended (used after cancel)."""
        self._thread.join(timeout)

    def _finish(self, status, report):
        self._elapsed_final = int(time.monotonic() - self._t0)
        self.report = report
        self.status = status
        prune_runs(self.project_path)
        if self._on_finish:
            try:
                self._on_finish(self)
            except Exception:
                pass  # a logging callback must never kill the worker


def parse_result(raw):
    """result.json -> one plain dict the report page renders from.

    Success: ok True, plates list (id, time_s, filaments with slot
    number and grams, feature times sorted biggest first, objects,
    warning), plus totals. Failure: ok False with the engine's own
    return_code and error_string quoted.
    """
    code = raw.get("return_code")
    report = {
        "ok": code == 0,
        "return_code": code,
        "error_string": str(raw.get("error_string") or ""),
        "plates": [],
        "total_time_s": 0.0,
        "total_grams": 0.0,
        "warnings": [],
    }
    for p in raw.get("sliced_plates") or []:
        features = sorted(
            ((name, float(secs)) for name, secs in
             (p.get("feature_type_times") or {}).items()
             if float(secs) >= 1.0),
            key=lambda kv: -kv[1])
        filaments = [{"slot": f.get("id"),
                      "grams": float(f.get("total_used_g") or 0.0)}
                     for f in (p.get("filaments") or [])]
        objects = [{"name": o.get("name") or f"object {o.get('id')}",
                    "triangles": o.get("triangle_count")}
                   for o in (p.get("objects") or [])]
        time_s = float(p.get("total_predication") or 0.0)
        warning = str(p.get("warning_message") or "").strip()
        report["plates"].append({
            "id": p.get("id"),
            "time_s": time_s,
            "filaments": filaments,
            "features": features,
            "objects": objects,
            "warning": warning,
        })
        report["total_time_s"] += time_s
        report["total_grams"] += sum(f["grams"] for f in filaments)
        if warning:
            report["warnings"].append(f"Plate {p.get('id')}: {warning}")
    return report


def friendly_duration(seconds):
    """9862 -> '2 hours 44 minutes'; always words, never a clock face."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    text = f"{hours} hour{'s' if hours != 1 else ''}"
    if minutes:
        text += f" {minutes} minute{'s' if minutes != 1 else ''}"
    return text


def prune_runs(project_path, keep=KEEP_RUNS):
    """Keep the newest run folders, exactly like the backups folder."""
    sliced = Path(project_path).parent / "sliced"
    try:
        runs = sorted((d for d in sliced.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)
    except OSError:
        return
    for old in runs[keep:]:
        shutil.rmtree(old, ignore_errors=True)
