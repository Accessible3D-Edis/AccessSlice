"""prusalink.py - talk to a Prusa printer (the Core One) over PrusaLink.

The no-slicer-window send path for the Prusa backend, built
2026-09-07 after Edis's first real Prusa run hit the dead end at the
final step: "Open the sliced file in PrusaSlicer" opens the G-code
VIEWER, which can look but never send. PrusaLink is the printer's
own HTTP server on the local network - the same thing PrusaSlicer's
Ctrl+Shift+G talks to - and it needs two facts: the printer's
address and its PrusaLink password (PrusaSlicer calls it the API
key). Both are on the printer's screen under Settings > Network >
PrusaLink, and both are remembered in AccessSlice's config (never
in the repository) once typed.

Protocol (the PrusaLink OpenAPI spec, prusa3d/Prusa-Link-Web
spec/openapi.yaml, read 2026-09-07; the same calls PrusaSlicer's
own PrusaLink host class makes):

- Auth: the X-Api-Key header carries the password; the printers
  also accept HTTP digest auth with user "maker" and that password,
  so both are offered - digest as the automatic fallback on a 401.
- Upload:  PUT http://<ip>/api/v1/files/usb/<name> with the raw
  bytes, Content-Type application/octet-stream, Overwrite: ?1 and
  Print-After-Upload: ?0 (RFC 8941 booleans). 201 or 204 on success.
- Start:   POST http://<ip>/api/v1/files/usb/<name> (no body)
  starts the print if no job runs; 204 on success, 409 when the
  printer is busy.
- Status:  GET /api/v1/status -> {"printer": {state, temp_nozzle,
  target_nozzle, temp_bed, target_bed, ...}, "job": {id, progress,
  time_remaining, time_printing}}; the printer state is one of
  IDLE, BUSY, PRINTING, PAUSED, FINISHED, STOPPED, ERROR, ATTENTION,
  READY. GET /api/v1/job adds the running file's name (404 when
  nothing runs).
- Version: GET /api/version -> {"api", "version", "printer",
  "text", "firmware", "capabilities": {"upload-by-put": bool}}.

- Job:     PUT /api/v1/job/<id>/pause and /resume, DELETE
  /api/v1/job/<id> stops it. The spec's "continue" call does not
  exist in the Core One firmware (6.8.1 answers 400, checked
  2026-09-09 in the Prusa Accessible Control program): no prompt on
  the printer's screen can be answered over PrusaLink - the knob on
  the printer is the answer.
- Files:   GET /api/v1/files/usb/<path> lists a folder (children:
  name, display_name, type FOLDER or PRINT_FILE, size, m_timestamp);
  PUT with a trailing slash makes a folder; DELETE removes a file.
- Info:    GET /api/v1/info -> name, location, serial, hostname,
  nozzle_diameter, min_extrusion_temp.

The upload goes to the printer's USB drive - the Core One keeps
its files there, so a drive must be plugged into the printer.

The address that is remembered (2026-09-21): a home router hands
the printer its address by DHCP, and the lease changes - the Core
One moved from one address to another between 2026-09-09 and
2026-09-21, and every send timed out at the old one with nothing
to say but "check the address". The printer also announces its own
name on the network (mDNS, <hostname>.local; the hostname is in
the info block and on the printer's screen), and Windows resolves
that name. So once a numeric address has worked, stable_address
asks the printer for its name and remembers the name instead when
it resolves to the same address; the name follows the printer to
its next address. A name the user typed is kept as typed.

The job and file calls arrived 2026-09-12 when the standalone Prusa
Accessible Control program's printer control was merged into
AccessSlice (printer_control.py runs command jobs through them).

Everything stdlib-only, like the rest of AccessSlice. Nothing here
runs except from the user's explicit button press, and the server
route demands the plate-empty confirmation first.
"""

import configparser
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from errors import ProjectError

# Tells the server this printer wants a password (API key) besides an
# address; the Creality module has no such thing.
NEEDS_KEY = True
KEY_LABEL = "PrusaLink password (API key)"
KEY_HELP = ("On the printer: Settings, Network, PrusaLink - the "
            "password shown there, eight characters. It is remembered "
            "here after the first send.")
DIGEST_USER = "maker"

# Printer state words -> the phases the monitoring page reasons
# about. ATTENTION (the printer waits for a person, e.g. a filament
# change) and BUSY keep the page watching; IDLE and READY are quiet.
_PHASES = {
    "PRINTING": "printing", "PAUSED": "paused", "BUSY": "busy",
    "ATTENTION": "attention", "FINISHED": "finished",
    "STOPPED": "stopped", "ERROR": "stopped", "IDLE": "idle",
    "READY": "idle",
}
DONE_PHASES = ("finished", "stopped", "idle")

STATE_WORDS = {
    "IDLE": "idle", "READY": "ready to print", "BUSY": "busy",
    "PRINTING": "printing", "PAUSED": "paused", "FINISHED":
    "finished with its print", "STOPPED": "stopped", "ERROR": "in an "
    "error state - see its screen", "ATTENTION": "waiting for you at "
    "its screen",
}


def job_phase(merged):
    """'printing' | 'paused' | 'busy' | 'attention' | 'finished' |
    'stopped' | 'idle' | 'unknown' for a merged status dict."""
    return _PHASES.get(str(merged.get("state") or "").upper(), "unknown")


# --------------------------------------------- PrusaSlicer's records


def _physical_printers():
    """PrusaSlicer's saved physical printers as parsed INI sections,
    newest file first. %APPDATA%\\PrusaSlicer\\physical_printer\\*.ini,
    one printer per file: print_host, printhost_apikey, host_type."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    folder = Path(appdata) / "PrusaSlicer" / "physical_printer"
    if not folder.is_dir():
        return []
    found = []
    try:
        files = sorted(folder.glob("*.ini"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for path in files:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string("[printer]\n"
                               + path.read_text(encoding="utf-8"))
        except (OSError, configparser.Error, UnicodeDecodeError):
            continue
        section = dict(parser["printer"])
        if section.get("host_type", "prusalink").strip() != "prusalink":
            continue
        found.append(section)
    return found


def saved_device_address():
    """The printer's address from PrusaSlicer's own physical printer
    records, so nobody has to read it off the printer's screen when
    PrusaSlicer already knows it. "" when there is none."""
    for printer in _physical_printers():
        host = printer.get("print_host", "").strip()
        if host:
            return re.sub(r"^https?://", "", host).rstrip("/")
    return ""


def saved_api_key():
    """The password PrusaSlicer stored for that printer, or ""."""
    for printer in _physical_printers():
        key = printer.get("printhost_apikey", "").strip()
        if key:
            return key
    return ""


# ------------------------------------------------------ the calls


def safe_gcode_name(stem, out=None):
    """A FAT-and-URL-safe file name for the printer's USB drive,
    keeping the produced file's own suffix (.bgcode or .gcode)."""
    suffix = ".bgcode"
    if out is not None:
        name = str(out)
        suffix = ".bgcode" if name.endswith(".bgcode") else ".gcode"
    stem = re.sub(r"[^A-Za-z0-9_().-]+", "_", stem).strip("_") or "print"
    return f"{stem}_{time.strftime('%Y-%m-%d_%H-%M')}{suffix}"


def extract_gcode(out):
    """(name, bytes) of the produced file. PrusaSlicer's output IS the
    printable file (no zip around it), so this is a plain read - the
    name matches the Creality module's interface."""
    try:
        return Path(out).name, Path(out).read_bytes()
    except OSError as e:
        raise ProjectError(f"The sliced file could not be read: {e}")


def _opener(ip, key):
    """An opener that retries with digest auth (maker / key) when the
    printer answers 401 to the X-Api-Key header."""
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, f"http://{ip}/", DIGEST_USER, key or "")
    return urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(manager))


def _call(ip, key, method, path, data=None, headers=None, timeout=20,
          busy_message=None):
    """One request to the printer; the parsed JSON body (or {}) on
    success, ProjectError in plain words otherwise. busy_message:
    the words for a 409 (the printer refused because of its state);
    the default is the send's."""
    if not ip:
        raise ProjectError("The printer's address is missing.")
    url = f"http://{ip}{path}"
    request = urllib.request.Request(url, data=data, method=method)
    if key:
        request.add_header("X-Api-Key", key)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with _opener(ip, key).open(request, timeout=timeout) as r:
            body = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace").strip()
            parsed = json.loads(detail)
            detail = str(parsed.get("message") or parsed.get("error")
                         or detail)
        except (ValueError, AttributeError, OSError):
            pass
        if e.code in (401, 403):
            raise ProjectError(
                "The printer refused the PrusaLink password. Check it "
                "on the printer's screen (Settings, Network, "
                "PrusaLink) and type it again. Nothing was sent.")
        if e.code == 409:
            raise Busy(
                (busy_message or
                 "The printer is busy - it is printing, paused, or "
                 "waiting at its screen - so it did not start the new "
                 "job. The file is on its USB drive; start it from the "
                 "printer once it is free.")
                + (f" ({detail})" if detail else ""))
        if e.code == 404:
            raise NotFound(f"The printer at {ip} has nothing at {path}.")
        raise ProjectError(
            f"The printer at {ip} answered HTTP {e.code}"
            + (f": {detail}" if detail else "") + ".")
    except OSError as e:
        raise ProjectError(_unreachable_words(ip, e))
    if status not in (200, 201, 204):
        raise ProjectError(f"The printer answered HTTP {status}.")
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return {}


def _split_port(address):
    """(host, ":port" or "") of an address that may carry a port."""
    host, sep, port = address.rpartition(":")
    if sep and host and port.isdigit():
        return host, ":" + port
    return address, ""


def _is_numeric(host):
    """True for a plain IP address (not a name)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _resolve(name):
    """The address a network name resolves to, "" when nothing on
    the network answers to it. Patched in the tests."""
    try:
        return socket.gethostbyname(name)
    except OSError:
        return ""


def _unreachable_words(ip, error):
    """Why the printer could not be reached, with what to do: a
    numeric address may simply have changed (the router hands out
    a new one now and then), and a name nobody answers to is said
    as that."""
    host, _port = _split_port(ip)
    reason = getattr(error, "reason", error)
    if not _is_numeric(host) and isinstance(reason, socket.gaierror):
        return (f"Nothing on this network answers to the name {host}. "
                "Check that the printer is on and on the same network "
                "as this PC; its name is on its screen under Settings, "
                "Network.")
    words = (f"Could not reach the printer at {ip}: {error}. Check that "
             "the printer is on and on the same network, and that the "
             "address is right.")
    if _is_numeric(host):
        words += (" A printer's address can change when your router "
                  "hands out a new one - then type the printer's NAME "
                  "instead (its hostname on the screen under Settings, "
                  "Network, followed by .local - for example "
                  "prusa-core-one.local); AccessSlice finds it by "
                  "name from then on.")
    return words


def stable_address(ip, key="", timeout=10):
    """The address to remember for a printer that just answered at
    ip: the printer's own network name (<hostname>.local, from its
    info block) when that name resolves to the same address, else
    ip as it is. A name the user typed is kept; a port travels
    along. Never raises - a printer that will not say its name is
    remembered by the address that worked."""
    host, port = _split_port(ip)
    if not _is_numeric(host):
        return ip
    try:
        info = read_info(ip, key, timeout=timeout)
    except ProjectError:
        return ip
    name = str(info.get("hostname") or "").strip().strip(".").lower()
    if not name or not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name):
        return ip
    candidate = name + ".local"
    if _resolve(candidate) == host:
        return candidate + port
    return ip


class Busy(ProjectError):
    """The printer answered 409: its state forbids the call."""


class NotFound(ProjectError):
    """The printer answered 404: no such file, folder or job. A
    ProjectError, so an unhandled one is still said in words."""


_NotFound = NotFound


STORAGE = "usb"


def _file_path(path):
    """The API path of a file or folder on the USB drive; a
    trailing slash (a folder) survives the quoting."""
    return ("/api/v1/files/" + STORAGE + "/"
            + urllib.parse.quote(path.strip("/"), safe="/")
            + ("/" if path.endswith("/") else ""))


def check_printer(ip, key, timeout=10):
    """The printer's version facts (GET /api/version). Raises
    ProjectError when it is not a PrusaLink printer."""
    info = _call(ip, key, "GET", "/api/version", timeout=timeout)
    if not isinstance(info, dict) or "api" not in info:
        raise ProjectError(
            f"The device at {ip} answered, but not as a PrusaLink "
            "printer. Nothing was sent.")
    return info


def upload_gcode(ip, filename, data, key="", timeout=600):
    """PUT the printable file onto the printer's USB drive."""
    _call(ip, key, "PUT", _file_path(filename),
          data=data, timeout=timeout, headers={
              "Content-Type": "application/octet-stream",
              "Content-Length": str(len(data)),
              "Overwrite": "?1",
              "Print-After-Upload": "?0",
          })


def start_print(ip, filename, key="", timeout=20):
    """Ask the printer to print the file just uploaded."""
    _call(ip, key, "POST", _file_path(filename),
          data=b"", timeout=timeout,
          headers={"Content-Length": "0"})


# ------------------------------------------- control: jobs and files
#
# The calls printer_control.py runs its command jobs through, and
# the file page's. Every one raises ProjectError in words; Busy and
# NotFound where the caller can do something about it.

CAN_CONTROL = True    # printer_control.py may run command jobs here
IDLE_STATES = ("IDLE", "READY", "FINISHED", "STOPPED")
ACTIVE_STATES = ("PRINTING", "PAUSED", "ATTENTION", "BUSY")
END_STATES = ("FINISHED", "STOPPED", "ERROR", "IDLE", "READY")


def read_info(ip, key="", timeout=10):
    """The printer's info block (name, serial, hostname, nozzle)."""
    info = _call(ip, key, "GET", "/api/v1/info", timeout=timeout)
    return info if isinstance(info, dict) else {}


def read_job(ip, key="", timeout=10):
    """The running job's block (id, state, progress, file), or None
    when nothing runs."""
    try:
        job = _call(ip, key, "GET", "/api/v1/job", timeout=timeout)
    except NotFound:
        return None
    return job if isinstance(job, dict) and job else None


def list_files(ip, path="", key="", timeout=20):
    """The entries of a folder on the USB drive, folders first,
    each a dict with name, display_name, type (FOLDER or
    PRINT_FILE), size and m_timestamp as the printer gives them."""
    listing = _call(ip, key, "GET", _file_path(path + "/" if path else ""),
                    timeout=timeout)
    children = (listing or {}).get("children") or []
    entries = [dict(c) for c in children if isinstance(c, dict)]
    entries.sort(key=lambda c: (c.get("type") != "FOLDER",
                                str(c.get("display_name")
                                    or c.get("name") or "").lower()))
    return entries


def create_folder(ip, path, key="", timeout=20):
    """Make a folder on the USB drive; one that exists is fine."""
    try:
        _call(ip, key, "PUT", _file_path(path.rstrip("/") + "/"),
              data=b"", timeout=timeout,
              headers={"Content-Length": "0", "Overwrite": "?1"},
              busy_message="The printer refused to make the folder "
                           f"{path}.")
    except Busy:
        pass


def delete_file(ip, path, key="", force=False, timeout=20):
    """Remove a file (or an empty folder) from the USB drive."""
    _call(ip, key, "DELETE", _file_path(path), timeout=timeout,
          headers={"Force": "?1" if force else "?0"},
          busy_message=f"The printer refused to delete {path} - it is "
                       "printing it, or otherwise busy.")


def pause_job(ip, job_id, key="", timeout=20):
    _call(ip, key, "PUT", f"/api/v1/job/{int(job_id)}/pause", data=b"",
          timeout=timeout, headers={"Content-Length": "0"},
          busy_message="The printer would not pause - it is not "
                       "printing right now.")


def resume_job(ip, job_id, key="", timeout=20):
    _call(ip, key, "PUT", f"/api/v1/job/{int(job_id)}/resume", data=b"",
          timeout=timeout, headers={"Content-Length": "0"},
          busy_message="The printer would not resume - it is not "
                       "paused, or a question on its screen is "
                       "waiting for the knob.")


def stop_job(ip, job_id, key="", timeout=20):
    _call(ip, key, "DELETE", f"/api/v1/job/{int(job_id)}", timeout=timeout,
          busy_message="The printer would not stop the job.")


def read_status(ip, key="", timeout=10):
    """One flat status dict: the printer block's fields plus the
    job's progress and, when a job runs, its file name."""
    status = _call(ip, key, "GET", "/api/v1/status", timeout=timeout)
    if not isinstance(status, dict) or "printer" not in status:
        raise ProjectError(
            f"The printer at {ip} answered, but not with a PrusaLink "
            "status. Try again in a moment.")
    merged = dict(status.get("printer") or {})
    job = status.get("job") or {}
    for field in ("progress", "time_remaining", "time_printing"):
        if field in job:
            merged[field] = job[field]
    if job.get("id") is not None:
        merged["job_id"] = job["id"]
    if job:
        try:
            detail = _call(ip, key, "GET", "/api/v1/job", timeout=timeout)
        except _NotFound:
            detail = {}
        except ProjectError:
            detail = {}
        file_info = (detail or {}).get("file") or {}
        name = file_info.get("display_name") or file_info.get("name")
        if name:
            merged["file_name"] = str(name)
    return merged


def _num(value):
    try:
        return f"{float(value):.0f}"
    except (TypeError, ValueError):
        return str(value)


def _pos(value):
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def position(merged):
    """{"x", "y", "z"} floats from the status's axis fields (the
    Core One reports axis_x, axis_y, axis_z in millimetres), or {}
    for the ones it left out. motion_guard reasons from this before
    every move."""
    found = {}
    for axis in ("x", "y", "z"):
        value = merged.get(f"axis_{axis}")
        if value is None:
            continue
        try:
            found[axis] = float(value)
        except (TypeError, ValueError):
            continue
    return found


def _at_target(temp, target):
    """True when a heater with a target above zero is within three
    degrees of it - the "heating done" moment."""
    try:
        temp, target = float(temp), float(target)
    except (TypeError, ValueError):
        return False
    return target > 0 and abs(temp - target) <= 3.0


def status_lines(merged):
    """The status dict as plain-language lines for the status page."""
    lines = []
    state = str(merged.get("state") or "").upper()
    if state:
        lines.append("The printer is "
                     + STATE_WORDS.get(state, f"in state {state}") + ".")
    name = merged.get("file_name")
    if name:
        lines.append(f"Current job: {name}.")
    if merged.get("progress") is not None:
        lines.append(f"Progress: {_num(merged['progress'])} percent.")
    left = merged.get("time_remaining")
    if left:
        try:
            from engine_driver import friendly_duration
            lines.append("Time left: about "
                         f"{friendly_duration(float(left))}.")
        except (TypeError, ValueError):
            pass
    nozzle = merged.get("temp_nozzle")
    nozzle_t = merged.get("target_nozzle")
    if nozzle is not None:
        lines.append(f"Nozzle {_num(nozzle)} degrees"
                     + (f" (target {_num(nozzle_t)})"
                        if nozzle_t not in (None, 0, "0") else "")
                     + ".")
    bed = merged.get("temp_bed")
    bed_t = merged.get("target_bed")
    if bed is not None:
        lines.append(f"Bed {_num(bed)} degrees"
                     + (f" (target {_num(bed_t)})"
                        if bed_t not in (None, 0, "0") else "") + ".")
    chamber = merged.get("temp_chamber")
    chamber_t = merged.get("target_chamber")
    if chamber is not None:
        lines.append(f"Chamber {_num(chamber)} degrees"
                     + (f" (target {_num(chamber_t)})"
                        if chamber_t not in (None, 0, "0") else "")
                     + ".")
    reached = [label for label, temp, target in (
        ("nozzle", nozzle, nozzle_t), ("bed", bed, bed_t),
        ("chamber", chamber, chamber_t)) if _at_target(temp, target)]
    if reached:
        lines.append("Heating done: the " + " and ".join(reached)
                     + (" are" if len(reached) > 1 else " is")
                     + " at target.")
    if merged.get("axis_z") is not None:
        where = [f"{axis.upper()} {_pos(merged[key])}"
                 for axis, key in (("x", "axis_x"), ("y", "axis_y"),
                                   ("z", "axis_z"))
                 if merged.get(key) is not None]
        lines.append("Head at " + ", ".join(where) + ".")
    speed, flow = merged.get("speed"), merged.get("flow")
    if speed is not None and flow is not None and (
            _num(speed) != "100" or _num(flow) != "100"):
        lines.append(f"Speed {_num(speed)} percent, flow {_num(flow)} "
                     "percent.")
    message = None
    if isinstance(merged.get("status_printer"), dict):
        message = merged["status_printer"].get("message")
    if message and str(message).strip().upper() != "OK":
        lines.append(f"The printer says: {str(message).strip()}")
    if not lines:
        lines.append("The printer answered, but its status held none "
                     "of the usual fields. Raw keys: "
                     + ", ".join(sorted(merged)[:12]) + ".")
    return lines
