"""prusa_catalog.py - plain-language names for PrusaSlicer's settings.

The same shape as settings_catalog.py (the Bambu catalog), so the
editor pages render either through session.catalog: curated tabs of
(key, label, help, unit, control, options) entries, FILAMENTS of
(key, label, help, unit), the helpers curated_entry / control_for /
label_for / tab_for, and the key sets the pages consult. Keys are
PrusaSlicer's own (its --help-fff names); every curated key is
checked against a real Core One project by the test suite, so a typo
can never hide a setting.

Placements follow PrusaSlicer's own tabs (Print Settings: Layers and
perimeters, Infill, Skirt and brim, Support material, Speed, ...;
Filament Settings; Printer Settings) as far as this app's six topic
tabs allow. Unverified against the GUI's exact page layout - the FULL
TAB AUDIT todo item owns that for every slicer.
"""

QUALITY = [
    ("layer_height", "Layer height",
     "How thick each printed layer is. Smaller is finer and slower.",
     "mm", "number", None),
    ("first_layer_height", "First layer height",
     "Thickness of the very first layer. A little thicker grips the "
     "plate better.", "mm", "number", None),
    ("perimeters", "Perimeters (walls)",
     "How many outline lines each layer gets. More walls, stronger and "
     "heavier.", "walls", "number", None),
    ("top_solid_layers", "Top solid layers",
     "Solid layers closing the top. More layers hide the infill better.",
     "layers", "number", None),
    ("bottom_solid_layers", "Bottom solid layers",
     "Solid layers at the bottom.", "layers", "number", None),
    ("seam_position", "Seam position",
     "Where each layer's start point (the visible seam) is placed.",
     None, "select",
     [("nearest", "Nearest - fastest"),
      ("aligned", "Aligned - one straight seam line"),
      ("rear", "Rear - at the back of the object"),
      ("random", "Random - scattered, no visible line")]),
    ("external_perimeters_first", "Outer wall first",
     "Print the outside wall before the inner ones. Better overhangs, "
     "slightly weaker walls.", None, "checkbox", None),
    ("extra_perimeters_on_overhangs", "Extra perimeters on overhangs",
     "Add walls where the model overhangs so nothing prints in the "
     "air.", None, "checkbox", None),
    ("thin_walls", "Detect thin walls",
     "Print very thin features as a single line instead of skipping "
     "them.", None, "checkbox", None),
    ("gap_fill_enabled", "Fill small gaps",
     "Fill the narrow gaps between walls with short extrusions.",
     None, "checkbox", None),
    ("ironing", "Ironing",
     "Smooth the top surface with a hot, nearly empty pass.",
     None, "checkbox", None),
    ("ironing_type", "Ironing applies to",
     "Which surfaces get ironed.", None, "select",
     [("top", "All top surfaces"),
      ("topmost", "Topmost surface only"),
      ("solid", "All solid surfaces")]),
    ("avoid_crossing_perimeters", "Avoid crossing walls",
     "Route travel moves around the outside of walls to reduce "
     "stringing.", None, "checkbox", None),
    ("avoid_crossing_perimeters_max_detour", "Avoid crossing walls - max detour",
     "How far the head may go around instead of across, in mm or as a "
     "percent of the direct move.", "mm or %", "text", None),
    ("slice_closing_radius", "Slice gap closing radius",
     "Cracks in the model narrower than this are closed when slicing.",
     "mm", "number", None),
    ("resolution", "Slicing resolution",
     "Detail below this size is simplified away. 0 keeps everything.",
     "mm", "number", None),
    ("xy_size_compensation", "Horizontal size compensation",
     "Grow (positive) or shrink (negative) every layer's outline.",
     "mm", "number", None),
    ("arc_fitting", "Arc fitting",
     "Turn short straight segments into arcs for smoother curves.",
     None, "select",
     [("disabled", "Off"),
      ("emit_center", "On - G2/G3 arcs")]),
]

STRENGTH = [
    ("fill_density", "Infill density",
     "How much of the inside is filled. 15% is typical; 100% is solid.",
     "%", "text", None),
    ("fill_pattern", "Infill pattern",
     "The shape of the inside structure.", None, "select",
     [("rectilinear", "Rectilinear - fast"),
      ("alignedrectilinear", "Aligned rectilinear"),
      ("grid", "Grid - strong, fast"),
      ("triangles", "Triangles"),
      ("stars", "Stars"),
      ("cubic", "Cubic - strong in all directions"),
      ("line", "Line"),
      ("concentric", "Concentric"),
      ("honeycomb", "Honeycomb"),
      ("3dhoneycomb", "3D honeycomb"),
      ("gyroid", "Gyroid - strong, good for flexible parts"),
      ("hilbertcurve", "Hilbert curve"),
      ("archimedeanchords", "Archimedean chords"),
      ("octagramspiral", "Octagram spiral"),
      ("adaptivecubic", "Adaptive cubic - dense near the surface"),
      ("supportcubic", "Support cubic - light"),
      ("lightning", "Lightning - fastest, only supports the top")]),
    ("top_fill_pattern", "Top surface pattern",
     "The line pattern on the very top.", None, "select",
     [("monotonic", "Monotonic - smoothest"),
      ("monotoniclines", "Monotonic lines"),
      ("rectilinear", "Rectilinear"),
      ("alignedrectilinear", "Aligned rectilinear"),
      ("concentric", "Concentric"),
      ("hilbertcurve", "Hilbert curve"),
      ("archimedeanchords", "Archimedean chords"),
      ("octagramspiral", "Octagram spiral")]),
    ("bottom_fill_pattern", "Bottom surface pattern",
     "The line pattern on the very bottom.", None, "select",
     [("monotonic", "Monotonic - smoothest"),
      ("monotoniclines", "Monotonic lines"),
      ("rectilinear", "Rectilinear"),
      ("alignedrectilinear", "Aligned rectilinear"),
      ("concentric", "Concentric"),
      ("hilbertcurve", "Hilbert curve"),
      ("archimedeanchords", "Archimedean chords"),
      ("octagramspiral", "Octagram spiral")]),
    ("fill_angle", "Infill angle",
     "Direction of the infill lines.", "degrees", "number", None),
    ("infill_every_layers", "Combine infill every",
     "Print infill only every N layers, thicker. 1 means every layer.",
     "layers", "number", None),
    ("infill_overlap", "Infill and wall overlap",
     "How far infill lines reach into the walls, in mm or percent.",
     "mm or %", "text", None),
    ("solid_infill_below_area", "Solid infill below area",
     "Small areas below this size are filled solid.",
     "square mm", "number", None),
    ("bridge_flow_ratio", "Bridge flow ratio",
     "Extrusion multiplier for bridges. Lower for tighter bridges.",
     None, "number", None),
    ("thick_bridges", "Thick bridges",
     "Print bridges with round, thick extrusions (the classic way).",
     None, "checkbox", None),
]

SUPPORT = [
    ("support_material", "Enable supports",
     "Print support structures under overhangs.", None, "checkbox", None),
    ("support_material_auto", "Automatic supports",
     "Generate supports everywhere they are needed, not only where "
     "painted.", None, "checkbox", None),
    ("support_material_threshold", "Overhang angle threshold",
     "Overhangs flatter than this angle from horizontal get supported. "
     "0 means automatic.", "degrees", "number", None),
    ("support_material_style", "Support style",
     "Organic (tree-like) supports touch less of the model and remove "
     "easier.", None, "select",
     [("grid", "Grid - classic"),
      ("snug", "Snug - hugs the model"),
      ("organic", "Organic - tree-like")]),
    ("support_material_buildplate_only", "Supports from build plate only",
     "Never grow supports on top of the model itself.",
     None, "checkbox", None),
    ("support_material_contact_distance", "Support top gap",
     "Gap between supports and the model above them. Bigger removes "
     "easier, smaller supports better.", "mm", "number", None),
    ("support_material_bottom_contact_distance", "Support bottom gap",
     "Gap between the model and supports standing on it. 0 follows the "
     "top gap.", "mm", "number", None),
    ("support_material_pattern", "Support pattern",
     "The shape of the support columns.", None, "select",
     [("rectilinear", "Rectilinear"),
      ("rectilinear-grid", "Rectilinear grid"),
      ("honeycomb", "Honeycomb")]),
    ("support_material_spacing", "Support line spacing",
     "Distance between support lines. Wider is sparser and easier to "
     "remove.", "mm", "number", None),
    ("support_material_interface_layers", "Support interface layers",
     "Dense layers between supports and the model. More gives a "
     "smoother underside.", "layers", "number", None),
    ("support_material_xy_spacing", "Support to model side gap",
     "Sideways distance between supports and the model, in mm or "
     "percent of the extrusion width.", "mm or %", "text", None),
    ("support_material_angle", "Support pattern angle",
     "Direction of the support lines.", "degrees", "number", None),
    ("dont_support_bridges", "Do not support bridges",
     "Let bridges span on their own instead of supporting them.",
     None, "checkbox", None),
    ("support_tree_angle", "Organic branch angle",
     "How far organic support branches may lean while growing.",
     "degrees", "number", None),
    ("support_tree_branch_diameter", "Organic branch thickness",
     "Diameter of organic support branches. Thicker is sturdier.",
     "mm", "number", None),
    ("support_tree_top_rate", "Organic branch density",
     "How densely organic branches cover the supported area.",
     "%", "text", None),
]

SPEED = [
    ("perimeter_speed", "Wall speed",
     "Speed for walls.", "mm/s", "number", None),
    ("external_perimeter_speed", "Outer wall speed",
     "Speed for the outside wall, in mm/s or percent of the wall "
     "speed. Slower is smoother.", "mm/s or %", "text", None),
    ("small_perimeter_speed", "Small feature speed",
     "Speed for tiny outlines, in mm/s or percent of the wall speed.",
     "mm/s or %", "text", None),
    ("infill_speed", "Infill speed",
     "Speed for sparse infill.", "mm/s", "number", None),
    ("solid_infill_speed", "Solid infill speed",
     "Speed for solid layers, in mm/s or percent of the infill speed.",
     "mm/s or %", "text", None),
    ("top_solid_infill_speed", "Top surface speed",
     "Speed for the very top layer, in mm/s or percent of the solid "
     "infill speed. Slower looks better.", "mm/s or %", "text", None),
    ("support_material_speed", "Support speed",
     "Speed for support structures.", "mm/s", "number", None),
    ("bridge_speed", "Bridge speed",
     "Speed for bridging in the air. Slower bridges sag less.",
     "mm/s", "number", None),
    ("gap_fill_speed", "Gap fill speed",
     "Speed for filling small gaps.", "mm/s", "number", None),
    ("travel_speed", "Travel speed",
     "Speed for moves without extruding.", "mm/s", "number", None),
    ("first_layer_speed", "First layer speed",
     "Speed for the whole first layer, in mm/s or percent. Slower "
     "sticks better.", "mm/s or %", "text", None),
    ("first_layer_infill_speed", "First layer infill speed",
     "Speed for the first layer's infill.", "mm/s or %", "text", None),
    ("max_print_speed", "Maximum print speed",
     "The auto-speed ceiling when speeds are set to 0.",
     "mm/s", "number", None),
    ("max_volumetric_speed", "Maximum volumetric speed",
     "Caps how much plastic per second may flow; 0 means no cap.",
     "cubic mm/s", "number", None),
    ("default_acceleration", "Default acceleration",
     "How quickly the head speeds up and slows down for everything "
     "not set separately. 0 uses the printer's own.",
     "mm/s squared", "number", None),
    ("first_layer_acceleration", "First layer acceleration",
     "Acceleration for the first layer. 0 uses the default.",
     "mm/s squared", "number", None),
    ("perimeter_acceleration", "Wall acceleration",
     "Acceleration for walls. 0 uses the default.",
     "mm/s squared", "number", None),
    ("infill_acceleration", "Infill acceleration",
     "Acceleration for infill. 0 uses the default.",
     "mm/s squared", "number", None),
    ("travel_acceleration", "Travel acceleration",
     "Acceleration for moves without extruding. 0 uses the default.",
     "mm/s squared", "number", None),
]

ADHESION = [
    ("brim_type", "Brim",
     "A flat ring around the first layer that keeps corners down.",
     None, "select",
     [("no_brim", "No brim"),
      ("outer_only", "Outer brim only"),
      ("inner_only", "Inner brim only (inside holes)"),
      ("outer_and_inner", "Outer and inner brim")]),
    ("brim_width", "Brim width",
     "How wide the brim ring is.", "mm", "number", None),
    ("brim_separation", "Brim gap",
     "A small gap between brim and object makes the brim easier to "
     "peel off.", "mm", "number", None),
    ("skirts", "Skirt loops",
     "Loops printed around the job before it starts, to prime the "
     "nozzle. 0 for none.", "loops", "number", None),
    ("skirt_distance", "Skirt distance",
     "Gap between the skirt and the objects.", "mm", "number", None),
    ("skirt_height", "Skirt height",
     "How many layers tall the skirt is.", "layers", "number", None),
    ("draft_shield", "Draft shield",
     "Raise the skirt into a wall that shields the print from "
     "draughts.", None, "select",
     [("disabled", "Off"),
      ("limited", "Up to the skirt height"),
      ("enabled", "Full height")]),
    ("raft_layers", "Raft layers",
     "Layers of raft under the object. 0 prints straight on the "
     "plate.", "layers", "number", None),
    ("raft_contact_distance", "Raft gap",
     "Gap between the raft and the object.", "mm", "number", None),
    ("elefant_foot_compensation", "Elephant foot compensation",
     "Shrink the first layer's outline to counter the squashed first "
     "layer.", "mm", "number", None),
]

OTHERS = [
    ("complete_objects", "Print one object at a time",
     "Finish each object completely before starting the next, instead "
     "of layer by layer across all objects. Mind the printhead's "
     "clearances.", None, "checkbox", None),
    ("extruder_clearance_height", "Printhead clearance height",
     "How tall a finished object may be before the printhead's gantry "
     "would hit it when printing one object at a time.",
     "mm", "number", None),
    ("extruder_clearance_radius", "Printhead clearance radius",
     "How much room around the nozzle the printhead needs when passing "
     "a finished object.", "mm", "number", None),
    ("spiral_vase", "Spiral vase mode",
     "Print a single continuous spiral outer wall - one object, no "
     "infill, no top.", None, "checkbox", None),
    ("wipe_tower", "Wipe tower",
     "A sacrificial tower for cleaning the nozzle at filament changes.",
     None, "checkbox", None),
    ("wipe_tower_width", "Wipe tower width",
     "How wide the wipe tower is.", "mm", "number", None),
    ("fuzzy_skin", "Fuzzy skin",
     "Jitter the outer wall for a rough, textured surface.",
     None, "select",
     [("none", "Off"),
      ("external", "Outside walls only"),
      ("all", "All walls")]),
    ("fuzzy_skin_thickness", "Fuzzy skin thickness",
     "How far the jitter reaches.", "mm", "number", None),
    ("fuzzy_skin_point_dist", "Fuzzy skin point distance",
     "Distance between jitter points. Smaller is rougher.",
     "mm", "number", None),
    ("gcode_label_objects", "Label objects in G-code",
     "Mark each object in the G-code so the printer can skip or cancel "
     "one.", None, "select",
     [("disabled", "Off"),
      ("octoprint", "OctoPrint comments"),
      ("firmware", "Firmware-specific - what the Core One uses")]),
    ("output_filename_format", "Output file name pattern",
     "How the exported G-code file is named; uses placeholders in "
     "braces.", None, "text", None),
]

# The flushing page is Bambu-specific (per-nozzle purge matrix with
# Studio's own layout); PrusaSlicer's wipe-tower purge volumes stay
# reachable through All settings. These names exist so the pages
# render, and never match a PrusaSlicer key.
FLUSH_MATRIX_KEY = "__accessslice_no_flush_matrix__"
FLUSH_MULTIPLIER_KEY = "__accessslice_no_flush_multiplier__"
FLUSH_TOGGLES = []
PRIME_TOWER = []
FLUSHING_PAGE_KEYS = set()

# Per-extruder settings shown on the Filaments tab, one field per slot:
# (key, label, help, unit). Only keys the project stores as lists.
FILAMENTS = [
    ("temperature", "Nozzle temperature",
     "Printing temperature for this filament.", "degrees C"),
    ("first_layer_temperature", "First layer nozzle temperature",
     "Often a touch hotter for grip.", "degrees C"),
    ("bed_temperature", "Bed temperature",
     "Build plate temperature after the first layer.", "degrees C"),
    ("first_layer_bed_temperature", "First layer bed temperature",
     "Build plate temperature for the first layer.", "degrees C"),
    ("filament_type", "Filament type",
     "Material name (PLA, PETG, ASA...).", None),
    ("filament_colour", "Filament colour",
     "Colour as a hex code, for the preview only.", None),
    ("extrusion_multiplier", "Flow ratio",
     "Scales how much plastic is pushed. 1 is normal.", None),
    ("filament_diameter", "Filament diameter",
     "Nominal filament thickness, normally 1.75.", "mm"),
    ("filament_max_volumetric_speed", "Maximum volumetric speed",
     "The most plastic per second this filament can flow.",
     "cubic mm/s"),
    ("min_fan_speed", "Minimum fan speed",
     "Part cooling fan at the slowest.", "%"),
    ("max_fan_speed", "Maximum fan speed",
     "Part cooling fan at the fastest.", "%"),
    ("bridge_fan_speed", "Bridge fan speed",
     "Fan speed while bridging.", "%"),
    ("disable_fan_first_layers", "Fan off for the first layers",
     "How many first layers print with the fan off.", "layers"),
    ("slowdown_below_layer_time", "Slow down below layer time",
     "Layers faster than this are slowed for cooling.", "seconds"),
    ("min_print_speed", "Minimum print speed",
     "Never slow down below this for cooling.", "mm/s"),
    ("filament_cost", "Filament cost",
     "Price per kilogram, for the report.", "per kg"),
    ("filament_density", "Filament density",
     "Used to turn length into grams.", "g per cubic cm"),
    ("filament_spool_weight", "Spool weight",
     "Weight of the empty spool.", "g"),
    ("chamber_temperature", "Chamber temperature",
     "Target temperature for the enclosed chamber; 0 for none.",
     "degrees C"),
    ("idle_temperature", "Idle nozzle temperature",
     "Nozzle temperature while another extruder prints.", "degrees C"),
]

TABS = [
    ("quality", "Quality", QUALITY),
    ("strength", "Strength", STRENGTH),
    ("support", "Support", SUPPORT),
    ("speed", "Speed", SPEED),
    ("adhesion", "Adhesion", ADHESION),
    ("others", "Others", OTHERS),
]

_CURATED_BY_KEY = {
    e[0]: e for tab in (QUALITY, STRENGTH, SUPPORT, SPEED, ADHESION,
                        OTHERS, PRIME_TOWER)
    for e in tab
}

CURATED_KEYS = (set(_CURATED_BY_KEY) | {e[0] for e in FILAMENTS}
                | FLUSHING_PAGE_KEYS)

KNOWN_BOOL_KEYS = {
    key for key, *_rest in _CURATED_BY_KEY.values()
    if _CURATED_BY_KEY[key][4] == "checkbox"
} | {
    "gcode_comments", "use_relative_e_distances", "use_firmware_retraction",
    "use_volumetric_e", "variable_layer_height", "thin_walls", "overhangs",
    "infill_first", "only_retract_when_crossing_perimeters",
    "interface_shells", "ooze_prevention", "wipe_tower_no_sparse_layers",
    "staggered_inner_seams", "prefer_clockwise_movements",
    "remaining_times", "silent_mode", "autoemit_temperature_commands",
    "binary_gcode", "thick_bridges", "support_material_with_sheath",
    "support_material_synchronize_layers", "dont_support_bridges",
    "external_perimeters_first", "extra_perimeters",
    "extra_perimeters_on_overhangs", "avoid_crossing_curled_overhangs",
    "avoid_crossing_perimeters", "ironing", "gap_fill_enabled",
    "complete_objects", "spiral_vase", "wipe_tower",
    "single_extruder_multi_material", "single_extruder_multi_material_priming",
    "automatic_extrusion_widths", "automatic_infill_combination",
    "enable_dynamic_overhang_speeds", "interlocking_beam",
    "wipe_tower_extensions", "support_material", "support_material_auto",
    "support_material_buildplate_only", "support_material_interface_contact_loops",
    "high_current_on_filament_swap",
}


def curated_entry(key):
    """The curated (key,label,help,unit,control,options) or None."""
    return _CURATED_BY_KEY.get(key)


def control_for(key, value):
    """Best accessible control for an arbitrary key in All settings:
    "checkbox" only for known booleans, otherwise "text"."""
    entry = curated_entry(key)
    if entry:
        control, options = entry[4], entry[5]
        if control == "checkbox" and str(value) not in ("0", "1"):
            return "text", None
        return control, options
    if key in KNOWN_BOOL_KEYS and str(value) in ("0", "1"):
        return "checkbox", None
    return "text", None


def label_for(key):
    """Plain-language label if curated, else the raw key itself."""
    entry = curated_entry(key)
    return entry[1] if entry else key


# Topic classifier for the per-tab "all other settings" lists. Order
# matters: Others and Speed words are matched before the broad
# Strength and Quality needles claim them.
_TOPIC_RULES = [
    ("others", ("wipe_tower", "fuzzy", "complete_objects",
                "extruder_clearance", "spiral", "label_objects",
                "output_filename", "post_process", "notes",
                "interlocking", "mmu_segmented", "ooze_prevention",
                "standby_temperature", "colorprint", "gcode_substitutions",
                "thumbnails")),
    ("speed", ("_speed", "speed_", "acceleration", "max_volumetric",
               "_jerk", "max_print", "slowdown", "min_print")),
    ("support", ("support", "raft_", "dont_support")),
    ("adhesion", ("brim", "skirt", "draft_shield", "elefant")),
    ("strength", ("infill", "fill_", "solid_", "bridge", "perimeters",
                  "shell", "anchor", "interface")),
    ("quality", ("layer_height", "extrusion_width", "seam", "ironing",
                 "thin_walls", "gap_fill", "resolution", "xy_size",
                 "slice_", "perimeter", "overhang", "arc_fitting",
                 "closing", "wall", "precise", "avoid_crossing",
                 "only_retract", "min_bead", "top_", "bottom_",
                 "first_layer", "compensation", "smoothing",
                 "layer_", "z_")),
]

_TOPIC_OVERRIDES = {}

OBJECT_COMPANION_KEYS = {}

# The shortlist on the per-object settings page: what PrusaSlicer's
# own right-click "Add settings" menu surfaces most prominently.
OBJECT_CURATED_KEYS = (
    "layer_height", "perimeters", "top_solid_layers",
    "bottom_solid_layers", "fill_density", "fill_pattern",
    "support_material", "support_material_threshold",
    "support_material_style", "seam_position", "ironing", "brim_type",
    "extruder",
)

# mm-or-% fields: a large bare number typed here gets the "is it N%
# or N mm?" question (PrusaSlicer's own dialog exists for these; the
# exact threshold is not public, 100 is this app's rule).
MM_OR_PERCENT_KEYS = {"avoid_crossing_perimeters_max_detour",
                      "infill_overlap", "support_material_xy_spacing"}


def tab_for(key):
    """Which curated tab a process-scope key belongs on, or None."""
    if key in _TOPIC_OVERRIDES:
        return _TOPIC_OVERRIDES[key]
    for tab_id, needles in _TOPIC_RULES:
        for needle in needles:
            if needle in key:
                return tab_id
    return None
