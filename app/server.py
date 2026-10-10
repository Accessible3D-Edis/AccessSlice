"""server.py - AccessSlice's local web server and routing.

A small stdlib HTTP server bound to 127.0.0.1 only (nothing ever leaves
this machine). Every screen is an ordinary GET page; every action is an
ordinary POST followed by a redirect carrying a plain-language status
message, which the next page announces through its live region. That
pattern - post, redirect, announce - is what keeps the whole app sturdy
for screen readers.

Routes:
  GET  /                    home (or setup on first run)
  GET/POST /setup           first-run setup / settings
  GET  /start               explainer, POST /start launches the slicer
  POST /new-job             start building a project from a model file
                            (no GUI) in the background
  GET  /building            the build's progress page (JS polls
                            /api/build); opens the project when built
  GET  /api/build           build job status as JSON
  POST /building/cancel     stop the running build
  GET  /waiting             watching the projects folder (JS polls)
  GET  /api/watch           watcher status as JSON
  GET  /open-found          open the project the watcher spotted
  GET  /files               open-a-project page
  POST /open                open a given project path
  GET/POST /project/<tab>   the editor: quality strength support adhesion
                            filaments placement all review
  GET/POST /project/setting/<key>   one setting's edit page
  POST /project/discard     drop one staged change
  POST /project/save        write the file (backup + verify)
  GET  /project/send        the slice-and-send page
  POST /project/send        launch the slicer on the saved file (its
                            Device page switched first when closed)
  GET  /project/send-device the question when the slicer is open on
                            another printer: close it, or open anyway
  POST /project/slice       start a headless slice of the saved file
  GET  /project/slicing     slicing progress page (JS polls /api/slice)
  GET  /api/slice           slice job status as JSON
  POST /project/slice-cancel   stop the running slice
  GET  /project/slice-report   the plain-language slice report
  POST /project/send-sliced    open the sliced file in the slicer
  POST /project/send-lan       upload to the printer over LAN and start
  POST /project/copy-usb       copy the sliced file to a USB drive/folder
  POST /project/show-sliced    File Explorer with the sliced file selected
  GET  /handoff             after the launch
  GET  /printer             what the printer reports (JS polls /api/printer)
  GET  /printer/control     control the printer: preheat, filament, moves,
                            calibration, settings, any G-code (2026-09-12)
  POST /printer/control/<action>   one command as a job on the printer
  POST /printer/connection  save the printer's address and password
  GET  /printer/files       the USB drive's files
  POST /printer/files/start|delete|upload
  GET  /api/control         the running command's events as JSON
  POST /project/close       close the project
  GET  /static/<file>       stylesheet and the one script
"""

import datetime
import json
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import backends
import build_job
import config_store
import coffee
import wizard
import diagnostics
import usbcopy
import geometry
import preview
import printer_control
import toolpath
import viewport
import webui
from errors import ProjectError
from watcher import FolderWatcher

STATIC_DIR = Path(__file__).resolve().parent / "static"

# The names this server answers to; anything else in a Host header
# is a request that was not meant for AccessSlice's own window.
LOCAL_HOSTS = ("127.0.0.1", "localhost")

# One user, one machine, one open project: module-level state is right.
state = {
    "session": None,          # the open project session, or None
    "watcher": FolderWatcher(),
    "lock": threading.Lock(), # serialises project mutations
    "slice_job": None,
    "build_job": None,        # the running/latest new-job BuildJob
    "preview": preview.PreviewCache(),        # the running/latest engine SliceJob
    "control_job": None,      # the running/latest printer command job
}


def match_names(names, query):
    """Case-insensitive multi-word search: every word must appear."""
    terms = query.lower().split()
    return [n for n in names if all(t in n.lower() for t in terms)]


def known(config, key):
    """The user's learned short list for the ACTIVE slicer.

    Since 2026-09-01 these lists are stored per slicer id
    ({"bambu": [...], "creality": [...]}) - a K2 Plus must never be
    offered for a Bambu job nor a Bambu printer for a Creality one
    (found by Edis on the first Creality start page). A plain list,
    the pre-multi-slicer format, is read as the Bambu list.
    """
    slicer = config.get("slicer") or "bambu"
    value = config.get(key)
    if isinstance(value, list):
        return list(value) if slicer == "bambu" else []
    if isinstance(value, dict):
        return list(value.get(slicer, []))
    return []


def learn(config, key, names):
    """Remember names in the ACTIVE slicer's short list.

    The preset and printer pickers show these instead of the slicer's
    full library lists, which were overwhelming; anything ever used or
    seen in a project joins the list. An old plain-list value is
    migrated to the per-slicer form as the Bambu list.
    """
    slicer = config.get("slicer") or "bambu"
    value = config.get(key)
    if isinstance(value, list):
        value = {"bambu": value}
    elif not isinstance(value, dict):
        value = {}
    target = list(value.get(slicer, []))
    added = False
    for name in names:
        if name and name not in target:
            target.append(name)
            added = True
    if added:
        value[slicer] = target
        config[key] = value
        config_store.save(config)


def slicer_display_name(config):
    for value, name, _supported in config_store.SLICERS:
        if value == config.get("slicer"):
            return name
    return "your slicer"


def _is_program_of(exe, slicer_id):
    """True when the file name is one of that slicer's program names
    (the same check the Settings save makes), or when the slicer has
    no known names to check against."""
    try:
        names = backends.get_backend(slicer_id).exe_names
    except backends.BackendError:
        return False
    if not names:
        return True
    return Path(exe).name.lower() in tuple(n.lower() for n in names)


def backend_for(config):
    """The configured slicer's backend - everything slicer-specific.

    Raises BackendError (a ProjectError, so it surfaces as a normal
    friendly Problem page) for a slicer id without a backend.
    """
    return backends.get_backend(config.get("slicer") or "bambu")


def launch_slicer(config, file_path=None):
    """Start the slicer, optionally with a project file to open."""
    exe = config.get("slicer_exe")
    if not exe or not Path(exe).is_file():
        raise ProjectError(
            "The slicer program was not found at the configured path. "
            "Check it on the Settings page."
        )
    cmd = [exe] + ([str(file_path)] if file_path else [])
    try:
        subprocess.Popen(cmd, cwd=str(Path(exe).parent))
    except OSError as e:
        raise ProjectError(
            f"The slicer program could not be started ({e}). Check the "
            "slicer path on the Settings page, or start it by hand this "
            "time."
        )
    diagnostics.log("Launched the slicer"
                    + (f" with {Path(file_path).name}" if file_path
                       else ""))


def device_plan(config, machine):
    """What a send must do about the slicer's Device-page printer.

    A project file carries its own machine PRESET, so the slicer's
    Prepare side follows the file - but Bambu Studio's Device page
    (the printer the send window offers) is a separate, saved choice
    that never follows a file. Seen 2026-09-23: a send for the H2C
    while Studio was watching an X1 Carbon print left the X1 Carbon
    in the send window.

    Returns None when nothing is needed (no conf-file switch for this
    slicer, no printer named, or the page already on it), else a dict:
    "state" - "other" (the model's serial is known, so the switch can
    be made while the slicer is closed) or "unknown" (no serial known,
    only the user can move it); "printer" and "other" - model names.
    """
    switch = backend_for(config).printer_switch
    if switch is None or not machine:
        return None
    serials = config.get("device_serials", {})
    state = switch.device_state(machine, serials)
    if state == "same":
        return None
    return {"state": state, "printer": switch.model_name(machine),
            "other": switch.current_device(serials), "switch": switch,
            "serials": serials}


class Handler(BaseHTTPRequestHandler):
    server_version = "AccessSlice"

    # Silence per-request console noise; errors still surface normally.
    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------- plumbing

    def _send_html(self, html, code=200):
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location, msg=None):
        if msg:
            sep = "&" if "?" in location else "?"
            location = location + sep + "msg=" + urllib.parse.quote(msg)
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self):
        """Parse a POST body into {name: [values...]}."""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        return urllib.parse.parse_qs(body, keep_blank_values=True)

    def _query(self):
        return urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query, keep_blank_values=True
        )

    def _path_parts(self):
        return urllib.parse.urlparse(self.path).path.strip("/").split("/")

    def _printer_address(self, config, backend):
        """The printer's LAN address: ?ip= on the request, else the
        address the slicer itself recorded (Creality Print's
        deviceInfo.json, PrusaSlicer's physical printer), else the
        one remembered in config."""
        if backend.lan is None:
            return ""
        return (self._query().get("ip", [""])[0].strip()
                or getattr(backend.lan, "saved_device_address",
                           lambda: "")()
                or config.get("lan_printer_ips", {}).get(backend.id, ""))

    @staticmethod
    def _printer_key(config, backend):
        """The printer's password (API key) for backends whose LAN
        module NEEDS_KEY (PrusaLink): the one remembered in config,
        else the one the slicer itself stored; "" for the rest."""
        if backend.lan is None or not getattr(backend.lan, "NEEDS_KEY",
                                              False):
            return ""
        keys = config.get("lan_printer_keys")
        if isinstance(keys, dict) and keys.get(backend.id):
            return str(keys[backend.id])
        return getattr(backend.lan, "saved_api_key", lambda: "")()

    @staticmethod
    def _lan_kwargs(config, backend):
        """The extra keyword arguments the LAN module's calls take:
        the key for NEEDS_KEY modules, nothing for the others."""
        if backend.lan is not None and getattr(backend.lan, "NEEDS_KEY",
                                               False):
            return {"key": Handler._printer_key(config, backend)}
        return {}

    # ------------------------------------------- who is asking?
    #
    # The server listens on 127.0.0.1 only, but a web page open in the
    # user's browser can still address it - browsers let any site send
    # form submissions to localhost, and DNS rebinding lets a site's own
    # name resolve to 127.0.0.1. Two checks close both doors, using the
    # headers browsers always attach: every request must name THIS
    # server in Host, and every POST must come from the app's own
    # origin (Origin / Referer / Sec-Fetch-Site). A client that sends
    # none of the origin headers is not a browser doing a cross-site
    # post (browsers always send Origin on those), so it is let through
    # - that keeps the test suite and curl usable.

    def _own_origins(self):
        port = self.server.server_address[1]
        return {f"http://{h}:{port}" for h in LOCAL_HOSTS}

    def _host_is_this_server(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if ":" in host:
            name, _, port = host.rpartition(":")
        else:
            name, port = host, "80"
        return (name in LOCAL_HOSTS
                and port == str(self.server.server_address[1]))

    def _post_is_from_own_page(self):
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none"):
            return False
        own = self._own_origins()
        origin = self.headers.get("Origin")
        if origin is not None:
            return origin.strip().lower().rstrip("/") in own
        referer = (self.headers.get("Referer") or "").strip().lower()
        if referer:
            return any(referer == o or referer.startswith(o + "/")
                       for o in own)
        return True

    def _refuse(self, why):
        """403 with a plain-words page; the attempt goes to the log."""
        diagnostics.log(
            f"Refused a request that did not come from AccessSlice's own "
            f"window ({why}; Host: {self.headers.get('Host')!r}, Origin: "
            f"{self.headers.get('Origin')!r}, path: {self.path})")
        self._send_error_page(webui.page(
            "Request refused",
            "<h1>Request refused</h1>"
            "<p>AccessSlice only accepts requests from its own window. "
            "This one arrived from somewhere else - typically another "
            "website open in a browser trying to reach AccessSlice on "
            "this computer - so nothing was done.</p>"
            "<p>If you were using AccessSlice normally when you saw this, "
            "open it again from the Start menu; the attempt was recorded "
            "on the Diagnostics page.</p>"), code=403)

    def _msg(self):
        vals = self._query().get("msg")
        return vals[0] if vals else None

    def _need_session(self):
        """The open project, or None after redirecting home."""
        if state["session"] is None:
            self._redirect("/", msg="No project is open. Start a print job "
                                    "or open a project file first.")
            return None
        return state["session"]

    # ------------------------------------------------------------ GET

    def do_GET(self):
        try:
            if not self._host_is_this_server():
                return self._refuse("the Host header names another server")
            self._route_get()
        except ConnectionError:
            # The browser gave up on this request (closed tab, aborted
            # navigation). Nothing can be sent to it and nothing is
            # wrong server-side - the waiting page retries on its own.
            pass
        except ProjectError as e:
            diagnostics.log(f"Problem shown to the user: {e}")
            self._send_error_page(webui.page(
                "Problem", f"<h1>Problem</h1>"
                f"<p>{webui.esc(str(e))}</p>"
                '<p><a href="/">Home</a></p>'))
        except Exception as e:  # never a blank screen or silent death
            self._send_unexpected_error_page(e, "showing a page")

    def _send_unexpected_error_page(self, exc, doing_what):
        """The safety net for bugs: plain words on screen, the full
        technical traceback in the diagnostics log."""
        context = f"{doing_what} ({self.path})"
        diagnostics.log_exception(context, exc)
        self._send_error_page(webui.page(
            "Unexpected problem",
            "<h1>Unexpected problem</h1>"
            f"<p>AccessSlice hit an internal error while {webui.esc(doing_what)}. "
            "This is a bug in AccessSlice, not a mistake on your part.</p>"
            "<p>Your project file on disk was not changed by this - "
            "changes are only ever written when a save reports success.</p>"
            "<p>The technical details were written to the diagnostics "
            "log, so the problem can be reported and fixed.</p>"
            '<p><a href="/diagnostics">Open the diagnostics page</a></p>'
            '<p><a href="/">Home</a></p>'),
            code=500)

    def _send_error_page(self, html, code=200):
        """Send an error page, tolerating a browser that already left."""
        try:
            self._send_html(html, code=code)
        except ConnectionError:
            pass

    def _route_get(self):
        parts = self._path_parts()
        config = config_store.load()

        # Static files.
        if parts[0] == "static" and len(parts) == 2:
            return self._serve_static(parts[1])

        # First run: everything funnels into the setup wizard until
        # setup is complete (Settings stays reachable as the fallback).
        if (not config_store.is_complete(config)
                and parts[0] not in ("setup", "welcome")):
            return self._redirect("/welcome")
        if parts[0] == "welcome":
            return self._get_wizard(config, parts[1:])

        if parts == [""]:
            # The coffee pop-up's week counts from the first start.
            if coffee.note_first_run(config):
                config_store.save(config)
            return self._send_html(webui.render_home(
                config, self._recent_entries(config), msg=self._msg(),
                slicer_name=slicer_display_name(config),
                coffee=coffee.due(config),
                printer=backend_for(config).lan is not None,
                control=getattr(backend_for(config).lan, "CAN_CONTROL",
                                False)))
        if parts == ["tour"]:
            first = self._query().get("first", [""])[0] == "1"
            return self._send_html(webui.render_tour(
                slicer_display_name(config), first=first,
                msg=self._msg()))

        if parts == ["setup"]:
            # The setup page must always render - it is where a broken
            # slicer choice gets fixed - so detection failure stays quiet.
            try:
                backend = backend_for(config)
                detected = backend.detect_exe()
                texts = backend.texts
            except backends.BackendError:
                detected, texts = None, None
            # The path field's value per slicer choice, so the page's
            # script can swap it the moment another radio is chosen.
            paths = {}
            for slicer_id in backends.supported_ids():
                other = backends.get_backend(slicer_id)
                paths[slicer_id] = config_store.exe_for(
                    config, slicer_id, other.detect_exe)
            configured = config_store.is_complete(config)
            if not config.get("projects_dir"):
                config["projects_dir"] = str(config_store.DEFAULT_PROJECTS_DIR)
            return self._send_html(webui.render_setup(
                config, detected, config_store.SLICERS,
                configured=configured, texts=texts, paths=paths))

        if parts == ["start"]:
            backend = backend_for(config)
            search = self._query().get("printer_search", [""])[0].strip()
            matches = None
            if search:
                matches = match_names(backend.profiles.list_presets(
                    config.get("slicer_exe"), "machine"), search)
            current = (backend.printer_switch.current_printer()
                       if backend.printer_switch else "")
            return self._send_html(webui.render_start(
                slicer_display_name(config),
                known(config, "known_printers"),
                current,
                matches=matches, search=search, msg=self._msg(),
                texts=backend.texts,
                model_file_available=backend.newjob is not None))

        if parts == ["waiting"]:
            return self._send_html(webui.render_waiting(
                config.get("projects_dir", ""), msg=self._msg(),
                texts=backend_for(config).texts))

        if parts == ["api", "watch"]:
            _watching, found = state["watcher"].status()
            return self._send_json({
                "found": bool(found),
                "name": Path(found).name if found else None,
            })

        if parts == ["api", "printer"]:
            # The monitoring poll behind /printer: the same read as the
            # page, as JSON, with the phase the page reasons about.
            # Errors come back as ok: false in plain words (HTTP 200),
            # so the page can say them instead of dying quietly.
            backend = backend_for(config)
            ip = self._printer_address(config, backend)
            if backend.lan is None or not ip:
                return self._send_json({
                    "ok": False,
                    "error": "No printer address is known."})
            try:
                merged = backend.lan.read_status(
                    ip, **self._lan_kwargs(config, backend))
            except ProjectError as e:
                return self._send_json({"ok": False, "error": str(e)})
            phase = backend.lan.job_phase(merged)
            lines = backend.lan.status_lines(merged)
            extra = getattr(backend.lan, "control_lines", None)
            if extra is not None:
                lines = lines + extra(merged)
            return self._send_json({
                "ok": True,
                "phase": phase,
                "done": phase in backend.lan.DONE_PHASES,
                "lines": lines,
                "text": " ".join(lines),
            })

        if parts in (["api", "toolpath"], ["api", "toolpath-facts"]):
            # The sliced preview's data (Phase V): the binary segment
            # blob, or the JSON facts the page was rendered from. A
            # parse still running answers with its status.
            entry = self._preview_entry(config)
            if entry is None:
                return self._send_json({"error": "Nothing has been sliced "
                                        "yet."})
            if entry["status"] != "ready":
                return self._send_json({"error": entry["error"] or
                                        "The preview is still being "
                                        "prepared.",
                                        "status": entry["status"]})
            if parts[1] == "toolpath":
                return self._send_bytes(entry["payload"],
                                        "application/octet-stream")
            return self._send_json(entry["facts"])

        if parts == ["api", "scene"]:
            # The 3D view's description of the open project (Phase IV):
            # plates, keep-out corner, and the shown plate's objects at
            # their EFFECTIVE placement. JSON, so the page script can
            # draw it; no session means nothing to draw.
            session = state["session"]
            if session is None:
                return self._send_json({"error": "No project is open."})
            query = self._query()
            plate = query.get("plate", [""])[0]
            every = query.get("all", [""])[0] == "1"
            return self._send_json(viewport.scene(
                session, plate_id=plate or None, all_plates=every))

        if len(parts) == 3 and parts[:2] == ["api", "mesh"]:
            session = state["session"]
            if session is None:
                return self._send_json({"error": "No project is open."})
            objectid = parts[2]
            if objectid not in {o["id"] for o in session.objects()}:
                return self._send_json({"error": "No such object."})
            blob = viewport.mesh_bytes(session, objectid)
            if blob is None:
                return self._send_json({"error": "No readable mesh."})
            return self._send_bytes(blob, "application/octet-stream")

        if parts == ["api", "build"]:
            job = state["build_job"]
            if job is None:
                return self._send_json({"status": "none"})
            return self._send_json({
                "status": job.status,
                "elapsed": job.elapsed(),
                "text": job.describe(),
                "next": None if job.status == "running" else "/building",
            })

        if parts == ["building"]:
            return self._get_building(config)

        if parts == ["api", "slice"]:
            job = state["slice_job"]
            if job is None:
                return self._send_json({"status": "none"})
            return self._send_json({
                "status": job.status,
                "elapsed": job.elapsed(),
                "text": job.describe(),
                "next": (None if job.status == "running"
                         else "/project/slice-report"),
            })

        if parts == ["open-found"]:
            _watching, found = state["watcher"].status()
            if not found:
                # The waiting page retries this address until navigation
                # sticks, so a repeat arriving after the project already
                # opened must land in the editor, not back on /waiting.
                if state["session"]:
                    return self._redirect("/project/quality")
                return self._redirect("/waiting")
            state["watcher"].stop()
            diagnostics.log(f"The folder watcher found {Path(found).name}")
            return self._open_project(found, config)

        if parts == ["files"]:
            return self._send_html(webui.render_files(
                config.get("projects_dir", ""),
                self._folder_entries(config), msg=self._msg()))

        if parts == ["diagnostics"]:
            return self._send_html(webui.render_diagnostics(
                diagnostics.read_recent(),
                str(diagnostics.log_path()), msg=self._msg()))

        if parts == ["printer"]:
            # Read-only: what the printer itself reports over LAN.
            backend = backend_for(config)
            if backend.lan is None:
                return self._redirect("/", msg=(
                    "Asking the printer directly is not available "
                    f"for {slicer_display_name(config)}."))
            ip = self._printer_address(config, backend)
            if not ip:
                return self._redirect("/", msg=(
                    "No printer address is known yet. It is found "
                    "by itself once the slicer has seen the printer "
                    "on the network, or asked for on the slice "
                    "report page."))
            merged = backend.lan.read_status(
                ip, **self._lan_kwargs(config, backend))
            lines = backend.lan.status_lines(merged)
            extra = getattr(backend.lan, "control_lines", None)
            if extra is not None:
                lines = lines + extra(merged)
            return self._send_html(webui.render_printer_status(
                lines, ip, msg=self._msg(),
                live=backend.lan.job_phase(merged)
                not in backend.lan.DONE_PHASES,
                control=getattr(backend.lan, "CAN_CONTROL", False)))

        if parts == ["printer", "control"]:
            return self._get_printer_control(config)

        if parts == ["printer", "files"]:
            return self._get_printer_files(config)

        if parts == ["api", "control"]:
            job = state["control_job"]
            if job is None:
                return self._send_json({"ok": True, "running": False,
                                        "done": False, "events": [],
                                        "text": ""})
            return self._send_json(job.snapshot())

        if parts == ["limits"]:
            return self._send_html(webui.render_limits_page(
                slicer_display_name(config),
                texts=backend_for(config).texts))

        if parts == ["handoff"]:
            session = self._need_session()
            if session:
                when = datetime.datetime.now().strftime("%H:%M")
                query = self._query()
                second = query.get("second", [""])[0] == "1"
                sliced_name = None
                if query.get("sliced", [""])[0] == "1":
                    job = state["slice_job"]
                    out = job.output_file() if job else None
                    sliced_name = out.name if out else None
                device = None
                outcome = query.get("device", [""])[0]
                switch = backend_for(config).printer_switch
                if outcome in ("switched", "kept") and switch is not None:
                    machine = session.settings.get("printer_settings_id",
                                                   "")
                    device = {
                        "state": outcome,
                        "printer": switch.model_name(machine),
                        "other": ("" if outcome == "switched" else
                                  switch.current_device(
                                      config.get("device_serials", {}))),
                    }
                self._send_html(webui.render_handoff(
                    session, slicer_display_name(config), when,
                    second_window=second, sliced_name=sliced_name,
                    texts=backend_for(config).texts, device=device))
            return None

        if parts[0] == "project":
            return self._route_get_project(parts, config)

        self._redirect("/")

    def _route_get_project(self, parts, config):
        session = self._need_session()
        if not session:
            return
        msg = self._msg()

        if len(parts) == 2 and parts[1] == "unit-question":
            query = self._query()
            key = query.get("key", [""])[0]
            return_to = query.get("return_to", ["/project/quality"])[0]
            if not return_to.startswith("/project/"):
                return_to = "/project/quality"
            return self._send_html(webui.render_unit_question(
                session, key, return_to, msg=msg))

        if len(parts) == 2 and parts[1] == "send-device":
            sliced = self._query().get("sliced", [""])[0] == "1"
            plan = device_plan(config,
                               session.settings.get("printer_settings_id",
                                                    ""))
            if not plan:
                return self._redirect(
                    "/project/slice-report" if sliced else "/project/send",
                    msg=f"{slicer_display_name(config)} is already on "
                    "this project's printer - nothing to switch.")
            return self._send_html(webui.render_send_device_question(
                session, slicer_display_name(config), plan["printer"],
                plan["other"], sliced=sliced, msg=msg,
                texts=backend_for(config).texts))

        if len(parts) == 2 and parts[1] == "infill-question":
            return_to = self._query().get(
                "return_to", ["/project/strength"])[0]
            if not return_to.startswith("/project/"):
                return_to = "/project/strength"
            return self._send_html(webui.render_infill_question(
                session, return_to, msg=msg))

        if len(parts) == 3 and parts[1] == "object":
            objectid = parts[2]
            if objectid not in {o["id"] for o in session.objects()}:
                return self._redirect("/project/placement",
                                      msg=f"No object with id {objectid}.")
            search = self._query().get("q", [""])[0].strip()
            return self._send_html(webui.render_object_page(
                session, objectid, q=search, msg=msg))

        if len(parts) == 3 and parts[1] == "setting":
            key = urllib.parse.unquote(parts[2])
            if key not in session.settings:
                return self._redirect("/project/all",
                                      msg=f"No setting named {key}.")
            return self._send_html(
                webui.render_setting_page(session, key, msg=msg))

        tab = parts[1] if len(parts) > 1 else "quality"
        curated = {tab_id: entries for tab_id, _name, entries
                   in backend_for(config).catalog.TABS}
        if tab in curated:
            return self._send_html(webui.render_curated_tab(
                session, tab, curated[tab], msg=msg))
        if tab == "filaments":
            return self._send_html(webui.render_filaments_tab(session, msg=msg))
        if tab == "flushing":
            return self._send_html(webui.render_flushing_page(session, msg=msg))
        if tab == "presets":
            return self._send_html(webui.render_presets_tab(
                session, self._preset_view(session, config), msg=msg))
        if tab == "placement":
            return self._send_html(webui.render_placement_tab(session, msg=msg))
        if tab == "all":
            q = self._query().get("q", [""])[0]
            return self._send_html(webui.render_all_tab(session, q, msg=msg))
        if tab == "review":
            return self._send_html(webui.render_review(session, msg=msg))
        if tab == "backups":
            return self._send_html(webui.render_backups_tab(session, msg=msg))
        if tab == "send":
            backend = backend_for(config)
            # Pre-flight warnings are computed from the saved file
            # only once nothing is staged (the page refuses to
            # slice otherwise); an unreadable mesh degrades to no
            # claim inside preflight, never to an error page.
            warnings = None
            if backend.preflight is not None and not session.pending:
                warnings = backend.preflight.warnings(session)
            return self._send_html(webui.render_send(
                session, slicer_display_name(config),
                blocked_by_pending=bool(session.pending), msg=msg,
                texts=backend.texts, warnings=warnings))
        if tab == "preview":
            return self._preview_page(session, config, msg)
        if tab == "slicing":
            job = state["slice_job"]
            if job is None:
                return self._redirect("/project/send")
            if job.status != "running":
                return self._redirect("/project/slice-report", msg=msg)
            return self._send_html(webui.render_slicing(session, job))
        if tab == "slice-report":
            job = state["slice_job"]
            if job is None:
                return self._redirect("/project/send")
            if job.status == "running":
                return self._redirect("/project/slicing")
            backend = backend_for(config)
            lan = None
            if backend.lan is not None:
                # The slicer's own device list beats the saved value:
                # it is refreshed by the slicer's discovery, so a
                # printer with a new address heals itself - and the
                # user never reads an IP off the printer's screen.
                discovered = getattr(backend.lan,
                                     "saved_device_address",
                                     lambda: "")()
                lan = {"ip": discovered
                       or config.get("lan_printer_ips", {})
                       .get(backend.id, "")}
                if getattr(backend.lan, "NEEDS_KEY", False):
                    lan.update({
                        "needs_key": True,
                        "key": self._printer_key(config, backend),
                        "key_label": getattr(backend.lan, "KEY_LABEL",
                                             "Password"),
                        "key_help": getattr(backend.lan, "KEY_HELP", ""),
                    })
            produced = job.output_file() if job else None
            if lan is not None and produced is not None:
                warn = getattr(backend.lan, "send_warning", None)
                if warn is not None:
                    lan["warning"] = warn(produced)
            return self._send_html(webui.render_slice_report(
                session, job, slicer_display_name(config), msg=msg,
                texts=backend.texts, lan=lan,
                drives=usbcopy.removable_drives()))
        self._redirect("/project/quality")

    # ----------------------------------------------------------- POST

    def do_POST(self):
        try:
            if not self._host_is_this_server():
                return self._refuse("the Host header names another server")
            if not self._post_is_from_own_page():
                return self._refuse("the post came from another website")
            with state["lock"]:
                self._route_post()
        except ConnectionError:
            pass  # browser dropped the connection; see do_GET
        except ProjectError as e:
            # Friendly failure: back to a sensible page with the message.
            diagnostics.log(f"Problem shown to the user: {e}")
            target = "/project/review" if state["session"] else "/"
            try:
                self._redirect(target, msg=f"Problem: {e}")
            except ConnectionError:
                pass
        except Exception as e:
            self._send_unexpected_error_page(e, "carrying out that action")

    def _route_post(self):
        parts = self._path_parts()
        config = config_store.load()
        form = self._form()

        def field(name, default=""):
            return form.get(name, [default])[0].strip()

        if parts == ["setup"]:
            return self._post_setup(config, field)
        if parts[0] == "welcome":
            return self._post_wizard(config, parts[1:], field)
        if parts == ["tour", "done"]:
            config["tour_seen"] = True
            config_store.save(config)
            return self._redirect("/", msg=(
                "AccessSlice is ready. Help and tour in the top "
                "navigation brings the tour back any time."))
        if parts == ["coffee", "open"]:
            coffee.open_page()
            config_store.save(coffee.rest(config))
            diagnostics.log("Opened the buy-me-a-coffee page in the "
                            "browser")
            return self._redirect("/", msg=(
                "Opened buymeacoffee.com/accessible3d in your web "
                "browser - thank you!"))
        if parts == ["coffee", "later"]:
            config_store.save(coffee.rest(config))
            return self._redirect("/", msg=(
                "No problem - AccessSlice will ask again in about a "
                "month."))

        if parts[:1] == ["printer"] and len(parts) >= 2:
            return self._post_printer(config, parts[1:], form, field)

        if parts == ["start"]:
            projects_dir = Path(config["projects_dir"])
            projects_dir.mkdir(parents=True, exist_ok=True)
            # Set Studio's printer BEFORE launching it, so the project
            # saved from it inherits the right machine. Only possible
            # while Studio is closed - it rewrites its conf on exit.
            chosen = field("printer")
            printer_msgs = []
            serials = config.get("device_serials", {})
            backend = backend_for(config)
            switch = backend.printer_switch
            if (chosen and switch
                    and switch.pending_changes(chosen, serials)):
                if backend.slicer_running(config.get("slicer_exe")):
                    return self._redirect("/start", msg=(
                        f"{slicer_display_name(config)} is already "
                        "running, so the printer choice cannot be set. "
                        "Close it (Alt+F4 in its window), then press "
                        "OK here again."))
                try:
                    printer_msgs = switch.set_printer(
                        chosen, config.get("slicer_exe"), serials)
                    diagnostics.log("Studio printer switch: "
                                    + " ".join(printer_msgs))
                except switch.StudioConfError as e:
                    diagnostics.log(f"Studio printer switch failed: {e}")
                    return self._redirect("/start", msg=str(e))
            if chosen:
                learn(config, "known_printers", [chosen])
            launch_slicer(config)
            state["watcher"].arm(projects_dir)
            return self._redirect("/waiting", msg=" ".join(printer_msgs)
                                  if printer_msgs else None)

        if parts == ["open"]:
            return self._open_project(field("path"), config)

        if parts == ["new-job"]:
            return self._post_new_job(config, field)

        if parts == ["building", "cancel"]:
            job = state["build_job"]
            if job and job.status == "running":
                job.cancel()
                job.wait(10)
                diagnostics.log(f"New job from {job.model_name} "
                                "cancelled")
            return self._redirect("/building")

        if parts == ["diagnostics", "clear"]:
            diagnostics.clear()
            return self._redirect("/diagnostics",
                                  msg="The diagnostics log is now empty.")

        if parts[0] != "project":
            return self._redirect("/")

        session = self._need_session()
        if not session:
            return

        # Remember which editor page this request's form lives on, so
        # every change staged below records where it came from - the
        # Review tab's Discard uses it to send the user back there.
        session.stage_source = self._stage_source(
            parts, field, backend_for(config).catalog.TABS)

        if parts[1] == "infill-answer":
            return self._post_infill_answer(session, field)

        if parts[1] == "unit-answer":
            return self._post_unit_answer(session, field)

        if parts[1] == "setting" and len(parts) == 3:
            key = urllib.parse.unquote(parts[2])
            return self._post_setting(session, key, form, field)

        if parts[1] == "object" and len(parts) == 3:
            return self._post_object(session, parts[2], form, field)

        if parts[1] == "discard":
            return self._post_discard(session, form, field)

        if parts[1] == "reload":
            lines = session.reload()
            viewport.reset_cache(session)
            diagnostics.log(f"Reloaded {session.path.name} after an "
                            "outside change")
            return_to = field("return_to")
            if not return_to.startswith("/project/"):
                return_to = "/project/quality"
            return self._redirect(return_to, msg=" ".join(lines))

        if parts[1] == "backups":
            lines = session.restore_backup(field("backup"))
            diagnostics.log(f"Restore on {session.path.name}: "
                            + " ".join(lines))
            return self._redirect("/project/backups", msg=" ".join(lines))

        if parts[1] == "save":
            lines = session.save()
            diagnostics.log(f"Saved {session.path.name}: "
                            + " ".join(lines))
            config_store.remember_recent(config, session.path)
            # Land back on the page the user saved from, never elsewhere.
            return_to = field("return_to")
            if not return_to.startswith("/project/"):
                return_to = "/project/review"
            return self._redirect(return_to, msg=" ".join(lines))

        if parts[1] == "send":
            if session.pending:
                return self._redirect("/project/send")
            if session.is_stale():
                return self._redirect("/project/quality", msg=(
                    "The project file changed on disk after AccessSlice "
                    "read it - probably saved from "
                    f"{slicer_display_name(config)}. Reload "
                    "the file from disk (the warning on this page has "
                    "the button) and check the settings before sending."))
            return self._hand_to_slicer(session, config, session.path,
                                        field, sliced=False)

        if parts[1] == "slice":
            return self._post_slice(session, config, field)

        if parts[1] == "slice-cancel":
            job = state["slice_job"]
            if job and job.status == "running":
                job.cancel()
                job.wait(5)
                diagnostics.log(f"Slice cancelled on {session.path.name}")
                return self._redirect("/project/slice-report",
                                      msg="Slicing cancelled.")
            return self._redirect("/project/send",
                                  msg="Nothing is being sliced right now.")

        if parts[1] in ("send-lan", "copy-usb", "show-sliced"):
            # A failed send or copy lands back on the report with the
            # problem in words, not on the review page.
            try:
                if parts[1] == "send-lan":
                    return self._post_send_lan(session, config, field)
                if parts[1] == "copy-usb":
                    return self._post_copy_usb(session, config, field)
            except ProjectError as e:
                diagnostics.log(f"Problem shown to the user: {e}")
                return self._redirect("/project/slice-report",
                                      msg=f"Problem: {e}")

            job = state["slice_job"]
            out = job.output_file() if job else None
            if not out:
                return self._redirect("/project/send", msg=(
                    "There is no sliced file yet - slice first."))
            try:
                usbcopy.reveal(out)
            except ProjectError as e:
                return self._redirect("/project/slice-report",
                                      msg=f"Problem: {e}")
            return self._redirect("/project/slice-report", msg=(
                f"Opened File Explorer with {out.name} selected - "
                "Alt+Tab to it. Copy the file from there to wherever "
                "the printer reads it from."))

        if parts[1] == "send-sliced":
            job = state["slice_job"]
            out = job.output_file() if job else None
            if not out:
                return self._redirect("/project/send", msg=(
                    "There is no sliced file yet - slice first."))
            return self._hand_to_slicer(session, config, out, field,
                                        sliced=True)

        if parts[1] == "close":
            job, extra = state["slice_job"], ""
            if job and job.status == "running":
                job.cancel()
                job.wait(5)
                diagnostics.log(f"Slice cancelled on {session.path.name} "
                                "(project closed)")
                extra = " The running slice was cancelled."
            state["slice_job"] = None
            state["session"] = None
            return self._redirect("/", msg="Project closed." + extra)

        # Tab forms that stage settings.
        curated = {tab_id: entries for tab_id, _name, entries
                   in backend_for(config).catalog.TABS}
        if parts[1] in curated:
            return self._post_curated(session, parts[1], curated[parts[1]],
                                      form, field)
        if parts[1] == "filaments":
            return self._post_filaments(session, form, field)
        if parts[1] == "flushing":
            return self._post_flushing(session, form, field)
        if parts[1] == "presets":
            return self._post_presets(session, field)
        if parts[1] == "placement":
            return self._post_placement(session, form, field)

        self._redirect("/project/quality")

    # ----------------------------------------------- POST sub-handlers

    # ------------------------------------------------ printer control
    #
    # The Prusa Accessible Control program's window as pages
    # (2026-09-12). Every command is one POST that starts a command
    # job (printer_control.CommandJob) and redirects back to the
    # control page, which follows the job through /api/control.

    def _control_parts(self, config):
        """(backend, ip, key) for the control pages, or None after
        redirecting home with the reason in words."""
        backend = backend_for(config)
        if backend.lan is None or not getattr(backend.lan, "CAN_CONTROL",
                                              False):
            self._redirect("/", msg=(
                "Controlling the printer from here is not available "
                f"for {slicer_display_name(config)} yet - it works for "
                "PrusaSlicer's Core One over PrusaLink and Creality "
                "Print's K series over the LAN."))
            return None
        ip = self._printer_address(config, backend)
        key = self._printer_key(config, backend)
        return backend, ip, key

    def _get_printer_control(self, config):
        found = self._control_parts(config)
        if found is None:
            return None
        backend, ip, key = found
        lan = backend.lan
        lines, error = [], None
        if not ip:
            error = ("No printer address is known yet - type it under "
                     "Connection below.")
        else:
            try:
                merged = lan.read_status(
                    ip, **self._lan_kwargs(config, backend))
                lines = lan.status_lines(merged)
                extra = getattr(lan, "control_lines", None)
                if extra is not None:
                    lines = lines + extra(merged)
            except ProjectError as e:
                error = str(e)
        job = state["control_job"]
        profile = printer_control.profile_for(lan)
        cfs = None
        if "cfs" in profile["features"] and ip and not error:
            try:
                cfs = lan.cfs_info(ip)
            except ProjectError:
                cfs = None
        return self._send_html(webui.render_printer_control(
            ip, bool(key), getattr(lan, "NEEDS_KEY", False), lines, error,
            job.snapshot() if job else None,
            profile["presets"], profile["snippets"], profile["limits"],
            msg=self._msg(),
            key_label=getattr(lan, "KEY_LABEL", "Password"),
            key_help=getattr(lan, "KEY_HELP", ""),
            features=profile["features"], mode=profile["mode"], cfs=cfs,
            printer_name=("the printer" if profile["mode"] == "job"
                          else "the K-series printer")))

    def _get_printer_files(self, config):
        found = self._control_parts(config)
        if found is None:
            return None
        backend, ip, _key = found
        if not ip:
            return self._redirect("/printer/control", msg=(
                "No printer address is known yet - type it under "
                "Connection first."))
        path = self._query().get("path", [""])[0].strip("/")
        if ".." in path.split("/"):
            return self._redirect("/printer/files",
                                  msg="Problem: that is not a folder on "
                                      "the printer.")
        try:
            entries = backend.lan.list_files(
                ip, path, **self._lan_kwargs(config, backend))
        except ProjectError as e:
            return self._redirect("/printer/control",
                                  msg=f"Problem: {e}")
        return self._send_html(webui.render_printer_files(
            ip, path, entries, msg=self._msg()))

    def _post_printer(self, config, parts, form, field):
        """POST /printer/... - connection, control/<action>, files/<verb>.
        Problems come back to the control page in words, never to
        the home page."""
        found = self._control_parts(config)
        if found is None:
            return None
        backend, ip, key = found
        back = "/printer/control"
        try:
            if parts == ["connection"]:
                return self._post_printer_connection(config, backend,
                                                     field)
            if not ip:
                return self._redirect(back, msg=(
                    "Nothing was sent - no printer address is known "
                    "yet. Type it under Connection first."))
            if len(parts) == 2 and parts[0] == "control":
                return self._post_printer_action(config, backend, ip, key,
                                                 parts[1], field)
            if len(parts) == 2 and parts[0] == "files":
                back = "/printer/files"
                folder = field("folder").strip("/")
                if folder:
                    back += "?path=" + urllib.parse.quote(folder)
                return self._post_printer_file(config, backend, ip, key,
                                               parts[1], field, back)
        except ProjectError as e:
            diagnostics.log(f"Printer control problem: {e}")
            return self._redirect(back, msg=f"Problem: {e}")
        return self._redirect(back, msg="Problem: AccessSlice knows no "
                                        "such printer action.")

    def _post_printer_connection(self, config, backend, field):
        lan = backend.lan
        ip = field("ip").strip()
        if not ip:
            return self._redirect("/printer/control", msg=(
                "Type the printer's address first."))
        needs_key = getattr(lan, "NEEDS_KEY", False)
        key = field("key").strip() if needs_key else ""
        if needs_key and not key:
            key = self._printer_key(config, backend)
        if needs_key and not key:
            return self._redirect("/printer/control", msg=(
                "Type the printer's "
                + getattr(lan, "KEY_LABEL", "password") + " too. "
                + getattr(lan, "KEY_HELP", "")))
        return self._redirect("/printer/control", msg=(
            self._save_printer_connection(config, backend, ip, key)))

    def _save_printer_connection(self, config, backend, ip, key):
        """Ask the printer at ip (with key where the LAN module needs
        one), and remember the address - and the password - once it
        answered. Returns the spoken result; raises ProjectError when
        the printer did not answer."""
        lan = backend.lan
        needs_key = getattr(lan, "NEEDS_KEY", False)
        kwargs = {"key": key} if needs_key else {}
        info = lan.check_printer(ip, **kwargs)
        remembered = self._address_to_remember(lan, ip, kwargs)
        ips = config.get("lan_printer_ips")
        if not isinstance(ips, dict):
            ips = {}
        ips[backend.id] = remembered
        config["lan_printer_ips"] = ips
        if needs_key:
            keys = config.get("lan_printer_keys")
            if not isinstance(keys, dict):
                keys = {}
            keys[backend.id] = key
            config["lan_printer_keys"] = keys
        config_store.save(config)
        words = "The printer answered"
        if isinstance(info, dict):
            name = info.get("printer") or info.get("hostname")
            firmware = info.get("firmware") or info.get("version")
            if name:
                words += f": {name}"
            if firmware:
                words += f", firmware {firmware}"
        diagnostics.log(f"Printer connection saved for {remembered}")
        if remembered != ip:
            words += (". It is remembered by its own name, "
                      f"{remembered}, which follows the printer when "
                      "your router hands it a new address"
                      + ("; the password is remembered too." if needs_key
                         else "."))
        else:
            words += (". The address"
                      + (" and password are" if needs_key else " is")
                      + " remembered.")
        return words

    @staticmethod
    def _address_to_remember(lan, ip, kwargs):
        """The address to keep for a printer that just answered at
        ip: the LAN module's stable_address (PrusaLink: the printer's
        own name when it resolves), else ip itself."""
        stable = getattr(lan, "stable_address", None)
        if stable is None:
            return ip
        return stable(ip, **kwargs) or ip

    def _post_printer_action(self, config, backend, ip, key, action,
                             field):
        lan = backend.lan
        kwargs = self._lan_kwargs(config, backend)
        direct = getattr(lan, "CONTROL_MODE", "job") == "direct"
        if action in ("pause", "resume", "stop"):
            if action == "stop" and field("confirm") != "yes":
                raise ProjectError(
                    "Nothing was stopped. Tick the confirmation next to "
                    "the Stop button first.")
            merged = lan.read_status(ip, **kwargs)
            if direct:
                phase = lan.job_phase(merged)
                wanted = {"pause": ("printing",), "resume": ("paused",),
                          "stop": ("printing", "paused", "busy")}[action]
                if phase not in wanted:
                    raise ProjectError(
                        f"The printer has no running print to {action}. "
                        + lan.status_lines(merged)[0])
                getattr(lan, action + "_print")(ip)
                diagnostics.log(f"Printer asked to {action} its print")
                done = {"pause": "paused", "resume": "resumed",
                        "stop": "stopped"}[action]
                return self._redirect("/printer/control", msg=(
                    f"Asked the printer to {action}; it should now be "
                    f"{done}. The printer's own report is below."))
            job_id = merged.get("job_id")
            phase = lan.job_phase(merged)
            wanted = {"pause": ("printing",), "resume": ("paused",),
                      "stop": ("printing", "paused", "attention",
                               "busy")}[action]
            if job_id is None or phase not in wanted:
                raise ProjectError(
                    f"The printer has no running print to {action}. "
                    + lan.status_lines(merged)[0])
            getattr(lan, action + "_job")(ip, job_id, **kwargs)
            diagnostics.log(f"Printer job {job_id} asked to {action}")
            done = {"pause": "paused", "resume": "resumed",
                    "stop": "stopped"}[action]
            return self._redirect("/printer/control", msg=(
                f"The print was {done}. The printer's own report is "
                "below."))
        # The head position read just before the command is what
        # motion_guard checks every move against (the 2026-09-15
        # plate damage: a jog below the plate, then a home that
        # dragged the nozzle). No position: moves toward the plate,
        # sideways moves and homes are refused in words.
        position, phase = None, None
        try:
            merged = lan.read_status(ip, **kwargs)
            position = lan.position(merged)
            phase = lan.job_phase(merged)
        except ProjectError:
            pass
        clear = field("clear") == "yes"
        if direct:
            name, steps = printer_control.build_direct_action(
                action, field, lan, position=position, phase=phase,
                clear=clear)
            state["control_job"] = printer_control.start_direct_job(
                state["control_job"], lan, ip, name, steps)
        else:
            name, lines = printer_control.build_action(
                action, field, position=position, clear=clear)
            state["control_job"] = printer_control.start_job(
                state["control_job"], lan, ip, key, name, lines)
        return self._redirect("/printer/control", msg=(
            f"Sent: {name}. The page follows it under The command and "
            "says when it is done."))

    def _post_printer_file(self, config, backend, ip, key, verb, field,
                           back):
        lan = backend.lan
        kwargs = self._lan_kwargs(config, backend)
        path = field("path").strip("/")
        if verb in ("start", "delete"):
            if not path or ".." in path.split("/"):
                raise ProjectError("Choose a file on the printer first.")
        if verb == "start":
            if field("plate_empty") != "yes":
                raise ProjectError(
                    "Nothing was started. First check with your hands "
                    "that the build plate is empty, then tick the "
                    "confirmation - a leftover print on the plate can "
                    "wreck the printer.")
            lan.start_print(ip, path, **kwargs)
            diagnostics.log(f"Printer asked to print {path}")
            return self._redirect("/printer", msg=(
                f"Asked the printer to print {path}. The lines below "
                "are the printer's own report."))
        if verb == "delete":
            if field("confirm") != "yes":
                raise ProjectError(
                    "Nothing was deleted. Tick the confirmation next to "
                    "the Delete button first.")
            lan.delete_file(ip, path, **kwargs)
            diagnostics.log(f"Deleted {path} from the printer")
            return self._redirect(back, msg=f"Deleted {path} from the "
                                            "printer.")
        if verb == "upload":
            source = Path(field("file").strip().strip('"'))
            if not field("file").strip():
                raise ProjectError("Type the path of the file to upload "
                                   "first.")
            if not source.is_file():
                raise ProjectError(f"There is no file at {source}.")
            if source.suffix.lower() not in (".gcode", ".bgcode"):
                raise ProjectError(
                    "Only .gcode and .bgcode files can be printed - "
                    f"{source.name} is neither.")
            folder = field("folder").strip("/")
            name = lan.safe_gcode_name(source.stem, source)
            target = f"{folder}/{name}" if folder else name
            try:
                data = source.read_bytes()
            except OSError as e:
                raise ProjectError(f"The file could not be read: {e}")
            lan.upload_gcode(ip, target, data, **kwargs)
            diagnostics.log(f"Uploaded {source} to the printer as "
                            f"{target}")
            if field("start") == "yes":
                lan.start_print(ip, target, **kwargs)
                return self._redirect("/printer", msg=(
                    f"Uploaded {name} and asked the printer to print "
                    "it."))
            return self._redirect(back, msg=(
                f"Uploaded {name} ({len(data) // 1024} KB) to the "
                "printer."))
        raise ProjectError("AccessSlice knows no such file action.")

    def _post_setup(self, config, field):
        problems, exe_note = self._apply_setup(config, field)
        if problems:
            return self._redirect("/setup", msg=" ".join(problems))
        # The first completed setup leads into the tour (skippable);
        # later visits to Settings go straight home.
        target = "/" if config.get("tour_seen") else "/tour?first=1"
        return self._redirect(
            target, msg="Setup complete. AccessSlice is ready to use."
                        + (f" {exe_note}" if exe_note else ""))

    @staticmethod
    def _apply_setup(config, field):
        """Check and save the slicer, its program and the projects
        folder (Settings and the wizard's folders step share this).
        Returns (problems, exe_note); nothing is saved on a problem."""
        slicer = field("slicer") or config.get("slicer") or "bambu"
        try:
            backend = backends.get_backend(slicer)
        except backends.BackendError as e:
            return [str(e)], ""
        projects_dir = field("projects_dir").strip().strip('"').strip("'")
        # Paths are the app's job to format: quotes stripped, desktop
        # shortcuts resolved, an empty field filled by detection.
        slicer_exe, exe_note = config_store.normalize_exe_path(
            field("slicer_exe"))
        # Switching slicers: the field still holds the OLD slicer's
        # program (its prefilled value) - that is never what the new
        # slicer wants, so it counts as empty and the new slicer's own
        # path (last saved, else detected) takes its place.
        old_slicer = config.get("slicer")
        switched = False
        if (slicer_exe and old_slicer and slicer != old_slicer
                and _is_program_of(slicer_exe, old_slicer)
                and not _is_program_of(slicer_exe, slicer)):
            slicer_exe, switched = "", True
        if not slicer_exe:
            slicer_exe = config_store.exe_for(
                config, slicer, backend.detect_exe)
            if slicer_exe and switched:
                exe_note = (f"The program path switched to "
                            f"{backend.display_name} at {slicer_exe}.")
            elif slicer_exe:
                exe_note = (f"AccessSlice found {backend.display_name} "
                            f"at {slicer_exe} by itself.")
        problems = []
        if projects_dir:
            try:
                Path(projects_dir).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                problems.append(f"Could not create the projects folder: {e}")
        else:
            problems.append("The projects folder is required.")
        if not slicer_exe or not Path(slicer_exe).is_file():
            problems.append("The slicer program was not found at that "
                            "path. Leave the field empty to let "
                            "AccessSlice look in the standard install "
                            "places.")
        elif (backend.exe_names
              and Path(slicer_exe).name.lower()
              not in tuple(n.lower() for n in backend.exe_names)):
            expected = " or ".join(backend.exe_names)
            detected = backend.detect_exe()
            problems.append(
                f"That file is not {backend.display_name}'s program - "
                f"its program is called {expected}."
                + (f" AccessSlice found it at {detected}; paste that, "
                   "or leave the field empty to use it."
                   if detected else ""))
        if problems:
            return problems, exe_note
        config.update({
            "slicer": slicer,
            "projects_dir": projects_dir,
            "slicer_exe": slicer_exe,
        })
        config_store.remember_exe(config, slicer, slicer_exe)
        config_store.save(config)
        return [], exe_note

    # ------------------------------------------------- setup wizard

    def _wizard_position(self, config, rest):
        """(printer row, steps, number, step) for /welcome/<n>, or None
        when no printer is chosen yet or n is out of range."""
        found = wizard.choice(config.get(wizard.CONFIG_KEY, ""))
        if not found or len(rest) != 1 or not rest[0].isdigit():
            return None
        steps = wizard.steps_for(found[0])
        number = int(rest[0])
        if not 1 <= number <= len(steps):
            return None
        return found, steps, number, steps[number - 1]

    def _get_wizard(self, config, rest):
        configured = config_store.is_complete(config)
        if not rest or rest == [""]:
            return self._send_html(webui.render_wizard_choose(
                wizard.PRINTERS, config.get(wizard.CONFIG_KEY, ""),
                configured=configured, msg=self._msg()))
        position = self._wizard_position(config, rest)
        if position is None:
            return self._redirect("/welcome")
        (_pid, name, slicer, tested), steps, number, step = position
        backend = backends.get_backend(slicer)
        kind = wizard.KINDS[step]
        values = {"slicer_name": backend.display_name}
        projects_dir = (config.get("projects_dir")
                        or str(config_store.DEFAULT_PROJECTS_DIR))
        text = ""
        if step == "install":
            text = wizard.install_html(config[wizard.CONFIG_KEY])
        elif step == "printer-network":
            text = wizard.PRUSA_NETWORK_HTML
        elif kind == "folders":
            detected = backend.detect_exe()
            values.update(
                projects_dir=projects_dir,
                slicer_exe=config_store.exe_for(config, slicer,
                                                backend.detect_exe),
                detected=detected or "",
                example=getattr(backend.texts, "EXE_EXAMPLE", ""))
        elif kind == "network":
            values.update(self._wizard_network_values(config, backend))
        elif kind == "project":
            text = wizard.project_html(
                slicer, projects_dir, backend.texts,
                model_file_available=backend.newjob is not None)
            values["projects"] = wizard.saved_projects(projects_dir)
        elif kind == "finish":
            values["checks"] = self._wizard_checks(config, backend, name,
                                                   tested)
        return self._send_html(webui.render_wizard_step(
            name, number, len(steps), wizard.title(step, slicer), kind,
            text_html=text, values=values, configured=configured,
            msg=self._msg()))

    @staticmethod
    def _wizard_network_values(config, backend):
        """The network step's fields, filled in from what is already
        known: the address the slicer recorded, else the remembered
        one; for PrusaLink the password likewise."""
        lan = backend.lan
        from_slicer = getattr(lan, "saved_device_address", lambda: "")()
        remembered = config.get("lan_printer_ips", {}).get(backend.id, "")
        values = {"ip": from_slicer or remembered,
                  "needs_key": getattr(lan, "NEEDS_KEY", False),
                  "key_label": getattr(lan, "KEY_LABEL", "Password"),
                  "key_help": getattr(lan, "KEY_HELP", "")}
        if from_slicer:
            values["found_note"] = (
                f"AccessSlice found the address in "
                f"{backend.display_name}'s own records and filled it "
                "in. Press Test to make sure the printer answers there.")
        if values["needs_key"]:
            keys = config.get("lan_printer_keys")
            values["key"] = ((keys.get(backend.id) if isinstance(keys, dict)
                              else "")
                             or getattr(lan, "saved_api_key",
                                        lambda: "")())
        return values

    @staticmethod
    def _wizard_checks(config, backend, printer_name, tested):
        """The finish step's sentences: what is set up and what is
        still missing."""
        lines = []
        exe = config.get("slicer_exe", "")
        if (config.get("slicer") == backend.id and exe
                and Path(exe).is_file()):
            lines.append(f"{backend.display_name} is installed at {exe}.")
        else:
            lines.append(f"{backend.display_name}'s program is not set "
                         "yet - the folders step sets it.")
        folder = config.get("projects_dir", "")
        projects = (wizard.saved_projects(folder, limit=None)
                    if folder else [])
        if projects:
            lines.append(f"The projects folder {folder} holds "
                         f"{len(projects)} project"
                         f"{'' if len(projects) == 1 else 's'}; the "
                         f"newest is {projects[0][0]}.")
        elif folder:
            lines.append(f"The projects folder is {folder}. It has no "
                         "project yet - save one from "
                         f"{backend.display_name} before the first job.")
        if backend.lan is not None:
            address = (config.get("lan_printer_ips", {}).get(backend.id)
                       or getattr(backend.lan, "saved_device_address",
                                  lambda: "")())
            lines.append(f"The printer's address is {address}."
                         if address else
                         "The printer's address is not known yet - "
                         "AccessSlice asks for it at the first send.")
        else:
            lines.append(f"Prints are sent through {backend.display_name}'s "
                         "own send window; AccessSlice tells you the keys.")
        lines.append(f"The {printer_name} "
                     + ("has been tested with AccessSlice." if tested else
                        "has not been tested with AccessSlice yet."))
        return lines

    def _post_wizard(self, config, rest, field):
        if not rest or rest == [""]:
            found = wizard.choice(field("printer"))
            if not found:
                return self._redirect("/welcome", msg=(
                    "Choose your printer first, then press Start the "
                    "wizard."))
            config[wizard.CONFIG_KEY] = found[0]
            config_store.save(config)
            return self._redirect("/welcome/1")
        position = self._wizard_position(config, rest)
        if position is None:
            return self._redirect("/welcome")
        (_pid, _name, slicer, _tested), steps, number, step = position
        here = f"/welcome/{number}"
        after = f"/welcome/{number + 1}"
        action = field("action")
        backend = backends.get_backend(slicer)
        if action == "folders":
            def wizard_field(name, default=""):
                return slicer if name == "slicer" else field(name, default)
            problems, exe_note = self._apply_setup(config, wizard_field)
            if problems:
                return self._redirect(here, msg=" ".join(problems))
            return self._redirect(after, msg=(
                "Saved." + (f" {exe_note}" if exe_note else "")))
        if action == "network":
            ip = field("ip")
            key = field("key")
            if not ip:
                return self._redirect(here, msg=(
                    "Type the printer's address first, or skip this "
                    "step."))
            if getattr(backend.lan, "NEEDS_KEY", False) and not key:
                return self._redirect(here, msg=(
                    "Type the printer's "
                    + getattr(backend.lan, "KEY_LABEL", "password")
                    + " too."))
            try:
                words = self._save_printer_connection(config, backend,
                                                      ip, key)
            except ProjectError as e:
                diagnostics.log(f"Wizard printer check failed: {e}")
                return self._redirect(here, msg=(
                    f"The printer did not answer: {e} Nothing was "
                    "saved. Check the address and try again, or skip "
                    "this step."))
            return self._redirect(after, msg=words)
        if action == "launch":
            try:
                launch_slicer(config)
            except ProjectError as e:
                return self._redirect(here, msg=str(e))
            return self._redirect(here, msg=(
                f"{backend.display_name} is starting. It can take a "
                "while to appear - check with Alt+Tab."))
        if action == "check":
            count = len(wizard.saved_projects(
                config.get("projects_dir", ""), limit=None))
            return self._redirect(here, msg=(
                f"Found {count} project{'' if count == 1 else 's'} in "
                "the projects folder." if count else
                "No project in the projects folder yet."))
        if action == "finish":
            if not config_store.is_complete(config):
                folders = steps.index("folders") + 1
                return self._redirect(f"/welcome/{folders}", msg=(
                    "One thing is still needed before AccessSlice can "
                    "start: the projects folder and the slicer "
                    "program."))
            config[wizard.DONE_KEY] = True
            config_store.save(config)
            diagnostics.log("Setup wizard finished for "
                            + config[wizard.CONFIG_KEY])
            target = "/" if config.get("tour_seen") else "/tour?first=1"
            return self._redirect(target, msg=(
                "Setup complete. AccessSlice is ready to use."))
        # Next and Skip simply move on.
        return self._redirect(after if number < len(steps) else here)

    def _open_project(self, path, config, note=""):
        # slicer_exe lets the session classify keys by scope and read the
        # slicer's preset library. Raises ProjectError with guidance.
        # note: prepended to the spoken open message (e.g. how a freshly
        # created project came to be).
        session = backend_for(config).open_project(
            path, slicer_exe=config.get("slicer_exe"))
        # Pages deep in the editor name the slicer through the session
        # (the stale banner, the dialog mirrors), the same way
        # stage_source rides on it.
        session.slicer_name = slicer_display_name(config)
        # A slice job (running or finished) belongs to the previous
        # project; never show its report against this one.
        job = state["slice_job"]
        if job and job.status == "running":
            job.cancel()
            job.wait(5)
            diagnostics.log("Slice cancelled (another project opened)")
        state["slice_job"] = None
        state["session"] = session
        config_store.remember_recent(config, session.path)
        # Auto-learn this project's printer and filaments into the short
        # picker lists - "what is loaded" without asking anyone.
        learn(config, "known_printers",
              [session.settings.get("printer_settings_id", "")])
        learn(config, "known_filaments",
              session.settings.get("filament_settings_id") or [])
        diagnostics.log(
            f"Opened {session.path.name} (written by "
            f"{session.application}, "
            f"{len(session.structure_notes)} structure note(s))")
        # Structure notes are spoken with the open message AND stay
        # visible on every editor page until the project is closed.
        notes = " ".join(session.structure_notes)
        return self._redirect(
            "/project/quality",
            msg=(f"{note} " if note else "")
                + f"Opened {session.path.name}. "
                + " ".join(session.summary())
                + (f" {notes}" if notes else ""))

    def _post_new_job(self, config, field):
        """Create a project from a model file - no slicer GUI.

        The donor-merge design (see app\\newjob.py): the newest project
        already saved for the chosen printer donates its known-good
        settings; the engine merges the model in; the donor's objects
        are removed again. Without a donor the answer is honest: use
        the guided Studio way once.
        """
        backend = backend_for(config)
        if backend.newjob is None:
            return self._redirect("/start", msg=(
                "Starting from a model file is not available for "
                f"{slicer_display_name(config)} yet - its engine "
                "crashes when handed a raw model file (a bug in the "
                "slicer itself, not in your file). Import the model "
                f"in {slicer_display_name(config)} once and save the "
                "project into the projects folder; everything after "
                "that happens here, including slicing."))
        printer = field("printer")
        model = field("model_path").strip().strip('"')
        if not model:
            return self._redirect("/start", msg=(
                "Type the full path of the model file first."))
        model_path = Path(model)
        if not model_path.is_file():
            return self._redirect("/start", msg=(
                f"No file was found at {model}. Check the path - it "
                "must include the folder and the file name."))
        if model_path.suffix.lower() not in backend.newjob.MODEL_SUFFIXES:
            return self._redirect("/start", msg=(
                f"{model_path.name} is not a model file AccessSlice "
                "can start from. It takes STL, STEP and OBJ files. "
                "A 3MF project opens directly from the Open a "
                "project page instead."))
        if not printer:
            return self._redirect("/start", msg=(
                "Choose which printer this job is for first."))
        exe = config.get("slicer_exe")
        if not exe or not Path(exe).is_file():
            return self._redirect("/start", msg=(
                "The slicer program was not found at the configured "
                "path. Check it on the Settings page."))

        projects_dir = Path(config["projects_dir"])
        projects_dir.mkdir(parents=True, exist_ok=True)
        candidates = sorted(projects_dir.glob("*.3mf"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        candidates = [p for p in candidates
                      if not p.name.endswith(".pre-edit-backup.3mf")]
        candidates += [Path(p) for p in config.get("recent", [])]
        donor = backend.newjob.find_donor(candidates, printer)
        if donor is None:
            return self._redirect("/start", msg=(
                f"No project saved for {printer} exists yet, and "
                "AccessSlice builds new projects on the settings of an "
                f"existing one. Use the {slicer_display_name(config)} "
                "way further down "
                "this page once - every job for this printer after "
                "that starts right here."))

        running = state["build_job"]
        if running and running.status == "running":
            return self._redirect("/building", msg=(
                f"{running.model_name} is still being built - one "
                "build at a time."))
        target = backend.newjob.unique_target(projects_dir, model_path)
        diagnostics.log(f"New job from {model_path.name} started "
                        f"(donor {donor.name})")
        job = build_job.BuildJob(
            lambda: backend.newjob.create_project(exe, donor,
                                                  [model_path], target),
            target, model_path.name)
        job.donor_name, job.printer = donor.name, printer
        state["build_job"] = job
        return self._redirect("/building")

    def _get_building(self, config):
        """The build's progress page while it runs; once it ends, the
        project opens (built) or the Start page says why (failed,
        cancelled). Reached again after that - a repeat of the
        page's last poll - it goes where the finished build went."""
        with state["lock"]:
            job = state["build_job"]
            if job is not None and job.status != "running":
                state["build_job"] = None
        if job is None:
            if state["session"]:
                return self._redirect("/project/quality")
            return self._redirect("/start")
        if job.status == "running":
            return self._send_html(webui.render_building(job,
                                                         msg=self._msg()))
        if job.status == "cancelled":
            return self._redirect("/start", msg=(
                f"Building the project from {job.model_name} was "
                "cancelled. Nothing was kept."))
        if job.status == "failed":
            diagnostics.log(f"New job from {job.model_name} failed: "
                            f"{job.error}")
            return self._redirect("/start", msg=f"Problem: {job.error}")
        diagnostics.log(f"New job: created {job.target.name} from "
                        f"{job.model_name} (donor {job.donor_name}) in "
                        f"{job.elapsed()} s")
        learn(config, "known_printers", [job.printer])
        try:
            return self._open_project(str(job.target), config,
                                      note=" ".join(job.notes))
        except ProjectError as e:
            return self._redirect("/start", msg=f"Problem: {e}")

    def _post_curated(self, session, tab_id, entries, form, field):
        staged = 0
        present = set(form.get("__present", []))
        for key, _label, _help, _unit, control, _options in entries:
            if key not in session.settings:
                continue
            if control == "checkbox":
                if key not in present:
                    continue           # control was not on the page
                new = "1" if key in form else "0"
            else:
                if key not in form:
                    continue
                new = field(key)
            current = session.settings[key]
            if isinstance(current, list):
                # A curated scalar control over a list value edits every
                # slot to the same value only if the user changed it.
                if new != (current[0] if current else ""):
                    if session.stage_setting(key, [new] * len(current)):
                        staged += 1
            else:
                if session.stage_setting(key, new):
                    staged += 1
        return self._staged_redirect(session, f"/project/{tab_id}", staged)

    def _staged_redirect(self, session, target, staged):
        """After staging: normal redirect, or Studio's infill question.

        Studio pops its 100%-density dialog the moment the bad
        combination appears; AccessSlice does the same by landing on
        the question page instead of back on the tab. Answering (or
        fixing the values by hand) clears it.
        """
        msg = self._staged_msg(staged, session)
        # A question page states its question itself, so its status
        # line carries only the count - the same words twice, once
        # in the live region and once as the heading, were what Edis
        # heard as "the buttons read the warning" (2026-09-21).
        short = self._staged_msg(staged, session, advice=False)
        unit = session.unit_conflict() if staged else None
        if unit:
            return self._redirect(
                "/project/unit-question?key="
                + urllib.parse.quote(unit["key"]) + "&return_to="
                + urllib.parse.quote(target), msg=short)
        if staged and session.infill_conflict():
            return self._redirect(
                "/project/infill-question?return_to="
                + urllib.parse.quote(target), msg=short)
        return self._redirect(target, msg=msg)

    @staticmethod
    def _preset_lists(session, config):
        """Preset names for the presets tab, filtered to this printer.

        The filtering grammar is the backend's - preset naming is a
        per-slicer fact (Bambu's "@BBL H2C" suffixes, for example).
        """
        return backend_for(config).preset_lists(
            config.get("slicer_exe"),
            session.settings.get("printer_settings_id", ""))

    def _preset_view(self, session, config):
        """Options for the presets tab: the user's own short lists.

        Selects show the presets in this project plus everything used
        before (learned lists in the config); the search fields add
        matches from the slicer's full library on demand.
        """
        full = self._preset_lists(session, config)
        query = self._query()
        fil_search = query.get("filament_search", [""])[0].strip()
        mach_search = query.get("printer_search", [""])[0].strip()

        fil_opts = []
        current_fils = session.settings.get("filament_settings_id") or []
        for name in list(current_fils) + known(config, "known_filaments"):
            if name and name not in fil_opts:
                fil_opts.append(name)

        mach_opts = []
        current_mach = session.settings.get("printer_settings_id", "")
        for name in [current_mach] + known(config, "known_printers"):
            if name and name not in mach_opts:
                mach_opts.append(name)

        return {
            "process": full.get("process", []),
            "filament_options": fil_opts,
            "filament_matches": (match_names(full.get("filament", []),
                                             fil_search)
                                 if fil_search else None),
            "filament_search": fil_search,
            "machine_options": mach_opts,
            "machine_matches": (match_names(full.get("machine", []),
                                            mach_search)
                                if mach_search else None),
            "printer_search": mach_search,
        }

    def _post_presets(self, session, field):
        staged = 0
        config = config_store.load()
        process = field("process_preset")
        if process and session.stage_preset("process", process):
            staged += 1
        machine = field("machine_preset")
        if machine and session.stage_preset("machine", machine):
            staged += 1
            learn(config, "known_printers", [machine])
        for i in range(session.filament_count()):
            name = field(f"filament_preset__{i}")
            if name and session.stage_preset("filament", name,
                                             filament_index=i):
                staged += 1
                learn(config, "known_filaments", [name])
        # The which-filament-prints-each-object chooser also lives on
        # this tab (ext__<objectid>), same as on the Filaments tab.
        for o in session.objects():
            value = field(f"ext__{o['id']}")
            if value and session.stage_extruder(o["id"], value):
                staged += 1
        return self._staged_redirect(session, "/project/presets", staged)

    def _post_filaments(self, session, form, field):
        staged = 0
        # Per-filament settings arrive as key__i where i is the FILAMENT
        # index (0-based). The underlying list may hold one value per
        # filament or one block of k values per filament (H2C nozzle
        # variants); one field writes the filament's whole block, exactly
        # like the slicer GUI's single per-filament field.
        n = session.filament_count()
        list_edits = {}
        for name, values in form.items():
            if "__" not in name:
                continue
            base, _, idx = name.rpartition("__")
            if base in session.settings and idx.isdigit():
                list_edits.setdefault(base, {})[int(idx)] = values[0].strip()
        for key, by_index in list_edits.items():
            current = session.settings[key]
            if not isinstance(current, list) or n == 0 or len(current) % n:
                continue
            k = len(current) // n
            new = list(current)
            for i, v in by_index.items():
                # Only rewrite a filament's block when its field actually
                # changed from the value shown (the block's first entry);
                # an untouched field must never flatten per-variant values.
                if 0 <= i < n and v != current[i * k]:
                    new[i * k:(i + 1) * k] = [v] * k
            if session.stage_setting(key, new):
                staged += 1

        # The filament-to-nozzle maps: map__<i> on single-plate
        # projects, map__<plate>__<i> when there are several plates.
        plates = session.plates()
        multi_plate = len(plates) > 1
        for plate_entry in (plates or [{"id": None}]):
            pid = plate_entry["id"]
            _mode, fmap = session.filament_map(pid)
            if not fmap:
                continue
            prefix = f"map__{pid}__" if multi_plate else "map__"
            new_map = [field(f"{prefix}{i}", fmap[i])
                       for i in range(len(fmap))]
            if session.stage_filament_map(new_map, plate_id=pid):
                staged += 1

        # Per-object filament: ext__<objectid>.
        for o in session.objects():
            value = field(f"ext__{o['id']}")
            if value and session.stage_extruder(o["id"], value):
                staged += 1

        return self._redirect("/project/filaments",
                              msg=self._staged_msg(staged, session))

    def _post_flushing(self, session, form, field):
        """The flushing-and-prime-tower page: matrix, multiplier,
        waste destinations, prime tower fields - staged like any edit."""
        staged = 0
        n = session.filament_count()

        def numeric(name, value, allow_negative=False):
            try:
                v = float(value)
            except ValueError:
                raise ProjectError(
                    f"{name} needs a plain number; got {value!r}.")
            if v < 0 and not allow_negative:
                raise ProjectError(f"{name} cannot be negative.")
            return value.strip()

        # The matrix: fields flush__<extruder>_<from>_<to>, diagonal
        # untouched. Only rebuild when the page actually carried a grid.
        matrix = session.settings.get(session.catalog.FLUSH_MATRIX_KEY)
        grid_fields = {name: values[0] for name, values in form.items()
                       if name.startswith("flush__")}
        if (grid_fields and isinstance(matrix, list) and n
                and len(matrix) % (n * n) == 0):
            new = list(session.effective_setting(session.catalog.FLUSH_MATRIX_KEY))
            for name, value in grid_fields.items():
                try:
                    e, i, j = (int(p) for p in
                               name[len("flush__"):].split("_"))
                except ValueError:
                    continue
                pos = e * n * n + i * n + j
                if 0 <= pos < len(new) and i != j:
                    new[pos] = numeric(
                        f"Flush volume from filament {i + 1} to "
                        f"{j + 1}", value)
            if session.stage_setting(session.catalog.FLUSH_MATRIX_KEY, new):
                staged += 1

        multiplier = session.settings.get(session.catalog.FLUSH_MULTIPLIER_KEY)
        if isinstance(multiplier, list):
            new_mult = list(
                session.effective_setting(session.catalog.FLUSH_MULTIPLIER_KEY))
            changed = False
            for e in range(len(new_mult)):
                v = field(f"flushmult__{e}")
                if v and v != new_mult[e]:
                    new_mult[e] = numeric(f"Flush multiplier {e + 1}", v)
                    changed = True
            if changed and session.stage_setting(
                    session.catalog.FLUSH_MULTIPLIER_KEY, new_mult):
                staged += 1

        present = set(form.get("__present", []))
        for key, _label, _help in session.catalog.FLUSH_TOGGLES:
            if key in session.settings and key in present:
                new_v = "1" if key in form else "0"
                if session.stage_setting(key, new_v):
                    staged += 1

        for key, _label, _help, _unit, control, _options in session.catalog.PRIME_TOWER:
            if key not in session.settings:
                continue
            if control == "checkbox":
                if key not in present:
                    continue
                new_v = "1" if key in form else "0"
            else:
                new_v = field(key)
                if not new_v:
                    continue
                new_v = numeric(session.catalog.label_for(key), new_v,
                                allow_negative=True)
            current = session.settings[key]
            if isinstance(current, list):
                if new_v != (current[0] if current else ""):
                    if session.stage_setting(key, [new_v] * len(current)):
                        staged += 1
            elif session.stage_setting(key, new_v):
                staged += 1

        return self._redirect("/project/flushing",
                              msg=self._staged_msg(staged, session))

    def _post_placement(self, session, form, field):
        # The automatic commands are buttons: when one was pressed it
        # is the whole request - the position fields on the page are
        # ignored, so a half-typed number never sneaks into an
        # automatic result.
        if field("arrange") or field("orient") or field("orient_plate"):
            return self._post_auto_placement(session, field)
        if (field("copy") or field("remove") or field("add_model")
                or field("arrange_spill")):
            return self._post_object_ops(session, field)
        staged = 0
        warning = ""
        plate = session.plate_size()
        centre_id = field("centre")
        plate_of = {oid: p["id"] for p in session.plates()
                    for oid in p["object_ids"]}
        for o in session.objects():
            # Fields hold PLATE-LOCAL coordinates; the file stores world
            # coordinates (plates tile in a grid), so convert here.
            origin = None
            pid = plate_of.get(o["id"])
            if pid is not None:
                origin = session.plate_origin(pid)
            ox, oy = origin if origin else (0.0, 0.0)
            if centre_id == o["id"] and plate:
                lx, ly = plate[0] / 2, plate[1] / 2
            else:
                try:
                    lx = float(field(f"x__{o['id']}", str(o["x"] - ox)))
                    ly = float(field(f"y__{o['id']}", str(o["y"] - oy)))
                except ValueError:
                    raise ProjectError(
                        f"The position for {o['name']} must be numbers "
                        "in millimetres.")
            if session.stage_move(o["id"], lx + ox, ly + oy):
                staged += 1
                if plate and not (0 <= lx <= plate[0]
                                  and 0 <= ly <= plate[1]):
                    warning = (f" Warning: {o['name']} is now outside the "
                               f"{plate[0]:g} by {plate[1]:g} mm plate.")
            # Move to another plate: the select's baseline is the
            # current plate, so an untouched chooser stages nothing.
            target = field(f"replate__{o['id']}")
            new_staged = any(c["kind"] == "replate_new"
                             and c["objectid"] == o["id"]
                             for c in session.pending)
            if target == "new":
                if not new_staged:
                    session.stage_new_plate(o["id"])
                    staged += 1
            elif target and pid is not None and (target != pid
                                                 or new_staged):
                if new_staged and target == pid:
                    # Back to the current plate: drop the staged move.
                    session.pending = [
                        c for c in session.pending
                        if not (c["kind"] == "replate_new"
                                and c["objectid"] == o["id"])]
                    staged += 1
                elif session.stage_replate(o["id"], target):
                    staged += 1
            # Rotation: a non-zero value means "turn it by this much".
            rot = field(f"rot__{o['id']}", "0") or "0"
            if session.stage_rotate(o["id"], rot):
                staged += 1
            # Size: only an actually edited field stages (string
            # comparison against what the page showed, so float noise
            # never re-stages an untouched object).
            size = field(f"scale__{o['id']}")
            base = field(f"basescale__{o['id']}")
            if size and size != base and session.stage_scale(o["id"], size):
                staged += 1
        return self._redirect("/project/placement",
                              msg=self._staged_msg(staged, session) + warning)

    def _post_object_ops(self, session, field):
        """Copies, removal, adding a model file, and the arrange that
        spills onto new plates - all staged, all reviewable."""
        names = {o["id"]: o["name"] for o in session.objects()}
        try:
            if field("copy"):
                oid = field("copy")
                count, notes = session.stage_copy(
                    oid, field(f"copies__{oid}", "1") or "1")
                plural = "copies" if count != 1 else "copy"
                msg = (f"Staged {count} {plural} of "
                       f"{names.get(oid, 'the object')}, laid out with "
                       "the rest of the plate. They appear as objects "
                       "after you save." + "".join(" " + n for n in notes))
            elif field("remove"):
                oid = field("remove")
                if session.stage_remove(oid):
                    msg = (f"Staged: {names.get(oid, 'the object')} will "
                           "be removed when you save - any other staged "
                           "change to it was dropped. Discard on Review "
                           "changes to keep it.")
                else:
                    msg = (f"{names.get(oid, 'The object')} is already "
                           "staged for removal.")
            elif field("add_model"):
                pid = field("add_model")
                path = field(f"addmodel__{pid}", "")
                if not path.strip():
                    raise ProjectError("Type the model file's full path "
                                       "first.")
                config = config_store.load()
                added, notes = session.stage_add_model(
                    path, config.get("slicer_exe"),
                    None if pid == "all" else pid)
                msg = ("Staged: " + ", ".join(added) + " will be added "
                       "and laid out with the rest of the plate when "
                       "you save." + "".join(" " + n for n in notes))
            else:
                pid = field("arrange_spill")
                staged, notes = session.stage_arrange_spill(
                    None if pid == "all" else pid)
                msg = (f"Staged a spot for {staged} object(s); what did "
                       "not fit goes onto new plates when you save."
                       + "".join(" " + n for n in notes))
        except ProjectError as e:
            msg = f"Nothing staged: {e}"
        return self._redirect("/project/placement", msg=msg)

    def _post_auto_placement(self, session, field):
        """The Placement tab's automatic commands: arrange one plate,
        or auto-orient one object or a whole plate. Every result is
        staged, never written - the same review-then-save flow as any
        other change."""
        names = {o["id"]: o["name"] for o in session.objects()}
        try:
            if field("arrange"):
                pid = field("arrange")
                staged, notes = session.stage_arrange(
                    None if pid == "all" else pid)
                msg = (f"Staged a fresh spot for {staged} object(s), at "
                       f"least {geometry.ARRANGE_GAP_MM:g} mm apart, "
                       "centred on the plate."
                       if staged else
                       "Everything already sits where arranging would "
                       "put it - nothing staged.")
                msg += "".join(" " + n for n in notes)
            elif field("orient"):
                oid = field("orient")
                name = names.get(oid, f"object {oid}")
                if session.stage_orient(oid):
                    if session.orient_is_drop(oid):
                        msg = (f"Staged: {name} already lies well but "
                               "was off the plate - it will be set "
                               "down onto the plate.")
                    else:
                        msg = (f"Staged: {name} will be turned so its "
                               "most printable side lies on the plate.")
                else:
                    msg = (f"{name} already lies with its most "
                           "printable side down - nothing to change.")
            else:
                pid = field("orient_plate")
                members = next(
                    (p["object_ids"] for p in session.plates()
                     if p["id"] == pid), None)
                ids = ([oid for oid in names if oid in members]
                       if members else list(names))
                turned, dropped, fine, problems = [], [], [], []
                for oid in ids:
                    try:
                        if not session.stage_orient(oid):
                            fine.append(names[oid])
                        elif session.orient_is_drop(oid):
                            dropped.append(names[oid])
                        else:
                            turned.append(names[oid])
                    except ProjectError as e:
                        problems.append(str(e))
                bits = []
                if turned:
                    bits.append("Staged a better lying direction for "
                                + ", ".join(turned) + ".")
                if dropped:
                    bits.append("Staged setting " + ", ".join(dropped)
                                + " down onto the plate (the lie was "
                                "fine, the height was not).")
                if fine:
                    bits.append(", ".join(fine)
                                + (" already lie" if len(fine) > 1
                                   else " already lies")
                                + " well - nothing to change.")
                bits.extend(problems)
                msg = " ".join(bits) or "There is nothing on this plate."
            if session.pending:
                msg += (f" {len(session.pending)} change(s) waiting in "
                        "total - review and save to apply.")
        except ProjectError as e:
            msg = f"Problem: {e}"
        return self._redirect("/project/placement", msg=msg)

    def _post_object(self, session, objectid, form, field):
        """Stage per-object overrides from the object settings page.

        Fields arrive as obj__<key> with base__obj__<key> carrying the
        value that was shown, so only actual edits stage. Companion
        keys (skin/skeleton infill with sparse density) are staged
        alongside, exactly as Studio's own GUI writes them.
        """
        OBJECT_COMPANION_KEYS = session.catalog.OBJECT_COMPANION_KEYS
        label_for = session.catalog.label_for
        if objectid not in {o["id"] for o in session.objects()}:
            return self._redirect("/project/placement",
                                  msg=f"No object with id {objectid}.")
        target = f"/project/object/{objectid}"

        remove = field("remove")
        if remove:
            removed = session.stage_object_setting(objectid, remove, None)
            companions = [
                comp for comp in OBJECT_COMPANION_KEYS.get(remove, ())
                if session.stage_object_setting(objectid, comp, None)]
            if not removed:
                return self._redirect(target, msg=(
                    "That override is not set - nothing to remove."))
            note = (" Its companion values were removed with it."
                    if companions else "")
            total = len(session.pending)
            return self._redirect(target, msg=(
                f"Removal of the {label_for(remove)} override staged."
                f"{note} {total} change(s) waiting - review and save "
                "when ready."))

        present = set(form.get("__present", []))
        keys = {n[len("obj__"):] for n in form if n.startswith("obj__")}
        keys |= {n[len("obj__"):] for n in present
                 if n.startswith("obj__")}
        staged = 0
        for key in sorted(keys):
            marker = f"obj__{key}"
            baseline = field(f"base__{marker}")
            if marker in present:
                new = "1" if marker in form else "0"
            elif marker in form:
                new = field(marker)
            else:
                continue
            if new == baseline:
                continue
            if session.stage_object_setting(objectid, key, new):
                staged += 1
                for comp in OBJECT_COMPANION_KEYS.get(key, ()):
                    if (comp in session.settings
                            and session.stage_object_setting(
                                objectid, comp, new)):
                        staged += 1
        return self._staged_redirect(session, target, staged)

    def _post_infill_answer(self, session, field):
        """Studio's 100%-infill dialog answers, staged the normal way.

        Yes - switch the pattern to rectilinear (stored as zig-zag).
        No - reset the density to the print profile's default value.
        Exactly the two actions Bambu Studio's own dialog performs.
        """
        return_to = field("return_to", "/project/strength")
        if not return_to.startswith("/project/"):
            return_to = "/project/strength"
        conflict = session.infill_conflict()
        if not conflict:
            return self._redirect(return_to, msg=(
                "The infill combination is fine now - nothing changed."))
        if field("answer") == "yes":
            session.stage_setting(session.PATTERN_KEY,
                                  session.SOLID_PATTERN)
            done = ("Infill pattern switched to rectilinear "
                    f"({session.SOLID_PATTERN}) - staged.")
        else:
            default = session.default_infill_density()
            session.stage_setting(session.DENSITY_KEY, default)
            done = f"Infill density reset to {default} - staged."
        total = len(session.pending)
        return self._redirect(return_to, msg=(
            f"{done} {total} change(s) waiting - review and save when "
            "ready."))

    def _post_unit_answer(self, session, field):
        """Studio's mm-or-% dialog answers.

        Yes - the number was a percentage: % is appended to the staged
        value. No - it was millimetres: the value stays as typed. Either
        way the change is marked confirmed so it is not asked again.
        """
        return_to = field("return_to", "/project/quality")
        if not return_to.startswith("/project/"):
            return_to = "/project/quality"
        key = field("key")
        new = session.answer_unit(key, field("answer") == "yes")
        if new is None:
            return self._redirect(return_to, msg=(
                "That change is no longer staged - nothing to clear up."))
        total = len(session.pending)
        return self._redirect(return_to, msg=(
            f"{key} staged as {new}. {total} change(s) waiting - review "
            "and save when ready."))

    def _post_setting(self, session, key, form, field):
        if key not in session.settings:
            return self._redirect("/project/all",
                                  msg=f"No setting named {key}.")
        current = session.settings[key]
        if isinstance(current, list):
            new = [field(f"{key}__{i}", current[i])
                   for i in range(len(current))]
            changed = session.stage_setting(key, new)
        else:
            present = set(form.get("__present", []))
            control, _options = session.catalog.control_for(key, current)
            if control == "checkbox" and key in present:
                new = "1" if key in form else "0"
            else:
                new = field(key)
            changed = session.stage_setting(key, new)
        if changed:
            return self._staged_redirect(
                session, f"/project/setting/{urllib.parse.quote(key)}", 1)
        msg = f"{key} is unchanged."
        return self._redirect(f"/project/setting/{urllib.parse.quote(key)}",
                              msg=msg)

    @staticmethod
    def _stage_source(parts, field, tabs):
        """The editor page a staging POST's form lives on, or None.

        Stamped onto each staged change (via session.stage_source) so
        the Review tab's Discard can land back on that page instead of
        leaving the user stranded on Review (Edis, 2026-08-31).
        """
        if parts[1] in ("infill-answer", "unit-answer"):
            rt = field("return_to")
            return rt if rt.startswith("/project/") else None
        if parts[1] in ("setting", "object") and len(parts) == 3:
            return f"/project/{parts[1]}/{parts[2]}"
        pages = {tab_id for tab_id, _name, _entries in tabs}
        pages |= {"filaments", "flushing", "presets", "placement"}
        if parts[1] in pages:
            return f"/project/{parts[1]}"
        return None

    def _post_discard(self, session, form, field):
        """The Review tab's discard controls: one change (landing back
        on the page it was staged from), the ticked ones, or all."""
        if field("all"):
            labels = session.discard_many(range(len(session.pending)))
            if not labels:
                return self._redirect(
                    "/project/review",
                    msg="Nothing is staged - nothing to discard.")
            n = len(labels)
            return self._redirect("/project/review", msg=(
                f"All {n} staged change{'s' if n != 1 else ''} "
                "discarded: " + "; ".join(labels)
                + ". Nothing is waiting now."))
        if field("selected"):
            indexes = []
            for v in form.get("drop", []):
                try:
                    indexes.append(int(v))
                except ValueError:
                    pass
            labels = session.discard_many(indexes)
            if not labels:
                return self._redirect("/project/review", msg=(
                    "No change was ticked. Tick the changes to drop, "
                    "then press the button again - or use Discard all."))
            left = len(session.pending)
            return self._redirect("/project/review", msg=(
                f"Discarded {len(labels)}: " + "; ".join(labels) + ". "
                + (f"{left} change(s) still staged."
                   if left else "Nothing is waiting now.")))
        try:
            index = int(field("index", "-1"))
        except ValueError:
            index = -1
        change = session.discard_pending(index)
        if change is None:
            return self._redirect("/project/review",
                                  msg="That change is no longer staged.")
        target = change.get("source") or ""
        if not target.startswith("/project/"):
            return self._redirect(
                "/project/review", msg=f"Discarded: {change['label']}.")
        # Name the landing page when it is a plain tab; other pages
        # (a single setting, one object) describe themselves.
        tab_names = dict(webui.EDITOR_TABS)
        tab_names["flushing"] = "Flushing and prime tower"
        name = tab_names.get(target[len("/project/"):])
        where = (f"You are back on the {name} tab."
                 if name else "You are back on the page it came from.")
        return self._redirect(
            target, msg=f"Discarded: {change['label']}. {where}")

    def _hand_to_slicer(self, session, config, file_path, field, sliced):
        """Open file_path in the slicer, its Device page first pointed
        at the project's printer when that is possible.

        The switch edits the slicer's conf file, which only takes while
        the slicer is closed (it writes the file back on exit). Slicer
        open and the Device page on another printer: the question page
        asks to close it first, or to go on and pick the printer in the
        send window (form field device=keep). device=switch is the
        question's first button - checked again here, so a slicer
        still running sends the question back with a status line.

        A send while the slicer is already open opens a SECOND window
        with the fresh file; the old one keeps showing the stale
        version (observed with real Studio, 2026-08-31). The handoff
        page warns about it when told.
        """
        machine = session.settings.get("printer_settings_id", "")
        backend = backend_for(config)
        exe = config.get("slicer_exe")
        already = backend.slicer_running(exe)
        question = "/project/send-device" + ("?sliced=1" if sliced else "")
        outcome = ""
        plan = device_plan(config, machine)
        if plan and plan["state"] == "other":
            if already and field("device") != "keep":
                if field("device") == "switch":
                    return self._redirect(question, msg=(
                        f"{slicer_display_name(config)} is still "
                        "running - close its window (Alt+F4 in it), "
                        "then press the first button again."))
                return self._redirect(question)
            if already:
                outcome = "kept"
            else:
                try:
                    msgs = plan["switch"].set_printer(
                        machine, exe, plan["serials"])
                    diagnostics.log("Studio printer switch before the "
                                    "send: " + " ".join(msgs))
                    outcome = "switched"
                except plan["switch"].StudioConfError as e:
                    diagnostics.log(f"Studio printer switch before the "
                                    f"send failed: {e}")
                    outcome = "kept"
        elif plan:
            outcome = "kept"  # no serial known: only the user can move it
            diagnostics.log(f"No device serial known for "
                            f"{plan['printer']}; the send window may "
                            "name another printer")
        launch_slicer(config, file_path)
        query = [q for q, on in (("sliced=1", sliced),
                                 ("second=1", already),
                                 (f"device={outcome}", outcome)) if on]
        return self._redirect("/handoff"
                              + ("?" + "&".join(query) if query else ""))

    def _post_send_lan(self, session, config, field):
        """Upload the sliced job to the printer itself and start it.

        The no-slicer-window send (K-series LAN). Runs only from the
        user's button press, and only with the plate-empty
        confirmation ticked - the same safety check the slicer's own
        dialog makes, in a form a screen reader can actually read.
        """
        backend = backend_for(config)
        if backend.lan is None:
            return self._redirect("/project/send", msg=(
                "Sending straight to the printer is not available "
                f"for {slicer_display_name(config)}."))
        job = state["slice_job"]
        out = job.output_file() if job else None
        if not out:
            return self._redirect("/project/send", msg=(
                "There is no sliced file yet - slice first."))
        ip = field("ip").strip()
        if not ip:
            return self._redirect("/project/slice-report", msg=(
                "Type the printer's IP address first - it is shown "
                "in the printer's own network settings."))
        needs_key = getattr(backend.lan, "NEEDS_KEY", False)
        key = field("key").strip() if needs_key else ""
        if needs_key and not key:
            return self._redirect("/project/slice-report", msg=(
                "Type the printer's "
                + getattr(backend.lan, "KEY_LABEL", "password")
                + " first. " + getattr(backend.lan, "KEY_HELP", "")))
        if field("plate_empty") != "yes":
            return self._redirect("/project/slice-report", msg=(
                "Nothing was sent. First check with your hands that "
                "the build plate is empty, then tick the "
                "confirmation - a leftover print on the plate can "
                "wreck the printer."))
        kwargs = {"key": key} if needs_key else {}
        # A cheap hello before a large upload: a wrong address or
        # password is heard about in a second, not after the upload.
        check = getattr(backend.lan, "check_printer", None)
        if check is not None:
            check(ip, **kwargs)
        remembered = self._address_to_remember(backend.lan, ip, kwargs)
        # A printer that would ignore the start (still showing its
        # finished print) or must not be interrupted: say so before
        # a large upload, with what to do at the printer.
        pre = getattr(backend.lan, "pre_send_problem", None)
        problem = pre(ip, **kwargs) if pre is not None else None
        if problem:
            diagnostics.log(f"LAN send refused before upload: {problem}")
            return self._redirect("/project/slice-report", msg=problem)
        name = backend.lan.safe_gcode_name(session.path.stem, out)
        _entry, data = backend.lan.extract_gcode(out)
        backend.lan.upload_gcode(ip, name, data, **kwargs)
        changed = False
        ips = config.get("lan_printer_ips")
        if not isinstance(ips, dict):
            ips = {}
        if ips.get(backend.id) != remembered:
            ips[backend.id] = remembered
            config["lan_printer_ips"] = ips
            changed = True
        if needs_key:
            keys = config.get("lan_printer_keys")
            if not isinstance(keys, dict):
                keys = {}
            if keys.get(backend.id) != key:
                keys[backend.id] = key
                config["lan_printer_keys"] = keys
                changed = True
        if changed:
            config_store.save(config)
        # The upload is on the printer's storage even if the start is
        # refused (busy printer) - the config is saved first so a
        # retry needs no retyping.
        backend.lan.start_print(ip, name, **kwargs)
        diagnostics.log(
            f"LAN send: uploaded {name} ({len(data) // 1024} KB) to "
            f"{ip} and asked it to start")
        return self._redirect("/printer", msg=(
            f"Uploaded {name} to the printer and asked it to start. "
            "The lines below are the printer's own report - check "
            "again any time."))

    def _post_copy_usb(self, session, config, field):
        """Copy the printable file to a USB drive (or any folder the
        user names) - the path everyone can walk without a slicer:
        the printer reads the drive itself."""
        backend = backend_for(config)
        job = state["slice_job"]
        out = job.output_file() if job else None
        if not out:
            return self._redirect("/project/send", msg=(
                "There is no sliced file yet - slice first."))
        target = field("drive").strip() or field("folder").strip()
        if not target:
            return self._redirect("/project/slice-report", msg=(
                "Choose a drive from the list, or type a folder to "
                "copy to. Nothing was copied."))
        extract = (backend.lan.extract_gcode
                   if getattr(backend, "usb_wants_plain_gcode", False)
                   and backend.lan is not None else None)
        copied = usbcopy.copy_to(out, target, extract=extract)
        diagnostics.log(f"Copied {copied.name} to {copied.parent}")
        return self._redirect("/project/slice-report", msg=(
            f"Copied {copied.name} to {copied.parent}. "
            + usbcopy.after_copy_note(target)))

    def _preview_target(self):
        """(sliced file, plates in it, chosen plate) for the newest
        finished slice, or None when there is nothing to preview."""
        job = state["slice_job"]
        if job is None or job.status != "done":
            return None
        out = job.output_file()
        if not out:
            return None
        try:
            plates = toolpath.plates_in_output(out)
        except Exception:
            plates = []
        if not plates:
            return None
        wanted = self._query().get("plate", [""])[0]
        if wanted not in plates:
            wanted = str(job.plate) if str(job.plate) in plates else plates[0]
        return out, plates, wanted

    def _preview_entry(self, config):
        target = self._preview_target()
        if target is None:
            return None
        out, _plates, plate = target
        session = state["session"]
        size = session.plate_size() if session else None
        return state["preview"].ensure(out, plate, size)

    def _preview_page(self, session, config, msg):
        """The sliced preview (Phase V): the layer facts in words, and
        the toolpath picture as their mirror. Needs a finished slice;
        the parse runs once in the background and the page refreshes
        itself until it is ready."""
        target = self._preview_target()
        if target is None:
            return self._redirect("/project/send", msg=(
                "Slice the project first - the preview reads the "
                "sliced file."))
        out, plates, plate = target
        entry = state["preview"].ensure(out, plate, session.plate_size())
        layer = self._query().get("layer", [""])[0]
        return self._send_html(webui.render_preview(
            session, entry, plates, layer_query=layer, msg=msg,
            sliced_name=out.name))

    def _post_slice(self, session, config, field):
        """Start a headless slice of the SAVED project file.

        Runs only from the user pressing "Slice now" (the
        never-act-uninvited rule holds); refuses with guidance while
        unsaved changes or an outside edit make the file's contents
        uncertain.
        """
        if session.pending:
            return self._redirect("/project/send")
        if session.is_stale():
            return self._redirect("/project/quality", msg=(
                "The project file changed on disk after AccessSlice "
                "read it - probably saved from "
                f"{slicer_display_name(config)}. Reload "
                "the file from disk (the warning on this page has the "
                "button) and check the settings before slicing."))
        job = state["slice_job"]
        if job and job.status == "running":
            return self._redirect("/project/slicing",
                                  msg="Slicing is already running.")
        exe = config.get("slicer_exe")
        if not exe or not Path(exe).is_file():
            raise ProjectError(
                "The slicer program was not found at the configured "
                "path. Check it on the Settings page.")
        plate = field("plate", "0")
        if not plate.isdigit():
            plate = "0"

        engine = backend_for(config).engine

        def log_outcome(finished):
            e = finished.elapsed()
            if finished.status == "done":
                r = finished.report
                diagnostics.log(
                    f"Slice finished on {session.path.name} in {e} s: "
                    f"{engine.friendly_duration(r['total_time_s'])}"
                    f" print time, {r['total_grams']:.1f} g, "
                    f"{len(r['warnings'])} warning(s)")
            elif finished.status != "cancelled":
                problem = (finished.report or {}).get("error_string") \
                    or (finished.report or {}).get("problem") or ""
                diagnostics.log(
                    f"Slice {finished.status} on {session.path.name} "
                    f"after {e} s: {problem}")

        state["preview"].reset()
        state["slice_job"] = engine.SliceJob(
            exe, session.path, plate=int(plate), on_finish=log_outcome)
        what = "all plates" if plate == "0" else f"plate {plate}"
        diagnostics.log(f"Slice started on {session.path.name} ({what})")
        return self._redirect("/project/slicing")

    @staticmethod
    def _staged_msg(staged, session, advice=True):
        total = len(session.pending)
        if staged == 0:
            return ("Nothing changed on this tab. "
                    + (f"{total} change(s) still staged." if total else ""))
        advice = " ".join(session.advice()) if advice else ""
        return (f"{staged} change(s) staged. {total} in total waiting - "
                "review and save when ready." + (f" {advice}" if advice
                                                 else ""))

    # -------------------------------------------------------- helpers

    def _serve_static(self, name):
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR)) or not target.is_file():
            self._redirect("/")
            return
        types = {".css": "text/css", ".js": "text/javascript"}
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         types.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _recent_entries(config):
        """Recent projects as (path, display line) with live details."""
        entries = []
        for raw in config.get("recent", []):
            p = Path(raw)
            if not p.is_file():
                continue
            when = datetime.datetime.fromtimestamp(p.stat().st_mtime)
            entries.append(
                (raw, f"{p.name} - last saved {when:%Y-%m-%d %H:%M}"))
        return entries

    @staticmethod
    def _folder_entries(config):
        """3MFs in the projects folder as (path, display), newest first."""
        folder = Path(config.get("projects_dir", ""))
        entries = []
        if folder.is_dir():
            files = [p for p in folder.glob("*.3mf")
                     if not p.name.endswith(".pre-edit-backup.3mf")]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files:
                when = datetime.datetime.fromtimestamp(p.stat().st_mtime)
                entries.append(
                    (str(p), f"{p.name} - saved {when:%Y-%m-%d %H:%M}"))
        return entries


def make_server(port):
    """The server bound to localhost only, not yet running.

    The caller runs serve_forever() (usually on its own thread) and
    stops it with shutdown() - the app window does exactly that when
    it closes, so nothing keeps running behind a closed window.
    """
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port):
    """Run the server forever on localhost only."""
    make_server(port).serve_forever()
