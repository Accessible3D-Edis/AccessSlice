"""compat.py - which Bambu Studio versions AccessSlice is verified for.

AccessSlice's knowledge of Studio's file formats was reverse-engineered
and verified against specific Studio versions: the project 3MF
structure, the BambuStudio.conf checksum scheme, and the profile
library layout. A Studio update can change any of them.

The policy has two layers:

1. ACTUAL breakage always fails loudly, whatever the version says:
   the session's structure check and save verification guard the 3MF,
   set_printer's checksum recompute and restore-on-failure guard the
   conf, and a missing profile library is announced at project open.
2. A Studio version OUTSIDE the verified series is announced as a
   caution the moment it is seen (project open, conf edit), so the
   user is alert before anything subtle goes wrong - but nothing is
   refused on the version number alone, because a new Studio that
   kept its formats would otherwise block the app for no reason.

When a new Studio series is verified by hand, add it to
VERIFIED_SERIES and record the evidence in docs/log.md.
"""

# Version series ("major.minor") whose file formats have been verified
# against real Studio-written files. 02.08 verified 2026-08-25/27
# (blind-Benchy H2C project, marshmallow-roller X1C project, conf
# checksum round trips, first real print sent end to end).
VERIFIED_SERIES = ("02.08",)

# How the verified range reads in user-facing sentences.
VERIFIED_TEXT = "Bambu Studio 2.8"


def series(version_or_app):
    """The "major.minor" series of a version string, or "".

    Accepts both the conf's form ("02.08.02.61") and the 3MF's
    Application form ("BambuStudio-02.08.02.61").
    """
    text = (version_or_app or "").strip()
    if text.lower().startswith("bambustudio-"):
        text = text[len("bambustudio-"):]
    parts = text.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    return ""


def unverified_note(version_or_app, what):
    """A plain-language caution when the version is outside the
    verified series, or None when it is verified or unparseable
    (unparseable stays quiet - the structural checks still guard)."""
    found = series(version_or_app)
    if not found or found in VERIFIED_SERIES:
        return None
    return (f"{what} comes from Bambu Studio version {found}, which "
            f"AccessSlice has not been verified against yet (verified: "
            f"{VERIFIED_TEXT}). Everything is still checked "
            "structurally on every save, but if a value looks wrong, "
            "stop and report it.")
