"""build_job.py - a new-job build running in the background.

Building a project from a model file used to run inside the page
request: the Start page just loaded, saying nothing, for as long as
the engine took - eight minutes and more for a 23-million-triangle
model (Skyward Dragon, 2026-09-25). A BuildJob runs the backend's
create_project in a thread instead, so the building page can speak a
heartbeat and offer Cancel, like the slicing page.

The builders report where they are with stage() and start their
engine through run(), a stand-in for subprocess.run that Cancel can
kill. Both do nothing special outside a job, so the builders work
unchanged when called directly (tests, the replace-model paths).
"""

import subprocess
import threading
import time
from pathlib import Path

import noconsole
from engine_driver import friendly_duration
from errors import ProjectError

_current = threading.local()


class BuildCancelled(ProjectError):
    """The user pressed Cancel while the engine was building."""


def stage(text):
    """Name what the running build is doing now (spoken on the page)."""
    job = getattr(_current, "job", None)
    if job is not None:
        job.stage = text


def run(cmd, *, timeout=None, capture_output=False, text=False, **kw):
    """subprocess.run for a build's engine: windowless, and killed by
    the job's Cancel (raising BuildCancelled)."""
    job = getattr(_current, "job", None)
    if capture_output:
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    with subprocess.Popen(cmd, text=text, **kw,
                          **noconsole.hidden()) as proc:
        if job is not None:
            job._attach(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        finally:
            if job is not None:
                job._attach(None)
    if job is not None and job.cancelled:
        raise BuildCancelled("Cancelled while the engine was building.")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


class BuildJob:
    """One create_project call in a background thread.

    status is running, done, failed or cancelled. notes (done) are the
    builder's spoken notes; error (failed) is the ProjectError text.
    """

    def __init__(self, build, target, model_name):
        self.target = Path(target)
        self.model_name = model_name
        self.status = "running"
        self.stage = "Starting the slicer's engine."
        self.notes = []
        self.error = ""
        self.cancelled = False
        self._build = build
        self._process = None
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._elapsed_final = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def elapsed(self):
        if self._elapsed_final is not None:
            return self._elapsed_final
        return int(time.monotonic() - self._t0)

    def describe(self):
        """One plain-language line for the live region."""
        if self.status == "running":
            return (f"{self.stage} {friendly_duration(self.elapsed())} "
                    "so far.")
        if self.status == "done":
            return (f"Built {self.target.name} in "
                    f"{friendly_duration(self.elapsed())}.")
        if self.status == "cancelled":
            return "Building was cancelled."
        return f"Building failed: {self.error}"

    def cancel(self):
        """Stop the engine; nothing the build made is kept."""
        with self._lock:
            self.cancelled = True
            proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def wait(self, timeout=None):
        self._thread.join(timeout)

    def _attach(self, proc):
        with self._lock:
            self._process = proc
            cancelled = self.cancelled
        if proc is not None and cancelled:
            proc.kill()

    def _run(self):
        _current.job = self
        try:
            notes = self._build()
            if self.cancelled:
                # Cancel came after the engine, while the file was
                # being checked: the user said no, so it is not kept.
                self.target.unlink(missing_ok=True)
                self._finish("cancelled")
            else:
                self.notes = list(notes or [])
                self._finish("done")
        except BuildCancelled:
            self.target.unlink(missing_ok=True)
            self._finish("cancelled")
        except ProjectError as e:
            self.error = str(e)
            self._finish("cancelled" if self.cancelled else "failed")
        except Exception as e:  # a bug must still end the page's wait
            self.error = f"An unexpected error stopped the build ({e})."
            self._finish("failed")
        finally:
            _current.job = None

    def _finish(self, status):
        self._elapsed_final = int(time.monotonic() - self._t0)
        self.status = status
