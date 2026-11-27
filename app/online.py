"""online.py - the update check and the opt-in usage statistics.

The only two things AccessSlice ever sends over the internet on its
own (decided by Edis 2026-11-27; docs\\online-services.md is the
contract the accessible3d.io side implements):

1. THE UPDATE CHECK - on unless the user turns it off on the Privacy
   page. At most once a day AccessSlice asks accessible3d.io for the
   newest version, sending only its own version number. A newer one
   is announced on the Home page with a button that opens the
   download page in the user's browser; nothing is downloaded or
   installed by AccessSlice itself.
2. USAGE STATISTICS - OFF until the user says yes (a one-time
   question on the Home page; the Privacy page changes it any time).
   Once a week AccessSlice sends counts of what it was used for: a
   random install number, the AccessSlice and Windows versions, the
   active slicer, the printer models used and how many jobs, slices,
   sends and problems there were. Never file names, folders, printer
   serial numbers or addresses, passwords, custom profile names or
   anything typed. Turning it off deletes what was counted and the
   install number; turning it on again starts a new number.

The counts and timestamps live in online.json beside config.json -
NOT in the config - because the background thread writes them while
request handlers load and save the config; a shared file would let
one overwrite the other's changes. The thread only READS the config.

Nothing here may ever break the app: every network or file failure is
swallowed and simply tried again later. ACCESSSLICE_OFFLINE=1 turns
all network traffic off (the test suite sets it).
"""

import datetime
import json
import os
import platform
import re
import threading
import urllib.parse
import urllib.request
import uuid
import webbrowser

import config_store
from version import VERSION

BASE_URL = "https://accessible3d.io/api/accessslice"
DEFAULT_DOWNLOAD_URL = "https://accessible3d.io/software/accessslice/"
# A download link from the server is only opened when it points here.
DOWNLOAD_HOSTS = {"accessible3d.io", "www.accessible3d.io", "github.com"}
OFFLINE_ENV = "ACCESSSLICE_OFFLINE"
SCHEMA = 1

UPDATE_EVERY = datetime.timedelta(days=1)
REPORT_EVERY = datetime.timedelta(days=7)
RETRY_AFTER = datetime.timedelta(days=1)
TIMEOUT_S = 10
LOOP_S = 3600          # how often the background thread looks
FIRST_LOOK_S = 20      # after start, so the start itself stays quick

UPDATE_KEY = "update_check"     # config: False turns the check off
USAGE_KEY = "usage_stats"       # config: True / False / missing = not asked

# What is counted, in the order the Privacy page lists it.
EVENTS = {
    "app_starts": "times AccessSlice was started",
    "jobs_created": "new print jobs built from a model file",
    "jobs_from_step": "of those, jobs from a STEP file",
    "jobs_failed": "new print jobs that could not be built",
    "projects_opened": "project files opened",
    "slices_done": "slices that finished",
    "slices_failed": "slices that failed",
    "sends_network": "jobs sent to the printer over the network",
    "sends_usb": "jobs copied to a USB drive or a folder",
    "slicer_opened": "times AccessSlice opened the slicer program",
    "printer_controls": "printer controls used (preheat, move, pause "
                        "and the like)",
    "problems_shown": "problems shown in plain words",
    "unexpected_errors": "unexpected internal errors",
}

# A printer is reported by model only when its name looks like a
# maker's own profile ("Bambu Lab H2C 0.4 nozzle"); a profile the user
# named themselves could hold a name, so it becomes "custom profile".
BRANDS = ("Bambu Lab ", "Prusa ", "Original Prusa ", "Creality ",
          "Anycubic ", "Elegoo ", "Qidi ", "QIDI ", "Voron ", "Sovol ",
          "Flashforge ", "Snapmaker ", "Artillery ", "Ender")
_PRINTER_SHAPE = re.compile(r"^[A-Za-z0-9 .+\-()]{3,60}$")
CUSTOM_PRINTER = "custom profile"

_lock = threading.Lock()
_thread = None


# ------------------------------------------------------------ helpers

def _now(now=None):
    return now or datetime.datetime.now()


def _stamp(when):
    return when.replace(microsecond=0).isoformat()


def _parse(stamp):
    try:
        return datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def state_path():
    """online.json beside the config (looked up at call time, so the
    test suite's sandboxed %APPDATA% is honoured)."""
    return config_store.CONFIG_PATH.parent / "online.json"


def load_state():
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def offline():
    return os.environ.get(OFFLINE_ENV, "") not in ("", "0")


def parse_version(text):
    """"1.1.3" -> (1, 1, 3); anything else -> None."""
    if not isinstance(text, str) or not re.fullmatch(r"\d+(\.\d+){0,3}",
                                                     text.strip()):
        return None
    return tuple(int(p) for p in text.strip().split("."))


def printer_model(name):
    """The printer name as reported: a maker's profile name, or
    CUSTOM_PRINTER for anything that may be the user's own wording."""
    name = str(name or "").strip()
    if (name.startswith(BRANDS) and name.endswith("nozzle")
            and _PRINTER_SHAPE.match(name)):
        return name
    return CUSTOM_PRINTER


def safe_download_url(url):
    """The server's download link when it is https on a known host,
    else the accessible3d.io download page."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return DEFAULT_DOWNLOAD_URL
    if parts.scheme == "https" and parts.hostname in DOWNLOAD_HOSTS:
        return parts.geturl()
    return DEFAULT_DOWNLOAD_URL


# ------------------------------------------------------- the settings

def update_check_enabled(config):
    return config.get(UPDATE_KEY, True) is not False


def usage_asked(config):
    return config.get(USAGE_KEY) in (True, False)


def usage_enabled(config):
    return config.get(USAGE_KEY) is True


def set_update_check(config, on):
    """Change the update check (the caller saves the config). Turning
    it off also forgets a pending update notice."""
    config[UPDATE_KEY] = bool(on)
    if not on:
        with _lock:
            state = load_state()
            for key in ("latest", "last_update_check"):
                state.pop(key, None)
            save_state(state)
    return config


def set_usage(config, on, now=None):
    """Answer the usage question (the caller saves the config).

    On: a fresh random install number and an empty count starting now.
    Off: the counts and the install number are deleted, so nothing
    collected so far can ever be sent.
    """
    config[USAGE_KEY] = bool(on)
    with _lock:
        state = load_state()
        for key in ("install_id", "period_start", "counts", "printers",
                    "last_report_sent", "last_report_attempt"):
            state.pop(key, None)
        if on:
            state["install_id"] = str(uuid.uuid4())
            state["period_start"] = _stamp(_now(now))
            state["counts"] = {}
            state["printers"] = {}
        save_state(state)
    return config


# ------------------------------------------------------------ counting

def count(event, printer=None, config=None):
    """Count one event (a key of EVENTS) when usage statistics are on.
    Never raises - counting must not be able to break anything."""
    try:
        if event not in EVENTS:
            return
        config = config_store.load() if config is None else config
        if not usage_enabled(config):
            return
        with _lock:
            state = load_state()
            if not state.get("install_id"):
                return
            counts = state.setdefault("counts", {})
            counts[event] = int(counts.get(event, 0)) + 1
            if printer:
                printers = state.setdefault("printers", {})
                model = printer_model(printer)
                printers[model] = int(printers.get(model, 0)) + 1
            save_state(state)
    except Exception:  # noqa: BLE001 - see the docstring
        pass


def report_payload(config, state=None, now=None):
    """Exactly what the next weekly report sends (the Privacy page
    shows this very dictionary)."""
    state = load_state() if state is None else state
    counts = state.get("counts") or {}
    return {
        "schema": SCHEMA,
        "install_id": state.get("install_id", ""),
        "app_version": VERSION,
        "windows_version": platform.version(),
        "slicer": str(config.get("slicer") or ""),
        "period_start": (state.get("period_start") or "")[:10],
        "period_end": _stamp(_now(now))[:10],
        "counts": {k: int(counts.get(k, 0)) for k in EVENTS},
        "printers": dict(sorted((state.get("printers") or {}).items())),
    }


# ------------------------------------------------------------ network

def _request(url, data=None):
    """GET (data None) or POST JSON; returns (status, body bytes)."""
    headers = {"User-Agent": f"AccessSlice/{VERSION}",
               "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.status, resp.read(64 * 1024)


def check_update(now=None):
    """Ask for the newest version and remember the answer. Returns
    True when the server answered."""
    now = _now(now)
    url = (f"{BASE_URL}/latest?"
           + urllib.parse.urlencode({"version": VERSION}))
    with _lock:
        state = load_state()
        state["last_update_check"] = _stamp(now)
        save_state(state)
    try:
        status, body = _request(url)
        answer = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001 - offline, server down, bad JSON
        return False
    if status != 200 or not isinstance(answer, dict):
        return False
    latest = str(answer.get("latest_version", ""))
    if parse_version(latest) is None:
        return False
    with _lock:
        state = load_state()
        state["latest"] = {
            "version": latest,
            "url": safe_download_url(answer.get("download_url")),
            "notes": str(answer.get("notes") or "")[:300],
        }
        save_state(state)
    return True


def send_report(config, now=None):
    """Send the weekly report; on success start a new period. Returns
    True when the server accepted it."""
    now = _now(now)
    with _lock:
        state = load_state()
        state["last_report_attempt"] = _stamp(now)
        save_state(state)
    payload = report_payload(config, state, now)
    try:
        status, _body = _request(f"{BASE_URL}/usage", payload)
    except Exception:  # noqa: BLE001
        return False
    if not 200 <= status < 300:
        return False
    with _lock:
        state = load_state()
        if state.get("install_id") != payload["install_id"]:
            return True  # switched off or renewed meanwhile
        # Counts that arrived while sending stay for the next report.
        counts = state.get("counts") or {}
        state["counts"] = {k: n - payload["counts"].get(k, 0)
                           for k, n in counts.items()
                           if n - payload["counts"].get(k, 0) > 0}
        printers = state.get("printers") or {}
        state["printers"] = {k: n - payload["printers"].get(k, 0)
                             for k, n in printers.items()
                             if n - payload["printers"].get(k, 0) > 0}
        state["period_start"] = _stamp(now)
        state["last_report_sent"] = _stamp(now)
        save_state(state)
    return True


def update_due(config, state, now=None):
    if not update_check_enabled(config):
        return False
    last = _parse(state.get("last_update_check"))
    return last is None or _now(now) - last >= UPDATE_EVERY


def report_due(config, state, now=None):
    if not usage_enabled(config) or not state.get("install_id"):
        return False
    now = _now(now)
    start = _parse(state.get("period_start"))
    if start is None or now - start < REPORT_EVERY:
        return False
    attempt = _parse(state.get("last_report_attempt"))
    sent = _parse(state.get("last_report_sent"))
    if attempt and (sent is None or attempt > sent) \
            and now - attempt < RETRY_AFTER:
        return False
    return True


def tick(now=None):
    """One look: run whatever is due. Never raises."""
    if offline():
        return
    try:
        config = config_store.load()
        state = load_state()
        if update_due(config, state, now):
            check_update(now)
        if report_due(config, state, now):
            send_report(config, now)
    except Exception:  # noqa: BLE001
        pass


def start_background():
    """Start the background thread once (a daemon: it never keeps the
    program alive). Does nothing when offline."""
    global _thread
    if offline() or _thread is not None:
        return
    stop = threading.Event()

    def loop():
        if stop.wait(FIRST_LOOK_S):
            return
        while True:
            tick()
            if stop.wait(LOOP_S):
                return

    _thread = threading.Thread(target=loop, name="accessslice-online",
                               daemon=True)
    _thread.start()


# -------------------------------------------------------- for the UI

def update_available(config, state=None):
    """(version, url, notes) of a newer AccessSlice, or None."""
    if not update_check_enabled(config):
        return None
    state = load_state() if state is None else state
    latest = state.get("latest") or {}
    theirs = parse_version(latest.get("version"))
    ours = parse_version(VERSION)
    if theirs is None or ours is None or theirs <= ours:
        return None
    return (latest["version"], safe_download_url(latest.get("url")),
            latest.get("notes", ""))


def open_download(config, opener=None):
    """Open the download page of the announced update (or the general
    one) in the user's browser; returns the URL opened."""
    found = update_available(config)
    url = found[1] if found else DEFAULT_DOWNLOAD_URL
    (opener or webbrowser.open)(url)
    return url
