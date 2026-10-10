"""motion_guard.py - the safety in front of every move the pages send.

Why this exists. On 2026-09-15 the K2 Plus at Edis's work was homed
from AccessSlice (it rests at the plate centre, about 8 mm above the
plate), then "Z down 10 mm" was pressed. The printer accepted it: its
Klipper allows Z down to -10, so the bed was driven about 2 mm past
the nozzle into the plate, and the page said nothing. Twenty minutes
later "Home all" was pressed from that position. Creality's homing
runs X, then Y, then Z, with no lift first, so the pressed nozzle was
dragged from the centre to the right edge and along it: scratches in
a triangle from the centre, found by a colleague on 2026-09-18. The
page had trusted the printer's own limits; those limits are 10 mm
below the plate. Nothing on the printer side prevents this, so
AccessSlice has to.

The rules, applied to every move, go-to, home, park, calibration
routine, typed G-code line and ready-made snippet, in both modes
(Prusa job files and the K series' direct commands), from the head
position read from the printer just before the command:

1. THE FLOOR. No move may end below Z 0, the nozzle touching the
   plate. The refusal says where the head is and the largest step
   down that is still allowed.
2. SIDEWAYS CLEARANCE. No X or Y move with the nozzle lower than
   XY_CLEARANCE above the plate; the refusal says to move Z up
   first. A go-to that carries a Z above the clearance goes up
   first and sideways after, as two moves.
3. THE LIFT BEFORE A HOME. A home (and a park or a routine that
   travels over the plate) with the nozzle lower than HOME_CLEARANCE
   is preceded by a lift to HOME_LIFT_TO. In the direct mode the
   lift is read back and the home is NOT sent when the printer did
   not lift (not homed since power-up, say).
4. NO POSITION, NO MOVE TOWARD THE PLATE. When the printer gives no
   head position, a move toward the plate, a sideways move or a
   home is refused - a home can be confirmed by hand ("the nozzle
   is clear of the plate") because after a power-up nothing else
   gets the printer going.
5. NO WAY AROUND THE LIMITS. Typed lines that redefine where the
   head is or switch the printer's own limits off (G92 with an axis,
   M211, SET_KINEMATIC_POSITION, FORCE_MOVE, SET_GCODE_OFFSET...)
   are not passed on.

Nothing here knows about objects on the plate: the plate-empty
confirmation before a print stays the user's.
"""

import re

from errors import ProjectError

# Millimetres above the plate. Z 0 is the nozzle touching the plate on
# both the Core One and the K series.
Z_FLOOR = 0.0
XY_CLEARANCE = 1.0
HOME_CLEARANCE = 3.0
HOME_LIFT_TO = 5.0
FEED_LIFT = 600

# Commands that travel over the plate on their own (park, bed mesh,
# probing, calibrations, arcs): they need the clearance like a
# sideways move, and get the lift like a home.
TRAVELLING = frozenset((
    "G2", "G3", "G27", "G29", "G30", "G32", "G64", "G162", "M959",
    "BED_MESH_CALIBRATE", "SHAPER_CALIBRATE", "Z_TILT_ADJUST",
    "PROBE", "PROBE_ACCURACY", "PROBE_CALIBRATE", "SCREWS_TILT_CALCULATE",
    "QUAD_GANTRY_LEVEL", "Z_AXIS_CALIBRATION", "ACCURATE_G28",
))
HOMING = frozenset(("G28", "ACCURATE_HOME_Z"))
# Commands that move the head's coordinates or switch the limits off.
BYPASS = frozenset((
    "M211", "SET_KINEMATIC_POSITION", "FORCE_MOVE", "MANUAL_STEPPER",
    "SET_GCODE_OFFSET", "M206", "M428", "G53",
))

_WORD = re.compile(r"^([GM])0*(\d+)(?:\.\d+)?$")


class Refused(ProjectError):
    """A move AccessSlice will not send; the message says why and
    what to do instead. Always starts with 'Nothing was sent.'"""


class Plan:
    """The outcome of a check: the lift (mm) to send before the
    lines, and the words for it."""

    def __init__(self, lift=0.0, lines=None):
        self.lift = lift
        self.lines = lines

    @property
    def lift_words(self):
        return (f"Lifted the nozzle to {_t(HOME_LIFT_TO)} mm before the "
                "home, because the printer homes X and Y before Z and a "
                "low nozzle would be dragged over the plate.")


def _t(value):
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _command(line):
    """The command word of a G-code line, normalised (G01 -> G1,
    lower case -> upper), or "" for a blank or comment line."""
    code = line.split(";", 1)[0].strip()
    if not code:
        return "", code
    word = code.split()[0].upper()
    m = _WORD.match(word)
    if m:
        word = f"{m.group(1)}{int(m.group(2))}"
    return word, code


def _axes(code):
    found = {}
    for axis, value in re.findall(
            r"(?<![A-Za-z_])([XYZ])\s*=?\s*(-?\d+(?:\.\d+)?)", code.upper()):
        found[axis] = float(value)
    return found


def _where(z):
    return "unknown" if z is None else f"Z {_t(z)}"


def _unknown(action):
    return Refused(
        "Nothing was sent. AccessSlice cannot read how far the nozzle is "
        f"from the plate, so it will not {action}. Home the printer first "
        "(the Home button lifts the nozzle before homing), then Read "
        "again.")


def _home_unknown():
    return Refused(
        "Nothing was sent. AccessSlice cannot read the head height, and "
        "the printer homes X and Y before Z, so a nozzle resting on the "
        "plate would be dragged across it. Feel that there is a clear "
        "gap between the nozzle and the plate, tick 'The nozzle is clear "
        "of the plate' and press Home again.")


def check(lines, position, clear=False, what="This command"):
    """Walk G-code lines from the head position the printer gave
    ({"x", "y", "z"} floats, any missing) and refuse the first line
    that would scrape or dig into the plate. Returns a Plan: its
    lift is the millimetres to move Z up BEFORE the lines (0 when
    none is needed). clear: the user confirmed by hand that the
    nozzle is clear of the plate, which stands in for an unreadable
    height before a home."""
    z = None
    if position and position.get("z") is not None:
        z = float(position["z"])
    start_z = z
    relative = False
    z_moved = False
    homed = False        # the printer's own home leaves the nozzle clear
    lift = 0.0
    for raw in lines:
        cmd, code = _command(str(raw))
        if not cmd:
            continue
        if cmd == "G90":
            relative = False
            continue
        if cmd == "G91":
            relative = True
            continue
        if cmd in BYPASS or (cmd == "G92" and _axes(code)):
            raise Refused(
                f"Nothing was sent. {cmd} changes the printer's idea of "
                "where the head is or switches its own limits off; "
                "AccessSlice does not pass it on, because the checks that "
                "keep the nozzle off the plate depend on them.")
        if cmd in HOMING or cmd in TRAVELLING:
            if not homed:
                if z is None:
                    if not clear:
                        raise _home_unknown()
                elif z < HOME_CLEARANCE:
                    if z_moved:
                        raise Refused(
                            f"Nothing was sent. {cmd} would travel over the "
                            f"plate with the nozzle at {_where(z)}. Move Z "
                            f"up to at least {_t(HOME_CLEARANCE)} mm first, "
                            "on a line of its own.")
                    lift = max(lift, HOME_LIFT_TO - z)
                    z = HOME_LIFT_TO
            if cmd in HOMING:
                homed = True
                z = None
            continue
        if cmd not in ("G0", "G1"):
            continue
        axes = _axes(code)
        target = z
        if "Z" in axes:
            if relative:
                if z is None:
                    if axes["Z"] < 0:
                        raise _unknown("move the nozzle toward it")
                    target = None
                else:
                    target = z + axes["Z"]
            else:
                target = axes["Z"]
            if target is not None and target < Z_FLOOR - 1e-9:
                if z is not None and z > Z_FLOOR + 1e-9:
                    room = (f" The head is at {_where(z)}; the largest "
                            f"step down from here is {_t(z)} mm.")
                elif z is not None:
                    room = " The nozzle is already at the plate."
                else:
                    room = ""
                raise Refused(
                    f"Nothing was sent. {what} would take the nozzle to Z "
                    f"{_t(target)}, below the plate (Z 0 is the nozzle "
                    f"touching the plate).{room}")
        if "X" in axes or "Y" in axes:
            if not homed:
                if target is None:
                    raise _unknown("move sideways")
                if target < XY_CLEARANCE - 1e-9:
                    raise Refused(
                        "Nothing was sent. A sideways move with the nozzle "
                        f"only {_t(target)} mm from the plate would scrape "
                        f"it. Move Z up to at least {_t(XY_CLEARANCE)} mm "
                        "first.")
        if "Z" in axes:
            z = target
            z_moved = z_moved or target != start_z
    return Plan(lift=lift)


def goto_lines(position, x=None, y=None, z=None, feed_xy=3000,
               feed_z=600):
    """The absolute move lines for a go-to, the Z part first when the
    head is low and the target Z is not: up, then sideways."""
    here = None
    if position and position.get("z") is not None:
        here = float(position["z"])
    parts = [f"{a}{_t(v)}" for a, v in (("X", x), ("Y", y)) if v is not None]
    lines = ["G90"]
    if z is not None and parts and (here is None or here < XY_CLEARANCE) \
            and z >= XY_CLEARANCE:
        lines.append(f"G1 Z{_t(z)} F{_t(feed_z)}")
        lines.append("G1 " + " ".join(parts) + f" F{_t(feed_xy)}")
        return lines
    if z is not None:
        parts.append(f"Z{_t(z)}")
    feed = feed_z if (z is not None and len(parts) == 1) else feed_xy
    lines.append("G1 " + " ".join(parts) + f" F{_t(feed)}")
    return lines


def lift_lines(lift, feed=FEED_LIFT):
    """Relative G-code for the lift before a home (job mode)."""
    return ["G91", f"G1 Z{_t(lift)} F{_t(feed)}", "G90"]
