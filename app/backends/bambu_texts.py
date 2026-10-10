"""backends.bambu_texts - what the UI says about Bambu Studio's GUI.

Part 5 of the backend interface: the keyboard shortcuts and the
sentences that describe THIS slicer's own observed behaviour (the
fresh-project quirk of a sliced file, the second-window behaviour,
the printer preselected in the send dialog - all verified hands-on,
see docs\\test-results.md 2026-08-31). webui composes its pages from
these; a future backend ships its own module with the same names.

Functions return small HTML snippets: fixed sentences with <kbd>
markup, plus the slicer's display name already escaped by the caller.
Nothing user-typed ever flows through here.
"""

# The slicer's own keyboard shortcuts, spoken in the guided flow.
SHORTCUTS = {
    "import": "Ctrl+I",           # Import model file(s)
    "save_project_as": "Ctrl+Shift+S",
    "slice": "Ctrl+R",            # Slice plate
    "send": "Ctrl+Shift+G",       # Send print job
}

# Shown on the setup page as the path example.
EXE_EXAMPLE = "C:\\Program Files\\Bambu Studio\\bambu-studio.exe"


def slice_and_send_html(name):
    """How to slice and send inside the slicer's own window."""
    return (f"{name} opens with this project. There, press "
            f"<kbd>{SHORTCUTS['slice']}</kbd> to slice and "
            f"<kbd>{SHORTCUTS['send']}</kbd> to send it to your "
            "printer.")


def send_sliced_html(name):
    """What happens when the sliced .gcode.3mf is opened to send.

    Verified 2026-08-31: Studio opens it as a fresh project, the send
    key is a silent no-op until the plate is sliced in that window,
    and the send dialog preselects the last-used printer.
    """
    return (f"{name} opens it with everything already set, but treats "
            "it as a fresh project (verified 2026-08-31: the send key "
            "does nothing until the plate is sliced in that window). "
            f"So: press <kbd>{SHORTCUTS['slice']}</kbd> once - it "
            "re-slices quickly, same settings, same result as this "
            f"report - then <kbd>{SHORTCUTS['send']}</kbd> to send. "
            "In the send window, check the printer named at the top: "
            f"{name} suggests the printer used last time, and a wrong "
            "one shows an incompatibility error - pick yours there if "
            "it differs.")


def handoff_sliced_html(name):
    """The handoff page's instructions for a sliced file."""
    return ("It opens the file as a fresh project, so press "
            f"<kbd>{SHORTCUTS['slice']}</kbd> once (a quick re-slice, "
            f"same settings and result), then "
            f"<kbd>{SHORTCUTS['send']}</kbd> to send. In the send "
            "window, check the printer named at the top - "
            f"{name} suggests the one used last time; pick yours if "
            "it differs, or the send shows an incompatibility error.")


def handoff_project_html(name):
    """The handoff page's instructions for an editable project."""
    return (f"Then press <kbd>{SHORTCUTS['slice']}</kbd> to slice "
            f"and <kbd>{SHORTCUTS['send']}</kbd> to send to the "
            "printer.")


def second_window_html(name):
    """A send while the slicer was already open (observed 2026-08-31):
    a SECOND window opens; the old one keeps showing the stale file."""
    return (f"{name} was already open, so this opened a second "
            f"{name} window with the fresh file. The older window "
            "still shows the previous version of the project - once "
            "the new window has appeared, close the old one "
            "(<kbd>Alt+F4</kbd> in its window), so the out-of-date "
            "version never gets sliced by mistake.")


def device_switched_html(name, printer):
    """The handoff page's line after a send switched Studio's Device
    page (only possible while Studio was closed)."""
    return (f"Before {name} opened, AccessSlice switched its Device page "
            f"to the {printer}, so the send window names the right "
            "printer.")


def device_kept_html(name, printer, other):
    """The handoff page's warning when the Device page could not be
    switched: Studio was open (it keeps the choice in memory and
    writes it back over its settings file on exit), or no serial is
    known for the project's printer."""
    on_now = f"the {other}" if other else "another printer"
    return (f"{name}'s Device page still shows {on_now}, not the "
            f"{printer} this project is for. In the send window, pick "
            f"the {printer} at the top before confirming - a wrong "
            "one shows an incompatibility error.")


def device_question_html(name, printer, other):
    """The question page when a send needs the Device page switched
    but Studio is running (seen 2026-09-23: an H2C send while Studio
    watched an X1 Carbon print)."""
    on_now = f"the {other}" if other else "another printer"
    carries_on = (f"A print already running on the {other} carries "
                  f"on - the printer has the whole file and {name} "
                  "only watches it. " if other else "")
    return (f"<p>This project is for the {printer}, but {name} is "
            f"already open and its Device page is on {on_now} (as far "
            "as its saved settings show). AccessSlice can switch the "
            f"Device page only while {name} is closed: {name} keeps "
            "that choice in memory and writes it back over its "
            "settings file when it exits.</p>"
            f"<p>Close {name} first: <kbd>Alt+Tab</kbd> to its window, "
            f"then <kbd>Alt+F4</kbd>. {carries_on}Then press the "
            "first button.</p>")


def limits_body_html(name):
    """The honest-limits page's sections - per-slicer facts."""
    return f"""
<p>AccessSlice works on the text inside a saved project file. A few
things in a {name} project are not stored as text, so no text
tool - AccessSlice included - can edit them. This page lists them
honestly, each with its practical workaround.</p>

<h2>Painted-on supports, seams and colours</h2>
<p>Studio's painting tools (support painting, seam painting, colour
painting) store their strokes as per-triangle binary data on the mesh
itself. There is no text to edit.</p>
<p>Workarounds: for supports, the per-object settings page and the
Support tab cover almost everything painting is used for - enable
supports for one object only, restrict them to the build plate, or
change the overhang angle threshold. For seams, the seam position
setting (Quality tab) chooses nearest, aligned, back or random without
any painting. True painted multi-colour needs Studio's GUI; if a
sighted helper paints once and saves, AccessSlice can still edit
everything else in that project afterwards without disturbing the
painting.</p>
<p>Multi-colour without any painting at all: a model designed in
OpenSCAD can be exported as a multi-coloured 3MF that {name}
reads - wrap each colour's geometry in a color() call and export with
the manifold backend. The colours come from the design itself, no
mouse work anywhere, and Studio maps them to filaments. Fully
screen-reader-doable from start to finish.</p>

<h2>Real AMS filament colours</h2>
<p>The colours stored in the project are display-only. The colours that
actually print come from the AMS units, synced inside {name}.
AccessSlice edits which filament SLOT each object uses - that part is
real - but what is loaded in each slot is between Studio and the
printer.</p>

<h2>Which plate gets sliced</h2>
<p>A multi-plate project does not record a chosen plate in the file;
the plate is picked at slicing time. AccessSlice states this on the
send page so it is never a surprise.</p>

<h2>Shapes themselves</h2>
<p>AccessSlice moves, rotates, scales and arranges objects, but it does
not sculpt or repair meshes. Model fixing belongs in the design tool
that made the model.</p>
"""
