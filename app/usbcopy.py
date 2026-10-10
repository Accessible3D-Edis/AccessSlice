"""usbcopy.py - the printable file onto a USB drive, without a file
manager.

Built 2026-09-07 after Edis's first real Prusa run: the report named
the file's path, but finding that path in File Explorer and getting
the file onto a drive was the hard part - and the slicer's own window
was no help (PrusaSlicer opens the sliced file in a viewer that
cannot send). This module lists the removable drives Windows sees
right now, copies the file to the chosen one (or to any folder the
user types), and verifies the copy by size. Nothing here runs except
from the user's own button press.

Windows facts, via ctypes on kernel32 (no third-party modules):
GetLogicalDrives is a bitmask of drive letters; GetDriveTypeW answers
2 (DRIVE_REMOVABLE) for USB sticks and SD cards; GetVolumeInformationW
fails for a card reader with no card in it, which is how empty
readers are skipped; GetDiskFreeSpaceExW gives the free space. Some
large USB disks report as fixed (3) - the "type a folder" field
covers them.
"""

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

from errors import ProjectError

DRIVE_REMOVABLE = 2


def _friendly_size(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit == "bytes"
                    else f"{n:.1f} {unit}")
        n /= 1024.0
    return f"{n:.1f} TB"


def removable_drives():
    """[{"path": "E:\\\\", "label": "PRUSA", "free": bytes,
    "text": "E: (PRUSA, 14.2 GB free)"}, ...] for every removable
    drive with a volume in it; [] off Windows or when none is in."""
    if sys.platform != "win32":
        return []
    try:
        k = ctypes.windll.kernel32
        mask = k.GetLogicalDrives()
    except (AttributeError, OSError):
        return []
    found = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        try:
            if k.GetDriveTypeW(root) != DRIVE_REMOVABLE:
                continue
            label = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            if not k.GetVolumeInformationW(root, label, 261, None, None,
                                           None, fs, 261):
                continue  # a reader with nothing in it
            free = ctypes.c_ulonglong(0)
            total = ctypes.c_ulonglong(0)
            k.GetDiskFreeSpaceExW(root, ctypes.byref(free),
                                  ctypes.byref(total), None)
        except OSError:
            continue
        name = label.value.strip()
        text = f"{root[:2]} ({name + ', ' if name else ''}" \
               f"{_friendly_size(free.value)} free)"
        found.append({"path": root, "label": name, "free": free.value,
                      "text": text})
    return found


def copy_to(out, target, extract=None):
    """Copy the printable file to the folder `target`; the copied
    Path. extract: an optional (path) -> (entry name, bytes) that
    unpacks the plain G-code from a container file, for printers
    that want that from a drive (the K series)."""
    out = Path(out)
    folder = Path(target)
    if not folder.is_dir():
        raise ProjectError(
            f"{target} is not a folder that exists right now. If it "
            "is a USB drive, check that it is plugged in, then reload "
            "this page so the list is fresh. Nothing was copied.")
    if extract is not None:
        _entry, data = extract(out)
        name = out.name
        for suffix in (".gcode.3mf", ".3mf"):
            if name.endswith(suffix):
                name = name[:-len(suffix)] + ".gcode"
                break
        dest = folder / name
        try:
            dest.write_bytes(data)
        except OSError as e:
            raise ProjectError(f"Could not write {dest}: {e}")
        expected = len(data)
    else:
        dest = folder / out.name
        try:
            shutil.copy2(out, dest)
        except OSError as e:
            raise ProjectError(f"Could not copy to {dest}: {e}")
        expected = out.stat().st_size
    try:
        actual = dest.stat().st_size
    except OSError as e:
        raise ProjectError(f"The copy at {dest} could not be checked: {e}")
    if actual != expected:
        raise ProjectError(
            f"The copy at {dest} is {actual} bytes but the file is "
            f"{expected} bytes - the drive may be full. Delete the "
            "half copy and try another drive.")
    return dest


def after_copy_note(target):
    """What to say after a copy: the eject reminder for drives."""
    t = str(target).strip()
    if len(t) <= 3 and t[1:2] == ":":
        return ("Eject the drive before pulling it out, so the copy "
                "is complete on it: in File Explorer, select the "
                "drive and choose Eject from its context menu (the "
                "Applications key), or use the Safely Remove Hardware "
                "icon in the notification area (Windows key + B "
                "reaches it). Then plug it into the printer.")
    return "The file is complete and printable as it is."


def reveal(path):
    """Open File Explorer with the file selected."""
    path = Path(path)
    if sys.platform != "win32":
        raise ProjectError("Showing the file in a file manager works "
                           "on Windows only; the path is above.")
    try:
        subprocess.Popen(["explorer.exe", f"/select,{path}"])
    except OSError as e:
        raise ProjectError(f"File Explorer could not be opened ({e}). "
                           f"The file is at {path}.")
