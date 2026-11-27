# Third-party notices

AccessSlice is licensed under the GNU General Public License, version
3 (see `LICENSE`). AccessSlice itself is stdlib-only Python plus its
own HTML, CSS and JavaScript. One third-party library ships in the
source tree, and the installed program bundles a few more (listed
under "The installed program" below); all are under licences
compatible with GPL-3.0.

## Three.js

- File: `app/static/three.min.js` (release r160, the UMD build,
  fetched from cdnjs on 2026-08-30 for the viewport spike; a copy
  also lives in `experiments/viewport-spike/`).
- Used by: the 3D view on the Placement tab (`app/static/viewport.js`)
  and the sliced preview (`app/static/preview.js`).
- Licence: MIT. Copyright 2010-2023 Three.js Authors. The licence
  header is intact at the top of the file, and the full text is:

```
The MIT License

Copyright © 2010-2023 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## The installed program

`AccessSlice-Setup-<version>.exe` (built by `packaging\build.py`)
bundles, unchanged, beside AccessSlice's own code:

- **Python 3.13** (the interpreter and its standard library) - Python
  Software Foundation License.
- **PyInstaller bootloader** - GPL-2.0-or-later with the PyInstaller
  exception that permits bundling programs under any licence.
- **pywebview** (the app window) - BSD 3-Clause, copyright Roman
  Sirokov.
- **pythonnet** (pywebview's bridge to .NET on Windows) - MIT.
- **bottle**, **proxy_tools**, **typing_extensions** (pywebview's
  dependencies) - MIT; MIT; Python Software Foundation License.
- **cryptography** (Apache-2.0 or BSD-3-Clause, at your choice),
  **MarkupSafe** (BSD-3-Clause) and **importlib_metadata**
  (Apache-2.0) - pulled in by pywebview; each ships its licence text
  in its `.dist-info` folder under `_internal`.
- **Microsoft Edge WebView2 SDK** (`Microsoft.Web.WebView2.Core.dll`,
  `Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`) -
  redistributable under Microsoft's WebView2 SDK licence as part of
  an application. The WebView2 *runtime* is not bundled; Windows
  provides it.
- **cascadio** 0.1.1 (reads STEP files: `_internal\cascadio\_core.pyd`,
  used by `app/step_mesh.py`) - MIT, copyright 2024 Michael
  Dawson-Haggerty; its licence text is
  `_internal\cascadio\LICENSE-cascadio.md`. Only its compiled core
  ships, not its Python package. Source:
  https://github.com/trimesh/cascadio
- **Open CASCADE Technology** (the CAD kernel inside cascadio, the
  `TK*.dll` files in `_internal\cascadio.libs`) - GNU LGPL 2.1 with
  the Open CASCADE exception. The DLLs are unmodified and loaded at
  run time, so they can be replaced with any compatible build.
  Source: https://github.com/Open-Cascade-SAS/OCCT. The same folder
  holds Microsoft's C++ runtime (`msvcp140-*.dll`), redistributable
  under the Visual C++ Redistributable licence.
- **Inno Setup** builds the installer; its runtime is bundled in the
  installer under the Inno Setup licence, which permits this.

Each package's own licence text is in its distribution; the versions
used are recorded in `docs/log.md` (2026-09-07).

## Formats read, not code used

The binary G-code decoder (`app/bgcode.py`) is AccessSlice's own
implementation of the libbgcode file format (Prusa Research; the
format's heatshrink and MeatPack layers). No libbgcode, heatshrink or
MeatPack code is included.
