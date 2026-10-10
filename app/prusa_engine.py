"""prusa_engine.py - runs PrusaSlicer's engine headless.

Facts this driver is built on (probed 2026-09-02 with PrusaSlicer
2.9.6 on a Core One project assembled from its own presets,
docs\\log.md):

- The engine is prusa-slicer-console.exe beside prusa-slicer.exe
  (the GUI program the Settings page points at). Command:
  `prusa-slicer-console.exe --export-gcode --output <name>
  project.3mf`, working directory = the output folder. A project 3MF
  that carries Metadata/Slic3r_PE.config is sliced with the settings
  inside it - no presets need naming. The Benchy took 22 s.
- stdout is a live progress log ("89 => Calculating overhanging
  perimeters" ...); the EXIT CODE is honest (0 on success), and a
  refusal is a plain sentence on its own line ("All objects are
  outside of the print volume.").
- With the Core One's binary_gcode = 1 the output is Prusa's BINARY
  G-code (magic "GCDE"): a header, then blocks, each a 2-byte type,
  2-byte compression, 4-byte uncompressed size (+ 4-byte compressed
  size when compressed), block-specific parameters (metadata blocks:
  a 2-byte encoding), the data, and a CRC32. Block types: 0 file
  metadata, 1 G-code, 2 slicer metadata, 3 printer metadata,
  4 print metadata, 5 thumbnail. Compression 0 none, 1 deflate,
  2 and 3 heatshrink (used for the G-code blocks only). The print
  metadata block (deflate) is INI text: "estimated printing time
  (normal mode)=1h 35m 19s", "filament used [g]=27.40", ...; the
  printer metadata block (plain) carries printer_model and
  objects_info (JSON with the object names). ASCII G-code, when
  binary_gcode is 0, carries the same facts as "; key = value"
  comment lines.

The job runner (thread, cancel, timeout, run folders, pruning) is
engine_driver.SliceJob unchanged - only the command, the output name
and the outcome judgement differ.
"""

import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

import engine_driver
from engine_driver import (CLI_LOG_NAME, KEEP_RUNS,  # noqa: F401
                           TIMEOUT_S, friendly_duration, prune_runs)
import prusa_format

CONSOLE_EXE = "prusa-slicer-console.exe"


def engine_exe(slicer_exe):
    """The console engine beside the GUI program; the GUI program
    itself when no console build exists (it also takes the same
    command line, showing nothing)."""
    exe = Path(slicer_exe)
    console = exe.with_name(CONSOLE_EXE)
    if exe.name.lower() != CONSOLE_EXE and console.is_file():
        return str(console)
    return str(exe)


def build_command(exe, project_path, plate, out_name):
    """The CLI invocation (split out so tests can stand in a fake).
    plate is ignored: PrusaSlicer projects have one bed."""
    return [engine_exe(exe), "--export-gcode", "--output", out_name,
            str(project_path)]


def wants_binary(project_path):
    """True when the project's printer setting asks for binary G-code
    (the Core One default). Unreadable -> True, the safer guess for a
    Prusa printer of this generation."""
    try:
        with zipfile.ZipFile(project_path) as z:
            text = z.read(prusa_format.CONFIG_ENTRY).decode(
                "utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return True
    m = re.search(r"^; binary_gcode = (.*)$", text, re.M)
    return (m.group(1).strip() != "0") if m else True


class SliceJob(engine_driver.SliceJob):
    """The proven job runner with PrusaSlicer's command and outcome
    judgement."""

    def _build_command(self):
        # The output name follows the project's own binary_gcode
        # choice; decided here (on the worker thread, before the run)
        # because the base class names the output in its constructor.
        suffix = ".bgcode" if wants_binary(self.project_path) else ".gcode"
        self.out_name = f"{self.project_path.stem}{suffix}"
        return build_command(self.exe, self.project_path, self.plate,
                             self.out_name)

    def _finish_from_result(self):
        rc = self._process.returncode if self._process else None
        out = self.output_file()
        if rc == 0 and out:
            try:
                report = parse_output(out)
            except Exception as e:
                report = _failure_report(rc, (
                    "The engine produced a file whose print "
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


# ------------------------------------------------------ the output


def parse_output(path):
    """A produced .bgcode or .gcode -> the report shape the report
    page renders (the same one engine_driver.parse_result builds)."""
    data = Path(path).read_bytes()
    if data[:4] == b"GCDE":
        meta = bgcode_metadata(data)
    else:
        meta = ascii_metadata(data)
    return report_from_metadata(meta)


def bgcode_metadata(data):
    """{key: value} merged from the file, printer and print metadata
    blocks of a binary G-code file. G-code and thumbnail blocks are
    skipped by size; heatshrink-compressed metadata (never seen from
    PrusaSlicer) is skipped honestly."""
    if data[:4] != b"GCDE":
        raise ValueError("not a binary G-code file")
    _version, checksum_type = struct.unpack("<IH", data[4:10])
    crc_len = 4 if checksum_type == 1 else 0
    pos = 10
    meta = {}
    while pos + 8 <= len(data):
        btype, comp, usize = struct.unpack("<HHI", data[pos:pos + 8])
        pos += 8
        csize = usize
        if comp != 0:
            (csize,) = struct.unpack("<I", data[pos:pos + 4])
            pos += 4
        params = {0: 2, 1: 2, 2: 2, 3: 2, 4: 2, 5: 6}.get(btype, 0)
        pos += params
        blob = data[pos:pos + csize]
        pos += csize + crc_len
        if btype not in (0, 2, 3, 4):
            continue
        if comp == 0:
            raw = blob
        elif comp == 1:
            raw = zlib.decompress(blob)
        else:
            continue
        for line in raw.decode("utf-8", "replace").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                meta.setdefault(key.strip(), value.strip())
    return meta


def ascii_metadata(data):
    """{key: value} from the "; key = value" comment lines of an ASCII
    G-code file (the header carries the model facts, the tail the
    print totals)."""
    meta = {}
    text = data.decode("utf-8", "replace")
    for m in re.finditer(r"^; ([^=\n]+?) = (.*)$", text, re.M):
        meta.setdefault(m.group(1).strip(), m.group(2).strip())
    return meta


_DURATION_RE = re.compile(r"(\d+)\s*([dhms])")


def parse_duration(text):
    """'1h 35m 19s' / '2d 3h' / '45s' -> seconds."""
    seconds = 0.0
    for num, unit in _DURATION_RE.findall(text or ""):
        seconds += float(num) * {"d": 86400, "h": 3600, "m": 60,
                                 "s": 1}[unit]
    return seconds


def _floats(text):
    out = []
    for part in str(text or "").split(","):
        try:
            out.append(float(part.strip()))
        except ValueError:
            continue
    return out


def report_from_metadata(meta):
    """The metadata dict -> the report shape. Prusa gives one bed, so
    one plate; no per-feature time breakdown exists in the output."""
    time_s = parse_duration(meta.get("estimated printing time (normal mode)"))
    per_filament = _floats(meta.get("filament used [g]"))
    total = _floats(meta.get("total filament used [g]"))
    filaments = [{"slot": i + 1, "grams": g}
                 for i, g in enumerate(per_filament)]
    objects = []
    try:
        info = json.loads(meta.get("objects_info") or "{}")
        for o in info.get("objects", []):
            name = str(o.get("name") or "").replace("\\", "/")
            objects.append({"name": name.rsplit("/", 1)[-1]
                            or "(unnamed object)", "triangles": None})
    except ValueError:
        pass
    warnings = []
    plate = {
        "id": "1",
        "time_s": time_s,
        "filaments": filaments,
        "features": [],
        "objects": objects,
        "warning": "",
    }
    return {
        "ok": True,
        "return_code": 0,
        "error_string": "",
        "plates": [plate],
        "total_time_s": time_s,
        "total_grams": (total[0] if total
                        else sum(f["grams"] for f in filaments)),
        "warnings": warnings,
        "printer_model": meta.get("printer_model", ""),
    }


# ---------------------------------------------------------- errors

_PROGRESS = re.compile(r"^\d+\s*=>")


def extract_error(log_path, rc):
    """The engine's own last meaningful words from the log: PrusaSlicer
    states a refusal as one plain sentence, after (or instead of) its
    "NN => stage" progress lines."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    meaningful = []
    for line in text.splitlines()[1:]:
        line = line.strip()
        if not line or _PROGRESS.match(line):
            continue
        if line.startswith("Slicing result exported") \
                or line.startswith("File exported"):
            continue
        meaningful.append(line)
    if meaningful:
        return meaningful[-1]
    return ("The engine stopped without an error message "
            + (f"(exit code {rc}). " if rc is not None else ". ")
            + "Its captured output is kept with the run.")
