"""step_mesh.py - STEP models turned into meshes the engines can take.

A STEP file is a CAD model (exact surfaces), not a mesh. The slicers'
windows mesh it on import, but their command-line engines - the ones
AccessSlice drives - do not. Bambu Studio 2.9, OrcaSlicer and Creality
Print all refuse EVERY STEP file there: Bambu with rc -6 "The input
model file to the slicer can not be parsed", the others by writing
nothing at all. Even a plain 20 mm box made by OpenCASCADE fails
(probed 2026-11-27 after a user could not start a job from two
SolidWorks STEP files). PrusaSlicer and Anycubic Slicer Next do read
STEP from the command line.

So for the Bambu-style merge, AccessSlice meshes the STEP itself with
cascadio, a small OpenCASCADE build (the same CAD kernel the slicers'
windows mesh STEP with), and hands the engine a binary STL with the
same name. The tolerances are the slicers' own import defaults: at
most 0.01 mm between the mesh and the true surface, and 0.5 radian
between neighbouring facets.

cascadio writes the mesh as OBJ in the file's own units (millimetres
for these files - checked against PrusaSlicer's import of the same
file, identical sizes); the OBJ is read here and written as STL with
the standard library only.
"""

import importlib.util
import math
import os
import shutil
import struct
import sys
from pathlib import Path

from errors import ProjectError

STEP_SUFFIXES = {".step", ".stp"}

# The slicers' own STEP import defaults (linear in mm, angular in rad).
LINEAR_TOLERANCE_MM = 0.01
ANGULAR_TOLERANCE_RAD = 0.5


_kernel = None
_dll_dirs = []  # add_dll_directory handles must stay alive


def load_kernel():
    """cascadio's compiled core (the module with step_to_obj), or None.

    Loaded straight from its .pyd, without the cascadio package's
    Python half: that half imports numpy (for trimesh integration
    AccessSlice does not use), and the packaged program carries no
    numpy. The OpenCASCADE DLLs sit in cascadio.libs beside the
    package, both in a normal install and in the packaged program
    (AccessSlice.spec copies exactly these two folders).
    """
    global _kernel
    if _kernel is not None:
        return _kernel
    bases = [getattr(sys, "_MEIPASS", None)] + list(sys.path)
    for base in bases:
        if not base:
            continue
        package = Path(base) / "cascadio"
        try:
            core = next(package.glob("_core*.pyd"), None)
        except OSError:
            continue
        if core is None:
            continue
        libs = Path(base) / "cascadio.libs"
        try:
            if libs.is_dir() and hasattr(os, "add_dll_directory"):
                _dll_dirs.append(os.add_dll_directory(str(libs)))
            spec = importlib.util.spec_from_file_location(
                "cascadio._core", core)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except (ImportError, OSError):
            continue
        _kernel = module
        return module
    return None


def is_step(path):
    """True for a .step or .stp file name, in any letter case."""
    return Path(path).suffix.lower() in STEP_SUFFIXES


def meshable_models(model_paths, work_dir):
    """The model list with every STEP file replaced by an STL mesh.

    The STLs are written into work_dir, each named after its STEP file
    (Axle.STEP -> Axle.stl), so the object keeps its name in the
    project. Other files are returned unchanged.
    """
    work_dir = Path(work_dir)
    result = []
    for index, model in enumerate(model_paths):
        model = Path(model)
        if not is_step(model):
            result.append(model)
            continue
        # One folder per model: two STEP files with the same name from
        # different folders must not overwrite each other's mesh.
        folder = work_dir / f"step-mesh-{index}"
        folder.mkdir(parents=True, exist_ok=True)
        result.append(step_to_stl(model, folder / f"{model.stem}.stl"))
    return result


def step_to_stl(step_path, stl_path):
    """Mesh one STEP file into a binary STL; returns stl_path.

    Raises ProjectError in plain words when the file cannot be meshed.
    """
    step_path = Path(step_path)
    stl_path = Path(stl_path)
    cascadio = load_kernel()
    if cascadio is None:
        raise ProjectError(
            f"{step_path.name} is a STEP file, and the part of "
            "AccessSlice that reads STEP files is missing from this "
            "installation. Reinstall AccessSlice, or save the model as "
            "an STL file in your design program and start from that.")
    obj_path = stl_path.with_suffix(".obj")
    try:
        rc = cascadio.step_to_obj(
            str(step_path), str(obj_path),
            tol_linear=LINEAR_TOLERANCE_MM,
            tol_angular=ANGULAR_TOLERANCE_RAD,
            use_colors=False)
    except Exception as e:  # a crash inside the CAD kernel's binding
        rc, detail = 1, f" ({e})"
    else:
        detail = ""
    if rc != 0 or not obj_path.is_file():
        raise ProjectError(
            f"{step_path.name} could not be read as a STEP model"
            f"{detail}. The file may be damaged or not really a STEP "
            "file. Try saving it again from your design program, or "
            "save it as an STL file and start from that.")
    try:
        vertices, triangles = read_obj(obj_path)
    finally:
        obj_path.unlink(missing_ok=True)
    if not triangles:
        raise ProjectError(
            f"{step_path.name} was read, but it holds no solid shape "
            "to print - it may contain only lines, points or a "
            "drawing. Save the 3D part itself as STEP or STL.")
    write_binary_stl(vertices, triangles, stl_path)
    return stl_path


def read_obj(path):
    """(vertices, triangles) from an OBJ file.

    Reads the "v" and "f" lines and ignores everything else (normals,
    texture coordinates, groups). Faces with more than three corners
    are split into triangles around their first corner. OBJ indices
    start at 1; negative ones count back from the latest vertex.
    """
    vertices = []
    triangles = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                x, y, z = line.split()[1:4]
                vertices.append((float(x), float(y), float(z)))
            elif line.startswith("f "):
                corners = []
                for token in line.split()[1:]:
                    i = int(token.split("/")[0])
                    corners.append(i - 1 if i > 0 else len(vertices) + i)
                for k in range(1, len(corners) - 1):
                    triangles.append((corners[0], corners[k],
                                      corners[k + 1]))
    return vertices, triangles


def write_binary_stl(vertices, triangles, path):
    """Write a binary STL with a computed normal for every facet."""
    path = Path(path)
    tmp = path.with_suffix(".stl.part")
    with open(tmp, "wb") as f:
        f.write(b"AccessSlice mesh of a STEP model".ljust(80, b" "))
        f.write(struct.pack("<I", len(triangles)))
        pack = struct.Struct("<12fH").pack
        for a, b, c in triangles:
            ax, ay, az = vertices[a]
            bx, by, bz = vertices[b]
            cx, cy, cz = vertices[c]
            ux, uy, uz = bx - ax, by - ay, bz - az
            vx, vy, vz = cx - ax, cy - ay, cz - az
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, \
                ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            f.write(pack(nx / length, ny / length, nz / length,
                         ax, ay, az, bx, by, bz, cx, cy, cz, 0))
    shutil.move(str(tmp), str(path))
