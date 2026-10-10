"""The PrusaSlicer backend (built 2026-09-02) - the second project format.

Covers, hermetically (a stub install, a mini vendor bundle and a fake
engine under a sandbox %APPDATA%):

- the format: the INI-style settings entry round-trips byte for byte,
  per-extruder values split and join in all three spellings, the
  model config's object lines read, change and remove cleanly,
- the preset library: bundle sections with multi-parent inheritance,
  the installed-models filter, user presets inheriting from the
  bundle, key scopes, the printer's default profiles,
- the session on the committed Core One specimen (built by AccessSlice
  from PrusaSlicer's own presets) and on a real 2.9.4-saved five-tool
  tile: open, stage, save, reopen for settings, per-extruder lists,
  per-object overrides, transforms and presets; single-bed answers;
  the 100%-infill question with PrusaSlicer's keys; the verification
  and backups,
- the engine driver on binary and ASCII G-code, refusals quoted,
- the project builder from presets and from a donor,
- pre-flight through the backend's key table,
- the whole editor over HTTP, every page, plus a fake slice to a
  report, and
- with the real PrusaSlicer installed: a real build and a real slice.
"""

import http.client
import json
import os
import shutil
import sys
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import support
import backends
import config_store
import edit_bambu_3mf as engine
import preflight
import prusa_catalog
import prusa_compat
import prusa_engine
import prusa_format
import prusa_newjob
import prusa_profiles
import server
import webui
from errors import ProjectError
from prusa_project import PrusaProjectSession

TESTDATA = support.TEST_PROJECT.parent
COREONE = (TESTDATA / "blind-benchy-core-one-built-by-accessslice-from-"
           "prusaslicer-presets.3mf")
TILE = TESTDATA / "map-tile-saved-from-prusaslicer.3mf"
PRINTER = "Prusa CORE One HF0.4 nozzle"

EMPTY_PRESET_VIEW = {
    "process": [], "filament_options": [], "filament_matches": None,
    "filament_search": "", "machine_options": [], "machine_matches": None,
    "printer_search": "",
}


class SandboxCase(unittest.TestCase):
    """A sandbox with the fake install and %APPDATA% pointed at it."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.exe = support.make_fake_prusa(self.sandbox)
        self._old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(self.sandbox / "AppData")
        self.addCleanup(self._restore_appdata)
        prusa_profiles._bundle_cache.clear()
        prusa_profiles._scope_cache.clear()

    def _restore_appdata(self):
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata

    def copy(self, source, name):
        target = self.sandbox / name
        shutil.copy2(source, target)
        return target


# ------------------------------------------------------------ format


class FormatTests(unittest.TestCase):

    def test_config_round_trips_byte_for_byte(self):
        entries = engine.load_entries(COREONE)
        text = entries[prusa_format.CONFIG_ENTRY].decode("utf-8")
        header, settings = prusa_format.parse_config(text)
        self.assertGreater(len(settings), 300)
        self.assertEqual(prusa_format.serialise_config(header, settings),
                         text)
        self.assertEqual(prusa_format.generator_line(header),
                         "PrusaSlicer 2.9.6")

    def test_per_extruder_values_in_all_three_spellings(self):
        self.assertEqual(prusa_format.split_value("temperature", "225,220"),
                         ["225", "220"])
        self.assertEqual(prusa_format.split_value("filament_type", "PLA;PETG"),
                         ["PLA", "PETG"])
        self.assertEqual(prusa_format.split_value(
            "filament_settings_id", '"Prusament PLA @XLIS";"Generic PLA"'),
            ["Prusament PLA @XLIS", "Generic PLA"])
        self.assertEqual(prusa_format.split_value("bed_shape", "0x0,250x0"),
                         "0x0,250x0")   # not per-extruder: stays text
        self.assertEqual(prusa_format.join_value("temperature", ["225", "220"]),
                         "225,220")
        self.assertEqual(prusa_format.join_value("filament_type", ["PLA"]),
                         "PLA")
        self.assertEqual(prusa_format.join_value(
            "filament_settings_id", ['A "quoted" name', "B"]),
            '"A \\"quoted\\" name";"B"')
        self.assertEqual(prusa_format.split_value(
            "filament_settings_id", '"A \\"quoted\\" name";"B"'),
            ['A "quoted" name', "B"])

    def test_the_five_tool_tile_reads_five_values_per_extruder(self):
        text = engine.load_entries(TILE)[prusa_format.CONFIG_ENTRY].decode()
        _header, settings = prusa_format.parse_config(text)
        self.assertEqual(len(settings["temperature"]), 5)
        self.assertEqual(len(settings["filament_type"]), 5)
        self.assertEqual(len(settings["nozzle_diameter"]), 5)
        self.assertEqual(settings["printer_model"], "XL5IS")
        self.assertEqual(prusa_format.bed_size(settings), (360.0, 360.0))

    def test_object_metadata_lines(self):
        xml = engine.load_entries(COREONE)[
            prusa_format.MODEL_CONFIG_ENTRY].decode("utf-8")
        objs = prusa_format.object_entries(xml)
        self.assertEqual(list(objs), ["1"])
        self.assertEqual(objs["1"]["name"], "blind-benchy.stl")
        self.assertEqual(objs["1"]["instances"], 1)
        self.assertEqual(objs["1"]["overrides"], {})
        added = prusa_format.set_object_metadata(xml, "1", "fill_density",
                                                 "40%")
        self.assertIn('<metadata type="object" key="fill_density" '
                      'value="40%"/>', added)
        self.assertEqual(prusa_format.object_entries(added)["1"]["overrides"],
                         {"fill_density": "40%"})
        # The line sits right after the name, before the volume.
        self.assertLess(added.index('key="name"'), added.index('key="fill_density"'))
        self.assertLess(added.index('key="fill_density"'), added.index("<volume"))
        changed = prusa_format.set_object_metadata(added, "1", "fill_density",
                                                   "55%")
        self.assertEqual(prusa_format.object_entries(changed)["1"]["overrides"],
                         {"fill_density": "55%"})
        self.assertEqual(changed.count('key="fill_density"'), 1)
        removed = prusa_format.set_object_metadata(changed, "1",
                                                   "fill_density", None)
        self.assertEqual(removed, xml)
        with self.assertRaises(KeyError):
            prusa_format.set_object_metadata(xml, "9", "perimeters", "2")

    def test_cli_full_path_names_become_bare_names(self):
        xml = ('<config><object id="1" instances_count="1">'
               '<metadata type="object" key="name" '
               'value="C:/models/sub/part.stl"/>'
               '<volume firstid="0" lastid="1"><metadata type="volume" '
               'key="name" value="C:\\models\\sub\\part.stl"/></volume>'
               '</object></config>')
        fixed = prusa_format.rename_objects_to_basenames(xml)
        self.assertEqual(fixed.count('value="part.stl"'), 2)


class CompatTests(unittest.TestCase):

    def test_series_forms(self):
        self.assertEqual(prusa_compat.series("PrusaSlicer 2.9.4"), "2.9")
        self.assertEqual(prusa_compat.series(
            "; generated by PrusaSlicer 2.9.4 on 2026-05-05"), "2.9")
        self.assertEqual(prusa_compat.series("PrusaSlicer-2.9.6 based on"),
                         "2.9")
        self.assertEqual(prusa_compat.series("2.10.0"), "2.10")
        self.assertEqual(prusa_compat.series("garbage"), "")

    def test_cautions(self):
        self.assertIsNone(prusa_compat.unverified_note("PrusaSlicer 2.9.4",
                                                       "This project"))
        self.assertIn("2.10", prusa_compat.unverified_note(
            "PrusaSlicer 2.10.0", "This project"))
        self.assertIsNone(prusa_compat.unverified_note("", "x"))


# ---------------------------------------------------------- profiles


class ProfileTests(SandboxCase):

    def test_bundle_lists_and_installed_filter(self):
        machines = prusa_profiles.list_presets(self.exe, "machine")
        self.assertEqual(machines, ["Prusa CORE One HF0.4 nozzle",
                                    "Prusa CORE One 0.4 nozzle"])
        self.assertNotIn("*common*", machines)
        prints = prusa_profiles.list_presets(self.exe, "process")
        self.assertIn("0.20mm SPEED @COREONE HF0.4", prints)
        self.assertIn("0.20mm SPEED @XL 0.4", prints)
        self.assertNotIn("*speed*", prints)

    def test_multi_parent_inheritance_resolves_in_order(self):
        values = prusa_profiles.resolve_preset(self.exe, "process",
                                               "0.20mm SPEED @COREONE HF0.4")
        self.assertEqual(values["perimeters"], "3")        # own value
        self.assertEqual(values["perimeter_speed"], "200")   # from *speed*
        self.assertEqual(values["layer_height"], "0.2")    # from *common*
        self.assertNotIn("inherits", values)
        self.assertNotIn("compatible_printers_condition", values)

    def test_user_preset_inherits_from_the_bundle(self):
        self.assertEqual(prusa_profiles.list_user_presets("process"),
                         ["My draft"])
        values = prusa_profiles.resolve_preset(self.exe, "process", "My draft")
        self.assertEqual(values["layer_height"], "0.3")
        self.assertEqual(values["perimeters"], "3")

    def test_unknown_preset_is_a_plain_error(self):
        with self.assertRaises(prusa_profiles.ProfileError):
            prusa_profiles.resolve_preset(self.exe, "filament", "Nope")

    def test_scopes_and_defaults(self):
        self.assertEqual(prusa_profiles.scope_of(self.exe, "perimeters"),
                         "process")
        self.assertEqual(prusa_profiles.scope_of(self.exe, "temperature"),
                         "filament")
        self.assertEqual(prusa_profiles.scope_of(self.exe, "bed_shape"),
                         "machine")
        # Not in the mini bundle: by name.
        self.assertEqual(prusa_profiles.scope_of(self.exe, "filament_cost"),
                         "filament")
        self.assertEqual(prusa_profiles.scope_of(self.exe, "printer_notes"),
                         "machine")
        self.assertEqual(prusa_profiles.scope_of(self.exe, "ironing"),
                         "process")
        self.assertEqual(prusa_profiles.printer_defaults(self.exe, PRINTER),
                         ("0.20mm SPEED @COREONE HF0.4",
                          "Prusament PLA @COREONE HF0.4"))
        self.assertEqual(prusa_profiles.installed_version(), "2.9.6")
        self.assertEqual(prusa_profiles.installed_models(),
                         {"COREONE": ["HF0.4", "0.4"]})


# ----------------------------------------------------------- session


class SessionTests(SandboxCase):

    def setUp(self):
        super().setUp()
        self.project = self.copy(COREONE, "benchy.3mf")

    def open(self):
        return PrusaProjectSession(self.project, slicer_exe=str(self.exe))

    def test_opens_with_the_right_facts(self):
        s = self.open()
        self.assertEqual(s.application, "PrusaSlicer 2.9.6")
        self.assertEqual(s.structure_notes, [])
        self.assertEqual(s.catalog, prusa_catalog)
        objs = s.objects()
        self.assertEqual([o["name"] for o in objs], ["blind-benchy.stl"])
        self.assertAlmostEqual(objs[0]["x"], 125.0)
        self.assertAlmostEqual(objs[0]["y"], 110.0)
        self.assertEqual(s.settings["printer_settings_id"], PRINTER)
        self.assertEqual(s.settings["temperature"], ["230"])
        self.assertEqual(s.filament_count(), 1)
        self.assertEqual(s.filament_names(), ["Prusament PLA @COREONE HF0.4"])
        self.assertEqual(s.plate_size(), (250.0, 220.0))
        self.assertEqual(len(s.plates()), 1)
        self.assertEqual(s.plates()[0]["object_ids"], ["1"])
        self.assertEqual(s.filament_map(), (None, []))
        self.assertIn("Bed: 250 by 220 mm", s.summary())
        # The mesh sits exactly on the bed, validating the transform chain.
        import geometry
        eff = s.effective_item_nums("1")
        bb = geometry.bbox([geometry.transform_point(eff, v)
                            for v in s.object_shape("1")[0]])
        self.assertAlmostEqual(bb[2], 0.0, places=3)

    def test_settings_lists_and_overrides_survive_save_and_reopen(self):
        s = self.open()
        self.assertTrue(s.stage_setting("perimeters", "3"))
        self.assertTrue(s.stage_setting("temperature", ["215"]))
        with self.assertRaises(ProjectError):
            s.stage_setting("temperature", ["215", "220"])
        with self.assertRaises(ProjectError):
            s.stage_setting("no_such_key", "1")
        self.assertTrue(s.stage_object_setting("1", "fill_density", "40%"))
        lines = s.save()
        self.assertIn("3 changes", lines[0])
        fresh = self.open()
        self.assertEqual(fresh.settings["perimeters"], "3")
        self.assertEqual(fresh.settings["temperature"], ["215"])
        self.assertEqual(fresh.object_overrides("1"), {"fill_density": "40%"})
        text = engine.load_entries(self.project)[
            prusa_format.CONFIG_ENTRY].decode("utf-8")
        self.assertIn("\n; temperature = 215\n", text)
        self.assertIn("\n; perimeters = 3\n", text)
        self.assertTrue(fresh.stage_object_setting("1", "fill_density", None))
        fresh.save()
        self.assertEqual(self.open().object_overrides("1"), {})
        self.assertTrue((self.sandbox / "backups").is_dir())

    def test_transforms_and_the_per_object_extruder(self):
        s = self.open()
        self.assertTrue(s.stage_move("1", 100, 90))
        self.assertTrue(s.stage_rotate("1", 45))
        self.assertTrue(s.stage_scale("1", 50))
        self.assertTrue(s.stage_extruder("1", 1))
        s.save()
        fresh = self.open()
        o = fresh.objects()[0]
        self.assertAlmostEqual(o["x"], 100.0)
        self.assertAlmostEqual(o["y"], 90.0)
        self.assertEqual(o["extruder"], "1")
        facts = fresh.object_transform_facts("1")
        self.assertAlmostEqual(facts["scale_percent"], 50.0, places=3)
        self.assertAlmostEqual(facts["z_rotation_degrees"], 45.0, places=3)
        self.assertFalse(fresh.stage_orient("1"))   # already lies well

    def test_single_bed_answers(self):
        s = self.open()
        with self.assertRaises(ProjectError):
            s.stage_replate("1", "2")
        with self.assertRaises(ProjectError):
            s.stage_filament_map(["1"])
        self.assertEqual(s.plate_origin("1"), (0.0, 0.0))
        self.assertTrue(s.plate_grid()["reliable"])

    def test_presets_switch_with_values_from_the_bundle(self):
        s = self.open()
        self.assertTrue(s.stage_preset("process",
                                       "0.20mm QUALITY @COREONE HF0.4"))
        self.assertEqual(s.effective_setting("layer_height"), "0.15")
        self.assertTrue(s.stage_preset("filament",
                                       "Prusament PETG @COREONE HF0.4", 0))
        self.assertEqual(s.effective_setting("temperature"), ["240"])
        self.assertEqual(s.effective_setting("filament_type"), ["PETG"])
        s.save()
        fresh = self.open()
        self.assertEqual(fresh.settings["print_settings_id"],
                         "0.20mm QUALITY @COREONE HF0.4")
        self.assertEqual(fresh.settings["filament_settings_id"],
                         ["Prusament PETG @COREONE HF0.4"])
        self.assertEqual(fresh.settings["temperature"], ["240"])
        with self.assertRaises(ProjectError):
            fresh.stage_preset("process", "No such profile")

    def test_the_infill_question_uses_prusa_keys(self):
        s = self.open()
        self.assertIsNone(s.infill_conflict())
        s.stage_setting("fill_density", "100%")
        s.stage_setting("fill_pattern", "gyroid")
        self.assertEqual(s.infill_conflict(),
                         {"pattern": "gyroid", "density": "100%"})
        self.assertEqual(s.PATTERN_KEY, "fill_pattern")
        self.assertEqual(s.SOLID_PATTERN, "rectilinear")
        self.assertEqual(s.default_infill_density(), "15%")
        self.assertIn("100% density", " ".join(s.advice()))
        s.stage_setting("fill_pattern", "rectilinear")
        self.assertIsNone(s.infill_conflict())

    def test_a_plain_model_3mf_is_refused_with_guidance(self):
        entries = engine.load_entries(self.project)
        del entries[prusa_format.CONFIG_ENTRY]
        engine.save_entries(self.project, entries)
        with self.assertRaises(ProjectError) as cm:
            self.open()
        self.assertIn("Start page", str(cm.exception))

    def test_unverified_version_is_a_caution_not_a_refusal(self):
        entries = engine.load_entries(self.project)
        text = entries[prusa_format.CONFIG_ENTRY].decode("utf-8")
        entries[prusa_format.CONFIG_ENTRY] = text.replace(
            "PrusaSlicer 2.9.6", "PrusaSlicer 2.11.0").encode("utf-8")
        engine.save_entries(self.project, entries)
        s = self.open()
        self.assertEqual(s.application, "PrusaSlicer 2.11.0")
        self.assertTrue(any("2.11" in n for n in s.structure_notes))

    def test_the_real_five_tool_tile_opens(self):
        tile = self.copy(TILE, "tile.3mf")
        s = PrusaProjectSession(tile, slicer_exe=str(self.exe))
        self.assertEqual(s.application, "PrusaSlicer 2.9.4")
        self.assertEqual(s.filament_count(), 5)
        self.assertEqual(len(s.settings["temperature"]), 5)
        self.assertEqual(s.plate_size(), (360.0, 360.0))
        self.assertEqual(s.objects()[0]["name"], "21_colored_Prusa")
        self.assertTrue(s.stage_setting("perimeters", "4"))
        s.save()
        self.assertEqual(PrusaProjectSession(tile).settings["perimeters"], "4")


# ------------------------------------------------------------ engine


class EngineTests(SandboxCase):

    def setUp(self):
        super().setUp()
        self.project = self.copy(COREONE, "benchy.3mf")
        self.script = support.make_fake_prusa_engine(self.sandbox)
        self._original = prusa_engine.build_command
        self.addCleanup(setattr, prusa_engine, "build_command",
                        self._original)

    def _patch(self, behaviour):
        script = self.script
        prusa_engine.build_command = \
            lambda exe, project, plate, out: \
            [sys.executable, str(script), out, behaviour]

    def test_binary_gcode_report(self):
        self._patch("success")
        job = prusa_engine.SliceJob(str(self.exe), self.project)
        job.wait(60)
        self.assertEqual(job.status, "done", job.report)
        self.assertEqual(job.output_file().name, "benchy.bgcode")
        r = job.report
        self.assertTrue(r["ok"])
        self.assertEqual(r["total_time_s"], 5719.0)
        self.assertAlmostEqual(r["total_grams"], 27.4)
        self.assertEqual(r["plates"][0]["filaments"],
                         [{"slot": 1, "grams": 27.4}])
        self.assertEqual(r["plates"][0]["objects"][0]["name"],
                         "blind-benchy.stl")
        self.assertEqual(r["printer_model"], "COREONE")

    def test_ascii_gcode_report(self):
        self._patch("ascii")
        job = prusa_engine.SliceJob(str(self.exe), self.project)
        job.wait(60)
        self.assertEqual(job.status, "done", job.report)
        self.assertEqual(job.report["total_time_s"], 5719.0)
        self.assertEqual(job.report["plates"][0]["objects"][0]["name"],
                         "blind-benchy.stl")

    def test_refusal_quotes_the_engine(self):
        self._patch("fail")
        job = prusa_engine.SliceJob(str(self.exe), self.project)
        job.wait(60)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.report["error_string"],
                         "All objects are outside of the print volume.")

    def test_crash_reports_honestly(self):
        self._patch("crash")
        job = prusa_engine.SliceJob(str(self.exe), self.project)
        job.wait(60)
        self.assertEqual(job.status, "failed")
        self.assertIn("exit code 3", job.report["error_string"])

    def test_helpers(self):
        self.assertEqual(prusa_engine.parse_duration("1h 35m 19s"), 5719)
        self.assertEqual(prusa_engine.parse_duration("2d 1h"), 176400)
        self.assertEqual(prusa_engine.parse_duration(""), 0)
        self.assertTrue(prusa_engine.wants_binary(self.project))
        self.assertEqual(Path(prusa_engine.engine_exe(str(self.exe))).name,
                         "prusa-slicer-console.exe")
        self.assertEqual(prusa_engine.build_command(
            str(self.exe), Path("p.3mf"), 0, "p.bgcode")[1:],
            ["--export-gcode", "--output", "p.bgcode", "p.3mf"])


# ------------------------------------------------------------ newjob


class NewJobTests(SandboxCase):

    def setUp(self):
        super().setUp()
        self.script = support.make_fake_prusa_engine(self.sandbox)
        # The CLI's export: the specimen without its settings entry.
        entries = engine.load_entries(COREONE)
        del entries[prusa_format.CONFIG_ENTRY]
        self.fixture = self.sandbox / "cli-export.3mf"
        engine.save_entries(self.fixture, entries)
        self._original = prusa_newjob.build_export_command
        self.addCleanup(setattr, prusa_newjob, "build_export_command",
                        self._original)
        script, fixture = self.script, self.fixture
        prusa_newjob.build_export_command = \
            lambda exe, models, out, centre: \
            [sys.executable, str(script), out, "export", str(fixture)]
        self.stl = self.sandbox / "blind-benchy.stl"
        self.stl.write_bytes(b"\0" * 84)

    def test_donor_search_finds_a_saved_project_else_the_presets(self):
        saved = self.copy(COREONE, "saved.3mf")
        self.assertEqual(prusa_newjob.find_donor([saved], PRINTER), saved)
        donor = prusa_newjob.find_donor([saved], "Prusa CORE One 0.4 nozzle")
        self.assertEqual(prusa_newjob.preset_donor_printer(donor),
                         "Prusa CORE One 0.4 nozzle")
        self.assertIn("PrusaSlicer presets", donor.name)
        self.assertIsNone(prusa_newjob.preset_donor_printer(saved))

    def test_a_project_from_presets(self):
        donor = prusa_newjob.find_donor([], PRINTER)
        target = prusa_newjob.unique_target(self.sandbox, self.stl)
        notes = prusa_newjob.create_project(str(self.exe), donor,
                                            [self.stl], target)
        self.assertIn("PrusaSlicer's own presets", notes[0])
        s = PrusaProjectSession(target, slicer_exe=str(self.exe))
        self.assertEqual(s.settings["printer_settings_id"], PRINTER)
        self.assertEqual(s.settings["print_settings_id"],
                         "0.20mm SPEED @COREONE HF0.4")
        self.assertEqual(s.settings["filament_settings_id"],
                         ["Prusament PLA @COREONE HF0.4"])
        self.assertEqual(s.settings["temperature"], ["225"])
        self.assertEqual(s.settings["perimeters"], "3")
        self.assertEqual(s.settings["binary_gcode"], "1")
        self.assertEqual(s.objects()[0]["name"], "blind-benchy.stl")
        self.assertFalse((self.sandbox / "blind-benchy.building.3mf").exists())

    def test_a_project_from_a_donor(self):
        donor = self.copy(COREONE, "donor.3mf")
        d = PrusaProjectSession(donor)
        d.stage_setting("perimeters", "5")
        d.save()
        target = prusa_newjob.unique_target(self.sandbox, self.stl)
        notes = prusa_newjob.create_project(str(self.exe), donor,
                                            [self.stl], target)
        self.assertIn("donor.3mf", notes[0])
        self.assertEqual(PrusaProjectSession(target).settings["perimeters"],
                         "5")

    def test_a_missing_model_is_refused(self):
        with self.assertRaises(ProjectError):
            prusa_newjob.create_project(
                str(self.exe), prusa_newjob.find_donor([], PRINTER),
                [self.sandbox / "nope.stl"], self.sandbox / "x.3mf")

    def test_a_build_past_the_limit_says_so(self):
        # A 23-million-triangle STL took PrusaSlicer about five minutes
        # (2026-09-25) and was stopped with words about the slicer path.
        self.assertGreaterEqual(prusa_newjob.EXPORT_TIMEOUT_S, 60 * 60)
        self.addCleanup(setattr, prusa_newjob, "EXPORT_TIMEOUT_S",
                        prusa_newjob.EXPORT_TIMEOUT_S)
        prusa_newjob.EXPORT_TIMEOUT_S = 1
        prusa_newjob.build_export_command = \
            lambda exe, models, out, centre: \
            [sys.executable, "-c", "import time; time.sleep(30)"]
        with self.assertRaises(ProjectError) as caught:
            prusa_newjob.create_project(
                str(self.exe), prusa_newjob.find_donor([], PRINTER),
                [self.stl], self.sandbox / "x.3mf")
        self.assertIn("still building", str(caught.exception))
        self.assertNotIn("slicer path", str(caught.exception))


# ----------------------------------------------------------- backend


class BackendTests(SandboxCase):

    def setUp(self):
        super().setUp()
        self.backend = backends.get_backend("prusa")

    def test_identity_and_parts(self):
        b = self.backend
        self.assertEqual(b.display_name, "PrusaSlicer")
        self.assertTrue(b.supported)
        self.assertIs(b.catalog, prusa_catalog)
        self.assertIs(b.engine, prusa_engine)
        self.assertIs(b.newjob, prusa_newjob)
        self.assertIsNone(b.printer_switch)
        self.assertEqual(b.lan.__name__, "prusalink")  # since 2026-09-07
        self.assertIn("slice", b.texts.SHORTCUTS)
        self.assertIn("prusa", backends.supported_ids())
        self.assertIn(("prusa", "PrusaSlicer", True), config_store.SLICERS)

    def test_preset_lists_follow_the_printer_and_its_nozzle(self):
        lists = self.backend.preset_lists(str(self.exe), PRINTER)
        self.assertEqual(lists["process"],
                         ["0.20mm SPEED @COREONE HF0.4",
                          "0.20mm QUALITY @COREONE HF0.4", "My draft"])
        self.assertEqual(lists["filament"],
                         ["Prusament PLA @COREONE HF0.4", "Generic PLA @COREONE",
                          "Prusament PETG @COREONE HF0.4"])
        self.assertEqual(lists["machine"],
                         ["Prusa CORE One HF0.4 nozzle",
                          "Prusa CORE One 0.4 nozzle"])

    def test_every_curated_key_exists_in_a_real_core_one_project(self):
        s = self.backend.open_project(COREONE)
        missing = []
        for _tab, _name, entries in prusa_catalog.TABS:
            for key, *_rest in entries:
                if key not in s.settings:
                    missing.append(key)
        for key, *_rest in prusa_catalog.FILAMENTS:
            if key not in s.settings or not isinstance(s.settings[key], list):
                missing.append(key)
        for key in prusa_catalog.OBJECT_CURATED_KEYS:
            if key != "extruder" and key not in s.settings:
                missing.append(key)
        self.assertEqual(missing, [])
        for key in prusa_catalog.KNOWN_BOOL_KEYS:
            if key in s.settings:
                self.assertIn(str(s.settings[key]), ("0", "1"), key)

    def test_preflight_reads_prusa_keys(self):
        project = self.copy(COREONE, "pf.3mf")
        s = self.backend.open_project(project, slicer_exe=str(self.exe))
        # The Core One's default profile prints with supports on.
        self.assertEqual(s.settings["support_material"], "1")
        self.assertEqual(self.backend.preflight.warnings(s), [])
        s.stage_setting("support_material", "0")
        found = self.backend.preflight.warnings(s)
        self.assertEqual([w["check"] for w in found], ["supports"])
        self.assertIn("less than 35 degrees from horizontal", found[0]["text"])
        s.stage_setting("support_material", "1")
        s.stage_setting("complete_objects", "1")
        s.stage_setting("max_print_height", "60")
        self.assertEqual([w["check"] for w in self.backend.preflight.warnings(s)],
                         ["too_tall"])
        self.assertEqual(preflight.BY_OBJECT, "by object")


# ---------------------------------------------------------------- HTTP


class HttpFixture:
    httpd = thread = port = sandbox = script = None
    old_config_path = old_appdata = None


H = HttpFixture


class EditorOverHttpTests(unittest.TestCase):
    """Every editor page for a PrusaSlicer project, over real HTTP."""

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        cls.project = projects_dir / "benchy.3mf"
        shutil.copy2(COREONE, cls.project)
        fake_exe = support.make_fake_prusa(H.sandbox)
        H.script = support.make_fake_prusa_engine(H.sandbox)
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({"slicer": "prusa", "slicer_exe": str(fake_exe),
                           "projects_dir": str(projects_dir)})
        H.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(H.sandbox / "AppData")
        prusa_profiles._bundle_cache.clear()
        prusa_profiles._scope_cache.clear()
        server.state["session"] = None
        H.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        H.port = H.httpd.server_address[1]
        H.thread = threading.Thread(target=H.httpd.serve_forever,
                                    daemon=True)
        H.thread.start()

    @classmethod
    def tearDownClass(cls):
        if H.httpd:
            H.httpd.shutdown()
            H.httpd.server_close()
        server.state["session"] = None
        server.state["watcher"].stop()
        config_store.CONFIG_PATH = H.old_config_path
        if H.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = H.old_appdata
        shutil.rmtree(H.sandbox, ignore_errors=True)

    def request(self, method, url, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=60)
        try:
            body = urllib.parse.urlencode(form, doseq=True) if form else None
            headers = {}
            if body is not None:
                headers = {"Content-Type": "application/x-www-form-urlencoded",
                           "Content-Length": str(len(body))}
            conn.request(method, url, body, headers)
            resp = conn.getresponse()
            return (resp.status, resp.read().decode("utf-8"),
                    resp.getheader("Location") or "")
        finally:
            conn.close()

    def test_every_page_renders_and_a_change_reaches_the_file(self):
        status, _b, _l = self.request("POST", "/open",
                                      {"path": str(self.project)})
        self.assertEqual(status, 303)
        pages = ["/project/quality", "/project/strength", "/project/support",
                 "/project/speed", "/project/adhesion", "/project/others",
                 "/project/filaments", "/project/presets",
                 "/project/placement", "/project/all", "/project/all?q=fan",
                 "/project/review", "/project/backups", "/project/send",
                 "/project/object/1", "/project/setting/perimeters",
                 "/project/setting/temperature", "/limits", "/"]
        for url in pages:
            status, body, _l = self.request("GET", url)
            self.assertEqual(status, 200, url)
            self.assertNotIn("Traceback", body, url)
            self.assertNotIn("unexpected error", body.lower(), url)
        status, body, _l = self.request("GET", "/project/strength")
        self.assertIn("Infill density", body)
        self.assertIn('name="fill_density"', body)
        self.assertNotIn("sparse_infill_density", body)
        status, body, _l = self.request("GET", "/project/filaments")
        self.assertIn("Nozzle temperature", body)
        self.assertNotIn("nozzle_temperature", body)
        status, body, _l = self.request("GET", "/project/send")
        self.assertIn("Warnings before slicing", body)
        self.assertIn("Nothing to warn about", body)
        self.assertIn("Ctrl+R", body)
        # A curated edit travels page -> staging -> file.
        status, _b, loc = self.request("POST", "/project/strength", {
            "__present": ["fill_density", "thick_bridges"],
            "fill_density": "25%", "base__fill_density": "15%"})
        self.assertEqual(status, 303, loc)
        session = server.state["session"]
        self.assertEqual(session.effective_setting("fill_density"), "25%")
        session.save()
        self.assertEqual(PrusaProjectSession(self.project).settings[
            "fill_density"], "25%")

    def test_slice_now_reaches_a_report(self):
        self.request("POST", "/open", {"path": str(self.project)})
        original = prusa_engine.build_command
        script = H.script
        prusa_engine.build_command = \
            lambda exe, project, plate, out: \
            [sys.executable, str(script), out, "success"]
        try:
            status, _b, loc = self.request("POST", "/project/slice", {})
            self.assertEqual(status, 303, loc)
            job = server.state["slice_job"]
            job.wait(60)
            self.assertEqual(job.status, "done", job.report)
            status, body, _l = self.request("GET", "/project/slice-report")
            self.assertEqual(status, 200)
            self.assertIn("1 hour", body)
            self.assertIn("27", body)
            self.assertIn("benchy.bgcode", body)
        finally:
            prusa_engine.build_command = original


# ------------------------------------------------ real PrusaSlicer


@unittest.skipUnless(support.real_prusa_exe(), "PrusaSlicer not installed")
class RealPrusaIntegrationTests(unittest.TestCase):
    """Against the real PrusaSlicer and its real library (skipped
    without them). Builds a Core One project from the presets and
    slices it - about half a minute."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        prusa_profiles._bundle_cache.clear()
        prusa_profiles._scope_cache.clear()

    def test_build_and_slice_for_real(self):
        exe = str(support.real_prusa_exe())
        if PRINTER not in prusa_profiles.list_presets(exe, "machine"):
            self.skipTest("the Core One is not installed in PrusaSlicer")
        stl = self.sandbox / "blind-benchy.stl"
        s = PrusaProjectSession(COREONE)
        support.write_stl(*s.object_shape("1"), stl)
        donor = prusa_newjob.find_donor([], PRINTER)
        target = prusa_newjob.unique_target(self.sandbox, stl)
        prusa_newjob.create_project(exe, donor, [stl], target)
        built = PrusaProjectSession(target, slicer_exe=exe)
        self.assertEqual(built.settings["printer_settings_id"], PRINTER)
        self.assertEqual(built.objects()[0]["name"], "blind-benchy.stl")
        job = prusa_engine.SliceJob(exe, target)
        job.wait(600)
        self.assertEqual(job.status, "done", job.report)
        self.assertGreater(job.report["total_time_s"], 1800)
        self.assertGreater(job.report["total_grams"], 10)
        self.assertEqual(job.report["printer_model"], "COREONE")


if __name__ == "__main__":
    unittest.main()
