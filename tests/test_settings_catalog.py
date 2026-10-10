"""Tests for settings_catalog - curated entries, typing, tab topics.

The catalog audit tests run against the committed test project so a
typo in a curated key (which would silently hide the control) fails
loudly here.
"""

import shutil
import unittest

import support
import settings_catalog as sc
from slicer_project import ProjectSession

# Curated keys that legitimately do not exist in the committed H2C test
# project (checked 2026-08-27). A key showing up here unexpectedly means
# a typo in the catalog or a structural change in the test data.
ALLOWED_ABSENT = {"only_one_wall_top", "tree_support_tip_diameter"}


class CatalogAuditTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sandbox = support.make_sandbox()
        cls.session = ProjectSession(support.copy_test_project(cls.sandbox))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def test_every_curated_key_exists_in_the_test_project(self):
        absent = {k for k in sc.CURATED_KEYS
                  if k not in self.session.settings}
        self.assertEqual(absent, ALLOWED_ABSENT,
                         "curated keys missing from the test project "
                         "(typo in the catalog?)")

    def test_filament_tab_keys_are_lists_in_the_project(self):
        for key, *_rest in sc.FILAMENTS:
            value = self.session.settings.get(key)
            if value is None:
                continue
            self.assertIsInstance(value, list,
                                  f"{key} is on the Filaments tab but is "
                                  "not a per-filament list")

    def test_infill_pattern_options_match_the_stored_spellings(self):
        entry = sc.curated_entry("sparse_infill_pattern")
        values = {v for v, _label in entry[5]}
        # The project's own value and the 100%-dialog answer must be
        # selectable, with Bambu's exact stored spellings.
        self.assertIn("grid", values)
        self.assertIn("zig-zag", values)
        self.assertIn("alignedrectilinear", values)
        self.assertIn(self.session.settings["sparse_infill_pattern"], values)


class TypingTests(unittest.TestCase):

    def test_checkbox_only_for_known_booleans(self):
        self.assertEqual(sc.control_for("enable_support", "1")[0],
                         "checkbox")
        self.assertEqual(sc.control_for("exclude_object", "0")[0],
                         "checkbox")
        # Unknown keys must stay text so no value can be misrendered.
        self.assertEqual(sc.control_for("some_future_key", "1")[0], "text")
        # A known boolean with a non-boolean value falls back to text.
        self.assertEqual(sc.control_for("exclude_object", "maybe")[0],
                         "text")

    def test_curated_control_wins(self):
        control, options = sc.control_for("sparse_infill_pattern", "grid")
        self.assertEqual(control, "select")
        self.assertTrue(options)

    def test_label_for_falls_back_to_the_raw_key(self):
        self.assertEqual(sc.label_for("wall_loops"), "Wall count")
        self.assertEqual(sc.label_for("mystery_key"), "mystery_key")


class TabTopicTests(unittest.TestCase):

    def test_overrides_pin_studio_verified_placements(self):
        # smooth_speed_discontinuity_area contains "_speed" but lives on
        # Studio's Quality tab (screenshot-verified 2026-08-26).
        self.assertEqual(sc.tab_for("smooth_speed_discontinuity_area"),
                         "quality")
        self.assertEqual(sc.tab_for("reduce_crossing_wall"), "quality")
        self.assertEqual(sc.tab_for("max_travel_detour_distance"),
                         "quality")

    def test_topic_rules_route_obvious_keys(self):
        self.assertEqual(sc.tab_for("sparse_infill_speed"), "speed")
        self.assertEqual(sc.tab_for("tree_support_branch_diameter"),
                         "support")
        self.assertEqual(sc.tab_for("brim_width"), "adhesion")
        self.assertEqual(sc.tab_for("sparse_infill_density"), "strength")
        self.assertEqual(sc.tab_for("layer_height"), "quality")

    def test_mm_or_percent_keys(self):
        self.assertIn("max_travel_detour_distance", sc.MM_OR_PERCENT_KEYS)


class OthersAndFlushingTests(unittest.TestCase):
    """The Others tab and the flushing/prime-tower page knowledge."""

    def test_others_is_a_curated_tab(self):
        self.assertIn("others", [t[0] for t in sc.TABS])

    def test_spiral_mode_moved_from_quality_to_others(self):
        # The 2026-08-27 tab-audit note: Studio shows spiral vase on
        # its Others tab, not Quality.
        self.assertNotIn("spiral_mode", {e[0] for e in sc.QUALITY})
        self.assertIn("spiral_mode", {e[0] for e in sc.OTHERS})

    def test_topic_rules_route_others_keys(self):
        self.assertEqual(sc.tab_for("fuzzy_skin"), "others")
        self.assertEqual(sc.tab_for("timelapse_type"), "others")
        self.assertEqual(sc.tab_for("wipe_tower_no_sparse_layers"),
                         "others")
        self.assertEqual(sc.tab_for("flush_volumes_vector"), "others")

    def test_flushing_page_keys_are_curated_so_not_double_listed(self):
        for key in ("flush_volumes_matrix", "flush_multiplier",
                    "flush_into_support", "enable_prime_tower",
                    "prime_tower_width", "prime_tower_brim_width"):
            self.assertIn(key, sc.CURATED_KEYS, key)

    def test_curated_checkbox_with_an_odd_value_falls_back_to_text(self):
        # The never-misrender rule holds for curated checkboxes too.
        self.assertEqual(sc.control_for("spiral_mode", "0")[0], "checkbox")
        self.assertEqual(sc.control_for("spiral_mode", "maybe")[0], "text")


if __name__ == "__main__":
    unittest.main()
