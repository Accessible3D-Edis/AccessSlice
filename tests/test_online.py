"""The update check and the opt-in anonymous usage reports (online.py).

What must hold, decided 2026-11-27:
- Nothing about use is counted or sent until the user says yes; "No",
  Escape and turning it off later all leave nothing behind (off also
  deletes the counts and the install number).
- A report holds only the documented fields - never a path, a file
  name, a serial number or a profile name the user typed.
- The update check sends only the version, runs at most daily, and a
  newer version is announced on Home with a button that opens the
  download page (only an https link on a known host is ever opened).
- The suite itself never goes online: online.py is pointed at a fake
  server on 127.0.0.1 here, and ACCESSSLICE_OFFLINE (set by support)
  keeps the background thread from starting anywhere else.
"""

import datetime
import http.client
import json
import os
import shutil
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import support
import coffee
import config_store
import online
import server
from version import VERSION

DAY = datetime.timedelta(days=1)


class FakeSite:
    """accessible3d.io's two endpoints, on 127.0.0.1."""

    def __init__(self):
        self.requests = []          # (method, path, headers, body)
        self.latest = {"latest_version": "9.0.0",
                       "download_url": "https://accessible3d.io/software/"
                                       "accessslice/",
                       "notes": "STEP files and more."}
        self.latest_raw = None      # bytes to send instead of JSON
        self.usage_status = 204
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self, body=b""):
                site.requests.append((self.command, self.path,
                                      dict(self.headers), body))

            def do_GET(self):
                self._record()
                if self.path.startswith("/api/accessslice/latest"):
                    body = (site.latest_raw if site.latest_raw is not None
                            else json.dumps(site.latest).encode("utf-8"))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self._record(self.rfile.read(length))
                if self.path == "/api/accessslice/usage":
                    self.send_response(site.usage_status)
                    self.end_headers()
                else:
                    self.send_error(404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        self.base = (f"http://127.0.0.1:{self.httpd.server_address[1]}"
                     "/api/accessslice")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class OnlineCase(unittest.TestCase):
    """A sandboxed config + online.json, and online.py aimed at the
    fake site."""

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        old_config = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = self.sandbox / "config.json"
        self.addCleanup(setattr, config_store, "CONFIG_PATH", old_config)
        self.site = FakeSite()
        self.addCleanup(self.site.close)
        old_base = online.BASE_URL
        online.BASE_URL = self.site.base
        self.addCleanup(setattr, online, "BASE_URL", old_base)

    def config(self, **extra):
        config = {"slicer": "bambu",
                  "slicer_exe": str(self.sandbox / "Secret Folder"
                                    / "bambu-studio.exe"),
                  "projects_dir": str(self.sandbox / "Private Projects"),
                  "known_printers": ["Bambu Lab H2C 0.4 nozzle"]}
        config.update(extra)
        config_store.save(config)
        return config


class RulesTests(unittest.TestCase):

    def test_maker_printer_names_pass_custom_ones_do_not(self):
        for name in ("Bambu Lab H2C 0.4 nozzle",
                     "Bambu Lab X1 Carbon 0.4 nozzle",
                     "Creality K2 Plus 0.4 nozzle",
                     "Anycubic Kobra S1 0.4 nozzle"):
            self.assertEqual(online.printer_model(name), name)
        for name in ("Jane's H2C", "Bambu Lab H2C 0.4 nozzle - Jane",
                     "Bambu Lab H2C 0.4 nozzle (copy) jane@x.dk nozzle",
                     "", None, "My printer 0.4 nozzle"):
            self.assertEqual(online.printer_model(name),
                             online.CUSTOM_PRINTER, name)

    def test_only_https_links_on_known_hosts_are_opened(self):
        good = "https://github.com/Accessible3D-Edis/AccessSlice/releases"
        self.assertEqual(online.safe_download_url(good), good)
        for bad in ("http://accessible3d.io/x", "https://evil.example/x",
                    "file:///C:/Windows/system32/calc.exe",
                    "https://accessible3d.io.evil.example/", "", None):
            self.assertEqual(online.safe_download_url(bad),
                             online.DEFAULT_DOWNLOAD_URL, bad)

    def test_versions_compare_as_numbers(self):
        self.assertGreater(online.parse_version("1.10.0"),
                           online.parse_version("1.9.9"))
        for bad in ("", "v1.2", "1.2-beta", None, 3):
            self.assertIsNone(online.parse_version(bad))

    def test_the_update_check_is_on_and_usage_off_until_asked(self):
        self.assertTrue(online.update_check_enabled({}))
        self.assertFalse(online.usage_enabled({}))
        self.assertFalse(online.usage_asked({}))
        self.assertTrue(online.usage_asked({"usage_stats": False}))


class CountingTests(OnlineCase):

    def test_nothing_is_counted_before_a_yes(self):
        self.config()
        online.count("slices_done", printer="Bambu Lab H2C 0.4 nozzle")
        self.assertEqual(online.load_state(), {})
        self.config(usage_stats=False)
        online.count("slices_done")
        self.assertEqual(online.load_state(), {})

    def test_counts_and_printers_after_a_yes(self):
        config = online.set_usage(self.config(), True)
        config_store.save(config)
        online.count("slices_done", printer="Bambu Lab H2C 0.4 nozzle")
        online.count("slices_done", printer="Jane's printer")
        online.count("jobs_from_step")
        online.count("not_an_event")
        state = online.load_state()
        self.assertEqual(state["counts"],
                         {"slices_done": 2, "jobs_from_step": 1})
        self.assertEqual(state["printers"],
                         {"Bambu Lab H2C 0.4 nozzle": 1,
                          online.CUSTOM_PRINTER: 1})

    def test_off_deletes_the_counts_and_on_starts_a_new_number(self):
        config = online.set_usage(self.config(), True)
        config_store.save(config)
        first = online.load_state()["install_id"]
        online.count("app_starts")
        config_store.save(online.set_usage(config, False))
        state = online.load_state()
        for key in ("install_id", "counts", "printers", "period_start"):
            self.assertNotIn(key, state)
        config_store.save(online.set_usage(config, True))
        state = online.load_state()
        self.assertNotEqual(state["install_id"], first)
        self.assertEqual(state["counts"], {})

    def test_the_report_holds_only_the_documented_fields(self):
        config = online.set_usage(self.config(), True)
        config_store.save(config)
        online.count("jobs_created", printer="Bambu Lab H2C 0.4 nozzle")
        payload = online.report_payload(config)
        self.assertEqual(set(payload), {
            "schema", "install_id", "app_version", "windows_version",
            "slicer", "period_start", "period_end", "counts", "printers"})
        self.assertEqual(set(payload["counts"]), set(online.EVENTS))
        self.assertEqual(payload["counts"]["jobs_created"], 1)
        self.assertEqual(payload["app_version"], VERSION)
        text = json.dumps(payload)
        for private in ("Secret Folder", "Private Projects", "\\\\",
                        str(self.sandbox), "bambu-studio.exe"):
            self.assertNotIn(private, text)


class NetworkTests(OnlineCase):

    def test_update_check_sends_only_the_version(self):
        self.config()
        self.assertTrue(online.check_update())
        method, path, headers, body = self.site.requests[-1]
        self.assertEqual(method, "GET")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        self.assertEqual(query, {"version": [VERSION]})
        self.assertEqual(headers["User-Agent"], f"AccessSlice/{VERSION}")
        self.assertEqual(body, b"")
        found = online.update_available(config_store.load())
        self.assertEqual(found, ("9.0.0",
                                 "https://accessible3d.io/software/"
                                 "accessslice/", "STEP files and more."))

    def test_same_or_older_versions_announce_nothing(self):
        self.config()
        for version in (VERSION, "0.9.0"):
            self.site.latest["latest_version"] = version
            online.check_update()
            self.assertIsNone(online.update_available(config_store.load()))

    def test_a_bad_answer_is_ignored(self):
        self.config()
        self.site.latest_raw = b"<html>maintenance</html>"
        self.assertFalse(online.check_update())
        self.assertIsNone(online.update_available(config_store.load()))
        self.site.latest_raw = None
        self.site.latest["download_url"] = "https://evil.example/x.exe"
        online.check_update()
        self.assertEqual(online.update_available(config_store.load())[1],
                         online.DEFAULT_DOWNLOAD_URL)

    def test_switching_the_check_off_hides_the_notice(self):
        config = self.config()
        online.check_update()
        config_store.save(online.set_update_check(config, False))
        self.assertIsNone(online.update_available(config_store.load()))
        self.assertFalse(online.update_due(config_store.load(),
                                           online.load_state()))

    def test_report_is_the_payload_and_a_success_starts_a_new_week(self):
        config = online.set_usage(self.config(), True)
        config_store.save(config)
        online.count("slices_done", printer="Bambu Lab H2C 0.4 nozzle")
        expected = online.report_payload(config)
        self.assertTrue(online.send_report(config))
        method, path, headers, body = self.site.requests[-1]
        self.assertEqual((method, path), ("POST", "/api/accessslice/usage"))
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), expected)
        state = online.load_state()
        self.assertEqual(state["counts"], {})
        self.assertEqual(state["printers"], {})
        self.assertIn("last_report_sent", state)

    def test_a_refused_report_keeps_its_counts(self):
        config = online.set_usage(self.config(), True)
        config_store.save(config)
        online.count("slices_done")
        self.site.usage_status = 500
        self.assertFalse(online.send_report(config))
        self.assertEqual(online.load_state()["counts"], {"slices_done": 1})

    def test_when_things_are_due(self):
        config = online.set_usage(self.config(), True)
        config_store.save(config)
        state = online.load_state()
        start = datetime.datetime.fromisoformat(state["period_start"])
        self.assertTrue(online.update_due(config, state, start))
        self.assertFalse(online.report_due(config, state, start + 6 * DAY))
        self.assertTrue(online.report_due(config, state, start + 7 * DAY))
        # A failed attempt waits a day before the next try.
        state["last_report_attempt"] = (start + 7 * DAY).isoformat()
        self.assertFalse(online.report_due(config, state,
                                           start + 7 * DAY + DAY / 2))
        self.assertTrue(online.report_due(config, state, start + 8 * DAY))
        state["last_update_check"] = start.isoformat()
        self.assertFalse(online.update_due(config, state, start + DAY / 2))
        self.assertTrue(online.update_due(config, state, start + DAY))

    def test_tick_stays_offline_when_told_to(self):
        self.config()
        self.assertTrue(online.offline())  # support sets it
        online.tick()
        self.assertEqual(self.site.requests, [])
        os.environ.pop(online.OFFLINE_ENV)
        try:
            online.tick()
        finally:
            os.environ[online.OFFLINE_ENV] = "1"
        self.assertEqual(len(self.site.requests), 1)  # the update check


class OverHttpTests(OnlineCase):
    """The Home question, the update notice and the Privacy page over
    real HTTP against the sandboxed server."""

    def setUp(self):
        super().setUp()
        self.fake_exe = support.make_fake_studio(self.sandbox)
        server.state["session"] = None
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever,
                         daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.opened = []
        old_open = online.webbrowser.open
        online.webbrowser.open = self.opened.append
        self.addCleanup(setattr, online.webbrowser, "open", old_open)

    def config(self, **extra):
        extra.setdefault("slicer_exe", str(self.fake_exe))
        return super().config(**extra)

    def request(self, method, url, form=None):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_address[1], timeout=10)
        try:
            if form is None:
                conn.request(method, url)
            else:
                conn.request(method, url,
                             body=urllib.parse.urlencode(form),
                             headers={"Content-Type": "application/"
                                      "x-www-form-urlencoded"})
            r = conn.getresponse()
            return (r.status, dict(r.getheaders()),
                    r.read().decode("utf-8"))
        finally:
            conn.close()

    def test_home_asks_once_and_a_no_sends_nothing(self):
        self.config()
        _s, _h, body = self.request("GET", "/")
        self.assertIn('id="usage-dialog"', body)
        self.assertIn('aria-labelledby="usage-heading"', body)
        status, headers, _b = self.request("POST", "/usage/answer",
                                           {"answer": "no"})
        self.assertEqual(status, 303)
        self.assertIs(config_store.load()["usage_stats"], False)
        _s, _h, body = self.request("GET", "/")
        self.assertNotIn('id="usage-dialog"', body)
        self.assertEqual(online.load_state().get("counts"), None)

    def test_a_yes_starts_counting(self):
        self.config()
        self.request("POST", "/usage/answer", {"answer": "yes"})
        self.assertIs(config_store.load()["usage_stats"], True)
        self.assertTrue(online.load_state()["install_id"])
        self.request("GET", "/limits")
        self.request("POST", "/open", {"path": str(self.sandbox / "x.3mf")})
        self.assertEqual(online.load_state()["counts"].get(
            "problems_shown"), 1)

    def test_the_coffee_pop_up_goes_first(self):
        past = datetime.datetime.now() - 8 * DAY
        self.config(**{coffee.FIRST_RUN_KEY: past.isoformat()})
        _s, _h, body = self.request("GET", "/")
        self.assertIn('id="coffee-dialog"', body)
        self.assertNotIn('id="usage-dialog"', body)

    def test_the_script_makes_the_question_modal_escape_means_no(self):
        script = (support.REPO_ROOT / "app" / "static" /
                  "app.js").read_text(encoding="utf-8")
        self.assertIn("usageDialog.showModal()", script)
        self.assertIn("usage-no-form", script)

    def test_privacy_page_shows_the_exact_report_and_the_switches(self):
        self.config(usage_stats=False)
        _s, _h, body = self.request("GET", "/privacy")
        self.assertIn("Turn anonymous usage reports on", body)
        self.assertIn("Turn the update check off", body)
        self.request("POST", "/privacy/usage", {"on": "yes"})
        online.count("sends_usb")
        _s, _h, body = self.request("GET", "/privacy")
        self.assertIn("Exactly what the next report contains", body)
        self.assertIn(online.load_state()["install_id"], body)
        self.assertIn("&quot;sends_usb&quot;: 1", body)
        self.request("POST", "/privacy/usage", {"on": "no"})
        self.assertNotIn("install_id", online.load_state())
        self.request("POST", "/privacy/update-check", {"on": "no"})
        self.assertIs(config_store.load()["update_check"], False)
        _s, _h, home = self.request("GET", "/")
        self.assertIn('href="/privacy"', home)

    def test_the_privacy_route_leaves_the_other_routes_working(self):
        # A local named "state" in the Privacy route once hid the
        # server's global state for the whole GET router: every route
        # after it answered 500 (found by the full suite, 2026-11-27).
        self.config(usage_stats=False)
        for url in ("/api/scene", "/api/watch", "/project/quality"):
            status, _h, _b = self.request("GET", url)
            self.assertNotEqual(status, 500, url)

    def test_a_newer_version_is_announced_and_its_page_opened(self):
        self.config(usage_stats=False)
        online.check_update()
        _s, _h, body = self.request("GET", "/")
        self.assertIn("AccessSlice 9.0.0 is available", body)
        self.assertIn("STEP files and more.", body)
        status, _h, _b = self.request("POST", "/update/open", {})
        self.assertEqual(status, 303)
        self.assertEqual(self.opened, ["https://accessible3d.io/software/"
                                       "accessslice/"])


if __name__ == "__main__":
    unittest.main()
