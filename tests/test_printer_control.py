"""Printer control: command jobs over PrusaLink (merged 2026-09-12
from the Prusa Accessible Control program).

Covers, against the simulated Core One (support.FakeCoreOnePrinter):

- the G-code builders, line for line - the presets, the cooldown,
  the filament jobs ending with the nozzle off (the firmware's 170
  degree standby), moves, homing, the fan, the purge, typed G-code,
- the form-to-job table (build_action): every action builds, bad
  numbers and missing choices are refused in words, the factory
  reset wants its confirmation,
- the job runner: a beep runs and its file is removed, a preheat
  sets the targets, a second job is refused while one runs, a load
  stops for the knob and finishes after it with the nozzle off, a
  file without the model check triggers the compatibility prompt,
  a printing printer refuses commands in words, a timed-out job
  leaves its file while the printer is still active, a move changes
  the position, and
- the PrusaLink calls the runner uses: info, folder listing, delete,
  pause, resume, stop, and continue refused like the real firmware.
"""

import time
import unittest

import support
import printer_control as pc
import prusalink
from errors import ProjectError


class BuilderTests(unittest.TestCase):

    def test_preheat_preset(self):
        self.assertEqual(pc.preheat_preset("PLA"), ["M104 S215", "M140 S60"])
        self.assertEqual(pc.preheat_preset("PETG", nozzle_only=True),
                         ["M104 S230"])

    def test_cooldown(self):
        self.assertEqual(pc.cooldown(),
                         ["M104 S0", "M140 S0", "M141 S0", "M107"])

    def test_load_with_temp_skips_the_preheat_dialog(self):
        self.assertEqual(pc.load_filament(215, "PLA"),
                         ["M109 S215", 'M701 S"PLA" W255', "M104 S0"])

    def test_load_without_temp(self):
        self.assertEqual(pc.load_filament(), ["M701", "M104 S0"])

    def test_load_can_keep_the_nozzle_hot(self):
        self.assertEqual(pc.load_filament(215, "PLA", cool_after=False),
                         ["M109 S215", 'M701 S"PLA" W255'])

    def test_unload(self):
        self.assertEqual(pc.unload_filament(230),
                         ["M109 S230", "M702 W255", "M104 S0"])
        self.assertEqual(pc.unload_filament(230, ask_if_successful=True),
                         ["M109 S230", "M702 W255 I", "M104 S0"])

    def test_change_cools_only_at_the_end(self):
        self.assertEqual(pc.change_filament(215, "PLA"),
                         ["M109 S215", "M702 W255", "M109 S215",
                          'M701 S"PLA" W255', "M104 S0"])

    def test_load_no_prompts_ends_cold(self):
        self.assertEqual(pc.load_filament_no_prompts(215)[-1], "M104 S0")

    def test_move_relative(self):
        self.assertEqual(pc.move_relative("X", 10),
                         ["G91", "G1 X10 F3000", "G90"])
        self.assertEqual(pc.move_relative("z", -0.1),
                         ["G91", "G1 Z-0.1 F600", "G90"])
        self.assertEqual(pc.move_relative("E", 5),
                         ["M83", "G1 E5 F300", "M82"])

    def test_move_absolute(self):
        self.assertEqual(pc.move_absolute(x=10, y=20),
                         ["G90", "G1 X10 Y20 F3000"])
        self.assertEqual(pc.move_absolute(z=50), ["G90", "G1 Z50 F600"])

    def test_home(self):
        self.assertEqual(pc.home(), ["G28"])
        self.assertEqual(pc.home("xy"), ["G28 X Y"])
        self.assertEqual(pc.home("Z"), ["G28 Z"])

    def test_fan(self):
        self.assertEqual(pc.set_print_fan(0), ["M107"])
        self.assertEqual(pc.set_print_fan(100), ["M106 S255"])
        self.assertEqual(pc.set_print_fan(50), ["M106 S128"])

    def test_purge(self):
        self.assertEqual(pc.purge_filament(215, 30),
                         ["M109 S215", "M83", "G1 E30 F300", "M82",
                          "M104 S0"])

    def test_beep_lights_and_settings(self):
        self.assertEqual(pc.beep(), ["M300 S1000 P300"])
        self.assertEqual(pc.chamber_lights(255, 255, 255),
                         ["M151 R255 G255 B255"])
        self.assertEqual(pc.save_settings(), ["M500"])
        self.assertEqual(pc.set_input_shaper("x", 45.5, 0.1, 2),
                         ["M593 X F45.5 D0.1 T2"])

    def test_custom_strips_blank_lines(self):
        self.assertEqual(pc.custom("G28\n\n  M114 \n"), ["G28", "M114"])

    def test_number_formatting(self):
        self.assertEqual(pc._t(10.0), "10")
        self.assertEqual(pc._t(0.1), "0.1")
        self.assertEqual(pc._t(-2.5), "-2.5")

    def test_job_text_has_the_header_and_the_name_on_screen(self):
        text = pc.job_text("Beep", pc.beep())
        self.assertTrue(text.startswith("; AccessSlice command job\n"))
        self.assertIn('M862.3 P "COREONE"', text)
        self.assertIn("M117 Beep\n", text)
        self.assertTrue(text.endswith("M300 S1000 P300\n"))


def _fields(**values):
    return lambda name: str(values.get(name, "")).strip()


# Where the head is for the builder checks: the nozzle 8 mm clear of
# the plate (motion_guard refuses moves toward the plate and sideways
# moves without a readable height).
AT = {"x": 0.0, "y": 0.0, "z": 8.0}


class ActionTableTests(unittest.TestCase):

    def test_every_button_action_builds(self):
        cases = {
            "preheat": _fields(preset="PLA"),
            "preheat-custom": _fields(nozzle="200", bed="50"),
            "cooldown": _fields(),
            "nozzle": _fields(temp="210"),
            "bed": _fields(temp="60"),
            "chamber": _fields(temp="40"),
            "fan": _fields(percent="50"),
            "load": _fields(filament="PETG"),
            "unload": _fields(temp="230"),
            "change": _fields(filament="PLA"),
            "purge": _fields(temp="215", length="30"),
            "autoload": _fields(),
            "load-no-prompts": _fields(temp="215"),
            "stuck-on": _fields(), "stuck-off": _fields(),
            "move": _fields(axis="X", direction="plus", step="10"),
            "extrude": _fields(length="5"),
            "retract": _fields(length="5"),
            "goto": _fields(x="10", z="50"),
            "home": _fields(axes="XY"),
            "park": _fields(), "motors-off": _fields(),
            "motors-on": _fields(), "quick-stop": _fields(),
            "mesh-level": _fields(), "z-align": _fields(),
            "z-height": _fields(), "z-probe": _fields(),
            "shaper": _fields(axis="Y", frequency="40"),
            "beep": _fields(), "lights-on": _fields(),
            "lights-off": _fields(), "save-settings": _fields(),
            "reload-settings": _fields(),
            "factory-settings": _fields(confirm="yes"),
            "gcode": _fields(gcode="M114\nM115"),
            "snippet": _fields(snippet="Beep test"),
        }
        for action, field in cases.items():
            name, lines = pc.build_action(action, field, position=AT)
            self.assertTrue(name, action)
            self.assertTrue(lines, action)
            for line in lines:
                self.assertIsInstance(line, str)

    def test_a_preset_load_takes_the_presets_temperature(self):
        name, lines = pc.build_action("load", _fields(filament="PETG"))
        self.assertEqual(lines, ["M109 S230", 'M701 S"PETG" W255',
                                 "M104 S0"])
        self.assertIn("PETG", name)

    def test_a_move_names_its_direction(self):
        name, lines = pc.build_action(
            "move", _fields(axis="Z", direction="minus", step="0.1"),
            position=AT)
        self.assertEqual(name, "Move Z 0.1 mm down")
        self.assertEqual(lines, ["G91", "G1 Z-0.1 F600", "G90"])

    def test_bad_numbers_are_refused_in_words(self):
        with self.assertRaises(ProjectError) as ctx:
            pc.build_action("nozzle", _fields(temp="abc"))
        self.assertIn("must be a number", str(ctx.exception))
        with self.assertRaises(ProjectError) as ctx:
            pc.build_action("nozzle", _fields(temp="400"))
        self.assertIn("between 0 and 290", str(ctx.exception))
        with self.assertRaises(ProjectError) as ctx:
            pc.build_action("nozzle", _fields())
        self.assertIn("Type the nozzle temperature", str(ctx.exception))
        with self.assertRaises(ProjectError):
            pc.build_action("goto", _fields())
        with self.assertRaises(ProjectError):
            pc.build_action("move", _fields(axis="Q"))

    def test_a_comma_decimal_is_read(self):
        _name, lines = pc.build_action(
            "move", _fields(axis="X", direction="plus", step="0,5"),
            position=AT)
        self.assertEqual(lines[1], "G1 X0.5 F3000")

    def test_the_factory_reset_wants_its_confirmation(self):
        with self.assertRaises(ProjectError) as ctx:
            pc.build_action("factory-settings", _fields())
        self.assertIn("Nothing was sent", str(ctx.exception))

    def test_unknown_actions_and_empty_gcode_are_refused(self):
        with self.assertRaises(ProjectError):
            pc.build_action("explode", _fields())
        with self.assertRaises(ProjectError):
            pc.build_action("gcode", _fields(gcode="\n\n"))
        with self.assertRaises(ProjectError):
            pc.build_action("snippet", _fields(snippet="nope"))


class RunnerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fake = support.FakeCoreOnePrinter(speed=40.0)
        cls.ip = cls.fake.address()
        cls.key = support.FAKE_PRUSALINK_KEY

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    @property
    def printer(self):
        return self.fake.printer

    def run_job(self, name, lines, current=None, **options):
        options.setdefault("poll_seconds", 0.05)
        return pc.start_job(current, prusalink, self.ip, self.key, name,
                            lines, **options)

    def wait(self, job, timeout=30):
        deadline = time.monotonic() + timeout
        while job.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        return not job.busy

    def wait_for_kind(self, job, kind, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(e["kind"] == kind for e in job.events):
                return True
            time.sleep(0.02)
        return False

    def setUp(self):
        self.printer.wait_for_state("IDLE", "FINISHED", "STOPPED",
                                    timeout=10)

    def test_a_beep_runs_and_its_file_is_removed(self):
        before = self.printer.beeps
        job = self.run_job("Beep", pc.beep())
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        self.assertEqual(self.printer.beeps, before + 1)
        self.assertNotIn("usb/accessslice/beep.gcode", self.printer.files)
        kinds = [e["kind"] for e in job.events]
        self.assertEqual(kinds[:2], ["started", "uploaded"])
        self.assertIn("cleaned", kinds)
        self.assertEqual(self.printer.screen_message, "Beep")
        snap = job.snapshot()
        self.assertTrue(snap["done"])
        self.assertEqual(snap["result"], "finished")
        self.assertIn("Beep: done.", [e["message"] for e in snap["events"]])

    def test_a_preheat_sets_the_targets(self):
        job = self.run_job("Preheat PLA", pc.preheat_preset("PLA"))
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        self.assertEqual(self.printer.target["nozzle"], 215)
        self.assertEqual(self.printer.target["bed"], 60)
        cool = self.run_job("Cooldown", pc.cooldown(), current=job)
        self.assertTrue(self.wait(cool))
        self.assertEqual(self.printer.target["nozzle"], 0)

    def test_a_second_job_is_refused_while_one_runs(self):
        job = self.run_job("Home all axes", pc.home())
        with self.assertRaises(ProjectError) as ctx:
            self.run_job("Beep", pc.beep(), current=job)
        self.assertIn("still running", str(ctx.exception))
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)

    def test_a_load_waits_for_the_knob_and_ends_cold(self):
        job = self.run_job("Load filament", pc.load_filament(215, "PLA"))
        self.assertTrue(self.wait_for_kind(job, "attention"), job.events)
        self.assertEqual(self.printer.state, "ATTENTION")
        said = next(e["message"] for e in job.events
                    if e["kind"] == "attention")
        self.assertIn("press the knob", said.lower())
        self.assertTrue(job.snapshot()["attention"])
        # PrusaLink has no continue call: the fake answers 400 too.
        with self.assertRaises(ProjectError):
            prusalink._call(self.ip, self.key, "PUT",
                            f"/api/v1/job/{job.job_id}/continue", data=b"",
                            headers={"Content-Length": "0"})
        self.printer.press_knob()
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        self.assertTrue(self.printer.filament_loaded)
        # The firmware's 170 standby must have been switched off.
        self.assertEqual(self.printer.target["nozzle"], 0.0)

    def test_a_file_without_the_model_check_triggers_the_prompt(self):
        job = self.run_job("Beep", pc.beep(), header=["; no checks"])
        self.assertTrue(self.wait_for_kind(job, "attention"), job.events)
        self.printer.press_knob()
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)

    def test_a_printing_printer_refuses_commands_in_words(self):
        self.printer.upload("real.gcode",
                            b'M862.3 P "COREONE"\n' + b"G4 P1\n" * 400)
        self.assertIsNone(self.printer.start("real.gcode"))
        try:
            job = self.run_job("Beep", pc.beep())
            self.assertTrue(self.wait(job))
            self.assertEqual(job.result, "failed", job.events)
            self.assertIn("printing", job.events[-1]["message"])
            self.assertIn("idle", job.events[-1]["message"])
            # The direct job calls work on the running print.
            job_id = prusalink.read_status(self.ip, self.key)["job_id"]
            prusalink.pause_job(self.ip, job_id, self.key)
            self.assertTrue(self.printer.wait_for_state("PAUSED"))
            self.assertIn("paused", prusalink.status_lines(
                prusalink.read_status(self.ip, self.key))[0])
            prusalink.resume_job(self.ip, job_id, self.key)
            self.assertTrue(self.printer.wait_for_state("PRINTING"))
            with self.assertRaises(prusalink.Busy):
                prusalink.resume_job(self.ip, job_id, self.key)
        finally:
            job_id = self.printer.job["id"]
            prusalink.stop_job(self.ip, job_id, self.key)
            self.assertTrue(self.printer.wait_for_state("STOPPED"))
            self.printer.delete("real.gcode")

    def test_a_timed_out_job_leaves_its_file_while_active(self):
        job = self.run_job("Load filament", pc.load_filament(215, "PLA"),
                           timeout_seconds=1)
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "failed", job.events)
        self.assertIn("gave up waiting", job.events[-2]["message"]
                      + job.events[-1]["message"])
        self.assertIn("usb/accessslice/load_filament.gcode",
                      self.printer.files)
        self.printer.press_knob()
        self.assertTrue(self.printer.wait_for_state("FINISHED"))
        self.printer.delete("accessslice/load_filament.gcode")

    def test_a_move_changes_the_position(self):
        job = self.run_job("Move X", pc.move_relative("X", 10))
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        self.assertEqual(self.printer.axis["x"], 10.0)
        lines = prusalink.status_lines(
            prusalink.read_status(self.ip, self.key))
        self.assertIn("Head at X 10, Y 0, Z 5.", lines)

    def test_the_file_calls(self):
        info = prusalink.read_info(self.ip, self.key)
        self.assertEqual(info["name"], "Simulated CORE One")
        prusalink.create_folder(self.ip, "keep", self.key)
        prusalink.create_folder(self.ip, "keep", self.key)  # twice is fine
        prusalink.upload_gcode(self.ip, "keep/part.gcode", b"G28\n",
                               key=self.key)
        entries = prusalink.list_files(self.ip, "", self.key)
        names = [e["display_name"] for e in entries]
        self.assertIn("keep", names)
        self.assertEqual(entries[0]["type"], "FOLDER")   # folders first
        inside = prusalink.list_files(self.ip, "keep", self.key)
        self.assertEqual([e["display_name"] for e in inside],
                         ["part.gcode"])
        prusalink.delete_file(self.ip, "keep/part.gcode", self.key)
        self.assertEqual(prusalink.list_files(self.ip, "keep", self.key),
                         [])
        with self.assertRaises(prusalink.NotFound):
            prusalink.delete_file(self.ip, "keep/part.gcode", self.key)
        with self.assertRaises(prusalink.NotFound):
            prusalink.list_files(self.ip, "nowhere", self.key)
        self.assertIsNone(prusalink.read_job(self.ip, self.key))


if __name__ == "__main__":
    unittest.main()
