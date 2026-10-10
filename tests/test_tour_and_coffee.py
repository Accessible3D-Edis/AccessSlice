"""The guided tour and the monthly buy-me-a-coffee pop-up.

Tour: reachable from every page's top navigation, shown once after the
first completed setup with a skip button, never again after "done"
(Settings then goes straight home), every editor tab links to its own
section, and each tab's heading carries the way back. Coffee: the
pop-up is a dialog on the Home page when due - a week after the
first start, then a month after the last answer - and nowhere else;
"Buy me a coffee" opens the page in the browser (a stand-in records
it; no browser is opened here) and rests it a month; "Not now"
rests it a month. Both run over real HTTP against the sandboxed
server.
"""

import datetime
import http.client
import os
import shutil
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

import support
import coffee
import config_store
import server
import webui


class F:
    httpd = None
    port = None
    sandbox = None
    project = None
    old_config = None
    old_appdata = None
    fake_exe = None
    projects_dir = None


def setUpModule():
    F.sandbox = support.make_sandbox()
    F.projects_dir = F.sandbox / "projects"
    F.projects_dir.mkdir()
    F.project = support.copy_test_project(F.projects_dir)
    F.fake_exe = support.make_fake_studio(F.sandbox)
    F.old_config = config_store.CONFIG_PATH
    config_store.CONFIG_PATH = F.sandbox / "config.json"
    F.old_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(F.sandbox / "AppData")
    support.write_studio_conf(os.environ["APPDATA"], {
        "app": {}, "presets": {"machine": "Bambu Lab H2C 0.4 nozzle"}})
    server.state["session"] = None
    F.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    F.port = F.httpd.server_address[1]
    threading.Thread(target=F.httpd.serve_forever, daemon=True).start()


def tearDownModule():
    F.httpd.shutdown()
    F.httpd.server_close()
    server.state["session"] = None
    config_store.CONFIG_PATH = F.old_config
    if F.old_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = F.old_appdata
    shutil.rmtree(F.sandbox, ignore_errors=True)


def request(method, url, form=None):
    conn = http.client.HTTPConnection("127.0.0.1", F.port, timeout=10)
    try:
        if form is None:
            conn.request(method, url)
        else:
            conn.request(method, url, body=urllib.parse.urlencode(form),
                         headers={"Content-Type":
                                  "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode("utf-8")
    finally:
        conn.close()


def configured(**extra):
    config = {"slicer": "bambu", "slicer_exe": str(F.fake_exe),
              "projects_dir": str(F.projects_dir),
              "known_printers": ["Bambu Lab H2C 0.4 nozzle"]}
    config.update(extra)
    config_store.save(config)
    return config


DAY = datetime.timedelta(days=1)
INSTALLED = datetime.datetime(2026, 10, 1, 12, 0)


def week_old(**extra):
    """A config whose first start is more than a week ago."""
    past = datetime.datetime.now() - 8 * DAY
    extra.setdefault(coffee.FIRST_RUN_KEY, past.isoformat())
    return extra


class TestCoffeeRules(unittest.TestCase):
    def test_a_first_start_is_not_due(self):
        self.assertFalse(coffee.due({}))

    def test_first_run_is_stamped_once(self):
        cfg = {}
        self.assertTrue(coffee.note_first_run(cfg, now=INSTALLED))
        self.assertFalse(coffee.note_first_run(cfg, now=INSTALLED + DAY))
        self.assertEqual(cfg[coffee.FIRST_RUN_KEY], INSTALLED.isoformat())

    def test_first_ask_is_a_week_after_the_first_start(self):
        cfg = {}
        coffee.note_first_run(cfg, now=INSTALLED)
        self.assertFalse(coffee.due(cfg, now=INSTALLED + 6 * DAY))
        self.assertTrue(coffee.due(cfg, now=INSTALLED + 7 * DAY))

    def test_rest_lasts_a_month(self):
        cfg = {}
        coffee.note_first_run(cfg, now=INSTALLED)
        asked = INSTALLED + 8 * DAY
        coffee.rest(cfg, now=asked)
        self.assertFalse(coffee.due(cfg, now=asked + 29 * DAY))
        self.assertTrue(coffee.due(cfg, now=asked + 30 * DAY))

    def test_a_damaged_stamp_means_due_not_a_crash(self):
        self.assertTrue(coffee.due(week_old(**{coffee.CONFIG_KEY:
                                                   "not a date"})))

    def test_a_damaged_first_run_is_restamped(self):
        cfg = {coffee.FIRST_RUN_KEY: "not a date"}
        self.assertFalse(coffee.due(cfg))
        self.assertTrue(coffee.note_first_run(cfg))

    def test_open_page_uses_the_coffee_address(self):
        opened = []
        coffee.open_page(opener=opened.append)
        self.assertEqual(opened, ["https://buymeacoffee.com/accessible3d"])


class TestTourAndCoffeeOverHttp(unittest.TestCase):
    def setUp(self):
        server.state["session"] = None
        self._opened = []
        self._old_open = coffee.webbrowser.open
        coffee.webbrowser.open = self._opened.append

    def tearDown(self):
        coffee.webbrowser.open = self._old_open

    # ---------------------------------------------------------- tour
    def test_tour_is_in_the_navigation_of_every_page(self):
        configured()
        for url in ("/", "/setup", "/files", "/diagnostics", "/limits",
                    "/tour"):
            _s, _h, body = request("GET", url)
            self.assertIn('href="/tour">Help and tour</a>', body, url)

    def test_tour_page_names_every_tab_and_the_navigation_rules(self):
        configured()
        status, _h, body = request("GET", "/tour")
        self.assertEqual(status, 200)
        for tab_id, name in webui.EDITOR_TABS:
            self.assertIn(f'id="tab-{tab_id}"', body)
            self.assertIn(f"Open the {name} tab", body)
        self.assertIn('id="tab-review"', body)
        self.assertIn("Landmarks", body)
        self.assertIn("Bambu Studio", body)
        self.assertNotIn("Skip the tour", body)

    def test_first_setup_leads_into_the_tour_and_only_once(self):
        config_store.save({})
        status, headers, _b = request("POST", "/setup", {
            "slicer": "bambu", "slicer_exe": str(F.fake_exe),
            "projects_dir": str(F.projects_dir)})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/tour?first=1"))
        _s, _h, body = request("GET", "/tour?first=1")
        self.assertIn("Skip the tour", body)
        self.assertIn("Start using AccessSlice", body)

        status, headers, _b = request("POST", "/tour/done", {})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/"))
        self.assertTrue(config_store.load().get("tour_seen"))

        status, headers, _b = request("POST", "/setup", {
            "slicer": "bambu", "slicer_exe": str(F.fake_exe),
            "projects_dir": str(F.projects_dir)})
        self.assertTrue(headers["Location"].startswith("/?msg="))

    def test_every_editor_tab_links_to_its_tour_section(self):
        configured()
        status, _h, _b = request("POST", "/open", {"path": str(F.project)})
        self.assertEqual(status, 303)
        for tab_id, name in webui.EDITOR_TABS:
            _s, _h, body = request("GET", f"/project/{tab_id}")
            self.assertIn(f'href="/tour#tab-{tab_id}">What the {name} tab '
                          f"is for</a>", body, tab_id)
        request("POST", "/project/close", {})

    # -------------------------------------------------------- coffee
    def test_no_pop_up_in_the_first_week(self):
        configured()
        _s, _h, body = request("GET", "/")
        self.assertNotIn('id="coffee-dialog"', body)
        self.assertIn(coffee.FIRST_RUN_KEY, config_store.load())
        self.assertIn("Support AccessSlice - buy me a coffee", body)

    def test_pop_up_shows_on_home_when_due_and_nowhere_else(self):
        configured(**week_old())
        _s, _h, body = request("GET", "/")
        self.assertIn('<dialog open class="coffee" id="coffee-dialog"',
                      body)
        self.assertIn('aria-labelledby="coffee-heading"', body)
        self.assertIn("Buy me a coffee", body)
        self.assertIn('id="coffee-later-form"', body)
        for url in ("/setup", "/files", "/tour", "/diagnostics"):
            _s, _h, other = request("GET", url)
            self.assertNotIn('id="coffee-dialog"', other, url)

    def test_the_script_makes_it_modal_and_escape_means_not_now(self):
        script = (support.REPO_ROOT / "app" / "static" /
                  "app.js").read_text(encoding="utf-8")
        self.assertIn("coffeeDialog.showModal()", script)
        self.assertIn('addEventListener("cancel"', script)
        self.assertIn("coffee-later-form", script)

    def test_not_now_rests_the_pop_up_a_month(self):
        configured(**week_old())
        status, headers, _b = request("POST", "/coffee/later", {})
        self.assertEqual(status, 303)
        self.assertIn("about a month",
                      urllib.parse.unquote(headers["Location"]))
        cfg = config_store.load()
        self.assertIn(coffee.CONFIG_KEY, cfg)
        self.assertFalse(coffee.due(cfg))
        self.assertTrue(coffee.due(cfg, now=datetime.datetime.now()
                                   + 31 * DAY))
        _s, _h, body = request("GET", "/")
        self.assertNotIn('id="coffee-dialog"', body)
        # The quiet permanent link stays for anyone who wants it.
        self.assertIn("Support AccessSlice - buy me a coffee", body)
        self.assertEqual(self._opened, [])

    def test_buy_opens_the_page_in_the_browser_and_rests_the_pop_up(self):
        configured(**week_old())
        status, headers, _b = request("POST", "/coffee/open", {})
        self.assertEqual(status, 303)
        self.assertEqual(self._opened, ["https://buymeacoffee.com/accessible3d"])
        self.assertIn("thank you", urllib.parse.unquote(headers["Location"]))
        self.assertFalse(coffee.due(config_store.load()))

    def test_pop_up_is_back_after_the_rest(self):
        past = (datetime.datetime.now() - datetime.timedelta(minutes=1))
        configured(**week_old(**{coffee.CONFIG_KEY: past.isoformat()}))
        _s, _h, body = request("GET", "/")
        self.assertIn('id="coffee-dialog"', body)


if __name__ == "__main__":
    unittest.main()
