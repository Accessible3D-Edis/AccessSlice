"""Tests for engine_driver - the headless slicing driver (Phase II).

The fake engine (tests/support.py) mimics the real CLI contract probed
2026-08-31: result.json in the working directory is the truth (written
on success AND failure), the printable file appears only on success,
and the process exit code is meaningless. parse_result is additionally
tested against the REAL result.json specimens committed in testdata.

One integration test runs the REAL Bambu Studio CLI when it is
installed (skipped otherwise) - it pins the contract the fakes are
built on, so a Studio update that changes the CLI is caught by a test,
not a user.
"""

import json
import shutil
import sys
import time
import unittest

import support
import engine_driver


def patched_command(script, behaviour):
    """A build_command stand-in that runs the fake engine instead."""
    def build(exe, project_path, plate, out_name):
        return [sys.executable, str(script), out_name, behaviour]
    return build


class DriverTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.project = support.copy_test_project(self.sandbox)
        self.script = support.make_fake_engine(self.sandbox)
        self._orig_build = engine_driver.build_command
        self._orig_timeout = engine_driver.TIMEOUT_S

    def tearDown(self):
        engine_driver.build_command = self._orig_build
        engine_driver.TIMEOUT_S = self._orig_timeout
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def _job(self, behaviour, **kw):
        engine_driver.build_command = patched_command(self.script,
                                                      behaviour)
        return engine_driver.SliceJob("fake.exe", self.project, **kw)

    def test_success_run(self):
        job = self._job("success")
        job.wait(30)
        self.assertEqual(job.status, "done")
        r = job.report
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["total_grams"], 37.87, places=2)
        self.assertAlmostEqual(r["total_time_s"], 10502.27, places=1)
        out = job.output_file()
        self.assertTrue(out and out.is_file())
        self.assertEqual(out.parent.parent.name, "sliced")
        self.assertTrue((job.out_dir / engine_driver.RESULT_NAME)
                        .is_file())
        self.assertTrue((job.out_dir / engine_driver.CLI_LOG_NAME)
                        .is_file())

    def test_failure_quotes_the_engines_own_message(self):
        job = self._job("fail")
        job.wait(30)
        self.assertEqual(job.status, "failed")
        self.assertFalse(job.report["ok"])
        self.assertEqual(job.report["return_code"], -50)
        self.assertIn("boundary of plate", job.report["error_string"])
        self.assertIsNone(job.output_file())

    def test_ending_without_result_json_is_a_failure(self):
        job = self._job("silent")
        job.wait(30)
        self.assertEqual(job.status, "failed")
        self.assertIn("without writing its result file",
                      job.report["problem"])

    def test_cancel_kills_the_engine(self):
        job = self._job("hang")
        time.sleep(0.5)
        job.cancel()
        job.wait(30)
        self.assertEqual(job.status, "cancelled")
        self.assertIn("cancelled", job.describe().lower())

    def test_timeout_gives_up_honestly(self):
        engine_driver.TIMEOUT_S = 2
        job = self._job("hang")
        job.wait(60)
        self.assertEqual(job.status, "timeout")
        self.assertIn("stopped", job.report["problem"])

    def test_unrunnable_engine_is_a_friendly_failure(self):
        # Unpatched build_command with an "exe" that is a plain text
        # file: Windows refuses to run it, and the driver must answer
        # in words, never with a traceback.
        fake_exe = self.sandbox / "not-an-engine.exe"
        fake_exe.write_bytes(b"")
        job = engine_driver.SliceJob(fake_exe, self.project)
        job.wait(30)
        self.assertEqual(job.status, "failed")
        self.assertIn("could not be started", job.report["problem"])

    def test_on_finish_callback_fires(self):
        seen = []
        engine_driver.build_command = patched_command(self.script,
                                                      "success")
        job = engine_driver.SliceJob("fake.exe", self.project,
                                     on_finish=seen.append)
        job.wait(30)
        self.assertEqual(seen, [job])

    def test_prune_keeps_only_the_newest_runs(self):
        sliced = self.project.parent / "sliced"
        for day in range(10, 23):
            (sliced / f"2026-08-{day} run").mkdir(parents=True)
        engine_driver.prune_runs(self.project, keep=10)
        left = sorted(d.name for d in sliced.iterdir())
        self.assertEqual(len(left), 10)
        self.assertNotIn("2026-08-10 run", left)
        self.assertIn("2026-08-22 run", left)


class ParseResultTests(unittest.TestCase):
    """parse_result against the REAL result.json specimens captured
    from Bambu Studio 2.8.2 on 2026-08-31 (committed in testdata)."""

    def _load(self, name):
        raw = json.loads((support.REPO_ROOT / "testdata" / name)
                         .read_text(encoding="utf-8"))
        return engine_driver.parse_result(raw)

    def test_the_real_success_specimen(self):
        r = self._load("bambu-slice-result-success.json")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["plates"]), 1)
        plate = r["plates"][0]
        self.assertAlmostEqual(plate["time_s"], 10502.27, places=1)
        self.assertEqual(len(plate["filaments"]), 1)
        self.assertEqual(plate["filaments"][0]["slot"], 2)
        self.assertAlmostEqual(plate["filaments"][0]["grams"], 37.87,
                               places=2)
        # Features come sorted biggest first; Travel tops this one.
        self.assertEqual(plate["features"][0][0], "Travel")
        self.assertEqual(plate["objects"][0]["name"], "blind-Benchy.stl")
        self.assertEqual(r["warnings"], [])

    def test_the_real_failure_specimen(self):
        r = self._load("bambu-slice-result-failure.json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["return_code"], -2)
        self.assertIn("Invalid parameters", r["error_string"])
        self.assertEqual(r["plates"], [])


class FriendlyDurationTests(unittest.TestCase):

    def test_words_never_clock_faces(self):
        fd = engine_driver.friendly_duration
        self.assertEqual(fd(1), "1 second")
        self.assertEqual(fd(45), "45 seconds")
        self.assertEqual(fd(90), "2 minutes")
        self.assertEqual(fd(3600), "1 hour")
        self.assertEqual(fd(10502), "2 hours 55 minutes")


@unittest.skipUnless(support.real_studio_exe(),
                     "real Bambu Studio not installed")
class RealEngineIntegrationTests(unittest.TestCase):
    """Slices the committed blind-Benchy project with the REAL CLI
    (about a minute). This is the contract pin: if a Studio update
    changes the CLI's flags or result.json, this fails first."""

    def test_slices_the_committed_benchy_project(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        project = support.copy_test_project(sandbox)
        job = engine_driver.SliceJob(support.real_studio_exe(), project)
        job.wait(600)
        self.assertEqual(job.status, "done",
                         msg=f"report: {job.report}")
        r = job.report
        self.assertGreater(r["total_grams"], 20)
        self.assertGreater(r["total_time_s"], 1000)
        out = job.output_file()
        self.assertTrue(out and out.stat().st_size > 100000)
