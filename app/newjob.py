"""newjob.py - create a project straight from a model file (Phase II).

The one screen the old flow could not skip was Bambu Studio's import
dialog. This module removes it: a new print job starts from a model
file (STL and friends) picked inside AccessSlice, no slicer GUI.

How it works - the donor-merge design (probed 2026-08-31, log entry):

- The engine's own settings loading is BROKEN upstream for H2-series
  printers: any project the CLI builds from --load-settings JSONs
  refuses to slice (rc -66, "Some filaments cannot be mapped"), no
  matter what map keys or flags are supplied. A project SAVED by
  Studio slices fine.
- But the CLI happily MERGES: given a Studio-saved project FIRST and
  model files after it ("File list order... 3mf in the first place"),
  `--allow-newer-file --arrange 1 --export-3mf out.3mf donor.3mf
  model.stl` writes a new project whose project_settings are the
  donor's nearly verbatim - and THAT project slices (proven for the
  H2C end to end). Without --allow-newer-file the CLI dies on its own
  version check before writing result.json.
- A donor stripped to ZERO objects is refused by the engine's parser,
  so the donor keeps its objects through the merge and AccessSlice
  removes them afterwards (their object ids survive the merge
  unchanged - verified), leaving only the new model.

So: find a donor project for the chosen printer, merge, remove the
donor's objects, centre what remains with the app's own arrange, and
open. Every new job inherits a known-good, Studio-blessed settings
base for that printer. If no donor exists yet, the guided Studio
round-trip runs once - and its saved project becomes the donor for
every job after it.
"""

import json
import shutil
import subprocess
import re
import tempfile
import zipfile
from pathlib import Path

import build_job
from slicer_project import ProjectError, ProjectSession
import edit_bambu_3mf as engine  # noqa: E402 (slicer_project adds tools/)

# Model formats Bambu Studio's importer takes; STL is the proven one.
MODEL_SUFFIXES = {".stl", ".step", ".stp", ".obj"}

# As long as a slice may take (engine_driver.TIMEOUT_S): PrusaSlicer
# needs about five minutes just to import a 23-million-triangle STL
# (Skyward Dragon, 2026-09-25), and the old 300 s stopped it.
MERGE_TIMEOUT_S = 60 * 60


def find_donor(candidates, printer_name):
    """The newest project saved for this printer, or None.

    candidates: paths to try, best first (the caller orders them by
    modification time). A candidate only needs a readable
    project_settings.config naming the printer - broken files are
    skipped silently, they just cannot donate.
    """
    for path in candidates:
        path = Path(path)
        if not path.is_file():
            continue
        try:
            with zipfile.ZipFile(path) as z:
                ps = json.loads(z.read("Metadata/project_settings.config"))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
        if ps.get("printer_settings_id") == printer_name:
            return path
    return None


def remove_objects(project_path, objectids):
    """Surgically remove objects from a project (at least one must stay).

    Drops, for each id: the object definition and build item in
    3D/3dmodel.model, the per-object block and plate model_instance in
    Metadata/model_settings.config, and any 3D/Objects mesh file no
    remaining object references (plus its relationship entry).
    """
    ids = {str(i) for i in objectids}
    entries = engine.load_entries(project_path)

    model = entries[engine.MODEL_ENTRY].decode("utf-8")
    removed_paths = set()
    for oid in ids:
        pattern = (r"[ \t]*<object id=\"" + re.escape(oid)
                   + r"\"[^>]*>.*?</object>\n?")
        block = re.search(pattern, model, re.S)
        if block:
            removed_paths.update(re.findall(r'p:path="([^"]+)"',
                                            block.group(0)))
            model = model[:block.start()] + model[block.end():]
        model = re.sub(r"[ \t]*<item objectid=\"" + re.escape(oid)
                       + r"\"[^>]*/>\n?", "", model)
    # A mesh file stays if any remaining object still references it.
    still_used = set(re.findall(r'p:path="([^"]+)"', model))
    entries[engine.MODEL_ENTRY] = model.encode("utf-8")

    ms = entries[engine.MODEL_SETTINGS_ENTRY].decode("utf-8")
    for oid in ids:
        ms = re.sub(r"[ \t]*<object id=\"" + re.escape(oid)
                    + r"\">.*?</object>\n?", "", ms, flags=re.S)
        ms = re.sub(r"[ \t]*<model_instance>\s*<metadata key=\"object_id\""
                    r" value=\"" + re.escape(oid)
                    + r"\"/>.*?</model_instance>\n?", "", ms, flags=re.S)
        # The assemble block references objects too; a dangling
        # assemble_item makes the engine refuse the whole file with
        # -6 "can not be parsed" (found the hard way, 2026-08-31).
        ms = re.sub(r"[ \t]*<assemble_item object_id=\""
                    + re.escape(oid) + r"\"[^>]*/>\n?", "", ms)
    entries[engine.MODEL_SETTINGS_ENTRY] = ms.encode("utf-8")

    for path in removed_paths - still_used:
        entry = path.lstrip("/")
        if entry in entries:
            del entries[entry]
        rels = "3D/_rels/3dmodel.model.rels"
        if rels in entries:
            r = entries[rels].decode("utf-8")
            r = re.sub(r"[ \t]*<Relationship [^>]*Target=\""
                       + re.escape(path) + r"\"[^>]*/>\n?", "", r)
            entries[rels] = r.encode("utf-8")

    engine.save_entries(project_path, entries)


def create_project(exe, donor_path, model_paths, target_path):
    """Create target_path from the models, on the donor's settings.

    Returns plain-language notes about what happened. Raises
    ProjectError with the engine's own message when the merge fails.
    """
    donor_path = Path(donor_path)
    target_path = Path(target_path)
    models = [Path(m) for m in model_paths]
    donor_ids = [o["id"]
                 for o in ProjectSession(donor_path).objects()]

    staging = Path(tempfile.mkdtemp(prefix="accessslice-newjob-"))
    try:
        out_name = "new-project.3mf"
        cmd = build_merge_command(exe, donor_path, models, out_name)
        build_job.stage("The slicer's engine is reading the model and "
                        "building the project.")
        try:
            build_job.run(cmd, cwd=str(staging), timeout=MERGE_TIMEOUT_S,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ProjectError(
                "The slicer engine was still building the project after "
                f"{MERGE_TIMEOUT_S // 60} minutes and was stopped. Try "
                "again, or use the guided way further down the Start "
                "page.")
        except OSError as e:
            raise ProjectError(
                f"The slicer engine could not be started ({e}). Check "
                "the slicer path on the Settings page.")
        # The exe is a GUI-subsystem binary: result.json is the truth.
        try:
            result = json.loads((staging / "result.json")
                                .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ProjectError(
                "The slicer engine ended without writing its result "
                "file while building the project, so the outcome is "
                "unknown. Try again, or use the guided way further "
                "down the Start page.")
        if result.get("return_code") != 0:
            raise ProjectError(
                "The slicer engine could not build the project. Its "
                f"own message: {result.get('error_string')}")
        made = staging / out_name
        if not made.is_file():
            raise ProjectError(
                "The slicer engine reported success but wrote no "
                "project file. Try again, or use the guided way "
                "further down the Start page.")

        build_job.stage("Checking the built project.")
        remove_objects(made, donor_ids)

        # Prove the result opens before it reaches the projects folder.
        session = ProjectSession(made)
        if not session.objects():
            raise ProjectError(
                "The created project ended up without any object - "
                "the model file may not be importable. The engine "
                "accepts STL, STEP and OBJ files.")
        shutil.move(str(made), str(target_path))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    notes = [f"Created {target_path.name} from "
             + ", ".join(m.name for m in models)
             + f" with the settings of {donor_path.name} as the "
             "starting point."]

    build_job.stage("Centring the model on the plate.")
    # Centre the new model with the app's own arrange - the engine
    # arranged it AROUND the donor's now-removed objects.
    try:
        session = ProjectSession(target_path, slicer_exe=exe)
        for p in session.plates():
            session.stage_arrange(p["id"])
        if session.pending:
            session.save()
    except ProjectError:
        notes.append("The model was left where the engine placed it - "
                     "the Placement tab's Arrange button centres it.")
    return notes


def build_merge_command(exe, donor_path, models, out_name):
    """The CLI invocation (split out so tests can stand in a fake)."""
    return [str(exe), "--allow-newer-file", "--arrange", "1",
            "--export-3mf", out_name, str(donor_path)] \
        + [str(m) for m in models]


def unique_target(projects_dir, model_path):
    """blind-Benchy.stl -> <projects>/blind-Benchy.3mf, never clobbering."""
    stem = Path(model_path).stem or "new project"
    target = Path(projects_dir) / f"{stem}.3mf"
    n = 2
    while target.exists():
        target = Path(projects_dir) / f"{stem} ({n}).3mf"
        n += 1
    return target
