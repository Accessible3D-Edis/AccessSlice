"""diagnostics.py - AccessSlice's plain-text diagnostics log.

When something goes wrong during real use, this file is the trace that
replaces reconstructing events from memory. It lives beside the config
(%APPDATA%\\AccessSlice\\diagnostics.log), is plain text with one dated
line per event (screen-reader friendly - no tables, no symbols), and is
readable inside the app on the Diagnostics page.

Two rules keep it trustworthy:

1. Logging must NEVER break the app: every write is wrapped, and a
   failure to log is silently ignored (the log is a convenience; the
   user's work is not).
2. The log never grows without bound: past roughly 400 KB the oldest
   half is dropped.

Everything is stdlib-only, like the rest of AccessSlice.
"""

import datetime
import traceback

import config_store

# Rotation: when the file passes this size, keep only the newest half.
_MAX_BYTES = 400_000


def log_path():
    """Where the log lives - beside AccessSlice's config file."""
    return config_store.CONFIG_PATH.parent / "diagnostics.log"


def log(text):
    """Append one dated event line. Never raises."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _append(f"{stamp}  {text}\n")


def log_exception(context, exc):
    """Record an unexpected error with its full technical traceback.

    context is plain language ("Rendering the Quality tab"); the
    traceback lines are indented below it so the log stays scannable
    line by line.
    """
    detail = "".join(traceback.format_exception(
        type(exc), exc, exc.__traceback__))
    indented = "".join("    " + line + "\n"
                       for line in detail.rstrip().splitlines())
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _append(f"{stamp}  UNEXPECTED ERROR - {context}\n{indented}")


def read_recent(max_lines=200):
    """The newest part of the log as text, newest events LAST
    (chronological order reads naturally), or "" if there is no log."""
    try:
        text = log_path().read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def clear():
    """Empty the log (the Diagnostics page's clear button)."""
    try:
        log_path().write_text("", encoding="utf-8")
    except OSError:
        pass


def _append(chunk):
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(chunk)
        if path.stat().st_size > _MAX_BYTES:
            _rotate(path)
    except OSError:
        pass  # rule 1: logging never breaks the app


def _rotate(path):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    keep = lines[len(lines) // 2:]
    path.write_text(
        "(older diagnostics entries were dropped to keep this file "
        "small)\n" + "".join(keep), encoding="utf-8")
