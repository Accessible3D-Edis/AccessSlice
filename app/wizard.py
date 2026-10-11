"""wizard.py - the setup wizard: choose your printer, then one step
per page until AccessSlice is ready to use.

Asked for by Edis 2026-10-10, before the first public release: a new
user must be told exactly what to do - install the slicer, put the
printer on the network, save one project - and for the Prusa and
Creality printers exactly what AccessSlice needs to reach them. It
replaces the old three-question first-run page (which stays as
Settings) and is reachable again from Help and from Settings.

Only four printers have been tested with AccessSlice so far: the
Bambu Lab X1 Carbon and H2C, the Prusa Core One and the Creality K2
Plus. Every other choice says plainly that it is untested so far -
the wizard must never claim more than has been tried.

The steps per slicer are data here; webui renders them and server
routes them. Step kinds:

- info     text and a "Done - next step" button
- folders  the projects folder and the slicer program (the same save
           as Settings)
- network  the printer's address (and PrusaLink password), tested
           against the printer before it is saved; skippable
- project  save one project from the slicer into the projects
           folder; AccessSlice lists what it finds there
- finish   what AccessSlice found, and Finish

Everything is stdlib-only, like the rest of AccessSlice.
"""

import json
import re
import zipfile
from html import escape as esc
from pathlib import Path

# (id, shown name, slicer id, tested in the house)
PRINTERS = [
    ("x1c", "Bambu Lab X1 Carbon", "bambu", True),
    ("h2c", "Bambu Lab H2C", "bambu", True),
    ("bambu-other", "Another Bambu Lab printer (P1S, P1P, A1, A1 mini, "
     "X1E, H2D and others)", "bambu", False),
    ("coreone", "Prusa Core One", "prusa", True),
    ("prusa-other", "Another Prusa printer with PrusaLink (MK4, MK4S, "
     "MK3.9, XL, MINI and others)", "prusa", False),
    ("k2plus", "Creality K2 Plus", "creality", True),
    ("creality-other", "Another Creality printer on Creality OS (K1, "
     "K1 Max, K2, Ender-3 V3, Hi and others)", "creality", False),
    ("anycubic", "An Anycubic printer, with Anycubic Slicer Next",
     "anycubic", False),
    ("orca", "Another printer, with OrcaSlicer", "orca", False),
]

CONFIG_KEY = "wizard_printer"
DONE_KEY = "wizard_done"

# Where each slicer is downloaded: the maker's own site and where to
# look on it (deep links move; the sites' menus are stable).
DOWNLOADS = {
    "bambu": "bambulab.com, under Download, then Bambu Studio",
    "prusa": "prusa3d.com, under Software, then PrusaSlicer",
    "creality": "creality.com, under Download, then Creality Print",
    "anycubic": "anycubic.com, under Download, then Anycubic Slicer Next",
    "orca": "github.com/SoftFever/OrcaSlicer, under Releases",
}

SLICER_NAMES = {
    "bambu": "Bambu Studio",
    "prusa": "PrusaSlicer",
    "creality": "Creality Print",
    "anycubic": "Anycubic Slicer Next",
    "orca": "OrcaSlicer",
}

# The step lists. Prusa and Creality printers are reached by
# AccessSlice itself over the network, so they get a network step;
# Bambu, Anycubic and OrcaSlicer send through the slicer's own window.
STEPS = {
    "bambu": ["install", "folders", "project", "finish"],
    "prusa": ["install", "printer-network", "folders", "network",
              "project", "finish"],
    "creality": ["install", "folders", "network", "project", "finish"],
    "anycubic": ["install", "folders", "project", "finish"],
    "orca": ["install", "folders", "project", "finish"],
}

KINDS = {
    "install": "info", "printer-network": "info", "folders": "folders",
    "network": "network", "project": "project", "finish": "finish",
}

TITLES = {
    "install": "Install {slicer} and set up your printer in it",
    "printer-network": "Put the printer on your network",
    "folders": "Where AccessSlice keeps your projects",
    "network": "How AccessSlice reaches the printer",
    "project": "Save your first project from {slicer}",
    "finish": "Check and finish",
}


def choice(printer_id):
    """(id, name, slicer, tested) for a printer id, or None."""
    for entry in PRINTERS:
        if entry[0] == printer_id:
            return entry
    return None


def steps_for(printer_id):
    """The step names for a printer id ([] for an unknown one)."""
    found = choice(printer_id)
    return list(STEPS[found[2]]) if found else []


def title(step, slicer):
    return TITLES[step].format(slicer=SLICER_NAMES.get(slicer, slicer))


# ------------------------------------------------------------ texts

def _sighted_help_note(slicer_name):
    return (f"<p>Parts of {esc(slicer_name)}'s own first-start screens "
            "are hard or impossible to use with a screen reader. If you "
            "get stuck there, this is the one time a sighted helper "
            "is worth asking for: it is needed once, and AccessSlice "
            "does the rest from then on.</p>")


def install_html(printer_id):
    """The first step's text for a printer choice."""
    _pid, name, slicer, tested = choice(printer_id)
    slicer_name = SLICER_NAMES[slicer]
    url = DOWNLOADS[slicer]
    tested_html = (
        f"<p><strong>{esc(name)}</strong> has been tested with "
        "AccessSlice from start to finish.</p>" if tested else
        f"<p><strong>{esc(name)}</strong> has not been tested with "
        "AccessSlice yet. It uses the same slicer and the same way of "
        "talking to the printer as a tested one, so it is expected to "
        "work, but nobody has tried it. If something does not work, "
        "the Diagnostics page has the details to report.</p>")
    common = (f"<li>Download {esc(slicer_name)} from its maker's site: "
              f"<strong>{esc(url)}</strong>. Install it with the "
              "standard options. AccessSlice finds it in the standard "
              "install folder by itself.")
    if slicer == "bambu":
        steps = f"""
<ol>
{common}</li>
<li>Start Bambu Studio once. Sign in with your Bambu Lab account and
let it finish its first-start questions (region, printer models,
filaments).</li>
<li>Make sure Bambu Studio can send to your printer: its
<strong>Device</strong> page lists your printer. If you already print
from Bambu Studio, this is already done. A new printer is bound to
your account from the printer's own screen or the Bambu Handy app;
a printer in LAN-only mode is added with its access code.</li>
<li>Close Bambu Studio (<kbd>Alt+F4</kbd>).</li>
</ol>
<h2>What AccessSlice does with a Bambu Lab printer</h2>
<p>All settings, the plate, slicing, the slice report and the layer
preview happen in AccessSlice, with Bambu Studio's own engine. For the
final send, AccessSlice opens the sliced job in Bambu Studio and tells
you which keys to press (<kbd>Ctrl+R</kbd>, then
<kbd>Ctrl+Shift+G</kbd>): Bambu Studio's send window does the network
part. AccessSlice does not read a Bambu printer's status or control
it - that stays in Bambu Studio and the Bambu Handy app.</p>"""
    elif slicer == "prusa":
        steps = f"""
<ol>
{common}</li>
<li>Start PrusaSlicer once. Its Configuration Wizard opens by itself:
choose your printer model (for example <strong>Core One</strong>)
and the filaments you use, then finish the wizard.</li>
<li>Close PrusaSlicer (<kbd>Alt+F4</kbd>).</li>
</ol>
<h2>What AccessSlice does with a Prusa printer</h2>
<p>Everything: settings, the plate, slicing, the report and the layer
preview, and then it sends the print straight to the printer over your
network with PrusaLink - no PrusaSlicer window. It also reads the
printer's progress aloud, and can preheat, load and unload filament,
move, pause and stop, and manage the files on the printer's USB drive.
The next step puts the printer on the network.</p>"""
    elif slicer == "creality":
        steps = f"""
<ol>
{common} AccessSlice is tested with Creality Print 7.</li>
<li>Start Creality Print once and choose your printer model in its
first-start questions.</li>
<li>Have the printer switched on and connected to the same network as
this computer (on the printer's screen: Settings, then Network, then
Wi-Fi - or a network cable). On Creality Print's
<strong>Device</strong> page, add the printer: Creality Print searches
your network for it. AccessSlice later reads the printer's address
from Creality Print's own device list, so you never have to type
it.</li>
<li>Close Creality Print (<kbd>Alt+F4</kbd>).</li>
</ol>
<p>The printer stays on Creality's normal firmware - nothing has to be
rooted or changed on it.</p>
<h2>What AccessSlice does with a Creality printer</h2>
<p>Everything: settings, the plate, slicing, the report and the layer
preview, and then it sends the print straight to the printer over your
network - no Creality Print window. It also reads the printer's
progress aloud, and can preheat, load and unload filament (including
the CFS), move, pause and stop, and manage the printer's files.</p>"""
    else:
        steps = f"""
<ol>
{common}</li>
<li>Start {esc(slicer_name)} once, choose your printer model in its
first-start questions, and make sure you can print from it the usual
way.</li>
<li>Close {esc(slicer_name)} (<kbd>Alt+F4</kbd>).</li>
</ol>
<h2>What AccessSlice does with this printer</h2>
<p>Settings, the plate, slicing, the report and the layer preview
happen in AccessSlice with {esc(slicer_name)}'s own engine. For the
final send, AccessSlice opens the sliced job in {esc(slicer_name)} and
tells you which keys to press; its own send does the network part.</p>"""
    return tested_html + steps + _sighted_help_note(slicer_name)


PRUSA_NETWORK_HTML = """
<p>AccessSlice sends prints to a Prusa printer with
<strong>PrusaLink</strong>, the printer's own connection on your home
network. It is built into the Core One, MK4, MK3.9, XL and MINI
(the MINI needs Prusa's network module or a cable).</p>
<ol>
<li>On the printer's screen, open <strong>Settings</strong>, then
<strong>Network</strong>, and connect it to your Wi-Fi network, or
plug in a network cable.</li>
<li>Still under <strong>Settings</strong>, <strong>Network</strong>,
open <strong>PrusaLink</strong> and make sure it is turned on. That
screen shows two things AccessSlice needs in a later step: the
printer's <strong>address</strong> (four numbers with dots, like
192.0.2.25) and its <strong>password</strong>. Write both down, or ask
someone to read them to you.</li>
<li>Put a USB drive in the printer and leave it there. Prusa printers
keep their print files on it, so that is where AccessSlice puts every
print.</li>
</ol>
<p>Already added this printer to PrusaSlicer as a physical printer
with PrusaLink? Then AccessSlice reads the address and password from
PrusaSlicer, and the later step has them filled in already.</p>
"""


def project_html(slicer, projects_dir, texts, model_file_available=True):
    """The save-one-project step's text. model_file_available: the
    slicer's engine can build a new job from a model file (not
    Creality Print's - its raw-model import crashes upstream)."""
    slicer_name = SLICER_NAMES[slicer]
    shortcuts = getattr(texts, "SHORTCUTS", {}) or {}
    import_keys = shortcuts.get("import", "Ctrl+I")
    save_keys = shortcuts.get("save_project_as", "Ctrl+Shift+S")
    if model_file_available:
        later = ("<p>From then on, a new job starts in AccessSlice from "
                 "just a model file, and the slicer window is not needed "
                 "to make projects.</p>")
    else:
        # Edis, 2026-10-11: "every new job starts this same way" read
        # as if sending went through the slicer too - it does not.
        later = (f"<p>One thing is different with {esc(slicer_name)}: "
                 "its engine cannot turn a model file into a project by "
                 "itself, so <strong>only creating the project</strong> "
                 f"happens in {esc(slicer_name)}, for every new job. "
                 f"Start a new print job opens {esc(slicer_name)}, you "
                 "import the model and save the project, and AccessSlice "
                 "opens the saved project the moment it appears.</p>"
                 "<p><strong>Everything after that happens in "
                 "AccessSlice</strong>: the settings, slicing, the "
                 "report, sending the print straight to the printer over "
                 "your network, and following and controlling the "
                 f"printer. {esc(slicer_name)} is never needed to slice "
                 "or to send.</p>")
    return f"""
<p>AccessSlice builds every new print job on a project you saved in
{esc(slicer_name)} for that printer - that is where the printer's
settings, the print profile and your filaments come from. So the very
first project is made in {esc(slicer_name)}, once:</p>
<ol>
<li>Start {esc(slicer_name)} (the button below does it for you). It can
take a while to appear; check with <kbd>Alt+Tab</kbd>.</li>
<li>Check that your printer is the one selected in it.</li>
<li>Press <kbd>{esc(import_keys)}</kbd> and choose any model file (an
STL, 3MF, STEP or OBJ). This is an ordinary Windows file dialog.</li>
<li>Press <kbd>{esc(save_keys)}</kbd> (Save project as) and save the
project into your AccessSlice projects folder:
<strong>{esc(projects_dir)}</strong></li>
<li>Close {esc(slicer_name)} (<kbd>Alt+F4</kbd>).</li>
<li>Come back here and press <strong>Check the projects folder</strong>.
</li>
</ol>
{later}
"""


# ------------------------------------------------------------ checks

_PRUSA_PRINTER = re.compile(r"^;\s*printer_settings_id\s*=\s*(.+)$",
                            re.MULTILINE)


def project_printer(path):
    """The printer a saved project was made for, read from the 3MF
    (Bambu, Orca, Creality, Anycubic: Metadata/project_settings.config;
    PrusaSlicer: Metadata/Slic3r_PE.config); "" when unreadable."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "Metadata/project_settings.config" in names:
                data = json.loads(z.read("Metadata/project_settings.config")
                                  .decode("utf-8", "replace"))
                value = data.get("printer_settings_id", "")
                if isinstance(value, list):
                    value = value[0] if value else ""
                return str(value).strip()
            if "Metadata/Slic3r_PE.config" in names:
                text = z.read("Metadata/Slic3r_PE.config").decode(
                    "utf-8", "replace")
                found = _PRUSA_PRINTER.search(text)
                return found.group(1).strip() if found else ""
    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
        return ""
    return ""


def saved_projects(projects_dir, limit=5):
    """[(file name, printer)] for the newest project files in the
    folder - sliced results (.gcode.3mf) left out."""
    try:
        folder = Path(projects_dir)
        files = [p for p in folder.glob("*.3mf")
                 if not p.name.lower().endswith(".gcode.3mf")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    return [(p.name, project_printer(p)) for p in files[:limit]]
