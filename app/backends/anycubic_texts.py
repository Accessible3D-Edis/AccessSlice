"""backends.anycubic_texts - what the UI says about Anycubic Slicer Next.

An Orca fork with Orca's menus; shortcuts are the family defaults
(Import Ctrl+I, Save project as Ctrl+Shift+S, Slice plate Ctrl+R,
send Ctrl+Shift+G). Documented defaults, not judged under NVDA here -
and Edis owns no Anycubic printer, so this slicer is verified to
slicing only (docs\\multi-slicer-plan.md, Phase 5).
"""

SHORTCUTS = {
    "import": "Ctrl+I",
    "save_project_as": "Ctrl+Shift+S",
    "slice": "Ctrl+R",
    "send": "Ctrl+Shift+G",
}

EXE_EXAMPLE = "C:\\Program Files\\AnycubicSlicerNext\\AnycubicSlicerNext.exe"


def slice_and_send_html(name):
    return (f"{name} opens with this project. Press <kbd>Ctrl+R</kbd> "
            "to slice the plate, then <kbd>Ctrl+Shift+G</kbd> to send "
            "it to an Anycubic printer, or export the G-code from the "
            "File menu for a USB drive.")


def send_sliced_html(name):
    return (f"{name} opens the sliced file with the model on the plate; "
            "press <kbd>Ctrl+Shift+G</kbd> to send it, or copy the "
            "printable file at the path above to a USB drive - it is "
            "complete and printable as it is.")


def handoff_sliced_html(name):
    return ("Press <kbd>Ctrl+Shift+G</kbd> to send the sliced plate, or "
            "use the USB path - the file is complete and printable as "
            "it is.")


def handoff_project_html(name):
    return ("Press <kbd>Ctrl+R</kbd> to slice and <kbd>Ctrl+Shift+G</kbd> "
            "to send.")


def second_window_html(name):
    return (f"{name} was already open. Whether it reuses the window or "
            "opens a second one has not been observed yet - if two "
            "windows exist, close the older one (<kbd>Alt+F4</kbd>) so "
            "an out-of-date version never gets sliced by mistake.")


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
painting is used for; the seam position setting chooses nearest,
aligned, back or random without painting.</p>

<h2>Not tested on an Anycubic printer yet</h2>
<p>AccessSlice builds, edits and slices {name} projects, and the
sliced file is a complete printable job - but no print has been run
from it yet, because no Anycubic printer has been tested.</p>

<h2>Which plate gets sliced</h2>
<p>A multi-plate project does not record a chosen plate in the file;
the plate is picked at slicing time. AccessSlice states this on the
send page so it is never a surprise.</p>

<h2>Shapes themselves</h2>
<p>AccessSlice moves, rotates, scales and arranges objects, but it
does not sculpt or repair meshes. Model fixing belongs in the design
tool that made the model.</p>
"""
