"""The setup wizard (asked for by Edis 2026-10-10, before the first
public release): a first run lands in it, the four printers tested in
the house are named as tested and every other choice as untested, each
printer gets its slicer's steps one page at a time, the folders step
is the Settings save, the network step tests the printer before it
saves anything (the address read from the slicer's own records is
filled in), the project step lists what is in the projects folder with
the printer each project was made for, and Finish leads into the
tour. All over real HTTP against the sandboxed server; the printer is
a stand-in, no network is touched.
"""

import http.client
import re
import os
import shutil
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

import support
import config_store
import prusalink
import server
import wizard


class F:
    httpd = None
    port = None
    sandbox = None
    old_config = None
    old_appdata = None
    studio = None
    prusa = None
    projects_dir = None


def setUpModule():
    F.sandbox = support.make_sandbox()
    F.projects_dir = F.sandbox / "projects"
    F.studio = support.make_fake_studio(F.sandbox)
    F.old_config = config_store.CONFIG_PATH
    config_store.CONFIG_PATH = F.sandbox / "config.json"
    F.old_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(F.sandbox / "AppData")
    F.prusa = support.make_fake_prusa(F.sandbox)
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


def location(headers):
    return urllib.parse.unquote_plus(headers.get("Location", ""))


def physical_printer(host, key):
    """A PrusaSlicer physical printer record in the sandbox AppData."""
    folder = F.sandbox / "AppData" / "PrusaSlicer" / "physical_printer"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Core One.ini").write_text(
        f"host_type = prusalink\nprint_host = {host}\n"
        f"printhost_apikey = {key}\n", encoding="utf-8")
    return folder


class WizardCase(unittest.TestCase):
    def setUp(self):
        config_store.save({})
        shutil.rmtree(F.projects_dir, ignore_errors=True)
        F.projects_dir.mkdir()
        shutil.rmtree(F.sandbox / "AppData" / "PrusaSlicer" /
                      "physical_printer", ignore_errors=True)
        self._old_check = prusalink.check_printer
        self._old_stable = prusalink.stable_address
        self.asked = []

        def check(ip, key="", timeout=10):
            self.asked.append((ip, key))
            if ip == "printer.invalid":
                raise prusalink.ProjectError("No answer from the printer.")
            return {"printer": "Core One", "firmware": "6.4.0"}
        prusalink.check_printer = check
        prusalink.stable_address = lambda ip, key="", timeout=10: ip

    def tearDown(self):
        prusalink.check_printer = self._old_check
        prusalink.stable_address = self._old_stable

    def choose(self, printer_id):
        status, headers, _b = request("POST", "/welcome",
                                      {"printer": printer_id})
        self.assertEqual(status, 303)
        self.assertEqual(location(headers), "/welcome/1")

    def folders(self, number, exe):
        return request("POST", f"/welcome/{number}", {
            "action": "folders", "projects_dir": str(F.projects_dir),
            "slicer_exe": str(exe)})


class TestChoosing(WizardCase):
    def test_a_first_run_lands_in_the_wizard(self):
        status, headers, _b = request("GET", "/")
        self.assertEqual(status, 303)
        self.assertEqual(location(headers), "/welcome")
        status, _h, body = request("GET", "/welcome")
        self.assertEqual(status, 200)
        self.assertIn("setup wizard", body)
        # Settings stays reachable as the fallback.
        status, _h, _b = request("GET", "/setup")
        self.assertEqual(status, 200)

    def test_only_the_four_tested_printers_are_called_tested(self):
        tested = [name for _p, name, _s, t in wizard.PRINTERS if t]
        self.assertEqual(tested, ["Bambu Lab X1 Carbon", "Bambu Lab H2C",
                                  "Prusa Core One", "Creality K2 Plus"])
        _s, _h, body = request("GET", "/welcome")
        tested_part = body.split("Not tested yet")[0]
        for name in tested:
            self.assertIn(name, tested_part)
        for _p, name, _s, t in wizard.PRINTERS:
            if not t:
                self.assertIn(f"{name} - not tested yet", body)

    def test_no_choice_asks_for_one(self):
        status, headers, _b = request("POST", "/welcome", {})
        self.assertEqual(status, 303)
        self.assertIn("Choose your printer first", location(headers))

    def test_a_step_without_a_printer_or_out_of_range_goes_back(self):
        _s, headers, _b = request("GET", "/welcome/1")
        self.assertEqual(location(headers), "/welcome")
        self.choose("x1c")
        _s, headers, _b = request("GET", "/welcome/9")
        self.assertEqual(location(headers), "/welcome")

    def test_every_page_has_one_h1_and_labelled_fields(self):
        pages = ["/welcome"]
        for printer_id in ("coreone", "k2plus", "x1c", "orca"):
            self.choose(printer_id)
            pages += [f"/welcome/{n}" for n in
                      range(1, len(wizard.steps_for(printer_id)) + 1)]
            for url in pages:
                status, _h, body = request("GET", url)
                self.assertEqual(status, 200, url)
                self.assertEqual(body.count("<h1"), 1, url)
                self.assertIn("step" if url != "/welcome" else "wizard",
                              body)
                for field_id in re.findall(
                        r'<input type="text" id="([^"]+)"', body):
                    self.assertIn(f'<label for="{field_id}"', body,
                                  (url, field_id))
            pages = []

    def test_the_wizard_is_linked_from_settings_home_and_the_tour(self):
        config_store.save({"slicer": "bambu", "slicer_exe": str(F.studio),
                           "projects_dir": str(F.projects_dir)})
        for url in ("/", "/setup", "/tour"):
            _s, _h, body = request("GET", url)
            self.assertIn('href="/welcome"', body, url)


class TestBambuWalkthrough(WizardCase):
    def test_x1c_from_choice_to_the_tour(self):
        self.choose("x1c")
        _s, _h, body = request("GET", "/welcome/1")
        self.assertIn("step 1 of 4", body)
        self.assertIn("Install Bambu Studio", body)
        self.assertIn("has been tested with AccessSlice", body)
        self.assertIn("Ctrl+Shift+G", body)
        self.assertIn("does not read a Bambu printer's status", body)
        status, headers, _b = request("POST", "/welcome/1",
                                      {"action": "next"})
        self.assertEqual(location(headers), "/welcome/2")

        _s, _h, body = request("GET", "/welcome/2")
        self.assertIn('id="projects_dir"', body)
        self.assertIn('id="slicer_exe"', body)
        status, headers, _b = self.folders(2, F.studio)
        self.assertEqual(status, 303)
        self.assertTrue(location(headers).startswith("/welcome/3"))
        config = config_store.load()
        self.assertEqual(config["slicer"], "bambu")
        self.assertEqual(config["slicer_exe"], str(F.studio))
        self.assertTrue(config_store.is_complete(config))

        _s, _h, body = request("GET", "/welcome/3")
        self.assertIn("Ctrl+Shift+S", body)
        self.assertIn(str(F.projects_dir), body)
        self.assertIn("No project yet", body)
        support.copy_test_project(F.projects_dir)
        _s, _h, body = request("GET", "/welcome/3")
        self.assertIn("blind-benchy.3mf - for the printer Bambu Lab", body)
        _s, headers, _b = request("POST", "/welcome/3",
                                  {"action": "check"})
        self.assertIn("Found 1 project", location(headers))

        _s, _h, body = request("GET", "/welcome/4")
        self.assertIn("Bambu Studio is installed at", body)
        self.assertIn("holds 1 project; the newest is blind-benchy.3mf",
                      body)
        self.assertIn("own send window", body)
        self.assertIn("The Bambu Lab X1 Carbon has been tested", body)
        status, headers, _b = request("POST", "/welcome/4",
                                      {"action": "finish"})
        self.assertEqual(status, 303)
        self.assertTrue(location(headers).startswith("/tour?first=1"))
        self.assertTrue(config_store.load()[wizard.DONE_KEY])

    def test_a_wrong_program_is_refused_and_nothing_is_saved(self):
        self.choose("h2c")
        wrong = F.sandbox / "notepad.exe"
        wrong.write_bytes(b"")
        status, headers, _b = self.folders(2, wrong)
        self.assertEqual(location(headers).split("?")[0], "/welcome/2")
        self.assertIn("not Bambu Studio's program", location(headers))
        self.assertFalse(config_store.is_complete(config_store.load()))

    def test_finish_before_the_folders_step_goes_back_to_it(self):
        self.choose("x1c")
        _s, headers, _b = request("POST", "/welcome/4",
                                  {"action": "finish"})
        self.assertTrue(location(headers).startswith("/welcome/2"))
        self.assertNotIn(wizard.DONE_KEY, config_store.load())

    def test_an_untested_printer_says_so(self):
        self.choose("bambu-other")
        _s, _h, body = request("GET", "/welcome/1")
        self.assertIn("has not been tested with AccessSlice yet", body)


class TestPrusaWalkthrough(WizardCase):
    def test_core_one_has_the_network_steps(self):
        self.assertEqual(wizard.steps_for("coreone"),
                         ["install", "printer-network", "folders",
                          "network", "project", "finish"])
        self.choose("coreone")
        _s, _h, body = request("GET", "/welcome/2")
        self.assertIn("PrusaLink", body)
        self.assertIn("USB drive", body)
        self.folders(3, F.prusa)
        self.assertEqual(config_store.load()["slicer"], "prusa")

    def test_the_network_step_fills_in_prusaslicers_printer(self):
        physical_printer("http://printer.example", "abcdEFGH")
        self.choose("coreone")
        self.folders(3, F.prusa)
        _s, _h, body = request("GET", "/welcome/4")
        self.assertIn('value="printer.example"', body)
        self.assertIn('value="abcdEFGH"', body)
        self.assertIn("found the address in PrusaSlicer", body)

    def test_a_printer_that_answers_is_remembered_with_its_password(self):
        self.choose("coreone")
        self.folders(3, F.prusa)
        status, headers, _b = request("POST", "/welcome/4", {
            "action": "network", "ip": "printer.example",
            "key": "abcdEFGH"})
        self.assertEqual(status, 303)
        self.assertTrue(location(headers).startswith("/welcome/5"))
        self.assertIn("The printer answered: Core One", location(headers))
        self.assertEqual(self.asked, [("printer.example", "abcdEFGH")])
        config = config_store.load()
        self.assertEqual(config["lan_printer_ips"]["prusa"],
                         "printer.example")
        self.assertEqual(config["lan_printer_keys"]["prusa"], "abcdEFGH")

    def test_a_printer_that_does_not_answer_saves_nothing(self):
        self.choose("coreone")
        self.folders(3, F.prusa)
        _s, headers, _b = request("POST", "/welcome/4", {
            "action": "network", "ip": "printer.invalid", "key": "x"})
        self.assertTrue(location(headers).startswith("/welcome/4"))
        self.assertIn("did not answer", location(headers))
        self.assertNotIn("lan_printer_ips", config_store.load())

    def test_a_missing_password_is_asked_for(self):
        self.choose("coreone")
        self.folders(3, F.prusa)
        _s, headers, _b = request("POST", "/welcome/4", {
            "action": "network", "ip": "printer.example"})
        self.assertIn("PrusaLink password", location(headers))
        self.assertEqual(self.asked, [])

    def test_the_network_step_can_be_skipped(self):
        self.choose("coreone")
        _s, headers, _b = request("POST", "/welcome/4", {"action": "skip"})
        self.assertEqual(location(headers), "/welcome/5")


class TestCrealityWalkthrough(WizardCase):
    def test_k2_plus_steps_and_texts(self):
        self.assertEqual(wizard.steps_for("k2plus"),
                         ["install", "folders", "network", "project",
                          "finish"])
        self.choose("k2plus")
        _s, _h, body = request("GET", "/welcome/1")
        self.assertIn("Creality K2 Plus</strong> has been tested", body)
        self.assertIn("nothing has to be", body)
        self.assertIn("Device", body)
        self.folders(2, support.make_fake_creality(F.sandbox))
        _s, _h, body = request("GET", "/welcome/4")
        self.assertIn("only creating the project</strong>", body)
        self.assertIn("sending the print straight to the printer", body)
        self.assertIn("never needed to slice or to send", body)


class TestProjectPrinter(unittest.TestCase):
    def test_reads_the_printer_from_a_bambu_project(self):
        sandbox = support.make_sandbox()
        try:
            project = support.copy_test_project(sandbox)
            self.assertTrue(wizard.project_printer(project)
                            .startswith("Bambu Lab"))
            self.assertEqual(wizard.project_printer(sandbox / "none.3mf"),
                             "")
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    def test_sliced_results_are_not_projects(self):
        sandbox = support.make_sandbox()
        try:
            support.copy_test_project(sandbox)
            (sandbox / "plate.gcode.3mf").write_bytes(b"")
            names = [n for n, _p in wizard.saved_projects(sandbox)]
            self.assertEqual(names, ["blind-benchy.3mf"])
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
