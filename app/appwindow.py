"""appwindow.py - AccessSlice as a normal program window.

The installed AccessSlice is not a browser tab: it is its own window,
titled AccessSlice, drawn by the Edge WebView2 runtime that Windows 11
ships (pywebview opens it). Screen readers read WebView2 exactly as
they read Edge, so everything the page promises holds inside the
window too. This module does three things and nothing else:

1. starts the local server on 127.0.0.1 (nothing leaves the machine),
2. opens the window on it and waits until the user closes the window,
3. stops the server - nothing keeps running behind a closed window.

If the window cannot open at all (the WebView2 runtime missing, which
the installer checks for, or pywebview absent in a developer's
Python), AccessSlice falls back to the default browser and shows a
plain message box that keeps the program alive until the user
presses OK. The reason is written to the diagnostics log.

The development entry point (accessslice.py, browser tab, console)
is unchanged; accessslice_window.py is the entry point the packaged
program runs.
"""

import ctypes
import socket
import sys
import threading
import webbrowser

import diagnostics
import server
from version import VERSION

WINDOW_TITLE = "AccessSlice"

# A fixed home port so the address is predictable, with fallbacks in case
# something else on this machine happens to use it.
PREFERRED_PORTS = [8462, 8463, 8464, 8465]

# The window's opening size and the smallest it may be shrunk to - the
# stylesheet reflows down to 360 pixels wide, so 400% zoom in a small
# window still shows no sideways scrolling.
WINDOW_SIZE = (1200, 820)
MIN_SIZE = (480, 360)


class NoFreePort(Exception):
    pass


def pick_port(ports=PREFERRED_PORTS):
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise NoFreePort(
        "AccessSlice could not find a free port. Is it already running?")


def start_server(port):
    """The server running on its own thread; returns it for shutdown()."""
    httpd = server.make_server(port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True,
                              name="accessslice-server")
    thread.start()
    return httpd


def stop_server(httpd):
    httpd.shutdown()
    httpd.server_close()


def open_window(url):
    """Open the WebView2 window on the URL; returns when it is closed.

    Raises whatever pywebview raises when no window can be made, so
    the caller can fall back to the browser.
    """
    import webview  # imported here: the development run never needs it

    webview.create_window(
        WINDOW_TITLE, url,
        width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=MIN_SIZE,
        text_select=True, zoomable=True)
    # private_mode=False keeps the page's own storage (the 3D view's
    # remembered camera) between runs, like a browser would.
    webview.start(private_mode=False)


def message_box(text, title=WINDOW_TITLE):
    """A native, screen-reader-readable OK box; blocks until pressed."""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except (AttributeError, OSError):
        # Not Windows (a developer elsewhere): print instead and wait.
        print(text)
        try:
            input("Press Enter to close AccessSlice. ")
        except EOFError:
            pass


def browser_fallback(url, reason):
    """No window could open: the browser instead, kept alive by a box."""
    diagnostics.log(f"App window unavailable ({reason}); "
                    f"opened the browser on {url} instead")
    webbrowser.open(url)
    message_box(
        "AccessSlice could not open its own window on this computer, "
        "so it has opened in your web browser instead.\n\n"
        f"If the browser did not open, go to {url}\n\n"
        "Keep this message open while you work. Press OK when you are "
        "finished with AccessSlice to close it.\n\n"
        f"Reason: {reason}")


def run(open_window_fn=open_window, fallback_fn=browser_fallback,
        ports=PREFERRED_PORTS):
    """Start the server, show the window, stop the server. Returns 0."""
    try:
        port = pick_port(ports)
    except NoFreePort as e:
        message_box(str(e))
        return 1
    url = f"http://127.0.0.1:{port}/"
    httpd = start_server(port)
    diagnostics.log(f"AccessSlice {VERSION} started as a window on {url}")
    try:
        try:
            open_window_fn(url)
        except Exception as e:  # noqa: BLE001 - any failure -> browser
            fallback_fn(url, f"{type(e).__name__}: {e}")
    finally:
        stop_server(httpd)
        diagnostics.log("AccessSlice window closed; server stopped")
    return 0


def serve_only(port):
    """Run the server with no window until the process is ended.

    Used by the packaged-program tests, which start AccessSlice.exe
    with --serve PORT and talk to it over HTTP - so the very bundle
    that ships is what the suite exercises.
    """
    httpd = start_server(port)
    diagnostics.log(f"AccessSlice {VERSION} serving without a window "
                    f"on port {port}")
    try:
        threading.Event().wait()
    finally:
        stop_server(httpd)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--serve":
        serve_only(int(argv[1]))
        return 0
    try:
        return run()
    except Exception as e:  # noqa: BLE001 - a windowed program has no console
        diagnostics.log_exception("starting the app window", e)
        message_box(
            "AccessSlice could not start.\n\n"
            f"{type(e).__name__}: {e}\n\n"
            "The details are in the diagnostics log:\n"
            f"{diagnostics.log_path()}")
        return 1
