"""printer_control.py - drive the printer from the PC: command jobs.

The Prusa Accessible Control program (a separate wxPython window,
2026-09-09 to 2026-09-11) proved on the real Core One that a
screen-reader user can preheat, load, unload, move, home, calibrate
and query the printer with nothing but PrusaLink. On 2026-09-12 that
program's logic moved in here so AccessSlice has it on its own pages
- this module is its portable core (the window is gone; the pages
in webui.py and the routes in server.py replace it).

How a command reaches the printer: PrusaLink cannot send a single
G-code line. It CAN upload a file and print it. So every action
becomes a tiny .gcode file - a header that declares the printer model
(so no compatibility question stops it on the screen), an M117 that
shows the action's name on the printer, then the lines - uploaded to
a folder of its own on the USB drive, started as a print, watched
until it ends, and deleted. A few seconds of overhead per action,
one entry in the print history, and it only works while the printer
is idle: nothing here can reach a running print (pause, resume and
stop go straight through the job calls instead).

Facts learned on the real printer (Prusa Accessible Control LOG,
2026-09-09) that shape the builders:

- Load (M701) stops twice for the knob: "filament detected, continue"
  and after the purge "continue, purge more, help". Unload (M702)
  finishes by itself. PrusaLink cannot answer any prompt; the page
  says "press the knob on the printer" whenever the state is
  ATTENTION.
- Inside a command job the firmware leaves the nozzle at a 170 degree
  standby after M701 or M702, and nothing turns it off - so every
  filament job ends with M104 S0.
- Command files are deleted after the job; never while it is still
  active.

Everything stdlib-only, like the rest of AccessSlice. Nothing here
runs except from the user's explicit button press.
"""

import json
import re
import threading
import time

import diagnostics
import motion_guard
from errors import ProjectError

# Where the command files live on the printer's USB drive.
JOB_FOLDER = "accessslice"

# How often a running command's state is read (seconds); the tests
# turn it down against the simulator.
POLL_SECONDS = 1.0

# The header every command file starts with: the model check keeps
# the printer from asking a compatibility question that would need a
# tap on the screen.
DEFAULT_HEADER = [
    "; AccessSlice command job",
    "; printer_model = COREONE",
    'M862.3 P "COREONE" ; printer model check',
    'M862.6 P "Input shaper" ; firmware feature check',
]

# Prusa's own preheat presets for the Core One: nozzle, bed.
PREHEAT_PRESETS = {
    "PLA": (215, 60),
    "PETG": (230, 85),
    "ASA": (260, 105),
    "PC": (275, 110),
    "PVB": (215, 75),
    "ABS": (255, 100),
    "HIPS": (220, 100),
    "PP": (240, 100),
    "FLEX": (240, 50),
    "PA": (285, 105),
}

# Safe speeds in mm per minute.
FEED_XY = 3000
FEED_Z = 600
FEED_E = 300

# The printer's reach, for the go-to fields (Core One build volume)
# and the heater limits the fields are checked against.
LIMITS = {
    "x": (0, 250), "y": (0, 220), "z": (0, 270),
    "nozzle": (0, 290), "bed": (0, 120), "chamber": (0, 60),
    "fan": (0, 100), "step": (0.1, 200), "length": (0.1, 200),
    "frequency": (0, 200), "damping": (0, 1),
}

# Ready-made lines for the free G-code page: commands whose details
# Prusa documents thinly, kept off the main buttons until tried.
SNIPPETS = {
    "Beep test": "M300 S1000 P300",
    "Report firmware version (M115)": "M115",
    "Report current position (M114)": "M114",
    "Report settings (M503)": "M503",
    "Self test, all (M50)": "M50",
    "Input shaper calibration (M959)": "M959",
    "Filament stuck monitoring on (M591 S1)": "M591 S1",
    "Filament stuck monitoring off (M591 S0)": "M591 S0",
    "Chamber vent open (M870)": "M870 S1",
    "Chamber vent close (M870)": "M870 S0",
    "Chamber lights white (M151)": "M151 R255 G255 B255",
    "Chamber lights off (M151)": "M151 R0 G0 B0",
    "Filament change menu (M1600)": "M1600",
    "Preheat menu (M1700)": "M1700",
    "Show IP address on screen (M46)": "M46",
    "Enable bed levelling (M420 S1)": "M420 S1",
    "Disable bed levelling (M420 S0)": "M420 S0",
}


# ------------------------------------------------------ G-code builders
#
# Every builder returns a list of G-code lines without the header.
# Commands follow the Buddy firmware G-code reference:
# https://help.prusa3d.com/article/buddy-firmware-specific-g-code-commands_633112


def _t(value):
    """A number for G-code: no trailing zeros, never scientific."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def set_nozzle(temp, wait=False):
    return [f"{'M109' if wait else 'M104'} S{_t(temp)}"]


def set_bed(temp, wait=False):
    return [f"{'M190' if wait else 'M140'} S{_t(temp)}"]


def set_chamber(temp, wait=False):
    return [f"{'M191' if wait else 'M141'} S{_t(temp)}"]


def set_print_fan(percent):
    percent = max(0.0, min(100.0, float(percent)))
    if percent == 0:
        return ["M107"]
    return [f"M106 S{round(percent * 255 / 100)}"]


def preheat(nozzle, bed=None, chamber=None):
    lines = set_nozzle(nozzle)
    if bed is not None:
        lines += set_bed(bed)
    if chamber is not None:
        lines += set_chamber(chamber)
    return lines


def preheat_preset(name, nozzle_only=False):
    nozzle, bed = PREHEAT_PRESETS[name]
    return preheat(nozzle, None if nozzle_only else bed)


def cooldown():
    return ["M104 S0", "M140 S0", "M141 S0", "M107"]


def nozzle_off():
    """The nozzle heater off - the end of every filament job, because
    the firmware's load and unload dialogs leave a 170 degree standby
    that a command job would otherwise keep forever."""
    return ["M104 S0"]


def load_filament(temp=None, filament_name=None, cool_after=True):
    """Load filament to the nozzle. With a temperature the nozzle is
    heated and held first and M701 runs with W255 (no preheat
    dialog); without one the printer preheats by itself and may ask
    which filament on its screen."""
    lines = []
    cmd = "M701"
    if filament_name:
        cmd += f' S"{filament_name}"'
    if temp is not None:
        lines += set_nozzle(temp, wait=True)
        cmd += " W255"
    lines.append(cmd)
    if cool_after:
        lines += nozzle_off()
    return lines


def unload_filament(temp=None, ask_if_successful=False, cool_after=True):
    lines = []
    cmd = "M702"
    if temp is not None:
        lines += set_nozzle(temp, wait=True)
        cmd += " W255"
    if ask_if_successful:
        cmd += " I"
    lines.append(cmd)
    if cool_after:
        lines += nozzle_off()
    return lines


def load_filament_no_prompts(temp, fast_mm=60, slow_mm=40, purge_mm=20,
                             cool_after=True):
    """Load without the printer's load dialog: heat, then turn the
    extruder to pull the filament in. The filament must be pushed in
    until it stops against the gears first. Nothing asks a question
    on the screen; the printer does not record the filament type."""
    lines = set_nozzle(temp, wait=True) + [
        "M83",
        f"G1 E{_t(fast_mm)} F600",
        f"G1 E{_t(slow_mm)} F200",
        f"G1 E{_t(purge_mm)} F100",
        "M82",
    ]
    if cool_after:
        lines += nozzle_off()
    return lines


def change_filament(temp=None, filament_name=None, cool_after=True):
    # The nozzle stays hot between the unload and the load.
    return (unload_filament(temp, cool_after=False)
            + load_filament(temp, filament_name, cool_after=cool_after))


def purge_filament(temp, length=50, feed=FEED_E, cool_after=True):
    lines = set_nozzle(temp, wait=True) + [
        "M83", f"G1 E{_t(length)} F{_t(feed)}", "M82"]
    if cool_after:
        lines += nozzle_off()
    return lines


def autoload_filament():
    return ["M1701"]


def stuck_detection(on):
    return ["M591 S1" if on else "M591 S0"]


def move_relative(axis, distance, feed=None):
    axis = axis.upper()
    if axis not in ("X", "Y", "Z", "E"):
        raise ValueError(f"Unknown axis {axis}")
    if axis == "E":
        return ["M83", f"G1 E{_t(distance)} F{_t(feed or FEED_E)}", "M82"]
    if feed is None:
        feed = FEED_Z if axis == "Z" else FEED_XY
    return ["G91", f"G1 {axis}{_t(distance)} F{_t(feed)}", "G90"]


def move_absolute(x=None, y=None, z=None, feed=None):
    parts = []
    if x is not None:
        parts.append(f"X{_t(x)}")
    if y is not None:
        parts.append(f"Y{_t(y)}")
    if z is not None:
        parts.append(f"Z{_t(z)}")
    if not parts:
        raise ValueError("Give at least one coordinate")
    if feed is None:
        feed = FEED_Z if all(p.startswith("Z") for p in parts) else FEED_XY
    return ["G90", "G1 " + " ".join(parts) + f" F{_t(feed)}"]


def home(axes=""):
    """Home all axes, or only the ones named ('XY', 'Z')."""
    axes = "".join(a for a in axes.upper() if a in "XYZ")
    if not axes:
        return ["G28"]
    return ["G28 " + " ".join(axes)]


def park():
    return ["G27"]


def disable_motors(axes=""):
    axes = "".join(a for a in axes.upper() if a in "XYZE")
    return ["M18" + ("" if not axes else " " + " ".join(axes))]


def enable_motors():
    return ["M17"]


def quick_stop():
    return ["M410"]


def wait_for_moves():
    return ["M400"]


def mesh_bed_level():
    return ["G28", "G29"]


def z_align():
    return ["G162"]


def measure_z_height():
    return ["G64"]


def z_probe():
    return ["G30"]


def set_input_shaper(axis, frequency, damping=None, shaper_type=None):
    axis = axis.upper()
    if axis not in ("X", "Y"):
        raise ValueError("Input shaper axis must be X or Y")
    parts = [f"M593 {axis}"]
    if frequency is not None:
        parts.append(f"F{_t(frequency)}")
    if damping is not None:
        parts.append(f"D{_t(damping)}")
    if shaper_type is not None:
        parts.append(f"T{int(shaper_type)}")
    return [" ".join(parts)]


def beep(frequency=1000, duration_ms=300, volume=None):
    line = f"M300 S{int(frequency)} P{int(duration_ms)}"
    if volume is not None:
        line += f" V{int(volume)}"
    return [line]


def chamber_lights(r, g, b):
    return [f"M151 R{int(r)} G{int(g)} B{int(b)}"]


def save_settings():
    return ["M500"]


def reload_settings():
    return ["M501"]


def factory_defaults_settings():
    return ["M502"]


def custom(text):
    """Typed G-code as lines, blanks dropped."""
    return [line.strip() for line in text.splitlines() if line.strip()]


# ----------------------------------------------- the page's actions
#
# build_action turns a control-page form into (job name, lines).
# Bad input is a ProjectError in words, never a crash and never a
# silently corrected number.


def _number(field, name, label, lo, hi, default=None, required=True):
    raw = field(name)
    if raw == "":
        if default is not None or not required:
            return default
        raise ProjectError(
            f"Type {label[0].lower() + label[1:]} first.")
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        raise ProjectError(f"{label} must be a number, not '{raw}'.")
    if value < lo or value > hi:
        raise ProjectError(
            f"{label} must be between {_t(lo)} and {_t(hi)}, not "
            f"{_t(value)}.")
    return value


def _filament(field):
    """The filament type name for M701's S parameter: a preset name
    (PLA, PETG...) or blank."""
    name = field("filament").strip().upper()
    if not name:
        return None
    if not re.fullmatch(r"[A-Z0-9_-]{1,20}", name):
        raise ProjectError(
            "The filament type must be a short name such as PLA or "
            "PETG.")
    return name


def _filament_temp(field, default=215):
    """The nozzle temperature for a filament job: the typed one, else
    the preset's for the named filament, else the default."""
    typed = _number(field, "temp", "The nozzle temperature",
                    *LIMITS["nozzle"], required=False)
    if typed is not None:
        return typed
    name = _filament(field)
    if name in PREHEAT_PRESETS:
        return PREHEAT_PRESETS[name][0]
    return default


class Lines(list):
    """The lines of a command plus the notes the page should say
    when it starts (the lift before a home)."""
    notes = ()


def build_action(action, field, position=None, clear=False):
    """(job name, G-code lines) for a control-page action and its
    form fields (field(name) -> stripped string). ProjectError in
    words for an unknown action or a bad field.

    position: the head position read from the printer just before
    ({"x", "y", "z"}, or None when it gave none); every line goes
    through motion_guard against it, and a home from a low nozzle
    gets its lift first. clear: the user's hand check that the
    nozzle is clear of the plate, for a home when no height can be
    read."""
    name, lines = _build_action(action, field, position)
    plan = motion_guard.check(lines, position, clear=clear, what=name)
    out = Lines(lines)
    if plan.lift > 0:
        out = Lines(motion_guard.lift_lines(plan.lift) + list(lines))
        out.notes = (plan.lift_words,)
    return name, out


def _build_action(action, field, position=None):
    if action == "preheat":
        preset = field("preset").upper()
        if preset not in PREHEAT_PRESETS:
            raise ProjectError("Choose a preheat preset first.")
        nozzle_only = field("nozzle_only") == "yes"
        return (f"Preheat {preset}" + (" (nozzle only)" if nozzle_only
                                        else ""),
                preheat_preset(preset, nozzle_only))
    if action == "preheat-custom":
        nozzle = _number(field, "nozzle", "The nozzle temperature",
                         *LIMITS["nozzle"])
        bed = _number(field, "bed", "The bed temperature",
                      *LIMITS["bed"], required=False)
        chamber = _number(field, "chamber", "The chamber temperature",
                          *LIMITS["chamber"], required=False)
        return ("Preheat to your values", preheat(nozzle, bed, chamber))
    if action == "cooldown":
        return ("Cooldown", cooldown())
    if action == "nozzle":
        temp = _number(field, "temp", "The nozzle temperature",
                       *LIMITS["nozzle"])
        return (f"Nozzle to {_t(temp)} degrees", set_nozzle(temp))
    if action == "bed":
        temp = _number(field, "temp", "The bed temperature",
                       *LIMITS["bed"])
        return (f"Bed to {_t(temp)} degrees", set_bed(temp))
    if action == "chamber":
        temp = _number(field, "temp", "The chamber temperature",
                       *LIMITS["chamber"])
        return (f"Chamber to {_t(temp)} degrees", set_chamber(temp))
    if action == "fan":
        percent = _number(field, "percent", "The fan speed",
                          *LIMITS["fan"])
        return (f"Print fan to {_t(percent)} percent",
                set_print_fan(percent))

    if action == "load":
        name = _filament(field)
        temp = _filament_temp(field)
        return ("Load filament" + (f" ({name})" if name else ""),
                load_filament(temp, name))
    if action == "unload":
        temp = _filament_temp(field)
        return ("Unload filament", unload_filament(temp))
    if action == "change":
        name = _filament(field)
        temp = _filament_temp(field)
        return ("Change filament", change_filament(temp, name))
    if action == "purge":
        temp = _filament_temp(field)
        length = _number(field, "length", "The purge length",
                         *LIMITS["length"], default=50)
        return (f"Purge {_t(length)} mm", purge_filament(temp, length))
    if action == "autoload":
        return ("Autoload filament", autoload_filament())
    if action == "load-no-prompts":
        temp = _filament_temp(field)
        return ("Load without prompts", load_filament_no_prompts(temp))
    if action == "stuck-on":
        return ("Stuck filament detection on", stuck_detection(True))
    if action == "stuck-off":
        return ("Stuck filament detection off", stuck_detection(False))

    if action == "move":
        # The page's one move form: the pressed button's value is
        # "X-", "Z+" and so on; axis and direction fields work too.
        go = field("go").upper()
        axis = go[:1] if go else field("axis").upper()
        if axis not in ("X", "Y", "Z"):
            raise ProjectError("Choose the axis to move: X, Y or Z.")
        step = _number(field, "step", "The step", *LIMITS["step"],
                       default=10)
        sign = -1 if (go[1:] == "-" if go
                      else field("direction") == "minus") else 1
        word = ("right" if sign > 0 else "left") if axis == "X" else \
            ("back" if sign > 0 else "forward") if axis == "Y" else \
            ("up" if sign > 0 else "down")
        return (f"Move {axis} {_t(step)} mm {word}",
                move_relative(axis, sign * step))
    if action in ("extrude", "retract"):
        length = _number(field, "length", "The length",
                         *LIMITS["length"], default=5)
        sign = 1 if action == "extrude" else -1
        return (f"{action.capitalize()} {_t(length)} mm",
                move_relative("E", sign * length))
    if action == "goto":
        x = _number(field, "x", "X", *LIMITS["x"], required=False)
        y = _number(field, "y", "Y", *LIMITS["y"], required=False)
        z = _number(field, "z", "Z", *LIMITS["z"], required=False)
        if x is None and y is None and z is None:
            raise ProjectError("Type at least one of X, Y and Z to go "
                               "to.")
        where = ", ".join(f"{a} {_t(v)}" for a, v in
                          (("X", x), ("Y", y), ("Z", z)) if v is not None)
        return (f"Go to {where}",
                motion_guard.goto_lines(position, x, y, z, FEED_XY, FEED_Z))
    if action == "home":
        axes = "".join(a for a in field("axes").upper() if a in "XYZ")
        label = {"": "Home all axes", "XY": "Home X and Y",
                 "Z": "Home Z"}.get(axes, f"Home {' and '.join(axes)}")
        return (label, home(axes))
    if action == "park":
        return ("Park the head", park())
    if action == "motors-off":
        return ("Disable motors", disable_motors())
    if action == "motors-on":
        return ("Enable motors", enable_motors())
    if action == "quick-stop":
        return ("Quick stop", quick_stop())

    if action == "mesh-level":
        return ("Mesh bed levelling", mesh_bed_level())
    if action == "z-align":
        return ("Z axis alignment", z_align())
    if action == "z-height":
        return ("Measure Z height", measure_z_height())
    if action == "z-probe":
        return ("Single Z probe", z_probe())
    if action == "shaper":
        axis = field("axis").upper()
        if axis not in ("X", "Y"):
            raise ProjectError("Choose the input shaper axis: X or Y.")
        frequency = _number(field, "frequency", "The frequency",
                            *LIMITS["frequency"])
        damping = _number(field, "damping", "The damping",
                          *LIMITS["damping"], required=False)
        shaper_type = _number(field, "type", "The filter type", 0, 6,
                              required=False)
        return (f"Input shaper {axis} to {_t(frequency)} Hz",
                set_input_shaper(axis, frequency, damping,
                                 int(shaper_type)
                                 if shaper_type is not None else None))

    if action == "beep":
        return ("Beep", beep())
    if action == "lights-on":
        return ("Chamber lights on", chamber_lights(255, 255, 255))
    if action == "lights-off":
        return ("Chamber lights off", chamber_lights(0, 0, 0))
    if action == "save-settings":
        return ("Save the printer's settings (M500)", save_settings())
    if action == "reload-settings":
        return ("Reload the saved settings (M501)", reload_settings())
    if action == "factory-settings":
        if field("confirm") != "yes":
            raise ProjectError(
                "Nothing was sent. Tick the confirmation first - "
                "restoring the factory values loses every tuned "
                "setting on the printer (calibrations stay).")
        return ("Restore the factory settings (M502)",
                factory_defaults_settings())

    if action == "gcode":
        lines = custom(field("gcode"))
        if not lines:
            raise ProjectError("Type at least one G-code line first.")
        return (f"Your G-code ({len(lines)} line"
                + ("s" if len(lines) != 1 else "") + ")", lines)
    if action == "snippet":
        name = field("snippet")
        if name not in SNIPPETS:
            raise ProjectError("Choose a ready-made command first.")
        return (name, custom(SNIPPETS[name]))
    raise ProjectError(f"AccessSlice knows no printer action '{action}'.")


# ----------------------------------------------------- command jobs


def slug(name):
    text = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return text[:40] or "command"


def describe_state(state):
    words = {
        "IDLE": "The printer is idle",
        "READY": "The printer is ready",
        "BUSY": "The printer is busy",
        "PRINTING": "The printer is printing",
        "PAUSED": "The print is paused",
        "FINISHED": "The print is finished",
        "STOPPED": "The print was stopped",
        "ERROR": "The printer reports an error",
        "ATTENTION": "The printer is asking a question on its screen",
    }
    if not state:
        return "The printer state is unknown"
    return words.get(str(state).upper(),
                     f"The printer state is {str(state).lower()}")


def job_text(name, lines, header=None, show_name=True):
    """The command file's text: header, the name on the printer's
    screen, the lines."""
    body = []
    if show_name:
        body.append(f"M117 {name[:40]}")
    return "\n".join(list(header or DEFAULT_HEADER) + body + lines) + "\n"


class CommandJob:
    """One action on its way through the printer, on its own thread.

    events: what happened, in order, each {"when", "kind", "message"}
    - kinds: started, uploaded, running, attention, finished, failed,
    cleaned. snapshot() is what the page's poll reads.
    """

    def __init__(self, lan, ip, key, name, lines, header=None,
                 folder=JOB_FOLDER, poll_seconds=1.0, timeout_seconds=900,
                 delete_after=True, notes=()):
        self.lan = lan
        self.ip = ip
        self.key = key
        self.name = name
        self.lines = list(lines)
        self.notes = list(notes or getattr(lines, "notes", ()))
        self.header = list(header or DEFAULT_HEADER)
        self.folder = folder.strip("/")
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.delete_after = delete_after
        self.path = (f"{self.folder}/{slug(name)}.gcode" if self.folder
                     else f"{slug(name)}.gcode")
        self.events = []
        self.result = None      # "finished" | "failed" once over
        self.job_id = None
        self.state = None
        self.started_at = time.strftime("%H:%M")
        self._lock = threading.Lock()
        self._thread = None

    # ------------------------------------------------ the outside

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="printer-command-job")
        self._thread.start()
        return self

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self):
        with self._lock:
            events = [dict(e) for e in self.events]
        done = not self.busy
        last = events[-1]["message"] if events else ""
        return {
            "ok": True,
            "name": self.name,
            "started_at": self.started_at,
            "running": not done,
            "done": done,
            "result": self.result if done else None,
            "state": self.state,
            "attention": self.state == "ATTENTION",
            "events": events,
            "text": last,
        }

    # ------------------------------------------------- the inside

    def _emit(self, kind, message):
        event = {"when": time.strftime("%H:%M:%S"), "kind": kind,
                 "message": message}
        with self._lock:
            self.events.append(event)
        if kind in ("finished", "failed"):
            self.result = kind
            diagnostics.log(f"Printer command '{self.name}' {kind}: "
                            f"{message}")

    def _kwargs(self):
        return {"key": self.key} if getattr(self.lan, "NEEDS_KEY",
                                            False) else {}

    def _status(self):
        return self.lan.read_status(self.ip, **self._kwargs())

    def _run(self):
        text = job_text(self.name, self.lines, self.header)
        kwargs = self._kwargs()
        uploaded = False
        try:
            merged = self._status()
            state = str(merged.get("state") or "UNKNOWN").upper()
            self.state = state
            if state not in self.lan.IDLE_STATES:
                raise ProjectError(
                    f"{describe_state(state)}. Commands need an idle "
                    "printer. If its screen shows a question, press the "
                    "knob on the printer first.")
            self._emit("started", f"{self.name}: sending "
                       f"{len(self.lines)} command line"
                       f"{'s' if len(self.lines) != 1 else ''} to the "
                       "printer.")
            for note in self.notes:
                self._emit("running", note)
            if self.folder:
                try:
                    self.lan.create_folder(self.ip, self.folder, **kwargs)
                except ProjectError:
                    pass
            self.lan.upload_gcode(self.ip, self.path,
                                  text.encode("utf-8"), **kwargs)
            uploaded = True
            self._emit("uploaded", f"Uploaded as {self.path}.")
            self.lan.start_print(self.ip, self.path, **kwargs)
            final = self._wait(initial_state=state,
                               initial_job_id=merged.get("job_id"))
            if final == "ERROR":
                self._emit("failed", f"{self.name}: the printer reported "
                           "an error - see its screen.")
            elif final == "STOPPED":
                self._emit("failed", f"{self.name}: stopped before it "
                           "finished.")
            elif final == "TIMEOUT":
                self._emit("failed", f"{self.name}: gave up waiting after "
                           f"{int(self.timeout_seconds)} seconds. The "
                           "printer may still be working on it.")
            else:
                self._emit("finished", f"{self.name}: done.")
        except ProjectError as e:
            self._emit("failed", f"{self.name}: {e}")
        except Exception as e:  # the thread must never die silently
            diagnostics.log_exception("printer command job", e)
            self._emit("failed", f"{self.name}: unexpected problem: {e!r}")
        finally:
            if uploaded and self.delete_after:
                self._cleanup()

    def _wait(self, initial_state, initial_job_id=None):
        deadline = time.monotonic() + self.timeout_seconds
        start_deadline = time.monotonic() + 20
        seen_active = False
        attention_said = False
        last_state = None
        while time.monotonic() < deadline:
            time.sleep(self.poll_seconds)
            try:
                merged = self._status()
            except ProjectError as e:
                self._emit("running", f"Waiting - the printer did not "
                           f"answer ({e}).")
                continue
            state = str(merged.get("state") or "UNKNOWN").upper()
            self.state = state
            if merged.get("job_id") is not None:
                self.job_id = merged["job_id"]
            if state in self.lan.ACTIVE_STATES:
                seen_active = True
            if state == "ATTENTION":
                if not attention_said:
                    message = ""
                    if isinstance(merged.get("status_printer"), dict):
                        message = str(merged["status_printer"].get(
                            "message") or "")
                    if message.strip().upper() == "OK":
                        message = ""
                    self._emit("attention", (
                        "The printer is asking a question on its screen. "
                        "Press the knob on the printer to confirm. "
                        + message).strip())
                    attention_said = True
            else:
                attention_said = False
            if state != last_state:
                self._emit("running", describe_state(state) + ".")
                last_state = state
            if seen_active and state in self.lan.END_STATES:
                return state
            if (state in self.lan.END_STATES and self.job_id is not None
                    and self.job_id != initial_job_id):
                # A job of our own has come and gone between two
                # polls (a beep takes a second): its id is new.
                return state
            if (not seen_active and state in self.lan.END_STATES
                    and state != initial_state):
                # A very short job ends between two polls; the state
                # change is the proof it ran.
                return state
            if (not seen_active and time.monotonic() > start_deadline
                    and state in self.lan.END_STATES):
                return state
        return "TIMEOUT"

    def _cleanup(self):
        kwargs = self._kwargs()
        try:
            state = str(self._status().get("state") or "").upper()
        except ProjectError:
            state = ""
        if state in self.lan.ACTIVE_STATES:
            self._emit("cleaned", f"Left {self.path} on the printer "
                       "because the job is still active.")
            return
        for _attempt in range(5):
            try:
                self.lan.delete_file(self.ip, self.path, **kwargs)
                self._emit("cleaned", f"Removed {self.path} from the "
                           "printer.")
                return
            except self.lan.NotFound:
                return
            except self.lan.Busy:
                time.sleep(self.poll_seconds * 2)
            except ProjectError as e:
                self._emit("cleaned", f"Could not remove {self.path}: {e}")
                return
        self._emit("cleaned", f"Could not remove {self.path}: the printer "
                   "kept refusing.")


def start_job(current, lan, ip, key, name, lines, **options):
    """Start a command job unless one is still running (ProjectError
    in words then). Returns the new job; the caller keeps it."""
    if current is not None and current.busy:
        raise ProjectError(
            f"'{current.name}' is still running on the printer - wait "
            "for it to finish before sending the next command.")
    diagnostics.log(f"Printer command '{name}' sent to {ip}: "
                    + " | ".join(lines))
    options.setdefault("poll_seconds", POLL_SECONDS)
    return CommandJob(lan, ip, key, name, lines, **options).start()


# ------------------------------------------- direct control (K series)
#
# The Creality K family (creality_lan.py, CONTROL_MODE "direct")
# takes commands straight over its WebSocket, so an action is a short
# list of STEPS - each a set-params dict with a label and, where the
# printer's status shows the effect, a check that reads it back and
# says what changed. No job file, no idle-printer condition for most
# of them (temperatures, fans, light and pause work mid-print too).
# The vocabulary is community-documented and unverified on the K2
# Plus as of 2026-09-12: the checks are what turns the first real
# session into the probe.

DIRECT_PRESETS = {
    "PLA": (220, 60),
    "PETG": (240, 80),
    "ABS": (260, 100),
    "ASA": (260, 100),
    "TPU": (230, 50),
    "PA-CF": (280, 90),
    "PET-CF": (280, 90),
}

# No beep: the K2 Plus made no sound on M300 (Edis, 2026-09-15) -
# its Klipper has no M300, or no buzzer answers it.
DIRECT_SNIPPETS = {
    "Report firmware version (M115)": "M115",
    "Report current position (M114)": "M114",
    "Motors off (M84)": "M84",
    "Bed mesh calibration (BED_MESH_CALIBRATE)": "BED_MESH_CALIBRATE",
    "Input shaper calibration (SHAPER_CALIBRATE)": "SHAPER_CALIBRATE",
    "Query endstops (QUERY_ENDSTOPS)": "QUERY_ENDSTOPS",
    "Chamber heater off (M141 S0)": "M141 S0",
}

# What each page mode shows. The job mode is the Core One's full
# set; the direct mode is what the K family's WebSocket offers.
JOB_FEATURES = frozenset((
    "pause", "resume", "stop", "files",
    "preheat", "preheat-custom", "cooldown",
    "load", "unload", "change", "purge", "autoload", "load-no-prompts",
    "stuck-on", "stuck-off",
    "move", "home", "park", "motors-off", "motors-on", "quick-stop",
    "extrude", "retract", "goto",
    "nozzle", "bed", "chamber", "fan",
    "mesh-level", "z-align", "z-height", "z-probe", "shaper",
    "beep", "lights-on", "lights-off", "save-settings",
    "reload-settings", "factory-settings",
    "gcode", "snippet",
))
DIRECT_FEATURES = frozenset((
    "pause", "resume", "stop", "files",
    "preheat", "preheat-custom", "cooldown",
    "cfs", "extrude", "retract",
    "move", "home", "motors-off", "goto", "zoffset",
    "nozzle", "bed", "chamber", "fan", "fan-aux", "fan-case",
    "lights-on", "lights-off", "speed", "flow",
    "gcode", "snippet",
))


def profile_for(lan):
    """What the control page needs to know about a LAN module: its
    mode, presets, snippets, limits and features."""
    if getattr(lan, "CONTROL_MODE", "job") == "direct":
        return {"mode": "direct", "presets": list(DIRECT_PRESETS),
                "snippets": list(DIRECT_SNIPPETS),
                "limits": dict(getattr(lan, "LIMITS", LIMITS)),
                "features": DIRECT_FEATURES}
    return {"mode": "job", "presets": list(PREHEAT_PRESETS),
            "snippets": list(SNIPPETS), "limits": dict(LIMITS),
            "features": JOB_FEATURES}


def _target_check(key, label):
    def check(merged):
        value = merged.get(key)
        if value is None:
            return None
        try:
            return f"The printer reports the {label} target as " \
                   f"{float(value):g} degrees."
        except (TypeError, ValueError):
            return f"The printer reports the {label} target as {value}."
    return check


def _value_check(key, label, unit=""):
    def check(merged):
        value = merged.get(key)
        if value is None:
            return None
        return f"The printer reports {label} as {value}{unit}."
    return check


def _position_check(lan):
    def check(merged):
        where = getattr(lan, "position", lambda m: {})(merged)
        if not where:
            return None
        return "Head now at " + ", ".join(
            f"{a.upper()} {where[a]:g}" for a in ("x", "y", "z")
            if a in where) + "."
    return check


def _move_check(lan, axis, delta):
    """The read-back of a jog: where the head is now, or, when it did
    not move at all, that the printer refused the move (seen live
    2026-09-15: a Z move the K2 Plus's Klipper would not make is
    ignored without a word, and the page said nothing). Moves toward
    the plate are AccessSlice's own refusals now (motion_guard), so
    a printer refusal means the far end or an unhomed printer. Needs
    the position from before the step, so the step carries
    "before"."""
    pos = getattr(lan, "position", lambda m: {})
    a = axis.lower()

    def check(merged, before=None):
        after = pos(merged)
        prior = pos(before or {})
        if not after:
            return None
        where = ", ".join(f"{k.upper()} {after[k]:g}"
                          for k in ("x", "y", "z") if k in after)
        if a in after and a in prior and abs(after[a] - prior[a]) < 0.01:
            return (f"The head did not move: the printer refused "
                    f"{axis} {_t(prior[a] + delta)} - past the far end of "
                    "its travel, or not homed since power-up. Head still "
                    f"at {where}.")
        return f"Head now at {where}."
    return check


def _step(label, params, check=None, wait=None, before=False,
          must_move=None):
    """One direct step: what the page says, the set params, an
    optional read-back check, an optional busy-wait word (the page
    waits while the printer reports itself busy, e.g. homing);
    before: read the printer before sending too, and hand that
    status to the check as its second argument; must_move: an axis
    that has to have changed afterwards, or the job stops there (the
    lift before a home)."""
    return {"label": label, "params": params, "check": check,
            "wait": wait, "before": before or bool(must_move),
            "must_move": must_move}


def _lift_step(lan, lift, must=True):
    """The lift before a home in the direct mode: a relative Z move
    up, read back, and the job stops when the printer did not lift
    (Klipper refuses moves before a home, so after a power-up the
    reported position is stale and the lift is refused: then the
    user's hand check, must=False, lets the home go on after the
    attempt)."""
    return _step(f"lift the nozzle to {_t(motion_guard.HOME_LIFT_TO)} mm "
                 "first (the printer homes X and Y before Z)",
                 {"setPosition": f"Z{_t(lift)} F{motion_guard.FEED_LIFT}"},
                 _move_check(lan, "Z", lift), before=True,
                 must_move="z" if must else None)


LIFT_REFUSED = (
    "Stopped before the home: the printer did not lift the nozzle - not "
    "homed since power-up, or it refused the move. Feel that there is a "
    "clear gap between the nozzle and the plate, tick 'The nozzle is "
    "clear of the plate' and press Home again.")


def _step_lines(steps):
    """What the steps would do as G-code lines, for motion_guard: a
    relative jog, a home, a typed line."""
    lines = []
    for step in steps:
        params = step["params"]
        if "setPosition" in params:
            lines += ["G91", "G1 " + str(params["setPosition"]), "G90"]
        elif "autohome" in params:
            lines.append("G28 " + str(params["autohome"]))
        elif "gcodeCmd" in params:
            lines.append(str(params["gcodeCmd"]))
    return lines


MOTION_ACTIONS = frozenset(("move", "goto", "home", "gcode", "snippet"))


def build_direct_action(action, field, lan, limits=None, position=None,
                        phase=None, clear=False):
    """(name, steps) for a direct-mode action; ProjectError in words
    for a bad field or an unknown action. position, phase and clear
    as build_action's: the head position read just before, the
    printer's phase (a move is refused while it prints) and the
    user's hand check for a home without a readable height. Every
    step's motion goes through motion_guard; a home from a low
    nozzle gets a lift step first that must be seen to happen."""
    name, steps = _build_direct_action(action, field, lan, limits, position)
    if action in MOTION_ACTIONS and phase in ("printing", "busy"):
        raise ProjectError(
            "Nothing was sent. The printer is printing; moves, homes and "
            "typed G-code wait until it is done or paused.")
    plan = motion_guard.check(_step_lines(steps), position, clear=clear,
                              what=name)
    out = Lines(steps)
    if plan.lift > 0:
        out = Lines([_lift_step(lan, plan.lift, must=not clear)]
                    + list(steps))
        out.notes = (plan.lift_words,)
    return name, out


def _build_direct_action(action, field, lan, limits=None, position=None):
    lim = dict(limits or getattr(lan, "LIMITS", LIMITS))
    nozzle_check = _target_check("targetNozzleTemp", "nozzle")
    bed_check = _target_check("targetBedTemp0", "bed")
    box_check = _target_check("targetBoxTemp", "chamber")

    def heat_steps(nozzle=None, bed=None, chamber=None):
        steps = []
        if nozzle is not None:
            steps.append(_step(f"nozzle to {_t(nozzle)} degrees",
                               {"nozzleTempControl": int(round(nozzle))},
                               nozzle_check))
        if bed is not None:
            steps.append(_step(f"bed to {_t(bed)} degrees",
                               {"bedTempControl": {"num": 0, "val":
                                                   int(round(bed))}},
                               bed_check))
        if chamber is not None:
            steps.append(_step(f"chamber to {_t(chamber)} degrees",
                               {"boxTempControl": int(round(chamber))},
                               box_check))
        return steps

    if action == "preheat":
        preset = field("preset").upper()
        if preset not in DIRECT_PRESETS:
            raise ProjectError("Choose a preheat preset first.")
        nozzle, bed = DIRECT_PRESETS[preset]
        nozzle_only = field("nozzle_only") == "yes"
        return (f"Preheat {preset}" + (" (nozzle only)" if nozzle_only
                                        else ""),
                heat_steps(nozzle, None if nozzle_only else bed))
    if action == "preheat-custom":
        nozzle = _number(field, "nozzle", "The nozzle temperature",
                         *lim["nozzle"])
        bed = _number(field, "bed", "The bed temperature", *lim["bed"],
                      required=False)
        chamber = _number(field, "chamber", "The chamber temperature",
                          *lim["chamber"], required=False)
        return ("Preheat to your values", heat_steps(nozzle, bed, chamber))
    if action == "cooldown":
        return ("Cooldown", heat_steps(0, 0, 0)
                + [_step("model fan off", {"fan": 0},
                         _value_check("fan", "the model fan", " percent"))])
    if action == "nozzle":
        temp = _number(field, "temp", "The nozzle temperature",
                       *lim["nozzle"])
        return (f"Nozzle to {_t(temp)} degrees", heat_steps(nozzle=temp))
    if action == "bed":
        temp = _number(field, "temp", "The bed temperature", *lim["bed"])
        return (f"Bed to {_t(temp)} degrees", heat_steps(bed=temp))
    if action == "chamber":
        temp = _number(field, "temp", "The chamber temperature",
                       *lim["chamber"])
        return (f"Chamber to {_t(temp)} degrees", heat_steps(chamber=temp))
    if action in ("fan", "fan-aux", "fan-case"):
        percent = _number(field, "percent", "The fan speed", *lim["fan"])
        key, label = {"fan": ("fan", "model fan"),
                      "fan-aux": ("fanAuxiliary", "side fan"),
                      "fan-case": ("fanCase", "case fan")}[action]
        return (f"{label.capitalize()} to {_t(percent)} percent",
                [_step(f"{label} to {_t(percent)} percent",
                       {key: int(round(percent))},
                       _value_check(key, f"the {label}", " percent"))])

    if action == "cfs-feed" or action == "cfs-retract":
        box = _number(field, "box", "The CFS box", 0, 4, default=1)
        slot = _number(field, "slot", "The slot", 0, 3)
        feed = action == "cfs-feed"
        letter = "ABCD"[int(slot)]
        where = ("the external spool" if int(box) == 0
                 else f"CFS slot {letter}")
        return (("Load filament from " if feed else "Retract filament of ")
                + where,
                [_step("the printer's own feed routine" if feed
                       else "the printer's own retract routine",
                       {"feedInOrOut": {"boxId": int(box),
                                        "materialId": int(slot),
                                        "isFeed": 1 if feed else 0}},
                       wait="feeding" if feed else "retracting")])
    if action in ("extrude", "retract"):
        length = _number(field, "length", "The length", *lim["length"],
                         default=5)
        sign = 1 if action == "extrude" else -1
        return (f"{action.capitalize()} {_t(length)} mm", [
            _step("relative extrusion on", {"gcodeCmd": "M83"}),
            _step(f"move the extruder {_t(sign * length)} mm",
                  {"gcodeCmd": f"G1 E{_t(sign * length)} F{_t(FEED_E)}"}),
            _step("absolute extrusion back", {"gcodeCmd": "M82"})])

    if action == "move":
        go = field("go").upper()
        axis = go[:1] if go else field("axis").upper()
        if axis not in ("X", "Y", "Z"):
            raise ProjectError("Choose the axis to move: X, Y or Z.")
        step = _number(field, "step", "The step", *lim["step"], default=10)
        sign = -1 if (go[1:] == "-" if go
                      else field("direction") == "minus") else 1
        word = ("right" if sign > 0 else "left") if axis == "X" else \
            ("back" if sign > 0 else "forward") if axis == "Y" else \
            ("up" if sign > 0 else "down")
        feed = FEED_Z if axis == "Z" else FEED_XY
        return (f"Move {axis} {_t(step)} mm {word}",
                [_step(f"jog {axis} by {_t(sign * step)} mm",
                       {"setPosition": f"{axis}{_t(sign * step)} F{feed}"},
                       _move_check(lan, axis, sign * step), before=True)])
    if action == "goto":
        x = _number(field, "x", "X", *lim["x"], required=False)
        y = _number(field, "y", "Y", *lim["y"], required=False)
        z = _number(field, "z", "Z", *lim["z"], required=False)
        if x is None and y is None and z is None:
            raise ProjectError("Type at least one of X, Y and Z to go "
                               "to.")
        where = ", ".join(f"{a} {_t(v)}" for a, v in
                          (("X", x), ("Y", y), ("Z", z)) if v is not None)
        lines = motion_guard.goto_lines(position, x, y, z, FEED_XY, FEED_Z)
        steps = [_step("absolute positioning", {"gcodeCmd": lines[0]})]
        if len(lines) == 3:
            steps.append(_step("Z first, up to " + lines[1].split()[1][1:]
                               + " mm", {"gcodeCmd": lines[1]}))
        steps.append(_step(f"move to {where}", {"gcodeCmd": lines[-1]},
                           _position_check(lan)))
        return (f"Go to {where}", steps)
    if action == "home":
        axes = "".join(a for a in field("axes").upper() if a in "XYZ")
        label = {"": "Home all axes", "XY": "Home X and Y",
                 "Z": "Home Z"}.get(axes, f"Home {' and '.join(axes)}")
        value = " ".join(axes or "XYZ")
        return (label, [_step(f"home {value}", {"autohome": value},
                              _position_check(lan), wait="homing")])
    if action == "motors-off":
        return ("Disable motors", [_step("motors off",
                                         {"gcodeCmd": "M84"})])
    if action == "zoffset":
        step = _number(field, "step", "The Z offset step",
                       *lim["zoffset"], default=0.01)
        closer = field("direction") != "farther"
        signed = f"{'-' if closer else '+'}{_t(step)}"
        return (f"Nozzle {_t(step)} mm {'closer to' if closer else 'farther from'} the bed",
                [_step(f"Z offset {signed}", {"setZOffset": signed})])

    if action == "beep":
        return ("Beep", [_step("beep", {"gcodeCmd": "M300 S1000 P300"})])
    if action in ("lights-on", "lights-off"):
        on = action == "lights-on"

        def light_check(merged):
            value = merged.get("lightSw")
            if value is None:
                return None
            try:
                word = "on" if int(float(value)) else "off"
            except (TypeError, ValueError):
                word = str(value)
            return f"The printer reports the light {word}."
        return ("Light on" if on else "Light off",
                [_step("light on" if on else "light off",
                       {"lightSw": 1 if on else 0}, light_check)])
    if action in ("speed", "flow"):
        percent = _number(field, "percent", "The percentage",
                          *lim["percent"])
        key = "setFeedratePct" if action == "speed" else "setFlowratePct"
        back = "curFeedratePct" if action == "speed" else "curFlowratePct"
        return (f"Print {action} to {_t(percent)} percent",
                [_step(f"{action} {_t(percent)} percent",
                       {key: int(round(percent))},
                       _value_check(back, f"the print {action}",
                                    " percent"))])

    if action == "gcode":
        lines = custom(field("gcode"))
        if not lines:
            raise ProjectError("Type at least one G-code line first.")
        return (f"Your G-code ({len(lines)} line"
                + ("s" if len(lines) != 1 else "") + ")",
                [_step(line, {"gcodeCmd": line}) for line in lines])
    if action == "snippet":
        name = field("snippet")
        if name not in DIRECT_SNIPPETS:
            raise ProjectError("Choose a ready-made command first.")
        return (name, [_step(line, {"gcodeCmd": line})
                       for line in custom(DIRECT_SNIPPETS[name])])
    raise ProjectError(f"AccessSlice knows no printer action '{action}'.")


def _busy(lan, merged):
    """True while the printer reports itself busy with a routine
    (homing, feeding): deviceState 7 is homing per the community
    integrations. State 0 is NOT busy - it is the K2 Plus's idle
    state (seen live 2026-09-15); reading it as busy made every
    waiting step sit out its ninety seconds on an idle printer."""
    try:
        if int(merged.get("deviceState", -1)) == 7:
            return True
    except (TypeError, ValueError):
        pass
    return lan.job_phase(merged) == "busy"


class DirectJob:
    """Direct-mode steps on their way to the printer, on a thread;
    snapshot() has the same shape as CommandJob's so the page's poll
    needs no second path."""

    def __init__(self, lan, ip, name, steps, poll_seconds=1.0,
                 wait_seconds=90, notes=()):
        self.lan = lan
        self.ip = ip
        self.name = name
        self.steps = list(steps)
        self.notes = list(notes or getattr(steps, "notes", ()))
        self.poll_seconds = poll_seconds
        self.wait_seconds = wait_seconds
        self.events = []
        self.result = None
        self.state = None
        self.started_at = time.strftime("%H:%M")
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="printer-direct-job")
        self._thread.start()
        return self

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self):
        with self._lock:
            events = [dict(e) for e in self.events]
        done = not self.busy
        return {"ok": True, "name": self.name,
                "started_at": self.started_at, "running": not done,
                "done": done, "result": self.result if done else None,
                "state": self.state, "attention": False,
                "events": events,
                "text": events[-1]["message"] if events else ""}

    def _emit(self, kind, message):
        event = {"when": time.strftime("%H:%M:%S"), "kind": kind,
                 "message": message}
        with self._lock:
            self.events.append(event)
        if kind in ("finished", "failed"):
            self.result = kind
            diagnostics.log(f"Printer command '{self.name}' {kind}: "
                            f"{message}")

    def _run(self):
        try:
            self._emit("started", f"{self.name}: {len(self.steps)} step"
                       f"{'s' if len(self.steps) != 1 else ''} to the "
                       "printer.")
            for note in self.notes:
                self._emit("running", note)
            for step in self.steps:
                before = None
                if step.get("before"):
                    try:
                        before = self.lan.read_status(self.ip)
                    except ProjectError:
                        before = None
                pushed = self.lan.set_params(self.ip, step["params"])
                self._emit("running", f"Sent: {step['label']}.")
                if step.get("wait"):
                    self._wait_while_busy(step["wait"])
                merged = None
                if step.get("check") or step.get("must_move"):
                    time.sleep(self.poll_seconds)
                    merged = self.lan.read_status(self.ip)
                    merged = dict(pushed, **merged)
                if step.get("check"):
                    said = (step["check"](merged, before)
                            if step.get("before") else step["check"](merged))
                    if said:
                        self._emit("running", said)
                if step.get("must_move"):
                    self._must_have_moved(step["must_move"], before, merged)
            self._emit("finished", f"{self.name}: done.")
        except ProjectError as e:
            self._emit("failed", f"{self.name}: {e}")
        except Exception as e:  # the thread must never die silently
            diagnostics.log_exception("printer direct job", e)
            self._emit("failed", f"{self.name}: unexpected problem: {e!r}")

    def _must_have_moved(self, axis, before, after):
        """The lift before a home has to be seen in the position, or
        the home is not sent: the step after it is what would drag
        a nozzle that is still on the plate."""
        pos = getattr(self.lan, "position", lambda m: {})
        prior = pos(before or {})
        now = pos(after or {})
        if (axis not in prior or axis not in now
                or abs(now[axis] - prior[axis]) < 0.01):
            raise ProjectError(LIFT_REFUSED)

    def _wait_while_busy(self, word):
        deadline = time.monotonic() + self.wait_seconds
        said = False
        while time.monotonic() < deadline:
            time.sleep(self.poll_seconds)
            try:
                merged = self.lan.read_status(self.ip)
            except ProjectError:
                continue
            self.state = merged.get("state")
            if not _busy(self.lan, merged):
                if said:
                    self._emit("running", f"The printer finished {word}.")
                return
            if not said:
                self._emit("running", f"The printer is {word} - "
                           "waiting.")
                said = True
        self._emit("running", f"Still {word} after "
                   f"{int(self.wait_seconds)} seconds - check The "
                   "printer now for where it got to.")


def start_direct_job(current, lan, ip, name, steps, **options):
    """Start direct-mode steps unless a command is still running."""
    if current is not None and current.busy:
        raise ProjectError(
            f"'{current.name}' is still running on the printer - wait "
            "for it to finish before sending the next command.")
    diagnostics.log(f"Printer command '{name}' sent to {ip}: "
                    + " | ".join(json.dumps(s["params"]) for s in steps))
    options.setdefault("poll_seconds", POLL_SECONDS)
    return DirectJob(lan, ip, name, steps, **options).start()
