# AccessSlice test suite

Run everything from the project root:

    python -m unittest discover -s tests -v

No extra packages are needed - the suite is stdlib-only, like the app.
The watcher tests genuinely wait for the 1-second polling loop, and
the real-slicer integration checks run when those slicers are
installed, so the whole run (700 checks) takes eight to fifteen
minutes depending on the machine.

## What the suite guarantees

- **The engine** (`test_engine.py`): 3MF zip round-trips, forward-slash
  entry names (backslash names corrupt the file for Bambu Studio), and
  structural verification that really catches broken JSON, broken XML,
  and missing entries.
- **The session** (`test_project_session.py`): opening and reading the
  committed H2C test project, staging of settings / moves / extruders /
  filament maps / presets, the 100%-infill and mm-or-% Studio-parity
  questions, and the full save pipeline - backup beside the file,
  verification, restore-on-failure, and the
  `different_settings_to_system` slot bookkeeping that Bambu Studio
  silently discards edits without.
- **The catalog** (`test_settings_catalog.py`): every curated key
  really exists in the test project (typo guard), control typing
  (checkboxes only for known booleans), and the Studio-verified tab
  placements.
- **The profile library** (`test_profiles.py`): preset listing,
  inheritance resolution, and key-scope classification - both learned
  from a library and via the no-library fallback heuristic.
- **BambuStudio.conf editing** (`test_studio_config.py`): the checksum
  format, printer switching with preset remapping, the Device-page
  serial fix, backups, and no-op detection.
- **The watcher** (`test_watcher.py`): fresh saves are found, backups
  and pre-existing files are not, re-saves are.
- **Backups** (in the session and HTTP suites): every save keeps the
  version it replaced, retention prunes to the newest ten, restore
  swaps the file back (proven on disk), the restore is itself backed
  up, staged changes are dropped with an announcement, and path
  tricks in a posted backup name are rejected.
- **Multi-plate projects** (`test_multiplate.py`): plate membership
  and the verified grid layout, replating with exact round trips,
  per-plate filament maps staying independent, unreliable layouts
  announced and blocking replating, and - when the local multi-plate
  specimens are present - read-only checks against real 36-plate and
  21-plate Studio projects.
- **Rotation and scaling** (`test_transforms.py` plus HTTP checks):
  exact matrices for the 90-degree and 200% cases, move + rotate +
  scale combining in one save, scale round trips back to identity,
  3D-rotated objects reporting honestly, and friendly errors for
  nonsense values.
- **Automatic placement** (`test_autoplace.py`): the transform
  conventions and the rotation builder on exact cases, the shelf
  packer (separation, margins, keepout corner, honest refusal), the
  lie chooser on synthetic boxes (flat stays, sideways flips, an
  edge-balanced cube falls flat), and the real Benchy end to end -
  the parsed mesh sits at exactly z = 0 (validating the whole
  transform chain), the saved Benchy keeps its designer's lie, a
  tilted copy is stood back onto the plate, a stacked pair arranges
  apart and survives save/reopen, scaling keeps the bottom on the
  plate, and the Placement buttons work over real HTTP.
- **Per-object overrides** (`test_object_settings.py` plus HTTP
  checks): the committed Studio-saved specimen reads back exactly as
  written, staging/removal/save round-trips work (including creating a
  block for objects without one), companion infill keys stage and
  remove together, globals and the preset-diff bookkeeping stay
  untouched, and the written lines match Studio's own format.
- **Version cautions** (`test_compat.py` and friends): verified Studio
  versions stay quiet, unverified ones warn without refusing, and
  unparseable ones stay quiet while the structural checks still guard.
- **Edge projects** (`test_edge_projects.py`): multi-object,
  empty-plate, multi-plate, odd-list-shape, future-key, unicode-name,
  and broken-archive projects all open (or refuse in a friendly way),
  render every page, and stay editable where that makes sense.
- **The stale-file guard** (in the session and HTTP suites): an
  outside save (Bambu Studio rewriting the open 3MF) is detected,
  saving/restoring/sending refuse while stale and the outside version
  survives untouched, the warning banner appears and clears, and
  reload picks up the disk truth while naming the staged changes it
  dropped.
- **Graceful failure** (`test_diagnostics.py` and parts of the session
  and HTTP suites): the diagnostics log (ordering, rotation, tracebacks,
  never raising), the open-time structure sanity notes (extra plates,
  unreadable transforms, missing metadata - and zero notes for the
  clean test project), locked-file saves restoring the project
  byte-identical, and the unexpected-error page keeping raw tracebacks
  off the screen and in the log.
- **Pre-flight warnings** (`test_preflight.py`): the supports check
  built 2026-09-02 - overhang measuring on synthetic boxes (resting,
  floating, tilted against two thresholds, scaled), the three real
  projects from three printers through both backends (the H2C Benchy
  with staged edits, the X1C cane's per-object override, the K2 Plus
  Dodecagon via Creality Print), honest silence for a missing mesh,
  the rule-of-thumb fallback for an unreadable threshold, the cache,
  and the Slice-and-send page over HTTP in all three states. Plus
  the head-crash class for by-object printing: convex outlines and
  the gap between them, two Benchies on the H2C in every layout
  (rod band, clearance radius, lid limit, the print-last wording,
  separate plates, missing numbers) and the K2 Plus Dodecagon
  through Creality Print with the older clearance-radius spelling.
  And the cheap set: floating and sunk objects (auto-orient setting
  them down, over HTTP too), taller than the printer, the real
  cane's tiny foot with the brim-dependent fix and raft exemption,
  spiral vase with two objects on a plate.
- **Print monitoring** (`test_printer_monitor.py`): the K family's
  state codes as phases (unknown codes never guessed), /api/printer
  over HTTP against the fake K2 (running, finished, stopped, a
  vanished printer said in words), and the printer page carrying the
  live region only while a job runs.
- **The PrusaSlicer backend** (`test_prusa_backend.py`): the second
  project format - the INI-style settings entry round-tripping byte
  for byte with per-extruder values in three spellings, the model
  config's object lines, the vendor bundle with multi-parent
  inheritance and the installed-models filter, user presets, key
  scopes; the session on the committed Core One project and on a
  real 2.9.4-saved five-tool tile (settings, lists, overrides,
  transforms, presets, the infill question, refusals and cautions);
  the engine on binary and ASCII G-code and refusals; the project
  builder from presets and from a donor; pre-flight through the
  Prusa key table; every editor page over HTTP plus a fake slice to
  a report; and a real build-and-slice when PrusaSlicer is installed.
- **OrcaSlicer and Anycubic Slicer Next** (`test_orca_family.py`): the
  shared Orca-family backend - identities and registration, version
  stamps (Orca's own metadata, Anycubic's Bambu-compatible string,
  silence for another program's), the library over installed vendors
  and without a config folder, the committed specimens, the builder
  from presets with objects set down on the plate, preset lists by
  both naming conventions, every editor page over HTTP plus a fake
  slice, and real builds and slices with both programs installed.
- **The design system** (`test_design.py`): WCAG contrast of every
  text-on-background pair the stylesheet uses, computed for the light
  AND the dark theme (4.5:1 text, 3:1 control boundaries and the
  focus ring); no colour defined only for dark mode; the focus ring,
  the wrapping rule and the layout breakpoints still there; the
  shared header navigation on every page; extra scripts only where
  asked for.
- **The 3D view** (`test_viewport.py`): the scene description is a
  TRUE mirror - staged moves, turns and sizes come out as the very
  numbers the save writes, the edge-overhang flag follows the staged
  position and the pre-flight footprint, filament colours per
  extruder, the mesh blob's header and triangles match the parsed
  mesh and are packed once; two-plate projects (only the shown plate
  carries meshes, a staged move-to-plate moves the object in the
  view, the plate chooser); a PrusaSlicer project draws the same
  way; the Placement page hides the canvas from screen readers while
  keeping every operation a real control; the routes and the
  vendored Three.js over HTTP; and, when the private 36-plate
  specimen is on this machine, that the page renders without reading
  a mesh and one plate's shapes are ready in seconds.
- **The sliced preview** (`test_toolpath.py`): PrusaSlicer's binary
  G-code decoded (heatshrink and MeatPack on hand-made vectors, then
  the real Core One output matching the engine's own ASCII export
  move for move), the parse of real Bambu Studio and PrusaSlicer
  output (layers, heights, feature kinds in both naming schemes,
  arcs as chords, wipes and travel never counted as extrusion, layer
  start times from M73), the binary payload's layout with and
  without travel, the spoken facts and the layer sentence, plate
  choice inside a multi-plate output, the once-per-file parse cache
  with failure in words, and the page over HTTP in all its states
  (no slice yet, preparing with self-refresh, ready with real
  controls and the canvas hidden, a chosen layer without JavaScript,
  the report's link, a PrusaSlicer binary output, an unreadable
  file).
- **The whole app over HTTP** (`test_server_http.py`): every editor
  page renders with its jump heading, staging and both question flows
  work end to end, save returns to the right page and the file on disk
  proves it, static file path traversal is blocked, and closed-session
  routes redirect home.

- **Only the app's own window is served** (`RequestOriginTests` in
  `test_server_http.py`): a Host header naming another server is
  refused (DNS rebinding), a POST with another site's Origin,
  Referer or Sec-Fetch-Site is refused with nothing changed and the
  attempt logged, and the headers the app's own page sends pass.
- **The app window** (`test_appwindow.py`): the launcher the packaged
  program runs - the server answers only while the window is open
  and stops the moment it closes, a busy preferred port is skipped,
  no free port is a spoken message, a window that cannot open falls
  back to the browser with the reason, `--serve` runs headless. The
  window itself is never opened: a stand-in function plays the user.
- **Copies, removal, add a model, new plates** (`test_object_ops.py`):
  full-object copies in the Bambu family and inline copies in
  PrusaSlicer, structure and verification, numbering, removal with
  cleanup and the last-object guard, a new plate with a reliable
  grid, objects following their plate when the grid grows, the spill
  onto new plates, the honest arrange message, add-model through the
  fake engines with staging cleanup, and the Placement routes.
- **The tour and the coffee pop-up** (`test_tour_and_coffee.py`):
  the due rules (a week after the first start, then monthly), the
  tour on every page and after the first setup only, every editor
  tab's link to its section, the pop-up dialog on Home when due and
  nowhere else, the script making it modal with Escape as Not now,
  both buttons resting it a month, the browser opener replaced by a
  stand-in.
- **The setup wizard** (`test_wizard.py`): a first run landing in
  it, only the four printers tested in the house called tested, each
  printer's steps one page at a time (one h1, labelled fields), the
  folders step saving like Settings and refusing a wrong program, the
  network step filled in from PrusaSlicer's physical printer, saving
  the address and password only after a stand-in printer answered,
  skippable, the project step reading each project's printer from
  the 3MF, Finish leading into the tour, and the links from Settings,
  Home and the tour.
- **Switching slicers on Settings** (`test_settings_switch.py`): the
  per-slicer path memory, the old slicer's path in the field replaced
  by the new slicer's remembered or detected path at save, a typed
  path kept, an unfindable slicer refused without saving, and every
  slicer's path on its radio for the live swap.
- **The signing step** (`test_packaging_signing.py`): the build
  script's code-signing command from a local configuration - unsigned
  without one, the digest / timestamp / certificate arguments, Inno
  Setup's spelling of the same command, bad configurations refused;
  signtool is never run.
- **No terminal window** (`test_noconsole.py`): the no-window flag
  for console helpers (the PrusaSlicer engine, tasklist,
  powershell), a real launch accepting it, and a read of every app
  source file proving each subprocess call passes it - except the
  two named launches the user is meant to see (the slicer on a
  send, File Explorer).
- **The packaged program** (`test_packaged.py`): the built
  `AccessSlice.exe` (from `packaging\build.py`) started headless with
  a sandboxed `%APPDATA%` and driven over HTTP - every runtime file
  shipped, every backend on the setup page, a project opened through
  the bundled 3MF engine, the version on Diagnostics, the exe
  windowed and version-stamped. Skipped when no build exists.
- **Printer control** (`test_printer_control.py`,
  `test_printer_control_http.py`): against the simulated Core One
  (temperatures move, files run line by line, a load stops for the
  knob, the 170 degree standby, continue refused like the firmware)
  - every G-code builder line for line, the form-to-command table
  with its refusals in words, the command-job runner (a beep runs
  and its file is removed, a load waits for the knob and ends cold,
  a printing printer refuses in words, a timed-out job leaves its
  file, a fast job is caught by its new id), the PrusaLink job and
  file calls, and the pages over real HTTP: connection saved after a
  hello, a preheat followed through `/api/control` to done, the one
  move form, typed G-code, pause / resume / stop of a running print,
  the files page's start, delete and upload with their confirmations,
  and a slicer without control sent home in words.
- **The motion guard** (`test_motion_guard.py`, plus checks in both
  control page suites): the floor (no move may end below Z 0, the
  refusal names the room left), sideways clearance (no X or Y move
  with the nozzle under 1 mm), the lift to 5 mm before a home, a
  park or a plate-travelling routine from a low nozzle (job mode:
  lift lines prepended and said; direct mode: a lift step that must
  be seen in the read-back, or the home is not sent), refusals in
  words when no height can be read and the hand-check tick for a
  home after a power-up, typed G-code walked the same way with
  G92 / M211 / SET_KINEMATIC_POSITION and the like never passed on,
  a go-to going up first and sideways after, direct moves refused
  mid-print, and - against the fake K2, which now makes a move into
  the plate exactly like the real printer (Klipper position_min
  -10) - a jog into the plate never reaching it and a home from a
  pressed nozzle lifting first. Written after the 2026-09-15 plate
  damage on the K2 Plus.
- **K-series control** (`test_creality_control.py`): against the fake
  K2 that applies set commands to its status - every button's exact
  params (temperatures, the bed's num and val, the chamber, three
  fans, light, pause and stop, homing words, a jog with the axis
  feed, signed Z offsets, speed and flow, one gcodeCmd per line, the
  CFS feed and retract with box and slot), the LAN calls (set read
  back, pause / resume / stop, hello, CFS info, file list with the
  printer's paths, delete, position and the extra status lines), the
  step runner (a preheat read back, a home waited for, a second
  command refused), and the pages over HTTP in direct mode.
- **Protected passwords** (`test_config_secrets.py`): the PrusaLink
  password saved DPAPI-encrypted, loaded decrypted, never on disk in
  plain text, a foreign blob left out, plain off Windows.
- **No private data** (`test_no_private_data.py`): no private
  network address and no printer-serial-shaped token in any tracked
  text file; no path into a user's own Windows profile in the public
  files or inside the test projects; no slicer account number in a
  test project.

## Hermetic by construction

Tests never touch your real configuration or Bambu Studio:

- AccessSlice's config path and `%APPDATA%` are pointed into a
  throwaway sandbox for the duration of each test.
- A fake Bambu Studio (stub exe + miniature profile library built in
  `support.py`) stands in for the real install.
- The committed test project is always copied first; `testdata\` is
  never written to.
- No test launches a program or opens anything beyond 127.0.0.1.

The routes that launch the real slicer (POST `/start`, POST
`/project/send`) are intentionally untested here - they need a human
and a real Studio, and live in `docs\edis-test-checklist.md` instead.

## Optional extras

- If a real Bambu Studio is installed at the standard path, a few
  read-only integration checks against its genuine profile library run
  automatically (they are skipped otherwise).
- Set `ACCESSSLICE_EXTRA_PROJECT` to the path of another saved Bambu
  Studio project (for example an X1C one) to run an open-stage-save
  smoke test against a copy of it as well.
