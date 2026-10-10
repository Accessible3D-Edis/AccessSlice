"""backends.orca - the OrcaSlicer backend (an Orca-family flavour).

Built 2026-09-02 on the facts in orca_family.py's docstring and
docs\\log.md: OrcaSlicer 2.4.2, installed vendors read from
%APPDATA%\\OrcaSlicer\\system\\, user presets under
%APPDATA%\\OrcaSlicer\\user\\default\\, files stamped with a separate
"OrcaSlicer" version metadata. On Edis's machine Orca's Bambu profiles
cover the X1C (no H2C profile in 2.4.2).
"""

import orca_family

from backends import orca_texts
from backends.orcafamily import OrcaFamilyBackend

FLAVOUR = orca_family.Flavour(
    id="orca",
    display_name="OrcaSlicer",
    exe_names=("orca-slicer.exe",),
    candidates=[
        r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
        r"C:\Program Files (x86)\OrcaSlicer\orca-slicer.exe",
    ],
    appdata_names=["OrcaSlicer"],
    default_vendors=["BBL"],
    version_marker=("metadata", "OrcaSlicer"),
    verified_series=("2.4",),
    verified_text="OrcaSlicer 2.4",
    texts=orca_texts,
)


class OrcaBackend(OrcaFamilyBackend):
    def __init__(self):
        super().__init__(FLAVOUR)
