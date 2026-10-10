"""settings_catalog.py - plain-language knowledge about slicer settings.

Two jobs:

1. The CURATED TABS: for the most-used settings, we know a friendly name,
   a one-line explanation, a unit, and the right control type (checkbox,
   number field, dropdown with its options). These fill the Quality,
   Strength, Support and Adhesion tabs.

2. TYPE INFERENCE for the "All settings" view: every key in the project
   must be editable, including the ~500 we have no curated entry for.
   Values in a Bambu project are all strings ("0"/"1" for booleans,
   "15%" for percentages) or lists of strings (one value per filament
   slot). We render a checkbox only for keys we positively know are
   booleans; everything else gets a text field showing the exact current
   value - safe for any format the slicer uses.

A curated entry is skipped at render time if its key does not exist in
the open project, so the catalog can never break on a different printer
profile or slicer version.
"""

# ------------------------------------------------------------ curated tabs

# Each entry: (key, label, help, unit, control, options)
#   control: "number" | "text" | "checkbox" | "select"
#   options: list of (value, shown_text) for "select", else None.

QUALITY = [
    ("layer_height", "Layer height",
     "How thick each printed layer is. Smaller is finer and slower.",
     "mm", "number", None),
    ("initial_layer_print_height", "First layer height",
     "Thickness of the very first layer. A little thicker grips the "
     "plate better.", "mm", "number", None),
    ("seam_position", "Seam position",
     "Where each layer's start point (the visible seam) is placed.",
     None, "select",
     [("nearest", "Nearest - fastest"),
      ("aligned", "Aligned - one straight seam line"),
      ("back", "Back of the model"),
      ("random", "Random - scattered, least visible line")]),
    ("ironing_type", "Ironing",
     "Smooths top surfaces by going over them again with the hot nozzle.",
     None, "select",
     [("no ironing", "Off"),
      ("top", "All top surfaces"),
      ("topmost", "Topmost surface only"),
      ("solid", "All solid surfaces")]),
    ("line_width", "Default line width",
     "Width of one printed line. Usually near the nozzle size.",
     "mm", "number", None),
    ("outer_wall_line_width", "Outer wall line width",
     "Line width for the visible outside walls.", "mm", "number", None),
    ("inner_wall_line_width", "Inner wall line width",
     "Line width for the hidden inner walls.", "mm", "number", None),
    ("top_surface_line_width", "Top surface line width",
     "Line width for the visible top layers.", "mm", "number", None),
    ("initial_layer_line_width", "First layer line width",
     "A wider first layer grips the plate better.", "mm", "number", None),
    ("elefant_foot_compensation", "Elephant foot compensation",
     "Shrinks the first layer inward to cancel the squashed-out bulge.",
     "mm", "number", None),
    ("precise_outer_wall", "Precise wall",
     "Prioritises exact outer wall dimensions over strength between "
     "walls.", None, "checkbox", None),
    ("wall_generator", "Wall generator",
     "Arachne varies line widths for thin features; Classic keeps them "
     "fixed.", None, "select",
     [("classic", "Classic - fixed line widths"),
      ("arachne", "Arachne - adaptive, better thin walls")]),
    ("only_one_wall_top", "Single wall on top surfaces",
     "Uses one wall on top layers so more area gets smooth infill.",
     None, "checkbox", None),
    ("detect_thin_wall", "Detect thin walls",
     "Prints walls thinner than a normal line where the model needs "
     "them.", None, "checkbox", None),
    # The next five sit together on Bambu Studio's own Quality tab, in
    # this order (screenshot-verified 2026-08-26) - keep them together.
    ("only_one_wall_first_layer", "Only one wall on first layer",
     "Uses only one wall on the first layer of the model.",
     None, "checkbox", None),
    ("smooth_speed_discontinuity_area", "Smooth speed discontinuity area",
     "Smooths out sudden speed changes between areas printed at "
     "different speeds.", None, "checkbox", None),
    ("smooth_coefficient", "Smooth coefficient",
     "How strongly speed discontinuities are smoothed. Bigger smooths "
     "more.", None, "number", None),
    ("reduce_crossing_wall", "Avoid crossing walls",
     "Routes travel moves around walls instead of across them, reducing "
     "surface scars.", None, "checkbox", None),
    ("max_travel_detour_distance", "Avoid crossing walls - max detour "
     "length",
     "The longest detour a travel move may take to avoid crossing a "
     "wall. 0 means no limit. A plain number is millimetres; add % for "
     "a percentage of the direct path.", "mm or %", "text", None),
    ("xy_hole_compensation", "Hole size compensation",
     "Grows (positive) or shrinks (negative) holes in the X-Y plane.",
     "mm", "number", None),
    ("xy_contour_compensation", "Contour size compensation",
     "Grows or shrinks the outside contour in the X-Y plane.",
     "mm", "number", None),
    ("resolution", "Model resolution",
     "Detail size below which the slicer simplifies the model.",
     "mm", "number", None),
]

STRENGTH = [
    ("wall_loops", "Wall count",
     "Number of outline walls around each layer. More walls, more "
     "strength.", "walls", "number", None),
    ("top_shell_layers", "Top shell layers",
     "Solid layers closing the top of the model.", "layers", "number", None),
    ("bottom_shell_layers", "Bottom shell layers",
     "Solid layers under the model.", "layers", "number", None),
    ("sparse_infill_density", "Infill density",
     "How much of the inside is filled. 15% suits most prints; more adds "
     "strength and weight.", "percent, e.g. 15%", "text", None),
    ("sparse_infill_pattern", "Infill pattern",
     "The shape the inside filling is printed in. For 90-100% density "
     "use Zig-zag or Concentric - other patterns over-extrude when "
     "solid.", None, "select",
     [("grid", "Grid - fast, strong, default"),
      ("gyroid", "Gyroid - strong in all directions"),
      ("cubic", "Cubic"),
      ("triangles", "Triangles"),
      ("tri-hexagon", "Tri-hexagon"),
      ("honeycomb", "Honeycomb - strong, slow"),
      ("adaptivecubic", "Adaptive cubic - saves material"),
      ("supportcubic", "Support cubic - fastest, weak"),
      ("crosshatch", "Crosshatch - quiet, good for LIDAR"),
      ("lightning", "Lightning - minimal, only supports top"),
      ("line", "Lines"),
      ("zig-zag", "Zig-zag - the right choice for 100% density"),
      ("alignedrectilinear", "Aligned rectilinear"),
      ("concentric", "Concentric - also fine for 100% density")]),
    ("infill_direction", "Infill direction",
     "Angle the infill lines are printed at.", "degrees", "number", None),
    ("infill_wall_overlap", "Infill-wall overlap",
     "How far infill overlaps the walls so they bond.",
     "percent, e.g. 15%", "text", None),
    ("top_surface_pattern", "Top surface pattern",
     "Line pattern of the visible top layers.", None, "select",
     [("monotonic", "Monotonic - smoothest, default"),
      ("monotonicline", "Monotonic lines"),
      ("rectilinear", "Rectilinear"),
      ("concentric", "Concentric")]),
    ("bottom_surface_pattern", "Bottom surface pattern",
     "Line pattern of the bottom layers.", None, "select",
     [("monotonic", "Monotonic - smoothest, default"),
      ("monotonicline", "Monotonic lines"),
      ("rectilinear", "Rectilinear"),
      ("concentric", "Concentric")]),
    ("thick_bridges", "Thick bridges",
     "Prints bridges with thicker lines - more sag resistance, rougher "
     "look.", None, "checkbox", None),
    ("bridge_flow", "Bridge flow",
     "How much plastic is pushed while bridging. Less means less sag.",
     "ratio, 1 is normal", "number", None),
    ("minimum_sparse_infill_area", "Minimum infill area",
     "Areas smaller than this become solid instead of sparse infill.",
     "square mm", "number", None),
]

SUPPORT = [
    ("enable_support", "Enable supports",
     "Print support structures under overhangs.", None, "checkbox", None),
    ("support_type", "Support type",
     "Tree supports touch less of the model and are easier to remove.",
     None, "select",
     [("normal(auto)", "Normal, automatic"),
      ("tree(auto)", "Tree, automatic"),
      ("normal(manual)", "Normal, manual paint-on only"),
      ("tree(manual)", "Tree, manual paint-on only")]),
    ("support_threshold_angle", "Overhang angle threshold",
     "Overhangs steeper than this angle get supported.",
     "degrees", "number", None),
    ("support_on_build_plate_only", "Supports from build plate only",
     "Never grow supports on top of the model itself.",
     None, "checkbox", None),
    ("support_top_z_distance", "Support top gap",
     "Gap between supports and the model above them. Bigger removes "
     "easier, smaller supports better.", "mm", "number", None),
    ("tree_support_branch_diameter", "Tree support branch thickness",
     "Diameter of tree support branches. Thicker is sturdier.",
     "mm", "number", None),
    ("tree_support_branch_angle", "Tree support branch angle",
     "How far branches may lean while growing.", "degrees", "number", None),
    ("tree_support_wall_count", "Tree support wall count",
     "Walls of each tree branch. More walls, sturdier trees.",
     "walls", "number", None),
    ("support_interface_top_layers", "Support interface layers",
     "Dense layers between supports and the model for a cleaner "
     "underside.", "layers", "number", None),
    ("support_interface_bottom_layers", "Support interface bottom layers",
     "Dense layers where supports stand on the model.",
     "layers", "number", None),
    ("support_filament", "Support filament",
     "Which project filament prints the support body. 0 means the "
     "default.", "filament number", "number", None),
    ("support_interface_filament", "Support interface filament",
     "Which filament prints the dense interface layers. 0 means the "
     "default.", "filament number", "number", None),
    ("support_base_pattern", "Support pattern",
     "Line pattern of normal support bodies.", None, "select",
     [("default", "Default"),
      ("rectilinear", "Rectilinear"),
      ("rectilinear-grid", "Rectilinear grid"),
      ("honeycomb", "Honeycomb"),
      ("lightning", "Lightning - fastest, least material"),
      ("hollow", "Hollow")]),
    ("support_expansion", "Support expansion",
     "Grows each support area outward for sturdier supports.",
     "mm", "number", None),
    ("support_object_xy_distance", "Support to model horizontal gap",
     "Sideways clearance between supports and the model.",
     "mm", "number", None),
    ("tree_support_branch_distance", "Tree support branch spacing",
     "Distance between branch tips where they meet the model.",
     "mm", "number", None),
    ("tree_support_tip_diameter", "Tree support tip thickness",
     "Diameter of the thin branch tips touching the model.",
     "mm", "number", None),
    ("max_bridge_length", "Maximum bridge length",
     "Overhangs bridging further than this get supported anyway.",
     "mm", "number", None),
]

SPEED = [
    ("outer_wall_speed", "Outer wall speed",
     "Printing speed for the visible outside walls. Slower is neater.",
     "mm per second", "number", None),
    ("inner_wall_speed", "Inner wall speed",
     "Speed for the hidden inner walls.", "mm per second", "number", None),
    ("sparse_infill_speed", "Infill speed",
     "Speed for the inside filling.", "mm per second", "number", None),
    ("internal_solid_infill_speed", "Solid infill speed",
     "Speed for solid filled layers.", "mm per second", "number", None),
    ("top_surface_speed", "Top surface speed",
     "Speed for the visible top layers. Slower is smoother.",
     "mm per second", "number", None),
    ("initial_layer_speed", "First layer speed",
     "Speed for the first layer. Slow means good plate grip.",
     "mm per second", "number", None),
    ("bridge_speed", "Bridging speed",
     "Speed when printing across open air.", "mm per second", "number", None),
    ("gap_infill_speed", "Gap infill speed",
     "Speed for filling thin gaps.", "mm per second", "number", None),
    ("travel_speed", "Travel speed",
     "Speed when moving without printing.", "mm per second", "number", None),
    ("default_acceleration", "Acceleration",
     "How fast the printer changes speed. Lower can reduce ringing "
     "marks.", "mm per second squared", "number", None),
    ("outer_wall_acceleration", "Outer wall acceleration",
     "Acceleration for the visible walls. Lower means cleaner surfaces.",
     "mm per second squared", "number", None),
    ("initial_layer_acceleration", "First layer acceleration",
     "Gentle acceleration for a well-stuck first layer.",
     "mm per second squared", "number", None),
    ("travel_acceleration", "Travel acceleration",
     "Acceleration for non-printing moves.",
     "mm per second squared", "number", None),
    ("enable_overhang_speed", "Slow down overhangs",
     "Automatically slows printing on overhanging walls.",
     None, "checkbox", None),
    ("small_perimeter_speed", "Small feature speed",
     "Speed for very small loops like holes. Often a percentage of "
     "outer wall speed.", "mm per second or percent", "text", None),
]

ADHESION = [
    ("brim_type", "Brim",
     "A flat ring around the model's first layer for grip.",
     None, "select",
     [("auto_brim", "Automatic"),
      ("outer_only", "Outside edge only"),
      ("inner_only", "Inside holes only"),
      ("outer_and_inner", "Outside and inside"),
      ("brim_ears", "Ears at corners only"),
      ("no_brim", "No brim")]),
    ("brim_width", "Brim width",
     "How wide the brim ring is.", "mm", "number", None),
    ("brim_object_gap", "Brim gap",
     "Small gap between brim and model so it snaps off cleanly.",
     "mm", "number", None),
    ("skirt_loops", "Skirt loops",
     "Loose loops printed around the model to prime the nozzle.",
     "loops", "number", None),
    ("raft_layers", "Raft layers",
     "A thick disposable base under the whole model. 0 means no raft.",
     "layers", "number", None),
    ("skirt_distance", "Skirt distance",
     "Gap between the skirt loops and the model.", "mm", "number", None),
    ("skirt_height", "Skirt height",
     "How many layers tall the skirt is.", "layers", "number", None),
    ("raft_contact_distance", "Raft gap",
     "Gap between the raft and the model so they separate.",
     "mm", "number", None),
    ("raft_expansion", "Raft expansion",
     "How far the raft extends beyond the model.", "mm", "number", None),
]

# Bambu Studio's "Others" process tab. spiral_mode's placement here is
# screenshot-verified via the 2026-08-27 tab-audit note; the remaining
# placements follow Studio's grouping by name (Special mode / Fuzzy skin
# / G-code output) and are DOCUMENTED GUESSES until the tab audit
# confirms them with Edis or screenshots.
OTHERS = [
    ("print_sequence", "Print sequence",
     "By layer prints everything together; by object finishes one whole "
     "object before starting the next (objects need clearance).",
     None, "select",
     [("by layer", "By layer - normal"),
      ("by object", "By object - one object at a time")]),
    ("spiral_mode", "Spiral vase mode",
     "Prints the model as one continuous single-walled spiral. Only for "
     "vase-like models.", None, "checkbox", None),
    ("slicing_mode", "Slicing mode",
     "Regular suits almost everything. Even-odd alternates solid and "
     "hollow for special transparency effects; close holes fills "
     "internal cavities.", None, "select",
     [("regular", "Regular - normal"),
      ("even_odd", "Even-odd"),
      ("close_holes", "Close holes")]),
    ("timelapse_type", "Timelapse",
     "How the printer takes timelapse photos. Smooth pauses at a prime "
     "tower for each photo, so it needs one.", None, "select",
     [("0", "Traditional"),
      ("1", "Smooth - needs a prime tower")]),
    ("fuzzy_skin", "Fuzzy skin",
     "Deliberately roughens walls for a textured, fingerprint-hiding "
     "surface.", None, "select",
     [("none", "Off"),
      ("external", "Outside walls only"),
      ("all", "All walls")]),
    ("fuzzy_skin_point_distance", "Fuzzy skin point distance",
     "Distance between the random bumps. Smaller is a denser texture.",
     "mm", "number", None),
    ("fuzzy_skin_thickness", "Fuzzy skin thickness",
     "How far the bumps stick out of the wall.", "mm", "number", None),
    ("exclude_object", "Allow skipping objects",
     "Writes object markers into the G-code so single objects can be "
     "skipped on the printer's screen mid-print.", None, "checkbox", None),
    ("gcode_add_line_number", "Add line numbers to G-code",
     "Numbers every G-code line. Only useful for debugging.",
     None, "checkbox", None),
]

# ----------------------------------------- flushing and prime tower page
#
# These keys are edited on the dedicated "Flushing and prime tower"
# page (linked from the Filaments tab), not on a curated tab. The
# matrix key holds one N x N grid of flush volumes PER EXTRUDER,
# flattened: length = extruders x filaments x filaments, diagonal 0.

FLUSH_MATRIX_KEY = "flush_volumes_matrix"
FLUSH_MULTIPLIER_KEY = "flush_multiplier"

FLUSH_TOGGLES = [
    ("flush_into_infill", "Flush into infill",
     "Uses colour-change waste to print hidden sparse infill instead of "
     "flushing it all into the prime tower."),
    ("flush_into_objects", "Flush into objects",
     "Uses waste inside whole objects whose look does not matter."),
    ("flush_into_support", "Flush into support",
     "Uses waste to print support bodies - their colour is irrelevant."),
]

PRIME_TOWER = [
    ("enable_prime_tower", "Prime tower",
     "A sacrificial tower the nozzle primes on after each filament "
     "change. Needed for clean colours and smooth timelapse.",
     None, "checkbox", None),
    ("prime_tower_width", "Prime tower width",
     "Width of the prime tower.", "mm", "number", None),
    ("prime_tower_brim_width", "Prime tower brim width",
     "Brim around the prime tower. -1 means automatic.",
     "mm, -1 is automatic", "number", None),
]

FLUSHING_PAGE_KEYS = ({FLUSH_MATRIX_KEY, FLUSH_MULTIPLIER_KEY}
                      | {k for k, _l, _h in FLUSH_TOGGLES}
                      | {e[0] for e in PRIME_TOWER})

# Per-filament list settings shown on the Filaments tab, one labelled
# field per filament slot.
FILAMENTS = [
    ("nozzle_temperature", "Nozzle temperature",
     "Printing temperature for this filament.", "degrees C"),
    ("nozzle_temperature_initial_layer", "First layer nozzle temperature",
     "Often a touch hotter for grip.", "degrees C"),
    ("textured_plate_temp", "Bed temperature on the textured PEI plate",
     "Build plate temperature when using the textured plate.", "degrees C"),
    ("hot_plate_temp", "Bed temperature on the smooth hot plate",
     "Build plate temperature when using the smooth high-temperature "
     "plate.", "degrees C"),
    ("fan_max_speed", "Maximum part cooling fan speed",
     "Upper limit for the cooling fan.", "percent"),
    ("fan_min_speed", "Minimum part cooling fan speed",
     "Lower limit for the cooling fan.", "percent"),
    ("filament_flow_ratio", "Flow ratio",
     "Fine-tunes how much plastic is pushed out. 1 is neutral.",
     "ratio, e.g. 0.98"),
    ("filament_max_volumetric_speed", "Maximum volumetric speed",
     "The hard ceiling on how fast this filament can be melted. The "
     "single biggest speed limiter.", "cubic mm per second"),
    ("slow_down_layer_time", "Minimum layer time",
     "Layers faster than this are slowed so the plastic can cool.",
     "seconds"),
    ("close_fan_the_first_x_layers", "Fan off for first layers",
     "Keeps the cooling fan off for this many first layers for grip.",
     "layers"),
    ("overhang_fan_speed", "Overhang fan speed",
     "Fan speed used on overhangs and bridges.", "percent"),
    ("pressure_advance", "Pressure advance",
     "Compensates nozzle pressure for sharper corners.",
     "factor, e.g. 0.02"),
]

TABS = [
    ("quality", "Quality", QUALITY),
    ("strength", "Strength", STRENGTH),
    ("support", "Support", SUPPORT),
    ("speed", "Speed", SPEED),
    ("adhesion", "Adhesion", ADHESION),
    ("others", "Others", OTHERS),
]

# ----------------------------------------------------- all-settings typing

# Keys we positively know hold "0"/"1" booleans. Only these get a real
# checkbox in the All settings view; unknown keys stay as text fields so
# a wrong guess can never hide a value.
_CURATED_BY_KEY = {
    e[0]: e for tab in (QUALITY, STRENGTH, SUPPORT, SPEED, ADHESION,
                        OTHERS, PRIME_TOWER)
    for e in tab
}

# Every key that already has a curated home (tabs, Filaments, or the
# flushing page), so the per-tab "more settings" lists can exclude them.
CURATED_KEYS = (set(_CURATED_BY_KEY) | {e[0] for e in FILAMENTS}
                | FLUSHING_PAGE_KEYS)

KNOWN_BOOL_KEYS = {
    key for key, *_rest in _CURATED_BY_KEY.values()
    if _CURATED_BY_KEY[key][4] == "checkbox"
} | {
    "enable_prime_tower", "enable_arc_fitting", "enable_overhang_speed",
    "detect_overhang_wall", "detect_thin_wall", "detect_narrow_internal_solid_infill",
    "reduce_infill_retraction", "only_one_wall_top", "only_one_wall_first_layer",
    "enable_support", "interface_shells", "infill_combination",
    "print_sequence_one_by_one", "scan_first_layer", "slow_down_for_layer_cooling",
    "reduce_fan_stop_start_freq", "activate_air_filtration", "support_critical_regions_only",
    "exclude_object", "gcode_add_line_number", "independent_support_layer_height",
    "flush_into_infill", "flush_into_objects", "flush_into_support",
    "spiral_mode",
}


def curated_entry(key):
    """The curated (key,label,help,unit,control,options) or None."""
    return _CURATED_BY_KEY.get(key)


def control_for(key, value):
    """Best accessible control for an arbitrary key in All settings.

    Returns (control, options): "checkbox" only for known booleans,
    otherwise "text" - which safely round-trips every Bambu value format
    (numbers, percentages, expressions, names).
    """
    entry = curated_entry(key)
    if entry:
        control, options = entry[4], entry[5]
        if control == "checkbox" and str(value) not in ("0", "1"):
            return "text", None    # never misrender an odd value
        return control, options
    if key in KNOWN_BOOL_KEYS and str(value) in ("0", "1"):
        return "checkbox", None
    return "text", None


def label_for(key):
    """Plain-language label if curated, else the raw key itself."""
    entry = curated_entry(key)
    return entry[1] if entry else key


# --------------------------------------------------- tab topic classifier

# Every curated tab also lists ALL remaining settings of its topic (as
# links to their edit pages), so nothing is hidden behind the curated
# selection. This classifier assigns a process-scope key to a tab by
# name. Order matters: speed words are checked before strength words so
# sparse_infill_speed lands on Speed, not Strength.

_TOPIC_RULES = [
    # Others is checked first: its topics (fuzzy skin, spiral vase,
    # timelapse, flushing, prime tower, G-code output) use words the
    # broader quality/strength needles would otherwise claim.
    ("others", ("fuzzy", "spiral", "timelapse", "exclude_object",
                "gcode_add_line_number", "print_sequence", "print_order",
                "slicing_mode", "flush_", "prime_tower", "prime_volume",
                "wipe_tower")),
    ("speed", ("_speed", "speed_", "_acceleration", "acceleration_",
               "_jerk", "jerk_", "slowdown", "slow_down")),
    ("support", ("support", "tree_", "raft_", "bridge_no_",
                 "max_bridge_length")),
    ("adhesion", ("brim", "skirt", "elefant_foot", "draft_shield")),
    ("strength", ("infill", "shell", "wall_loops", "top_surface",
                  "bottom_surface", "bridge", "anchor", "solid_")),
    ("quality", ("layer_height", "line_width", "seam", "ironing", "wall",
                 "precise", "resolution", "xy_", "gap_",
                 "detect_", "slice_", "precision", "overhang",
                 "hole_", "contour_", "corner_", "smooth", "polish",
                 "arc_fitting", "circle_", "top_", "bottom_", "only_one",
                 "initial_layer", "first_layer",
                 "z_direction", "compensation")),
]


# Keys the needle rules would misfile, pinned to the tab Bambu Studio's
# own GUI shows them on (verified against the real GUI, 2026-08-26).
# E.g. smooth_speed_discontinuity_area contains "_speed" but lives on
# Studio's Quality tab, not Speed.
_TOPIC_OVERRIDES = {
    "smooth_speed_discontinuity_area": "quality",
    "smooth_coefficient": "quality",
    "reduce_crossing_wall": "quality",
    "max_travel_detour_distance": "quality",
    "avoid_crossing_wall_includes_support": "quality",
}

# Keys Bambu Studio's GUI writes TOGETHER when a per-object infill
# density is set (verified in the 2026-08-27 specimen project): the
# skin and skeleton variants silently receive the same value.
OBJECT_COMPANION_KEYS = {
    "sparse_infill_density": ("skeleton_infill_density",
                              "skin_infill_density"),
}

# The shortlist offered up front on the per-object settings page -
# the settings Studio's own object panel surfaces most prominently.
# Only keys present in the open project with scalar values are shown;
# every other scalar key is reachable through the page's search.
OBJECT_CURATED_KEYS = (
    "sparse_infill_density", "sparse_infill_pattern", "wall_loops",
    "top_shell_layers", "bottom_shell_layers", "enable_support",
    "support_type", "support_threshold_angle", "seam_position",
    "ironing_type", "brim_type",
)

# Fields whose value is millimetres or a percentage ("mm or %" in the
# slicer GUI). A large bare number typed here triggers the same
# "Is it N% or N mm?" validation question Bambu Studio pops.
MM_OR_PERCENT_KEYS = {"max_travel_detour_distance"}


def tab_for(key):
    """Which curated tab a process-scope key belongs on, or None."""
    if key in _TOPIC_OVERRIDES:
        return _TOPIC_OVERRIDES[key]
    for tab_id, needles in _TOPIC_RULES:
        for needle in needles:
            if needle in key:
                return tab_id
    return None
