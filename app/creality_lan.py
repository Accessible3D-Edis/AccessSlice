"""creality_lan.py - talk to a stock Creality K-series printer over LAN.

The send path that needs no slicer GUI at all: the unrooted K1/K2
family accepts gcode by plain HTTP and is driven and observed over a
JSON WebSocket. Protocol facts (community-verified: the ha_creality_ws
Home Assistant integration and OrcaSlicer issue 2103; port and HTTP
server confirmed live on Edis's K2 Plus, 2026-09-01):

- Upload:  POST http://<ip>/upload/<name>.gcode as multipart form
  data, one field named "file".
- Control: ws://<ip>:9999/ - plain JSON text frames.
  - The printer pushes state objects; keys seen on the K family:
    state (0 idle, 1 printing, 3 finished, 4 stopped, 5 paused),
    deviceState, printProgress, printJobTime, printLeftTime, layer,
    TotalLayer, nozzleTemp, targetNozzleTemp, bedTemp0,
    targetBedTemp0, boxTemp, printFileName.
  - {"ModeCode": "heart_beat"} frames are answered with the literal
    text "ok".
  - Ask for a state push: {"method":"get","params":{"ReqPrinterPara":1}}
  - Start a print of an uploaded file:
    {"method":"set","params":{"opGcodeFile":
     "printprt:<path the printer lists for the upload>"}}
    (K1: /usr/data/printer_data/gcodes/<name>; K2 Plus:
    /mnt/UDISK/printer_data/gcodes/<name>); the list itself comes
    from {"method":"get","params":{"reqGcodeFile":1}} as
    retGcodeFileInfo2 entries with name, path, size, timeCost

Everything stdlib-only, like the rest of AccessSlice - including the
small RFC 6455 WebSocket client below (text frames only, client-side
masking as the RFC requires).

Safety: nothing here runs except from the user's explicit button
press, and the server route demands the plate-empty confirmation
first - the same question Creality Print asks, in accessible form.
"""

import base64
import json
import os
import re
import socket
import struct
import time
import urllib.request
import urllib.parse
import zipfile
from pathlib import Path

from errors import ProjectError

WS_PORT = 9999
# Where uploads land. The K1 family keeps them under /usr/data; the
# K2 Plus under /mnt/UDISK (seen live 2026-09-08: a start request
# naming the /usr/data path was silently ignored, and the printer's
# own file list showed the upload under /mnt/UDISK). start_print
# therefore asks the printer for the uploaded file's real path first
# and only falls back to these guesses.
GCODES_DIR = "/usr/data/printer_data/gcodes"
GCODES_DIRS = ("/mnt/UDISK/printer_data/gcodes", GCODES_DIR)

# state codes, per the community integrations; unknown codes are
# shown as their number rather than guessed at. Code 0 SEEN LIVE
# 2026-09-15 on the K2 Plus: cold, no target, no error, a week after
# its last job - the plain idle state (the community's "busy" reading
# was wrong), with the last job's name, progress and time left still
# in the status until the next job starts.
STATE_WORDS = {
    0: "idle",
    1: "printing",
    2: "finished",
    3: ("idle after finishing its last print (the finished job stays "
        "shown until the next one starts)"),
    4: "stopped",
    5: "paused",
}

# The same codes as the phases the monitoring page reasons about.
# Only code 1 has been seen live (the K2 Plus mid-print, 2026-09-01);
# the rest follow the community integrations and are marked as such
# in docs\log.md. A code outside the table is "unknown", never a
# guess - the page keeps polling in that case.
# Code 3 SEEN LIVE 2026-09-08 on the K2 Plus: the previous job at
# 100 % with its file name still shown, nozzle cold, no error, and
# unchanged after OK was pressed on the screen - the printer's idle
# state after a completed print. A new job starts from it normally.
# Code 0 SEEN LIVE 2026-09-15: idle (see STATE_WORDS). Nothing maps
# to "busy" any more; a routine in progress (homing, feeding) shows
# as deviceState 7, which printer_control watches separately.
_PHASES = {0: "idle", 1: "printing", 2: "finished", 3: "finished",
           4: "stopped", 5: "paused"}
DONE_PHASES = ("finished", "stopped", "idle")
# Phases in which the job fields describe the LAST job, not one
# under way.
_OVER_PHASES = ("idle", "finished", "stopped")


def job_phase(merged):
    """'printing' | 'paused' | 'idle' | 'finished' | 'stopped' |
    'unknown' for a merged status dict."""
    state = merged.get("state", merged.get("deviceState"))
    try:
        return _PHASES.get(int(state), "unknown")
    except (TypeError, ValueError):
        return "unknown"


# ------------------------------------------------ WebSocket client


class _WebSocket:
    """Just enough RFC 6455 for this printer: text frames, masking,
    ping/pong, close. Raises ProjectError in plain words."""

    def __init__(self, ip, port=WS_PORT, timeout=8):
        # "host:port" overrides the standard port - how the hermetic
        # test suite points at its fake printer on 127.0.0.1.
        host, _, explicit = str(ip).partition(":")
        if explicit.isdigit():
            ip, port = host, int(explicit)
        self.sock = None
        try:
            self.sock = socket.create_connection((ip, port), timeout)
            self.sock.settimeout(timeout)
        except OSError as e:
            raise ProjectError(
                f"Could not reach the printer at {ip} (port {port}): "
                f"{e}. Check that the printer is on and on the same "
                "network, and that the IP address is right.")
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (f"GET / HTTP/1.1\r\nHost: {ip}:{port}\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\n"
                     "Sec-WebSocket-Version: 13\r\n\r\n")
        try:
            self.sock.sendall(handshake.encode("ascii"))
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        except OSError as e:
            self.close()
            raise ProjectError(
                f"The printer at {ip} did not answer the connection "
                f"handshake ({e}).")
        if b" 101" not in response.split(b"\r\n", 1)[0]:
            self.close()
            raise ProjectError(
                f"The printer at {ip} refused the control connection "
                "- it may not be a Creality K-series printer, or its "
                "firmware speaks differently. Nothing was sent.")
        # Whatever followed the handshake reply in the same packet is
        # already the first frame - the printer pushes its heartbeat
        # and status the moment the socket opens. Dropping it lost the
        # heartbeat (and its "ok") about half the time, seen as the
        # ack test flaking on 2026-09-02.
        self._buffer = response.split(b"\r\n\r\n", 1)[1]

    def send_text(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            header = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        try:
            self.sock.sendall(header + mask + masked)
        except OSError as e:
            raise ProjectError(f"Lost the printer connection ({e}).")

    def _read_exact(self, n):
        while len(self._buffer) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    _partial = None

    def recv_text(self):
        """The next text frame's payload, or None on timeout/close.
        Pings are answered, non-text frames skipped."""
        while True:
            try:
                b1, b2 = self._read_exact(2)
            except (OSError, socket.timeout):
                return None
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            try:
                if length == 126:
                    (length,) = struct.unpack("!H", self._read_exact(2))
                elif length == 127:
                    (length,) = struct.unpack("!Q", self._read_exact(8))
                if b2 & 0x80:  # masked server frame (unusual but legal)
                    mask = self._read_exact(4)
                    payload = bytes(
                        b ^ mask[i % 4] for i, b
                        in enumerate(self._read_exact(length)))
                else:
                    payload = self._read_exact(length)
            except (OSError, socket.timeout):
                return None
            if opcode == 0x9:  # ping -> pong
                try:
                    self.sock.sendall(
                        struct.pack("!BB", 0x8A, 0x80) + os.urandom(4))
                except OSError:
                    return None
                continue
            if opcode == 0x8:  # close
                return None
            # A large message (the gcode file list) arrives as a text
            # frame without FIN followed by continuation frames (opcode
            # 0); the payload is complete only when FIN is set.
            fin = bool(b1 & 0x80)
            if opcode == 0x1:
                self._partial = payload
            elif opcode == 0x0 and self._partial is not None:
                self._partial += payload
            else:
                continue
            if not fin:
                continue
            whole, self._partial = self._partial, None
            try:
                return whole.decode("utf-8")
            except UnicodeDecodeError:
                continue

    def close(self):
        """An orderly WebSocket close: our close frame, then our FIN,
        then read whatever the printer still sends until it hangs up
        (half a second at most).

        Closing a socket with unread data in its receive buffer makes
        the TCP stack send a RESET instead of a normal close, and a
        reset can discard what we sent just before it - the heartbeat
        "ok" or a start command - before the printer has read it.
        Seen as the heartbeat-ack test flaking on Windows (2026-09-02).
        """
        if self.sock is None:
            return
        sock, self.sock = self.sock, None
        try:
            try:
                sock.sendall(struct.pack("!BB", 0x88, 0x80) + os.urandom(4))
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            sock.settimeout(0.5)
            try:
                while sock.recv(4096):
                    pass
            except (OSError, socket.timeout):
                pass
        finally:
            try:
                sock.close()
            except OSError:
                pass


# ---------------------------------------------------- public calls


def saved_device_address():
    """The printer's address from Creality Print's own device list.

    Creality Print stores every printer it has discovered in
    %APPDATA%\\Creality\\Creality Print\\<version>\\deviceInfo.json
    (groups -> list -> entries with "address", "mac", "model";
    verified on the real 7.2.2 install, 2026-09-01). Reading it
    means the user never has to read an IP off the printer's screen
    - and since Creality Print keeps the file fresh, a router
    handing the printer a new address heals itself the next time
    Creality Print has run. Returns "" when nothing is found.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ""
    base = Path(appdata) / "Creality" / "Creality Print"
    if not base.is_dir():
        return ""

    def series_key(p):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (-1,)

    for version_dir in sorted(base.iterdir(), key=series_key,
                              reverse=True):
        info_path = version_dir / "deviceInfo.json"
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        current_mac = str(
            (info.get("current_device") or {}).get("mac") or "")
        entries = [entry
                   for group in (info.get("groups") or [])
                   for entry in (group.get("list") or [])
                   if str(entry.get("address") or "").strip()]
        if not entries:
            continue
        for entry in entries:  # the one Creality Print last used
            if current_mac and entry.get("mac") == current_mac:
                return str(entry["address"]).strip()
        return str(entries[0]["address"]).strip()
    return ""


def safe_gcode_name(stem, out=None):
    """A printer-filesystem-safe gcode file name from a project stem.
    (out, the produced file, is unused here: the K series always gets
    plain .gcode; the PrusaLink module keeps the file's suffix.)"""
    stem = re.sub(r"[^A-Za-z0-9 _().-]+", "_", stem).strip() or "print"
    return f"{stem} {time.strftime('%Y-%m-%d %H.%M')}.gcode"


def extract_gcode(sliced_3mf):
    """(name, bytes) of the gcode inside a produced .gcode.3mf."""
    try:
        with zipfile.ZipFile(sliced_3mf) as z:
            entries = [n for n in z.namelist() if n.endswith(".gcode")]
            if not entries:
                raise ProjectError(
                    "The sliced file holds no gcode entry - slice "
                    "again and retry.")
            return entries[0], z.read(entries[0])
    except zipfile.BadZipFile as e:
        raise ProjectError(f"The sliced file could not be read: {e}")


def upload_gcode(ip, filename, data, timeout=120):
    """POST the gcode to the printer's upload endpoint."""
    boundary = "----AccessSlice" + base64.b16encode(
        os.urandom(8)).decode("ascii")
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    url = (f"http://{ip}/upload/"
           + urllib.parse.quote(filename))
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type",
                       f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            if r.status != 200:
                raise ProjectError(
                    f"The printer refused the upload (HTTP {r.status}).")
    except OSError as e:
        raise ProjectError(
            f"Uploading to the printer at {ip} failed: {e}. The "
            "printer was not asked to print anything.")


def read_status(ip, timeout=8):
    """One merged status dict from the printer's push channel."""
    ws = _WebSocket(ip, timeout=timeout)
    merged = {}
    try:
        ws.send_text(json.dumps(
            {"method": "get", "params": {"ReqPrinterPara": 1}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = ws.recv_text()
            if text is None:
                break
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if data.get("ModeCode") == "heart_beat":
                ws.send_text("ok")
                continue
            if isinstance(data, dict):
                merged.update(data)
            # Enough for a useful report? Stop early once the core
            # facts have arrived.
            if "state" in merged and ("nozzleTemp" in merged
                                      or "printProgress" in merged):
                break
    finally:
        ws.close()
    if not merged:
        raise ProjectError(
            f"The printer at {ip} answered the connection but sent "
            "no status. Try again in a moment.")
    return merged


def pre_send_problem(ip, timeout=8):
    """Why a send would be pointless right now, in words, or None.

    Asked before the upload: a printer mid-print (or paused in one)
    must not be interrupted. A printer that cannot be reached returns
    None here - the upload itself will then say so with its own error.
    """
    try:
        merged = read_status(ip, timeout=timeout)
    except Exception:  # noqa: BLE001 - the upload reports the real failure
        return None
    state = merged.get("state", merged.get("deviceState"))
    try:
        state = int(state)
    except (TypeError, ValueError):
        return None
    name = Path(str(merged.get("printFileName") or "")).name
    job = f" ({name})" if name else ""
    if state in (1, 5):
        word = "printing" if state == 1 else "paused mid-print"
        return (f"Nothing was sent: the printer is {word}{job}. Wait "
                "for it to finish, or stop it on the printer, then send "
                "again.")
    return None


def uploaded_file_path(ip, filename, timeout=8):
    """The full path the printer itself lists for an uploaded file, or
    None. Asks for the gcode file list (reqGcodeFile); the reply can
    be large and arrive in several frames, so the raw text is searched
    rather than parsed as one JSON document."""
    ws = _WebSocket(ip, timeout=timeout)
    chunks = []
    try:
        ws.send_text(json.dumps({"method": "get",
                                 "params": {"reqGcodeFile": 1}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = ws.recv_text()
            if text is None:
                break
            if text.startswith('{"ModeCode": "heart_beat"'):
                ws.send_text("ok")
                continue
            chunks.append(text)
            if "GcodeFileInfo" in text and json.dumps(filename)[1:-1] in text:
                break
    except OSError:
        return None
    finally:
        ws.close()
    raw = "".join(chunks)
    wanted = re.escape(json.dumps(filename)[1:-1])
    m = re.search(r'"path":\s*"((?:[^"\\]|\\.)*?' + wanted + r')"', raw)
    if not m:
        return None
    return m.group(1).replace("\\/", "/")


def gcode_path_for(ip, filename, timeout=8):
    """The path to name in a start request: the printer's own listing
    first, then the folder its current job lives in, then the known
    folders (K2 Plus first)."""
    listed = uploaded_file_path(ip, filename, timeout=timeout)
    if listed:
        return listed
    try:
        current = str(read_status(ip, timeout=timeout)
                      .get("printFileName") or "")
    except Exception:  # noqa: BLE001 - a guess is still better than nothing
        current = ""
    folder = current.rsplit("/", 1)[0] if "/" in current else ""
    if folder.endswith("printer_data/gcodes"):
        return f"{folder}/{filename}"
    return f"{GCODES_DIRS[0]}/{filename}"


def send_warning(sliced_3mf):
    """A plain-words caution for the slice report, or None.

    OPEN ISSUE 2026-09-08: a job sliced in Creality's multi-material
    (CFS) mode loads filament with M8200 commands from the slot the
    file names, and Creality Print's own send carries a filament-to-
    slot mapping that AccessSlice's bare start does not. Edis's first
    such job printed only a few dots. Until the mapping is captured
    and sent, say so before the button.
    """
    try:
        _entry, data = extract_gcode(sliced_3mf)
    except (ProjectError, OSError):
        return None
    head = data[:200000].decode("utf-8", "replace")
    if "; multicolor_method = 1" not in head:
        return None
    return ("Caution: this job was sliced in Creality's multi-material "
            "(CFS) mode. Sending it from AccessSlice does not yet tell "
            "the printer which CFS slot to load from, and a job sent "
            "this way has already printed nothing but a few dots. Until "
            "that is fixed, send CFS jobs from Creality Print's own "
            "window (Ctrl+G) or copy the file to a USB drive, or slice "
            "with the CFS turned off in the project.")


def start_print(ip, filename, timeout=8):
    """Ask the printer to print an already-uploaded gcode file, by the
    path the printer itself knows it under."""
    # A full path (the files page names the printer's own) is used
    # as it is; a bare name is looked up in the printer's listing.
    path = (str(filename) if str(filename).startswith("/")
            else gcode_path_for(ip, filename, timeout=timeout))
    ws = _WebSocket(ip, timeout=timeout)
    try:
        ws.send_text(json.dumps({"method": "set", "params": {
            "opGcodeFile": f"printprt:{path}"}}))
        # No documented acknowledgement; give the frame time to land.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if ws.recv_text() is None:
                break
    finally:
        ws.close()


def _num(value):
    """'219.200000' -> '219'; leaves non-numbers alone."""
    try:
        return f"{float(value):.0f}"
    except (TypeError, ValueError):
        return str(value)


def status_lines(merged):
    """The status dict as plain-language lines for the status page."""
    lines = []
    state = merged.get("state", merged.get("deviceState"))
    if state is not None:
        try:
            word = STATE_WORDS.get(int(state), f"state code {state}")
        except (TypeError, ValueError):
            word = str(state)
        lines.append(f"The printer is {word}.")
    # With no job under way the printer still carries the last job's
    # name, progress and time left (seen live 2026-09-08 and
    # 2026-09-15): say so in one line and skip the running-job lines.
    over = job_phase(merged) in _OVER_PHASES
    name = str(merged.get("printFileName") or "").strip()
    progress = merged.get("printProgress", merged.get("dProgress"))
    if name and over:
        lines.append(f"Last job: {Path(name).name}"
                     + (f", reached {_num(progress)} percent"
                        if progress is not None else "") + ".")
    elif name:
        lines.append(f"Current job: {Path(name).name}.")
    if progress is not None and not over:
        lines.append(f"Progress: {_num(progress)} percent.")
    left = merged.get("printLeftTime")
    if left and not over:
        try:
            from engine_driver import friendly_duration
            lines.append(f"Time left: about "
                         f"{friendly_duration(float(left))}.")
        except (TypeError, ValueError):
            pass
    layer, total = merged.get("layer"), merged.get("TotalLayer")
    if layer is not None and total and not over:
        lines.append(f"Layer {layer} of {total}.")
    nozzle = merged.get("nozzleTemp")
    nozzle_t = merged.get("targetNozzleTemp")
    if nozzle is not None:
        lines.append(f"Nozzle {_num(nozzle)} degrees"
                     + (f" (target {_num(nozzle_t)})"
                        if nozzle_t not in (None, 0, "0") else "")
                     + ".")
    bed = merged.get("bedTemp0")
    bed_t = merged.get("targetBedTemp0")
    if bed is not None:
        lines.append(f"Bed {_num(bed)} degrees"
                     + (f" (target {_num(bed_t)})"
                        if bed_t not in (None, 0, "0") else "") + ".")
    box = merged.get("boxTemp")
    if box not in (None, 0, "0"):
        lines.append(f"Chamber {_num(box)} degrees.")
    if not lines:
        lines.append("The printer answered, but its status held none "
                     "of the usual fields. Raw keys: "
                     + ", ".join(sorted(merged)[:12]) + ".")
    return lines


# ------------------------------------------------ control: direct
#
# Added 2026-09-12 for the K2 Plus, after the Prusa control landed.
# Unlike PrusaLink, the K family's WebSocket takes commands
# DIRECTLY: {"method": "set", "params": {...}} - a temperature, a
# fan, the light, pause, stop, homing, a jog, a raw G-code line, a
# CFS feed - with no job file and no idle-printer condition for most
# of them. The vocabulary below is the community's (the
# creality-cfs-panel device API notes, the ha_creality_ws and
# ha-creality-lan Home Assistant integrations, read 2026-09-12);
# NOTHING here is verified on the K2 Plus yet - the printer was off
# that day. Every command therefore reads the status back afterwards
# and reports what changed, so the first real session is the probe.
#
# - nozzleTempControl: <degrees>        targetNozzleTemp follows
# - bedTempControl: {"num": 0, "val": <degrees>}
# - boxTempControl: <degrees>           the K2's chamber (box) heater
# - fan / fanAuxiliary / fanCase: 0-100 (the model, side and case fans)
# - lightSw: 0 | 1
# - pause: 1 | 0 (resume), stop: 1
# - autohome: "X Y Z" | "X Y" | "Z"    deviceState 7 while homing
# - setPosition: "X10 F3000"           a relative jog
# - setZOffset: "+0.01" | "-0.01"      live Z (baby step)
# - setFeedratePct / setFlowratePct: percent
# - gcodeCmd: "<one G-code line>"      run by the printer's Klipper
# - feedInOrOut: {"boxId": 1, "materialId": 0-3, "isFeed": 1 | 0}
#   (boxId 1 = the CFS, 0 = the external spool holder; isFeed 0
#   retracts); {"method": "get", "params": {"boxsInfo": 1}} lists
#   the CFS boxes and their materials.
# - opGcodeFile: "deleteprt:<path>" deletes a file; the list comes
#   from reqGcodeFile as retGcodeFileInfo2 (see uploaded_file_path).

CAN_CONTROL = True
CONTROL_MODE = "direct"    # printer_control runs steps, not job files
NEEDS_KEY = False

# The K2 Plus's reach and heater limits, for the page's checks.
LIMITS = {
    "x": (0, 350), "y": (0, 350), "z": (0, 350),
    "nozzle": (0, 350), "bed": (0, 120), "chamber": (0, 60),
    "fan": (0, 100), "step": (0.1, 200), "length": (0.1, 200),
    "zoffset": (0.005, 1), "percent": (10, 300),
}


def _collect(ws, seconds, want=None):
    """What the printer pushes after a request, merged; the heartbeat
    answered on the way. With `want` (a set of keys) it returns as
    soon as a push carries one of them; without, at the first push
    after the request; after `seconds` either way."""
    merged = {}
    ws.sock.settimeout(max(0.2, seconds))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        text = ws.recv_text()
        if text is None:
            break
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("ModeCode") == "heart_beat":
            ws.send_text("ok")
            continue
        merged.update(data)
        if want is None or any(k in data for k in want):
            break
    return merged


def _drain(ws, seconds=0.15):
    """Swallow what the printer pushed on connect (its heartbeat and
    a status) so the next read is the answer to OUR request."""
    ws.sock.settimeout(seconds)
    while True:
        text = ws.recv_text()
        if text is None:
            return
        if text.startswith('{"ModeCode": "heart_beat"'):
            ws.send_text("ok")


def set_params(ip, params, timeout=8, settle=1.5):
    """One set command; the first state the printer pushed after it
    (its state, usually already changed)."""
    ws = _WebSocket(ip, timeout=timeout)
    try:
        _drain(ws)
        ws.send_text(json.dumps({"method": "set", "params": params}))
        return _collect(ws, settle)
    finally:
        ws.close()


def get_params(ip, params, timeout=8, settle=2.0):
    """One get request; the reply that carries one of its keys (merged
    with whatever else arrived first)."""
    ws = _WebSocket(ip, timeout=timeout)
    try:
        _drain(ws)
        ws.send_text(json.dumps({"method": "get", "params": params}))
        return _collect(ws, settle, want=set(params))
    finally:
        ws.close()


def send_gcode(ip, line, timeout=8):
    return set_params(ip, {"gcodeCmd": str(line).strip()}, timeout=timeout)


def pause_print(ip, timeout=8):
    return set_params(ip, {"pause": 1}, timeout=timeout)


def resume_print(ip, timeout=8):
    return set_params(ip, {"pause": 0}, timeout=timeout)


def stop_print(ip, timeout=8):
    return set_params(ip, {"stop": 1}, timeout=timeout)


def check_printer(ip, timeout=8):
    """A hello: the status read, with the model and hostname when
    the printer names them. ProjectError in words otherwise."""
    merged = read_status(ip, timeout=timeout)
    return {"printer": str(merged.get("model") or "Creality K series"),
            "hostname": str(merged.get("hostname") or ""),
            "firmware": str(merged.get("modelVersion") or "")}


def cfs_info(ip, timeout=8):
    """The boxes as the printer lists them: [{"id", "type", "temp",
    "humidity", "materials": [{"id", "type", "name", "vendor",
    "color", "state", "selected", "percent"}]}]; [] when the printer
    has no CFS or did not answer the question.

    Seen live 2026-09-15 on the K2 Plus: box type 1 is the external
    spool holder (id 0, one remembered material, no temperature),
    type 0 a CFS with four slots. Colours come as seven hex digits
    ("#03ddf57"); the leading digit is dropped."""
    reply = get_params(ip, {"boxsInfo": 1}, timeout=timeout)
    info = reply.get("boxsInfo")
    if not isinstance(info, dict):
        return []
    boxes = []
    for box in info.get("materialBoxs") or []:
        if not isinstance(box, dict):
            continue
        materials = []
        for m in box.get("materials") or []:
            if isinstance(m, dict):
                color = str(m.get("color") or "")
                if re.fullmatch(r"#[0-9A-Fa-f]{7}", color):
                    color = "#" + color[2:]
                materials.append({
                    "id": m.get("id"), "type": str(m.get("type") or ""),
                    "name": str(m.get("name") or ""),
                    "vendor": str(m.get("vendor") or ""),
                    "color": color,
                    "state": m.get("state"), "selected": m.get("selected"),
                    "percent": m.get("percent")})
        boxes.append({"id": box.get("id"), "type": box.get("type"),
                      "temp": box.get("temp"),
                      "humidity": box.get("humidity"),
                      "materials": materials})
    return boxes


def cfs_feed(ip, box_id, material_id, feed=True, timeout=8):
    """Load (feed=True) or retract the filament of one CFS slot."""
    return set_params(ip, {"feedInOrOut": {
        "boxId": int(box_id), "materialId": int(material_id),
        "isFeed": 1 if feed else 0}}, timeout=timeout)


def list_files(ip, path="", timeout=8):
    """The printer's gcode files (reqGcodeFile), newest first, each
    {"name", "display_name", "path", "type": "PRINT_FILE", "size"}.
    path is ignored: the K family keeps one flat gcodes folder."""
    ws = _WebSocket(ip, timeout=timeout)
    entries = []
    try:
        ws.send_text(json.dumps({"method": "get",
                                 "params": {"reqGcodeFile": 1}}))
        ws.sock.settimeout(2.0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = ws.recv_text()
            if text is None:
                break
            if text.startswith('{"ModeCode": "heart_beat"'):
                ws.send_text("ok")
                continue
            try:
                data = json.loads(text)
            except ValueError:
                continue
            listing = data.get("retGcodeFileInfo2") if isinstance(
                data, dict) else None
            if isinstance(listing, list):
                for item in listing:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    if not name:
                        continue
                    entries.append({
                        "name": name, "display_name": name,
                        "path": str(item.get("path") or ""),
                        "type": "PRINT_FILE",
                        "size": item.get("size"),
                        "m_timestamp": item.get("modifyTime")
                        or item.get("time")})
                break
    finally:
        ws.close()
    entries.sort(key=lambda e: str(e.get("m_timestamp") or ""),
                 reverse=True)
    return entries


def delete_file(ip, path, timeout=8):
    """Remove a gcode file by the path the printer lists it under."""
    if not str(path).startswith("/"):
        path = gcode_path_for(ip, str(path), timeout=timeout)
    set_params(ip, {"opGcodeFile": f"deleteprt:{path}"}, timeout=timeout)


def position(merged):
    """{"x", "y", "z"} floats from the curPosition string - the K2
    Plus writes "X:175.50 Y:175.80 Z:-2.01" (seen live 2026-09-15;
    the colons had defeated the first reading, so no position was
    ever spoken), the community notes "X10.00 Y20.00 Z0.30" - or {}
    when the printer gave none."""
    text = str(merged.get("curPosition") or "")
    found = {}
    for axis, value in re.findall(
            r"([XYZ])\s*:?\s*(-?\d+(?:\.\d+)?)", text):
        found[axis.lower()] = float(value)
    return found


def _on_off(value):
    try:
        return "on" if int(float(value)) else "off"
    except (TypeError, ValueError):
        return str(value)


def control_lines(merged):
    """Status lines the control page adds to status_lines(): the
    chamber target, light, fans, head position, speed and flow -
    only the fields the printer actually sent."""
    lines = []
    box_t = merged.get("targetBoxTemp")
    if box_t not in (None, 0, "0", 0.0):
        lines.append(f"Chamber target {_num(box_t)} degrees.")
    if merged.get("lightSw") is not None:
        lines.append(f"Light {_on_off(merged['lightSw'])}.")
    fans = [(label, merged.get(key)) for label, key in (
        ("model fan", "fan"), ("side fan", "fanAuxiliary"),
        ("case fan", "fanCase")) if merged.get(key) is not None]
    if fans:
        lines.append("Fans: " + ", ".join(
            f"{label} {_num(value)} percent" for label, value in fans)
            + ".")
    where = position(merged)
    if where:
        lines.append("Head at " + ", ".join(
            f"{a.upper()} {where[a]:g}" for a in ("x", "y", "z")
            if a in where) + ".")
    speed = merged.get("curFeedratePct")
    flow = merged.get("curFlowratePct")
    if speed is not None and _num(speed) != "100":
        lines.append(f"Speed {_num(speed)} percent.")
    if flow is not None and _num(flow) != "100":
        lines.append(f"Flow {_num(flow)} percent.")
    return lines
