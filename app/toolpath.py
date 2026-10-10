"""toolpath.py - reading the sliced G-code back as layers and paths.

Phase V of the real-slicer plan: the sliced preview. The slicer's
engine has written the printable file; this module reads the G-code
inside it and turns it into what the preview page draws (line
segments per layer, each with its feature type) and what the page
SAYS (layer count and heights, how much filament goes into walls,
infill, supports and so on, per layer and in total, and when in the
print each layer starts). The spoken facts are the accessible
equivalent of the picture and ship first, always; the picture is a
mirror of the same numbers.

Understood formats (all verified on real Benchy slices, 2026-09-06):

- The Orca family (Bambu Studio, OrcaSlicer, Creality Print,
  Anycubic Slicer Next): a .gcode.3mf whose Metadata/plate_N.gcode
  marks layers with "; CHANGE_LAYER" + "; Z_HEIGHT:" +
  "; LAYER_HEIGHT:", features with "; FEATURE: <name>", and wipes
  with "; WIPE_START" ... "; WIPE_END". Arcs (G2/G3 with I/J) are
  common - a Benchy has 163,000 - and are drawn as short chords.
- PrusaSlicer: a .gcode or a binary .bgcode (decoded by bgcode.py)
  marking layers with ";LAYER_CHANGE" + ";Z:" + ";HEIGHT:" and
  features with ";TYPE:<name>". Its retraction wipes are plain moves
  with negative E, which count as wipes here.

Both write relative extrusion (M83); absolute (M82) is handled too.
M73 P<percent> R<remaining minutes> lines, which both families emit
at layer changes, give the start time of every layer.

Pure stdlib. A Benchy (930,000 lines) parses in a few seconds; the
result is compact arrays, not tuples, so a full plate fits in memory.
"""

import math
import re
import zipfile
from array import array
from pathlib import Path

import bgcode

# ------------------------------------------------------ feature kinds

# (key, spoken name, colour). The colours are the ones the preview
# draws; they were picked to stay apart from each other on both the
# light and the dark plate and to keep the wall/infill/support
# families recognisable at a glance.
KINDS = [
    ("travel", "Travel move (no extrusion)", "#8a94a6"),
    ("outer_wall", "Outer wall", "#e5533d"),
    ("inner_wall", "Inner wall", "#f0a13a"),
    ("overhang_wall", "Overhang wall", "#2d7ff9"),
    ("sparse_infill", "Sparse infill", "#a34fd6"),
    ("solid_infill", "Internal solid infill", "#7c4dbd"),
    ("top_surface", "Top surface", "#e04b8b"),
    ("bottom_surface", "Bottom surface", "#4f7fe0"),
    ("bridge", "Bridge", "#3fb3c7"),
    ("gap_fill", "Gap fill", "#e8c93a"),
    ("support", "Support", "#3aae5c"),
    ("support_interface", "Support interface", "#1f7a3e"),
    ("skirt_brim", "Skirt or brim", "#6fa8ff"),
    ("prime_tower", "Prime tower", "#9aa3b5"),
    ("ironing", "Ironing", "#ff8fc8"),
    ("custom", "Start-up, end and custom code", "#6b7280"),
    ("wipe", "Wipe (retraction move)", "#b8bfcb"),
    ("other", "Other", "#c084fc"),
]
KIND_INDEX = {key: i for i, (key, _n, _c) in enumerate(KINDS)}
TRAVEL = KIND_INDEX["travel"]
WIPE = KIND_INDEX["wipe"]
CUSTOM = KIND_INDEX["custom"]
OTHER = KIND_INDEX["other"]

# The slicers' own feature names, both families, lower-cased.
_FEATURE_NAMES = {
    # Orca family
    "outer wall": "outer_wall",
    "inner wall": "inner_wall",
    "overhang wall": "overhang_wall",
    "floating vertical shell": "overhang_wall",
    "sparse infill": "sparse_infill",
    "internal solid infill": "solid_infill",
    "top surface": "top_surface",
    "bottom surface": "bottom_surface",
    "bridge": "bridge",
    "internal bridge": "bridge",
    "gap infill": "gap_fill",
    "support": "support",
    "support interface": "support_interface",
    "support transition": "support",
    "skirt": "skirt_brim",
    "brim": "skirt_brim",
    "prime tower": "prime_tower",
    "ironing": "ironing",
    "custom": "custom",
    # PrusaSlicer
    "external perimeter": "outer_wall",
    "perimeter": "inner_wall",
    "overhang perimeter": "overhang_wall",
    "internal infill": "sparse_infill",
    "solid infill": "solid_infill",
    "top solid infill": "top_surface",
    "bottom solid infill": "bottom_surface",
    "bridge infill": "bridge",
    "internal bridge infill": "bridge",
    "gap fill": "gap_fill",
    "skirt/brim": "skirt_brim",
    "support material": "support",
    "support material interface": "support_interface",
    "wipe tower": "prime_tower",
}


def kind_of(feature_name):
    key = _FEATURE_NAMES.get(feature_name.strip().lower())
    return KIND_INDEX[key] if key else OTHER


# --------------------------------------------------------- the parse

_WORD_RE = re.compile(r"([XYZEIJRFP])(-?\d*\.?\d+)")
_M73_RE = re.compile(r"^M73\b.*?R(\d+)")
# The command word: "G1" in "G1 X1" and in PrusaSlicer's space-free
# "G1X1Y2E.1" (binary G-code drops the spaces).
_CODE_RE = re.compile(r"^([GM]\d+)")
ARC_STEP_DEG = 12.0


class Layer:
    __slots__ = ("z", "height", "xy", "kinds", "ext_by_kind", "travel_mm",
                 "remaining_min", "extrusion_mm")

    def __init__(self, z, height):
        self.z = z
        self.height = height
        self.xy = array("f")
        self.kinds = array("B")
        self.ext_by_kind = {}
        self.travel_mm = 0.0
        self.extrusion_mm = 0.0
        self.remaining_min = None

    @property
    def segments(self):
        return len(self.kinds)


class Toolpath:
    def __init__(self):
        self.layers = []
        self.total_min = None
        self.lines = 0
        self.bbox = None  # (minx, miny, maxx, maxy) of extrusion

    @property
    def segments(self):
        return sum(len(layer.kinds) for layer in self.layers)


def _words(rest):
    return {k: float(v) for k, v in _WORD_RE.findall(rest)}


def parse(text):
    """The G-code text -> Toolpath. Moves before the first layer
    marker (start-up purge lines and the like) are left out; every
    layer's segments are stored flat as x0,y0,x1,y1 floats plus one
    kind byte each."""
    tp = Toolpath()
    layers = tp.layers
    layer = None
    x = y = z = 0.0
    e_abs = 0.0
    relative_e = True
    kind = CUSTOM
    in_wipe = False
    pending_z = None
    pending_height = None
    minx = miny = math.inf
    maxx = maxy = -math.inf
    last_remaining = None   # the newest M73 R value seen
    own_m73 = False         # has the current layer had its own M73?

    def add(layer_, x0, y0, x1, y1, k):
        layer_.xy.extend((x0, y0, x1, y1))
        layer_.kinds.append(k)

    for raw in text.splitlines():
        tp.lines += 1
        line = raw.strip()
        if not line:
            continue
        c0 = line[0]
        if c0 == ";":
            body = line[1:].strip()
            if body.startswith("FEATURE:"):
                kind = kind_of(body[8:])
            elif body.startswith("TYPE:"):
                kind = kind_of(body[5:])
            elif body == "CHANGE_LAYER" or body == "LAYER_CHANGE":
                # The layer's z and height follow on the next lines;
                # open the layer on the first one that arrives.
                pending_z = "wait"
                pending_height = None
            elif body.startswith("Z_HEIGHT:") or body.startswith("Z:"):
                value = body.split(":", 1)[1].strip()
                try:
                    zval = float(value)
                except ValueError:
                    continue
                if pending_z is not None:
                    layer = Layer(zval, pending_height)
                    layers.append(layer)
                    pending_z = None
                    # Start from the newest progress line; a layer's
                    # own M73 (both families write one on most
                    # layer changes) refines it below.
                    layer.remaining_min = last_remaining
                    own_m73 = False
            elif body.startswith("LAYER_HEIGHT:") or body.startswith("HEIGHT:"):
                value = body.split(":", 1)[1].strip()
                try:
                    h = float(value)
                except ValueError:
                    continue
                if layer is not None and layer.height is None:
                    layer.height = h
                else:
                    pending_height = h
            elif body == "WIPE_START":
                in_wipe = True
            elif body == "WIPE_END":
                in_wipe = False
            continue
        cm = _CODE_RE.match(line)
        if not cm:
            continue
        code = cm.group(1)
        rest = line[cm.end():].split(";", 1)[0]
        if c0 == "M":
            if code == "M83":
                relative_e = True
            elif code == "M82":
                relative_e = False
            elif code == "M73":
                m = _M73_RE.match(line)
                if m:
                    remaining = int(m.group(1))
                    if tp.total_min is None:
                        tp.total_min = remaining
                    last_remaining = remaining
                    if layer is not None and not own_m73:
                        layer.remaining_min = remaining
                        own_m73 = True
            continue
        # G0/G1 straight moves, G2/G3 arcs; anything else ignored.
        if code not in ("G1", "G0", "G2", "G3"):
            if code == "G92":
                w = _words(rest)
                if "E" in w:
                    e_abs = w["E"]
            continue
        w = _words(rest)
        nx = w.get("X", x)
        ny = w.get("Y", y)
        nz = w.get("Z", z)
        if "E" in w:
            de = w["E"] if relative_e else w["E"] - e_abs
            if not relative_e:
                e_abs = w["E"]
        else:
            de = 0.0
        moved_xy = (nx != x) or (ny != y)
        if layer is not None and moved_xy:
            if de > 0:
                k = kind
            elif de < 0 or in_wipe:
                k = WIPE
            else:
                k = TRAVEL
            if code in ("G2", "G3") and ("I" in w or "J" in w):
                length = _arc(layer, x, y, nx, ny, w.get("I", 0.0),
                              w.get("J", 0.0), code == "G2", k, add)
            else:
                add(layer, x, y, nx, ny, k)
                length = math.hypot(nx - x, ny - y)
            if k == TRAVEL or k == WIPE:
                layer.travel_mm += length
            else:
                layer.ext_by_kind[k] = layer.ext_by_kind.get(k, 0.0) + length
                layer.extrusion_mm += length
                if nx < minx:
                    minx = nx
                if nx > maxx:
                    maxx = nx
                if ny < miny:
                    miny = ny
                if ny > maxy:
                    maxy = ny
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
        x, y, z = nx, ny, nz
    if minx < math.inf:
        tp.bbox = (minx, miny, maxx, maxy)
    # Layers without a stated height: the step from the one below.
    prev_z = 0.0
    for layer_ in layers:
        if layer_.height is None:
            layer_.height = round(layer_.z - prev_z, 4)
        prev_z = layer_.z
    return tp


def _arc(layer, x0, y0, x1, y1, i, j, clockwise, k, add):
    """Draw a G2/G3 arc as chords; returns its length."""
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j)
    if r < 1e-6:
        add(layer, x0, y0, x1, y1, k)
        return math.hypot(x1 - x0, y1 - y0)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    sweep = a1 - a0
    if clockwise:
        if sweep >= 0:
            sweep -= 2 * math.pi
    else:
        if sweep <= 0:
            sweep += 2 * math.pi
    steps = max(1, min(64, int(abs(sweep) / math.radians(ARC_STEP_DEG)) + 1))
    px, py = x0, y0
    for s in range(1, steps + 1):
        a = a0 + sweep * s / steps
        if s == steps:
            nx, ny = x1, y1
        else:
            nx, ny = cx + r * math.cos(a), cy + r * math.sin(a)
        add(layer, px, py, nx, ny, k)
        px, py = nx, ny
    return abs(sweep) * r


# ------------------------------------------------------ the outputs

MAGIC = b"ASTP"


def payload(tp, include_travel=True):
    """The binary the preview page draws from:

        'ASTP' | uint32 layer count | per layer: float32 z,
        float32 height, uint32 n | float32 x0,y0,x1,y1 x n |
        uint8 kind x n, padded to a multiple of 4 bytes.
    """
    import struct
    out = [MAGIC, struct.pack("<I", len(tp.layers))]
    for layer in tp.layers:
        if include_travel:
            xy, kinds = layer.xy, layer.kinds
        else:
            xy, kinds = array("f"), array("B")
            src = layer.xy
            for idx, k in enumerate(layer.kinds):
                if k != TRAVEL and k != WIPE:
                    xy.extend(src[idx * 4:idx * 4 + 4])
                    kinds.append(k)
        n = len(kinds)
        out.append(struct.pack("<ffI", layer.z, layer.height or 0.0, n))
        out.append(xy.tobytes())
        kb = kinds.tobytes()
        out.append(kb + b"\0" * (-len(kb) % 4))
    return b"".join(out)


def _kind_name(k):
    return KINDS[k][1]


def layer_facts(tp, index):
    """Plain facts about one layer (0-based index)."""
    layer = tp.layers[index]
    parts = []
    for k, mm in sorted(layer.ext_by_kind.items(), key=lambda kv: -kv[1]):
        parts.append({"kind": KINDS[k][0], "name": _kind_name(k),
                      "mm": round(mm, 1)})
    start_min = None
    if tp.total_min is not None and layer.remaining_min is not None:
        start_min = max(0, tp.total_min - layer.remaining_min)
    return {
        "number": index + 1,
        "z": round(layer.z, 3),
        "height": round(layer.height or 0.0, 3),
        "segments": layer.segments,
        "extrusion_mm": round(layer.extrusion_mm, 1),
        "travel_mm": round(layer.travel_mm, 1),
        "features": parts,
        "start_min": start_min,
    }


def facts(tp, plate_size=None):
    """Everything the page says, as JSON-ready data: the kinds table,
    totals, and every layer's facts (small: a few numbers per layer)."""
    totals = {}
    for layer in tp.layers:
        for k, mm in layer.ext_by_kind.items():
            totals[k] = totals.get(k, 0.0) + mm
    total_list = [{"kind": KINDS[k][0], "name": _kind_name(k),
                   "mm": round(mm, 1)}
                  for k, mm in sorted(totals.items(), key=lambda kv: -kv[1])]
    heights = sorted({round(layer.height or 0.0, 3) for layer in tp.layers})
    return {
        "kinds": [{"kind": key, "name": name, "colour": colour}
                  for key, name, colour in KINDS],
        "layer_count": len(tp.layers),
        "top_z": round(tp.layers[-1].z, 3) if tp.layers else 0.0,
        "layer_heights": heights,
        "first_layer_height": round(tp.layers[0].height or 0.0, 3)
        if tp.layers else 0.0,
        "total_min": tp.total_min,
        "segments": tp.segments,
        "extrusion_mm": round(sum(l.extrusion_mm for l in tp.layers), 1),
        "travel_mm": round(sum(l.travel_mm for l in tp.layers), 1),
        "totals": total_list,
        "bbox": [round(v, 2) for v in tp.bbox] if tp.bbox else None,
        "plate_size": list(plate_size) if plate_size else None,
        "layers": [layer_facts(tp, i) for i in range(len(tp.layers))],
    }


def friendly_mm(mm):
    """Extrusion length in words: metres above a metre, else mm."""
    if mm >= 1000:
        return f"{mm / 1000:.1f} m"
    return f"{mm:.0f} mm"


# ---------------------------------------------------- reading files


def plates_in_output(path):
    """The plate numbers a produced file holds: the plate_N.gcode
    entries of a .gcode.3mf, or ['1'] for a plain G-code file."""
    path = Path(path)
    if path.suffix.lower() == ".3mf":
        with zipfile.ZipFile(path) as z:
            found = []
            for name in z.namelist():
                m = re.fullmatch(r"Metadata/plate_(\d+)\.gcode", name)
                if m:
                    found.append(m.group(1))
            return sorted(found, key=int)
    return ["1"]


def gcode_text(path, plate=None):
    """The G-code text of one plate of a produced file, whatever the
    slicer wrote: .gcode.3mf (Orca family), .gcode or .bgcode
    (PrusaSlicer). Raises ValueError with plain words when the file
    holds no G-code for that plate."""
    path = Path(path)
    if path.suffix.lower() == ".3mf":
        plates = plates_in_output(path)
        if not plates:
            raise ValueError("The sliced file holds no G-code.")
        if plate in (None, "", "0"):
            plate = plates[0]
        if str(plate) not in plates:
            raise ValueError(f"The sliced file holds no plate {plate}.")
        with zipfile.ZipFile(path) as z:
            return z.read(f"Metadata/plate_{plate}.gcode").decode(
                "utf-8", "replace")
    data = path.read_bytes()
    if bgcode.is_binary(data):
        return bgcode.gcode_text(data)
    return data.decode("utf-8", "replace")


def read(path, plate=None):
    """Toolpath of one plate of a produced file."""
    return parse(gcode_text(path, plate))


__all__ = ["KINDS", "KIND_INDEX", "parse", "payload", "facts",
           "layer_facts", "friendly_mm", "plates_in_output",
           "gcode_text", "read", "Toolpath", "Layer", "MAGIC"]
