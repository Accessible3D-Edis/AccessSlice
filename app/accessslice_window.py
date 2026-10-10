"""accessslice_window.py - the packaged program's entry point.

AccessSlice.exe runs this: the server starts, the AccessSlice window
opens, and everything stops when the window closes (appwindow.py).
Developers keep using accessslice.py, which opens a browser tab and
prints to a console instead.

    python app\\accessslice_window.py            the window, as installed
    python app\\accessslice_window.py --serve 8470   server only (tests)
"""

import sys

import appwindow

if __name__ == "__main__":
    sys.exit(appwindow.main())
