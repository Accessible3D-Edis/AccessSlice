"""geometry.py - reading object shapes out of a project's 3MF entries.

A Bambu project stores each object's triangle mesh in 3D/Objects/*.model
files that the build items in 3D/3dmodel.model reference through
component entries. Nothing in this module writes anything: it parses
vertices and triangles, applies transforms, and answers the questions
the Placement tab's automatic commands need:

- how much floor space each object takes up and where its true centre
  is (auto-arrange, and keeping a scaled object ON the plate),
- which way an object should be turned so its most printable side lies
  on the plate (auto-orient), and
- how footprints pack onto one plate without overlapping.

Pure stdlib, pure functions. ProjectSession does the caching - meshes
are big (the test Benchy alone has 94745 triangles) but never edited,
so each one is parsed once per open project.
"""

import math
import re

# How much air automatic arranging leaves between objects, and between
# any object and the plate edge (millimetres). 10 mm clears skirts and
# ordinary brims; sequential (object-by-object) printing needs far more
# and gets a spoken caution instead.
ARRANGE_GAP_MM = 10.0
ARRANGE_MARGIN_MM = 5.0

# Faces steeper than 45 degrees count as unsupported overhang, the
# usual FDM rule of thumb.
_OVERHANG_COS = math.cos(math.radians(45))

# ---------------------------------------------------------- transforms
# A 3MF item transform is 12 numbers, row-major: three rows of the
# rotation/scale matrix, then the translation. Points transform as ROW
# vectors: world = point x M + translation - the same convention the
# staging code in slicer_project uses (verified against Studio's own
# files and its plate_1.json bounding boxes).


def transform_point(nums, p):
    """Apply a full 12-number item transform to one point."""
    x, y, z = p
    return (x * nums[0] + y * nums[3] + z * nums[6] + nums[9],
            x * nums[1] + y * nums[4] + z * nums[7] + nums[10],
            x * nums[2] + y * nums[5] + z * nums[8] + nums[11])


def rotate_point(rows9, p):
    """Apply just a 9-number rotation/scale part (no translation)."""
    x, y, z = p
    return (x * rows9[0] + y * rows9[3] + z * rows9[6],
            x * rows9[1] + y * rows9[4] + z * rows9[7],
            x * rows9[2] + y * rows9[5] + z * rows9[8])


def matmul9(a, b):
    """Compose two 9-number parts: applying a then b is a x b."""
    out = []
    for r in (0, 3, 6):
        for c in (0, 1, 2):
            out.append(a[r] * b[c] + a[r + 1] * b[3 + c]
                       + a[r + 2] * b[6 + c])
    return out


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def rotation_taking(frm, to):
    """The 9-number row-vector rotation with frm x Q = to.

    frm and to must be unit vectors. Built as a column-vector Rodrigues
    rotation and transposed, because row vectors compose as v x Q.
    """
    axis = _cross(frm, to)
    s = math.sqrt(_dot(axis, axis))
    c = _dot(frm, to)
    if s < 1e-12:
        if c > 0:
            return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        # Opposite directions: turn half way round about any axis that
        # is perpendicular to frm.
        pick = (1.0, 0.0, 0.0) if abs(frm[0]) < 0.9 else (0.0, 1.0, 0.0)
        axis = _cross(frm, pick)
        s2 = math.sqrt(_dot(axis, axis))
        axis = (axis[0] / s2, axis[1] / s2, axis[2] / s2)
        angle = math.pi
    else:
        axis = (axis[0] / s, axis[1] / s, axis[2] / s)
        angle = math.atan2(s, c)
    ca, sa = math.cos(angle), math.sin(angle)
    ax, ay, az = axis
    # Column-vector Rodrigues matrix R (R x column = rotated column).
    col = [
        [ca + ax * ax * (1 - ca), ax * ay * (1 - ca) - az * sa,
         ax * az * (1 - ca) + ay * sa],
        [ay * ax * (1 - ca) + az * sa, ca + ay * ay * (1 - ca),
         ay * az * (1 - ca) - ax * sa],
        [az * ax * (1 - ca) - ay * sa, az * ay * (1 - ca) + ax * sa,
         ca + az * az * (1 - ca)],
    ]
    # Row-vector form is the transpose.
    return [col[c][r] for r in range(3) for c in range(3)]


def bbox(points):
    """(minx, miny, minz, maxx, maxy, maxz) of an iterable of points."""
    xs, ys, zs = zip(*points)
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


# -------------------------------------------------------- mesh reading


def components_of(model_xml, objectid):
    """The mesh pieces one build object is made of.

    Returns [(entry_name, mesh_object_id, transform_nums_or_None)].
    entry_name None means the mesh sits inline in 3dmodel.model itself
    (not how Studio saves, but valid 3MF and used by the tests).
    """
    m = re.search(rf'<object id="{objectid}"[^>]*>(.*?)</object>',
                  model_xml, re.S)
    if not m:
        return []
    block = m.group(1)
    result = []
    if "<mesh" in block:
        result.append((None, objectid, None))
    for tag in re.finditer(r"<component\b[^>]*/>", block):
        t = tag.group(0)
        oid = re.search(r'\bobjectid="(\d+)"', t)
        if not oid:
            continue
        path = re.search(r'p:path="([^"]*)"', t)
        tf = re.search(r'\btransform="([^"]*)"', t)
        nums = None
        if tf:
            try:
                vals = [float(v) for v in tf.group(1).split()]
            except ValueError:
                vals = []
            if len(vals) == 12:
                nums = vals
        result.append((path.group(1).lstrip("/") if path else None,
                       oid.group(1), nums))
    return result


def parse_mesh(model_text, mesh_object_id):
    """(vertices, triangles) of one object's mesh in a model XML."""
    m = re.search(rf'<object id="{mesh_object_id}"[^>]*>(.*?)</object>',
                  model_text, re.S)
    if not m:
        return [], []
    block = m.group(1)
    verts = [(float(x), float(y), float(z)) for x, y, z in re.findall(
        r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', block)]
    tris = [(int(a), int(b), int(c)) for a, b, c in re.findall(
        r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', block)]
    return verts, tris


def shape_of_object(model_xml, entries, objectid):
    """Every vertex and triangle of one build object, in the object's
    own item coordinates (component transforms applied). None when no
    readable mesh was found - the caller says so honestly."""
    verts, tris = [], []
    for entry_name, mesh_id, nums in components_of(model_xml, objectid):
        if entry_name is None:
            text = model_xml
        else:
            raw = entries.get(entry_name)
            if raw is None:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        v, t = parse_mesh(text, mesh_id)
        if not v or not t:
            continue
        if nums:
            v = [transform_point(nums, p) for p in v]
        base = len(verts)
        verts.extend(v)
        tris.extend((a + base, b + base, c + base) for a, b, c in t
                    if a < len(v) and b < len(v) and c < len(v))
    return (verts, tris) if verts and tris else None


# --------------------------------------------------------- auto-orient


def best_down_direction(verts, tris, candidates=14):
    """The direction that should point straight down for the most
    printable lie, or None when the current bottom already wins.

    verts and tris must already be in world ORIENTATION (rotation and
    scale applied; translation does not matter). Candidate directions
    are the model's biggest flat areas, found by adding up triangle
    areas per (quantised) normal direction. Each candidate is scored
    by how much surface would rest flat on the plate (weighted up)
    minus how much would hang unsupported - overhang counts by how far
    past 45 degrees it tips, so gentle printable curves barely matter
    and face-down surfaces matter fully.

    When the model currently RESTS on real flat contact, it only loses
    to a candidate that beats it by a tenth of its whole surface area:
    a part on its side or face down flips decisively, while a
    sculpture that is overhung whichever way it lies (tested on the
    Benchy: laying it on its back scores mildly better but is plainly
    worse to print) stays as its designer placed it. A lie with almost
    no flat contact - balancing on an edge or a curve - is never worth
    keeping, so then the best candidate wins outright.
    """
    tri_data = []
    for a, b, c in tris:
        va, vb, vc = verts[a], verts[b], verts[c]
        n = _cross((vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]),
                   (vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]))
        length = math.sqrt(_dot(n, n))
        if length < 1e-12:
            continue
        tri_data.append(((n[0] / length, n[1] / length, n[2] / length),
                         length / 2.0, (a, b, c)))
    if not tri_data:
        return None

    # Group triangle area by direction; the weighted sum keeps the
    # exact direction of a flat face even when quantisation splits
    # hairs around it.
    buckets = {}
    for n, area, _ in tri_data:
        key = (round(n[0], 2), round(n[1], 2), round(n[2], 2))
        b = buckets.setdefault(key, [0.0, 0.0, 0.0, 0.0])
        b[0] += area
        b[1] += n[0] * area
        b[2] += n[1] * area
        b[3] += n[2] * area
    ranked = sorted(buckets.values(), key=lambda b: b[0], reverse=True)
    cands = []
    for b in ranked[:candidates]:
        length = math.sqrt(b[1] ** 2 + b[2] ** 2 + b[3] ** 2)
        if length > 1e-9:
            cands.append((b[1] / length, b[2] / length, b[3] / length))
    down = (0.0, 0.0, -1.0)

    total_area = sum(area for _n, area, _t in tri_data)

    def score(n):
        # The model rests where it reaches farthest along n; after the
        # turn, a triangle's steepness is its normal's dot with n.
        proj = [_dot(v, n) for v in verts]
        floor = max(proj)
        bottom = overhang = 0.0
        for un, area, (a, b, c) in tri_data:
            nz = -_dot(un, n)
            touching = min(proj[a], proj[b], proj[c]) > floor - 0.4
            if nz < -0.999 and touching:
                bottom += area
            elif nz < -_OVERHANG_COS and not touching:
                overhang += area * (-nz - _OVERHANG_COS) / (1 - _OVERHANG_COS)
        return 3.0 * bottom - overhang, bottom

    current, cur_bottom = score(down)
    best_n, best_s = None, current
    for n in cands:
        s, _bottom = score(n)
        if s > best_s:
            best_n, best_s = n, s
    if best_n is None or _dot(best_n, down) > 0.9999:
        return None
    # Resting on real contact (relative to the model's footprint) is
    # worth keeping unless a candidate wins decisively; balancing on
    # an edge or a curve is not.
    bb = bbox(verts)
    footprint = max(bb[3] - bb[0], 1e-9) * max(bb[4] - bb[1], 1e-9)
    stable = cur_bottom >= 0.02 * footprint
    margin = max(1.0, 0.10 * total_area) if stable else 1.0
    if best_s <= current + margin:
        return None
    # Snap mesh noise out of the direction, so a straight flip turns
    # about a clean axis instead of spinning the part a little too.
    snapped = tuple(0.0 if abs(c) < 1e-3 else c for c in best_n)
    length = math.sqrt(_dot(snapped, snapped))
    return (snapped[0] / length, snapped[1] / length,
            snapped[2] / length) if length > 1e-9 else best_n


# -------------------------------------------------------- auto-arrange


def arrange_rects(sizes, plate_w, plate_d, gap=ARRANGE_GAP_MM,
                  margin=ARRANGE_MARGIN_MM, keepout=None):
    """Give each footprint its own spot on one plate.

    sizes is [(width, depth)] in mm. Returns plate-local (cx, cy)
    centres in the SAME order (front-left corner of the plate is 0,0),
    or None when they cannot all fit. Simple shelf layout: biggest
    first, rows filling from the front, the finished bundle centred on
    the plate. keepout is the (width, depth) of an excluded front-left
    corner (some printers park a cutter or wiper there); the bundle is
    pushed off it when they would collide.
    """
    if not sizes:
        return []
    avail_w = plate_w - 2 * margin
    avail_d = plate_d - 2 * margin
    order = sorted(range(len(sizes)),
                   key=lambda i: sizes[i][0] * sizes[i][1], reverse=True)
    placed = {}
    x = y = row_d = used_w = 0.0
    for i in order:
        w, d = sizes[i]
        if w > avail_w or d > avail_d:
            return None
        if x > 0 and x + w > avail_w:
            y += row_d + gap
            x = row_d = 0.0
        placed[i] = (x + w / 2, y + d / 2)
        x += w + gap
        used_w = max(used_w, x - gap)
        row_d = max(row_d, d)
    used_d = y + row_d
    if used_d > avail_d:
        return None
    dx = (plate_w - used_w) / 2
    dy = (plate_d - used_d) / 2
    if keepout:
        kw, kd = keepout
        if dx < kw and dy < kd:
            # Prefer sliding the bundle back (deeper), else sideways.
            if kd + used_d <= plate_d - margin:
                dy = kd + gap / 2
            elif kw + used_w <= plate_w - margin:
                dx = kw + gap / 2
            else:
                return None
    return [(placed[i][0] + dx, placed[i][1] + dy)
            for i in range(len(sizes))]


# ------------------------------------------------- overhang measuring


def overhang_report(verts, tris, nums, threshold_deg, plate_z=0.0,
                    contact_mm=0.5):
    """How much of one object's underside hangs in the air.

    verts and tris are the object's mesh in item coordinates; nums is
    its full 12-number item transform, so scale and rotation count and
    heights come out above the real plate. A face is counted when it
    faces downward at a slope FLATTER than threshold_deg from the
    horizontal - the rule every slicer in the Orca family (Bambu
    Studio, OrcaSlicer, Creality Print) and PrusaSlicer apply to their
    support threshold: 0 degrees is a flat underside, 90 a vertical
    wall, and supports grow under anything below the threshold. Faces
    resting on the plate (every corner within contact_mm of plate_z)
    are the object's footprint, not overhang.

    Returns None when the mesh has no usable face, else a dict:
      area        - counted overhang surface, square mm
      total       - the object's whole surface, square mm
      flattest    - slope of the flattest counted face, degrees
      lowest      - height above the plate of the lowest counted face
      highest     - height above the plate of the highest counted face
      contact     - surface resting ON the plate (downward faces with
                    every corner within contact_mm), square mm - the
                    first layer's real footprint
    """
    world = [transform_point(nums, v) for v in verts]
    cos_limit = math.cos(math.radians(threshold_deg))
    total = area = contact = 0.0
    flattest = 90.0
    lowest = highest = None
    for a, b, c in tris:
        va, vb, vc = world[a], world[b], world[c]
        n = _cross((vb[0] - va[0], vb[1] - va[1], vb[2] - va[2]),
                   (vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]))
        length = math.sqrt(_dot(n, n))
        if length < 1e-12:
            continue
        tri_area = length / 2.0
        total += tri_area
        down = -n[2] / length          # 1 = fully face-down
        top = max(va[2], vb[2], vc[2])
        if down > 0.7 and top <= plate_z + contact_mm:
            contact += tri_area        # resting on the plate
            continue
        if down <= cos_limit:
            continue                   # upward, vertical, or steep enough
        if top <= plate_z + contact_mm:
            continue                   # a sliver at the plate, not overhang
        area += tri_area
        slope = math.degrees(math.acos(min(1.0, down)))
        flattest = min(flattest, slope)
        bottom = min(va[2], vb[2], vc[2]) - plate_z
        lowest = bottom if lowest is None else min(lowest, bottom)
        highest = bottom if highest is None else max(highest, bottom)
    if total <= 0:
        return None
    return {"area": area, "total": total, "flattest": flattest,
            "lowest": lowest, "highest": highest, "contact": contact}


# ------------------------------------------------ footprints and gaps
# By-object printing asks a question auto-arrange never had to: how
# much air is between two FINISHED objects, measured the way the
# slicers measure it - between the objects' convex outlines seen from
# above (Bambu Studio and the Orca family grow each outline by half the
# extruder clearance radius and refuse when they touch; so the gap must
# be at least the whole radius).


def convex_hull_xy(points):
    """The counter-clockwise convex outline of points seen from above
    (x, y pairs; z is ignored). Andrew's monotone chain. Fewer than
    three distinct points give whatever is there."""
    pts = sorted(set((p[0], p[1]) for p in points))
    if len(pts) <= 2:
        return pts

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _point_segment_distance(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length2 = dx * dx + dy * dy
    if length2 < 1e-18:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _inside_convex(p, hull):
    """True when p lies inside (or on) a counter-clockwise hull."""
    n = len(hull)
    if n < 3:
        return False
    for i in range(n):
        a, b = hull[i], hull[(i + 1) % n]
        if ((b[0] - a[0]) * (p[1] - a[1])
                - (b[1] - a[1]) * (p[0] - a[0])) < -1e-9:
            return False
    return True


def _segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    o1, o2 = orient(p1, p2, p3), orient(p1, p2, p4)
    o3, o4 = orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 != o2 and o3 != o4 and o1 != 0 and o2 != 0 \
        and o3 != 0 and o4 != 0


def hull_gap(h1, h2):
    """The shortest distance between two convex outlines in mm; 0 when
    they touch or overlap. Brute force over edges - outlines are a
    few hundred points at most."""
    if not h1 or not h2:
        return None
    if len(h1) == 1 and len(h2) == 1:
        return math.hypot(h1[0][0] - h2[0][0], h1[0][1] - h2[0][1])
    if any(_inside_convex(p, h2) for p in h1) \
            or any(_inside_convex(p, h1) for p in h2):
        return 0.0
    e1 = [(h1[i], h1[(i + 1) % len(h1)]) for i in range(len(h1))] \
        if len(h1) > 1 else []
    e2 = [(h2[i], h2[(i + 1) % len(h2)]) for i in range(len(h2))] \
        if len(h2) > 1 else []
    for a, b in e1:
        for c, d in e2:
            if _segments_cross(a, b, c, d):
                return 0.0
    best = None
    for p in h1:
        for c, d in e2:
            dist = _point_segment_distance(p, c, d)
            best = dist if best is None else min(best, dist)
    for p in h2:
        for a, b in e1:
            dist = _point_segment_distance(p, a, b)
            best = dist if best is None else min(best, dist)
    if best is None:
        best = min(math.hypot(p[0] - q[0], p[1] - q[1])
                   for p in h1 for q in h2)
    return best
