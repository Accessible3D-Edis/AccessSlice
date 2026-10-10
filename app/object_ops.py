"""object_ops.py - copying, removing and importing objects, and adding
plates, at the file-format level.

The session (slicer_project.py, prusa_project.py) decides WHAT to do
and when; this module knows HOW each format stores an object, so the
same staged change writes correctly into either.

Bambu family (Bambu Studio, OrcaSlicer, Creality Print, Anycubic
Slicer Next) - verified against Studio-saved and Creality-saved
specimens, 2026-09-08:

- 3D/3dmodel.model holds one <object id="B"> per build object whose
  <components> point at a mesh object in a sub-file
  (3D/Objects/object_<n>.model, mesh object id M), and one <item
  objectid="B" transform=...> per placed object. Ids are unique across
  the main file and every sub-file (Studio uses odd ids for meshes,
  even for build objects); the sub-file number n is independent.
- 3D/_rels/3dmodel.model.rels lists every sub-file (Id="rel-<n>").
- Metadata/model_settings.config: one <object id="B"> block (name,
  extruder, per-object overrides, one <part id="M"> per mesh), one
  <plate> block per plate holding <model_instance> membership records
  (object_id, instance_id, identify_id), and an optional <assemble>
  block with one line per object instance and per volume - a dangling
  assemble_item makes the engine refuse the file.

A copy is a full new object: its own sub-file (the mesh copied with
new ids and UUIDs), main-file object and item, model_settings block,
plate membership and assemble lines. Sharing one mesh file between
two objects would be smaller, but only a full object is known to
survive every slicer in the family.

PrusaSlicer: meshes sit inline in 3D/3dmodel.model (<object id="N">
with <mesh>), one <item objectid="N"> each, and
Metadata/Slic3r_PE_model.config holds one <object id="N"> block with
its <volume> ranges. There is one bed, so no plates.
"""

import re
import uuid

import edit_bambu_3mf as engine  # noqa: E402 (slicer_project adds tools/)
from errors import ProjectError

MODEL = engine.MODEL_ENTRY
MS = engine.MODEL_SETTINGS_ENTRY
RELS = "3D/_rels/3dmodel.model.rels"

_OBJ_RE = r'[ \t]*<object id="{oid}"[^>]*>.*?</object>\n?'
_ITEM_RE = r'[ \t]*<item objectid="{oid}"[^>]*/>\n?'


def _all_ids(text):
    return [int(v) for v in re.findall(r'<object id="(\d+)"', text)]


def max_object_id(entries, model_xml):
    """The highest object id used anywhere: main file and sub-files."""
    ids = _all_ids(model_xml)
    for name, raw in entries.items():
        if name.startswith("3D/Objects/") and name.endswith(".model"):
            try:
                ids.extend(_all_ids(raw.decode("utf-8")))
            except UnicodeDecodeError:
                continue
    return max(ids) if ids else 0


def _next_subfile_number(entries):
    used = [int(m.group(1)) for name in entries
            for m in [re.match(r"3D/Objects/object_(\d+)\.model$", name)]
            if m]
    return (max(used) if used else 0) + 1


def _uuid_like(pattern_uuid, index):
    """A fresh UUID in the shape Studio uses (an index in the first
    block, the rest per kind), or a random one when the sample has
    no such shape. Any valid UUID is accepted on load; the shape is
    kept for the sake of a file that looks Studio-made."""
    m = re.match(r"^[0-9a-fA-F]{8}-(.*)$", pattern_uuid or "")
    if m:
        return f"{index:08x}-{m.group(1)}"
    return str(uuid.uuid4())


def object_block(model_xml, objectid):
    m = re.search(_OBJ_RE.format(oid=re.escape(objectid)), model_xml, re.S)
    return m


def item_match(model_xml, objectid):
    return re.search(_ITEM_RE.format(oid=re.escape(objectid)), model_xml)


def ms_object_block(ms_xml, objectid):
    return re.search(r'[ \t]*<object id="' + re.escape(objectid)
                     + r'">.*?</object>\n?', ms_xml, re.S)


def ms_instance_block(ms_xml, objectid):
    return re.search(r'[ \t]*<model_instance>\s*<metadata key="object_id" '
                     r'value="' + re.escape(objectid)
                     + r'"/>.*?</model_instance>\n?', ms_xml, re.S)


def max_identify_id(ms_xml):
    ids = [int(v) for v in re.findall(
        r'key="identify_id" value="(\d+)"', ms_xml)]
    return max(ids) if ids else 0


def plate_block_match(ms_xml, plate_id):
    for m in re.finditer(r'<plate>(.*?)</plate>', ms_xml, re.S):
        if re.search(r'key="plater_id" value="' + re.escape(str(plate_id))
                     + '"', m.group(1)):
            return m
    return None


def plate_ids(ms_xml):
    return re.findall(r'key="plater_id" value="([^"]*)"', ms_xml)


# ----------------------------------------------------------- Bambu copy

def copy_bambu(dst, src, objectid, count, plate_id, name_fn):
    """Copy one build object `count` times from src into dst.

    dst and src are dicts {"entries", "model", "ms"}; src may be dst
    itself (duplicating) or another project's triple (importing).
    The copies are placed on plate `plate_id` of dst with the source
    item's transform; the caller positions them afterwards. name_fn(k)
    names copy k (1-based). Returns (dst, [new build object ids]).
    Everything is done on copies of the strings; dst is returned new.
    """
    s_entries, s_model, s_ms = src["entries"], src["model"], src["ms"]
    entries = dict(dst["entries"])
    model, ms = dst["model"], dst["ms"]

    src_obj = object_block(s_model, objectid)
    src_item = item_match(s_model, objectid)
    src_ms_obj = ms_object_block(s_ms, objectid)
    if not (src_obj and src_item):
        raise ProjectError(f"Object {objectid} was not found in the "
                           "model to copy from.")
    src_inst = ms_instance_block(s_ms, objectid)
    src_assemble = re.findall(
        r'[ \t]*<assemble_item object_id="' + re.escape(objectid)
        + r'"[^>]*/>\n?', s_ms)

    components = re.findall(r"<component\b[^>]*/>", src_obj.group(0))
    inline_mesh = "<mesh" in src_obj.group(0)
    new_ids = []
    for k in range(1, count + 1):
        next_id = max_object_id(entries, model) + 1
        new_build_id = str(next_id + len(components) + (1 if inline_mesh else 0))
        # --- the mesh(es): one new sub-file per component
        new_components = []
        mesh_ids = []
        for comp in components:
            path = re.search(r'p:path="([^"]*)"', comp)
            mid = re.search(r'\bobjectid="(\d+)"', comp)
            if not mid:
                continue
            new_mid = str(next_id)
            next_id += 1
            mesh_ids.append(new_mid)
            if path:
                raw = s_entries.get(path.group(1).lstrip("/"))
                if raw is None:
                    raise ProjectError(
                        f"The mesh file {path.group(1)} of object "
                        f"{objectid} is missing from the project.")
                text = raw.decode("utf-8")
                n = _next_subfile_number(entries)
                new_name = f"3D/Objects/object_{n}.model"
                text = re.sub(r'<object id="' + re.escape(mid.group(1))
                              + r'"', f'<object id="{new_mid}"', text, count=1)
                text = re.sub(
                    r'(<object id="' + new_mid + r'"[^>]*p:UUID=")[^"]*(")',
                    lambda m: m.group(1) + _uuid_like(
                        m.group(0)[len(m.group(1)):-1], n) + m.group(2),
                    text, count=1)
                entries[new_name] = text.encode("utf-8")
                if RELS in entries:
                    rels = entries[RELS].decode("utf-8")
                    rels = rels.replace(
                        "</Relationships>",
                        f' <Relationship Target="/{new_name}" Id="rel-{n}" '
                        'Type="http://schemas.microsoft.com/3dmanufacturing/'
                        '2013/01/3dmodel"/>\n</Relationships>')
                    entries[RELS] = rels.encode("utf-8")
                new_comp = comp.replace(path.group(0),
                                        f'p:path="/{new_name}"')
            else:
                # Inline mesh referenced by a component in the main
                # file: copy that mesh object into the main file.
                mesh_block = object_block(s_model, mid.group(1))
                if mesh_block is None:
                    raise ProjectError(
                        f"The mesh of object {objectid} was not found.")
                copy = re.sub(r'<object id="' + re.escape(mid.group(1))
                              + '"', f'<object id="{new_mid}"',
                              mesh_block.group(0), count=1)
                model = model.replace("</resources>",
                                      copy.rstrip("\n") + "\n </resources>",
                                      1)
                new_comp = comp
            new_comp = re.sub(r'\bobjectid="\d+"',
                              f'objectid="{new_mid}"', new_comp, count=1)
            new_comp = re.sub(r'p:UUID="[^"]*"',
                              lambda m: 'p:UUID="' + _uuid_like(
                                  m.group(0)[8:-1], int(new_mid)) + '"',
                              new_comp, count=1)
            new_components.append((comp, new_comp))
        if inline_mesh:
            # The build object IS the mesh (no components): copy the
            # whole block under the new id.
            mesh_ids.append(new_build_id)
        # --- the build object in the main file
        obj_text = src_obj.group(0)
        for old, new in new_components:
            obj_text = obj_text.replace(old, new, 1)
        obj_text = re.sub(r'<object id="' + re.escape(objectid) + '"',
                          f'<object id="{new_build_id}"', obj_text, count=1)
        obj_text = re.sub(
            r'(<object id="' + new_build_id + r'"[^>]*p:UUID=")[^"]*(")',
            lambda m: m.group(1) + _uuid_like(
                m.group(0)[len(m.group(1)):-1], int(new_build_id))
            + m.group(2), obj_text, count=1)
        model = model.replace("</resources>",
                              obj_text.rstrip("\n") + "\n </resources>", 1)
        # --- the placed item
        item_text = src_item.group(0)
        item_text = re.sub(r'objectid="\d+"', f'objectid="{new_build_id}"',
                           item_text, count=1)
        item_text = re.sub(r'p:UUID="[^"]*"',
                           lambda m: 'p:UUID="' + _uuid_like(
                               m.group(0)[8:-1], int(new_build_id)) + '"',
                           item_text, count=1)
        model = model.replace("</build>",
                              item_text.rstrip("\n") + "\n </build>", 1)
        # --- model_settings: the object block
        if src_ms_obj:
            block = src_ms_obj.group(0)
            block = block.replace(f'<object id="{objectid}">',
                                  f'<object id="{new_build_id}">', 1)
            src_part_ids = re.findall(r'<part id="(\d+)"', block)
            for old_pid, new_mid in zip(src_part_ids, mesh_ids):
                block = block.replace(f'<part id="{old_pid}"',
                                      f'<part id="{new_mid}"', 1)
            block = re.sub(r'(<metadata key="name" value=")([^"]*)(")',
                           lambda m: m.group(1) + _xml_attr(name_fn(k))
                           + m.group(3), block, count=1)
            block = re.sub(r'uuid="[^"]*"',
                           lambda m: f'uuid="{uuid.uuid4()}"', block)
            first_plate = ms.find("  <plate>")
            if first_plate < 0:
                first_plate = ms.find("<plate>")
            if first_plate < 0:
                ms = ms.replace("</config>", block + "</config>", 1)
            else:
                ms = ms[:first_plate] + block + ms[first_plate:]
        # --- plate membership
        pm = plate_block_match(ms, plate_id)
        if pm is None:
            raise ProjectError(f"No plate {plate_id} in this project.")
        ident = max_identify_id(ms) + 1
        if src_inst:
            inst = src_inst.group(0)
            inst = inst.replace(f'key="object_id" value="{objectid}"',
                                f'key="object_id" value="{new_build_id}"', 1)
            inst = re.sub(r'(key="identify_id" value=")\d+(")',
                          lambda m: f"{m.group(1)}{ident}{m.group(2)}", inst)
        else:
            inst = ("    <model_instance>\n"
                    f'      <metadata key="object_id" value="{new_build_id}"/>\n'
                    '      <metadata key="instance_id" value="0"/>\n'
                    f'      <metadata key="identify_id" value="{ident}"/>\n'
                    "    </model_instance>\n")
        ms = (ms[:pm.end(1)].rstrip(" ") + "  " + inst.strip("\n")
              + "\n  " + ms[pm.end(1):])
        # --- assemble lines
        if src_assemble and "</assemble>" in ms:
            lines = "".join(
                a.replace(f'object_id="{objectid}"',
                          f'object_id="{new_build_id}"', 1)
                for a in src_assemble)
            ms = ms.replace("</assemble>", lines.lstrip("\n").rstrip("\n")
                            + "\n  </assemble>", 1)
        new_ids.append(new_build_id)
    return {"entries": entries, "model": model, "ms": ms}, new_ids


def _xml_attr(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace('"', "&quot;"))


# --------------------------------------------------------- Bambu remove

def remove_bambu(dst, objectid):
    """Remove one build object: its main-file object and item, its
    model_settings block, plate membership and assemble lines, and any
    mesh sub-file nothing else references (plus its rels entry)."""
    entries = dict(dst["entries"])
    model, ms = dst["model"], dst["ms"]
    removed_paths = set()
    block = object_block(model, objectid)
    if block:
        removed_paths.update(re.findall(r'p:path="([^"]+)"',
                                        block.group(0)))
        model = model[:block.start()] + model[block.end():]
    model = re.sub(_ITEM_RE.format(oid=re.escape(objectid)), "", model)
    still_used = set(re.findall(r'p:path="([^"]+)"', model))
    ms = re.sub(r'[ \t]*<object id="' + re.escape(objectid)
                + r'">.*?</object>\n?', "", ms, flags=re.S)
    ms = re.sub(r'[ \t]*<model_instance>\s*<metadata key="object_id" '
                r'value="' + re.escape(objectid)
                + r'"/>.*?</model_instance>\n?', "", ms, flags=re.S)
    ms = re.sub(r'[ \t]*<assemble_item object_id="' + re.escape(objectid)
                + r'"[^>]*/>\n?', "", ms)
    for path in removed_paths - still_used:
        entry = path.lstrip("/")
        entries.pop(entry, None)
        if RELS in entries:
            r = entries[RELS].decode("utf-8")
            r = re.sub(r'[ \t]*<Relationship [^>]*Target="'
                       + re.escape(path) + r'"[^>]*/>\n?', "", r)
            entries[RELS] = r.encode("utf-8")
    return {"entries": entries, "model": model, "ms": ms}


# ---------------------------------------------------------- Bambu plates

_PLATE_COPIED_KEYS = ("filament_map_mode", "filament_maps",
                      "filament_volume_maps")


def add_plate_bambu(ms_xml):
    """Append an empty plate; returns (ms_xml, new plate id). The
    filament-map keys of the first plate are copied so a dual-nozzle
    project's new plate carries a map like its siblings; thumbnails
    are left to the slicer."""
    ids = [int(p) for p in plate_ids(ms_xml) if p.isdigit()]
    new_id = (max(ids) if ids else 0) + 1
    first = re.search(r'<plate>(.*?)</plate>', ms_xml, re.S)
    extra = ""
    if first:
        for key in _PLATE_COPIED_KEYS:
            m = re.search(r'<metadata key="' + key + r'" value="[^"]*"/>',
                          first.group(1))
            if m:
                extra += "    " + m.group(0) + "\n"
    block = ("  <plate>\n"
             f'    <metadata key="plater_id" value="{new_id}"/>\n'
             '    <metadata key="plater_name" value=""/>\n'
             '    <metadata key="locked" value="false"/>\n'
             + extra + "  </plate>\n")
    last = ms_xml.rfind("</plate>")
    if last < 0:
        anchor = ms_xml.find("<assemble>")
        if anchor < 0:
            anchor = ms_xml.rfind("</config>")
        ms_xml = ms_xml[:anchor].rstrip(" ") + block + "  " + ms_xml[anchor:]
    else:
        end = last + len("</plate>")
        ms_xml = ms_xml[:end] + "\n" + block.rstrip("\n") + ms_xml[end:]
    return ms_xml, str(new_id)


def move_instance_bambu(ms_xml, objectid, plate_id):
    """Relocate an object's membership record to another plate."""
    inst = ms_instance_block(ms_xml, objectid)
    if inst is None:
        raise ProjectError(
            f"Object {objectid} has no plate membership entry - save "
            "the project from the slicer once and try again.")
    text = inst.group(0).strip()
    ms_xml = ms_xml[:inst.start()] + ms_xml[inst.end():]
    tm = plate_block_match(ms_xml, plate_id)
    if tm is None:
        raise ProjectError(f"No plate {plate_id} in this project.")
    return (ms_xml[:tm.end(1)].rstrip(" ") + "  " + text + "\n  "
            + ms_xml[tm.end(1):])


# --------------------------------------------------------------- Prusa

def _prusa_ms_block(ms_xml, objectid):
    return re.search(r'[ \t]*<object id="' + re.escape(objectid)
                     + r'"[^>]*>.*?</object>\n?', ms_xml, re.S)


def copy_prusa(dst, src, objectid, count, name_fn):
    """Copy one inline-mesh object `count` times (src may be dst)."""
    s_model, s_ms = src["model"], src["ms"]
    model, ms = dst["model"], dst["ms"]
    src_obj = object_block(s_model, objectid)
    src_item = item_match(s_model, objectid)
    src_ms = _prusa_ms_block(s_ms, objectid)
    if not (src_obj and src_item):
        raise ProjectError(f"Object {objectid} was not found in the "
                           "model to copy from.")
    new_ids = []
    for k in range(1, count + 1):
        new_id = str(max(_all_ids(model) + [0]) + 1)
        obj = re.sub(r'<object id="' + re.escape(objectid) + '"',
                     f'<object id="{new_id}"', src_obj.group(0), count=1)
        model = model.replace("</resources>",
                              obj.rstrip("\n") + "\n </resources>", 1)
        item = re.sub(r'objectid="\d+"', f'objectid="{new_id}"',
                      src_item.group(0), count=1)
        model = model.replace("</build>",
                              item.rstrip("\n") + "\n </build>", 1)
        if src_ms:
            block = re.sub(r'<object id="' + re.escape(objectid) + '"',
                           f'<object id="{new_id}"', src_ms.group(0), count=1)
            block = re.sub(r'instances_count="\d+"', 'instances_count="1"',
                           block)
            block = re.sub(
                r'(<metadata type="object" key="name" value=")([^"]*)(")',
                lambda m: m.group(1) + _xml_attr(name_fn(k)) + m.group(3),
                block, count=1)
            ms = ms.replace("</config>", block + "</config>", 1)
        new_ids.append(new_id)
    return {"entries": dict(dst["entries"]), "model": model, "ms": ms}, new_ids


def remove_prusa(dst, objectid):
    model, ms = dst["model"], dst["ms"]
    block = object_block(model, objectid)
    if block:
        model = model[:block.start()] + model[block.end():]
    model = re.sub(_ITEM_RE.format(oid=re.escape(objectid)), "", model)
    ms = re.sub(r'[ \t]*<object id="' + re.escape(objectid)
                + r'"[^>]*>.*?</object>\n?', "", ms, flags=re.S)
    return {"entries": dict(dst["entries"]), "model": model, "ms": ms}


# ------------------------------------------------------ shared helpers

def set_item_translation(model_xml, objectid, x, y, z=None):
    """Write an item's translation (world mm); z untouched when None."""
    hit = []

    def repl(m):
        if m.group("objectid") != objectid:
            return m.group(0)
        nums = [float(v) for v in m.group("transform").split()]
        if len(nums) < 12:
            return m.group(0)
        nums[9], nums[10] = x, y
        if z is not None:
            nums[11] = z
        hit.append(True)
        return m.group(0).replace(
            m.group("transform"), " ".join(engine.fmt_num(v) for v in nums))

    out = engine.ITEM_RE.sub(repl, model_xml)
    if not hit:
        raise ProjectError(f"Object {objectid} has no placement entry.")
    return out


def shift_item(model_xml, objectid, dx, dy):
    """Move an item by (dx, dy) in world mm."""
    hit = []

    def repl(m):
        if m.group("objectid") != objectid:
            return m.group(0)
        nums = [float(v) for v in m.group("transform").split()]
        if len(nums) < 12:
            return m.group(0)
        nums[9] += dx
        nums[10] += dy
        hit.append(True)
        return m.group(0).replace(
            m.group("transform"), " ".join(engine.fmt_num(v) for v in nums))

    return engine.ITEM_RE.sub(repl, model_xml)


def spill_arrange(sizes, plate_w, plate_d, keepout, arrange_fn):
    """Lay footprints out over as many plates as needed.

    sizes: [(w, d)] in the caller's order. Returns [(plate_index,
    (cx, cy))] per footprint, plate 0 first; raises ProjectError when
    one footprint alone does not fit a plate. arrange_fn is
    geometry.arrange_rects (injected so tests can watch it)."""
    remaining = list(range(len(sizes)))
    remaining.sort(key=lambda i: sizes[i][0] * sizes[i][1], reverse=True)
    result = [None] * len(sizes)
    plate_index = 0
    while remaining:
        # The largest prefix (biggest first) that fits one plate.
        fit_n = 0
        spots = None
        for n in range(len(remaining), 0, -1):
            group = remaining[:n]
            trial = arrange_fn([sizes[i] for i in group], plate_w, plate_d,
                               keepout=keepout)
            if trial is not None:
                fit_n, spots = n, trial
                break
        if fit_n == 0:
            i = remaining[0]
            raise ProjectError(
                f"A footprint of {sizes[i][0]:g} by {sizes[i][1]:g} mm does "
                f"not fit a {plate_w:g} by {plate_d:g} mm plate at all.")
        for i, spot in zip(remaining[:fit_n], spots):
            result[i] = (plate_index, spot)
        remaining = remaining[fit_n:]
        plate_index += 1
    return result
