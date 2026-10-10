"""backends.base - the contract every slicer backend fulfils.

Phase III of the real-slicer plan: everything slicer-specific lives
behind one SlicerBackend object per slicer, so OrcaSlicer, Creality
Print, Anycubic's slicer and PrusaSlicer can be added without
redesigning the app. The five parts (docs\\phase3-backend-refactor.md):

1. Project format  - open_project() / ProjectError.
2. Settings catalog - .catalog (keys, labels, tabs, dialogs).
3. Profile library  - .profiles (system + user presets).
4. Engine driver    - .engine (headless slicing), .newjob (project
   creation), detect_exe(), slicer_running(), .compat (version
   cautions), .printer_switch (conf-file printer selection, or None
   for slicers without one), .preflight (warnings computed from
   the settings and meshes before slicing - preflight.py).
5. Guided-flow texts - .texts (the keyboard shortcuts and sentences
   webui speaks about this slicer's own GUI).

The parts are today's proven modules, reached through the backend
instead of imported directly - so the Bambu implementation is a thin
facade, tests' monkeypatch seams keep working, and a future backend
swaps whole parts without touching the front end.
"""


from errors import ProjectError


class BackendError(ProjectError):
    """A backend could not be provided; message is plain words.

    A ProjectError subclass so the front end's existing friendly-
    error handling shows it like any other Problem message.
    """


class SlicerBackend:
    """One slicer's implementation of the five parts.

    Subclasses set the class attributes and override the methods.
    Anything a slicer does not have stays None (e.g. printer_switch
    for slicers without an editable conf file) - callers check.
    """

    id = ""             # config_store.SLICERS id, e.g. "bambu"
    display_name = ""   # e.g. "Bambu Studio"
    supported = False   # appears enabled in the first-run picker
    exe_names = ()      # valid program file names, for setup checking

    ProjectError = None  # the friendly-error class open_project raises
    catalog = None       # part 2: settings catalog module
    profiles = None      # part 3: profile library module
    engine = None        # part 4: headless slicing module
    newjob = None        # part 4: project-creation module
    compat = None        # part 4: version-caution module
    printer_switch = None  # part 4: conf-file printer selection, or None
    lan = None           # part 4: direct printer LAN send/status, or None
    usb_wants_plain_gcode = False  # USB copy unpacks the .gcode from
    #                                the container (K series); needs lan
    preflight = None     # part 4: warnings before slicing, or None
    texts = None         # part 5: guided-flow texts

    def open_project(self, path, slicer_exe=None):
        """A live editing session on the project file (part 1)."""
        raise NotImplementedError

    def preset_lists(self, slicer_exe, printer_name):
        """Preset names per scope for the presets tab (part 3).

        {"process": [...], "filament": [...], "machine": [...]} -
        process and filament filtered to the given printer where the
        slicer's preset naming allows it, machine never filtered
        (switching printer is the point), the user's own saved
        presets always included.
        """
        raise NotImplementedError

    def detect_exe(self):
        """First standard install path that exists, or None (part 4)."""
        raise NotImplementedError

    def slicer_running(self, slicer_exe):
        """True when the slicer's GUI process is running (part 4)."""
        raise NotImplementedError
