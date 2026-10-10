"""preflight.py - warnings spoken BEFORE a slice is wasted.

Requested by Edis 2026-09-01: a "Warnings before slicing" list on the
Slice-and-send page, computed by AccessSlice itself from the project's
settings and the objects' real meshes - so a blind user hears about a
doomed job before the engine spends minutes on it, alongside the
engine's own warnings the slice report already quotes.

Design rules (docs\\todo.md):
- Warnings never block. The user decides ("never act uninvited").
- Plain words with real numbers, every number from the file.
- Every check is test-pinned; the wording is judged by Edis under NVDA.
- Nothing that exists only as visual paint in a slicer's preview.

Slicer independence: the checks read settings through a small key
table. Bambu Studio, OrcaSlicer and Creality Print share the Orca-
family key names (verified on real projects from the H2C, the X1C and
the K2 Plus); where the family drifted apart (Bambu Studio 2.x renamed
the clearance radius and added the nozzle-to-rod distance) the table
lists every spelling and the first one present wins. A PrusaSlicer
backend passes its own table (INI keys support_material,
support_material_threshold, complete_objects, extruder_clearance_*)
when it arrives. The mesh side is shared by every 3MF-based slicer.

Checks, in the build order Edis chose:
1. SUPPORTS - supports off, but an object has significant faces
   flatter than the settings' overhang threshold with nothing under
   them (2026-09-02).
2. HEAD CRASH class, all for by-object printing (2026-09-02): an
   object taller than the lid clearance; an object taller than the
   gantry-rod clearance with another object in its front-to-back
   band; two objects closer than the extruder clearance radius.
3. Cheap same-family checks (2026-09-02): an object floating
   above or sunk into the plate, taller than the printer prints,
   a tiny first-layer contact (adhesion risk), and spiral vase
   with more than one object on a plate.
"""

import math

import geometry

# Setting keys per slicer family. A value is the key as it appears in
# the project's settings entry, or a tuple of spellings to try in
# order (Bambu Studio 2.x: extruder_clearance_max_radius; Creality
# Print 7.2 and older Orca: extruder_clearance_radius).
ORCA_FAMILY_KEYS = {
    "enable_support": "enable_support",
    "threshold": "support_threshold_angle",
    "sequence": "print_sequence",
    "height_to_rod": "extruder_clearance_height_to_rod",
    "height_to_lid": "extruder_clearance_height_to_lid",
    "dist_to_rod": "extruder_clearance_dist_to_rod",
    "radius": ("extruder_clearance_max_radius",
               "extruder_clearance_radius"),
    "spiral": "spiral_mode",
    "printable_height": "printable_height",
    "brim_type": "brim_type",
    "raft_layers": "raft_layers",
}

# Check 3 thresholds. An object whose lowest point is further than
# this from the plate is floating (or sunk); a first layer smaller
# than TINY_CONTACT_MM2 (a 5 x 5 mm foot) or than TINY_CONTACT_SHARE
# of the object's outline, under a part taller than TINY_CONTACT_MIN_H,
# is a foot that can come loose mid-print.
FLOAT_MM = 0.5
TINY_CONTACT_MM2 = 25.0
TINY_CONTACT_SHARE = 0.10
TINY_CONTACT_MIN_H = 10.0

# The lever rule: a part taller than LEVER_RATIO times the width of
# its foot (the square root of the plate contact) sways as the head
# passes, even on a decent foot - a 10 x 10 mm foot carries 80 mm.
LEVER_RATIO = 8.0

# The print_sequence value that means one whole object at a time.
BY_OBJECT = "by object"

# The FDM rule of thumb, used only when a project's threshold is
# missing, unreadable, or 0 (some slicers spell "automatic" as 0).
DEFAULT_THRESHOLD_DEG = 45.0

# Overhang worth speaking about: at least a 10 x 10 mm patch, or at
# least one percent of the object's whole surface - so a thin cane's
# few square millimetres of snake head count, and an accidental
# sliver of near-flat hull does not.
SIGNIFICANT_MM2 = 100.0
SIGNIFICANT_SHARE = 0.01

# A face whose every corner lies this close to the plate is footprint,
# not overhang - the same tolerance the auto-orient scorer uses.
CONTACT_MM = 0.5


def _is_on(value):
    """Orca-family booleans are the strings "1" / "0"."""
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _threshold(value):
    """(degrees, came_from_the_file)."""
    try:
        degrees = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD_DEG, False
    if degrees <= 0 or degrees >= 90:
        return DEFAULT_THRESHOLD_DEG, False
    return degrees, True


def _number(session, keyspec):
    """The first present, parseable number among the key spellings,
    else None. Lists (per-extruder values) take their first entry."""
    for key in ((keyspec,) if isinstance(keyspec, str) else keyspec):
        value = session.effective_setting(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None:
            continue
        try:
            return float(str(value).strip())
        except ValueError:
            continue
    return None


def _mm(value):
    return f"{value:.0f}"


def _names(names):
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _area_words(mm2):
    if mm2 >= 100:
        cm2 = mm2 / 100.0
        if cm2 >= 10:
            shown = f"{cm2:.0f}"
        else:
            shown = f"{cm2:.1f}".rstrip("0").rstrip(".")
        unit = "square centimetre" if shown == "1" else "square centimetres"
        return f"about {shown} {unit}"
    rounded = max(5, int(round(mm2 / 5.0)) * 5)
    return f"about {rounded} square millimetres"


def _height_words(lowest, highest):
    lo = max(0, int(round(lowest)))
    hi = max(0, int(round(highest)))
    if hi <= 0:
        return "right at the plate"
    if lo <= 0:
        return f"from the plate up to {hi} mm above it"
    if hi - lo <= 1:
        return f"about {lo} mm above the plate"
    return f"between {lo} and {hi} mm above the plate"


def _flattest_words(degrees):
    if degrees < 1.0:
        return "fully horizontal"
    return f"at {degrees:.0f} degrees from horizontal"


def _cache(session):
    """The session's per-placement report cache (created on demand;
    reached through getattr so a thin view over a session shares the
    real session's cache)."""
    cache = getattr(session, "_preflight_cache", None)
    if cache is None:
        cache = {}
        try:
            session._preflight_cache = cache
        except AttributeError:
            pass
    return cache


def _plate_of(session):
    """{objectid: plate id} as it WILL be once staged move-to-plate
    changes are saved - the saved membership with pending replates
    laid over it."""
    result = {}
    for p in session.plates():
        for oid in p["object_ids"]:
            result[oid] = p["id"]
    for c in session.pending:
        if c.get("kind") == "replate":
            result[c["objectid"]] = c["target"]
    return result


def _by_object_plates(session, keys):
    """{plate id: True/False} - whether each plate prints one object at
    a time: its own print_sequence when the plate block carries one
    (Bambu Studio's per-plate setting), else the project-wide value.
    None (no plate) follows the project-wide value too."""
    global_seq = str(session.effective_setting(keys["sequence"]) or "")
    global_by = global_seq.strip().lower() == BY_OBJECT
    result = {None: global_by}
    for p in session.plates():
        own = p.get("print_sequence")
        if own is None or str(own).strip() == "":
            result[p["id"]] = global_by
        else:
            result[p["id"]] = str(own).strip().lower() == BY_OBJECT
    return result


def _spiral_plates(session, keys):
    """{plate id: True/False} - spiral vase per plate, the plate's own
    value first, else the project-wide one."""
    global_on = _is_on(session.effective_setting(keys["spiral"]))
    result = {None: global_on}
    for p in session.plates():
        own = p.get("spiral_mode")
        if own is None or str(own).strip() == "":
            result[p["id"]] = global_on
        else:
            result[p["id"]] = _is_on(own)
    return result


def object_overhang(session, objectid, threshold_deg):
    """The overhang report for one object at its EFFECTIVE placement
    (staged moves, turns and scales included), cached on the session
    per placement and threshold so the send page stays quick. None
    when the mesh or the placement cannot be read."""
    shape = session.object_shape(objectid)
    nums = session.effective_item_nums(objectid)
    if shape is None or nums is None:
        return None
    key = (objectid, tuple(nums), threshold_deg)
    cache = _cache(session)
    if key not in cache:
        cache[key] = geometry.overhang_report(
            shape[0], shape[1], nums, threshold_deg,
            contact_mm=CONTACT_MM)
    return cache[key]


def object_footprint(session, objectid):
    """{"bbox": world (minx, miny, minz, maxx, maxy, maxz), "hull":
    convex outline from above} at the object's EFFECTIVE placement,
    cached like the overhang report. None without a readable mesh."""
    shape = session.object_shape(objectid)
    nums = session.effective_item_nums(objectid)
    if shape is None or nums is None:
        return None
    key = (objectid, tuple(nums), "footprint")
    cache = _cache(session)
    if key not in cache:
        world = [geometry.transform_point(nums, v) for v in shape[0]]
        cache[key] = {"bbox": geometry.bbox(world),
                      "hull": geometry.convex_hull_xy(world)}
    return cache[key]


# ------------------------------------------------- check 1: supports


def support_warnings(session, keys=ORCA_FAMILY_KEYS):
    """Check 1: supports off with significant overhang, per object.

    Per-object overrides win over the project-wide values (an object
    with its own "enable supports" override is judged by it - the
    cane specimen from the X1C is exactly that case). Objects without
    a readable mesh are skipped: no data, no claim.
    """
    global_on = _is_on(session.effective_setting(keys["enable_support"]))
    global_thr = session.effective_setting(keys["threshold"])
    plates = session.plates()
    plate_of = _plate_of(session)
    found = []
    for obj in session.objects():
        over = session.effective_object_overrides(obj["id"])
        if keys["enable_support"] in over:
            on = _is_on(over[keys["enable_support"]])
        else:
            on = global_on
        if on:
            continue
        threshold, from_file = _threshold(
            over.get(keys["threshold"], global_thr))
        report = object_overhang(session, obj["id"], threshold)
        if report is None or report["area"] <= 0:
            continue
        significant = (report["area"] >= SIGNIFICANT_MM2
                       or report["area"] >= SIGNIFICANT_SHARE
                       * report["total"])
        if not significant:
            continue
        where = ""
        if len(plates) > 1 and obj["id"] in plate_of:
            where = f" (plate {plate_of[obj['id']]})"
        if from_file:
            rule = (f"less than {threshold:g} degrees from horizontal, "
                    "your overhang angle threshold")
        else:
            rule = (f"less than {threshold:g} degrees from horizontal, "
                    "the usual rule of thumb because the project's "
                    "threshold could not be read")
        found.append({
            "check": "supports",
            "objectid": obj["id"],
            "text": (
                f"{obj['name']}{where}: supports are off, but "
                f"{_area_words(report['area'])} of it faces downward at "
                f"{rule}, with nothing underneath. The flattest part is "
                f"{_flattest_words(report['flattest'])}, "
                f"{_height_words(report['lowest'], report['highest'])}. "
                "The slicer would put supports there if supports were "
                "on - see the Support tab - unless you know these are "
                "short bridges the printer can span."),
        })
    return found


# ------------------------------------- check 2: the head-crash class


def clearance_warnings(session, keys=ORCA_FAMILY_KEYS):
    """Check 2: by-object printing against the printer's clearances.

    Only when the print sequence is by object - printing by layer
    never brings a finished object under the printhead. Three rules,
    each read from the printer settings inside the project (so every
    printer Edis owns is judged by its own numbers - the H2C, X1C and
    K2 Plus projects all carry them):

    - LID: an object taller than extruder_clearance_height_to_lid
      cannot be printed one at a time at all.
    - ROD: an object taller than extruder_clearance_height_to_rod is
      hit by the gantry rod while a LATER object is printed in the
      same front-to-back band. The band is the tall object's depth
      range widened by the nozzle-to-rod distance where the project
      has it (Bambu Studio 2.x), else the plain overlap. Print order
      is not readable here, so two tall objects sharing a band are a
      certain hit and a tall object with a short neighbour is a hit
      unless the tall one is printed last - both said in words.
    - RADIUS: two finished outlines closer than the extruder
      clearance radius (Studio grows each outline by half of it and
      refuses when they meet). Measured between convex outlines seen
      from above, the way the slicers do it, never between boxes.

    Pairs are judged within one plate only. Objects without a
    readable mesh make no claim.
    """
    by_object = _by_object_plates(session, keys)
    if not any(by_object.values()):
        return []
    lid = _number(session, keys["height_to_lid"])
    rod = _number(session, keys["height_to_rod"])
    dist = _number(session, keys["dist_to_rod"]) or 0.0
    radius = _number(session, keys["radius"])

    plate_of = _plate_of(session)
    objs = []
    for obj in session.objects():
        plate = plate_of.get(obj["id"])
        if not by_object.get(plate, by_object[None]):
            continue   # this plate prints by layer: nothing to hit
        fp = object_footprint(session, obj["id"])
        if fp is None:
            continue
        bb = fp["bbox"]
        objs.append({"id": obj["id"], "name": obj["name"],
                     "plate": plate,
                     "height": bb[5], "miny": bb[1], "maxy": bb[4],
                     "hull": fp["hull"]})
    found = []

    # LID
    if lid is not None:
        for o in objs:
            if o["height"] > lid + 1e-6:
                found.append({
                    "check": "lid", "objectid": o["id"],
                    "text": (
                        f"{o['name']}: {_mm(o['height'])} mm tall, but "
                        "by-object printing on this printer allows at "
                        f"most {_mm(lid)} mm - the printhead's clearance "
                        "to the lid. Print by layer (Others tab, print "
                        "sequence), or lower or lay down the object.")})

    def same_band(a, b):
        return (a["plate"] == b["plate"]
                and b["miny"] <= a["maxy"] + dist
                and b["maxy"] >= a["miny"] - dist)

    # ROD
    if rod is not None:
        tall = [o for o in objs if o["height"] > rod + 1e-6]
        seen_pairs = set()
        for a in tall:
            others = [b for b in objs if b["id"] != a["id"]
                      and same_band(a, b)]
            tall_others = [b for b in others if b["height"] > rod + 1e-6]
            short_others = [b for b in others
                            if b["height"] <= rod + 1e-6]
            for b in tall_others:
                pair = frozenset((a["id"], b["id"]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                found.append({
                    "check": "rod", "objectid": a["id"],
                    "text": (
                        f"{a['name']} and {b['name']} are both taller "
                        f"than the {_mm(rod)} mm clearance under the "
                        f"printhead's gantry rod ({_mm(a['height'])} and "
                        f"{_mm(b['height'])} mm) and share the same "
                        "front-to-back band, so with by-object printing "
                        "the rod would hit whichever is finished first. "
                        "Move them apart front to back, or print by "
                        "layer (Others tab, print sequence).")})
            if short_others:
                verb = "lies" if len(short_others) == 1 else "lie"
                if dist:
                    band = f"{verb} within {_mm(dist)} mm of it front to back"
                else:
                    band = f"{verb} over the same front-to-back range"
                found.append({
                    "check": "rod", "objectid": a["id"],
                    "text": (
                        f"{a['name']} is {_mm(a['height'])} mm tall, more "
                        f"than the {_mm(rod)} mm clearance under the "
                        "printhead's gantry rod. With by-object printing "
                        "the rod would hit it while printing "
                        f"{_names([b['name'] for b in short_others])}, "
                        f"which {band} - unless {a['name']} is printed "
                        "last. Move them apart front to back, print "
                        f"{a['name']} last (Others tab, print order), or "
                        "print by layer.")})

    # RADIUS
    if radius is not None:
        for i, a in enumerate(objs):
            for b in objs[i + 1:]:
                if a["plate"] != b["plate"]:
                    continue
                gap = geometry.hull_gap(a["hull"], b["hull"])
                if gap is None or gap >= radius - 1e-6:
                    continue
                apart = ("touch or overlap" if gap < 0.5
                         else f"are only {_mm(gap)} mm apart")
                found.append({
                    "check": "radius", "objectid": a["id"],
                    "text": (
                        f"{a['name']} and {b['name']} {apart}, but "
                        "by-object printing on this printer needs at "
                        f"least {_mm(radius)} mm between objects - the "
                        "printhead's clearance radius - or it would hit "
                        "the finished one. Move them apart (Placement "
                        "tab), or print by layer (Others tab, print "
                        "sequence).")})
    return found


# ---------------------------------- check 3: the cheap same-family set


def _outline_area(hull):
    """Shoelace area of a convex outline, square mm."""
    n = len(hull)
    if n < 3:
        return 0.0
    return abs(sum(hull[i][0] * hull[(i + 1) % n][1]
                   - hull[(i + 1) % n][0] * hull[i][1]
                   for i in range(n))) / 2.0


def placement_warnings(session, keys=ORCA_FAMILY_KEYS):
    """Check 3a-3c, per object, from the same footprint numbers:

    - FLOATING / SUNK: the lowest point further than FLOAT_MM from
      the plate. The slicers drop objects onto the plate when they
      load them, but a file edited elsewhere can carry a placement
      they never saw; a first layer in the air prints nothing.
    - TOO TALL: taller than printable_height - the engine would refuse
      after the wait; here it is said before.
    - TINY CONTACT: the first layer's real plate contact (downward
      faces resting on the plate) under TINY_CONTACT_MM2 or under
      TINY_CONTACT_SHARE of the outline, for parts taller than
      TINY_CONTACT_MIN_H. Skipped when a raft is on (a raft is the
      contact). The fix names the brim setting's current state.
    """
    max_h = _number(session, keys["printable_height"])
    raft = _number(session, keys["raft_layers"]) or 0
    brim = str(session.effective_setting(keys["brim_type"]) or "").strip()
    found = []
    for obj in session.objects():
        fp = object_footprint(session, obj["id"])
        if fp is None:
            continue
        bb = fp["bbox"]
        name = obj["name"]
        height = bb[5] - bb[2]
        if bb[2] > FLOAT_MM:
            found.append({
                "check": "floating", "objectid": obj["id"],
                "text": (
                    f"{name} floats {_mm(bb[2])} mm above the plate: its "
                    "lowest point is not on the plate, so its first "
                    "layers would print in the air. Auto-orient it on "
                    "the Placement tab - that sets it down onto the "
                    "plate - or open the project in the slicer, which "
                    "drops objects onto the plate.")})
        elif bb[2] < -FLOAT_MM:
            found.append({
                "check": "sunk", "objectid": obj["id"],
                "text": (
                    f"{name} sinks {_mm(-bb[2])} mm into the plate: "
                    "everything below the plate is cut off and the "
                    "print starts with a flat slice through the object. "
                    "Auto-orient it on the Placement tab - that sets it "
                    "down onto the plate - or open the project in the "
                    "slicer, which drops objects onto the plate.")})
        if max_h is not None and bb[5] > max_h + 1e-6:
            found.append({
                "check": "too_tall", "objectid": obj["id"],
                "text": (
                    f"{name} reaches {_mm(bb[5])} mm, but this printer "
                    f"prints up to {_mm(max_h)} mm. Scale it down "
                    "(Placement tab), lay it down, or split it.")})
        if raft > 0 or height <= TINY_CONTACT_MIN_H or bb[2] > FLOAT_MM:
            continue
        report = object_overhang(session, obj["id"], DEFAULT_THRESHOLD_DEG)
        if report is None:
            continue
        contact = report["contact"]
        outline = _outline_area(fp["hull"])
        tiny = (contact < TINY_CONTACT_MM2
                or (outline > 0 and contact < TINY_CONTACT_SHARE * outline))
        lever = (not tiny and contact > 0
                 and height > LEVER_RATIO * math.sqrt(contact))
        if not tiny and not lever:
            continue
        if brim == "no_brim":
            fix = "Add a brim (Adhesion tab)."
        elif brim == "auto_brim":
            fix = ("The brim is set to automatic, so the slicer may add "
                   "one - an outer brim set by hand (Adhesion tab) "
                   "makes sure.")
        else:
            fix = ("A brim is already on - a raft (Adhesion tab) holds "
                   "better.")
        if tiny:
            found.append({
                "check": "contact", "objectid": obj["id"],
                "text": (
                    f"{name} stands on only {_area_words(contact)} of "
                    f"plate (its outline covers {_area_words(outline)}) "
                    f"and is {_mm(height)} mm tall: a small foot that can "
                    f"come loose mid-print. {fix} Or auto-orient the "
                    "object on the Placement tab so a bigger flat side "
                    "lies down.")})
        else:
            ratio = height / math.sqrt(contact)
            found.append({
                "check": "lever", "objectid": obj["id"],
                "text": (
                    f"{name} is {_mm(height)} mm tall on a foot of "
                    f"{_area_words(contact)} - about {ratio:.0f} times "
                    "taller than its foot is wide. A tall thin part sways "
                    "as the printhead passes and can snap off mid-print. "
                    f"{fix} Printing it slower helps too.")})
    return found


def spiral_warnings(session, keys=ORCA_FAMILY_KEYS):
    """Check 3d: spiral vase mode with more than one object on a plate
    while printing by layer. A vase is one continuous spiral, so the
    slicers refuse several objects at once unless the sequence is by
    object (then the clearance rules above apply)."""
    spiral = _spiral_plates(session, keys)
    if not any(spiral.values()):
        return []
    by_object = _by_object_plates(session, keys)
    plate_of = _plate_of(session)
    per_plate = {}
    for obj in session.objects():
        per_plate.setdefault(plate_of.get(obj["id"]), []).append(obj["name"])
    plates = session.plates()
    found = []
    for plate_id, names in per_plate.items():
        if len(names) < 2:
            continue
        if not spiral.get(plate_id, spiral[None]):
            continue
        if by_object.get(plate_id, by_object[None]):
            continue   # the clearance rules take over
        where = (f"plate {plate_id}" if len(plates) > 1 and plate_id
                 else "the plate")
        found.append({
            "check": "spiral", "objectid": None,
            "text": (
                f"Spiral vase mode is on, but {where} holds {len(names)} "
                f"objects ({_names(names)}) printed by layer. A vase is "
                "one continuous spiral, so the slicer refuses several "
                "objects at once. Keep one object per plate, or print by "
                "object (Others tab, print sequence) - then the clearance "
                "rules apply.")})
    return found


def warnings(session, keys=ORCA_FAMILY_KEYS):
    """Every pre-flight warning for the project, in check order.
    Each is {"check", "objectid", "text"}; the text is one spoken
    sentence group with real numbers."""
    result = []
    result.extend(support_warnings(session, keys))
    result.extend(clearance_warnings(session, keys))
    result.extend(placement_warnings(session, keys))
    result.extend(spiral_warnings(session, keys))
    return result
