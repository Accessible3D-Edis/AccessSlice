"""webui.py - every AccessSlice screen as server-rendered HTML.

Design rules (from docs/v1-screens-and-workflow.md), applied everywhere:

- One h1 per page saying where you are; h2/h3 below - navigable by
  screen-reader heading keys.
- Landmarks: banner (app header), nav (editor tabs), main (content).
- Only real HTML controls with programmatic labels; no click-only divs.
- Status messages appear in a live region (role="status") near the top,
  so screen readers announce them after each action, AND visibly.
- Plain language first, raw setting keys as secondary detail.

Pages are ordinary full page loads (forms + redirects); the only
JavaScript in the app is the waiting screen's poll for the folder
watcher. Server-rendered pages are the sturdiest thing a screen reader
can meet, which is why AccessSlice is built this way.
"""

from html import escape as esc
from urllib.parse import quote as urlquote

import engine_driver
import geometry
from version import VERSION
import toolpath
import viewport
# The settings catalog is reached through session.catalog: each
# backend brings its own (Bambu's for the Orca family,
# PrusaSlicer's for its own key names).

# Spoken names for shortcut actions when no backend texts are at hand
# (direct render calls in tests); with texts the slicer's real keys
# from texts.SHORTCUTS are used.
_SHORTCUT_FALLBACKS = {
    "import": "the import shortcut",
    "save_project_as": "the save-project-as shortcut",
    "slice": "the slice shortcut",
    "send": "the send shortcut",
}


def _shortcut(texts, action):
    """The slicer's keyboard shortcut for an action (part 5 texts)."""
    table = getattr(texts, "SHORTCUTS", None)
    if table and action in table:
        return table[action]
    return _SHORTCUT_FALLBACKS.get(action, action)


# The editor's tab list: (url part, shown name).
EDITOR_TABS = [
    ("quality", "Quality"),
    ("strength", "Strength"),
    ("support", "Support"),
    ("speed", "Speed"),
    ("adhesion", "Adhesion"),
    ("others", "Others"),
    ("filaments", "Filaments"),
    ("presets", "Printer and presets"),
    ("placement", "Placement"),
    ("all", "All settings"),
    ("backups", "Backups"),
]


# ------------------------------------------------------------- page shell


def page(title, body, msg=None, project=None, wide=False, scripts=(),
         head=""):
    """Wrap body HTML in the full document: banner, status region, main.

    wide: the editor pages that carry the 3D view use the wider
    two-column measure; every other page keeps the reading width.
    scripts: extra static scripts (the 3D viewport, the toolpath
    preview) loaded after app.js - only the pages that need them pay
    for them. head: extra head markup (the preview's self-refresh
    while its parse runs).
    """
    # The message is rendered visibly right away (works without any
    # JavaScript); app.js additionally re-inserts it into the live region
    # a moment after load, because screen readers only announce live
    # region CHANGES, not content that was already on the page.
    # tabindex="-1" lets app.js move focus here when the control the
    # user was on no longer exists after an action (e.g. the Save button
    # goes disabled once everything is saved) - landing on the result
    # message instead of the top of the page.
    status = (f'<div role="status" class="status" id="status" tabindex="-1" '
              f'data-msg="{esc(msg or "")}">{esc(msg or "")}</div>')
    project_note = (
        f'<span class="project-note">Editing: {esc(project)}</span>'
        if project else ""
    )
    extra = "".join('\n<script src="/static/' + esc(name) + '"></script>'
                    for name in scripts)
    main_class = ' class="wide"' if wide else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)} - AccessSlice</title>
<link rel="stylesheet" href="/static/style.css">{head}
</head>
<body>
<header class="banner">
  <a class="app-name" href="/"><span class="app-mark" aria-hidden="true"></span>AccessSlice</a>
  {project_note}
  <nav aria-label="AccessSlice">
    <ul>
      <li><a href="/">Home</a></li>
      <li><a href="/files">Open a project</a></li>
      <li><a href="/setup">Settings</a></li>
      <li><a href="/tour">Help and tour</a></li>
      <li><a href="/diagnostics">Diagnostics</a></li>
    </ul>
  </nav>
</header>
{status}
<main{main_class}>
{body}
</main>
<script src="/static/app.js"></script>{extra}
</body>
</html>"""


def _button_form(action, label, hidden=None, cls="", confirm_note=None):
    """A one-button POST form - the accessible way to make action links."""
    fields = "".join(
        f'<input type="hidden" name="{esc(k)}" value="{esc(str(v))}">'
        for k, v in (hidden or {}).items()
    )
    note = f'<p class="hint">{esc(confirm_note)}</p>' if confirm_note else ""
    return (f'<form method="post" action="{esc(action)}" class="inline-form">'
            f'{fields}{note}<button class="{cls}">{esc(label)}</button></form>')


# ---------------------------------------------------------------- screens


def render_setup(config, detected_exe, slicers, configured=False,
                 texts=None, paths=None):
    """Screen 1 - first-run setup, later reachable as Settings.

    paths: slicer id -> the program path to put in the field when
    that radio is chosen (the last saved one, else detected, else
    ""); app.js swaps the field the moment the choice changes, and
    the save does the same swap without script."""
    paths = paths or {}
    slicer_current = config.get("slicer", "bambu")
    current_name = next((name for value, name, _s in slicers
                         if value == slicer_current), "your slicer")
    radios = []
    for value, name, supported in slicers:
        checked = ' checked' if value == slicer_current and supported else ""
        disabled = "" if supported else " disabled"
        note = "" if supported else \
            ' <span class="hint">(coming later - not yet supported)</span>'
        radios.append(
            f'<label class="radio-row"><input type="radio" name="slicer" '
            f'value="{value}"{checked}{disabled} '
            f'data-exe="{esc(paths.get(value, ""))}" '
            f'data-name="{esc(name)}"> {esc(name)}{note}</label>'
        )
    exe_value = config.get("slicer_exe") or detected_exe or ""
    example = getattr(texts, "EXE_EXAMPLE", "")
    exe_note = (
        f"AccessSlice found {esc(current_name)} at {esc(detected_exe)}. "
        "If that is right, leave the field as it is."
        if detected_exe and not config.get("slicer_exe")
        else "Full path to the slicer program"
             + (f", for example {esc(example)}" if example else "")
    )
    supported_names = [name for _v, name, s in slicers if s]
    only = " and ".join(supported_names) or "no slicer"
    only_verb = "is" if len(supported_names) == 1 else "are"
    projects_value = config.get("projects_dir", "")

    if configured:
        heading = "<h1>AccessSlice settings</h1>"
        intro = ("<p>Change any of these whenever you like - nothing here "
                 "is set in stone. Your choices apply from the next "
                 "action onward.</p>"
                 '<p><a href="/welcome">Run the setup wizard again</a> '
                 "- step by step for a printer, including installing "
                 "the slicer and connecting the printer.</p>")
        finish = "Save settings"
    else:
        heading = "<h1>Welcome to AccessSlice - one-minute setup</h1>"
        intro = ("<p>Three questions to get going. You can change every "
                 "answer later at any time, under Settings on the home "
                 "page.</p>")
        finish = "Finish setup"
    body = f"""
{heading}
{intro}
<form method="post" action="/setup">
  <fieldset>
    <legend><h2>1. Which slicer do you use?</h2></legend>
    {''.join(radios)}
    <p class="hint">Only {esc(only)} {only_verb} supported so far; the
    other slicers are on the roadmap and will appear here as choices
    when they are ready. Switching slicers later is always
    possible.</p>
  </fieldset>
  <fieldset>
    <legend><h2>2. Where should print projects live?</h2></legend>
    <p>This is the folder AccessSlice watches. When you save a project
    from your slicer into it, AccessSlice notices and offers to open
    it. The folder is created if it does not exist.</p>
    <label for="projects_dir">Projects folder</label>
    <input type="text" id="projects_dir" name="projects_dir"
           value="{esc(projects_value)}" size="60" required>
  </fieldset>
  <fieldset>
    <legend><h2>3. Where is your slicer installed?</h2></legend>
    <p id="exe-note" aria-live="polite">{exe_note}</p>
    <label for="slicer_exe">Slicer program path</label>
    <input type="text" id="slicer_exe" name="slicer_exe"
           value="{esc(exe_value)}" size="60" required>
  </fieldset>
  <button class="primary" id="btn-finish-setup">{esc(finish)}</button>
</form>
"""
    return page("Settings" if configured else "Setup", body)


# ---------------------------------------------------------------- wizard


def render_wizard_choose(printers, current="", configured=False, msg=None):
    """The setup wizard's first page: which printer is this for.

    printers: wizard.PRINTERS rows (id, name, slicer, tested); the
    tested ones are said to be tested, the rest untested so far."""
    def radio(pid, name, tested):
        checked = " checked" if pid == current else ""
        note = "" if tested else " - not tested yet"
        return (f'<label class="radio-row"><input type="radio" '
                f'name="printer" value="{esc(pid)}"{checked} required> '
                f"{esc(name)}{note}</label>")
    tested = "".join(radio(p, n, t) for p, n, _s, t in printers if t)
    others = "".join(radio(p, n, t) for p, n, _s, t in printers if not t)
    leave = ('<p><a href="/">Leave the wizard and go home</a> - nothing '
             "you set up before is changed.</p>" if configured else "")
    body = f"""
<h1>Welcome to AccessSlice - setup wizard</h1>
<p>This wizard takes you through everything AccessSlice needs, one
step per page: installing your slicer, connecting the printer where
AccessSlice talks to it directly, and saving one first project. Each
page says exactly what to do and has a button for the next step. It
takes about ten minutes, most of it in the slicer's installer.</p>
<form method="post" action="/welcome">
<fieldset>
  <legend><h2>Which printer is this for?</h2></legend>
  <h3>Tested with AccessSlice</h3>
  {tested}
  <h3>Not tested yet - expected to work the same way</h3>
  {others}
</fieldset>
<p class="hint">Have more than one printer? Choose one now; this
wizard can be run again for the next one from Help and tour, or from
Settings.</p>
<button class="primary" id="wizard-start">Start the wizard</button>
</form>
{leave}
"""
    return page("Setup wizard", body, msg=msg)


def _wizard_button(number, action, label, cls="", button_id=""):
    ident = f' id="{esc(button_id)}"' if button_id else ""
    klass = f' class="{esc(cls)}"' if cls else ""
    return (f'<form method="post" action="/welcome/{number}" '
            f'class="inline-form"><input type="hidden" name="action" '
            f'value="{esc(action)}"><button{klass}{ident}>{esc(label)}'
            "</button></form>")


def render_wizard_step(printer_name, number, total, title, kind,
                       text_html="", values=None, configured=False,
                       msg=None):
    """One step of the setup wizard. kind is wizard.KINDS' value; the
    text is the step's own HTML from wizard.py; values carries what
    the step's form needs (paths, address, found projects, checks)."""
    v = values or {}
    if kind == "folders":
        exe_note = (f"AccessSlice found {esc(v.get('slicer_name', ''))} at "
                    f"{esc(v.get('detected', ''))}. If that is right, "
                    "leave the field as it is."
                    if v.get("detected") else
                    f"AccessSlice did not find {esc(v.get('slicer_name', ''))}"
                    " in its standard install folder. Type the full path "
                    "to its program"
                    + (f", for example {esc(v['example'])}"
                       if v.get("example") else "")
                    + " - or install it first and come back to this "
                    "page.")
        action = f"""
<p>AccessSlice keeps your print projects in one folder on this
computer. It is created if it does not exist yet; the suggested place
is fine for almost everyone.</p>
<form method="post" action="/welcome/{number}">
<input type="hidden" name="action" value="folders">
<div class="setting-row">
  <label for="projects_dir">Projects folder</label>
  <input type="text" id="projects_dir" name="projects_dir"
         value="{esc(v.get('projects_dir', ''))}" size="60" required>
</div>
<h2>Where is {esc(v.get('slicer_name', 'the slicer'))} installed?</h2>
<p id="exe-note">{exe_note}</p>
<div class="setting-row">
  <label for="slicer_exe">Slicer program path</label>
  <input type="text" id="slicer_exe" name="slicer_exe"
         value="{esc(v.get('slicer_exe', ''))}" size="60">
</div>
<button class="primary" id="wizard-next">Save and go to the next
step</button>
</form>"""
    elif kind == "network":
        key_html = ""
        if v.get("needs_key"):
            key_html = f"""
<div class="setting-row">
  <label for="wizard-key">{esc(v.get('key_label', 'Password'))}</label>
  <input type="text" id="wizard-key" name="key"
         value="{esc(v.get('key', ''))}" size="20"
         aria-describedby="wizard-key-help" autocomplete="off">
  <p class="hint" id="wizard-key-help">{esc(v.get('key_help', ''))}</p>
</div>"""
        found = v.get("found_note", "")
        action = f"""
<p>AccessSlice sends prints to this printer itself, over your home
network, and reads its progress aloud. For that it needs the printer's
address{' and its PrusaLink password' if v.get('needs_key') else ''}.
The printer must be switched on and on the same network as this
computer.</p>
{f'<p>{esc(found)}</p>' if found else ''}
<form method="post" action="/welcome/{number}">
<input type="hidden" name="action" value="network">
<div class="setting-row">
  <label for="wizard-ip">Printer address</label>
  <input type="text" id="wizard-ip" name="ip"
         value="{esc(v.get('ip', ''))}" size="30"
         aria-describedby="wizard-ip-help">
  <p class="hint" id="wizard-ip-help">Four numbers with dots, as the
  printer's screen shows it under its network settings, or the
  printer's network name.</p>
</div>
{key_html}
<button class="primary" id="wizard-test">Test the connection and
save</button>
</form>
<p>Printer not at hand right now? Skip this step: AccessSlice asks for
the address at the first send, or under Control the printer.</p>
{_wizard_button(number, 'skip', 'Skip this step for now', button_id='wizard-skip')}"""
    elif kind == "project":
        found = v.get("projects") or []
        if found:
            items = "".join(
                f"<li>{esc(name)}"
                + (f" - for the printer {esc(printer)}" if printer else "")
                + "</li>" for name, printer in found)
            found_html = (f"<h2>Found in the projects folder</h2>"
                          f"<ul>{items}</ul><p>That is all AccessSlice "
                          "needs. Go on to the last step.</p>")
        else:
            found_html = ("<h2>Found in the projects folder</h2><p>No "
                          "project yet. Save one from the slicer as "
                          "described above, then check again.</p>")
        action = f"""
{found_html}
<div class="actions">
{_wizard_button(number, 'launch', 'Open ' + v.get('slicer_name', 'the slicer') + ' now', button_id='wizard-launch')}
{_wizard_button(number, 'check', 'Check the projects folder', button_id='wizard-check')}
{_wizard_button(number, 'next', 'Done - next step', cls='primary', button_id='wizard-next')}
</div>"""
    elif kind == "finish":
        checks = "".join(f"<li>{esc(line)}</li>"
                         for line in v.get("checks", []))
        action = f"""
<h2>What AccessSlice found</h2>
<ul>{checks}</ul>
<p>Anything still missing can be finished later: Settings changes the
folder and the slicer, Control the printer sets the printer's address,
and this wizard runs again from Help and tour.</p>
{_wizard_button(number, 'finish', 'Finish setup', cls='primary', button_id='wizard-finish')}"""
    else:
        action = _wizard_button(number, "next", "Done - next step",
                                cls="primary", button_id="wizard-next")
    back = ("/welcome" if number <= 1 else f"/welcome/{number - 1}")
    leave = (' <a href="/">Leave the wizard and go home</a>'
             if configured else "")
    body = f"""
<h1>{esc(title)}</h1>
<p class="hint">Setup wizard for the {esc(printer_name)}: step {number}
of {total}.</p>
{text_html}
{action}
<p><a href="{back}">Back to the previous step</a>{leave}</p>
"""
    return page(f"Setup wizard, step {number} of {total}", body, msg=msg)


def coffee_dialog():
    """The monthly buy-me-a-coffee pop-up (coffee.py has the rules).

    Rendered open, so it is on the page even without script; app.js
    reopens it as a modal dialog, which screen readers announce and
    which holds the focus until it is answered. Escape counts as
    "Not now" (app.js posts that form), so a dismissed pop-up does
    not come back on the next visit to Home."""
    return f"""
<dialog open class="coffee" id="coffee-dialog"
        aria-labelledby="coffee-heading" aria-describedby="coffee-text">
<h2 id="coffee-heading">Buy me a coffee?</h2>
<p id="coffee-text">AccessSlice is free and made by one person. If it
helps you print, a coffee at buymeacoffee.com/accessible3d keeps it
going. The button opens the page in your web browser.</p>
<div class="actions">
<form method="post" action="/coffee/open" class="inline-form"><button
class="primary" id="coffee-open" autofocus>Buy me a coffee</button></form>
<form method="post" action="/coffee/later" class="inline-form"
id="coffee-later-form"><button id="coffee-later">Not now</button></form>
</div>
<p class="hint">AccessSlice asks about once a month, only when it
opens on the home page.</p>
</dialog>
"""


def render_home(config, recents, msg=None, slicer_name="your slicer",
                coffee=False, printer=False, control=False):
    """Screen 2 - home. recents: list of (path, display_line);
    coffee: show the monthly buy-me-a-coffee pop-up; printer: the active
    slicer's printer can be asked directly (the Printer status page
    is offered, so a running print can be followed after a restart)."""
    printer_html = (
        '<li><a href="/printer"><strong>Printer status</strong> What your '
        "printer is doing right now - progress and time left of a running "
        "print, read again every half minute.</a></li>") if printer else ""
    if control:
        printer_html += (
            '<li><a href="/printer/control"><strong>Control the printer'
            "</strong> Preheat, load and unload filament, move and home, "
            "calibrate, pause or stop a print, and the files on its USB "
            "drive - from here, nothing on its touchscreen.</a></li>")
    recent_html = ""
    if recents:
        items = "".join(
            "<li>"
            + _button_form("/open", display, hidden={"path": path})
            + "</li>"
            for path, display in recents
        )
        recent_html = f"""
<h2>Recent projects</h2>
<ul class="plain-list">{items}</ul>"""

    body = f"""
<div class="hero">
<h1>AccessSlice</h1>
<p>Your accessible slicer. Print jobs are created, edited and sliced
right here; the slicer's own window is only needed for the final send
to the printer.</p>
<p>Active slicer: <strong>{esc(slicer_name)}</strong> - changeable
any time under <a href="/setup">Settings</a>.</p>
<h2>Start</h2>
<div class="actions">
<a class="button primary" href="/start">Start a new print job</a>
<a class="button" href="/files">Open a project file</a>
</div>
</div>
{coffee_dialog() if coffee else ""}
{recent_html}
<h2>More</h2>
<ul class="link-cards">
{printer_html}<li><a href="/tour"><strong>Help and tour</strong> How AccessSlice
works and how to move around it - read it any time.</a></li>
<li><a href="/setup"><strong>Settings</strong> Change your slicer,
projects folder or slicer path at any time.</a></li>
<li><a href="/welcome"><strong>Setup wizard</strong> Step by step for
a printer: the slicer, the printer's connection and a first
project.</a></li>
<li><a href="/diagnostics"><strong>Diagnostics</strong> What
AccessSlice has been doing, for when something seems off.</a></li>
<li><a href="/limits"><strong>What AccessSlice cannot edit</strong>
Honest limits and their workarounds.</a></li>
{_button_form('/coffee/open', 'Support AccessSlice - buy me a coffee (opens your browser)') if not coffee else ''}
</ul>
"""
    return page("Home", body, msg=msg)


# ---------------------------------------------------------------- tour

TOUR_TABS = {
    "quality": ("Layer height, walls, top and bottom layers, seams, surface "
                "finish - the settings that decide how the print looks and "
                "how long it takes."),
    "strength": ("Infill pattern and density, wall count, the things that "
                 "make a part strong or light."),
    "support": ("Whether supports are on, their type (normal or tree), the "
                "overhang angle that triggers them, and how easily they "
                "come off."),
    "speed": ("Print speeds and accelerations, per feature. Slower is "
              "usually prettier; the defaults are safe."),
    "adhesion": ("Brim, skirt and raft - how the first layer holds the "
                 "plate."),
    "others": ("Print sequence, spiral vase, timelapse, fuzzy skin and "
               "other less common settings."),
    "filaments": ("Which filament prints each object (the slot numbers "
                  "match your AMS or spool holder), temperatures, flow, "
                  "flushing and the prime tower."),
    "presets": ("The printer, the print profile and each filament's "
                "preset from your slicer's own library - the same choices "
                "the slicer's top bar offers."),
    "placement": ("Where every object sits on the plate, in millimetres "
                  "and in words, with rotate, scale, arrange, auto-orient "
                  "and move-to-plate - including a new plate. Copies of "
                  "an object, removing an object and adding another model "
                  "file live here too; what does not fit on a plate can "
                  "spill onto new plates. A 3D view mirrors it for "
                  "sighted users; nothing exists only in the picture."),
    "all": ("Every setting in the project, searchable by plain words or "
            "by the slicer's own key name - nothing is out of reach."),
    "backups": ("Every earlier version AccessSlice saved, restorable with "
                "one button. Restoring is itself undoable."),
}


def render_tour(slicer_name="your slicer", first=False, msg=None):
    """The guided tour: what AccessSlice is, how a job flows, how to
    move around it with a screen reader, what every page and tab is
    for, and where to look when something is off. Shown once after
    setup (skippable) and reachable any time from Help and tour."""
    if first:
        top = ("<p>This tour takes a few minutes. You can skip it now and "
               "come back any time from the <strong>Help and tour</strong> "
               "link in the top navigation.</p>"
               + _button_form("/tour/done", "Skip the tour"))
        bottom = _button_form("/tour/done", "Start using AccessSlice",
                              cls="primary")
    else:
        top = ""
        bottom = '<p><a href="/">Home</a></p>'
    tabs = "".join(
        f'<h3 id="tab-{tab_id}">{esc(name)}</h3><p>{esc(TOUR_TABS[tab_id])}'
        f' <a href="/project/{tab_id}#tab-heading">Open the {esc(name)} '
        f"tab</a> (when a project is open).</p>"
        for tab_id, name in EDITOR_TABS)
    body = f"""
<h1>Welcome to AccessSlice</h1>
{top}
<h2 id="tour-what">What AccessSlice is</h2>
<p>AccessSlice prepares 3D prints without the slicer's mouse-only
screens. A slicer project is a file full of plain text, so every
setting in it - layer height, walls, infill, supports, filaments,
where each object sits - can be read and changed as text. AccessSlice
shows all of that as ordinary web-style pages that screen readers
read, slices the project with {esc(slicer_name)}'s own slicing engine,
and sends the result to your printer. {esc(slicer_name)} must be
installed; AccessSlice never replaces its engine, only its screens.</p>

<h2 id="tour-flow">How a print job flows</h2>
<ol>
<li><strong>Start a new print job</strong> from the home page: choose
the printer and give the path of your model file (an STL, 3MF or
similar). AccessSlice builds the project itself. Or <strong>Open a
project file</strong> you saved earlier.</li>
<li><strong>Edit</strong> on the tabs - quality, strength, supports,
adhesion, filaments, placement, everything. Changes are staged first
and saved together, with a backup of the file you started from.</li>
<li><strong>Slice now</strong> on the Slice and send page. Warnings
you would want to hear come first, in words; then the slice runs and
the report tells you the print time, the filament used, and anything
the engine complained about. A layer-by-layer preview is one link
away.</li>
<li><strong>Send</strong>: straight to the printer over your own
network where the printer supports it, or to a USB drive with one
button, or hand the project to {esc(slicer_name)} and press its own
send shortcut - AccessSlice tells you which keys at that moment.</li>
<li><strong>Print</strong>. When a printer is reached over the
network, the Printer page reads progress aloud every half minute and
announces the end.</li>
</ol>

<h2 id="tour-navigation">Moving around with a screen reader</h2>
<ul>
<li><strong>Landmarks.</strong> Every page has a banner (the app name
and the top navigation, a landmark named "AccessSlice"), and a main
region. Editor pages add a second navigation named "Project sections"
- the tabs. The landmark key jumps between them.</li>
<li><strong>Headings.</strong> Each page has one level-1 heading; the
sections below are level 2, settings groups level 3. The heading key
is the fastest way through a long tab.</li>
<li><strong>Tabs.</strong> The tab links land you straight on the
content heading of the next tab, past the summary and the tab list.</li>
<li><strong>Buttons and results.</strong> After any button, the result
is spoken and shown in a status line near the top, and the focus
stays on the control you pressed - or moves to the status line when
that control has gone. You never land back at the top of the page.</li>
<li><strong>Fields.</strong> Every field has a label, a unit where one
applies, and a help text read with it. Values stay exactly as the
slicer stores them ("0.2", "15%"), so nothing is reformatted behind
your back.</li>
<li><strong>Questions.</strong> When the slicer would have asked you
something in a dialog (a 100% infill choice, millimetres or percent),
AccessSlice asks the same question as a page with Yes and No.</li>
<li><strong>Pictures.</strong> The 3D view and the sliced preview are
mirrors for sighted users. Every fact in them is also in words on the
same page, and nothing inside a picture can take the focus.</li>
<li><strong>Zoom and themes.</strong> The pages reflow up to 400%
zoom without sideways scrolling and follow your light or dark
setting.</li>
</ul>

<h2 id="tour-pages">The pages</h2>
<ul>
<li><strong>Home</strong> - the two Start buttons, recent projects, and
the links below.</li>
<li><strong>Start a new print job</strong> - printer choice and the
model file, then straight into the editor.</li>
<li><strong>Open a project file</strong> - the projects folder
AccessSlice watches, newest first, plus any path you type.</li>
<li><strong>Settings</strong> - which slicer, which projects folder,
where the slicer program is. Nothing here is set in stone.</li>
<li><strong>Setup wizard</strong> - step by step for a printer: the
slicer, the printer's connection, a first project. Run it again for
each new printer.</li>
<li><strong>Slice and send</strong> - reached from the editor's bottom
bar: the warnings, Slice now, the report, the send buttons.</li>
<li><strong>Printer</strong> - progress of a running print reached over
the network.</li>
<li><strong>Diagnostics</strong> - a dated log of what AccessSlice did,
with the technical details of any error, ready to paste into a bug
report.</li>
<li><strong>What AccessSlice cannot edit</strong> - the honest limits
(painted supports and seams, the slicer's own previews) and the
workaround for each.</li>
</ul>

<h2 id="tour-tabs">The editor tabs</h2>
<p>Every tab shows the settings that matter most for its topic in plain
language, then an "All other" section with the rest of the settings on
that topic, so nothing is hidden. Each tab's heading carries a link
back to this section.</p>
{tabs}
<h3 id="tab-review">Review changes</h3>
<p>Every staged change in one list, each with its old and new value,
discardable one by one or all at once, then Save. Saving backs up the
file you started from, writes the new one, and checks it structurally
before saying so. If a check fails, the original is put back
untouched.</p>

<h2 id="tour-safety">What keeps your files safe</h2>
<ul>
<li>Every save keeps a backup; the last ten are on the Backups tab.</li>
<li>Every saved file is verified before AccessSlice reports success;
a failed verification restores the original byte for byte.</li>
<li>If the file changes on disk behind AccessSlice's back (the slicer
saved it), saving pauses and a reload button appears.</li>
<li>Nothing leaves your computer. AccessSlice talks only to itself
and, when you press send, to your own printer.</li>
</ul>

<h2 id="tour-trouble">When something seems off</h2>
<p>Open <strong>Diagnostics</strong> from the top navigation. It lists
what AccessSlice did, in order, and the details of any unexpected
error - your file is never changed by an error. When reporting a
problem, say what you pressed, what you heard, what you expected, and
paste the end of that log.</p>

<h2 id="tour-again">Reading this again</h2>
<p><strong>Help and tour</strong> in the top navigation brings this page
back at any time, from anywhere in AccessSlice. The setup wizard runs
again from here too: <a href="/welcome">Run the setup wizard</a>.</p>
{bottom}
"""
    return page("Help and tour", body, msg=msg)


def render_start(slicer_name, known, current_printer, matches=None,
                 search="", msg=None, texts=None,
                 model_file_available=True):
    """Screen 3a - printer choice + explainer before launching the slicer.

    known: the user's own printers (learned from use, a short list);
    current_printer: the one the slicer would open with today, always
    preselected so doing nothing keeps everything as it is. matches:
    results for the search field, for the rare day a new printer model
    is needed - the full library list was overwhelming as radios or a
    select (Edis, 2026-08-26).
    """
    def radio(name, note=""):
        checked = " checked" if name == current_printer else ""
        return (f'<label class="radio-row"><input type="radio" '
                f'name="printer" value="{esc(name)}"{checked}> '
                f"{esc(name)}{esc(note)}</label>")

    choices = list(known)
    if current_printer and current_printer not in choices:
        choices.insert(0, current_printer)
    radios = "".join(
        radio(name, " (current)" if name == current_printer else "")
        for name in choices)

    if matches is None:
        results = ""
    elif matches:
        results = ("<h3>Search results</h3>"
                   + "".join(radio(name) for name in matches))
    else:
        results = (f"<h3>Search results</h3><p>No printer matched "
                   f"{esc(search)}.</p>")

    if choices or matches is not None:
        printer_field = f"""
<fieldset>
  <legend>Which printer is this job for?</legend>
  {radios}
  {results}
</fieldset>
<p class="help">AccessSlice sets {esc(slicer_name)} to this printer
before opening it: the machine, the print profile and filament presets
(switched to that printer's versions), and the Device page's selected
printer, so the saved project starts out right. Keep the current one
and nothing is touched. A printer picked from a search joins this
short list for next time.</p>
"""
    else:
        # An empty short list must say so, never just vanish (Edis,
        # 2026-09-01, first Creality start page).
        printer_field = f"""
<fieldset>
  <legend>Which printer is this job for?</legend>
  <p>No printers are on your {esc(slicer_name)} short list yet.
  Search the full printer list further down this page and pick one -
  it joins this list for next time. Opening a project saved for a
  printer also adds that printer by itself.</p>
</fieldset>
"""

    search_form = f"""
<form method="get" action="/start">
  <p><label for="printer-search">Need a printer that is not listed?
  Search the slicer's full printer list</label><br>
  <input type="text" id="printer-search" name="printer_search"
         value="{esc(search)}">
  <button>Search printers</button></p>
</form>"""
    if model_file_available:
        model_file_html = f"""
<h2>Start from a model file</h2>
<p>Give AccessSlice your model (an STL, STEP or OBJ file) and it
creates the project itself - no slicer window, nothing to import by
hand. The new project opens right here, built on the settings of your
newest saved project for that printer.</p>
<div class="setting-row">
  <label for="model-path">Full path to the model file</label>
  <input type="text" id="model-path" name="model_path" size="70">
</div>
<button class="primary" formaction="/new-job" id="btn-new-job">Create
the project and open it</button>
<h2>Or create the project in {esc(slicer_name)}</h2>
<p>The first job for a brand-new printer starts this way (AccessSlice
needs one saved project to build on), and it also covers model formats
the engine cannot import. What happens:</p>"""
    else:
        model_file_html = f"""
<h2>Create the project in {esc(slicer_name)}</h2>
<p>Starting straight from a model file is not available for
{esc(slicer_name)} - its engine crashes when handed a raw model file
(a bug in the slicer itself). Import the model in its window once and
save the project; everything after that, including slicing, happens
here. What happens:</p>"""
    body = f"""
<h1>Start a new print job</h1>
<form method="post" action="/start">
{printer_field}
{model_file_html}
<ol>
  <li>{esc(slicer_name)} starts. It can take a while to appear,
      depending on your computer - check with <kbd>Alt+Tab</kbd> now and
      then until its window is there. AccessSlice keeps watching the
      whole time, so there is no hurry.</li>
  <li>In the slicer, press <kbd>{esc(_shortcut(texts, 'import'))}</kbd>
      and choose your model file
      (STL, STEP, OBJ and more). That dialog is an ordinary Windows
      dialog and works with your screen reader.</li>
  <li>Press <kbd>{esc(_shortcut(texts, 'save_project_as'))}</kbd>
      (Save project as) and save the
      project into your AccessSlice
      projects folder. Also an ordinary dialog.</li>
  <li>The moment the file is saved, AccessSlice notices it and asks
      whether to open it - nothing opens without your say-so. Everything
      else happens here. You can then simply close {esc(slicer_name)} -
      AccessSlice reopens it for you at slicing time, with this project
      loaded.</li>
</ol>
<p><strong>Do not slice yet.</strong> Settings are chosen in AccessSlice
first; slicing comes at the end.</p>
<button id="btn-open-slicer-start">OK, open the slicer</button>
</form>
{search_form}
<p><a class="button" href="/">Cancel</a></p>
"""
    return page("Start a new print job", body, msg=msg)


def render_waiting(projects_dir, msg=None, texts=None):
    """Screen 3b - watching the projects folder. The one JS-driven page."""
    body = f"""
<h1>Waiting for your saved project</h1>
<p>The slicer is starting - this can take a while depending on your
computer. Check with <kbd>Alt+Tab</kbd> until its window appears; there
is no hurry, AccessSlice keeps watching. Then, in the slicer:</p>
<ol>
  <li>Press <kbd>{esc(_shortcut(texts, 'import'))}</kbd> and choose
      your model.</li>
  <li>Press <kbd>{esc(_shortcut(texts, 'save_project_as'))}</kbd>
      (Save project as) and save the
      project into:
      <strong>{esc(projects_dir)}</strong></li>
</ol>
<p>After the save you can close the slicer if you like - AccessSlice
opens it again for you at slicing time, with this project loaded.</p>
<p id="watch-line" aria-live="polite">Watching the projects folder.
Nothing saved yet.</p>
<p><a class="button primary" id="open-found-link" href="/open-found"
      hidden>Open the found project</a></p>
<p><a class="button" href="/files">Open a different project file
instead</a></p>
<p><a class="button" href="/">Cancel and go home</a></p>
"""
    return page("Waiting for your saved project", body, msg=msg)


def render_files(projects_dir, files, msg=None):
    """Open-a-project page: list of 3MFs in the folder + a path field."""
    if files:
        items = "".join(
            "<li>"
            + _button_form("/open", display, hidden={"path": path})
            + "</li>"
            for path, display in files
        )
        listing = f'<ul class="plain-list">{items}</ul>'
    else:
        listing = "<p>No project files found in the projects folder yet.</p>"

    body = f"""
<h1>Open a project</h1>
<h2>Projects in {esc(str(projects_dir))}</h2>
{listing}
<h2>Open from somewhere else</h2>
<form method="post" action="/open">
  <label for="path">Full path to a project 3MF file</label>
  <input type="text" id="path" name="path" size="70" required>
  <button>Open this file</button>
</form>
<p><a href="/">Back to home</a></p>
"""
    return page("Open a project", body, msg=msg)


# ------------------------------------------------------------ the editor


def render_diagnostics(entries_text, log_path, msg=None):
    """The diagnostics page - the app's recent activity in plain text.

    One dated line per event, oldest first, so reading top to bottom
    tells the story. Unexpected errors appear here with their technical
    details, ready to paste into a bug report.
    """
    if entries_text.strip():
        log_html = (f'<pre class="diagnostics-log">{esc(entries_text)}'
                    "</pre>")
    else:
        log_html = ("<p>The log is empty - nothing noteworthy has "
                    "happened yet.</p>")
    body = f"""
<h1>Diagnostics</h1>
<p>A dated record of what AccessSlice has done: projects opened, saves,
printer switches, and any problems - including the technical details of
unexpected errors, ready to paste into a bug report. Newest entries are
at the bottom.</p>
<p>The log lives at: {esc(log_path)}</p>
<p>AccessSlice version {esc(VERSION)}.</p>
<h2 id="tab-heading" tabindex="-1">Recent entries</h2>
{log_html}
{_button_form('/diagnostics/clear', 'Empty the log',
              confirm_note='Emptying removes all entries; do this after '
                           'a problem has been reported and fixed.')}
<p><a href="/">Home</a></p>
"""
    return page("Diagnostics", body, msg=msg)


def _tab_nav(active):
    links = []
    for tab_id, name in EDITOR_TABS:
        current = ' aria-current="page"' if tab_id == active else ""
        # The #tab-heading fragment drops focus straight onto the
        # content heading of the next page - no arrowing through the
        # summary and tab list after every tab switch.
        links.append(f'<li><a href="/project/{tab_id}#tab-heading"'
                     f'{current}>{esc(name)}</a></li>')
    return ('<nav aria-label="Project sections"><ul class="tabs">'
            + "".join(links) + "</ul></nav>")


def _bottom_bar(session, return_to="/project/review"):
    """The always-present action bar.

    return_to: where the save action should land afterwards - the page
    the user is currently on, so saving never teleports them elsewhere.
    Stable ids on every control let app.js put focus back on the same
    control after the page reloads.
    """
    n = len(session.pending)
    pending_text = (f"Review changes ({n} waiting)" if n
                    else "Review changes (none waiting)")
    save_note = "" if n else " disabled"
    return f"""
<div class="bottom-bar">
  <a class="button" id="nav-review" href="/project/review">
    {esc(pending_text)}</a>
  <form method="post" action="/project/save" class="inline-form">
    <input type="hidden" name="return_to" value="{esc(return_to)}">
    <button class="primary" id="btn-save-all"{save_note}>Save all
    changes</button>
  </form>
  <a class="button" id="nav-send" href="/project/send">Slice and
    send</a>
  {_button_form('/project/close', 'Close project')}
</div>"""


def _stale_html(session, active_tab):
    """The changed-on-disk warning, on every editor page while it holds.

    Shown when something else (usually Bambu Studio) rewrote the 3MF
    after AccessSlice read it. Saving and restoring refuse on their own
    while stale; this banner explains why and offers the way forward.
    The reload lands back on the same tab.
    """
    if not session.is_stale():
        return ""
    n = len(session.pending)
    dropped = (f" Reloading discards your {n} staged "
               f"change{'s' if n != 1 else ''}, which describe the "
               "older version." if n else "")
    outside = getattr(session, "slicer_name", "the slicer")
    return (
        '<div class="unsaved-note"><p>Warning: the project file was '
        f"changed outside AccessSlice - probably saved from "
        f"{esc(outside)}. The values shown here are the older "
        "version, and saving is paused so the newer file cannot be "
        f"overwritten by accident.{esc(dropped)}</p>"
        + _button_form("/project/reload", "Reload the file from disk",
                       hidden={"return_to": f"/project/{active_tab}"})
        + "</div>")


def _structure_html(session):
    """The load-time project notes, visible on every editor page.

    These are the sanity-check findings from opening the file (extra
    plates, unreadable objects, missing metadata) - static per load,
    unlike advice(), which tracks the staged values.
    """
    return "".join(f'<p class="unsaved-note">{esc(n)}</p>'
                   for n in getattr(session, "structure_notes", []))


def _advice_html(session, active_tab):
    """Slicer-rule warnings shown on every editor page while they hold.

    The 100%-infill conflict links to the same yes/no question Studio
    would pop, so an already-saved bad combination (found in a fresh
    project, say) can be resolved the Studio way at any time.
    """
    notes = "".join(f'<p class="unsaved-note">{esc(w)}</p>'
                    for w in session.advice())
    if session.infill_conflict():
        notes += (f'<p><a class="button" href="/project/infill-question'
                  f'?return_to=/project/{esc(active_tab)}">Answer the '
                  "slicer's question about this</a></p>")
    return notes


def _editor_page(session, active_tab, inner, msg=None, wide=False,
                 scripts=()):
    summary = "".join(f"<li>{esc(line)}</li>" for line in session.summary())
    unsaved = ""
    if session.pending:
        n = len(session.pending)
        plural = "s" if n != 1 else ""
        unsaved = (f'<p class="unsaved-note">{n} change{plural} staged and '
                   "not saved yet - use Review changes below.</p>")
    tab_name = dict(EDITOR_TABS).get(active_tab, "Review changes")
    # The h2 right above the content lets a screen-reader user jump
    # straight past the summary and tab list with one press of the
    # heading key, instead of arrowing through them on every page.
    body = f"""
<h1>Editing {esc(session.path.name)} - {esc(tab_name)}</h1>
<ul class="summary">{summary}</ul>
{unsaved}
{_tab_nav(active_tab)}
<section class="tab-content">
<h2 id="tab-heading" tabindex="-1">{esc(tab_name)}</h2>
<p class="tab-help"><a href="/tour#tab-{esc(active_tab)}">What the {esc(tab_name)} tab is for</a></p>
{_stale_html(session, active_tab)}
{_structure_html(session)}
{_advice_html(session, active_tab)}
{inner}
</section>
{_bottom_bar(session, return_to=f"/project/{active_tab}")}
"""
    return page(f"{tab_name} - {session.path.name}", body,
                msg=msg, project=session.path.name, wide=wide,
                scripts=scripts)


def _control_html(key, value, control, options, field_name=None,
                  described_by=None):
    """One accessible form control for a setting's current value."""
    name = field_name or key
    described = f' aria-describedby="{described_by}"' if described_by else ""
    if control == "checkbox":
        checked = " checked" if str(value) == "1" else ""
        # The hidden marker lets the server see unchecked boxes too.
        return (f'<input type="hidden" name="__present" value="{esc(name)}">'
                f'<input type="checkbox" id="f-{esc(name)}" '
                f'name="{esc(name)}" value="1"{checked}{described}>')
    if control == "select":
        opts = list(options or [])
        if not any(v == str(value) for v, _ in opts):
            opts.insert(0, (str(value), f"Current value: {value}"))
        rendered = "".join(
            f'<option value="{esc(v)}"'
            + (" selected" if v == str(value) else "")
            + f">{esc(text)}</option>"
            for v, text in opts
        )
        return (f'<select id="f-{esc(name)}" name="{esc(name)}"{described}>'
                f"{rendered}</select>")
    # "number" and "text" both render as text inputs: slicer values are
    # strings with formats like "15%" that a strict number input would
    # reject. inputmode still brings up the right keyboard where relevant.
    inputmode = ' inputmode="decimal"' if control == "number" else ""
    return (f'<input type="text" id="f-{esc(name)}" name="{esc(name)}" '
            f'value="{esc(str(value))}" size="20"{inputmode}{described}>')


def render_curated_tab(session, tab_id, entries, msg=None):
    """Quality / Strength / Support / Adhesion: a form of curated settings."""
    rows = []
    for key, label, help_text, unit, control, options in entries:
        if key not in session.settings:
            continue  # different printer/profile: skip missing keys quietly
        value = session.effective_setting(key)
        varies = False
        if isinstance(value, list):
            # A uniform list edits as one value; a list whose entries
            # genuinely differ (per nozzle variant) must NOT be flattened
            # by a single field - it links to the per-value editor.
            varies = bool(value) and any(v != value[0] for v in value)
            value = value[0] if value else ""
        unit_text = f" ({unit})" if unit else ""
        staged = any(c["kind"] == "setting" and c["key"] == key
                     for c in session.pending)
        staged_note = (' <span class="staged">changed, not saved yet</span>'
                       if staged else "")
        if varies:
            widget = (f'<a href="/project/setting/{esc(key)}">Values differ '
                      f"per nozzle variant - open the detailed editor</a>")
            label_attr = ""   # no single control for the label to point at
        else:
            widget = _control_html(key, value, control, options,
                                   described_by=f"h-{key}")
            label_attr = f' for="f-{esc(key)}"'
        rows.append(f"""
<div class="setting-row">
  <label{label_attr}>{esc(label)}{esc(unit_text)}{staged_note}</label>
  <p class="hint" id="h-{esc(key)}">{esc(help_text)}
     <span class="raw-key">Setting key: {esc(key)}</span></p>
  {widget}
</div>""")
    inner = f"""
<form method="post" action="/project/{tab_id}">
  {''.join(rows)}
  <button class="primary" id="btn-apply-{esc(tab_id)}">Apply changes on
  this tab</button>
  <p class="hint">Applying stages your edits for review; nothing is
  written to the file until you press Save all changes.</p>
</form>
{_more_settings(session, tab_id)}"""
    return _editor_page(session, tab_id, inner, msg=msg)


def _more_settings(session, tab_id):
    """Every remaining setting of this tab's topic, as edit-page links.

    The fields above are the curated, plain-language selection; this
    section guarantees the tab still shows EVERYTHING that belongs to
    its topic, so no setting hides behind the curation.
    """
    tab_name = dict(EDITOR_TABS).get(tab_id, tab_id)
    keys = [
        key for key in sorted(session.settings)
        if key not in session.catalog.CURATED_KEYS
        and session.scope_of(key) == "process"
        and session.catalog.tab_for(key) == tab_id
    ]
    if not keys:
        return ""
    rows = "".join(_setting_link(session, key) for key in keys)
    return f"""
<h3>All other {esc(tab_name)} settings ({len(keys)})</h3>
<p class="hint">Everything else in this category. Each opens its own
edit page.</p>
<ul class="plain-list">{rows}</ul>"""


def _object_filament_html(session):
    """The "print this object with filament slot N" control.

    This is THE answer to "print my model with slot 3" - filament
    numbers here are the project's filament slots, which become AMS
    slots after the user syncs AMS in the slicer. Shared by the
    Filaments and Printer-and-presets tabs (both forms post the same
    ext__ fields), so the choice is at hand wherever filaments are on
    screen.
    """
    names = session.filament_names()
    _mode, fmap = session.filament_map()
    objs = session.effective_objects()    # staged choices shown
    if not objs:
        return ""
    rows = []
    for o in objs:
        opts = "".join(
            f'<option value="{i + 1}"'
            + (" selected" if str(i + 1) == (o["extruder"] or "") else "")
            + f">Slot {i + 1} - {esc(names[i] if i < len(names) else '')}"
              "</option>"
            for i in range(max(len(names), len(fmap) or 1))
        )
        rows.append(f"""
<div class="setting-row">
  <label for="f-ext__{esc(o['id'])}">Print {esc(o['name'])} with
  filament</label>
  <select id="f-ext__{esc(o['id'])}" name="ext__{esc(o['id'])}">{opts}</select>
</div>""")
    return ("<h3>Which filament prints each object</h3>"
            "<p class='hint'>Slot numbers match the project's "
            "filament list - after syncing AMS in the slicer, "
            "slot 3 here is AMS slot 3.</p>" + "".join(rows))


def _number_or_text(value):
    """A number field only for values that ARE numbers - a filament
    type ("PLA") or colour ("#FF8000") on PrusaSlicer's Filaments tab
    needs a text field, or the browser refuses the value."""
    try:
        float(str(value).strip().rstrip("%"))
        return "number" if not str(value).strip().endswith("%") else "text"
    except ValueError:
        return "text"


def render_filaments_tab(session, msg=None):
    """Filaments: per-filament values, nozzle map, per-object filament."""
    names = session.filament_names()
    sections = []

    # Per-filament list settings, ONE labelled field per filament.
    # Two shapes exist: one value per filament (length n), or one block
    # of k values per filament (length k*n - e.g. the H2C keeps three
    # nozzle-variant values per filament). For blocks we show one field
    # per filament and write the whole block, which is what the Studio
    # GUI's single per-filament field does too.
    n = session.filament_count()
    for key, label, help_text, unit in session.catalog.FILAMENTS:
        values = session.effective_setting(key)
        if not isinstance(values, list) or n == 0 or len(values) % n != 0:
            continue
        k = len(values) // n
        fields = []
        for i in range(n):
            block = values[i * k:(i + 1) * k]
            shown = block[0]
            varies = ("" if all(v == block[0] for v in block) else
                      f" (currently varies by nozzle variant: "
                      f"{', '.join(block)})")
            who = names[i] if i < len(names) else f"slot {i + 1}"
            fields.append(f"""
<div class="setting-row">
  <label for="f-{esc(key)}__{i}">{esc(label)} for filament {i + 1} of
  {n} - {esc(who)}{(' (' + esc(unit) + ')') if unit else ''}{esc(varies)}</label>
  {_control_html(key, shown, _number_or_text(shown), None,
                 field_name=f"{key}__{i}")}
</div>""")
        sections.append(f"<h3>{esc(label)}</h3><p class='hint'>"
                        f"{esc(help_text)} Setting key: {esc(key)}</p>"
                        + "".join(fields))

    # Filament-to-nozzle maps (dual-nozzle printers), one per plate.
    plates = session.plates()
    multi_plate = len(plates) > 1
    map_sections = []
    for plate_entry in (plates or [{"id": None, "name": ""}]):
        pid = plate_entry["id"]
        mode, fmap = session.filament_map(pid)
        if not fmap:
            continue
        staged = next((c for c in session.pending
                       if c["kind"] == "filament_map"
                       and c.get("plate_id") == pid), None)
        shown_map = staged["new"] if staged else fmap
        prefix = f"map__{pid}__" if multi_plate else "map__"
        rows = []
        for i, v in enumerate(shown_map):
            who = names[i] if i < len(names) else f"Filament {i + 1}"
            left = " checked" if v == "1" else ""
            right = " checked" if v == "2" else ""
            rows.append(f"""
<fieldset class="map-row">
  <legend>Filament {i + 1} - {esc(who)}</legend>
  <label class="radio-row"><input type="radio" name="{prefix}{i}"
    value="1"{left}> Left nozzle</label>
  <label class="radio-row"><input type="radio" name="{prefix}{i}"
    value="2"{right}> Right nozzle</label>
</fieldset>""")
        title = ("Which nozzle prints each filament"
                 + (f" on plate {esc(pid)}" if multi_plate else ""))
        map_sections.append(f"""
<h3>{title}</h3>
<p class="hint">Current mapping mode: {esc(mode or 'unknown')}. Saving a
change here always sets the mode to Manual - the only mode
{esc(getattr(session, 'slicer_name', 'the slicer'))}
reliably respects.</p>
{''.join(rows)}""")
    map_html = "".join(map_sections)

    # Which project filament each object uses - the shared chooser,
    # at the very top of the tab.
    obj_html = _object_filament_html(session)

    # Everything filament-scoped that the curated fields above miss.
    more_keys = [
        key for key in sorted(session.settings)
        if key not in session.catalog.CURATED_KEYS
        and session.scope_of(key) == "filament"
    ]
    more_html = ""
    if more_keys:
        rows = "".join(_setting_link(session, key) for key in more_keys)
        more_html = f"""
<h3>All other filament settings ({len(more_keys)})</h3>
<p class="hint">Everything else about material behaviour. Each opens its
own edit page with one field per filament slot.</p>
<ul class="plain-list">{rows}</ul>"""

    flushing_link = ""
    if session.catalog.FLUSH_MATRIX_KEY in session.settings:
        flushing_link = (
            '<h3>Flushing and prime tower</h3>'
            '<p><a class="button" href="/project/flushing">Flushing '
            "volumes and prime tower - how much is purged at each "
            "filament change, and where the waste goes</a></p>")

    inner = f"""
<form method="post" action="/project/filaments">
  {obj_html}
  {''.join(sections)}
  {map_html}
  <button class="primary" id="btn-apply-filaments">Apply changes on this
  tab</button>
  <p class="hint">Applying stages your edits for review; nothing is
  written until you press Save all changes.</p>
</form>
{flushing_link}
{more_html}"""
    return _editor_page(session, "filaments", inner, msg=msg)


def render_flushing_page(session, msg=None):
    """Flushing volumes and prime tower - the friendly matrix editor.

    flush_volumes_matrix holds one N x N grid of purge volumes PER
    EXTRUDER, flattened (N = project filaments): the value at row i,
    column j is how much is flushed when changing FROM filament i TO
    filament j. The diagonal is always 0 and is not editable. Verified
    against the committed H2C test project (2 extruders x 4 x 4 = 32).
    """
    names = session.filament_names()
    n = session.filament_count()
    matrix = session.effective_setting(session.catalog.FLUSH_MATRIX_KEY)
    title = "Flushing and prime tower"

    legend_html = ""
    grid_fields = []
    if not isinstance(matrix, list) or not n or len(matrix) % (n * n):
        legend_html = (
            "<p>The flushing table in this project has an unexpected "
            "shape, so the friendly editor stays out of the way. The raw "
            f'value is editable on <a href="/project/setting/'
            f'{esc(session.catalog.FLUSH_MATRIX_KEY)}">its own page</a>.</p>')
        extruders = 0
    else:
        extruders = len(matrix) // (n * n)
        who = "".join(
            f"<li>Filament {i + 1}: "
            f"{esc(names[i] if i < len(names) else '(unnamed)')}</li>"
            for i in range(n))
        legend_html = f"<ul class='plain-list'>{who}</ul>"
        if n == 1:
            legend_html += ("<p>This project uses a single filament, so "
                            "there are no filament changes to flush.</p>")
        for e in range(extruders):
            if extruders > 1:
                # Extruder order matches the filament map: 1 = left.
                side = "left" if e == 0 else "right"
                grid_fields.append(f"<h3>Nozzle {e + 1} ({side})</h3>")
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    v = matrix[e * n * n + i * n + j]
                    fid = f"flush__{e}_{i}_{j}"
                    grid_fields.append(f"""
<div class="setting-row">
  <label for="f-{fid}">From filament {i + 1} to filament {j + 1}
  (cubic mm)</label>
  {_control_html(session.catalog.FLUSH_MATRIX_KEY, v, "number", None, field_name=fid)}
</div>""")

    multiplier = session.effective_setting(session.catalog.FLUSH_MULTIPLIER_KEY)
    mult_html = ""
    if isinstance(multiplier, list) and multiplier:
        fields = []
        for e, v in enumerate(multiplier):
            side = ("" if len(multiplier) == 1 else
                    (" (left)" if e == 0 else " (right)"))
            fid = f"flushmult__{e}"
            fields.append(f"""
<div class="setting-row">
  <label for="f-{fid}">Flush multiplier, nozzle {e + 1}{side}</label>
  {_control_html(session.catalog.FLUSH_MULTIPLIER_KEY, v, "number", None,
                 field_name=fid)}
</div>""")
        mult_html = ("<h3>Flush multiplier</h3><p class='hint'>Scales "
                     "every volume in the table above. 1 is neutral; "
                     "lower saves material but risks colour bleed.</p>"
                     + "".join(fields))

    def staged_note(key):
        staged = any(c["kind"] == "setting" and c["key"] == key
                     for c in session.pending)
        return (' <span class="staged">changed, not saved yet</span>'
                if staged else "")

    toggles = []
    for key, label, help_text in session.catalog.FLUSH_TOGGLES:
        if key not in session.settings:
            continue
        value = session.effective_setting(key)
        toggles.append(f"""
<div class="setting-row">
  <label for="f-{esc(key)}">{esc(label)}{staged_note(key)}</label>
  <p class="hint" id="h-{esc(key)}">{esc(help_text)}
     <span class="raw-key">Setting key: {esc(key)}</span></p>
  {_control_html(key, value, "checkbox", None, described_by=f"h-{key}")}
</div>""")
    toggles_html = ("<h3>Where the waste goes</h3>" + "".join(toggles)
                    if toggles else "")

    prime_rows = []
    for key, label, help_text, unit, control, options in session.catalog.PRIME_TOWER:
        if key not in session.settings:
            continue
        value = session.effective_setting(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        unit_text = f" ({unit})" if unit else ""
        prime_rows.append(f"""
<div class="setting-row">
  <label for="f-{esc(key)}">{esc(label)}{esc(unit_text)}{staged_note(key)}</label>
  <p class="hint" id="h-{esc(key)}">{esc(help_text)}
     <span class="raw-key">Setting key: {esc(key)}</span></p>
  {_control_html(key, value, control, options, described_by=f"h-{key}")}
</div>""")
    prime_html = ("<h3>Prime tower</h3>" + "".join(prime_rows)
                  if prime_rows else "")

    staged_grid_note = ""
    if staged_note(session.catalog.FLUSH_MATRIX_KEY) or staged_note(session.catalog.FLUSH_MULTIPLIER_KEY):
        staged_grid_note = ('<p class="unsaved-note">The flushing values '
                            "below include staged changes, not saved "
                            "yet.</p>")

    body = f"""
<h1>{esc(title)}</h1>
<p>When the printer changes filament, it purges the old colour before
the new one prints cleanly. This page controls how much is purged for
each change, and what the waste is used for.</p>
{legend_html}
{staged_grid_note}
<form method="post" action="/project/flushing">
  {''.join(grid_fields)}
  {mult_html}
  {toggles_html}
  {prime_html}
  <button class="primary" id="btn-apply-flushing">Apply changes on this
  page</button>
  <p class="hint">Applying stages your edits for review; nothing is
  written until you press Save all changes.</p>
</form>
<p><a href="/project/filaments">Back to the Filaments tab</a></p>
{_bottom_bar(session, return_to="/project/flushing")}
"""
    return page(title, body, msg=msg, project=session.path.name)


def render_limits_page(slicer_name="the slicer", texts=None):
    """The honest-limits page: what AccessSlice cannot edit, and the way
    around each limit. Linked from Home; needs no open project. The
    sections are the backend's - which data is not text is a per-slicer
    fact."""
    if texts is not None and hasattr(texts, "limits_body_html"):
        sections = texts.limits_body_html(esc(slicer_name))
    else:
        sections = ("<p>AccessSlice works on the text inside a saved "
                    "project file; anything the slicer does not store "
                    "as text cannot be edited here.</p>")
    body = ("<h1>What AccessSlice cannot edit - and what to do "
            "instead</h1>"
            + sections
            + '\n<p><a href="/">Back to Home</a></p>\n')
    return page("What AccessSlice cannot edit", body)


def _preset_select(field_id, label_text, current, options, hint=None):
    """A labelled dropdown of preset names, current value always present."""
    opts = list(options)
    if current and current not in opts:
        opts.insert(0, current)
    rendered = "".join(
        f'<option value="{esc(name)}"'
        + (" selected" if name == current else "")
        + f">{esc(name)}</option>"
        for name in opts
    )
    hint_html = f'<p class="hint">{esc(hint)}</p>' if hint else ""
    return f"""
<div class="setting-row">
  <label for="{esc(field_id)}">{esc(label_text)}</label>
  {hint_html}
  <select id="{esc(field_id)}" name="{esc(field_id)}">{rendered}</select>
</div>"""


def _search_form(action, field_name, label_text, value):
    """A small GET form that re-renders the page with search results."""
    return f"""
<form method="get" action="{esc(action)}">
  <p><label for="{esc(field_name)}">{esc(label_text)}</label><br>
  <input type="text" id="{esc(field_name)}" name="{esc(field_name)}"
         value="{esc(value)}">
  <button>Search</button></p>
</form>"""


def render_presets_tab(session, view, msg=None):
    """Printer and presets: switch printer, print profile, and each
    filament preset - showing the user's OWN short lists, with search
    for the rare new preset (the full library lists were overwhelming -
    Edis, 2026-08-26).

    view: {"process": [...],
           "filament_options": [...], "filament_matches": None | [...],
           "filament_search": "",
           "machine_options": [...], "machine_matches": None | [...],
           "printer_search": ""}
    Options are the presets in this project plus everything the user has
    used before; matches (when a search ran) are appended to the selects.
    """
    names = session.filament_names()
    n = session.filament_count()

    # Show staged preset switches as the current choice right away,
    # marked as not saved yet - the tab must never look like a choice
    # did nothing.
    def with_staged(saved, scope, filament_index=None):
        staged = session.staged_preset(scope, filament_index)
        if staged and staged != saved:
            return staged, f"{staged} (staged, not saved)"
        return saved, saved

    printer, printer_shown = with_staged(
        session.settings.get("printer_settings_id", "(unknown)"), "machine")
    process, process_shown = with_staged(
        session.settings.get("print_settings_id", "(unknown)"), "process")
    saved_fil_ids = session.settings.get("filament_settings_id") or []
    fil_ids, fil_shown = [], []
    for i in range(n):
        saved = saved_fil_ids[i] if i < len(saved_fil_ids) else ""
        current, shown = with_staged(saved, "filament", i)
        fil_ids.append(current)
        fil_shown.append(shown)

    fil_opts = list(view.get("filament_options", []))
    for m in view.get("filament_matches") or []:
        if m not in fil_opts:
            fil_opts.append(m)
    mach_opts = list(view.get("machine_options", []))
    for m in view.get("machine_matches") or []:
        if m not in mach_opts:
            mach_opts.append(m)

    def result_note(matches, search, what):
        if matches is None:
            return ""
        if matches:
            return (f"<p>{len(matches)} {what} preset(s) matching "
                    f"{esc(search)} were added to the lists below.</p>")
        return f"<p>No {what} preset matched {esc(search)}.</p>"

    filament_selects = "".join(
        _preset_select(
            f"filament_preset__{i}",
            f"Filament {i + 1} of {n} - currently "
            f"{fil_shown[i] or '(none)'}",
            fil_ids[i],
            fil_opts,
        )
        for i in range(n)
    )

    library_note = ""
    if not view.get("process") and not fil_opts:
        library_note = ("<p class='hint'>The slicer's preset library was "
                        "not found next to the configured slicer program, "
                        "so only the current presets are shown. Check the "
                        "slicer path on the Settings page.</p>")

    inner = f"""
<p>These are the same presets your slicer offers - the system library
plus any presets you saved yourself in the slicer. Picking one here
stages the switch; save to make it real. Any individually changed
settings of that scope are reset to the new preset's values, exactly as
in the slicer.</p>
<p class="hint">The lists show the presets in this project plus the ones
you have used before. Anything else in the slicer's library is found
with the search fields, and joins your lists once used.</p>
{library_note}
<form method="post" action="/project/presets">
  <h3>Print profile</h3>
  {_preset_select("process_preset",
                  f"Print profile - currently {process_shown}",
                  process, view.get("process", []),
                  hint="Layer height, speeds and quality as one package, "
                       "e.g. 0.20mm Standard.")}
  <h3>Filaments</h3>
  {result_note(view.get("filament_matches"),
               view.get("filament_search", ""), "filament")}
  {filament_selects}
  {_object_filament_html(session)}
  <h3>Printer</h3>
  {result_note(view.get("machine_matches"),
               view.get("printer_search", ""), "printer")}
  {_preset_select("machine_preset",
                  f"Printer - currently {printer_shown}",
                  printer, mach_opts,
                  hint="Changing the printer is a big move: profiles and "
                       "filament compatibility follow it. Usually the "
                       "printer chosen in the slicer is already right.")}
  <button class="primary" id="btn-apply-presets">Apply changes on this
  tab</button>
  <p class="hint">Applying stages your choices for review; nothing is
  written until you press Save all changes.</p>
</form>
<h3>Find more presets</h3>
{_search_form("/project/presets", "filament_search",
              "Search the full filament preset list", "")}
{_search_form("/project/presets", "printer_search",
              "Search the full printer list", "")}"""
    return _editor_page(session, "presets", inner, msg=msg)


def render_infill_question(session, return_to, msg=None):
    """Bambu Studio's 100%-infill dialog, as an accessible page.

    Mirrors Studio's own wording and its two actions exactly (screenshot
    2026-08-26): Yes switches the pattern to rectilinear (stored value
    zig-zag), No resets the density to the print profile's default.
    Yes is focused first, like Studio's dialog.
    """
    conflict = session.infill_conflict()
    if not conflict:
        return page("Infill question", "<h1>Infill question</h1>"
                    "<p>The infill combination is fine now - nothing to "
                    f"answer.</p><p><a href='{esc(return_to)}'>Back to "
                    "the editor</a></p>")
    default = session.default_infill_density()
    pattern = conflict["pattern"].capitalize()
    body = f"""
<h1>Question from the slicer</h1>
<p>{esc(pattern)} infill pattern doesn't support 100% density.<br>
Switch to rectilinear pattern?</p>
<form method="post" action="/project/infill-answer">
  <input type="hidden" name="return_to" value="{esc(return_to)}">
  <p><button class="primary" name="answer" value="yes" autofocus>
    Yes - switch to rectilinear pattern</button></p>
  <p><button name="answer" value="no">
    No - reset density to default non 100% value ({esc(default)})
  </button></p>
</form>
<p class="hint">This is the same question
{esc(getattr(session, 'slicer_name', 'the slicer'))} asks in this
situation. Your answer is staged like any other change - nothing is
written until you save.</p>
"""
    return page("Infill question - " + session.path.name, body,
                msg=msg, project=session.path.name)


def render_unit_question(session, key, return_to, msg=None):
    """Bambu Studio's mm-or-% validation dialog, as an accessible page.

    Mirrors "Parameter validation: <key>" exactly (screenshot
    2026-08-26): "Is it N% or N mm? YES for N%, NO for N mm."
    """
    conflict = session.unit_conflict()
    if not conflict or conflict["key"] != key:
        return page("Parameter validation",
                    "<h1>Parameter validation</h1><p>Nothing to clear "
                    f"up - the value is unambiguous.</p><p><a "
                    f"href='{esc(return_to)}'>Back to the editor</a></p>")
    v = conflict["value"]
    body = f"""
<h1>Parameter validation: {esc(session.catalog.label_for(key))}</h1>
<p>Is it {esc(v)}% or {esc(v)} mm?<br>
YES for {esc(v)}%, NO for {esc(v)} mm.</p>
<form method="post" action="/project/unit-answer">
  <input type="hidden" name="key" value="{esc(key)}">
  <input type="hidden" name="return_to" value="{esc(return_to)}">
  <p><button class="primary" name="answer" value="yes" autofocus>
    Yes - it is {esc(v)} percent</button></p>
  <p><button name="answer" value="no">
    No - it is {esc(v)} millimetres</button></p>
</form>
<p class="hint">This is the same question
{esc(getattr(session, 'slicer_name', 'the slicer'))} asks when a
large plain number lands in a field that takes millimetres or a
percentage. Your answer is staged like any other change.</p>
"""
    return page("Parameter validation - " + session.path.name, body,
                msg=msg, project=session.path.name)


def render_placement_tab(session, msg=None):
    """Placement: object positions in words plus editable X/Y fields.

    Multi-plate projects group the objects under one heading per
    plate, with positions in that PLATE's own coordinates (X 0 to
    width, front-left corner is 0,0) - the numbers a person actually
    thinks in. A move-to-plate chooser appears when the plate layout
    read reliably.
    """
    plate = session.plate_size()
    plate_note = (f"The build plate is {plate[0]:g} by {plate[1]:g} mm; "
                  f"its centre is at X {plate[0] / 2:g}, Y {plate[1] / 2:g}."
                  if plate else
                  "Plate size unknown for this printer profile.")
    plates = session.plates()
    multi = len(plates) > 1
    grid_ok = session.plate_grid()["reliable"]
    plates_ok = bool(getattr(session, "supports_plates", False))
    plate_of = {oid: p["id"] for p in plates for oid in p["object_ids"]}
    origins = {p["id"]: session.plate_origin(p["id"]) for p in plates}

    def object_fieldset(o):
        staged = next((c for c in session.pending
                       if c["kind"] == "move" and c["objectid"] == o["id"]),
                      None)
        x, y = staged["new"] if staged else (o["x"], o["y"])
        # Show positions in the object's own plate's coordinates.
        origin = origins.get(plate_of.get(o["id"])) or (0.0, 0.0)
        x, y = x - origin[0], y - origin[1]
        where = _describe_position(x, y, plate)
        facts = session.object_transform_facts(o["id"])
        transform_html = ""
        if facts:
            # Staged rotations and scalings must be VISIBLE before the
            # save, like every other staged change - showing only the
            # saved state read as "typing 45 did nothing" (Edis,
            # 2026-08-27).
            staged_rot = next(
                (c for c in session.pending if c["kind"] == "rotate"
                 and c["objectid"] == o["id"]), None)
            staged_scale = next(
                (c for c in session.pending if c["kind"] == "scale"
                 and c["objectid"] == o["id"]), None)
            if facts["z_rotation_degrees"] is not None:
                base_deg = facts["z_rotation_degrees"]
                rot_now = (f"Currently rotated {base_deg:.1f} degrees "
                           "around the vertical axis.")
                if staged_rot:
                    after = (base_deg + staged_rot["degrees"]) % 360
                    rot_now += (f" A turn by {staged_rot['degrees']:g} "
                                "degrees is staged, not saved - saving "
                                f"makes it {after:.1f} degrees.")
            else:
                rot_now = ("This object carries a 3D rotation (from the "
                           "slicer, or from auto-orient); the rotate "
                           "field below still turns it further around "
                           "the vertical axis.")
                if staged_rot:
                    rot_now += (f" A turn by {staged_rot['degrees']:g} "
                                "degrees is staged, not saved.")
            if session.orient_is_drop(o["id"]):
                rot_now += (" Setting it down onto the plate is staged, "
                            "not saved.")
            elif any(c["kind"] == "orient" and c["objectid"] == o["id"]
                     for c in session.pending):
                rot_now += (" An automatic turn (most printable side "
                            "down) is staged, not saved.")
            if staged_scale:
                size_now = str(staged_scale["new"]).rstrip("%")
                rot_now += (f" A size change to {staged_scale['new']} "
                            "is staged, not saved (the saved size is "
                            f"{facts['scale_percent']:.1f}).")
            else:
                size_now = f"{facts['scale_percent']:.1f}"
            oid = esc(o["id"])
            transform_html = f"""
  <p>{esc(rot_now)}</p>
  <div class="setting-row">
    <label for="f-rot__{oid}">Rotate by degrees (counterclockwise seen
    from above; 0 means no change)</label>
    <input type="text" inputmode="decimal" id="f-rot__{oid}"
           name="rot__{oid}" value="0" size="10">
  </div>
  <div class="setting-row">
    <label for="f-scale__{oid}">Size in percent of the imported model
    (currently {esc(size_now)}; 100 is the original size)</label>
    <input type="text" inputmode="decimal" id="f-scale__{oid}"
           name="scale__{oid}" value="{esc(size_now)}" size="10">
    <input type="hidden" name="basescale__{oid}" value="{esc(size_now)}">
  </div>"""
        replate_html = ""
        current_pid = plate_of.get(o["id"])
        staged_new = next((c for c in session.pending
                           if c["kind"] == "replate_new"
                           and c["objectid"] == o["id"]), None)
        if plates_ok and grid_ok and current_pid is not None:
            options = "".join(
                f'<option value="{esc(p["id"])}"'
                + (" selected" if p["id"] == current_pid
                   and not staged_new else "")
                + f'>Plate {esc(p["id"])}'
                + (f' - {esc(p["name"])}' if p["name"] else "")
                + (" (current)" if p["id"] == current_pid else "")
                + "</option>"
                for p in plates)
            options += ('<option value="new"'
                        + (" selected" if staged_new else "")
                        + ">A new plate (added when you save)</option>")
            replate_html = f"""
  <div class="setting-row">
    <label for="f-replate__{esc(o['id'])}">Move to plate (the object is
    centred on the plate you pick){" - a move to a new plate is staged, not saved" if staged_new else ""}</label>
    <select id="f-replate__{esc(o['id'])}" name="replate__{esc(o['id'])}">
      {options}
    </select>
  </div>"""
        removed = o["id"] in session.removed_ids()
        staged_copies = next((c for c in session.pending
                              if c["kind"] == "copy"
                              and c["objectid"] == o["id"]), None)
        if removed:
            ops_html = (f'<p class="unsaved-note">Removal of {esc(o["name"])} '
                        "is staged, not saved - discard it on Review "
                        "changes to keep the object.</p>")
        else:
            copies_note = (f" {staged_copies['count']} copies are staged, "
                           "not saved." if staged_copies else "")
            ops_html = f"""
  <div class="setting-row">
    <label for="f-copies__{esc(o['id'])}">Make copies of {esc(o['name'])} -
    how many more (they are laid out with the rest of the plate{", spilling onto new plates when they do not fit" if plates_ok else ""}){esc(copies_note)}</label>
    <input type="text" inputmode="numeric" id="f-copies__{esc(o['id'])}"
           name="copies__{esc(o['id'])}" value="1" size="4">
    <button name="copy" value="{esc(o['id'])}">Make the copies of
    {esc(o['name'])}</button>
  </div>
  <button name="remove" value="{esc(o['id'])}">Remove {esc(o['name'])}
  from the project (staged; discard on Review changes to undo)</button>"""
        return f"""
<fieldset data-objectid="{esc(o['id'])}">
  <legend>{esc(o['name'])} (object {esc(o['id'])})</legend>
  <p>{esc(where)}</p>
  <p><button type="button" class="view-select" hidden
     data-objectid="{esc(o['id'])}" aria-pressed="false">Show
     {esc(o['name'])} in the 3D view</button>
     <span class="view-note" data-view-note="{esc(o['id'])}"></span></p>
  <div class="setting-row">
    <label for="f-x__{esc(o['id'])}">Centre X in millimetres</label>
    <input type="text" inputmode="decimal" id="f-x__{esc(o['id'])}"
           name="x__{esc(o['id'])}" value="{x:g}" size="10">
  </div>
  <div class="setting-row">
    <label for="f-y__{esc(o['id'])}">Centre Y in millimetres</label>
    <input type="text" inputmode="decimal" id="f-y__{esc(o['id'])}"
           name="y__{esc(o['id'])}" value="{y:g}" size="10">
  </div>
  {transform_html}
  {replate_html}
  {ops_html}
  {"<button name='centre' value='" + esc(o['id']) + "'>Centre "
   + esc(o['name']) + " on its plate</button>" if plate else ""}
  <button name="orient" value="{esc(o['id'])}">Auto-orient
  {esc(o['name'])} - turn its most printable side onto the plate</button>
  <p><a href="/project/object/{esc(o['id'])}">Settings for
  {esc(o['name'])} only - override project settings for just this
  object{_override_count_note(session, o['id'])}</a></p>
</fieldset>"""

    def auto_buttons(pid):
        spill = (f"""
  <button name="arrange_spill" value="{esc(pid)}">Arrange this plate,
  moving what does not fit onto new plates</button>""" if plates_ok
                 else "")
        return f"""
  <button name="arrange" value="{esc(pid)}">Arrange this plate
  automatically - give every object its own spot</button>{spill}
  <button name="orient_plate" value="{esc(pid)}">Auto-orient every
  object on this plate</button>"""

    def add_model_html(pid):
        staged = [c for c in session.pending if c["kind"] == "add_model"]
        note = ("".join(f" {esc(c['label'])} is staged, not saved."
                        for c in staged))
        return f"""
  <div class="setting-row">
    <label for="f-addmodel__{esc(pid)}">Add a model file to this plate -
    the full path of an STL, STEP, OBJ or 3MF file (it is laid out with
    the rest of the plate{", spilling onto new plates when it does not fit" if plates_ok else ""}){note}</label>
    <input type="text" id="f-addmodel__{esc(pid)}" name="addmodel__{esc(pid)}"
           value="" size="60">
    <button name="add_model" value="{esc(pid)}">Add the model file to
    this plate</button>
  </div>"""

    objs = session.objects()
    if not multi:
        pid = (plates[0]["id"] if plates else "") or "all"
        rows = (auto_buttons(pid) + add_model_html(pid)
                + "".join(object_fieldset(o) for o in objs))
    else:
        by_plate = {}
        loose = []
        for o in objs:
            pid = plate_of.get(o["id"])
            (by_plate.setdefault(pid, []) if pid else loose).append(o)
        parts = []
        for p in plates:
            members = by_plate.get(p["id"], [])
            title = f"Plate {esc(p['id'])}"
            if p["name"]:
                title += f" - {esc(p['name'])}"
            body = ("".join(object_fieldset(o) for o in members)
                    or "<p>Nothing on this plate.</p>")
            buttons = ""
            if members:
                # Arranging needs the reliable plate grid; orienting
                # keeps each object's spot, so it works regardless.
                buttons = (auto_buttons(p["id"]) if grid_ok else f"""
  <button name="orient_plate" value="{esc(p['id'])}">Auto-orient every
  object on this plate</button>""")
            parts.append(f"<h3>{title}</h3>{buttons}"
                         f"{add_model_html(p['id'])}{body}")
        if loose:
            parts.append("<h3>Not assigned to a plate</h3>"
                         + "".join(object_fieldset(o) for o in loose))
        rows = "".join(parts)
        coord_note = ("Positions are given per plate: X 0 to "
                      f"{plate[0]:g} and Y 0 to {plate[1]:g} on each "
                      "plate." if plate and grid_ok else "")
        if coord_note:
            plate_note += " " + coord_note

    auto_note = (
        "Two automatic commands are available. Arrange gives every "
        f"object on a plate its own spot with at least "
        f"{geometry.ARRANGE_GAP_MM:g} mm of air in between - the cure "
        "when imported parts sit on top of each other. Auto-orient "
        "turns an object so its most printable side lies flat on the "
        "plate. Both can take a few seconds on detailed models, and "
        "both only stage changes - nothing is written until you save.")
    inner = f"""
<div class="with-view">
<div class="view-fields">
<p>{esc(plate_note)} <a href="#view-panel">Jump to the 3D view.</a></p>
<p>{esc(auto_note)}</p>
<form method="post" action="/project/placement">
  {rows}
  <button class="primary" id="btn-apply-placement">Apply changes on this
  tab</button>
</form>
</div>
{_view_panel(session)}
</div>"""
    return _editor_page(session, "placement", inner, msg=msg, wide=True,
                        scripts=("three.min.js", "viewport.js"))


def _view_panel(session):
    """The 3D view of the plate, beside the Placement fields.

    A mirror, never the master: everything drawn here exists as a
    real control on the tab. The stage (the canvas) is hidden from
    screen readers; what they meet is this heading, the summary
    sentence, the plate chooser, the view buttons and the status
    line - all real HTML. The summary is rendered without reading
    any mesh (cheap on a 36-plate project); the script adds the
    edge-overhang facts once the shapes are loaded.
    """
    choices = viewport.plate_choices(session)
    chooser = ""
    if choices:
        options = "".join(
            f'<option value="{esc(pid)}"'
            + (" selected" if pid == viewport.default_plate(session) else "")
            + f">{esc(label)}</option>" for pid, label in choices)
        chooser = f"""
    <label for="view-plate">Plate shown</label>
    <select id="view-plate">{options}
      <option value="all">All plates (slower on big projects)</option>
    </select>"""
    summary = viewport.describe(_cheap_scene(session))
    return f"""
<section class="view-panel" id="view-panel" aria-labelledby="view-heading"
         data-scene-url="/api/scene" data-project="{esc(session.path.name)}">
  <h2 id="view-heading">3D view</h2>
  <p class="view-note" id="view-summary">{esc(summary)}</p>
  <div class="view-toolbar">{chooser}
    <button type="button" id="view-fit">Fit to plate</button>
    <button type="button" id="view-top">From above</button>
    <button type="button" id="view-front">From the front</button>
    <button type="button" id="view-angle">From the front left</button>
    <button type="button" id="view-zoom-in">Zoom in</button>
    <button type="button" id="view-zoom-out">Zoom out</button>
  </div>
  <div class="view-stage" id="view-stage" aria-hidden="true"></div>
  <p class="view-status" id="view-status" role="status"></p>
  <ul class="legend-list" id="view-legend" aria-label="Colours in the 3D view"></ul>
  <p class="view-note">With a mouse: drag on empty space to turn the
  view, use the wheel to zoom, right-drag to slide, click an object to
  select it, and drag an object to move it - that stages the same move
  the X and Y fields do, for review and saving like any other change.
  The buttons above and the fields beside the view do everything the
  mouse does, so nothing here needs the picture.</p>
</section>"""


def _cheap_scene(session):
    """The scene facts that cost no mesh reading, for the summary
    sentence at page-render time."""
    plates = session.plates()
    pid = viewport.default_plate(session)
    members = set()
    for p in plates:
        if p["id"] == pid:
            members = set(p["object_ids"])
    for c in session.pending:
        if c.get("kind") == "replate":
            if c["target"] == pid:
                members.add(c["objectid"])
            else:
                members.discard(c["objectid"])
    objs = [{"name": o["name"], "off_plate": False}
            for o in session.objects()
            if o["id"] in members or not plates]
    return {"objects": objs, "all_plates": False, "shown_plate": pid}


def _override_count_note(session, objectid):
    n = len(session.effective_object_overrides(objectid))
    if not n:
        return ""
    return f" ({n} override{'s' if n != 1 else ''} set)"


def render_object_page(session, objectid, q="", msg=None):
    """Per-object settings: the accessible version of Studio's object
    panel (which is mouse-only - the reason this page exists).

    An override applies to this one object; everything else keeps the
    project-wide value. Existing overrides are editable and removable;
    a curated shortlist plus a search over every scalar setting lets
    new overrides be added. All staged and saved like any other change.
    """
    objs = {o["id"]: o for o in session.objects()}
    o = objs[objectid]
    overrides = session.effective_object_overrides(objectid)

    def row(key, value, note=""):
        control, options = session.catalog.control_for(key, value)
        entry = session.catalog.curated_entry(key)
        help_text = entry[2] if entry else ""
        unit = entry[3] if entry else None
        bits = [help_text or "", f"Unit: {unit}." if unit else "", note]
        hint = " ".join(b for b in bits if b)
        hint_id = f"h-obj-{key}"
        hint_html = (f'<p class="hint" id="{hint_id}">{esc(hint)}</p>'
                     if hint else "")
        input_html = _control_html(key, value, control, options,
                                   field_name=f"obj__{key}",
                                   described_by=hint_id if hint else None)
        return f"""
<div class="setting-row">
  <label for="f-obj__{esc(key)}">{esc(session.catalog.label_for(key))}
    <span class="raw-key">({esc(key)})</span></label>
  {input_html}
  <input type="hidden" name="base__obj__{esc(key)}"
         value="{esc(str(value))}">
  {hint_html}
</div>"""

    def companion_note(key):
        comps = [c for c in session.catalog.OBJECT_COMPANION_KEYS.get(key, ())
                 if c in session.settings]
        if not comps:
            return ""
        return ("Changing this also sets " + " and ".join(comps)
                + " for this object, exactly as the slicer does.")

    # Section 1: overrides already on this object.
    if overrides:
        current_rows = "".join(
            row(k, v, note=companion_note(k))
            for k, v in sorted(overrides.items()))
        current_html = f"""
<h3>Overrides now on this object</h3>
<p>These settings differ from the project settings for
{esc(o['name'])} alone.</p>
{current_rows}"""
        remove_rows = "".join(
            "<li>" + _button_form(
                f"/project/object/{esc(objectid)}",
                f"Remove the {session.catalog.label_for(k)} override - back to the "
                f"project value {session.effective_setting(k)}",
                hidden={"remove": k}) + "</li>"
            for k in sorted(overrides))
        remove_html = f"""
<h3>Remove an override</h3>
<ul class="plain-list">{remove_rows}</ul>"""
    else:
        current_html = ("<p>No overrides yet - this object uses the "
                        "project settings for everything.</p>")
        remove_html = ""

    # Section 2: the curated shortlist, prefilled with project values.
    shortlist = "".join(
        row(key, session.effective_setting(key),
            note=companion_note(key))
        for key in session.catalog.OBJECT_CURATED_KEYS
        if key in session.settings and key not in overrides
        and not isinstance(session.settings[key], list))

    # Section 3: search results over every scalar setting.
    results_html = ""
    if q:
        terms = q.lower().split()
        matches = []
        shown = set(overrides) | set(session.catalog.OBJECT_CURATED_KEYS)
        for key, value in sorted(session.settings.items()):
            if key in shown or isinstance(value, list):
                continue
            text = (key + " " + session.catalog.label_for(key)).lower()
            if all(t in text for t in terms):
                matches.append(key)
        rows_html = "".join(
            row(key, session.effective_setting(key)) for key in
            matches[:20])
        more = (f"<p>Only the first 20 of {len(matches)} matches are "
                "shown - refine the search.</p>"
                if len(matches) > 20 else "")
        results_html = f"""
<h3>Search results for {esc(q)}</h3>
{rows_html or "<p>No scalar settings match that search.</p>"}
{more}"""

    body = f"""
<h1>Object settings - {esc(o['name'])}</h1>
<p>Overrides set here apply to {esc(o['name'])} ONLY; every other
object keeps the project settings. This is AccessSlice's accessible
version of the slicer's per-object settings panel. Changes stage like
everything else - review and save when ready.</p>
<h2 id="tab-heading" tabindex="-1">Overrides</h2>
<form method="post" action="/project/object/{esc(objectid)}">
{current_html}
<h3>Set a setting for this object only</h3>
<p>Fields below show the project value; change one to give
{esc(o['name'])} its own value instead.</p>
{shortlist}
{results_html}
<button class="primary" id="btn-apply-object">Apply object
settings</button>
</form>
{remove_html}
<h3>Find any other setting to override</h3>
<form method="get" action="/project/object/{esc(objectid)}"
      class="inline-form">
  <label for="f-obj-search">Search all settings</label>
  <input type="text" id="f-obj-search" name="q" value="{esc(q)}">
  <button>Search</button>
</form>
<p><a href="/project/placement">Back to Placement</a></p>
{_bottom_bar(session, return_to=f"/project/object/{objectid}")}
"""
    return page(f"Object settings - {o['name']}", body, msg=msg,
                project=session.path.name)


def _describe_position(x, y, plate):
    """A position in words: 'centre of the plate' or offsets from centre."""
    if not plate:
        return f"Position: X {x:g} mm, Y {y:g} mm."
    cx, cy = plate[0] / 2, plate[1] / 2
    dx, dy = x - cx, y - cy
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return "Currently at the centre of the plate."
    parts = []
    if abs(dx) >= 0.5:
        parts.append(f"{abs(dx):g} mm {'right' if dx > 0 else 'left'} of centre")
    if abs(dy) >= 0.5:
        parts.append(f"{abs(dy):g} mm toward the "
                     f"{'back' if dy > 0 else 'front'}")
    outside = ""
    if x < 0 or y < 0 or x > plate[0] or y > plate[1]:
        outside = " WARNING: this is outside the build plate."
    return "Currently " + " and ".join(parts) + "." + outside


def _setting_link(session, key):
    """One 'key - current value' line linking to the setting's editor."""
    value = session.effective_setting(key)
    if isinstance(value, list):
        preview = ", ".join(str(v) for v in value[:3])
        if len(value) > 3:
            preview += f", and {len(value) - 3} more"
        preview += " (list)"
    else:
        preview = str(value)
    if len(preview) > 70:
        preview = preview[:67] + "..."
    label = session.catalog.label_for(key)
    shown_label = (f"{label} ({key})" if label != key else key)
    staged = any(c["kind"] == "setting" and c["key"] == key
                 for c in session.pending)
    staged_note = " - changed, not saved yet" if staged else ""
    return (f'<li><a href="/project/setting/{esc(key)}">{esc(shown_label)}'
            f"</a> - current value: {esc(preview)}{esc(staged_note)}</li>")


# The overview's groups, in reading order, with plain descriptions.
_SCOPE_GROUPS = [
    ("process", "Print process settings",
     "How the model is sliced and printed: layers, walls, infill, "
     "supports, speeds, adhesion."),
    ("filament", "Filament settings",
     "Material behaviour: temperatures, cooling, flow. Most are lists "
     "with values per filament."),
    ("machine", "Printer settings",
     "The machine itself: size, nozzles, extruders, machine G-code. "
     "Rarely needs changing by hand."),
]


def render_all_tab(session, query, msg=None):
    """All settings - the completeness guarantee.

    Without a search: EVERY key in the project, grouped by scope
    (process / filament / printer) under navigable headings.
    With a search: a flat result list; every word must match somewhere
    in the key or its friendly label, so "tree branch" finds
    tree_support_branch_diameter.
    """
    words = (query or "").strip().lower().split()
    search_form = f"""
<form method="get" action="/project/all" role="search">
  <label for="q">Search all {len(session.settings)} settings by name</label>
  <input type="search" id="q" name="q" value="{esc(query or '')}" size="30">
  <button>Search</button>
</form>"""

    if words:
        hits = []
        for key in sorted(session.settings):
            haystack = key.lower() + " " + session.catalog.label_for(key).lower()
            if all(w in haystack for w in words):
                hits.append(key)
        total = len(hits)
        rows = [_setting_link(session, key) for key in hits[:150]]
        count_line = (f"{total} settings match '{query}'."
                      + ("" if total <= 150 else " Showing the first 150 - "
                         "narrow the search to see the rest."))
        inner = (search_form + f"<p>{esc(count_line)}</p>"
                 + f'<ul class="plain-list">{"".join(rows)}</ul>')
        return _editor_page(session, "all", inner, msg=msg)

    # No search: the complete grouped overview.
    by_scope = {"process": [], "filament": [], "machine": []}
    for key in sorted(session.settings):
        scope = session.scope_of(key)
        by_scope.setdefault(scope, []).append(key)

    sections = []
    for scope, heading, description in _SCOPE_GROUPS:
        keys = by_scope.get(scope, [])
        if not keys:
            continue
        rows = "".join(_setting_link(session, key) for key in keys)
        sections.append(
            f"<h3>{esc(heading)} ({len(keys)})</h3>"
            f'<p class="hint">{esc(description)}</p>'
            f'<ul class="plain-list">{rows}</ul>'
        )
    inner = (search_form
             + f"<p>Every setting in this project - all "
               f"{len(session.settings)} of them - grouped below. "
               "Each one opens its own edit page.</p>"
             + "".join(sections))
    return _editor_page(session, "all", inner, msg=msg)


def render_setting_page(session, key, msg=None):
    """One setting's own edit page - covers every key in the file."""
    value = session.effective_setting(key)
    control, options = session.catalog.control_for(key, value)
    label = session.catalog.label_for(key)
    title = f"{label} ({key})" if label != key else key

    if isinstance(value, list):
        # Give each slot the most meaningful name we can: per-filament
        # lists name the filament; filament blocks name filament and
        # nozzle variant; anything else is numbered plainly.
        n = session.filament_count()
        scope = session.scope_of(key)
        length = len(value)

        def slot_label(i):
            if scope == "filament" and n and length == n:
                return f"Filament {i + 1} of {n}"
            if scope == "filament" and n and length % n == 0:
                k = length // n
                return (f"Filament {i // k + 1} of {n}, "
                        f"nozzle variant {i % k + 1} of {k}")
            if length == 2 and scope == "machine":
                return ("Left nozzle" if i == 0 else "Right nozzle")
            return f"Value {i + 1} of {length}"

        fields = "".join(f"""
<div class="setting-row">
  <label for="f-{esc(key)}__{i}">{esc(slot_label(i))}</label>
  {_control_html(key, v, "text", None, field_name=f"{key}__{i}")}
</div>""" for i, v in enumerate(value))
        list_note = ("<p class='hint'>This is a list setting - every slot "
                     "is shown and edited here; the list's length is kept "
                     "in step automatically.</p>")
    else:
        fields = f"""
<div class="setting-row">
  <label for="f-{esc(key)}">New value</label>
  {_control_html(key, value, control, options)}
</div>"""
        list_note = ""

    body = f"""
<h1>Setting: {esc(title)}</h1>
{list_note}
<form method="post" action="/project/setting/{esc(key)}">
  {fields}
  <button class="primary" id="btn-apply-setting">Apply this change</button>
</form>
<p><a href="/project/all">Back to All settings</a></p>
{_bottom_bar(session, return_to=f"/project/setting/{key}")}
"""
    return page(title, body, msg=msg, project=session.path.name)


def render_review(session, msg=None):
    """The pending-changes list: old value, new value, discard controls.

    One change discards with its own button (landing back on the page
    it was staged from). With several waiting there are also per-change
    tick boxes plus "Discard the ticked changes" and "Discard all" -
    both asked for by Edis on 2026-08-31, after one-at-a-time discarding
    proved slow in real use.
    """
    if not session.pending:
        inner = "<p>No staged changes. Everything shown is saved.</p>"
    else:
        n = len(session.pending)
        multi = n > 1
        rows = []
        for i, c in enumerate(session.pending):
            old = _shown_value(c["old"])
            new = _shown_value(c["new"])
            tick = ""
            if multi:
                tick = f"""
  <label class="radio-row"><input type="checkbox" name="drop"
    value="{i}"> Tick to discard: {esc(c['label'])}</label>"""
            rows.append(f"""
<li>
  <strong>{esc(c['label'])}</strong>: from {esc(old)} to {esc(new)}
  <button name="index" value="{i}">Discard this change</button>{tick}
</li>""")
        multi_note = ""
        multi_buttons = ""
        if multi:
            multi_note = ("""
<p class="hint">Discard this change drops one change and returns you
to the page it was staged from. To drop several at once, tick them and
press Discard the ticked changes - or press Discard all.</p>""")
            multi_buttons = f"""
<p>
  <button name="selected" value="1">Discard the ticked changes</button>
  <button name="all" value="1">Discard all {n} staged changes</button>
</p>"""
        inner = f"""
<p>These changes are staged and will be written when you press
Save all changes:</p>{multi_note}
<form method="post" action="/project/discard">
<ol class="review-list">{''.join(rows)}</ol>
{multi_buttons}
</form>"""
    return _editor_page(session, "review", inner, msg=msg)


def render_backups_tab(session, msg=None):
    """The restore-a-previous-version page.

    Every save first copies the outgoing version into the project's
    backups folder; this page lists those copies newest first, each
    with its own restore button. A restore backs up the current
    version too, so it can itself be undone - stated right here so the
    button is never a leap of faith.
    """
    backups = session.list_backups()
    if not backups:
        inner = ("<p>No backups yet. The first time you save a change, "
                 "the version being replaced is stored here "
                 "automatically.</p>")
    else:
        pending_note = ""
        if session.pending:
            n = len(session.pending)
            pending_note = (
                f'<p class="unsaved-note">You have {n} staged '
                f'change{"s" if n != 1 else ""} not saved yet. '
                "Restoring a backup discards them - they describe "
                "edits to the version being replaced.</p>")
        rows = []
        for b in backups:
            rows.append(f"""
<li>
  <strong>{esc(b['name'])}</strong> - holds the version last saved
  {esc(b['version_saved'])}.
  {_button_form('/project/backups', 'Restore this version',
                hidden={'backup': b['name']})}
</li>""")
        inner = f"""
<p>Every save stores the version being replaced here, newest first
(the last {session.BACKUP_KEEP} are kept). Restoring swaps the project
file back to that version - and the version you have now is backed up
first, so a restore can itself be undone from this same page.</p>
{pending_note}
<ol class="review-list">{''.join(rows)}</ol>
<p>The backups live in the backups folder next to your project
file.</p>"""
    return _editor_page(session, "backups", inner, msg=msg)


def _shown_value(v):
    if isinstance(v, (list, tuple)):
        return ", ".join(f"{x:g}" if isinstance(x, float) else str(x)
                         for x in v)
    if v is None:
        return "(not set)"
    return str(v)


def render_send(session, slicer_name, blocked_by_pending, msg=None,
                texts=None, warnings=None):
    """Screen 5 - slice and send (or refuse politely with unsaved changes).

    Since Phase II the primary path slices right here: AccessSlice runs
    the slicer's engine invisibly and reads the result back in words.
    Opening the slicer's own window remains as the second path.

    warnings: the pre-flight list (preflight.warnings), spoken ABOVE
    the Slice now button so a doomed job is heard about before the
    engine spends minutes on it. None means the backend has no
    pre-flight checks; an empty list says so in words - silence would
    leave a screen-reader user wondering whether the check ran.
    """
    if blocked_by_pending:
        n = len(session.pending)
        body = f"""
<h1>Save before slicing</h1>
<p>You have {n} staged change{'s' if n != 1 else ''} that are not in the
file yet. Save them first, then slice.</p>
<p><a class="button primary" href="/project/review">Review changes</a></p>
<p><a class="button" href="/project/quality">Back to editing</a></p>
"""
        return page("Save before slicing", body, msg=msg,
                    project=session.path.name)
    plates = session.plates()
    plate_field = ""
    if len(plates) > 1:
        rows = ['<label class="radio-row"><input type="radio" '
                'name="plate" value="0" checked> All plates</label>']
        for p in plates:
            n = len(p["object_ids"])
            rows.append(
                f'<label class="radio-row"><input type="radio" '
                f'name="plate" value="{esc(str(p["id"]))}"> Plate '
                f'{esc(str(p["id"]))} ({n} object'
                f'{"s" if n != 1 else ""})</label>')
        plate_field = ("<fieldset><legend>Which plate to slice"
                       "</legend>" + "".join(rows) + "</fieldset>")
    body = f"""
<h1>Slice and send</h1>
<p>Your project is saved and verified, with your settings inside the
file.</p>
{_preflight_html(warnings)}
<h2>Slice here in AccessSlice</h2>
<p>AccessSlice runs {esc(slicer_name)}'s slicing engine invisibly and
reads the result back to you in words: print time, filament use,
warnings - plus a printable file, ready to send.</p>
<form method="post" action="/project/slice">
  {plate_field}
  <button class="primary" id="btn-slice-now">Slice now</button>
</form>
<h2>Or slice in {esc(slicer_name)} itself</h2>
<p>{texts.slice_and_send_html(esc(slicer_name))
    if texts is not None and hasattr(texts, 'slice_and_send_html')
    else esc(slicer_name) + ' opens with this project; slice and send '
    'from there.'}</p>
{f'''<p>This project has {len(plates)} build plates. Going this way,
{esc(slicer_name)} slices the plate selected there - choose the plate
in the slicer before pressing {esc(_shortcut(texts, 'slice'))}.</p>'''
 if len(plates) > 1 else ''}
<form method="post" action="/project/send">
  <button id="btn-open-slicer">Open in {esc(slicer_name)}</button>
</form>
<p><a class="button" href="/project/quality">Back to editing</a></p>
"""
    return page("Slice and send", body, msg=msg, project=session.path.name)


def _preflight_html(warnings):
    """The 'Warnings before slicing' section of the send page."""
    if warnings is None:
        return ""
    if not warnings:
        found = ("<p>Nothing to warn about: supports match the "
                 "overhangs, nothing would be hit when printing one "
                 "object at a time, and every object stands firmly "
                 "on the plate within the printer's height.</p>")
    else:
        items = "".join(f"<li>{esc(w['text'])}</li>" for w in warnings)
        found = f'<ul class="warnings">{items}</ul>'
    return f"""<h2>Warnings before slicing</h2>
<p>Checked by AccessSlice from your settings and the objects' real
shapes, before any slicing time is spent. A warning never stops you -
you decide.</p>
{found}"""


def render_slicing(session, job):
    """The in-app slicing progress page.

    app.js polls /api/slice and updates the live region with a
    heartbeat ("Slicing... 40 seconds elapsed."), then moves to the
    report when the engine ends. Without JavaScript the "Check
    progress" link does the same by reloading - the server redirects
    to the report once the job is over.
    """
    what = ("all plates" if job.plate == 0
            else f"plate {job.plate}")
    body = f"""
<h1>Slicing</h1>
<p>{esc(session.path.name)} ({esc(what)}) is being sliced by the
slicer's own engine, invisibly. A single small model takes under a
minute; a full plate can take several. This page announces progress by
itself and moves to the report when the slice ends.</p>
<p id="slice-line" aria-live="polite">{esc(job.describe())}</p>
<form method="post" action="/project/slice-cancel">
  <button id="btn-slice-cancel">Cancel slicing</button>
</form>
<p><a class="button" href="/project/slicing">Check progress now</a></p>
"""
    return page("Slicing", body, project=session.path.name)


def render_building(job, msg=None):
    """The new-job build's progress page (built 2026-09-26).

    A detailed model keeps the engine busy for minutes - the Skyward
    Dragon, 23 million triangles, took eight - so the build runs in
    the background and this page speaks where it is. app.js polls
    /api/build, speaks the stage and time every 15 seconds and moves
    on when the build ends; without JavaScript, "Check progress now"
    reloads and the server moves on.
    """
    body = f"""
<h1>Building the project</h1>
<p>AccessSlice is building a project from {esc(job.model_name)} with
the slicer's own engine, invisibly. A small model takes seconds; a very
detailed one can take ten minutes or more. This page announces progress
by itself and opens the project when it is built.</p>
<p id="build-line" aria-live="polite">{esc(job.describe())}</p>
<form method="post" action="/building/cancel">
  <button id="btn-build-cancel">Cancel building</button>
</form>
<p><a class="button" href="/building">Check progress now</a></p>
"""
    return page("Building the project", body, msg=msg)


def render_slice_report(session, job, slicer_name, msg=None,
                        texts=None, lan=None, drives=None):
    """The slice report - the headline feature no other slicer has.

    Success: print time, filament use and cost, per-feature breakdown,
    warnings, and where the printable file lives - all in words.
    Failure: the engine's own message quoted, and the reassurance that
    the project file is untouched.

    The send paths, in order (rebuilt 2026-09-07 after Edis's first
    Prusa run dead-ended in PrusaSlicer's G-code viewer): straight to
    the printer over LAN when the backend can (lan: ip, and for
    PrusaLink the key too); a copy to a USB drive (drives: what
    Windows sees right now) or any folder; the slicer's own window -
    with the PROJECT, not the sliced file, when the slicer would only
    open a viewer (texts.SLICED_OPENS_VIEWER); File Explorer with the
    file selected.
    """
    back = """
<p><a class="button" href="/project/send">Back to Slice and send</a>
<a class="button" href="/project/quality">Back to editing</a></p>"""

    if job.status == "cancelled":
        body = f"""
<h1>Slicing cancelled</h1>
<p>The slice of {esc(session.path.name)} was stopped before it
finished. No printable file was produced, and the project file is
untouched.</p>
{back}"""
        return page("Slicing cancelled", body, msg=msg,
                    project=session.path.name)

    if job.status != "done":
        r = job.report or {}
        problem = r.get("error_string") or r.get("problem") \
            or "The slicer engine gave no reason."
        code = r.get("return_code")
        code_line = (f"<p>The engine's return code was {code}.</p>"
                     if code not in (None, 0) else "")
        where = r.get("out_dir") or str(job.out_dir)
        body = f"""
<h1>Slicing failed</h1>
<p>The slicer engine could not slice {esc(session.path.name)}. Its own
message: <strong>{esc(str(problem))}</strong></p>
{code_line}
<p>The project file is untouched - nothing was changed by trying.
Fix what the message points at (the Placement tab for off-plate
objects, the Filaments tab for filament problems) and slice again.</p>
<p>The engine's captured output is kept in {esc(str(where))} and the
attempt is recorded on the <a href="/diagnostics">Diagnostics
page</a>.</p>
{back}"""
        return page("Slicing failed", body, msg=msg,
                    project=session.path.name)

    r = job.report
    fil_names = session.settings.get("filament_settings_id") or []
    fil_costs = session.settings.get("filament_cost") or []

    # Filament lines: grams per slot, named, with cost when the preset
    # carries a price per kg.
    by_slot = {}
    for plate in r["plates"]:
        for f in plate["filaments"]:
            by_slot[f["slot"]] = by_slot.get(f["slot"], 0.0) + f["grams"]
    fil_lines, total_cost, cost_known = [], 0.0, True
    for slot in sorted(by_slot):
        grams = by_slot[slot]
        name = (fil_names[slot - 1]
                if isinstance(slot, int) and 1 <= slot <= len(fil_names)
                else "")
        line = (f"Filament {slot}"
                + (f" ({esc(name)})" if name else "")
                + f": {grams:.1f} g")
        cost = 0.0
        try:
            cost = grams / 1000.0 * float(fil_costs[slot - 1])
        except (TypeError, ValueError, IndexError):
            cost_known = False
        if cost > 0:
            line += f", costing about {cost:.2f}"
            total_cost += cost
        fil_lines.append(f"<li>{line}</li>")
    cost_note = ("<li>Total filament cost: about "
                 f"{total_cost:.2f} (from the filament presets' price "
                 "per kilogram)</li>"
                 if cost_known and total_cost > 0 and len(fil_lines) > 1
                 else "")

    warnings_html = "".join(
        f'<p class="unsaved-note">Slicer warning: {esc(w)}</p>'
        for w in r["warnings"])

    plate_sections = []
    for plate in r["plates"]:
        objects = ", ".join(esc(o["name"]) for o in plate["objects"]) \
            or "nothing"
        features = "".join(
            f"<li>{esc(fname)}: "
            f"{engine_driver.friendly_duration(secs)}</li>"
            for fname, secs in plate["features"])
        # Some engines (Creality) report no per-feature breakdown -
        # then the section is honestly absent, never an empty list.
        if not features and len(r["plates"]) == 1:
            continue
        heading = (f"<h2>Plate {esc(str(plate['id']))}</h2>"
                   if len(r["plates"]) > 1
                   else "<h2>Where the time goes</h2>")
        intro = (f"<p>Print time "
                 f"{engine_driver.friendly_duration(plate['time_s'])}, "
                 f"printing: {objects}.</p>"
                 if len(r["plates"]) > 1 else "")
        features_html = (f'\n<ul class="summary">{features}</ul>'
                         if features else "")
        plate_sections.append(f"""
{heading}{intro}{features_html}""")

    out = job.output_file()
    out_path = esc(str(out)) if out else "(missing)"
    # Direct LAN send, for backends that can talk to the printer
    # itself (the K series, the Core One over PrusaLink). Deliberately
    # the FIRST offer: no slicer window at all. The plate-empty
    # confirmation mirrors the slicer's own safety dialog, in
    # accessible form.
    lan_html = ""
    if lan is not None:
        key_html = ""
        if lan.get("needs_key"):
            key_html = f"""
  <div class="setting-row">
    <label for="printer-key">{esc(lan.get('key_label', 'Password'))}
    <span class="hint">{esc(lan.get('key_help', ''))}</span></label>
    <input type="text" id="printer-key" name="key"
           autocomplete="off" spellcheck="false"
           value="{esc(lan.get('key', ''))}">
  </div>"""
        lan_html = f"""
<h3>Straight to the printer - no slicer window</h3>
{('<p class="unsaved-note"><strong>' + esc(lan['warning']) + '</strong></p>') if lan.get('warning') else ''}
<p>AccessSlice uploads the sliced job to the printer over your own
network and starts it. First the same safety check the slicer's
dialog makes: check with your hands that the build plate is EMPTY -
a leftover print on the plate can wreck the printer.</p>
<form method="post" action="/project/send-lan">
  <div class="setting-row">
    <label for="printer-ip">The printer's address or name (found by
    itself from the slicer's own records when possible - only type
    one if the field is empty; both are on the printer's screen
    under its network settings, the name followed by .local, for
    example prusa-core-one.local)</label>
    <input type="text" id="printer-ip" name="ip"
           value="{esc(lan.get('ip', ''))}">
  </div>{key_html}
  <label class="radio-row"><input type="checkbox" name="plate_empty"
    value="yes"> I checked: the build plate is empty</label>
  <p><button class="primary" id="btn-send-lan">Upload to the printer
  and start the print</button></p>
</form>
<p><a class="button" href="/printer">Check the printer's status</a></p>"""

    # The USB drive: the path every printer has. The drives Windows
    # sees right now are offered as radio buttons; a typed folder
    # covers a drive that reports as fixed, or any other place.
    drive_rows = "".join(
        f'<label class="radio-row"><input type="radio" name="drive" '
        f'value="{esc(d["path"])}"'
        + (" checked" if i == 0 else "")
        + f"> {esc(d['text'])}</label>"
        for i, d in enumerate(drives or []))
    if drive_rows:
        drives_html = (f"<fieldset><legend>USB drives plugged in right "
                       f"now</legend>{drive_rows}</fieldset>")
    else:
        drives_html = ("<p>No USB drive is plugged in right now. Plug "
                       "one in, then <a href=\"/project/slice-report\">"
                       "reload this page</a> to see it listed - or type "
                       "a folder below.</p>")
    usb_note = (texts.usb_copy_html(esc(slicer_name))
                if texts is not None and hasattr(texts, "usb_copy_html")
                else "The file is complete and printable as it is.")
    usb_html = f"""
<h3>{'Or copy' if lan is not None else 'Copy'} it to a USB drive</h3>
<p>AccessSlice copies the printable file to the drive you choose and
checks the copy. {usb_note}</p>
<form method="post" action="/project/copy-usb">
  {drives_html}
  <div class="setting-row">
    <label for="copy-folder">Or a folder to copy to (a drive letter
    such as E: works too)</label>
    <input type="text" id="copy-folder" name="folder" value="">
  </div>
  <p><button class="primary" id="btn-copy-usb">Copy the file
  there</button></p>
</form>"""

    # The slicer's own window. When the slicer would open the sliced
    # file in a viewer that cannot send (PrusaSlicer), the project is
    # handed over instead and the viewer is a demoted "look only".
    opens_viewer = bool(getattr(texts, "SLICED_OPENS_VIEWER", False))
    sliced_note = (texts.send_sliced_html(esc(slicer_name))
                   if texts is not None and hasattr(texts, 'send_sliced_html')
                   else esc(slicer_name) + ' opens it with everything '
                   'already set.')
    if opens_viewer:
        slicer_html = f"""
<h3>Or send through {esc(slicer_name)}</h3>
<p>{sliced_note}</p>
<form method="post" action="/project/send">
  <button id="btn-open-slicer">Open the project in {esc(slicer_name)}
  to slice and send there</button>
</form>
<form method="post" action="/project/send-sliced">
  <button id="btn-send-sliced">Only look: open the sliced file in
  {esc(slicer_name)}'s G-code viewer (it cannot send)</button>
</form>"""
    else:
        slicer_html = f"""
<h3>Or send through {esc(slicer_name)}</h3>
<form method="post" action="/project/send-sliced">
  <button class="primary" id="btn-send-sliced">Open the sliced file in
  {esc(slicer_name)} to send</button>
</form>
<p>{sliced_note}</p>"""
    body = f"""
<h1>Sliced and ready to print</h1>
<p>{esc(session.path.name)} sliced without errors, in
{engine_driver.friendly_duration(job.elapsed())}.</p>
{warnings_html}
<ul class="summary">
  <li>Estimated print time:
      {engine_driver.friendly_duration(r['total_time_s'])}</li>
  <li>Filament used: {r['total_grams']:.1f} g in total</li>
  {''.join(fil_lines)}
  {cost_note}
</ul>
{''.join(plate_sections)}
<h2>Look at the sliced result</h2>
<p><a class="button" href="/project/preview">Sliced preview - the
print layer by layer, in words and as a picture</a></p>
<h2>Send it to the printer</h2>
<p>The printable file is ready:</p>
<p><strong>{out_path}</strong></p>
<form method="post" action="/project/show-sliced">
  <button id="btn-show-sliced">Show the file in File Explorer</button>
</form>
{lan_html}
{usb_html}
{slicer_html}
<p class="hint">Each slice gets its own folder under the project's
sliced folder; the newest {engine_driver.KEEP_RUNS} are kept.</p>
{back}"""
    return page("Sliced and ready to print", body, msg=msg,
                project=session.path.name)


def _minutes_words(minutes):
    if minutes is None:
        return ""
    return engine_driver.friendly_duration(minutes * 60)


def layer_sentence(facts, number):
    """One layer's facts in a sentence - the same words preview.js
    speaks when the slider moves, so both audiences read one text."""
    layer = facts["layers"][number - 1]
    text = (f"Layer {number} of {facts['layer_count']}, top at "
            f"{layer['z']:g} mm, {layer['height']:g} mm thick.")
    if layer.get("start_min") is not None:
        if layer["start_min"] == 0:
            text += " Starts at the beginning of the print."
        else:
            text += (f" Starts about {_minutes_words(layer['start_min'])} "
                     "into the print.")
    text += (f" {layer['segments']} moves: "
             f"{toolpath.friendly_mm(layer['extrusion_mm'])} of extrusion")
    if layer["features"]:
        text += " - " + ", ".join(
            f"{f['name'].lower()} {toolpath.friendly_mm(f['mm'])}"
            for f in layer["features"])
    text += f"; {toolpath.friendly_mm(layer['travel_mm'])} of travel."
    return text


def render_preview(session, entry, plates, layer_query="", msg=None,
                   sliced_name=""):
    """The sliced preview page (Phase V).

    Three states of the parse: working (the page refreshes itself
    every two seconds, no JavaScript needed), failed (the reason in
    words), ready. Ready: the facts first - layers, heights, where
    the filament goes, per layer on request - then the picture, a
    mirror of those facts with real controls (slider, number field,
    one checkbox per feature, view buttons); the canvas is hidden
    from screen readers.
    """
    name = esc(session.path.name)
    back = """
<p><a class="button" href="/project/slice-report">Back to the slice
report</a> <a class="button" href="/project/quality">Back to
editing</a></p>"""
    plate = entry["plate"]
    plate_links = ""
    if len(plates) > 1:
        items = "".join(
            f'<li><a href="/project/preview?plate={esc(p)}"'
            + (' aria-current="page"' if p == plate else "")
            + f">Plate {esc(p)}</a></li>" for p in plates)
        plate_links = (f"<p>This sliced file holds {len(plates)} plates; "
                       f"showing plate {esc(plate)}.</p>"
                       f'<nav aria-label="Plates in the sliced file">'
                       f'<ul class="tabs">{items}</ul></nav>')

    if entry["status"] == "working":
        body = f"""
<h1>Preparing the sliced preview</h1>
<p>AccessSlice is reading the G-code of {esc(sliced_name)} (plate
{esc(plate)}) to count its layers and see where every move goes. A
small print takes a few seconds; this page refreshes itself and shows
the facts as soon as they are ready.</p>
<p aria-live="polite">Still reading. This page checks again every two
seconds.</p>
<p><a class="button" href="/project/preview?plate={esc(plate)}">Check
now</a></p>
{back}"""
        return page("Preparing the sliced preview", body, msg=msg,
                    project=session.path.name,
                    head='\n<meta http-equiv="refresh" content="2">')

    if entry["status"] != "ready":
        body = f"""
<h1>The sliced preview could not be prepared</h1>
<p>The sliced file {esc(sliced_name)} could not be read back:
<strong>{esc(entry['error'])}</strong></p>
<p>The print itself is not affected - the file is what the slicer
wrote, and the slice report still stands.</p>
{back}"""
        return page("Sliced preview", body, msg=msg,
                    project=session.path.name)

    f = entry["facts"]
    n_layers = f["layer_count"]
    try:
        chosen = int(layer_query)
    except (TypeError, ValueError):
        chosen = n_layers
    chosen = max(1, min(n_layers, chosen))
    heights = ", ".join(f"{h:g}" for h in f["layer_heights"])
    time_line = ""
    if f.get("total_min"):
        time_line = (f"<li>The whole print takes about "
                     f"{_minutes_words(f['total_min'])} (the engine's "
                     "own estimate, written into the G-code).</li>")
    totals = "".join(
        f"<li>{esc(t['name'])}: {toolpath.friendly_mm(t['mm'])}</li>"
        for t in f["totals"])
    travel_note = ("" if f.get("travel_drawn", True) else
                   " Travel moves are left out of the picture on this "
                   "big print (they are still counted here).")
    kinds_present = {t["kind"] for t in f["totals"]}
    kind_boxes = "".join(
        f'<label class="radio-row"><input type="checkbox" '
        f'class="kind-toggle" value="{esc(k["kind"])}" checked> '
        f'<span class="swatch" style="background:{esc(k["colour"])}" '
        f'aria-hidden="true"></span>{esc(k["name"])}</label>'
        for k in f["kinds"] if k["kind"] in kinds_present)
    body = f"""
<h1>Sliced preview - {name}</h1>
<p>What the slicer made of your project, read back from the sliced
file {esc(sliced_name)}: the layers, and what each one is made of.
The picture further down is a mirror of these facts, nothing more.
{esc(travel_note)}</p>
{plate_links}
<h2>The print in numbers</h2>
<ul class="summary">
  <li>{n_layers} layers, the top one ending at {f['top_z']:g} mm.</li>
  <li>First layer {f['first_layer_height']:g} mm; layer heights used:
      {esc(heights)} mm.</li>
  {time_line}
  <li>{toolpath.friendly_mm(f['extrusion_mm'])} of extrusion paths in
      total; {toolpath.friendly_mm(f['travel_mm'])} of travel.</li>
</ul>
<h3>Where the filament goes</h3>
<ul class="summary">{totals}</ul>
<h2 id="layers-heading">Layer by layer</h2>
<form method="get" action="/project/preview" class="layer-controls">
  <input type="hidden" name="plate" value="{esc(plate)}">
  <div class="row">
    <label for="layer-number">Layer number (1 to {n_layers})</label>
    <input type="number" id="layer-number" name="layer" min="1"
           max="{n_layers}" value="{chosen}">
    <button id="btn-show-layer">Show this layer</button>
  </div>
</form>
<p id="layer-facts" aria-live="polite">{esc(layer_sentence(f, chosen))}</p>
<section class="view-panel" id="preview-panel" aria-labelledby="preview-heading"
         data-plate="{esc(plate)}">
  <h2 id="preview-heading">The picture</h2>
  <p class="view-note">Every layer from the bottom up to the chosen one,
  each move coloured by what it is. The slider and the layer number
  above are the same control; the checkboxes hide or show kinds of
  moves.</p>
  <div class="layer-controls">
    <label for="layer-slider">Layers shown, from the bottom up to
    layer</label>
    <input type="range" id="layer-slider" min="1" max="{n_layers}"
           value="{chosen}" step="1">
    <label class="radio-row"><input type="checkbox" id="show-travel">
    Show travel moves (the nozzle moving without extruding)</label>
    <fieldset>
      <legend>Kinds of moves shown</legend>
      {kind_boxes}
    </fieldset>
  </div>
  <div class="view-toolbar">
    <button type="button" id="preview-fit">Fit to the print</button>
    <button type="button" id="preview-top">From above</button>
    <button type="button" id="preview-front">From the front</button>
    <button type="button" id="preview-angle">From the front left</button>
    <button type="button" id="preview-zoom-in">Zoom in</button>
    <button type="button" id="preview-zoom-out">Zoom out</button>
  </div>
  <div class="view-stage" id="preview-stage" aria-hidden="true"></div>
  <p class="view-status" id="preview-status" role="status"></p>
  <p class="view-note">With a mouse: drag to turn the view, wheel to
  zoom, right-drag to slide.</p>
</section>
{back}"""
    return page(f"Sliced preview - {session.path.name}", body, msg=msg,
                project=session.path.name, wide=True,
                scripts=("three.min.js", "preview.js"))


def render_printer_status(lines, ip, msg=None, live=False, control=False):
    """What the printer itself reports, read over the network.

    live: a job is running (or paused, or the printer is busy), so
    the page keeps watching: app.js asks /api/printer every half
    minute, refreshes the list, reads a short progress line into the
    polite live region, and when the job ends puts "The print has
    finished." (or stopped) into an alert region and stops. Without
    JavaScript the "Check again" link does the same by hand.
    """
    items = "".join(f"<li>{esc(line)}</li>" for line in lines)
    control_html = ""
    if control:
        control_html = ('<p><a class="button" href="/printer/control">'
                        "Control the printer - preheat, filament, moves, "
                        "calibration</a> <a class=\"button\" "
                        'href="/printer/files">Files on the printer</a>'
                        "</p>\n")
    watch = ""
    if live:
        watch = """
<p id="printer-line" aria-live="polite" data-printer-poll="30">This page
asks the printer again every 30 seconds while the job runs and reads
the progress aloud. When the print ends, it says so.</p>
<p id="printer-alert" role="alert"></p>"""
    body = f"""
<h1>Printer status</h1>
<p>Read straight from the printer at {esc(ip)} over your own
network - nothing leaves the house.</p>
<ul class="summary" id="printer-lines">{items}</ul>
{watch}
<p><a class="button primary" href="/printer">Check again</a></p>
{control_html}<p><a class="button" href="/project/slice-report">Back to the slice
report</a></p>
<p><a class="button" href="/">Home</a></p>
"""
    return page("Printer status", body, msg=msg)


def render_handoff(session, slicer_name, when, second_window=False,
                   sliced_name=None, texts=None, device=None):
    """After launching the slicer: the quiet iteration-loop screen.

    second_window: the slicer was already open when this send happened.
    Observed with real Studio (check 6, 2026-08-31): the send opens a
    SECOND window with the fresh file while the old window keeps
    showing the previous version - so warn before anything is sliced.

    sliced_name: an already-sliced .gcode.3mf was handed over instead
    of the editable project - only the send keystroke remains.

    device: what the send did about the slicer's Device-page printer,
    or None when nothing was needed - a dict with "state" ("switched":
    the slicer was closed and its saved selection was moved to the
    project's printer; "kept": it could not be moved, so the send
    window will name the wrong printer), "printer" (the project's,
    "Bambu Lab H2C") and "other" (the one the page is on, or "").
    """
    name = esc(slicer_name)

    def text_or(fn_name, fallback="", *args):
        fn = getattr(texts, fn_name, None) if texts is not None else None
        return fn(name, *args) if fn else fallback

    device_note = ""
    if device:
        printer = esc(device.get("printer", ""))
        other = esc(device.get("other", ""))
        if device.get("state") == "switched":
            device_note = ("<p>" + text_or(
                "device_switched_html",
                f"Before {name} opened, AccessSlice switched its "
                f"printer to the {printer}.", printer) + "</p>")
        else:
            device_note = ('<p class="unsaved-note">' + text_or(
                "device_kept_html",
                f"{name} still has "
                + (f"the {other}" if other else "another printer")
                + f" selected, not the {printer} this project is for - "
                f"pick the {printer} there before sending.",
                printer, other) + "</p>")

    second_note = ""
    if second_window:
        second_note = ('<p class="unsaved-note">'
                       + text_or("second_window_html",
                                 f"{name} was already open, so this "
                                 "opened a second window with the "
                                 "fresh file; close the old one.")
                       + "</p>")
    waiting_line = ("The slicer can take a while to appear - check "
                    "with <kbd>Alt+Tab</kbd> until its window is "
                    "there.")
    if sliced_name:
        opened = (f"<p>Opened the sliced file {esc(sliced_name)} in "
                  f"{name} at {esc(when)}. {waiting_line} "
                  + text_or("handoff_sliced_html") + "</p>")
    else:
        opened = (f"<p>Opened {esc(session.path.name)} in {name} at "
                  f"{esc(when)}. {waiting_line} "
                  + text_or("handoff_project_html") + "</p>")
    body = f"""
<h1>Project handed to {esc(slicer_name)}</h1>{second_note}{device_note}{opened}
<p>If the print needs another tweak afterwards, come straight back:</p>
<p><a class="button primary" href="/project/quality">Edit this project
again</a></p>
<p><a class="button" href="/">Home</a></p>
"""
    return page("Handed to the slicer", body, project=session.path.name)


def render_send_device_question(session, slicer_name, printer, other,
                                sliced=False, msg=None, texts=None):
    """A send that needs the slicer's Device page switched while the
    slicer is running: the switch only takes while it is closed, so
    ask - close it and switch, or open anyway and pick the printer by
    hand in the send window. Same shape as the other question pages:
    the recommended action first and focused, so Enter takes it.

    printer / other: model names ("Bambu Lab H2C"); other may be "".
    sliced: the sliced file is what is being sent (the forms post to
    send-sliced and the way back is the slice report).
    """
    name = esc(slicer_name)
    action = "/project/send-sliced" if sliced else "/project/send"
    back = ("/project/slice-report" if sliced else "/project/send")
    back_words = "the slice report" if sliced else "Slice and send"
    fn = getattr(texts, "device_question_html", None) if texts else None
    if fn:
        explain = fn(name, esc(printer), esc(other))
    else:
        on_now = f"the {esc(other)}" if other else "another printer"
        explain = (f"<p>This project is for the {esc(printer)}, but "
                   f"{name} is already open with {on_now} selected. "
                   f"AccessSlice can only change that while {name} is "
                   f"closed. Close {name} first (<kbd>Alt+Tab</kbd> to "
                   "its window, then <kbd>Alt+F4</kbd>), then press the "
                   "first button.</p>")
    body = f"""
<h1>Which printer is {name} on?</h1>
{explain}
<form method="post" action="{action}">
  <input type="hidden" name="device" value="switch">
  <p><button class="primary" autofocus>{name} is closed now - switch
  it to the {esc(printer)} and open the file</button></p>
</form>
<form method="post" action="{action}">
  <input type="hidden" name="device" value="keep">
  <p><button>Open the file anyway - I will pick the {esc(printer)} in
  the send window myself</button></p>
</form>
<p><a class="button" href="{back}">Back to {back_words}</a></p>
"""
    return page("Which printer - " + session.path.name, body, msg=msg,
                project=session.path.name)


# ------------------------------------------------- printer control
#
# The Prusa Accessible Control program's window as AccessSlice pages
# (2026-09-12): every action is a form button, every number a
# labelled field, the running command's events a list the page
# polls (app.js, /api/control) and reads into a live region.


def _num_field(field_id, name, label, value="", hint=None,
               required=False, decimal=True):
    """One labelled field, a number unless decimal=False. Free text
    on purpose (no type=number): screen readers read a text field's
    value in full, and a typed comma is read as a decimal point by
    the server."""
    hint_html = f' <span class="hint">{esc(hint)}</span>' if hint else ""
    mode = ' inputmode="decimal"' if decimal else ""
    return (f'<div class="setting-row"><label for="{field_id}">'
            f'{esc(label)}{hint_html}</label>'
            f'<input type="text" id="{field_id}" name="{esc(name)}" '
            f'value="{esc(str(value))}"{mode} size="8" '
            f'autocomplete="off"{" required" if required else ""}></div>')


def _action_form(action, label, fields_html="", cls="", note=None,
                 button_id=None):
    """One command as a form: its fields, then the button."""
    note_html = f'<p class="hint">{esc(note)}</p>' if note else ""
    ident = f' id="{esc(button_id)}"' if button_id else ""
    cls_form = ("inline-form block" if 'class="setting-row"' in fields_html
                else "inline-form")
    return (f'<form method="post" action="/printer/control/{esc(action)}" '
            f'class="{cls_form}">{fields_html}{note_html}'
            f'<button class="{cls}"{ident}>{esc(label)}</button></form>')


def _events_html(snapshot):
    if not snapshot or not snapshot.get("events"):
        return ""
    items = "".join(
        f'<li>{esc(e["when"])} {esc(e["message"])}</li>'
        for e in snapshot["events"])
    return f'<ol class="review-list" id="control-events">{items}</ol>'


def render_printer_control(ip, key_known, needs_key, status_lines,
                           status_error, snapshot, presets, snippets,
                           limits, msg=None, key_label="Password",
                           key_help="", features=None, mode="job",
                           cfs=None, printer_name="the printer"):
    """The control page: connection, the printer's state, the running
    command, then the command sections - print job, preheat,
    filament, move, temperature, calibration, settings, free G-code.

    status_lines / status_error: one read of the printer at page
    time (the error in words when it did not answer). snapshot: the
    running or last command's snapshot(), or None. presets: the
    preheat preset names; snippets: the ready-made command names;
    limits: the printer's limits for the hints. features: the set of
    actions this printer offers (printer_control.JOB_FEATURES or
    DIRECT_FEATURES) - a form is only shown for an action in it, and
    a section with nothing left is skipped. mode: "job" (the Core
    One: each command is a tiny job file) or "direct" (the K series:
    commands go straight over the WebSocket). cfs: the K series'
    CFS boxes as creality_lan.cfs_info gives them, or None.
    """
    features = set(features if features is not None else
                   printer_control_job_features())

    def has(*actions):
        return any(a in features for a in actions)

    def only(action, html):
        return html if action in features else ""

    def lim(name):
        lo, hi = limits.get(name, (0, 0))
        return f"{lo:g} to {hi:g}"

    direct = mode == "direct"

    # -- connection
    key_html = ""
    if needs_key:
        key_html = f"""
  <div class="setting-row">
    <label for="control-key">{esc(key_label)}
    <span class="hint">{esc(key_help)}{' A password is remembered; leave the field empty to keep it.' if key_known else ''}</span></label>
    <input type="text" id="control-key" name="key" autocomplete="off"
           spellcheck="false" value="">
  </div>"""
    connection_html = f"""
<h2>Connection</h2>
<form method="post" action="/printer/connection">
  <div class="setting-row">
    <label for="control-ip">The printer's address or name <span
    class="hint">Both are on the printer's screen under its network
    settings; the name is typed followed by .local, for example
    prusa-core-one.local. Remembered here once it works - by the
    printer's name when it has one, so it is found again after your
    router hands it a new address.</span></label>
    <input type="text" id="control-ip" name="ip" value="{esc(ip)}"
           autocomplete="off" spellcheck="false">
  </div>{key_html}
  <p><button class="primary">Save and test the connection</button></p>
</form>"""

    # -- the printer now
    if status_error:
        now_html = (f'<p class="unsaved-note"><strong>{esc(status_error)}'
                    "</strong></p>")
    else:
        items = "".join(f"<li>{esc(line)}</li>" for line in status_lines)
        now_html = f'<ul class="summary" id="printer-lines">{items}</ul>'

    # -- the running or last command
    running = bool(snapshot and snapshot.get("running"))
    if snapshot:
        head = ("Running now: " if running else "Last command: ") \
            + snapshot["name"] + f" (started {snapshot['started_at']})"
        watch = ""
        if running:
            knob = ("" if direct else " If the printer asks a question on "
                    "its screen, press the knob on the printer.")
            watch = f"""
<p id="control-line" aria-live="polite" data-control-poll="2">This
page follows the command every two seconds and reads each step
aloud.{knob}</p>
<p id="control-alert" role="alert"></p>"""
        job_html = f"<h3>{esc(head)}</h3>{watch}{_events_html(snapshot)}"
    else:
        job_html = ('<p class="hint">No command has been sent since '
                    "AccessSlice started.</p>")

    preset_options = "".join(
        f'<option value="{esc(p)}">{esc(p)}</option>' for p in presets)
    snippet_options = "".join(
        f'<option value="{esc(s)}">{esc(s)}</option>' for s in snippets)

    # -- the print job now
    job_controls = ""
    if has("pause", "resume", "stop"):
        reach = ("These reach a running print at once. Temperatures, "
                 "fans, the light and the speed work mid-print too; "
                 "moves and homing need an idle printer."
                 if direct else
                 "These reach a running print at once - the only things "
                 "that do. Everything further down needs an idle "
                 "printer.")
        job_controls = f"""
<h2>The print job now</h2>
<p>{reach}</p>
<div class="actions">
{_action_form('pause', 'Pause the print', button_id='btn-pause')}
{_action_form('resume', 'Resume the print')}
<form method="post" action="/printer/control/stop" class="inline-form">
  <label class="radio-row"><input type="checkbox" name="confirm"
    value="yes"> I mean it: stop the running print</label>
  <button>Stop the print</button></form>
</div>
{only('files', '<p><a href="/printer/files">Files on the printer - start, delete, upload</a></p>')}"""

    # -- preheat
    preheat_html = ""
    if has("preheat", "preheat-custom", "cooldown"):
        preheat_html = f"""
<h2>Preheat</h2>
<div class="actions">
{only('preheat', f'''<form method="post" action="/printer/control/preheat" class="inline-form">
  <div class="setting-row"><label for="preheat-preset">Preset</label>
  <select id="preheat-preset" name="preset">{preset_options}</select></div>
  <label class="radio-row"><input type="checkbox" name="nozzle_only"
    value="yes"> Nozzle only, leave the bed cold</label>
  <button class="primary" id="btn-preheat">Preheat with this preset</button>
</form>''')}
{only('preheat-custom', _action_form('preheat-custom', 'Preheat to these values',
              _num_field('preheat-nozzle', 'nozzle', 'Nozzle, degrees', hint=lim('nozzle'), required=True)
              + _num_field('preheat-bed', 'bed', 'Bed, degrees (empty: unchanged)', hint=lim('bed'))
              + _num_field('preheat-chamber', 'chamber', 'Chamber, degrees (empty: unchanged)', hint=lim('chamber'))))}
{only('cooldown', _action_form('cooldown', 'Cooldown - all heaters and the fan off', button_id='btn-cooldown'))}
</div>"""

    # -- filament
    filament_html = ""
    if has("load", "unload", "change", "purge", "autoload",
           "load-no-prompts", "cfs", "extrude", "retract"):
        if direct:
            note = ("<p>Loading and retracting run the printer's own "
                    "routines from the CFS or the external spool holder; "
                    "the printer heats as it needs. Extrude and retract "
                    "turn the extruder by a length, with a hot "
                    "nozzle.</p>")
        else:
            note = ("<p>Load and unload run the printer's own routines, "
                    "heated first so nothing asks about preheating. A "
                    "load stops twice for the knob on the printer "
                    '("filament detected" and after the purge); an '
                    "unload finishes by itself. Every filament command "
                    "ends with the nozzle heater off.</p>")
        cfs_html = ""
        if "cfs" in features:
            boxes = cfs or []
            slot_rows = []
            holder_rows = []
            # The K2 Plus lists two kinds of box (seen live
            # 2026-09-15): type 1 is the external spool holder with
            # the one material the printer remembers for it, type 0
            # a CFS with four slots. Shown as one row each, never as
            # a slot of the CFS - the holder's entry had read as a
            # second "Slot A".
            for box in boxes:
                holder = box.get("type") == 1
                for m in box.get("materials") or []:
                    try:
                        slot = int(m.get("id"))
                    except (TypeError, ValueError):
                        continue
                    vendor, name = m.get("vendor") or "", m.get("name") or ""
                    words = name if name.startswith(vendor) else \
                        " ".join(w for w in (vendor, name) if w)
                    kind = m.get("type") or ""
                    detail = kind + (f", {words}" if words else "")
                    if kind and m.get("color"):
                        detail += f", colour {m['color']}"
                    if kind and m.get("percent") is not None:
                        detail += f", {m['percent']} percent left"
                    if m.get("selected"):
                        detail += " (loaded now)"
                    if not detail:
                        detail = "empty"
                    hidden = (f'<input type="hidden" name="box" value="{esc(str(box.get("id", 1)))}">'
                              f'<input type="hidden" name="slot" value="{slot}">')
                    if holder:
                        holder_rows.append(
                            f"<li><strong>External spool holder</strong>: "
                            f"{esc(detail)} - what the printer remembers "
                            "for the spool on the holder; change it on the "
                            "printer's screen if it is wrong."
                            '<div class="actions">'
                            + _action_form('cfs-feed', 'Load from the external spool', hidden)
                            + _action_form('cfs-retract', 'Retract the external spool', hidden)
                            + "</div></li>")
                        continue
                    letter = "ABCD"[slot] if 0 <= slot < 4 else str(slot)
                    slot_rows.append(
                        f"<li><strong>Slot {letter}</strong>: {esc(detail)}"
                        '<div class="actions">'
                        + _action_form('cfs-feed', f'Load from slot {letter}', hidden)
                        + _action_form('cfs-retract', f'Retract slot {letter}', hidden)
                        + "</div></li>")
            if slot_rows or holder_rows:
                cfs_list = (f'<ul class="plain-list">'
                            f'{"".join(slot_rows + holder_rows)}</ul>')
            elif cfs is None:
                cfs_list = ('<p class="hint">The printer did not answer '
                            "the CFS question; Read again once it is "
                            "on, or use the external spool buttons.</p>")
            else:
                cfs_list = ('<p class="hint">No CFS reported by the '
                            "printer.</p>")
            external = ('<input type="hidden" name="box" value="0">'
                        '<input type="hidden" name="slot" value="0">')
            external_html = "" if holder_rows else f"""
<div class="actions">
{_action_form('cfs-feed', 'Load from the external spool', external)}
{_action_form('cfs-retract', 'Retract the external spool', external)}
</div>"""
            cfs_html = f"""
<h3>CFS and spool</h3>
{cfs_list}{external_html}"""
        filament_html = f"""
<h2>Filament</h2>
{note}{cfs_html}
<div class="actions">
{only('load', _action_form('load', 'Load filament',
              _num_field('load-filament', 'filament', 'Filament type (PLA, PETG...) - sets the temperature and what the printer records', value='PLA', decimal=False)
              + _num_field('load-temp', 'temp', "Nozzle, degrees (empty: the type's own)", hint=lim('nozzle'))))}
{only('unload', _action_form('unload', 'Unload filament',
              _num_field('unload-temp', 'temp', 'Nozzle, degrees', value='215', hint=lim('nozzle'))))}
{only('change', _action_form('change', 'Change filament - unload, then load',
              _num_field('change-filament', 'filament', 'New filament type', value='PLA', decimal=False)
              + _num_field('change-temp', 'temp', "Nozzle, degrees (empty: the type's own)", hint=lim('nozzle'))))}
{only('purge', _action_form('purge', 'Purge - push filament through the hot nozzle',
              _num_field('purge-temp', 'temp', 'Nozzle, degrees', value='215', hint=lim('nozzle'))
              + _num_field('purge-length', 'length', 'Length, mm', value='50', hint=lim('length'))))}
{only('autoload', _action_form('autoload', 'Autoload - insert filament, the printer pulls it in'))}
{only('load-no-prompts', _action_form('load-no-prompts', 'Load without prompts - extruder moves only, no question on the screen',
              _num_field('noprompt-temp', 'temp', 'Nozzle, degrees', value='215', hint=lim('nozzle')),
              note='Push the filament in until it stops against the gears first. The printer does not record the filament type this way.'))}
{only('stuck-on', _action_form('stuck-on', 'Stuck filament detection on'))}
{only('stuck-off', _action_form('stuck-off', 'Stuck filament detection off'))}
{only('extrude', _action_form('extrude', 'Extrude', _num_field('extrude-length', 'length', 'Length, mm', value='5', hint='only with a hot nozzle')))}
{only('retract', _action_form('retract', 'Retract', _num_field('retract-length', 'length', 'Length, mm', value='5')))}
</div>"""

    # -- move
    move_html = ""
    if has("move", "home", "park", "motors-off", "motors-on",
           "quick-stop", "goto", "zoffset"):
        move_buttons = "".join(
            f'<button name="go" value="{axis}{sign}">{axis} {word}</button>'
            for axis, minus, plus in (
                ("X", "left", "right"), ("Y", "forward", "back"),
                ("Z", "down - nozzle toward the bed",
                 "up - nozzle away from the bed, the bed goes down"))
            for sign, word in (("-", minus), ("+", plus)))
        move_form = "" if "move" not in features else f"""
<form method="post" action="/printer/control/move">
<div class="setting-row"><label for="move-step">Step, mm
<span class="hint">{esc(lim('step'))}; 0.1, 1, 10, 50 or 100 are the
usual choices</span></label>
<input type="text" id="move-step" name="step" value="10"
       inputmode="decimal" size="8"></div>
<div class="actions">{move_buttons}</div>
</form>"""
        zoffset_form = "" if "zoffset" not in features else f"""
<h3>Live Z (first-layer tuning)</h3>
<p>Moves the nozzle a hair closer to or farther from the bed while a
print runs - closer squashes the first layer more.</p>
<form method="post" action="/printer/control/zoffset">
<div class="setting-row"><label for="zoffset-step">Step, mm
<span class="hint">{esc(lim('zoffset'))}; 0.01 is the usual
choice</span></label>
<input type="text" id="zoffset-step" name="step" value="0.01"
       inputmode="decimal" size="8"></div>
<div class="actions"><button name="direction" value="closer">Nozzle
closer to the bed</button><button name="direction"
value="farther">Nozzle farther from the bed</button></div>
</form>"""
        home_form = "" if "home" not in features else """
<form method="post" action="/printer/control/home" class="inline-form block">
<p class="hint">Before a home, AccessSlice lifts the nozzle to 5 mm
when it is lower, because the printer homes X and Y before Z and a
nozzle resting on the plate would be dragged across it. The tick is
only needed when the head height cannot be read (after a power-up):
it stands for your own hand check.</p>
<label class="radio-row"><input type="checkbox" name="clear"
value="yes"> The nozzle is clear of the plate (I felt the gap)</label>
<div class="actions"><button name="axes" value="" id="btn-home">Home
all axes</button><button name="axes" value="XY">Home X and Y</button>
<button name="axes" value="Z">Home Z</button></div>
</form>"""
        move_html = f"""
<h2>Move</h2>
<p>Relative moves by the step below. Z is the gap between the nozzle
and the plate: "Z down" makes it smaller (on a printer whose bed
moves, the bed comes UP toward the nozzle), "Z up" makes it larger.
AccessSlice reads the head position before every move and refuses,
in words, any move that would end below the plate (Z 0) or go
sideways with the nozzle lower than 1 mm - the printer itself allows
worse. To reach the nozzle by hand, move Z up by a large step, 100
or more: the bed goes down.</p>
{move_form}{home_form}
<div class="actions">
{only('park', _action_form('park', 'Park the head'))}
{only('motors-off', _action_form('motors-off', 'Disable motors'))}
{only('motors-on', _action_form('motors-on', 'Enable motors'))}
{only('quick-stop', _action_form('quick-stop', 'Quick stop - abort planned movement'))}
</div>
<div class="actions">
{only('goto', _action_form('goto', 'Go to this position',
              _num_field('goto-x', 'x', 'X, mm', hint=lim('x'))
              + _num_field('goto-y', 'y', 'Y, mm', hint=lim('y'))
              + _num_field('goto-z', 'z', 'Z, mm', hint=lim('z')),
              note='Leave an axis empty to keep it where it is.'))}
</div>{zoffset_form}"""

    # -- temperature
    temperature_html = ""
    if has("nozzle", "bed", "chamber", "fan", "fan-aux", "fan-case"):
        temperature_html = f"""
<h2>Temperature and fans</h2>
<div class="actions">
{only('nozzle', _action_form('nozzle', 'Set the nozzle', _num_field('temp-nozzle', 'temp', 'Nozzle, degrees', hint=lim('nozzle'), required=True)))}
{only('bed', _action_form('bed', 'Set the bed', _num_field('temp-bed', 'temp', 'Bed, degrees', hint=lim('bed'), required=True)))}
{only('chamber', _action_form('chamber', 'Set the chamber', _num_field('temp-chamber', 'temp', 'Chamber, degrees', hint=lim('chamber'), required=True)))}
{only('fan', _action_form('fan', 'Set the print fan' if not direct else 'Set the model fan', _num_field('fan-percent', 'percent', 'Fan, percent (0 is off)', hint=lim('fan'), required=True)))}
{only('fan-aux', _action_form('fan-aux', 'Set the side (auxiliary) fan', _num_field('fan-aux-percent', 'percent', 'Fan, percent (0 is off)', hint=lim('fan'), required=True)))}
{only('fan-case', _action_form('fan-case', 'Set the case fan', _num_field('fan-case-percent', 'percent', 'Fan, percent (0 is off)', hint=lim('fan'), required=True)))}
</div>"""

    # -- calibration
    calibration_html = ""
    if has("mesh-level", "z-align", "z-height", "z-probe", "shaper"):
        calibration_html = f"""
<h2>Calibration</h2>
<div class="actions">
{only('mesh-level', _action_form('mesh-level', 'Mesh bed levelling (homes first)'))}
{only('z-align', _action_form('z-align', 'Z axis alignment'))}
{only('z-height', _action_form('z-height', 'Measure Z height'))}
{only('z-probe', _action_form('z-probe', 'Single Z probe'))}
{only('shaper', _action_form('shaper', 'Set the input shaper',
              '<div class="setting-row"><label for="shaper-axis">Axis</label>'
              '<select id="shaper-axis" name="axis"><option value="X">X</option>'
              '<option value="Y">Y</option></select></div>'
              + _num_field('shaper-frequency', 'frequency', 'Frequency, Hz', hint=lim('frequency'), required=True)
              + _num_field('shaper-damping', 'damping', 'Damping (empty: unchanged)', hint=lim('damping'))
              + _num_field('shaper-type', 'type', 'Filter type 0 to 6 (empty: unchanged)'),
              note='Save the printer settings afterwards to keep it.'))}
</div>"""

    # -- settings and lights
    settings_html = ""
    if has("beep", "lights-on", "lights-off", "save-settings",
           "reload-settings", "factory-settings", "speed", "flow"):
        factory = "" if "factory-settings" not in features else """
<form method="post" action="/printer/control/factory-settings" class="inline-form">
  <label class="radio-row"><input type="checkbox" name="confirm"
    value="yes"> I mean it: restore the factory settings (tuned values are lost, calibrations stay)</label>
  <button>Restore the factory settings (M502)</button></form>"""
        settings_html = f"""
<h2>Printer settings and lights</h2>
<div class="actions">
{only('beep', _action_form('beep', 'Beep - an audible "I am here" from the printer', button_id='btn-beep'))}
{only('lights-on', _action_form('lights-on', 'Chamber lights on' if not direct else 'Light on'))}
{only('lights-off', _action_form('lights-off', 'Chamber lights off' if not direct else 'Light off'))}
{only('speed', _action_form('speed', 'Set the print speed', _num_field('speed-percent', 'percent', 'Speed, percent (100 is normal)', value='100', hint=lim('percent'), required=True)))}
{only('flow', _action_form('flow', 'Set the flow', _num_field('flow-percent', 'percent', 'Flow, percent (100 is normal)', value='100', hint=lim('percent'), required=True)))}
{only('save-settings', _action_form('save-settings', 'Save the printer settings (M500)'))}
{only('reload-settings', _action_form('reload-settings', 'Reload the saved settings (M501)'))}
{factory}
</div>"""

    # -- any G-code
    gcode_html = ""
    if has("gcode", "snippet"):
        how = ("Each line goes to the printer as its own command."
               if direct else
               "The lines run as one command, with the same header and "
               "cleanup.")
        gcode_html = f"""
<h2>Any G-code</h2>
<p>For anything the buttons do not cover. {how}</p>
<div class="actions">
{only('gcode', '''<form method="post" action="/printer/control/gcode" class="inline-form">
  <div class="setting-row"><label for="free-gcode">G-code lines, one
  per line</label>
  <textarea id="free-gcode" name="gcode" rows="4" cols="40"
            spellcheck="false"></textarea></div>
  <button id="btn-gcode">Send these lines</button>
</form>''')}
{only('snippet', f'''<form method="post" action="/printer/control/snippet" class="inline-form">
  <div class="setting-row"><label for="snippet">Ready-made
  commands</label>
  <select id="snippet" name="snippet">{snippet_options}</select></div>
  <button>Send the chosen command</button>
</form>''')}
</div>"""

    if direct:
        how_it_works = (
            "Commands go straight to the printer over your own network "
            "and take effect at once; the page follows each one and "
            "reads back what the printer reports. This is the first "
            "version for the K series: every button is documented by "
            "the community and read back from the printer, so a button "
            "the printer ignores says so in the events rather than "
            "pretending.")
    else:
        how_it_works = (
            "Each command is sent as a tiny job file, so it takes a few "
            "seconds and needs an idle printer; the page follows it and "
            "says when it is done. If the printer asks a question on its "
            "screen, press the knob on the printer - AccessSlice cannot "
            "answer it for you.")
    body = f"""
<h1>Control the printer</h1>
<p>Preheat, filament, moves, temperatures and more for {esc(printer_name)}
at {esc(ip) if ip else '(no address yet)'}, from this page - nothing
on the printer's touchscreen. {how_it_works}</p>
{connection_html}
<h2>The printer now</h2>
{now_html}
<p><a class="button" href="/printer/control">Read again</a>
<a class="button" href="/printer">Printer status page</a></p>
<h2>The command</h2>
{job_html}
{job_controls}
{preheat_html}
{filament_html}
{move_html}
{temperature_html}
{calibration_html}
{settings_html}
{gcode_html}
<p><a class="button" href="/">Home</a></p>
"""
    return page("Control the printer", body, msg=msg)


def printer_control_job_features():
    """The Core One's action set, for direct render calls without a
    features argument (tests)."""
    import printer_control
    return printer_control.JOB_FEATURES


def render_printer_files(ip, path, entries, msg=None):
    """The USB drive's files: start, delete, and an upload from a
    path on this PC. path: the folder shown ("" for the top)."""
    crumbs = '<a href="/printer/files">USB drive</a>'
    parts = [p for p in path.split("/") if p]
    for i, part in enumerate(parts):
        sub = "/".join(parts[:i + 1])
        crumbs += (' / <a href="/printer/files?path='
                   f'{esc(urlquote(sub))}">{esc(part)}</a>')
    rows = []
    for entry in entries:
        name = entry.get("display_name") or entry.get("name") or ""
        full = entry.get("path") or f"{path}/{name}".strip("/")
        if entry.get("type") == "FOLDER":
            rows.append(f'<li><a href="/printer/files?path='
                        f'{esc(urlquote(full))}">Folder: {esc(name)}</a></li>')
            continue
        size = entry.get("size")
        size_words = (f", {int(size) // 1024} KB" if isinstance(size, (int, float))
                      and size >= 1024 else f", {size} bytes"
                      if isinstance(size, (int, float)) else "")
        rows.append(f"""<li><strong>{esc(name)}</strong>{esc(size_words)}
<form method="post" action="/printer/files/start" class="inline-form">
  <input type="hidden" name="path" value="{esc(full)}">
  <label class="radio-row"><input type="checkbox" name="plate_empty"
    value="yes"> I checked: the build plate is empty</label>
  <button class="primary">Print {esc(name)}</button></form>
<form method="post" action="/printer/files/delete" class="inline-form">
  <input type="hidden" name="path" value="{esc(full)}">
  <label class="radio-row"><input type="checkbox" name="confirm"
    value="yes"> I mean it: delete {esc(name)}</label>
  <button>Delete {esc(name)}</button></form></li>""")
    listing = (f'<ul class="plain-list">{"".join(rows)}</ul>' if rows
               else "<p>This folder is empty.</p>")
    body = f"""
<h1>Files on the printer</h1>
<p>The files on the printer at {esc(ip)}: {crumbs}</p>
{listing}
<h2>Upload a file from this PC</h2>
<form method="post" action="/printer/files/upload">
  <div class="setting-row"><label for="upload-path">Full path of a
  .gcode or .bgcode file on this PC</label>
  <input type="text" id="upload-path" name="file" size="60"
         autocomplete="off"></div>
  <input type="hidden" name="folder" value="{esc(path)}">
  <label class="radio-row"><input type="checkbox" name="start"
    value="yes"> Start printing it at once (only with an empty plate)</label>
  <p><button class="primary">Upload</button></p>
</form>
<p><a class="button" href="/printer/files{('?path=' + esc(urlquote(path))) if path else ''}">Read again</a>
<a class="button" href="/printer/control">Control the printer</a>
<a class="button" href="/">Home</a></p>
"""
    return page("Files on the printer", body, msg=msg)
