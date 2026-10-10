"""backends.anycubic - the Anycubic Slicer Next backend (Orca flavour).

Built 2026-09-02 (facts in orca_family.py and docs\\log.md): Anycubic
Slicer Next 1.4.1.2, one vendor folder (Anycubic, the Kobra family),
files stamped Application "BambuStudio-1.4.1.2". The config folder
under %APPDATA% does not exist until the program has been launched
once; both spellings seen in the wild are tried. Verified to slicing
only - no Anycubic printer has been tested yet.
"""

import orca_family

from backends import anycubic_texts
from backends.orcafamily import OrcaFamilyBackend

FLAVOUR = orca_family.Flavour(
    id="anycubic",
    display_name="Anycubic Slicer Next",
    exe_names=("AnycubicSlicerNext.exe",),
    candidates=[
        r"C:\Program Files\AnycubicSlicerNext\AnycubicSlicerNext.exe",
        r"C:\Program Files (x86)\AnycubicSlicerNext\AnycubicSlicerNext.exe",
    ],
    appdata_names=["AnycubicSlicerNext", "Anycubic Slicer Next",
                   "AnycubicSlicer"],
    default_vendors=["Anycubic"],
    version_marker=("application", "BambuStudio-1."),
    verified_series=("1.4",),
    verified_text="Anycubic Slicer Next 1.4",
    texts=anycubic_texts,
)


class AnycubicBackend(OrcaFamilyBackend):
    def __init__(self):
        super().__init__(FLAVOUR)
