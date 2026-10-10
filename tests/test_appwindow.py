"""The app window launcher: server up while the window is open, down
the moment it closes, browser fallback when no window can open.

The window itself (pywebview over WebView2) is never opened here -
the suite must never show a window or need pywebview installed. A
stand-in "window" function plays the user: it talks to the server
over HTTP while the window is "open", then returns as a closed window
would. What is pinned: the port is chosen from the preferred list,
the server answers on it only while the window is open, the fallback
path is taken with the reason when the window raises, and --serve
runs headless for the packaged-program tests.
"""

import http.client
import socket
import threading
import unittest

import support  # noqa: F401  (sandboxes APPDATA before app imports)
import appwindow
import config_store
import diagnostics
import server


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url):
    host, _, rest = url[len("http://"):].partition("/")
    h, p = host.split(":")
    conn = http.client.HTTPConnection(h, int(p), timeout=5)
    conn.request("GET", "/" + rest)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def _port_answers(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


class TestAppWindow(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._old_config = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = support.make_sandbox() / "config.json"
        server.state["session"] = None

    def tearDown(self):
        config_store.CONFIG_PATH = self._old_config
        super().tearDown()

    def test_server_answers_only_while_the_window_is_open(self):
        port = _free_port()
        seen = {}

        def fake_window(url):
            seen["url"] = url
            # A fresh config: the home page sends the user to setup.
            status, body = _get(url + "setup")
            seen["status"] = status
            seen["home"] = b"AccessSlice" in body
            status, body = _get(url + "static/style.css")
            seen["css"] = status == 200 and b"--accent" in body

        rc = appwindow.run(open_window_fn=fake_window,
                           fallback_fn=self._no_fallback, ports=[port])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["url"], f"http://127.0.0.1:{port}/")
        self.assertEqual(seen["status"], 200)
        self.assertTrue(seen["home"])
        self.assertTrue(seen["css"])
        self.assertFalse(_port_answers(port),
                         "the server must stop when the window closes")
        log = diagnostics.read_recent()
        self.assertIn("started as a window", log)
        self.assertIn("server stopped", log)

    def test_a_busy_preferred_port_is_skipped(self):
        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        busy_port = busy.getsockname()[1]
        free = _free_port()
        seen = {}
        try:
            appwindow.run(open_window_fn=lambda url: seen.setdefault(
                "url", url), fallback_fn=self._no_fallback,
                ports=[busy_port, free])
        finally:
            busy.close()
        self.assertEqual(seen["url"], f"http://127.0.0.1:{free}/")

    def test_no_free_port_is_a_spoken_message_not_a_crash(self):
        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        boxes = []
        old_box = appwindow.message_box
        appwindow.message_box = lambda text, title="": boxes.append(text)
        try:
            rc = appwindow.run(open_window_fn=self._never,
                               fallback_fn=self._no_fallback,
                               ports=[busy.getsockname()[1]])
        finally:
            appwindow.message_box = old_box
            busy.close()
        self.assertEqual(rc, 1)
        self.assertEqual(len(boxes), 1)
        self.assertIn("free port", boxes[0])

    def test_window_failure_falls_back_with_the_reason(self):
        port = _free_port()
        fallback = {}

        def broken_window(url):
            raise RuntimeError("WebView2 runtime is not installed")

        def fake_fallback(url, reason):
            fallback["url"] = url
            fallback["reason"] = reason
            fallback["alive"] = _port_answers(port)

        rc = appwindow.run(open_window_fn=broken_window,
                           fallback_fn=fake_fallback, ports=[port])
        self.assertEqual(rc, 0)
        self.assertEqual(fallback["url"], f"http://127.0.0.1:{port}/")
        self.assertIn("RuntimeError", fallback["reason"])
        self.assertIn("WebView2", fallback["reason"])
        self.assertTrue(fallback["alive"],
                        "the server must still run during the fallback")
        self.assertFalse(_port_answers(port))

    def test_browser_fallback_opens_browser_and_holds_on_the_box(self):
        opened = []
        boxes = []
        old_open = appwindow.webbrowser.open
        old_box = appwindow.message_box
        appwindow.webbrowser.open = lambda url: opened.append(url)
        appwindow.message_box = lambda text, title="": boxes.append(text)
        try:
            appwindow.browser_fallback("http://127.0.0.1:1/", "no window")
        finally:
            appwindow.webbrowser.open = old_open
            appwindow.message_box = old_box
        self.assertEqual(opened, ["http://127.0.0.1:1/"])
        self.assertEqual(len(boxes), 1)
        self.assertIn("Press OK when you are finished", boxes[0])
        self.assertIn("no window", boxes[0])
        self.assertIn("App window unavailable", diagnostics.read_recent())

    def test_serve_only_runs_headless_until_ended(self):
        port = _free_port()
        thread = threading.Thread(target=appwindow.serve_only, args=(port,),
                                  daemon=True)
        thread.start()
        deadline = 50
        while not _port_answers(port) and deadline:
            deadline -= 1
            threading.Event().wait(0.1)
        status, body = _get(f"http://127.0.0.1:{port}/setup")
        self.assertEqual(status, 200)
        self.assertIn(b"AccessSlice", body)
        # It is still up: --serve ends only with the process.
        self.assertTrue(thread.is_alive())

    def test_main_routes_serve_flag_and_window_run(self):
        calls = []
        old_serve, old_run = appwindow.serve_only, appwindow.run
        appwindow.serve_only = lambda port: calls.append(("serve", port))
        appwindow.run = lambda: calls.append(("run",)) or 0
        try:
            self.assertEqual(appwindow.main(["--serve", "8470"]), 0)
            self.assertEqual(appwindow.main([]), 0)
        finally:
            appwindow.serve_only, appwindow.run = old_serve, old_run
        self.assertEqual(calls, [("serve", 8470), ("run",)])

    def test_version_is_on_the_diagnostics_page(self):
        import webui
        from version import VERSION
        html = webui.render_diagnostics("", "C:\\somewhere\\diagnostics.log")
        self.assertIn(f"AccessSlice version {VERSION}", html)

    # helpers ------------------------------------------------------
    def _no_fallback(self, url, reason):
        self.fail(f"fallback must not run: {reason}")

    def _never(self, url):
        self.fail("the window must not open without a port")


if __name__ == "__main__":
    unittest.main()
