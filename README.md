# AccessSlice

**An accessible slicer for 3D printing, built for blind and low-vision
makers and friendly for everyone.**

AccessSlice lets you prepare, slice and send 3D prints with a screen
reader, from start to finish. Every setting is a labelled field, every
result is spoken, and the printer can be followed and controlled from
ordinary pages instead of a touchscreen.

It is free, open source, and made by one person.

## Contents

- What AccessSlice does
- Which printers work
- What you need
- Installing AccessSlice
- The first start: the setup wizard
- What each printer brand needs
- A print job, step by step
- Moving around with a screen reader
- Privacy and safety
- When something goes wrong
- Supporting AccessSlice
- Reporting a problem or a new printer
- For developers
- Licence

## What AccessSlice does

Mainstream slicers such as Bambu Studio, PrusaSlicer and Creality
Print draw their own screens, and screen readers cannot use most of
them. AccessSlice does not replace the slicer. It replaces the
screens:

- A slicer project (a 3MF file) is a zip of plain text, so every
  setting in it - layer height, walls, infill, supports, filaments,
  where each object sits on the plate - can be read and changed as
  text. AccessSlice shows all of that as simple pages with real
  headings, labelled fields and buttons.
- It slices with your slicer's own slicing engine, so the result is
  exactly what the slicer itself would produce.
- It tells you the result in words: print time, filament used, and
  any warnings. A layer-by-layer preview describes every layer.
- It sends the print to the printer. Prusa and Creality printers get
  it straight over your home network; Bambu Lab printers get it
  through Bambu Studio's own send window, with AccessSlice telling
  you exactly which keys to press. A USB drive works for every
  printer.
- On Prusa and Creality printers it also reads the print's progress
  aloud and controls the printer: preheat, load and unload filament,
  move and home, pause and stop, and the files on the printer.

Nothing is hidden behind a picture. The 3D view of the plate and the
sliced preview are there for sighted helpers, and every fact in them
is also on the page in words.

## Which printers work

### Tested from start to finish

These printers have been used with AccessSlice for real prints, from
a new job to the finished part:

- **Bambu Lab X1 Carbon**, with Bambu Studio
- **Bambu Lab H2C**, with Bambu Studio
- **Prusa Core One**, with PrusaSlicer
- **Creality K2 Plus**, with Creality Print

### Expected to work, not tested yet

These use the same slicer and talk to the printer the same way as a
tested printer, so they should work. Nobody has tried them yet. If you
do, please report how it went (see "Reporting a problem or a new
printer").

- **Other Bambu Lab printers** that Bambu Studio can send to (P1S,
  P1P, A1, A1 mini, X1E, H2D and others). Sending goes through Bambu
  Studio, so anything Bambu Studio can print to should work.
- **Other Prusa printers with PrusaLink**: Core One L, MK4 and MK4S,
  MK3.9 and MK3.5, XL, and the MINI with Prusa's network module or a
  cable. An MK3S with PrusaLink on a Raspberry Pi probably works too.
- **Other Creality printers on Creality OS**: K2 and K2 Pro, K1, K1C,
  K1 Max and K1 SE, Ender-3 V3, V3 KE and V3 Plus, and the Creality Hi.
- **Anycubic printers with Anycubic Slicer Next**, and **other
  printers with OrcaSlicer**: editing and slicing work in
  AccessSlice; the send happens in the slicer's own window.

### Not supported

- Creality printers without Creality OS (for example the Ender-3 V3
  SE on its own, the CR-10 Smart, the Sermoon series), the SPARKX i7,
  resin printers, and rooted Creality printers whose Creality
  services were removed.
- Klipper printers reached through Moonraker (Voron, Sovol, Qidi,
  Elegoo and others) cannot be sent to or controlled yet. Their
  projects can still be edited and sliced if your slicer is one of
  the five above.

The details and sources are in `docs/printer-compatibility.md`.

## What you need

- **Windows 10 or 11, 64-bit.**
- **Your printer's slicer**, installed in its standard place: Bambu
  Studio, PrusaSlicer, Creality Print (version 7 is tested), OrcaSlicer
  or Anycubic Slicer Next. AccessSlice uses its slicing engine and
  its printer and filament profiles. The setup wizard tells you where
  to download it.
- **A screen reader** if you use one. AccessSlice is tested with NVDA.
  Its window reads like a web page in Microsoft Edge, so JAWS and
  Narrator work the same way.
- For Prusa and Creality printers: the printer and this computer on
  the same home network.

AccessSlice never needs the internet.

## Installing AccessSlice

1. Open the **Releases** page of this project on GitHub:
   https://github.com/Accessible3D-Edis/AccessSlice/releases
2. Under the newest release, download `AccessSlice-Setup-<version>.exe`
   (for example `AccessSlice-Setup-1.0.1.exe`).
3. Run the downloaded file. The installer is a standard Windows
   installer and reads well with a screen reader. Press Next through
   its pages; the defaults are right for almost everyone.
4. Start AccessSlice from the Start menu or the desktop shortcut. It
   opens in its own window.

Good to know:

- **"Windows protected your PC".** The installer is not yet
  code-signed (a signing certificate costs money every year), so
  Windows SmartScreen may show this warning the first time. Choose
  **More info**, then **Run anyway**. The whole source code is here
  to read.
- **Antivirus.** Some antivirus programs occasionally flag programs
  packaged the way AccessSlice is packaged (with PyInstaller). If
  yours does, restore the file and add an exception.
- **No administrator rights needed.** AccessSlice installs under your
  own user profile.
- **The window** uses Microsoft's WebView2, which is part of Windows
  11. On Windows 10 the installer offers Microsoft's free download if
  it is missing.
- **Uninstalling**: Windows Settings, Apps, AccessSlice, Uninstall.
  Your AccessSlice settings in your AppData folder are kept, so a
  reinstall picks up where you left off.

## The first start: the setup wizard

The first time AccessSlice starts, a setup wizard takes you through
everything it needs, one step per page. Each page says exactly what
to do and ends with a button for the next step. It takes about ten
minutes, most of it in the slicer's own installer.

1. **Choose your printer.** The four tested printers come first; the
   others are listed as not tested yet.
2. **Install the slicer and set up your printer in it.** The page
   names the maker's download site and what to do on the slicer's
   first start. It also says plainly what AccessSlice will and will
   not do with your printer.
3. **Prusa only: put the printer on the network** and turn on
   PrusaLink, on the printer's own screen.
4. **Where AccessSlice keeps your projects.** A folder in your
   Documents is suggested. AccessSlice finds the slicer program by
   itself and fills in its path.
5. **Prusa and Creality: how AccessSlice reaches the printer.** The
   printer's address (and, for Prusa, its PrusaLink password) is
   filled in from the slicer's own records when the slicer already
   knows the printer. **Test the connection and save** asks the
   printer before anything is saved. You can skip this step and do it
   at the first send instead.
6. **Save your first project** from the slicer. AccessSlice builds
   every new job on a project saved for that printer, because that is
   where the printer's settings and your filaments come from. A
   button opens the slicer for you, and **Check the projects folder**
   lists what AccessSlice found, with the printer each project was
   made for.
7. **Check and finish.** A summary of what is set up and what is
   still missing, then a short guided tour of AccessSlice, which you
   can skip.

The wizard can be run again at any time - for a second printer, for
example - from **Help and tour** or from **Settings**.

Some slicers' own first-start screens (signing in to Bambu Studio,
for example) are hard to use with a screen reader. If you get stuck
there, this is the one time a sighted helper is worth asking for. It
is needed once; AccessSlice does the rest from then on.

## What each printer brand needs

### Bambu Lab (Bambu Studio)

- Bambu Studio installed, signed in to your Bambu Lab account, and
  able to send to your printer: the printer is listed on Bambu
  Studio's **Device** page. If you already print from Bambu Studio,
  this is done. A printer in LAN-only mode works too, added in Bambu
  Studio with its access code.
- One project saved from Bambu Studio for that printer, in the
  AccessSlice projects folder.
- For each print, AccessSlice slices the job and opens it in Bambu
  Studio, and tells you the keys: **Ctrl+R** to re-slice (quick, same
  result) and **Ctrl+Shift+G** to send. Before that, AccessSlice moves
  Bambu Studio's Device page to the project's printer, so the send
  window offers the right one.
- AccessSlice does not read a Bambu printer's status or control it.
  That stays in Bambu Studio and the Bambu Handy app.

### Prusa (PrusaSlicer and PrusaLink)

- PrusaSlicer installed, with your printer added in its Configuration
  Wizard.
- The printer on your network: on the printer's screen, Settings,
  Network - Wi-Fi or a cable.
- **PrusaLink turned on**: Settings, Network, PrusaLink on the
  printer's screen. That screen shows the printer's **address** and
  its **password** (PrusaSlicer calls it the API key). AccessSlice
  needs both. If you added the printer to PrusaSlicer as a physical
  printer, AccessSlice reads them from there.
- **A USB drive in the printer.** Prusa printers keep print files on
  it, so that is where AccessSlice puts every print.
- One project saved from PrusaSlicer for that printer (Ctrl+Alt+S is
  Save project as), in the AccessSlice projects folder.
- AccessSlice then sends, follows and controls the printer itself.
  The password is stored protected by Windows (DPAPI), readable only
  by your Windows account on this computer.
- The printer's network name is remembered once an address has
  worked, so a new address from your router changes nothing.

### Creality (Creality Print)

- Creality Print installed (version 7 is tested), with your printer
  model chosen.
- The printer on the same network as this computer (on the printer's
  screen: Settings, Network) and **added on Creality Print's Device
  page** - Creality Print searches your network for it. AccessSlice
  reads the printer's address from Creality Print's own device list,
  so you never type it. You can also type it in the wizard.
- The printer stays on Creality's normal firmware. Nothing has to be
  rooted, and no password is needed.
- One project saved from Creality Print for that printer (Ctrl+Shift+S
  is Save project as), in the AccessSlice projects folder.
- Creating each new project happens in Creality Print, because its
  engine cannot turn a model file into a project by itself: Start a
  new print job opens Creality Print, you import the model and save,
  and AccessSlice opens the project. Only that step: slicing and
  sending to the printer always happen in AccessSlice.
- AccessSlice then sends, follows and controls the printer itself,
  including loading and unloading filament through the CFS.

### Anycubic and OrcaSlicer

- The slicer installed, with your printer chosen in it, and one
  project saved from it into the AccessSlice projects folder.
- Editing and slicing happen in AccessSlice; for the send, AccessSlice
  opens the sliced job in the slicer and tells you the keys.

## A print job, step by step

1. **Start a new print job** on the Home page. Choose the printer and
   give the path of your model file (an STL, STEP or OBJ).
   AccessSlice builds the project itself, on the settings of your
   newest saved project for that printer, and opens it. A large model
   can take a while; the building page tells you the stage and the
   time every 15 seconds.

   Creality printers differ in this one step only: Creality Print's
   engine cannot turn a model file into a project, so AccessSlice
   opens Creality Print for you. Import the model with Ctrl+I, save
   the project with Ctrl+Shift+S, and AccessSlice opens it the moment
   it is saved. Everything after that - settings, slicing and sending
   to the printer - happens in AccessSlice, exactly as for the other
   brands.

   Or **Open a project file** you saved earlier. Your projects folder
   is listed newest first.

2. **Edit** on the tabs: Quality, Strength, Support, Speed, Adhesion,
   Others, Filaments, Printer and presets, Placement, All settings and
   Backups. Each tab shows the settings that matter most in plain
   language, then every other setting on that topic. Changes are
   staged first; the Review page lists them with old and new values,
   and **Save** writes them all, with a backup of the file you started
   from.

3. **Slice now**, on the Slice and send page. Warnings come first, in
   words. The slice report gives the print time, the filament used,
   and anything the engine complained about. **Preview** describes the
   print layer by layer.

4. **Send**: straight to a Prusa or Creality printer over your
   network, to a USB drive with one button, or through the slicer's
   own send window for Bambu Lab printers. Before a print starts,
   AccessSlice asks you to check with your hands that the build plate
   is empty.

5. **Print.** For Prusa and Creality printers, the Printer status page
   reads the progress aloud every half minute and announces the end.

## Moving around with a screen reader

- **Landmarks.** Every page has a banner with the top navigation
  (Home, Open a project, Settings, Help and tour, Diagnostics) and a
  main region. Editor pages add a navigation named "Project sections"
  for the tabs.
- **Headings.** One heading level 1 per page; sections are level 2,
  setting groups level 3. The heading key is the fastest way through
  a long tab.
- **Results.** After every button, the result is spoken and shown in
  a status line near the top. Focus stays on the control you pressed,
  so you never land back at the top of the page.
- **Fields.** Every field has a label, a unit where one applies, and
  a help text. Values stay exactly as the slicer stores them.
- **Questions.** Where the slicer would show a dialog (a 100% infill
  choice, millimetres or percent), AccessSlice asks the same question
  as a page with Yes and No.
- **Zoom and themes.** Pages reflow up to 400% zoom without sideways
  scrolling, and follow Windows' light or dark setting.

**Help and tour** in the top navigation explains every page and tab,
and can be read at any time.

## Privacy and safety

- **Nothing leaves your computer** unless you press a button that
  says so. AccessSlice talks only to itself (on 127.0.0.1) and, when
  you send or follow a print, to your own printer on your own
  network. It never uses the internet and contains no tracking or
  statistics.
- The only exception is **Buy me a coffee**: pressing it opens the
  donation page in your normal web browser. Nothing is sent about you.
- AccessSlice refuses requests that do not come from its own window,
  so a website open in your browser cannot reach it.
- **Your files are safe.** Every save keeps a backup (the last ten are
  on the Backups tab, restorable with one button) and checks the new
  file before saying it was saved. If a check fails, the original is
  put back untouched.
- **Printer safety.** Moves are checked before they are sent: nothing
  below the plate, no sideways move with the nozzle low, a lift before
  homing. A print only starts after you confirm the plate is empty.

## When something goes wrong

- **Diagnostics**, in the top navigation, lists what AccessSlice did,
  in order, with the full details of any unexpected error. Your files
  are never changed by an error.
- **"The slicer program was not found"**: open Settings and leave the
  slicer path field empty; AccessSlice looks in the standard install
  folders. Or paste the path to the slicer's program; a desktop
  shortcut works too.
- **"The printer did not answer"**: check that the printer is on and
  on the same network, and that the address is right (the printer's
  network settings screen shows it). For Prusa, check that PrusaLink
  is on and that the password matches.
- **Bambu Studio's send window shows the wrong printer**: pick yours
  at the top of the send window. AccessSlice moves Bambu Studio to the
  project's printer when Studio is closed; when it was already open,
  AccessSlice asks you first.
- **A new job from a very large model is slow**: that is the slicer's
  engine importing millions of triangles. The building page keeps
  telling you the time; Cancel building stops it and keeps nothing.

## Supporting AccessSlice

AccessSlice is free and made by one person. A week after you first
start it, a small pop-up asks whether you would like to buy me a
coffee, and after that it asks about once a month. **Buy me a coffee**
opens buymeacoffee.com/accessible3d in your browser; **Not now** (or
Escape) closes it until next month. There is also a quiet link on the
Home page for any time.

## Reporting a problem or a new printer

Please use the **Issues** page of this project on GitHub:
https://github.com/Accessible3D-Edis/AccessSlice/issues

Helpful things to include:

- What you pressed, what you heard, and what you expected.
- The end of the Diagnostics page (select it and copy it).
- Your printer model, your slicer and its version, and your screen
  reader.

Tried a printer from the "not tested yet" list? Please report it
either way - it working is as useful to hear as it failing. Printers
move to the tested list as they are confirmed.

## For developers

AccessSlice is Python 3 using only the standard library, with
server-rendered HTML pages. The installed program adds pywebview for
its window.

### Running from source

With Python 3.13 installed:

    python app\accessslice.py

That opens AccessSlice in your default browser with a console - the
development mode. The installed program's entry point is
`app\accessslice_window.py` (its own window; needs
`pip install pywebview`).

### Running the tests

    python -m unittest discover -s tests -v

728 checks, standard library only, fully offline and hermetic: they
never touch your real configuration or your slicers' settings, and
never launch a real program. A full run takes about half an hour.
`tests/README.md` describes what each file covers.

### Building the installer

    pip install pyinstaller pywebview
    python packaging\build.py

That produces the one-folder bundle `packaging\dist\AccessSlice\`
and, with Inno Setup 6 installed, the installer
`packaging\output\AccessSlice-Setup-<version>.exe`. The version number
lives in `app\version.py`. After a build,
`python -m unittest tests.test_packaged -v` drives the built program
itself. Code signing is one configuration file away; see
`packaging\README.md`.

### Project layout

- `app/` - the program:
  - `accessslice.py` - the development entry point (browser tab).
  - `accessslice_window.py`, `appwindow.py` - the installed program's
    entry point: its own window, the server stopped when it closes.
  - `server.py` - the local web server and routing.
  - `webui.py` - every screen as accessible server-rendered HTML.
  - `wizard.py` - the setup wizard's printers and steps.
  - `backends/` - one module per slicer (Bambu Studio, Creality
    Print, OrcaSlicer, Anycubic Slicer Next, PrusaSlicer) behind one
    interface.
  - `slicer_project.py` - the open project, staged changes, safe
    saving.
  - `settings_catalog.py` - plain-language knowledge about settings.
  - `profiles.py`, `prusa_profiles.py`, `creality_profiles.py` - the
    slicers' own preset libraries.
  - `engine_driver.py`, `build_job.py`, `newjob.py`,
    `prusa_newjob.py` - slicing and building new projects with the
    slicers' engines.
  - `creality_lan.py`, `prusalink.py` - the direct sends, status and
    file calls for Creality's LAN protocol and PrusaLink.
  - `printer_control.py`, `motion_guard.py` - driving the printer, and
    the safety checks in front of every move.
  - `toolpath.py`, `bgcode.py`, `preview.py` - the layer-by-layer
    preview.
  - `viewport.py` - the 3D view of the plate.
  - `config_store.py` - the saved configuration in AppData (printer
    passwords DPAPI-protected).
  - `coffee.py` - the buy-me-a-coffee pop-up's rules.
  - `diagnostics.py` - the activity log behind the Diagnostics page.
  - `static/` - the stylesheet (light and dark), the app's script, the
    3D view and preview scripts, and the vendored Three.js (MIT).
- `tests/` - the automated test suite.
- `testdata/` - test projects saved from each slicer (our own
  models).
- `packaging/` - the PyInstaller spec, the Inno Setup script, the
  build script and the icon.
- `tools/` - the 3MF text-editing tool.
- `docs/printer-compatibility.md` - which printers can be reached, and
  why.

### Contributing

See `CONTRIBUTING.md`: the two rules (accessibility is a shipping
requirement; every bug found by hand becomes a test first), how to
run and test the code, and the short contributor agreement.

Principles the code follows:

- **Accessibility is a shipping requirement, not a feature.** Every
  control labelled, keyboard-operable, screen-reader tested.
- **Visually clear too** - large readable text, strong contrast, dark
  mode, a clean layout.
- **No second-class settings.** Every slicer setting is editable, not
  just a curated subset.
- **Safe by default.** Backups and verification on every save.
- **A real program.** Installs like any Windows app; no Python, no
  command line, no internet needed.

## Licence

AccessSlice is free software under the **GNU General Public License,
version 3** (see `LICENSE`). You may use it, study it, change it and
share it; if you distribute a changed version, it must stay under the
same licence with its source available. Copyright 2026 Edis Adilovic.
Other licensing terms, for example for a closed product, are available
from the copyright holder. Bundled third-party components and their
licences are listed in `THIRD_PARTY_NOTICES.md`.
