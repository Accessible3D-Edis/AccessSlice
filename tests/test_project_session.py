"""Tests for ProjectSession - staging, dialogs, and the save pipeline.

These cover every regression Edis found by hand in the 2026-08 build
rounds, most importantly the different_settings_to_system bookkeeping
(a changed key not registered in the right slot is silently discarded
by Bambu Studio) and the staged-preset display overlay ("my profile
choice did nothing").

Ground truth for the committed H2C test project (probed 2026-08-27):
printer "Bambu Lab H2C 0.4 nozzle", 4 filaments, nozzle_temperature has
12 values (blocks of 3 per filament), one object id "2" at (165, 160)
printing with filament 2, plate 330 x 320 mm, filament map mode
"Auto For Flush" with map 1 2 1 1, sparse infill 15% grid, wall_loops 2,
and enable_support already registered in the process diff slot.
"""

import json
import os
import shutil
import unittest
import zipfile

import support
import edit_bambu_3mf as engine
from slicer_project import ProjectError, ProjectSession


class SessionCase(unittest.TestCase):
    """Base: fresh sandbox with a project copy and the fake library."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)
        self.fake_exe = support.make_fake_studio(self.sandbox)

    def session(self):
        return ProjectSession(self.project, slicer_exe=str(self.fake_exe))


class OpenAndFactsTests(SessionCase):

    def test_open_reads_the_projects_facts(self):
        s = self.session()
        self.assertEqual(s.settings["printer_settings_id"],
                         "Bambu Lab H2C 0.4 nozzle")
        self.assertEqual(s.filament_count(), 4)
        objs = s.objects()
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["id"], "2")
        self.assertEqual(objs[0]["name"], "blind-Benchy.stl")
        self.assertAlmostEqual(objs[0]["x"], 165.0)
        self.assertAlmostEqual(objs[0]["y"], 160.0)
        self.assertEqual(objs[0]["extruder"], "2")
        self.assertEqual(s.plate_size(), (330.0, 320.0))
        mode, fmap = s.filament_map()
        self.assertEqual(mode, "Auto For Flush")
        self.assertEqual(fmap, ["1", "2", "1", "1"])

    def test_summary_mentions_the_essentials(self):
        text = " ".join(self.session().summary())
        self.assertIn("blind-Benchy.stl", text)
        self.assertIn("Bambu Lab H2C 0.4 nozzle", text)

    def test_missing_file_is_a_friendly_error(self):
        with self.assertRaises(ProjectError):
            ProjectSession(self.sandbox / "does-not-exist.3mf")

    def test_non_zip_file_is_a_friendly_error(self):
        bad = self.sandbox / "not-a-zip.3mf"
        bad.write_text("hello", encoding="utf-8")
        with self.assertRaises(ProjectError):
            ProjectSession(bad)

    def test_zip_without_bambu_entries_says_save_from_studio(self):
        bad = self.sandbox / "foreign.3mf"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("3D/3dmodel.model", "<model/>")
        with self.assertRaises(ProjectError) as ctx:
            ProjectSession(bad)
        self.assertIn("Ctrl+Shift+S", str(ctx.exception))


class StagingTests(SessionCase):

    def test_stage_scalar_setting_and_effective_overlay(self):
        s = self.session()
        self.assertTrue(s.stage_setting("wall_loops", "3"))
        self.assertEqual(s.effective_setting("wall_loops"), "3")
        self.assertEqual(s.settings["wall_loops"], "2",
                         "staging must not touch the loaded state")
        # Re-staging the original value clears the pending change.
        self.assertFalse(s.stage_setting("wall_loops", "2"))
        self.assertEqual(s.pending, [])

    def test_stage_unknown_key_is_refused(self):
        with self.assertRaises(ProjectError):
            self.session().stage_setting("no_such_key_exists", "1")

    def test_stage_list_with_wrong_length_is_refused(self):
        s = self.session()
        with self.assertRaises(ProjectError):
            s.stage_setting("nozzle_temperature", ["220", "220"])

    def test_stage_move_and_same_position_noop(self):
        s = self.session()
        self.assertFalse(s.stage_move("2", 165.0, 160.0))
        self.assertTrue(s.stage_move("2", 100.0, 120.0))
        with self.assertRaises(ProjectError):
            s.stage_move("99", 10, 10)

    def test_stage_extruder_and_same_value_noop(self):
        s = self.session()
        self.assertFalse(s.stage_extruder("2", "2"))
        self.assertTrue(s.stage_extruder("2", "3"))

    def test_effective_objects_show_the_staged_filament(self):
        # The chooser must show the slot just chosen, not the file's
        # (Edis, 2026-09-21: PLA kept showing after he chose PETG).
        s = self.session()
        self.assertEqual(s.effective_objects()[0]["extruder"], "2")
        s.stage_extruder("2", "4")
        self.assertEqual(s.objects()[0]["extruder"], "2")      # the file
        self.assertEqual(s.effective_objects()[0]["extruder"], "4")
        self.assertEqual(s.effective_extruder("2", "2"), "4")
        # Back to the file's slot: the staged change is gone again.
        self.assertFalse(s.stage_extruder("2", "2"))
        self.assertEqual(s.effective_objects()[0]["extruder"], "2")
        # Never mutates what objects() reports.
        self.assertEqual(s.objects()[0]["extruder"], "2")

    def test_filament_names_show_a_staged_setting(self):
        s = self.session()
        before = s.filament_names()
        self.assertEqual(before[3], "Bambu PLA Matte @BBL H2C")
        ids = list(s.settings["filament_settings_id"])
        ids[3] = "Bambu PETG HF @BBL H2C"
        self.assertTrue(s.stage_setting("filament_settings_id", ids))
        self.assertEqual(s.filament_names()[3], "Bambu PETG HF @BBL H2C")
        self.assertEqual(s.settings["filament_settings_id"][3],
                         "Bambu PLA Matte @BBL H2C")

    def test_stage_filament_map_forces_manual_mode(self):
        s = self.session()
        # Same values as saved, but mode is "Auto For Flush", so staging
        # must still count as a change (Manual is the only respected mode).
        self.assertTrue(s.stage_filament_map(["1", "2", "1", "1"]))
        with self.assertRaises(ProjectError):
            s.stage_filament_map(["1", "3", "1", "1"])

    def test_discard_pending_by_index(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        s.stage_setting("sparse_infill_density", "20%")
        s.discard_pending(0)
        self.assertEqual(len(s.pending), 1)
        self.assertEqual(s.pending[0]["key"], "sparse_infill_density")


class StudioDialogTests(SessionCase):
    """The 100%-infill and mm-or-% questions, exactly as Studio asks."""

    def test_infill_conflict_appears_and_clears(self):
        s = self.session()
        self.assertIsNone(s.infill_conflict())      # 15% grid is fine
        s.stage_setting("sparse_infill_density", "100%")
        conflict = s.infill_conflict()
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["pattern"], "grid")
        self.assertTrue(s.advice())
        s.stage_setting("sparse_infill_pattern", "zig-zag")
        self.assertIsNone(s.infill_conflict())
        self.assertEqual(s.advice(), [])

    def test_infill_conflict_threshold_is_exactly_100(self):
        s = self.session()
        s.stage_setting("sparse_infill_density", "99%")
        self.assertIsNone(s.infill_conflict())

    def test_default_infill_density_comes_from_the_profile(self):
        s = self.session()
        # The fake library's Test Strength profile carries 25%.
        s.stage_preset("process", "Test Strength @BBL H2C")
        self.assertEqual(s.default_infill_density(), "25%")

    def test_unit_conflict_and_answer(self):
        s = self.session()
        s.stage_setting("max_travel_detour_distance", "10000")
        conflict = s.unit_conflict()
        self.assertEqual(conflict["key"], "max_travel_detour_distance")
        new = s.answer_unit("max_travel_detour_distance", as_percent=True)
        self.assertEqual(new, "10000%")
        self.assertIsNone(s.unit_conflict(), "answered - never re-asked")

    def test_unit_conflict_not_raised_for_small_or_percent_values(self):
        s = self.session()
        s.stage_setting("max_travel_detour_distance", "50")
        self.assertIsNone(s.unit_conflict())
        s.stage_setting("max_travel_detour_distance", "300%")
        self.assertIsNone(s.unit_conflict())


class PresetTests(SessionCase):

    def test_staged_preset_overlays_its_values(self):
        # The display-bug regression of 2026-08-26: after picking a
        # profile the tabs must show ITS numbers immediately.
        s = self.session()
        self.assertTrue(s.stage_preset("process", "Test Strength @BBL H2C"))
        self.assertEqual(s.staged_preset("process"),
                         "Test Strength @BBL H2C")
        self.assertEqual(s.effective_setting("wall_loops"), "6")
        self.assertEqual(s.settings["wall_loops"], "2")

    def test_unknown_preset_name_is_refused_at_staging_time(self):
        with self.assertRaises(ProjectError):
            self.session().stage_preset("process", "No Such Profile")

    def test_preset_save_updates_id_and_clears_diff_slot(self):
        s = self.session()
        s.stage_preset("process", "Test Strength @BBL H2C")
        s.save()
        fresh = ProjectSession(self.project)
        self.assertEqual(fresh.settings["print_settings_id"],
                         "Test Strength @BBL H2C")
        diff = fresh.settings["different_settings_to_system"]
        self.assertEqual(diff[0], "",
                         "switching preset must clear the process slot "
                         "(the project now matches the system preset)")


class SavePipelineTests(SessionCase):

    def test_full_save_applies_verifies_and_registers_diffs(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        # Edit filament 2's nozzle-temperature block (index 1, k=3).
        temps = list(s.settings["nozzle_temperature"])
        temps[3:6] = ["243", "243", "243"]
        s.stage_setting("nozzle_temperature", temps)
        s.stage_move("2", 100.0, 120.0)
        s.stage_extruder("2", "3")
        s.stage_filament_map(["2", "2", "1", "1"])
        lines = s.save()
        self.assertIn("Saved and verified", lines[0])

        backups = list((self.project.parent / "backups").glob(
            "* - before save *.3mf"))
        self.assertEqual(len(backups), 1,
                         "the save must keep the previous version in "
                         "the backups folder")

        fresh = ProjectSession(self.project)
        self.assertEqual(fresh.settings["wall_loops"], "3")
        self.assertEqual(fresh.settings["nozzle_temperature"][3:6],
                         ["243", "243", "243"])
        obj = fresh.objects()[0]
        self.assertAlmostEqual(obj["x"], 100.0)
        self.assertAlmostEqual(obj["y"], 120.0)
        self.assertEqual(obj["extruder"], "3")
        mode, fmap = fresh.filament_map()
        self.assertEqual(mode, "Manual",
                         "staging a map must always set Manual mode")
        self.assertEqual(fmap, ["2", "2", "1", "1"])

        # The bookkeeping Studio trusts: slot 0 process, 1..4 filaments,
        # 5 machine. wall_loops joins enable_support in the process
        # slot; the temperature edit lands ONLY in filament 2's slot.
        diff = fresh.settings["different_settings_to_system"]
        self.assertEqual(len(diff), 6)
        self.assertEqual(set(diff[0].split(";")),
                         {"enable_support", "wall_loops"})
        self.assertEqual(diff[1], "")
        self.assertEqual(diff[2], "nozzle_temperature")
        self.assertEqual(diff[3], "")
        self.assertEqual(diff[4], "")

        self.assertEqual(s.pending, [], "pending clears after save")
        self.assertEqual(s.settings["wall_loops"], "3",
                         "session re-reads the saved truth")

    def test_save_with_nothing_staged_says_so(self):
        self.assertEqual(self.session().save(), ["No changes to save."])

    def test_failed_verification_restores_the_original_file(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        before = self.project.read_bytes()
        real_verify = engine.verify
        engine.verify = lambda *a, **k: ["simulated corruption"]
        try:
            with self.assertRaises(ProjectError) as ctx:
                s.save()
        finally:
            engine.verify = real_verify
        self.assertIn("restored", str(ctx.exception))
        self.assertEqual(self.project.read_bytes(), before,
                         "the project on disk must be byte-identical "
                         "after a failed save")

    def test_second_save_of_same_session_works(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        s.save()
        s.stage_setting("wall_loops", "4")
        s.save()
        self.assertEqual(ProjectSession(self.project).settings["wall_loops"],
                         "4")


class BackupTests(SessionCase):
    """Timestamped backups: retention, ordering, and restore."""

    def test_each_save_keeps_the_version_it_replaced(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        s.save()
        s.stage_setting("wall_loops", "4")
        s.save()
        backups = s.list_backups()
        self.assertEqual(len(backups), 2,
                         "two saves in the same second must yield two "
                         "distinct backups, newest first")
        self.assertEqual(
            ProjectSession(backups[0]["path"]).settings["wall_loops"],
            "3", "the newest backup holds the version the second save "
                 "replaced")
        self.assertEqual(
            ProjectSession(backups[1]["path"]).settings["wall_loops"],
            "2", "the oldest backup holds the original version")

    def test_only_the_newest_backups_are_kept(self):
        s = self.session()
        folder = s.backup_dir()
        folder.mkdir()
        for i in range(12):
            (folder / (f"{self.project.stem} - before save "
                       f"2026-01-01 00.00.{i:02d}.3mf")).write_bytes(b"x")
        s.stage_setting("wall_loops", "3")
        s.save()
        backups = s.list_backups()
        self.assertEqual(len(backups), ProjectSession.BACKUP_KEEP)
        names = " ".join(b["name"] for b in backups)
        self.assertNotIn("00.00.00", names, "the oldest must be pruned")
        self.assertNotIn("00.00.01", names)
        self.assertNotIn("00.00.02", names)

    def test_restore_swaps_back_and_is_itself_undoable(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        s.save()
        before_save = s.list_backups()[0]["name"]
        s.stage_setting("wall_loops", "9")   # staged, must be dropped
        lines = s.restore_backup(before_save)
        self.assertEqual(s.settings["wall_loops"], "2",
                         "the file and session show the restored version")
        self.assertEqual(s.pending, [])
        self.assertIn("discarded", " ".join(lines))
        undo = [b for b in s.list_backups()
                if " - before restore " in b["name"]]
        self.assertEqual(len(undo), 1,
                         "the replaced version must be backed up too")
        self.assertEqual(
            ProjectSession(undo[0]["path"]).settings["wall_loops"], "3",
            "the undo backup holds the version the restore replaced")

    def test_restore_refuses_bad_names(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        s.save()
        for bad in (
            "..\\..\\evil.3mf",
            "backups/inner.3mf",
            "other-project - before save 2026-01-01 00.00.00.3mf",
            f"{self.project.stem} - before save 2099-01-01 00.00.00.3mf",
        ):
            with self.assertRaises(ProjectError, msg=bad):
                s.restore_backup(bad)

    def test_no_stray_pre_edit_backup_beside_the_project(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        s.save()
        old_style = self.project.with_name(
            self.project.stem + ".pre-edit-backup.3mf")
        self.assertFalse(old_style.exists(),
                         "the session must use the backups folder, not "
                         "the CLI tool's single-file scheme")


class StaleGuardTests(SessionCase):
    """The file changed on disk behind the session's back (the classic
    case: the project is also open in Bambu Studio and Studio saved).
    Nothing may overwrite that newer version."""

    def rewrite_outside(self):
        """Simulate another program saving the file: wall_loops -> 7."""
        entries = engine.load_entries(self.project)
        settings = json.loads(entries[engine.PROJECT_SETTINGS_ENTRY])
        settings["wall_loops"] = "7"
        entries[engine.PROJECT_SETTINGS_ENTRY] = json.dumps(
            settings).encode("utf-8")
        engine.save_entries(self.project, entries)

    def test_fresh_sessions_and_own_saves_are_not_stale(self):
        s = self.session()
        self.assertFalse(s.is_stale())
        s.stage_setting("wall_loops", "3")
        s.save()
        self.assertFalse(s.is_stale(),
                         "our own save must not read as an outside change")

    def test_an_outside_change_is_detected(self):
        s = self.session()
        self.rewrite_outside()
        self.assertTrue(s.is_stale())

    def test_saving_refuses_while_stale_and_preserves_the_newer_file(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        self.rewrite_outside()
        with self.assertRaises(ProjectError) as ctx:
            s.save()
        self.assertIn("changed on disk", str(ctx.exception))
        self.assertIn("Reload", str(ctx.exception))
        self.assertEqual(ProjectSession(self.project).settings["wall_loops"],
                         "7", "the outside save must survive untouched")

    def test_restoring_refuses_while_stale(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        s.save()
        name = s.list_backups()[0]["name"]
        self.rewrite_outside()
        with self.assertRaises(ProjectError):
            s.restore_backup(name)
        self.assertEqual(ProjectSession(self.project).settings["wall_loops"],
                         "7")

    def test_reload_picks_up_the_disk_truth_and_drops_staged_edits(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        self.rewrite_outside()
        lines = s.reload()
        self.assertFalse(s.is_stale())
        self.assertEqual(s.settings["wall_loops"], "7")
        self.assertEqual(s.pending, [])
        text = " ".join(lines)
        self.assertIn("Reloaded", text)
        self.assertIn("discarded", text)
        self.assertIn("wall_loops", text,
                      "the dropped change must be named")

    def test_a_deleted_file_reads_as_stale(self):
        s = self.session()
        self.project.unlink()
        self.assertTrue(s.is_stale())


class StructureCheckTests(SessionCase):
    """The open-time sanity check: honest notes, never a crash."""

    def mutate_project(self, entry_name, old, new):
        """Rewrite one text entry of the project copy on disk."""
        entries = engine.load_entries(self.project)
        text = entries[entry_name].decode("utf-8")
        self.assertIn(old, text, "test fixture drifted from the file")
        entries[entry_name] = text.replace(old, new).encode("utf-8")
        engine.save_entries(self.project, entries)

    def test_the_clean_test_project_has_no_notes(self):
        s = self.session()
        self.assertEqual(s.structure_notes, [])
        self.assertIn("BambuStudio", s.application)

    def test_extra_plates_go_to_the_summary_not_the_warnings(self):
        support.add_empty_plate(self.project, "2")
        s = self.session()
        self.assertIn("2 build plates", " ".join(s.summary()))
        self.assertEqual([n for n in s.structure_notes if "plate" in n],
                         [], "a readable multi-plate layout is normal "
                             "now, not a warning")

    def test_an_unreadable_transform_is_announced_not_crashed_on(self):
        self.mutate_project(
            engine.MODEL_ENTRY,
            'transform="1 0 0 0 1 0 0 0 1 165 160 38.4231987"',
            'transform="1 0 0"')
        s = self.session()
        self.assertEqual(s.objects(), [],
                         "a malformed transform must be skipped, not "
                         "crash the object list")
        notes = " ".join(s.structure_notes)
        self.assertIn("could not be read", notes)

    def test_a_dual_nozzle_project_without_a_map_is_announced(self):
        self.mutate_project(
            engine.MODEL_SETTINGS_ENTRY,
            '<metadata key="filament_maps" value="1 2 1 1"/>',
            "")
        notes = " ".join(self.session().structure_notes)
        self.assertIn("no filament-to-nozzle map", notes)

    def test_an_unverified_studio_version_is_a_caution(self):
        self.mutate_project(
            engine.MODEL_ENTRY,
            '<metadata name="Application">BambuStudio-02.08.02.61'
            "</metadata>",
            '<metadata name="Application">BambuStudio-03.00.00.01'
            "</metadata>")
        s = self.session()
        notes = " ".join(s.structure_notes)
        self.assertIn("not been verified", notes)
        self.assertIn("03.00", notes)
        # A caution, not a refusal: the project still opens and edits.
        self.assertTrue(s.stage_setting("wall_loops", "3"))

    def test_a_missing_preset_library_is_announced(self):
        bare_exe = self.sandbox / "BareStudio" / "bambu-studio.exe"
        bare_exe.parent.mkdir()
        bare_exe.write_bytes(b"")
        s = ProjectSession(self.project, slicer_exe=str(bare_exe))
        notes = " ".join(s.structure_notes)
        self.assertIn("preset library was not found", notes)

    def test_missing_diff_bookkeeping_is_announced(self):
        import json as _json
        entries = engine.load_entries(self.project)
        settings = _json.loads(entries[engine.PROJECT_SETTINGS_ENTRY])
        del settings["different_settings_to_system"]
        entries[engine.PROJECT_SETTINGS_ENTRY] = _json.dumps(
            settings).encode("utf-8")
        engine.save_entries(self.project, entries)
        notes = " ".join(self.session().structure_notes)
        self.assertIn("bookkeeping", notes)


class WriteFailureTests(SessionCase):
    """A save that cannot write must leave the project provably intact."""

    def test_locked_file_write_failure_restores_and_explains(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        before = self.project.read_bytes()
        real_save = engine.save_entries

        def failing_save(path, entries):
            # Simulate the file being locked (open in Bambu Studio):
            # the write dies AFTER the backup was taken.
            raise PermissionError(13, "The process cannot access the file")

        engine.save_entries = failing_save
        try:
            with self.assertRaises(ProjectError) as ctx:
                s.save()
        finally:
            engine.save_entries = real_save
        message = str(ctx.exception)
        self.assertIn("could not be written", message)
        self.assertIn("Bambu Studio", message)
        self.assertEqual(self.project.read_bytes(), before,
                         "the project must be restored byte-identical")


class ExtraProjectSmokeTest(unittest.TestCase):
    """Optional: the same open-stage-save smoke run on a second project.

    Point the environment variable ACCESSSLICE_EXTRA_PROJECT at another
    saved Bambu Studio project (e.g. an X1C one) to cover a second
    printer's structure. Skipped when unset. The file is copied first -
    the original is never touched.
    """

    def test_open_stage_save_round_trip(self):
        extra = os.environ.get("ACCESSSLICE_EXTRA_PROJECT")
        if not extra:
            self.skipTest("ACCESSSLICE_EXTRA_PROJECT not set")
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        copy = sandbox / "extra-project.3mf"
        shutil.copy2(extra, copy)
        s = ProjectSession(copy)
        self.assertTrue(s.objects(), "project has no objects")
        self.assertGreater(s.filament_count(), 0)
        key = "wall_loops" if "wall_loops" in s.settings else None
        if key:
            old = s.settings[key]
            s.stage_setting(key, str(int(float(old)) + 1))
            lines = s.save()
            self.assertIn("Saved and verified", lines[0])


class FlushingPersistenceTests(SessionCase):
    """The flushing matrix is an ordinary list setting to the save
    pipeline; this pins that one edited cell survives save and reopen
    with the list's length and every other cell intact."""

    def test_a_matrix_cell_edit_survives_save_and_reopen(self):
        s = self.session()
        matrix = list(s.settings["flush_volumes_matrix"])
        original_len = len(matrix)
        matrix[1] = "444"       # nozzle 1, from filament 1 to filament 2
        self.assertTrue(s.stage_setting("flush_volumes_matrix", matrix))
        s.save()
        again = ProjectSession(self.project)
        saved = again.settings["flush_volumes_matrix"]
        self.assertEqual(len(saved), original_len)
        self.assertEqual(saved[1], "444")
        self.assertEqual(saved[0], "0", "the diagonal must stay 0")
        self.assertEqual(saved[2], s.settings["flush_volumes_matrix"][2])


if __name__ == "__main__":
    unittest.main()
