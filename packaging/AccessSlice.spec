# -*- mode: python ; coding: utf-8 -*-
"""AccessSlice.spec - the PyInstaller recipe for the packaged program.

Run from the repository root (build.py does this):

    python -m PyInstaller --noconfirm --distpath packaging/dist \
        --workpath packaging/build packaging/AccessSlice.spec

ONE-FOLDER layout (AccessSlice.exe beside an _internal folder) - it
starts faster than a one-file bundle and draws fewer antivirus false
positives. No console window: the program is its own window
(app\appwindow.py). Everything the app reads at run time travels as
data: the static folder (stylesheet, scripts, the vendored Three.js),
PrusaSlicer's defaults file, and the 3MF engine from tools\ (imported
as a module, so it lives in the bundle's module archive). The data
list is built from the folder contents, so a new static file can
never be forgotten.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
APP = ROOT / "app"
sys.path.insert(0, str(APP))
from version import VERSION  # noqa: E402

datas = [(str(p), "static") for p in sorted((APP / "static").iterdir())
         if p.is_file()]
datas.append((str(APP / "prusa_defaults.ini"), "."))

# STEP support (app\step_mesh.py): cascadio's compiled core and the
# OpenCASCADE DLLs beside it, copied as FILES into the same two
# folders a normal install has. The cascadio package itself is never
# imported - its Python half needs numpy, which the bundle does not
# carry - so step_mesh.load_kernel loads the .pyd directly.
import importlib.util  # noqa: E402
_cascadio = Path(importlib.util.find_spec("cascadio").origin).parent
binaries = [(str(p), "cascadio") for p in _cascadio.glob("_core*.pyd")]
binaries += [(str(p), "cascadio.libs")
             for p in sorted((_cascadio.parent / "cascadio.libs").iterdir())]
if not any(p.endswith(".pyd") for p, _d in binaries):
    raise SystemExit("cascadio is not installed: pip install cascadio")
# Its licence travels with it (the dist-info folder does not).
datas += [(str(p), "cascadio")
          for p in _cascadio.parent.glob("cascadio-*.dist-info/**/LICENSE*")
          if p.is_file()]

a = Analysis(
    [str(APP / "accessslice_window.py")],
    pathex=[str(APP), str(ROOT / "tools"), str(APP / "backends")],
    binaries=binaries,
    datas=datas,
    # Reached through the registry / sys.path tricks PyInstaller's
    # import scan cannot follow.
    hiddenimports=["edit_bambu_3mf", "backends", "backends.bambu",
                   "backends.creality", "backends.orca", "backends.prusa",
                   "backends.anycubic", "webview.platforms.edgechromium",
                   "webview.platforms.winforms"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AccessSlice",
    icon=str(ROOT / "packaging" / "accessslice.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(ROOT / "packaging" / "build" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AccessSlice",
)
