"""The motion guard: what keeps the nozzle off the plate.

Written after the 2026-09-15 plate damage on the K2 Plus (a "Z down
10" from the 8 mm rest drove the bed 2 mm into the nozzle, and the
next home dragged it across the plate). Checks:

- the floor: a jog or a typed line that would end below Z 0 is
  refused in words, with the largest step still allowed; one that
  ends at 0 or above passes,
- sideways clearance: an X or Y move with the nozzle lower than
  1 mm is refused; higher, or right after a home, passes,
- the lift before a home: a home (or a park, a bed mesh, a probe)
  from a nozzle lower than 3 mm gets a lift to 5 mm first; from
  higher none; with no readable height it is refused unless the
  hand check is ticked; a home after a low Z move in the same
  block is refused,
- nothing around the limits: G92 with an axis, M211,
  SET_KINEMATIC_POSITION and the like are not passed on,
- go-to: up first, sideways after, when the head is low,
- both builders: the job mode prepends the lift lines and says so,
  the direct mode prepends a lift step that must be seen to happen,
  refuses moves while the printer prints, and a direct home from the
  fake K2 at a low rest lifts, homes and reports; from a stale
  power-up position the home is NOT sent unless the hand check
  stands in.
"""

import time
import unittest

import support
import creality_lan
import motion_guard as mg
import printer_control as pc
from errors import ProjectError


def _fields(**values):
    return lambda name: str(values.get(name, "")).strip()


AT = {"x": 175.5, "y": 175.8}


def at(z):
    return dict(AT, z=z)


class FloorTests(unittest.TestCase):

    def test_a_jog_below_the_plate_is_refused_with_the_room_left(self):
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G91", "G1 Z-10 F600", "G90"], at(8),
                     what="Move Z 10 mm down")
        text = str(ctx.exception)
        self.assertTrue(text.startswith("Nothing was sent."))
        self.assertIn("would take the nozzle to Z -2, below the plate", text)
        self.assertIn("The head is at Z 8; the largest step down from here "
                      "is 8 mm.", text)

    def test_down_to_the_plate_exactly_passes(self):
        plan = mg.check(["G91", "G1 Z-8 F600", "G90"], at(8))
        self.assertEqual(plan.lift, 0)

    def test_an_absolute_z_below_the_plate_is_refused(self):
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G90", "G1 Z-0.5 F600"], at(8))
        self.assertIn("Z -0.5", str(ctx.exception))
        with self.assertRaises(mg.Refused):
            mg.check(["G0 Z-1"], at(8))        # absolute is the default
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G91", "G1 Z-0.1"], at(0))
        self.assertIn("already at the plate", str(ctx.exception))

    def test_a_relative_z_down_without_a_height_is_refused(self):
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G91", "G1 Z-1"], {})
        self.assertIn("cannot read how far the nozzle is from the plate",
                      str(ctx.exception))
        with self.assertRaises(mg.Refused):
            mg.check(["G91", "G1 Z-1"], None)
        # Up is always fine, height or none.
        self.assertEqual(mg.check(["G91", "G1 Z10", "G90"], None).lift, 0)

    def test_lines_that_do_not_move_pass_anywhere(self):
        for lines in (["M104 S40"], ["M83", "G1 E5 F300", "M82"],
                      ["M114", "M115"], ["; a comment", ""]):
            self.assertEqual(mg.check(lines, None).lift, 0)
            self.assertEqual(mg.check(lines, at(0)).lift, 0)


class SidewaysTests(unittest.TestCase):

    def test_sideways_low_is_refused(self):
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G91", "G1 X10 F3000", "G90"], at(0.5))
        self.assertIn("only 0.5 mm from the plate would scrape it",
                      str(ctx.exception))
        self.assertIn("Move Z up to at least 1 mm first", str(ctx.exception))
        with self.assertRaises(mg.Refused):
            mg.check(["G1 Y-20"], at(-2))

    def test_sideways_high_enough_passes(self):
        self.assertEqual(mg.check(["G91", "G1 X10 F3000"], at(1)).lift, 0)
        self.assertEqual(mg.check(["G1 X10 Y20 Z5"], at(0.2)).lift, 0)

    def test_sideways_without_a_height_is_refused(self):
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G1 X10"], {})
        self.assertIn("will not move sideways", str(ctx.exception))

    def test_sideways_after_a_home_in_the_same_block_passes(self):
        self.assertEqual(mg.check(["G28", "G1 X10 Y10 F3000"], at(8)).lift,
                         0)
        # ...but a relative Z down after it has no height to reason
        # from and is refused.
        with self.assertRaises(mg.Refused):
            mg.check(["G28", "G91", "G1 Z-1"], at(8))

    def test_a_z_down_in_the_same_line_counts(self):
        with self.assertRaises(mg.Refused):
            mg.check(["G1 X10 Z0.2"], at(8))


class LiftTests(unittest.TestCase):

    def test_a_home_from_a_low_nozzle_gets_a_lift(self):
        self.assertEqual(mg.check(["G28"], at(8)).lift, 0)
        self.assertEqual(mg.check(["G28"], at(3)).lift, 0)
        self.assertAlmostEqual(mg.check(["G28"], at(2)).lift, 3)
        self.assertAlmostEqual(mg.check(["G28 X Y"], at(-2)).lift, 7)
        self.assertAlmostEqual(mg.check(["G28 Z"], at(0)).lift, 5)
        self.assertIn("Lifted the nozzle to 5 mm before the home",
                      mg.check(["G28"], at(0)).lift_words)

    def test_travelling_routines_get_the_lift_too(self):
        for line in ("G27", "G29", "G30", "BED_MESH_CALIBRATE",
                     "SHAPER_CALIBRATE", "G162", "G64", "M959"):
            self.assertAlmostEqual(mg.check([line], at(1)).lift, 4, line)
            self.assertEqual(mg.check([line], at(6)).lift, 0, line)

    def test_a_home_without_a_height_needs_the_hand_check(self):
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G28"], {})
        self.assertIn("tick 'The nozzle is clear of the plate'",
                      str(ctx.exception))
        self.assertEqual(mg.check(["G28"], {}, clear=True).lift, 0)
        self.assertEqual(mg.check(["G28"], None, clear=True).lift, 0)

    def test_a_home_after_a_low_z_move_is_refused(self):
        with self.assertRaises(mg.Refused) as ctx:
            mg.check(["G90", "G1 Z0.5", "G28"], at(8))
        self.assertIn("Move Z up to at least 3 mm first", str(ctx.exception))
        # The lift is computed from where the head IS, so a Z move
        # up first and then a home needs none.
        self.assertEqual(mg.check(["G90", "G1 Z10", "G28"], at(1)).lift, 0)

    def test_lift_lines(self):
        self.assertEqual(mg.lift_lines(3), ["G91", "G1 Z3 F600", "G90"])


class BypassTests(unittest.TestCase):

    def test_commands_that_move_the_coordinates_are_refused(self):
        for line in ("G92 Z0", "G92 X0 Y0", "M211 S0",
                     "SET_KINEMATIC_POSITION Z=0", "FORCE_MOVE STEPPER=z",
                     "SET_GCODE_OFFSET Z=-1", "M206 Z-2"):
            with self.assertRaises(mg.Refused) as ctx:
                mg.check([line], at(8))
            self.assertIn("does not pass it on", str(ctx.exception), line)

    def test_g92_on_the_extruder_only_passes(self):
        self.assertEqual(mg.check(["G92 E0"], at(8)).lift, 0)


class GotoTests(unittest.TestCase):

    def test_up_first_when_low(self):
        self.assertEqual(mg.goto_lines(at(0.5), x=10, z=5),
                         ["G90", "G1 Z5 F600", "G1 X10 F3000"])
        self.assertEqual(mg.goto_lines({}, x=10, y=20, z=5),
                         ["G90", "G1 Z5 F600", "G1 X10 Y20 F3000"])

    def test_one_move_when_clear(self):
        self.assertEqual(mg.goto_lines(at(8), x=10, z=5),
                         ["G90", "G1 X10 Z5 F3000"])
        self.assertEqual(mg.goto_lines(at(8), z=50), ["G90", "G1 Z50 F600"])
        self.assertEqual(mg.goto_lines(at(8), x=10, y=20),
                         ["G90", "G1 X10 Y20 F3000"])


class JobBuilderTests(unittest.TestCase):

    def test_a_jog_below_the_plate_is_refused_before_it_is_built(self):
        with self.assertRaises(ProjectError) as ctx:
            pc.build_action("move", _fields(go="Z-", step="10"),
                            position=at(8))
        self.assertIn("Move Z 10 mm down would take the nozzle to Z -2",
                      str(ctx.exception))
        name, lines = pc.build_action("move", _fields(go="Z-", step="5"),
                                      position=at(8))
        self.assertEqual(lines, ["G91", "G1 Z-5 F600", "G90"])

    def test_a_home_from_a_low_nozzle_is_lifted_first_and_said(self):
        name, lines = pc.build_action("home", _fields(axes=""),
                                      position=at(1))
        self.assertEqual(lines, ["G91", "G1 Z4 F600", "G90", "G28"])
        self.assertEqual(list(lines.notes), [mg.Plan().lift_words])
        name, lines = pc.build_action("home", _fields(axes=""),
                                      position=at(8))
        self.assertEqual(lines, ["G28"])
        self.assertEqual(list(lines.notes), [])
        for action in ("park", "mesh-level", "z-align", "z-height",
                       "z-probe"):
            name, lines = pc.build_action(action, _fields(), position=at(0))
            self.assertEqual(lines[:3], ["G91", "G1 Z5 F600", "G90"], action)

    def test_a_home_without_a_height_needs_the_tick(self):
        with self.assertRaises(ProjectError):
            pc.build_action("home", _fields(axes=""), position=None)
        name, lines = pc.build_action("home", _fields(axes=""),
                                      position=None, clear=True)
        self.assertEqual(lines, ["G28"])

    def test_a_goto_goes_up_first_and_typed_gcode_is_checked(self):
        name, lines = pc.build_action("goto", _fields(x="10", z="5"),
                                      position=at(0.2))
        self.assertEqual(lines, ["G90", "G1 Z5 F600", "G1 X10 F3000"])
        with self.assertRaises(ProjectError):
            pc.build_action("gcode", _fields(gcode="G1 X10"), position=at(0))
        with self.assertRaises(ProjectError):
            pc.build_action("gcode", _fields(gcode="G92 Z0"), position=at(8))
        name, lines = pc.build_action("gcode", _fields(gcode="M114"),
                                      position=None)
        self.assertEqual(lines, ["M114"])

    def test_everything_else_builds_without_a_position(self):
        for action, field in (("preheat", _fields(preset="PLA")),
                              ("cooldown", _fields()),
                              ("load", _fields(filament="PLA")),
                              ("extrude", _fields(length="5")),
                              ("move", _fields(go="Z+", step="10")),
                              ("beep", _fields())):
            name, lines = pc.build_action(action, field, position=None)
            self.assertTrue(lines, action)


class DirectBuilderTests(unittest.TestCase):

    def test_a_jog_below_the_plate_is_refused(self):
        with self.assertRaises(ProjectError) as ctx:
            pc.build_direct_action("move", _fields(go="Z-", step="10"),
                                   creality_lan, position=at(8))
        self.assertIn("Z -2, below the plate", str(ctx.exception))
        name, steps = pc.build_direct_action(
            "move", _fields(go="Z-", step="8"), creality_lan, position=at(8))
        self.assertEqual(steps[0]["params"], {"setPosition": "Z-8 F600"})

    def test_a_home_gets_a_lift_step_that_must_move(self):
        name, steps = pc.build_direct_action(
            "home", _fields(axes=""), creality_lan, position=at(-2))
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["params"], {"setPosition": "Z7 F600"})
        self.assertEqual(steps[0]["must_move"], "z")
        self.assertTrue(steps[0]["before"])
        self.assertEqual(steps[1]["params"], {"autohome": "X Y Z"})
        self.assertEqual(list(steps.notes), [mg.Plan().lift_words])
        # With the hand check the lift is still tried, but the home
        # goes on when the printer would not make it.
        name, steps = pc.build_direct_action(
            "home", _fields(axes="", clear="yes"), creality_lan,
            position=at(0), clear=True)
        self.assertIsNone(steps[0]["must_move"])
        name, steps = pc.build_direct_action(
            "home", _fields(axes=""), creality_lan, position=at(8))
        self.assertEqual(len(steps), 1)

    def test_moves_are_refused_while_the_printer_prints(self):
        for action, field in (("move", _fields(go="Z+", step="10")),
                              ("home", _fields(axes="")),
                              ("goto", _fields(z="50")),
                              ("gcode", _fields(gcode="M114"))):
            with self.assertRaises(ProjectError) as ctx:
                pc.build_direct_action(action, field, creality_lan,
                                       position=at(8), phase="printing")
            self.assertIn("The printer is printing", str(ctx.exception))
        # Temperatures still go through mid-print.
        name, steps = pc.build_direct_action(
            "nozzle", _fields(temp="200"), creality_lan, position=at(8),
            phase="printing")
        self.assertTrue(steps)

    def test_a_goto_goes_up_first(self):
        name, steps = pc.build_direct_action(
            "goto", _fields(x="10", z="5"), creality_lan, position=at(0.2))
        self.assertEqual([s["params"] for s in steps],
                         [{"gcodeCmd": "G90"}, {"gcodeCmd": "G1 Z5 F600"},
                          {"gcodeCmd": "G1 X10 F3000"}])


class DirectJobTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fake = support.FakeK2Printer()
        cls.ip = cls.fake.ws_address()

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def setUp(self):
        self.fake.status["state"] = 0
        self.fake.status["deviceState"] = 0
        self.fake.unhomed = False
        self.fake.homes.clear()

    def run_job(self, action, clear=False, **values):
        merged = creality_lan.read_status(self.ip)
        name, steps = pc.build_direct_action(
            action, _fields(**values), creality_lan,
            position=creality_lan.position(merged),
            phase=creality_lan.job_phase(merged), clear=clear)
        return pc.start_direct_job(None, creality_lan, self.ip, name,
                                   steps, poll_seconds=0.05, wait_seconds=2)

    def wait(self, job, timeout=20):
        deadline = time.monotonic() + timeout
        while job.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        return not job.busy

    def test_the_fake_makes_a_move_into_the_plate_like_the_real_one(self):
        # The guard is what stops it, not the printer: proven by
        # sending the raw command the page no longer builds.
        self.fake.status["curPosition"] = "X:175.50 Y:175.80 Z:8.00"
        creality_lan.set_params(self.ip, {"setPosition": "Z-10 F600"})
        self.assertEqual(self.fake.status["curPosition"],
                         "X:175.50 Y:175.80 Z:-2.00")
        creality_lan.set_params(self.ip, {"setPosition": "Z-30 F600"})
        self.assertEqual(self.fake.status["curPosition"],
                         "X:175.50 Y:175.80 Z:-2.00")

    def test_a_home_from_a_pressed_nozzle_lifts_first(self):
        self.fake.status["curPosition"] = "X:175.50 Y:175.80 Z:-2.00"
        job = self.run_job("home", axes="")
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        messages = [e["message"] for e in job.events]
        self.assertIn(mg.Plan().lift_words, messages)
        self.assertIn("Sent: lift the nozzle to 5 mm first (the printer "
                      "homes X and Y before Z).", messages)
        self.assertIn("Head now at X 175.5, Y 175.8, Z 5.", messages)
        self.assertIn("Sent: home X Y Z.", messages)
        self.assertEqual(self.fake.homes, ["X Y Z"])

    def test_a_home_from_a_stale_position_is_not_sent(self):
        # After a power-up Klipper refuses moves and reports a stale
        # position: the lift does not happen, so the home must not.
        self.fake.unhomed = True
        self.fake.status["curPosition"] = "X:0.00 Y:0.00 Z:0.00"
        job = self.run_job("home", axes="")
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "failed", job.events)
        self.assertIn(pc.LIFT_REFUSED, job.events[-1]["message"])
        self.assertEqual(self.fake.homes, [])
        # With the hand check ticked the home goes on after the
        # attempt, and says the printer did not lift.
        job = self.run_job("home", clear=True, axes="")
        self.assertTrue(self.wait(job))
        self.assertEqual(job.result, "finished", job.events)
        messages = " ".join(e["message"] for e in job.events)
        self.assertIn("The head did not move", messages)
        self.assertEqual(self.fake.homes, ["X Y Z"])

    def test_a_jog_into_the_plate_never_reaches_the_printer(self):
        self.fake.status["curPosition"] = "X:175.50 Y:175.80 Z:8.00"
        before = len(self.fake.commands)
        with self.assertRaises(ProjectError):
            self.run_job("move", go="Z-", step="10")
        # Only the status read went out.
        sets = [c for c in self.fake.commands[before:]
                if c.get("method") == "set"]
        self.assertEqual(sets, [])
        self.assertEqual(self.fake.status["curPosition"],
                         "X:175.50 Y:175.80 Z:8.00")


if __name__ == "__main__":
    unittest.main()
