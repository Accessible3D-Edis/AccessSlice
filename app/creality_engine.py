"""creality_engine.py - runs Creality Print's engine headless.

Facts this driver is built on (probed 2026-09-01 with Creality Print
7.2.2.5483 on Edis's Dodecagon plant cover project, docs\\log.md):

- Same CLI shape as Bambu: `--slice N --export-3mf out.gcode.3mf
  project.3mf`, relative output name, working directory = the output
  folder. A ~130 g model sliced in ~57 s.
- UNLIKE Bambu, stdout is a live log (each line level-tagged, oddly
  always [error], even progress lines) and the EXIT CODE means
  something: 0 on success; a refused slice ends with the real reason
  ("plate 1: Nothing to be sliced, Either the print is empty or no
  object is fully inside the print volume before apply.") followed by
  "Slic3r::CLI::run found error, exit" and a non-zero code.
- There is NO result.json. The report's numbers live INSIDE the
  produced .gcode.3mf: Metadata/slice_info.config carries, per plate,
  prediction (seconds), weight (grams), one <filament> per slot with
  used_g, the object names, and an "outside" flag.
- No per-feature time breakdown exists in the output, so Creality
  reports have no "where the time goes" section - stated honestly by
  the report page simply omitting it.

The job runner itself (thread, cancel, timeout, run folders, pruning)
is engine_driver.SliceJob unchanged - only the command builder and
the outcome judgement differ.
"""

import re
import xml.etree.ElementTree as ET
import zipfile

import engine_driver
from engine_driver import (CLI_LOG_NAME, KEEP_RUNS,  # noqa: F401
                           TIMEOUT_S, friendly_duration, prune_runs)


def build_command(exe, project_path, plate, out_name):
    """The CLI invocation (split out so tests can stand in a fake)."""
    return [str(exe), "--slice", str(plate),
            "--export-3mf", out_name, str(project_path)]


class SliceJob(engine_driver.SliceJob):
    """The proven job runner with Creality's outcome judgement."""

    def _build_command(self):
        return build_command(self.exe, self.project_path, self.plate,
                             self.out_name)

    def _finish_from_result(self):
        """Judge the run by exit code + the produced file's own
        slice information (there is no result.json on Creality)."""
        rc = self._process.returncode if self._process else None
        out = self.output_file()
        if rc == 0 and out:
            try:
                report = parse_sliced_3mf(out)
            except Exception as e:
                report = _failure_report(rc, (
                    "The engine produced a file whose slice "
                    f"information could not be read ({e})."))
            report["out_dir"] = str(self.out_dir)
            self._finish("done" if report["ok"] else "failed", report)
            return
        problem = extract_error(self.out_dir / CLI_LOG_NAME, rc)
        report = _failure_report(rc, problem)
        report["out_dir"] = str(self.out_dir)
        self._finish("failed", report)


def _failure_report(rc, problem):
    return {
        "ok": False,
        "return_code": rc,
        "error_string": problem,
        "plates": [],
        "total_time_s": 0.0,
        "total_grams": 0.0,
        "warnings": [],
    }


def parse_sliced_3mf(path):
    """Metadata/slice_info.config of a produced .gcode.3mf -> the
    same report shape engine_driver.parse_result builds, so the one
    report page renders both engines' results."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("Metadata/slice_info.config"))
    report = {
        "ok": True,
        "return_code": 0,
        "error_string": "",
        "plates": [],
        "total_time_s": 0.0,
        "total_grams": 0.0,
        "warnings": [],
    }
    for plate in root.iter("plate"):
        meta = {m.get("key"): m.get("value")
                for m in plate.findall("metadata")}
        pid = meta.get("index") or "1"
        time_s = float(meta.get("prediction") or 0.0)
        filaments = []
        for f in plate.findall("filament"):
            try:
                slot = int(f.get("id"))
            except (TypeError, ValueError):
                slot = f.get("id")
            filaments.append(
                {"slot": slot, "grams": float(f.get("used_g") or 0.0)})
        objects = [{"name": o.get("name") or f"object {o.get('id')}",
                    "triangles": None}
                   for o in plate.findall("object")]
        warning = ""
        if str(meta.get("outside", "")).lower() == "true":
            warning = ("Something on this plate ends outside the "
                       "printable area.")
        report["plates"].append({
            "id": pid,
            "time_s": time_s,
            "filaments": filaments,
            "features": [],   # Creality's output has no breakdown
            "objects": objects,
            "warning": warning,
        })
        report["total_time_s"] += time_s
        report["total_grams"] += sum(f["grams"] for f in filaments)
        if warning:
            report["warnings"].append(f"Plate {pid}: {warning}")
    return report


# Lines in the CLI log that carry no information about what went wrong.
_NOISE = re.compile(
    r"OpenGL probe|Initializing StaticPrintConfigs|"
    r"crealityprint_main start|Function took|^\s*$|"
    r"Slic3r::CLI::run found error")
# The "[stamp] [thread] [level]" prefix in front of every logged line.
_PREFIX = re.compile(r"^\[[^\]]*\]\s*\[[^\]]*\]\s*\[\w+\]\s*")


def extract_error(log_path, rc):
    """The engine's own last meaningful words from the stdout log."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    meaningful = []
    # The log's first line is the echoed command, never engine output.
    for line in text.splitlines()[1:]:
        line = _PREFIX.sub("", line).strip()
        if line and not _NOISE.search(line):
            meaningful.append(line)
    # The reason is logged right before the CLI gives up, so the last
    # meaningful line is the one to quote - but a crash (the upstream
    # segfault) says nothing at all.
    if meaningful and "start memory info" not in meaningful[-1]:
        return meaningful[-1]
    return ("The engine stopped without an error message "
            + (f"(exit code {rc}). " if rc is not None else ". ")
            + "Its captured output is kept with the run.")
