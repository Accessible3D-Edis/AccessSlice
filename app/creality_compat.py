"""creality_compat.py - which Creality Print versions are verified.

Same policy as compat.py (see there): actual breakage always fails
loudly through the structural checks; an unverified version is a
spoken caution, never a refusal.

7.2 verified 2026-09-01: Edis's Dodecagon plant cover project
(K2 Plus) - format read correctly, headless project-in slice rc 0,
report fields parsed from slice_info.config. When a new series is
hand-verified, add it here and record the evidence in docs/log.md.
"""

VERIFIED_SERIES = ("7.2",)
VERIFIED_TEXT = "Creality Print 7.2"


def series(version_or_app):
    """The "major.minor" series of a version string, or "".

    Accepts the 3MF's Application form
    ("Creality_Print V7.2.2.5483 Release") and a bare version
    ("7.2.2.5483").
    """
    text = (version_or_app or "").strip()
    low = text.lower()
    if low.startswith("creality_print"):
        text = text.split(None, 1)[1] if " " in text else ""
        text = text.lstrip("Vv")
    parts = text.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0])}.{int(parts[1])}"
    return ""


def unverified_note(version_or_app, what):
    """A plain-language caution outside the verified series, else None
    (unparseable stays quiet - the structural checks still guard)."""
    found = series(version_or_app)
    if not found or found in VERIFIED_SERIES:
        return None
    return (f"{what} comes from Creality Print version {found}, which "
            f"AccessSlice has not been verified against yet (verified: "
            f"{VERIFIED_TEXT}). Everything is still checked "
            "structurally on every save, but if a value looks wrong, "
            "stop and report it.")
