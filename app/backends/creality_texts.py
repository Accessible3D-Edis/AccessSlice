"""backends.creality_texts - what the UI says about Creality Print.

Part 5 of the backend interface for Creality Print. Verified against
the real 7.2.2 GUI (Claude drove it hands-on, screenshots, 2026-09-01
- details in docs\\log.md): the File menu lists New Project Ctrl+N,
Open Project Ctrl+O, Save Project Ctrl+S, Save Project as
Ctrl+Shift+S (same save-as key as Bambu Studio); Ctrl+I imports a
model (Creality's quick-start guide). SLICE AND SEND HAVE NO
KEYBOARD SHORTCUTS - they are the two buttons in the window's bottom
right corner ("Slice plate" and "Send print"). An AccessSlice-saved
project opens cleanly, and an AccessSlice-sliced .gcode.3mf opens as
a project with the model on the plate.

Whether "Send print" works on an opened sliced file without a
re-slice is still being verified - until then the send texts keep
the sure USB path beside it.
"""

SHORTCUTS = {
    "import": "Ctrl+I",             # Creality quick-start guide
    "save_project_as": "Ctrl+Shift+S",  # File menu, verified 2026-09-01
    # slice / send deliberately absent: NO shortcuts exist - they are
    # the bottom-right buttons (verified 2026-09-01).
}

EXE_EXAMPLE = ("C:\\Program Files\\Creality\\Creality Print 7.0"
               "\\CrealityPrint.exe")


def slice_and_send_html(name):
    return (f"{name} opens with this project. Slicing and sending "
            "there have NO keyboard shortcuts - they are the two "
            "buttons in the window's bottom right corner, "
            '"Slice plate" and "Send print".')


def send_sliced_html(name):
    return (f"{name} opens the sliced file with the model on the "
            "plate. Sending has no keyboard shortcut - it is the "
            '"Send print" button in the bottom right corner of the '
            "window. If that proves hard to reach, copy the printable "
            "file at the path above to a USB drive instead; it is "
            "complete and printable as it is.")


def handoff_sliced_html(name):
    return ('Use the "Send print" button in the bottom right corner '
            "of its window. If it does not offer to print the file "
            "directly, use the USB path instead - the file is "
            "complete and printable as it is.")


def handoff_project_html(name):
    return ('Slice with the "Slice plate" button and send with the '
            '"Send print" button - both sit in the bottom right '
            "corner of its window; no keyboard shortcuts exist for "
            "them.")


def second_window_html(name):
    return (f"{name} was already open. Whether it reuses the window "
            "or opens a second one has not been observed yet - if two "
            "windows exist, close the older one (<kbd>Alt+F4</kbd>) "
            "so an out-of-date version never gets sliced by mistake.")


def limits_body_html(name):
    return f"""
<p>AccessSlice works on the text inside a saved project file. A few
things in a {name} project are not stored as text, so no text
tool - AccessSlice included - can edit them. This page lists them
honestly, each with its practical workaround.</p>

<h2>Painted-on supports and seams</h2>
<p>Painting tools store their strokes as per-triangle binary data on
the mesh itself. There is no text to edit. Workarounds: the
per-object settings page and the Support tab cover almost everything
support painting is used for, and the seam position setting (Quality
tab) chooses nearest, aligned, back or random without painting.</p>

<h2>Starting a job from a model file</h2>
<p>On Creality Print this does not work yet: the engine's own
command line crashes when handed a raw model file (a bug upstream in
{name}, verified 2026-09-01). Import the model in {name} once and
save the project into the projects folder - everything after that
happens here, including slicing.</p>

<h2>Which plate gets sliced</h2>
<p>A multi-plate project does not record a chosen plate in the file;
the plate is picked at slicing time. AccessSlice states this on the
send page so it is never a surprise.</p>

<h2>Shapes themselves</h2>
<p>AccessSlice moves, rotates, scales and arranges objects, but it
does not sculpt or repair meshes. Model fixing belongs in the design
tool that made the model.</p>
"""
