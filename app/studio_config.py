"""studio_config.py - set Bambu Studio's active printer from AccessSlice.

Bambu Studio remembers its currently selected printer, print profile and
filament presets in %APPDATA%\\BambuStudio\\BambuStudio.conf. The file is
one JSON object followed by a checksum line:

    { ...json... }
    # MD5 checksum 9B32FE450C9CE3FCA0B4B385BA5895A6

The checksum is the MD5 of the JSON text with LF line endings and no
trailing newline (verified against a real Studio-written file,
2026-08-26); on disk the file itself uses CRLF. Studio ignores or resets
a config whose checksum does not match, so every edit here recomputes it.

Editing this file while Studio is CLOSED makes Studio start with the
chosen printer already selected, so a project saved from it inherits the
right machine from the beginning - no GUI automation needed, in keeping
with AccessSlice's edit-text-only philosophy.

When the printer changes, the selected print profile and filament
presets are remapped to the same-named presets of the new printer where
those exist (e.g. "Bambu PLA Basic @BBL X1C" -> "Bambu PLA Basic
@BBL H2C"), because Studio's presets are per-printer. Anything without a
match is left alone for Studio to resolve with its own dialogs.

Everything is stdlib-only, like the rest of AccessSlice.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import compat
import noconsole
import profiles

# Model tokens as used in preset name suffixes ("@BBL X1C"), keyed by
# the start of the machine preset name after "Bambu Lab ". Longest match
# wins, so "X1 Carbon" is tried before "X1". Bambu's printer range is a
# small fixed set; unknown future models fall back to the first word,
# and a failed remap merely leaves the old preset for Studio to fix.
_MODEL_TOKENS = [
    ("X1 Carbon", "X1C"),
    ("X1E", "X1E"),
    ("X1", "X1"),
    ("P1S", "P1S"),
    ("P1P", "P1P"),
    ("A1 mini", "A1M"),
    ("A1", "A1"),
    ("H2C", "H2C"),
    ("H2D", "H2D"),
    ("H2S", "H2S"),
]


def conf_path():
    """Path to BambuStudio.conf (may not exist on a fresh machine)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "BambuStudio" / "BambuStudio.conf"


def read_conf():
    """The parsed JSON object from BambuStudio.conf, or None."""
    path = conf_path()
    if not path or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data, _end = json.JSONDecoder().raw_decode(text)
        return data
    except (OSError, ValueError):
        return None


def current_printer():
    """The machine preset Studio will open with, or ""."""
    data = read_conf()
    if not data:
        return ""
    return data.get("presets", {}).get("machine", "")


def studio_running(slicer_exe):
    """True if the slicer process is currently running.

    The conf must not be edited while Studio runs - Studio rewrites it
    on exit and would silently overwrite the change.
    """
    exe_name = Path(slicer_exe).name if slicer_exe else "bambu-studio.exe"
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
            capture_output=True, text=True, timeout=10,
            **noconsole.hidden()).stdout
    except (OSError, subprocess.SubprocessError):
        return False  # cannot tell; do not block the user on a hunch
    return exe_name.lower() in out.lower()


def model_token(machine_name):
    """Preset-suffix token for a machine preset name, or "".

    "Bambu Lab X1 Carbon 0.4 nozzle" -> "X1C".
    """
    prefix = "Bambu Lab "
    if not machine_name.startswith(prefix):
        return ""
    rest = machine_name[len(prefix):]
    for start, token in _MODEL_TOKENS:
        if rest.startswith(start):
            return token
    first = rest.split()
    return first[0] if first else ""


def model_name(machine_preset):
    """Machine preset name without the nozzle part.

    "Bambu Lab H2C 0.4 nozzle" -> "Bambu Lab H2C". This is the name the
    device map in AccessSlice's config is keyed by.
    """
    return re.sub(r"\s+\d+(\.\d+)?\s+nozzle$", "", machine_preset).strip()


def pending_changes(machine_name, device_serials=None):
    """True if set_printer(machine_name, ...) would edit the conf.

    Lets the caller demand Studio-is-closed only when an edit will
    actually happen. Checks both the machine preset and, when the model
    has a known device serial, the Device-page selection - those can
    disagree (seen 2026-08-26: preset already switched, device not).
    """
    data = read_conf()
    if not data:
        return False  # nothing to edit; set_printer will explain
    if data.get("presets", {}).get("machine") != machine_name:
        return True
    serial = (device_serials or {}).get(model_name(machine_name), "")
    if serial and data.get("app", {}).get(
            "user_last_selected_machine") != serial:
        return True
    return False


def current_device(device_serials=None):
    """The model Studio's Device page was on when the conf was last
    written ("Bambu Lab X1 Carbon"), or "" when the selection is empty
    or its serial is not in the model-to-serial map.

    While Studio runs this is only as fresh as its last write of the
    conf, so callers word it as what the saved settings show.
    """
    data = read_conf()
    if not data:
        return ""
    serial = data.get("app", {}).get("user_last_selected_machine", "")
    if not serial:
        return ""
    for model, known in (device_serials or {}).items():
        if known == serial:
            return model
    return ""


def device_state(machine_name, device_serials=None):
    """How Studio's Device page stands to machine_name - the check a
    send makes (a project file carries its own machine PRESET, so only
    the Device-page selection can be wrong when it opens):

    "same"    - on that printer, or nothing on disk says otherwise;
    "other"   - on another printer, and machine_name's serial is known,
                so set_printer can move it while Studio is closed;
    "unknown" - on another known printer, but no serial is known for
                machine_name, so only the user can move it.

    Seen 2026-09-23: a send for the H2C while Studio was watching an
    X1 Carbon print kept the X1 Carbon on the Device page, and the
    send window offered the wrong printer.
    """
    serials = device_serials or {}
    model = model_name(machine_name)
    serial = serials.get(model, "")
    on_now = current_device(serials)
    data = read_conf() or {}
    selected = data.get("app", {}).get("user_last_selected_machine", "")
    if serial:
        return "same" if selected == serial else "other"
    if on_now and on_now != model:
        return "unknown"
    return "same"


def _remap(name, old_token, new_token, available, default=None):
    """Same preset for the new printer, or the old name unchanged.

    Tries the plain suffix swap first, then the swap without a trailing
    " 0.X nozzle" variant (some models name only the non-0.4 variants),
    then the scope default (e.g. "0.20mm Standard @BBL H2C") if given.
    """
    old_suffix = f"@BBL {old_token}"
    new_suffix = f"@BBL {new_token}"
    if old_token and old_suffix in name:
        candidate = name.replace(old_suffix, new_suffix)
        if candidate in available:
            return candidate
        head, _at, tail = candidate.rpartition(new_suffix)
        if tail.strip().endswith("nozzle"):
            bare = (head + new_suffix).strip()
            if bare in available:
                return bare
    if default and default in available and new_suffix in default:
        return default
    return name


def set_printer(machine_name, slicer_exe, device_serials=None):
    """Point Studio at machine_name; returns human-readable messages.

    Backs the conf up beside itself first, remaps the process and
    filament presets to the new printer, switches the Device-page
    selection when the model's serial is known (device_serials maps
    "Bambu Lab H2C" -> serial; kept in AccessSlice's config), rewrites
    the checksum, and verifies the result parses and checksums
    correctly. The machine preset and the device selection are two
    independent things in Studio, so each is only touched when needed.

    Raises StudioConfError with guidance if anything is off.
    """
    path = conf_path()
    if not path or not path.is_file():
        raise StudioConfError(
            "Bambu Studio's settings file was not found - has Studio "
            "been run at least once on this computer?")

    raw = path.read_bytes()
    text = raw.decode("utf-8")
    data, _end = json.JSONDecoder().raw_decode(text)
    presets = data.setdefault("presets", {})
    messages = []
    # An unverified Studio version gets a caution in the messages; the
    # checksum verification below is what actually refuses on breakage.
    caution = compat.unverified_note(
        data.get("app", {}).get("version", ""),
        "Bambu Studio's settings file")

    old_machine = presets.get("machine", "")
    if old_machine != machine_name:
        old_token = model_token(old_machine)
        new_token = model_token(machine_name)
        messages.append(f"Studio's printer set to {machine_name} "
                        f"(was {old_machine or 'not set'}).")

        # Remap the print profile and each filament preset to the new
        # printer where a same-named preset exists.
        processes = set(profiles.list_presets(slicer_exe, "process"))
        filaments = set(profiles.list_presets(slicer_exe, "filament"))

        old_process = presets.get("process", "")
        if old_process:
            new_process = _remap(old_process, old_token, new_token,
                                 processes,
                                 default=f"0.20mm Standard @BBL {new_token}")
            if new_process != old_process:
                presets["process"] = new_process
                messages.append(f"Print profile changed to {new_process}.")

        old_filaments = presets.get("filaments", [])
        new_filaments = [_remap(f, old_token, new_token, filaments)
                         for f in old_filaments]
        changed = sum(1 for a, b in zip(old_filaments, new_filaments)
                      if a != b)
        if changed:
            presets["filaments"] = new_filaments
            messages.append(f"{changed} filament preset(s) switched to "
                            f"their {new_token} versions.")

        presets["machine"] = machine_name

    # The Device page has its own selection, by printer serial number.
    serial = (device_serials or {}).get(model_name(machine_name), "")
    app_section = data.setdefault("app", {})
    if serial and app_section.get("user_last_selected_machine") != serial:
        app_section["user_last_selected_machine"] = serial
        messages.append(f"Device page switched to the "
                        f"{model_name(machine_name)}.")
    elif not serial:
        messages.append(f"No device serial known for "
                        f"{model_name(machine_name)} - if the Device "
                        f"page shows another printer, pick it there "
                        f"once.")

    if not any(m.startswith(("Studio's printer", "Device page"))
               for m in messages):
        return ([f"Studio is already set to {machine_name}."]
                + ([caution] if caution else []))

    # Rebuild the file: JSON with LF endings is what the checksum
    # covers; the file on disk gets CRLF like Studio's own writes.
    backup = path.with_suffix(".conf.pre-accessslice-backup")
    shutil.copy2(path, backup)
    body_lf = json.dumps(data, indent=4, ensure_ascii=False)
    digest = hashlib.md5(body_lf.encode("utf-8")).hexdigest().upper()
    file_bytes = (body_lf.replace("\n", "\r\n")
                  + f"\r\n# MD5 checksum {digest}\r\n").encode("utf-8")
    path.write_bytes(file_bytes)

    # Verify what landed on disk before declaring success.
    check = path.read_bytes().decode("utf-8")
    parsed, _end = json.JSONDecoder().raw_decode(check)
    stated = check.rsplit("# MD5 checksum", 1)[-1].strip()
    recomputed = hashlib.md5(
        check[:check.rfind("\r\n# MD5 checksum")]
        .replace("\r\n", "\n").encode("utf-8")).hexdigest().upper()
    if (parsed.get("presets", {}).get("machine") != machine_name
            or stated != recomputed):
        shutil.copy2(backup, path)
        raise StudioConfError(
            "The rewritten Studio settings file did not verify; the "
            "backup was restored and nothing changed.")

    messages.append(f"Backup kept: {backup.name}.")
    if caution:
        messages.append(caution)
    return messages


class StudioConfError(Exception):
    """A problem editing BambuStudio.conf, phrased for the user."""
