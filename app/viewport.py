"""viewport.py - what the 3D view is told about the open project.

Phase IV of the real-slicer plan. The one design principle: the
viewport is a MIRROR, never the master. Every operation exists as a
real, labelled control on the Placement tab; the canvas shows the
same state and adds mouse conveniences (click to select, drag to
move) that post the very same staged changes the fields produce.
Nothing exists only in the canvas, and a screen reader never meets it.

Two things are served to the page's script (static\\viewport.js):

- scene(session, plate_id): a small JSON description - plate size and
  layout, the keep-out corner, and every object on the shown plate
  with its EFFECTIVE transform (staged moves, turns, scales and
  auto-orient results included, so what the view shows is what the
  save will write), its filament colour, and whether it hangs over
  the plate edge (the same footprint the pre-flight checks use).
- mesh_bytes(session, objectid): the object's triangles in its own
  item coordinates as one compact binary blob (header, float32
  vertices, uint32 triangle indices). The item transform is applied
  in the browser, so a staged move never re-sends a mesh; meshes are
  packed once per open project and cached.

Per-plate loading is what keeps the 36-plate life-sized projects
usable: only the shown plate's meshes travel, and the biggest objects
(over half a million triangles) still take a couple of seconds to
read from the 3MF's XML - the page says so while it waits.
"""

import struct

MESH_MAGIC = b"ASM1"  # AccessSlice mesh, version 1

# A default object colour when the project carries none for its
# filament (a mid blue that reads on both themes' plates).
DEFAULT_COLOUR = "#4f8fd6"


def _colour_of(session, obj):
    """The object's filament colour from the project settings, by its
    extruder number (1-based), else the default."""
    colours = session.settings.get("filament_colour")
    if not isinstance(colours, list):
        colours = session.settings.get("extruder_colour")
    try:
        index = int(obj.get("extruder") or 1) - 1
        value = str(colours[index]).strip()
        if len(value) == 7 and value.startswith("#"):
            return value
    except (TypeError, ValueError, IndexError):
        pass
    return DEFAULT_COLOUR


def _footprint(session, objectid):
    """World bounding box at the effective placement, via the pre-
    flight module's cached footprint so the view and the warnings
    agree; None when the mesh cannot be read."""
    try:
        import preflight
        fp = preflight.object_footprint(session, objectid)
    except Exception:
        return None
    return fp["bbox"] if fp else None


def _plate_layout(session):
    """[(plate dict, origin or None)] and the plate size.

    Origins follow the PLANNED grid (staged new plates can change the
    column count), and every staged-but-unsaved plate is drawn as an
    outline of its own, so the picture matches effective_item_nums."""
    plates = session.plates()
    size = session.plate_size()
    try:
        planned = session.planned_layout()
    except Exception:
        planned = None
    layout = []
    for p in plates:
        origin = planned.get(p["id"]) if planned else None
        if origin is None:
            try:
                origin = session.plate_origin(p["id"])
            except Exception:
                origin = None
        layout.append((p, origin))
    if planned:
        staged_to = {}
        for c in session.pending:
            if c["kind"] == "replate_new":
                staged_to.setdefault(c["slot"], []).append(c["objectid"])
        for key, origin in planned.items():
            if isinstance(key, tuple):
                layout.append(({
                    "id": f"new-{key[1]}",
                    "name": "new plate (added when you save)",
                    "locked": False, "object_ids": staged_to.get(key[1], []),
                    "print_sequence": None, "spiral_mode": None}, origin))
    return layout, size


def default_plate(session):
    """The plate the view opens on: the first plate that has objects,
    else the first plate, else '' (a project without plate blocks)."""
    plates = session.plates()
    for p in plates:
        if p["object_ids"]:
            return p["id"]
    return plates[0]["id"] if plates else ""


def scene(session, plate_id=None, all_plates=False):
    """The JSON-ready description of what to draw.

    plate_id: the plate whose objects carry meshes (default: the
    first plate with objects). all_plates: every object everywhere
    (small projects, or the user's explicit choice) - the plate
    outlines are always all drawn so the layout stays readable.
    """
    layout, size = _plate_layout(session)
    if plate_id in (None, ""):
        plate_id = default_plate(session)
    shown = None
    for p, origin in layout:
        if p["id"] == plate_id:
            shown = p
    if shown is None and layout:
        shown = layout[0][0]
        plate_id = shown["id"]

    plate_of = {}
    for p, _origin in layout:
        for oid in p["object_ids"]:
            plate_of[oid] = p["id"]
    for c in session.pending:
        if c.get("kind") == "replate":
            plate_of[c["objectid"]] = c["target"]

    origins = {p["id"]: origin for p, origin in layout}
    keepout = None
    try:
        keepout = session._front_left_keepout()
    except Exception:
        keepout = None

    objects = []
    for obj in session.objects():
        oid = obj["id"]
        pid = plate_of.get(oid)
        on_shown = (pid == plate_id) or (pid is None and not layout)
        if not (all_plates or on_shown):
            continue
        nums = session.effective_item_nums(oid)
        if nums is None:
            continue
        has_mesh = session.object_shape(oid) is not None
        bbox = _footprint(session, oid) if has_mesh else None
        off_plate = False
        origin = origins.get(pid) or (0.0, 0.0)
        if bbox and size:
            minx, miny = bbox[0] - origin[0], bbox[1] - origin[1]
            maxx, maxy = bbox[3] - origin[0], bbox[4] - origin[1]
            off_plate = (minx < -0.01 or miny < -0.01
                         or maxx > size[0] + 0.01 or maxy > size[1] + 0.01)
        objects.append({
            "id": oid,
            "name": obj["name"],
            "plate": pid,
            "nums": [float(v) for v in nums[:12]],
            "colour": _colour_of(session, obj),
            "has_mesh": has_mesh,
            "bbox": list(bbox) if bbox else None,
            "off_plate": off_plate,
            "staged": any(c.get("objectid") == oid for c in session.pending),
        })

    plates_out = []
    for p, origin in layout:
        plates_out.append({
            "id": p["id"],
            "name": p["name"],
            "origin": list(origin) if origin else None,
            "object_count": len(p["object_ids"]),
        })

    height = None
    for key in ("printable_height", "max_print_height"):
        value = session.settings.get(key)
        try:
            height = float(str(value).strip())
            break
        except (TypeError, ValueError):
            continue

    return {
        "plate_size": list(size) if size else None,
        "printable_height": height,
        "keepout": list(keepout) if keepout else None,
        "plates": plates_out,
        "shown_plate": plate_id,
        "all_plates": bool(all_plates),
        "objects": objects,
        "project": session.path.name,
    }


def mesh_bytes(session, objectid):
    """The object's mesh as one binary blob, or None without a mesh:

        magic 'ASM1' | uint32 vertex count | uint32 triangle count |
        float32 x,y,z per vertex | uint32 a,b,c per triangle

    Vertices are in the object's own item coordinates (component
    transforms applied, the build item's transform NOT - the browser
    applies that one, so staged moves cost nothing to show). Packed
    once per open project and cached on the session.
    """
    cache = getattr(session, "_mesh_pack_cache", None)
    if cache is None:
        cache = {}
        try:
            session._mesh_pack_cache = cache
        except AttributeError:
            pass
    if objectid in cache:
        return cache[objectid]
    shape = session.object_shape(objectid)
    if shape is None:
        cache[objectid] = None
        return None
    verts, tris = shape
    flat_v = [c for p in verts for c in p]
    flat_t = [i for t in tris for i in t]
    blob = (MESH_MAGIC
            + struct.pack("<II", len(verts), len(tris))
            + struct.pack(f"<{len(flat_v)}f", *flat_v)
            + struct.pack(f"<{len(flat_t)}I", *flat_t))
    cache[objectid] = blob
    return blob


def reset_cache(session):
    """Forget packed meshes (after a reload, the file may differ)."""
    try:
        session._mesh_pack_cache = {}
    except AttributeError:
        pass


def describe(scene_data):
    """One plain sentence about the shown plate, for the panel's text
    (the accessible summary of what the picture shows)."""
    objs = scene_data["objects"]
    n = len(objs)
    if scene_data["all_plates"]:
        where = "all plates"
    elif scene_data["shown_plate"]:
        where = f"plate {scene_data['shown_plate']}"
    else:
        where = "the plate"
    if n == 0:
        return f"The 3D view shows {where} with nothing on it."
    names = ", ".join(o["name"] for o in objs[:6])
    if n > 6:
        names += f" and {n - 6} more"
    over = [o["name"] for o in objs if o["off_plate"]]
    text = (f"The 3D view shows {where} with {n} object"
            f"{'s' if n != 1 else ''}: {names}.")
    if over:
        text += (" Hanging over the plate edge: " + ", ".join(over)
                 + ".")
    return text


def plate_choices(session):
    """[(plate id, label)] for the panel's plate chooser; empty for a
    single-plate project (no chooser is shown then)."""
    plates = session.plates()
    if len(plates) <= 1:
        return []
    out = []
    for p in plates:
        n = len(p["object_ids"])
        label = f"Plate {p['id']}"
        if p["name"]:
            label += f" - {p['name']}"
        label += f" ({n} object{'s' if n != 1 else ''})"
        out.append((p["id"], label))
    return out

