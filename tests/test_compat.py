"""Tests for compat.py - the verified-Studio-version policy.

The rule under test: unverified versions warn but never refuse
(structural checks handle real breakage), and unparseable versions
stay quiet.
"""

import unittest

import support  # noqa: F401  (sets up sys.path)
import compat


class SeriesParsingTests(unittest.TestCase):

    def test_conf_and_3mf_forms_both_parse(self):
        self.assertEqual(compat.series("02.08.02.61"), "02.08")
        self.assertEqual(compat.series("BambuStudio-02.08.02.61"), "02.08")
        self.assertEqual(compat.series("BambuStudio-03.00.00.01"), "03.00")

    def test_garbage_parses_to_empty(self):
        self.assertEqual(compat.series(""), "")
        self.assertEqual(compat.series(None), "")
        self.assertEqual(compat.series("OrcaSlicer-2.3"), "")
        self.assertEqual(compat.series("(unknown application)"), "")


class UnverifiedNoteTests(unittest.TestCase):

    def test_verified_versions_are_quiet(self):
        self.assertIsNone(compat.unverified_note("02.08.02.61", "X"))
        self.assertIsNone(
            compat.unverified_note("BambuStudio-02.08.99.99", "X"))

    def test_unverified_versions_get_a_caution_not_a_refusal(self):
        note = compat.unverified_note("BambuStudio-03.00.00.01",
                                      "This project")
        self.assertIn("This project", note)
        self.assertIn("03.00", note)
        self.assertIn("not been verified", note)
        self.assertIn("checked structurally", note,
                      "the caution must say the safety net still holds")

    def test_unparseable_versions_stay_quiet(self):
        self.assertIsNone(compat.unverified_note("", "X"))
        self.assertIsNone(compat.unverified_note("(unknown application)",
                                                 "X"))


if __name__ == "__main__":
    unittest.main()
