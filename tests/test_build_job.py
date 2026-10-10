"""Tests for the background new-job build (app/build_job.py).

The Skyward Dragon (23 million triangles) kept the Start page loading
for eight silent minutes (2026-09-25). A BuildJob runs the build in a
thread so a page can speak its stage and time, and Cancel must really
stop the engine and keep nothing.
"""

import shutil
import subprocess
import sys
import time
import unittest

import support
import build_job
from errors import ProjectError

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


class RunOutsideAJobTests(unittest.TestCase):

    def test_run_behaves_like_subprocess_run(self):
        done = build_job.run([sys.executable, "-c", "print('hi')"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "hi")

    def test_run_still_times_out(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            build_job.run(SLEEPER, timeout=1)

    def test_stage_outside_a_job_is_harmless(self):
        build_job.stage("Nothing is listening.")


class BuildJobTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.target = self.sandbox / "model.3mf"

    def wait_for(self, job, stage):
        for _ in range(100):
            if job.stage == stage:
                return
            time.sleep(0.05)
        self.fail(f"the build never reached: {stage}")

    def test_a_build_reports_its_stage_and_notes(self):
        def build():
            build_job.stage("Reading the model.")
            build_job.run([sys.executable, "-c", "pass"], timeout=30)
            self.target.write_bytes(b"built")
            return ["Created model.3mf."]

        job = build_job.BuildJob(build, self.target, "model.stl")
        job.wait(30)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.notes, ["Created model.3mf."])
        self.assertIn("Built model.3mf in", job.describe())
        self.assertTrue(self.target.exists())

    def test_the_running_line_names_the_stage_and_the_time(self):
        def build():
            build_job.stage("PrusaSlicer is reading the model.")
            build_job.run(SLEEPER, timeout=60)

        job = build_job.BuildJob(build, self.target, "model.stl")
        self.addCleanup(job.wait, 10)
        self.addCleanup(job.cancel)
        self.wait_for(job, "PrusaSlicer is reading the model.")
        line = job.describe()
        self.assertTrue(line.startswith("PrusaSlicer is reading the model."))
        self.assertIn("so far", line)

    def test_cancel_stops_the_engine_and_keeps_nothing(self):
        def build():
            self.target.write_bytes(b"half")  # as if partly written
            build_job.stage("Building.")
            build_job.run(SLEEPER, timeout=60)
            return ["never reached"]

        job = build_job.BuildJob(build, self.target, "model.stl")
        self.wait_for(job, "Building.")
        started = time.monotonic()
        job.cancel()
        job.wait(10)
        self.assertLess(time.monotonic() - started, 10,
                        "the engine was not stopped")
        self.assertEqual(job.status, "cancelled")
        self.assertFalse(self.target.exists())

    def test_cancel_after_the_engine_still_keeps_nothing(self):
        # Cancel pressed while the built file is being checked.
        def build():
            self.target.write_bytes(b"built")
            build_job.stage("Checking the built project.")
            time.sleep(0.5)
            return ["Created model.3mf."]

        job = build_job.BuildJob(build, self.target, "model.stl")
        self.wait_for(job, "Checking the built project.")
        job.cancel()
        job.wait(10)
        self.assertEqual(job.status, "cancelled")
        self.assertFalse(self.target.exists())

    def test_a_project_error_is_the_failure_text(self):
        def build():
            raise ProjectError("The engine said no.")

        job = build_job.BuildJob(build, self.target, "model.stl")
        job.wait(10)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "The engine said no.")
        self.assertEqual(job.describe(),
                         "Building failed: The engine said no.")

    def test_a_bug_still_ends_the_wait(self):
        def build():
            raise KeyError("oops")

        job = build_job.BuildJob(build, self.target, "model.stl")
        job.wait(10)
        self.assertEqual(job.status, "failed")
        self.assertIn("unexpected error", job.error)


if __name__ == "__main__":
    unittest.main()
