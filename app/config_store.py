"""config_store.py - AccessSlice's saved configuration.

What lives here: which slicer the user chose, where the slicer executable
is, which folder AccessSlice watches for saved projects, and the list of
recently opened projects. Stored as one small JSON file in the user's
AppData folder so it survives between runs and never touches the internet.

The printer passwords (PrusaLink's API key, under lan_printer_keys) are
never written in plain text: on Windows they are protected with the
Data Protection API (DPAPI) so only the same Windows account on the same
PC can read them back (the way the Prusa Accessible Control program did
it, brought over 2026-09-12). load() hands them back decrypted, so the
rest of the app never sees the difference; a config copied from another
account or PC loses them and the password has to be typed again. On
other systems, or when DPAPI fails, they stay plain.

Everything is stdlib-only, like the rest of AccessSlice.
"""

import base64
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

import noconsole

# The config file lives in %APPDATA%\AccessSlice\config.json on Windows.
# On other systems (developers on Mac/Linux) fall back to ~/.accessslice.
def _config_dir():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "AccessSlice"
    return Path.home() / ".accessslice"


CONFIG_PATH = _config_dir() / "config.json"

# The default projects folder AccessSlice offers on first run.
DEFAULT_PROJECTS_DIR = Path.home() / "Documents" / "AccessSlice Projects"

# Slicers shown in the first-run picker. The supported flag mirrors
# the backend registry (backends\__init__.py) - a registry test pins
# the two lists together; the rest appear disabled with a "coming
# later" note so the roadmap is visible in the UI itself.
SLICERS = [
    ("bambu", "Bambu Studio", True),
    ("creality", "Creality Print", True),
    ("orca", "OrcaSlicer", True),
    ("prusa", "PrusaSlicer", True),
    ("anycubic", "Anycubic Slicer Next", True),
]

# Standard install locations tried when auto-detecting Bambu Studio.
BAMBU_CANDIDATES = [
    r"C:\Program Files\Bambu Studio\bambu-studio.exe",
    r"C:\Program Files\Bambu Studio\BambuStudio.exe",
    r"C:\Program Files (x86)\Bambu Studio\bambu-studio.exe",
]


def load():
    """Return the saved config as a dict, or {} if none exists yet.
    Protected printer passwords come back decrypted under
    lan_printer_keys; ones this account cannot read are left out."""
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(config, dict):
        return {}
    protected = config.pop(PROTECTED_KEYS, None)
    if isinstance(protected, dict) and protected:
        keys = config.get("lan_printer_keys")
        keys = dict(keys) if isinstance(keys, dict) else {}
        for slicer, token in protected.items():
            secret = unprotect_secret(token)
            if secret:
                keys[slicer] = secret
        config["lan_printer_keys"] = keys
    return config


def save(config):
    """Write the config dict to disk, creating the folder if needed.
    The caller's dict is left as it is; the file gets the printer
    passwords protected where DPAPI is available."""
    stored = dict(config)
    keys = stored.get("lan_printer_keys")
    if isinstance(keys, dict) and keys:
        plain, protected = {}, {}
        for slicer, secret in keys.items():
            token = protect_secret(str(secret)) if secret else None
            if token:
                protected[slicer] = token
            elif secret:
                plain[slicer] = secret
        stored["lan_printer_keys"] = plain
        if protected:
            stored[PROTECTED_KEYS] = protected
    else:
        stored.pop(PROTECTED_KEYS, None)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ------------------------------------------------------- DPAPI

PROTECTED_KEYS = "lan_printer_keys_protected"
DPAPI_DESCRIPTION = "AccessSlice printer password"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi():
    """The crypt32 and kernel32 libraries, or None off Windows."""
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.crypt32, ctypes.windll.kernel32
    except (AttributeError, OSError):
        return None


def _blob(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer,
                                            ctypes.POINTER(ctypes.c_char)))


def protect_secret(secret):
    """The secret encrypted for the current Windows user, as base64;
    None when DPAPI is unavailable or refuses."""
    libs = _dpapi()
    if libs is None or not secret:
        return None
    crypt32, kernel32 = libs
    out = _DataBlob()
    try:
        ok = crypt32.CryptProtectData(
            ctypes.byref(_blob(secret.encode("utf-8"))),
            ctypes.c_wchar_p(DPAPI_DESCRIPTION), None, None, None, 0,
            ctypes.byref(out))
    except (OSError, ValueError):
        return None
    if not ok:
        return None
    blob = ctypes.string_at(out.pbData, out.cbData)
    kernel32.LocalFree(out.pbData)
    return base64.b64encode(blob).decode("ascii")


def unprotect_secret(token):
    """The secret back from protect_secret's output; None when it
    cannot be read on this account or machine."""
    libs = _dpapi()
    if libs is None or not token:
        return None
    crypt32, kernel32 = libs
    try:
        raw = base64.b64decode(token)
    except (ValueError, TypeError):
        return None
    out = _DataBlob()
    try:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(_blob(raw)), None, None, None, None, 0,
            ctypes.byref(out))
    except (OSError, ValueError):
        return None
    if not ok:
        return None
    data = ctypes.string_at(out.pbData, out.cbData)
    kernel32.LocalFree(out.pbData)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def is_complete(config):
    """True when setup has answered all three first-run questions."""
    return bool(
        config.get("slicer")
        and config.get("slicer_exe")
        and config.get("projects_dir")
    )


def detect_bambu_exe():
    """Return the first existing standard Bambu Studio path, or None."""
    for candidate in BAMBU_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def resolve_shortcut(path):
    """The target of a Windows .lnk shortcut, or None (best effort)."""
    escaped = str(path).replace("'", "''")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(New-Object -ComObject WScript.Shell).CreateShortcut('"
             + escaped + "').TargetPath"],
            capture_output=True, text=True, timeout=15,
            **noconsole.hidden()).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def normalize_exe_path(raw, resolver=None):
    """A pasted program path, cleaned up so the user never has to:
    surrounding quotes stripped, a desktop shortcut (.lnk) resolved
    to the program it points at.

    Returns (path, note): note is a plain-language sentence about
    what was fixed, or "". (Edis pasted a desktop shortcut on
    2026-09-01 and launching it gave WinError 193 - paths are the
    app's job to format, not the user's.)
    """
    path = (raw or "").strip().strip('"').strip("'").strip()
    note = ""
    if path.lower().endswith(".lnk"):
        target = (resolver or resolve_shortcut)(path)
        if target:
            note = (f"The path you gave is a desktop shortcut; it "
                    f"points at {target}, so that is the program "
                    "path AccessSlice saved.")
            path = target
        else:
            note = ("The path is a shortcut (.lnk) that could not "
                    "be resolved - give the program's own path "
                    "instead.")
    return path, note


def exe_for(config, slicer_id, detect=None):
    """The program path to offer for a slicer, when it is chosen on
    the Settings page: the path this slicer was last saved with (if
    the file is still there), else what detection finds, else "".

    Every save records slicer -> path under "slicer_exes", so
    switching PrusaSlicer -> Creality Print -> PrusaSlicer brings each
    slicer's own path back instead of leaving the other one in the
    field (Edis, 2026-09-09: "it still has the prusa location placed
    and I need manually to type the address in again").
    """
    remembered = (config.get("slicer_exes") or {}).get(slicer_id, "")
    if remembered and Path(remembered).is_file():
        return remembered
    if config.get("slicer") == slicer_id:
        current = config.get("slicer_exe", "")
        if current and Path(current).is_file():
            return current
    return (detect() if detect else None) or ""


def remember_exe(config, slicer_id, exe):
    """Record slicer -> program path (see exe_for)."""
    table = dict(config.get("slicer_exes") or {})
    table[slicer_id] = exe
    config["slicer_exes"] = table


def remember_recent(config, path):
    """Put a project path at the top of the recent list (max 8 entries)."""
    path = str(path)
    recents = [p for p in config.get("recent", []) if p != path]
    recents.insert(0, path)
    config["recent"] = recents[:8]
    save(config)
