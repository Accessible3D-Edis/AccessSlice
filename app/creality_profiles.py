"""creality_profiles.py - Creality Print's preset library.

Creality Print is an Orca-family slicer and ships its system presets
exactly like Bambu Studio - <install>\\resources\\profiles\\<vendor>\\
{process,filament,machine} with the same JSON schema (inherits chains,
instantiation flags) - so this module is the profiles.py machinery
with Creality's two roots: the vendor folder is "Creality", and user
presets live under %APPDATA%\\Creality\\Creality Print\\<version>\\
user\\<account>\\{scope} (verified on the real 7.2.2 install,
2026-09-01; the account folder is "default").

Public surface mirrors profiles.py - a session or backend can hold
either module.
"""

import os
from pathlib import Path

import profiles
from profiles import (META_KEYS, SCOPE_FOLDERS,  # noqa: F401
                      ProfileError)

VENDOR = "Creality"


def library_root(slicer_exe):
    """The Creality vendor profile folder for an install, or None."""
    return profiles.library_root(slicer_exe, VENDOR)


def user_library_root():
    """The newest Creality Print user-preset root, or None.

    Creality Print keeps one folder per app series
    (...\\Creality Print\\6.0\\user, ...\\7.0\\user); the newest
    series that actually has a user folder wins.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    base = Path(appdata) / "Creality" / "Creality Print"
    if not base.is_dir():
        return None

    def series_key(p):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (-1,)

    for version_dir in sorted(base.iterdir(), key=series_key,
                              reverse=True):
        root = version_dir / "user"
        if root.is_dir():
            return root
    return None


def list_presets(slicer_exe, scope):
    return profiles.list_presets(slicer_exe, scope, vendor=VENDOR)


def list_user_presets(scope):
    return profiles.list_user_presets(scope, root=user_library_root())


def resolve_preset(slicer_exe, scope, name):
    return profiles.resolve_preset(slicer_exe, scope, name,
                                   vendor=VENDOR,
                                   user_root=user_library_root())


def scope_of(slicer_exe, key):
    return profiles.scope_of(slicer_exe, key, vendor=VENDOR)
