"""accessslice.py - start AccessSlice.

Run this file with Python (no arguments needed):

    python accessslice.py

It starts the local server on 127.0.0.1 (nothing leaves this machine),
opens your default browser on the AccessSlice page, and keeps running
until you close the window it prints in (or press Ctrl+C).

In the packaged, installed version of AccessSlice this same code runs
behind a normal program icon - this file is also the development entry
point.
"""

import socket
import threading
import time
import webbrowser

import server

# A fixed home port so the address is predictable, with fallbacks in case
# something else on this machine happens to use it.
PREFERRED_PORTS = [8462, 8463, 8464, 8465]


def pick_port():
    for port in PREFERRED_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit(
        "AccessSlice could not find a free port. Is it already running? "
        "Check your browser for an open AccessSlice tab."
    )


def main():
    port = pick_port()
    url = f"http://127.0.0.1:{port}/"

    thread = threading.Thread(
        target=server.serve, args=(port,), daemon=True
    )
    thread.start()

    print("AccessSlice is running.")
    print(f"If the browser did not open, go to: {url}")
    print("Keep this window open while you work. Press Ctrl+C to quit.")
    webbrowser.open(url)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("AccessSlice closed.")


if __name__ == "__main__":
    main()
