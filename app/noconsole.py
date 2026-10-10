"""noconsole.py - keep helper programs' windows off the screen.

AccessSlice is built without a console (packaging/AccessSlice.spec,
console=False). On Windows a program without a console that starts a
console-subsystem helper - prusa-slicer-console.exe, tasklist,
powershell - gets a black terminal window opened for the helper,
in front of everything, with the helper's output scrolling in it.
Edis saw it on 2026-09-24 ("when I run prusa slicer ... I can see
the terminal running. I don't want to see that"). A screen reader
may even move to it. The Bambu and Orca engines are GUI-subsystem
programs and never showed one, which is why it went unnoticed.

Every helper launch passes hidden() so the helper runs without a
window; its output still goes wherever the call sends it (the CLI
log file, a pipe, nowhere). The two deliberate launches of programs
the user is meant to see - the slicer itself and File Explorer -
do not use it.
"""

import subprocess
import sys


def hidden():
    """Popen keyword arguments that keep a console helper windowless.

    Empty off Windows, where no such window exists.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
