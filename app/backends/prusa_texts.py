"""backends.prusa_texts - what the UI says about PrusaSlicer.

Part 5 of the backend interface for PrusaSlicer. The shortcuts are
PrusaSlicer's documented defaults (File menu: Import STL/3MF/STEP/OBJ
Ctrl+I, Save Project Ctrl+S, Save Project as Ctrl+Alt+S; Slice now
Ctrl+R; Export G-code Ctrl+G; Send G-code to print host Ctrl+Shift+G).
None of them has been judged under NVDA on this machine yet, and
PrusaSlicer's own GUI is a wxWidgets application whose main window
is partly readable - that verdict is Edis's (manual Part 4f).
"""

SHORTCUTS = {
    "import": "Ctrl+I",
    "save_project_as": "Ctrl+Alt+S",
    "slice": "Ctrl+R",
    "send": "Ctrl+Shift+G",
}

# PrusaSlicer opens a sliced .bgcode/.gcode in its G-code VIEWER, a
# separate program that can look but never send (Edis hit this dead
# end on his first real Prusa run, 2026-09-07). So the slice report
# hands PrusaSlicer the PROJECT to slice and send, and offers the
# viewer only as "look".
SLICED_OPENS_VIEWER = True

EXE_EXAMPLE = "C:\\Program Files\\Prusa3D\\PrusaSlicer\\prusa-slicer.exe"


def slice_and_send_html(name):
    return (f"{name} opens with this project. Press <kbd>Ctrl+R</kbd> "
            "to slice, then <kbd>Ctrl+G</kbd> to export the G-code to "
            "a USB drive, or <kbd>Ctrl+Shift+G</kbd> to send it to the "
            "Core One over PrusaLink if a physical printer is set up "
            "there.")


def send_sliced_html(name):
    return (f"{name} can only open a sliced file in its G-code viewer, "
            "which cannot send. So this opens the project itself: "
            "press <kbd>Ctrl+R</kbd> to slice, then <kbd>Ctrl+Shift+G"
            "</kbd> to send it to the Core One over PrusaLink (that "
            f"needs a physical printer set up in {name}: Printer "
            "Settings, the icon beside the printer list) or "
            "<kbd>Ctrl+G</kbd> to export the G-code to a drive.")


def handoff_sliced_html(name):
    return (f"{name}'s viewer shows the sliced file but cannot send "
            "it. To print, go back to the slice report: send straight "
            "to the Core One over PrusaLink, or copy the file to a USB "
            "drive.")


def usb_copy_html(name):
    return ("The Core One reads .bgcode files from a USB drive: plug "
            "the drive into the printer, choose the file on its "
            "screen, and start. The file is complete as it is.")


def handoff_project_html(name):
    return ("Press <kbd>Ctrl+R</kbd> to slice, then <kbd>Ctrl+G</kbd> "
            "to export the G-code or <kbd>Ctrl+Shift+G</kbd> to send it "
            "to the printer.")


def second_window_html(name):
    return (f"{name} was already open, so this may have opened the "
            "project in that window or asked whether to replace the "
            "current one. If two windows exist, close the older one "
            "(<kbd>Alt+F4</kbd>) so an out-of-date version never gets "
            "sliced by mistake.")


def limits_body_html(name):
    return f"""
<p>AccessSlice works on the text inside a saved project file. A few
things in a {name} project are not stored as text, so no text
tool - AccessSlice included - can edit them. This page lists them
honestly, each with its practical workaround.</p>

<h2>Painted-on supports, seams and colours</h2>
<p>Painting tools store their strokes as per-triangle data on the
mesh itself. There is no text to edit. Workarounds: the per-object
settings page and the Support tab cover almost everything support
painting is used for, and the seam position setting (Quality tab)
chooses nearest, aligned, rear or random without painting.</p>

<h2>Several copies of one object</h2>
<p>{name} can print an object several times from one mesh
(instances). AccessSlice reads and moves the first copy; the others
keep their saved places. Turn extra copies into separate objects in
{name} if each needs its own placement here.</p>

<h2>Variable layer height</h2>
<p>A layer-height profile painted along the object's height is stored
as numbers on the object, not as a setting. The plain layer height
setting (Quality tab) still applies everywhere else.</p>

<h2>Shapes themselves</h2>
<p>AccessSlice moves, rotates, scales and arranges objects, but it
does not sculpt or repair meshes. Model fixing belongs in the design
tool that made the model.</p>
"""
