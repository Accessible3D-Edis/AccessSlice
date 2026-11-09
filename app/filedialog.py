"""filedialog.py - the standard Windows Open dialog for the path fields.

Typing a full path was the only way to give AccessSlice a file, and two
users asked for a browse button (2026-11): pasting a path is awkward
when it was not copied before AccessSlice opened. Windows' own Open
dialog is the most familiar file chooser there is, and screen readers
know it well, so a Browse button next to each path field opens exactly
that dialog and puts the chosen path into the field.

Stdlib only, like the rest of AccessSlice: comdlg32's GetOpenFileNameW
for files and shell32's SHBrowseForFolderW (with its edit box) for
folders, through ctypes. The dialog is owned by the window in front -
the AccessSlice window when its button was pressed - so it opens on top
of it and focus returns there when it closes. Everything runs on the
calling (request) thread; the server calls choose() outside its lock,
so the rest of AccessSlice keeps answering while the dialog is open.
"""

import ctypes
import os
import sys
from ctypes import wintypes

# kind -> (dialog title, [(filter name, "*.a;*.b")]). "All files" is
# always added last, so nothing is ever unreachable.
KINDS = {
    "model": ("Choose the model file",
              [("Model files (STL, STEP, OBJ, 3MF)",
                "*.stl;*.step;*.stp;*.obj;*.3mf")]),
    "project": ("Choose the project file",
                [("Project files (3MF)", "*.3mf")]),
    "gcode": ("Choose the print file",
              [("Print files (G-code)", "*.gcode;*.bgcode;*.gcode.3mf")]),
    "exe": ("Choose the slicer program",
            [("Programs", "*.exe")]),
    "folder": ("Choose the folder", []),
}


class DialogUnavailable(Exception):
    """No dialog can be shown here (not Windows, or the call failed)."""


def filter_string(filters):
    """The double-NUL-terminated filter list GetOpenFileNameW wants."""
    parts = []
    for name, pattern in list(filters) + [("All files", "*.*")]:
        parts += [name, pattern]
    return "\0".join(parts) + "\0\0"


def start_folder(current):
    """Where the dialog opens: the folder of what the field holds, when
    that exists; otherwise the dialog's own default."""
    text = (current or "").strip().strip('"')
    if not text:
        return ""
    if os.path.isdir(text):
        return text
    parent = os.path.dirname(text)
    return parent if parent and os.path.isdir(parent) else ""


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD), ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR), ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


class _BROWSEINFOW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND), ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR), ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT), ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM), ("iImage", ctypes.c_int),
    ]


_OFN_FILEMUSTEXIST = 0x00001000
_OFN_PATHMUSTEXIST = 0x00000800
_OFN_NOCHANGEDIR = 0x00000008
_OFN_EXPLORER = 0x00080000
_OFN_DONTADDTORECENT = 0x02000000
_BIF_RETURNONLYFSDIRS = 0x0001
_BIF_EDITBOX = 0x0010
_BIF_NEWDIALOGSTYLE = 0x0040


def _owner():
    try:
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return None


def _choose_file(title, filters, initial):
    buffer = ctypes.create_unicode_buffer(32768)
    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.hwndOwner = _owner()
    ofn.lpstrFilter = filter_string(filters)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = len(buffer)
    ofn.lpstrInitialDir = initial or None
    ofn.lpstrTitle = title
    ofn.Flags = (_OFN_FILEMUSTEXIST | _OFN_PATHMUSTEXIST | _OFN_NOCHANGEDIR
                 | _OFN_EXPLORER | _OFN_DONTADDTORECENT)
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return buffer.value
    if ctypes.windll.comdlg32.CommDlgExtendedError():
        raise DialogUnavailable("the Open dialog could not be shown")
    return ""     # cancelled


def _choose_folder(title):
    name = ctypes.create_unicode_buffer(260)
    info = _BROWSEINFOW()
    info.hwndOwner = _owner()
    info.pszDisplayName = ctypes.cast(name, wintypes.LPWSTR)
    info.lpszTitle = title
    info.ulFlags = (_BIF_RETURNONLYFSDIRS | _BIF_EDITBOX
                    | _BIF_NEWDIALOGSTYLE)
    shell32 = ctypes.windll.shell32
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
    if not pidl:
        return ""   # cancelled
    path = ctypes.create_unicode_buffer(32768)
    ok = shell32.SHGetPathFromIDListW(ctypes.c_void_p(pidl), path)
    ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    return path.value if ok else ""


def choose(kind, current=""):
    """Show the dialog for kind (a KINDS key); returns the chosen path,
    or "" when the person cancelled. Raises DialogUnavailable when no
    dialog can be shown."""
    if sys.platform != "win32":
        raise DialogUnavailable("the Windows Open dialog needs Windows")
    title, filters = KINDS.get(kind, ("Choose a file", []))
    # Both dialogs are COM-based and want a single-threaded apartment
    # on the thread that shows them (each request has its own thread).
    try:
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
    except Exception as e:
        raise DialogUnavailable(str(e))
    try:
        if kind == "folder":
            return _choose_folder(title)
        return _choose_file(title, filters, start_folder(current))
    except DialogUnavailable:
        raise
    except Exception as e:
        raise DialogUnavailable(str(e))
    finally:
        ole32.CoUninitialize()
