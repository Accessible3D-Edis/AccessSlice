"""End-to-end tests: the real server, driven over HTTP like a browser.

One server instance runs for the whole module against a sandboxed
config, a sandboxed %APPDATA%, a fake Bambu Studio install, and a copy
of the committed test project. POST /start, which would LAUNCH the
slicer, is deliberately not exercised - it needs a real Studio and a
human, and is covered by Edis's hands-on checklist instead. POST
/project/send is exercised with the launch replaced by a recorder (the
2026-09-23 Device-page checks), so no program is ever started here.

Tests are numbered because they tell one story: open the project, read
every tab, stage changes, answer both Studio-parity questions, save,
verify on disk, close.
"""

import http.client
import json
import os
import shutil
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import support
import config_store
import diagnostics
import server
import webui
from slicer_project import ProjectSession


class ServerFixture:
    httpd = None
    thread = None
    port = None
    sandbox = None
    project = None
    old_config_path = None
    old_appdata = None


F = ServerFixture


def setUpModule():
    F.sandbox = support.make_sandbox()
    projects_dir = F.sandbox / "projects"
    projects_dir.mkdir()
    F.project = support.copy_test_project(projects_dir)
    fake_exe = support.make_fake_studio(F.sandbox)

    # Point AccessSlice's config INTO the sandbox.
    F.old_config_path = config_store.CONFIG_PATH
    config_store.CONFIG_PATH = F.sandbox / "config.json"
    config_store.save({
        "slicer": "bambu",
        "slicer_exe": str(fake_exe),
        "projects_dir": str(projects_dir),
        "known_printers": ["Bambu Lab H2C 0.4 nozzle"],
    })

    # Point %APPDATA% into the sandbox too, so studio_config reads a
    # fixture conf and can never see the real one.
    F.old_appdata = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(F.sandbox / "AppData")
    support.write_studio_conf(os.environ["APPDATA"], {
        "app": {}, "presets": {"machine": "Bambu Lab H2C 0.4 nozzle"},
    })

    server.state["session"] = None
    F.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    F.port = F.httpd.server_address[1]
    F.thread = threading.Thread(target=F.httpd.serve_forever, daemon=True)
    F.thread.start()


def tearDownModule():
    if F.httpd:
        F.httpd.shutdown()
        F.httpd.server_close()
    server.state["session"] = None
    server.state["watcher"].stop()
    config_store.CONFIG_PATH = F.old_config_path
    if F.old_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = F.old_appdata
    shutil.rmtree(F.sandbox, ignore_errors=True)


def request(method, url, form=None, headers=None):
    """One HTTP request; returns (status, headers dict, body text).

    Redirects are NOT followed - the Location header is part of what
    the tests assert on. headers: extra request headers (the
    request-origin tests send a browser's Host / Origin / Referer /
    Sec-Fetch-Site headers; a Host given here replaces the default).
    """
    conn = http.client.HTTPConnection("127.0.0.1", F.port, timeout=10)
    extra = dict(headers or {})
    skip_host = "Host" in extra
    try:
        if form is None:
            conn.putrequest(method, url, skip_host=skip_host)
            for k, v in extra.items():
                conn.putheader(k, v)
            conn.endheaders()
        else:
            body = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
            conn.putrequest(method, url, skip_host=skip_host)
            conn.putheader("Content-Type",
                           "application/x-www-form-urlencoded")
            conn.putheader("Content-Length", str(len(body)))
            for k, v in extra.items():
                conn.putheader(k, v)
            conn.endheaders(body)
        resp = conn.getresponse()
        return (resp.status, dict(resp.getheaders()),
                resp.read().decode("utf-8", errors="replace"))
    finally:
        conn.close()


def get(url):
    return request("GET", url)


def post(url, **form):
    # Form field names arrive as keyword arguments; the route itself is
    # the one positional parameter, named so no field can collide with it.
    return request("POST", url, form=form)


def location_of(headers):
    return urllib.parse.unquote_plus(headers.get("Location", ""))


class RequestOriginTests(unittest.TestCase):
    """The two doors a local server leaves open to the user's browser,
    both shut: DNS rebinding (a Host header naming another server) and
    cross-site form posts (Origin / Referer / Sec-Fetch-Site from
    another site). A refused request changes nothing and is logged."""

    def _own(self):
        return f"http://127.0.0.1:{F.port}"

    def test_a_foreign_host_header_is_refused_on_get(self):
        status, _h, body = request("GET", "/", headers={
            "Host": "accessslice.attacker.example"})
        self.assertEqual(status, 403)
        self.assertIn("Request refused", body)
        self.assertIn("own window", body)

    def test_localhost_with_the_right_port_is_accepted(self):
        status, _h, _b = request("GET", "/setup", headers={
            "Host": f"localhost:{F.port}"})
        self.assertEqual(status, 200)

    def test_the_right_name_with_another_port_is_refused(self):
        status, _h, _b = request("GET", "/setup", headers={
            "Host": f"127.0.0.1:{F.port + 1}"})
        self.assertEqual(status, 403)

    def test_a_cross_site_post_is_refused_and_changes_nothing(self):
        before = config_store.load()
        status, _h, body = request("POST", "/setup", form={
            "slicer": "bambu", "slicer_exe": r"C:\Windows\notepad.exe",
            "projects_dir": before["projects_dir"]},
            headers={"Origin": "https://evil.example",
                     "Referer": "https://evil.example/page.html",
                     "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertIn("Request refused", body)
        self.assertEqual(config_store.load()["slicer_exe"],
                         before["slicer_exe"])
        self.assertIn("Refused a request", diagnostics.read_recent())

    def test_a_foreign_origin_alone_is_enough_to_refuse(self):
        status, _h, _b = request("POST", "/project/close", form={},
                                 headers={"Origin": "http://localhost:9999"})
        self.assertEqual(status, 403)

    def test_a_null_origin_is_refused(self):
        status, _h, _b = request("POST", "/project/close", form={},
                                 headers={"Origin": "null"})
        self.assertEqual(status, 403)

    def test_a_foreign_referer_without_origin_is_refused(self):
        status, _h, _b = request("POST", "/project/close", form={},
                                 headers={"Referer": "http://127.0.0.1.evil.example/x"})
        self.assertEqual(status, 403)

    def test_a_cross_site_fetch_metadata_alone_is_refused(self):
        status, _h, _b = request("POST", "/project/close", form={},
                                 headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_the_apps_own_page_posts_normally(self):
        # Exactly the headers WebView2 / a browser sends for a form on
        # the app's own page.
        status, headers, _b = request("POST", "/project/close", form={},
                                      headers={
            "Origin": self._own(), "Referer": self._own() + "/project/quality",
            "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 303)
        self.assertTrue(headers.get("Location", "").startswith("/"))

    def test_a_typed_address_post_is_accepted(self):
        # Sec-Fetch-Site "none" = user-initiated, no other page involved.
        status, _h, _b = request("POST", "/project/close", form={},
                                 headers={"Sec-Fetch-Site": "none",
                                          "Origin": self._own()})
        self.assertEqual(status, 303)


class EndToEndTests(unittest.TestCase):

    # ------------------------------------------------ before a project

    def test_01_home_renders(self):
        status, _headers, body = get("/")
        self.assertEqual(status, 200)
        self.assertIn("AccessSlice", body)

    def test_02_project_pages_redirect_home_without_a_session(self):
        status, headers, _body = get("/project/quality")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers).startswith("/?msg="))

    def test_03_files_page_lists_the_project(self):
        status, _headers, body = get("/files")
        self.assertEqual(status, 200)
        self.assertIn(F.project.name, body)

    def test_04_watch_api_reports_nothing_found(self):
        status, _headers, body = get("/api/watch")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["found"], False)

    def test_05_static_css_is_served(self):
        status, headers, body = get("/static/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", ""))
        self.assertTrue(body.strip())

    def test_06_static_path_traversal_is_blocked(self):
        for attempt in ("/static/../server.py", "/static/..%2Fserver.py"):
            status, _headers, body = get(attempt)
            self.assertNotEqual(status, 200,
                                f"{attempt} must never serve a file")
            self.assertNotIn("ThreadingHTTPServer", body)

    def test_07_start_page_renders_with_known_printers(self):
        status, _headers, body = get("/start")
        self.assertEqual(status, 200)
        self.assertIn("Bambu Lab H2C 0.4 nozzle", body)

    def test_08_the_honest_limits_page_renders(self):
        status, _headers, body = get("/limits")
        self.assertEqual(status, 200)
        self.assertIn("cannot edit", body)
        self.assertIn("painting", body.lower())

    # ------------------------------------------------- open and read

    def test_10_open_project_redirects_into_the_editor(self):
        status, headers, _body = post("/open", path=str(F.project))
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/quality"))
        self.assertIn("Opened", location)
        self.assertNotIn("Project note", location,
                         "the clean test project must open without "
                         "structure warnings")

    def test_11_every_editor_page_renders(self):
        expectations = {
            "quality": "Layer height",
            "strength": "Wall count",
            "support": "Enable supports",
            "speed": "Outer wall speed",
            "adhesion": "Brim",
            "others": "Fuzzy skin",
            "filaments": "Nozzle temperature",
            "presets": "Bambu Lab H2C 0.4 nozzle",
            "placement": "blind-Benchy.stl",
            "all": "printer_settings_id",
            "review": "",
            "backups": "No backups yet",
            "send": "",
        }
        for tab, marker in expectations.items():
            status, _headers, body = get(f"/project/{tab}")
            self.assertEqual(status, 200, f"/project/{tab} failed")
            if marker:
                self.assertIn(marker, body, f"/project/{tab} lacks marker")
            if tab != "send":   # send is a flow page, not a settings tab
                self.assertIn('id="tab-heading"', body,
                              f"/project/{tab} lacks the jump heading")

    def test_12_all_settings_search_finds_by_plain_words(self):
        status, _headers, body = get(
            "/project/all?q=" + urllib.parse.quote("branch diameter"))
        self.assertEqual(status, 200)
        self.assertIn("tree_support_branch_diameter", body)

    def test_13_single_setting_page_renders(self):
        status, _headers, body = get("/project/setting/wall_loops")
        self.assertEqual(status, 200)
        self.assertIn("Wall count", body)

    def test_14_unknown_setting_page_redirects_to_all(self):
        status, headers, _body = get("/project/setting/no_such_key")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers).startswith("/project/all"))

    def test_15_open_found_with_a_session_lands_in_the_editor(self):
        # The repeat-safe handoff: a stray /open-found after the project
        # is already open must land in the editor, not bounce to /waiting.
        status, headers, _body = get("/open-found")
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers), "/project/quality")

    # ------------------------------------------------------- staging

    def test_20_stage_a_setting_from_a_curated_tab(self):
        status, headers, _body = post("/project/strength", wall_loops="3")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/strength"))
        self.assertIn("1 change(s) staged", location)
        _s, _h, review = get("/project/review")
        self.assertIn("wall_loops", review)

    def test_21_posting_the_saved_value_back_clears_the_staged_edit(self):
        # Typing the saved value again means "never mind": nothing is
        # staged AND the stale staged edit from test_20 is dropped.
        status, headers, _body = post("/project/strength", wall_loops="2")
        self.assertEqual(status, 303)
        self.assertIn("Nothing changed", location_of(headers))
        session = server.state["session"]
        self.assertEqual([c for c in session.pending
                          if c["kind"] == "setting"
                          and c["key"] == "wall_loops"], [])

    def test_21b_stage_a_setting_from_the_others_tab(self):
        status, headers, _body = post("/project/others",
                                      fuzzy_skin="external")
        self.assertEqual(status, 303)
        self.assertIn("1 change(s) staged", location_of(headers))
        _s, _h, review = get("/project/review")
        self.assertIn("fuzzy_skin", review)
        # Posting the saved value back clears it again.
        status, headers, _body = post("/project/others", fuzzy_skin="none")
        self.assertIn("Nothing changed", location_of(headers))

    def test_22_stage_a_per_slot_list_value(self):
        session = server.state["session"]
        current = list(session.settings["nozzle_temperature"])
        form = {f"nozzle_temperature__{i}": v
                for i, v in enumerate(current)}
        form["nozzle_temperature__3"] = "243"
        status, headers, _body = post(
            "/project/setting/nozzle_temperature", **form)
        self.assertEqual(status, 303)
        staged = [c for c in session.pending
                  if c["kind"] == "setting"
                  and c["key"] == "nozzle_temperature"]
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0]["new"][3], "243")

    def test_23_placement_move_and_centre(self):
        status, headers, _body = post("/project/placement",
                                      x__2="100", y__2="120")
        self.assertEqual(status, 303)
        self.assertIn("staged", location_of(headers))
        session = server.state["session"]
        moves = [c for c in session.pending if c["kind"] == "move"]
        self.assertEqual(moves[0]["new"], (100.0, 120.0))

    def test_24_off_plate_position_warns(self):
        status, headers, _body = post("/project/placement",
                                      x__2="400", y__2="120")
        self.assertEqual(status, 303)
        self.assertIn("outside", location_of(headers))
        # Put it back somewhere sensible for the save test.
        post("/project/placement", x__2="100", y__2="120")

    def test_25_non_numeric_position_is_a_friendly_error(self):
        status, headers, _body = post("/project/placement",
                                      x__2="left a bit", y__2="120")
        self.assertEqual(status, 303)
        self.assertIn("Problem", location_of(headers))

    def test_25b_rotation_and_size_fields_on_the_placement_tab(self):
        status, _headers, body = get("/project/placement")
        self.assertEqual(status, 200)
        self.assertIn("Rotate by degrees", body)
        self.assertIn("Size in percent of the imported model", body)
        self.assertIn("Currently rotated 0.0 degrees", body)
        # Stage a rotation and a size change together.
        status, headers, _body = post(
            "/project/placement", x__2="165", y__2="160",
            rot__2="45", scale__2="150", basescale__2="100.0")
        self.assertEqual(status, 303)
        self.assertIn("2 change(s) staged", location_of(headers))
        session = server.state["session"]
        kinds = {c["kind"] for c in session.pending}
        self.assertIn("rotate", kinds)
        self.assertIn("scale", kinds)
        # An untouched size field (matches its baseline) plus rotation
        # 0 stages nothing further.
        status, headers, _body = post(
            "/project/placement", x__2="165", y__2="160",
            rot__2="0", scale__2="100.0", basescale__2="100.0")
        self.assertIn("Nothing changed", location_of(headers))
        # A nonsense rotation is a friendly problem, not a crash.
        status, headers, _body = post("/project/placement",
                                      x__2="165", y__2="160",
                                      rot__2="a bit")
        self.assertIn("Problem", location_of(headers))
        # Clean up the staged transforms for the tests that follow.
        session.pending.clear()

    def test_26_object_settings_page_renders_and_guards_bad_ids(self):
        status, _headers, body = get("/project/object/2")
        self.assertEqual(status, 200)
        self.assertIn("Object settings", body)
        self.assertIn("blind-Benchy.stl", body)
        self.assertIn("Infill density", body)
        self.assertIn("No overrides yet", body)
        status, headers, _body = get("/project/object/99")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers).startswith(
            "/project/placement"))

    def test_27_object_override_staging_with_companions_and_removal(self):
        # Changing the object's infill density stages the two companion
        # keys too, exactly as Studio's GUI writes them: 3 changes.
        status, headers, _body = post(
            "/project/object/2",
            obj__sparse_infill_density="33%",
            base__obj__sparse_infill_density="15%")
        self.assertEqual(status, 303)
        self.assertIn("3 change(s) staged", location_of(headers))
        session = server.state["session"]
        staged = {(c["key"]): c["new"] for c in session.pending
                  if c["kind"] == "object_setting"}
        self.assertEqual(staged, {"sparse_infill_density": "33%",
                                  "skeleton_infill_density": "33%",
                                  "skin_infill_density": "33%"})
        # An unchanged field (value equals its baseline) stages nothing.
        status, headers, _body = post("/project/object/2",
                                      obj__wall_loops="2",
                                      base__obj__wall_loops="2")
        self.assertIn("Nothing changed", location_of(headers))
        # Removal drops the override and its companions.
        status, headers, _body = post("/project/object/2",
                                      remove="sparse_infill_density")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertIn("Removal", location)
        self.assertIn("companion", location)
        remaining = [c for c in session.pending
                     if c["kind"] == "object_setting"]
        self.assertEqual(remaining, [],
                         "staged-only overrides vanish on removal")

    def test_28_object_override_survives_a_save(self):
        session = server.state["session"]
        session.pending.clear()
        post("/project/object/2", obj__wall_loops="6",
             base__obj__wall_loops="2")
        status, headers, _body = post("/project/save",
                                      return_to="/project/object/2")
        self.assertEqual(status, 303)
        self.assertIn("Saved and verified", location_of(headers))
        self.assertEqual(
            ProjectSession(F.project).object_overrides("2"),
            {"wall_loops": "6"})
        # The placement tab now mentions the override count.
        _s, _h, body = get("/project/placement")
        self.assertIn("(1 override set)", body)

    # ------------------------------------- the Studio-parity questions

    def test_29_flushing_page_renders_the_grid(self):
        status, _headers, body = get("/project/flushing")
        self.assertEqual(status, 200)
        self.assertIn("From filament 1 to filament 2", body)
        self.assertIn("Nozzle 1 (left)", body)      # H2C: two extruders
        self.assertIn("Nozzle 2 (right)", body)
        self.assertIn("Flush multiplier", body)
        self.assertIn("Prime tower", body)
        # The Filaments tab links here.
        _s, _h, filaments = get("/project/filaments")
        self.assertIn("/project/flushing", filaments)

    def test_29b_flushing_edits_stage_and_clear(self):
        session = server.state["session"]
        # Ground truth in the committed project: matrix cell from
        # filament 1 to 2 on nozzle 1 is 400; flush_into_infill is 0,
        # flush_into_support is 1; prime tower on, width 60.
        status, headers, _body = post(
            "/project/flushing",
            flush__0_0_1="450",
            __present=["flush_into_infill", "flush_into_objects",
                       "flush_into_support", "enable_prime_tower"],
            flush_into_infill="1", flush_into_support="1",
            enable_prime_tower="1",
            prime_tower_width="65")
        self.assertEqual(status, 303)
        self.assertIn("3 change(s) staged", location_of(headers))
        staged = {c["key"]: c for c in session.pending
                  if c["kind"] == "setting"}
        self.assertEqual(staged["flush_volumes_matrix"]["new"][1], "450")
        self.assertEqual(staged["flush_into_infill"]["new"], "1")
        self.assertEqual(staged["prime_tower_width"]["new"], "65")
        # Only the edited cell changed; the rest of the matrix is intact.
        self.assertEqual(staged["flush_volumes_matrix"]["new"][2],
                         session.settings["flush_volumes_matrix"][2])
        # Posting the saved values back clears everything again.
        status, headers, _body = post(
            "/project/flushing",
            flush__0_0_1="400",
            __present=["flush_into_infill", "flush_into_objects",
                       "flush_into_support", "enable_prime_tower"],
            flush_into_support="1", enable_prime_tower="1",
            prime_tower_width="60")
        self.assertIn("Nothing changed", location_of(headers))
        self.assertEqual([c for c in session.pending
                          if c["kind"] == "setting"], [])

    def test_29c_non_numeric_flush_volume_is_a_friendly_error(self):
        status, headers, _body = post("/project/flushing",
                                      flush__0_0_1="lots")
        self.assertEqual(status, 303)
        self.assertIn("Problem:", location_of(headers))

    def test_29_the_object_chooser_shows_the_staged_slot(self):
        # Edis, 2026-09-21: he moved the object from the PLA slot to
        # the PETG slot and the page kept showing PLA - the chooser
        # read the file, not the staged choice.
        status, headers, _b = post("/project/filaments", ext__2="4")
        self.assertEqual(status, 303)
        self.assertIn("change(s) staged", location_of(headers))
        _s, _h, body = get("/project/filaments")
        self.assertIn('<option value="4" selected>Slot 4 - ', body)
        self.assertNotIn('<option value="2" selected>', body)
        # The presets tab carries the same chooser.
        _s, _h, body = get("/project/presets")
        self.assertIn('<option value="4" selected>Slot 4 - ', body)
        # Choosing the file's slot again withdraws the change.
        post("/project/filaments", ext__2="2")
        _s, _h, body = get("/project/filaments")
        self.assertIn('<option value="2" selected>Slot 2 - ', body)
        session = server.state["session"]
        self.assertFalse([c for c in session.pending
                          if c["kind"] == "extruder"])
        # The bare post also staged the nozzle map (Manual mode is
        # forced on every map post); the later tests count pending
        # changes, so take that one back out.
        session.pending = [c for c in session.pending
                           if c["kind"] != "filament_map"]

    def test_30_infill_question_flow(self):
        status, headers, _body = post("/project/strength",
                                      sparse_infill_density="100%")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/infill-question"),
                        f"expected the infill question, got {location}")
        # The status line on a question page carries the count only -
        # the question is the page (Edis, 2026-09-21: the warning was
        # heard "on both buttons").
        self.assertIn("1 change(s) staged", location)
        self.assertNotIn("support", location)
        status, _headers, body = get("/project/infill-question"
                                     "?return_to=/project/strength")
        self.assertEqual(status, 200)
        self.assertIn("doesn't support 100% density", body)
        # Yes is the page's own first control (autofocus), so app.js
        # leaves focus there and does not re-announce over it.
        self.assertIn('value="yes" autofocus', body)
        status, headers, _body = post("/project/infill-answer",
                                      answer="yes",
                                      return_to="/project/strength")
        self.assertEqual(status, 303)
        session = server.state["session"]
        staged = {c["key"]: c["new"] for c in session.pending
                  if c["kind"] == "setting"}
        self.assertEqual(staged.get("sparse_infill_pattern"), "zig-zag")
        # Answered: the conflict is gone.
        self.assertIsNone(session.infill_conflict())

    def test_31_unit_question_flow(self):
        status, headers, _body = post("/project/quality",
                                      max_travel_detour_distance="10000")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/unit-question"),
                        f"expected the unit question, got {location}")
        status, _headers, body = get(
            "/project/unit-question?key=max_travel_detour_distance"
            "&return_to=/project/quality")
        self.assertEqual(status, 200)
        status, headers, _body = post("/project/unit-answer",
                                      key="max_travel_detour_distance",
                                      answer="yes",
                                      return_to="/project/quality")
        self.assertEqual(status, 303)
        self.assertIn("10000%", location_of(headers))
        session = server.state["session"]
        staged = {c["key"]: c["new"] for c in session.pending
                  if c["kind"] == "setting"}
        self.assertEqual(staged.get("max_travel_detour_distance"),
                         "10000%")

    def test_32_discard_a_staged_change(self):
        session = server.state["session"]
        count = len(session.pending)
        self.assertGreater(count, 0)
        status, headers, _body = post("/project/discard", index="0")
        self.assertEqual(status, 303)
        self.assertEqual(len(session.pending), count - 1)

    # The three discard improvements Edis asked for on 2026-08-31:
    # a single discard returns to the page the change came from, and
    # several changes can be dropped ticked-only or all at once.

    def test_33_discard_lands_back_on_the_source_tab(self):
        session = server.state["session"]
        session.pending.clear()
        post("/project/strength", wall_loops="7")
        self.assertEqual(len(session.pending), 1)
        status, headers, _body = post("/project/discard", index="0")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/strength"),
                        "discard must land back where the change "
                        "was staged, not stay on Review")
        self.assertIn("Strength", location)
        self.assertEqual(len(session.pending), 0)

    def test_34_discard_only_the_ticked_changes(self):
        session = server.state["session"]
        session.pending.clear()
        post("/project/strength", wall_loops="7")
        post("/project/setting/sparse_infill_density",
             sparse_infill_density="25%")
        post("/project/setting/sparse_infill_pattern",
             sparse_infill_pattern="gyroid")
        self.assertEqual(len(session.pending), 3)
        _s, _h, body = get("/project/review")
        self.assertIn("Discard the ticked changes", body)
        self.assertIn("Discard all 3 staged changes", body)
        status, headers, _body = post("/project/discard",
                                      selected="1", drop=["0", "2"])
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/review"))
        self.assertEqual([c["key"] for c in session.pending],
                         ["sparse_infill_density"],
                         "exactly the ticked changes must go")

    def test_35_discard_all_staged_changes_at_once(self):
        session = server.state["session"]
        self.assertGreater(len(session.pending), 0)
        post("/project/strength", wall_loops="7")
        status, headers, _body = post("/project/discard", all="1")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/review"))
        self.assertIn("Nothing is waiting now", location)
        self.assertEqual(session.pending, [])
        # With one change staged the multi controls stay out of the way.
        post("/project/strength", wall_loops="7")
        _s, _h, body = get("/project/review")
        self.assertNotIn("Discard the ticked changes", body)
        post("/project/discard", index="0")

    def test_36_handoff_warns_about_the_stale_second_window(self):
        # Check 6's answer (2026-08-31): a send while Studio is open
        # opens a SECOND window; the old one shows a stale version.
        _s, _h, body = get("/handoff?second=1")
        self.assertIn("already open", body)
        self.assertIn("close the old one", body)
        _s, _h, body = get("/handoff")
        self.assertNotIn("already open", body,
                         "a first send must not warn about a second "
                         "window")

    def test_37_limits_page_names_the_openscad_colour_route(self):
        # Edis's own painting-free multi-colour workflow (2026-08-31).
        _s, _h, body = get("/limits")
        self.assertIn("OpenSCAD", body)
        self.assertIn("color()", body)

    def test_38_long_tokens_wrap_instead_of_sideways_scrolling(self):
        # The 400%-zoom finding (2026-08-31): raw setting keys and file
        # paths must be allowed to break anywhere.
        _s, _h, css = get("/static/style.css")
        self.assertIn("overflow-wrap: anywhere", css)

    # ------------------------------------------------ save and close

    def test_40_save_writes_verifies_and_returns_to_the_tab(self):
        # Reset to one precise change so the on-disk assertion is exact.
        session = server.state["session"]
        session.pending.clear()
        post("/project/strength", wall_loops="3")
        status, headers, _body = post("/project/save",
                                      return_to="/project/strength")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/strength"),
                        "save must return to the page it was pressed on")
        self.assertIn("Saved and verified", location)

        fresh = ProjectSession(F.project)
        self.assertEqual(fresh.settings["wall_loops"], "3")
        diff = fresh.settings["different_settings_to_system"]
        self.assertIn("wall_loops", diff[0].split(";"))
        backups = list((F.project.parent / "backups").glob(
            "* - before save *.3mf"))
        self.assertTrue(backups, "the save must leave a backup")

    def test_41_backup_files_never_appear_in_the_files_list(self):
        status, _headers, body = get("/files")
        self.assertEqual(status, 200)
        self.assertNotIn(".pre-edit-backup.3mf", body)

    def test_42_backups_page_lists_and_restores_the_previous_version(self):
        status, _headers, body = get("/project/backups")
        self.assertEqual(status, 200)
        self.assertIn(" - before save ", body)
        self.assertIn("Restore this version", body)
        name = server.state["session"].list_backups()[0]["name"]
        status, headers, _body = post("/project/backups", backup=name)
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/backups"))
        self.assertIn("Restored the version", location)
        self.assertEqual(ProjectSession(F.project).settings["wall_loops"],
                         "2", "the file on disk is the restored version")

    def test_43_restoring_a_made_up_backup_is_a_friendly_problem(self):
        status, headers, _body = post("/project/backups",
                                      backup="not-a-real-backup.3mf")
        self.assertEqual(status, 303)
        self.assertIn("Problem", location_of(headers))

    # ------------------------------------- in-app slicing (Phase II)

    def _patch_engine(self, behaviour):
        """Route engine_driver at the fake engine for one test."""
        import sys as _sys
        import engine_driver
        script = support.make_fake_engine(F.sandbox)
        orig = engine_driver.build_command
        engine_driver.build_command = (
            lambda exe, project, plate, out:
            [_sys.executable, str(script), out, behaviour])
        self.addCleanup(setattr, engine_driver, "build_command", orig)

    def test_44_send_page_offers_slice_now(self):
        session = server.state["session"]
        session.pending.clear()
        status, _headers, body = get("/project/send")
        self.assertEqual(status, 200)
        self.assertIn("Slice and send", body)
        self.assertIn("Slice now", body)
        self.assertIn("Open in Bambu Studio", body)

    def test_45_slice_refuses_with_unsaved_changes(self):
        post("/project/strength", wall_loops="9")
        status, headers, _body = post("/project/slice")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers).startswith("/project/send"))
        _s, _h, body = get("/project/send")
        self.assertIn("Save before slicing", body)
        server.state["session"].pending.clear()

    def test_46_slice_flow_end_to_end(self):
        self._patch_engine("success")
        status, headers, _body = post("/project/slice")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers)
                        .startswith("/project/slicing"))
        job = server.state["slice_job"]
        self.assertIsNotNone(job)
        job.wait(30)
        self.assertEqual(job.status, "done")

        _s, _h, api = get("/api/slice")
        data = json.loads(api)
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["next"], "/project/slice-report")

        # The progress page now forwards to the report.
        status, headers, _body = get("/project/slicing")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers)
                        .startswith("/project/slice-report"))

        status, _headers, body = get("/project/slice-report")
        self.assertEqual(status, 200)
        self.assertIn("Sliced and ready to print", body)
        self.assertIn("2 hours 55 minutes", body)
        self.assertIn("37.9 g", body)
        self.assertIn("Outer wall", body, "feature breakdown present")
        self.assertIn("Open the sliced file", body)
        out = job.output_file()
        self.assertTrue(out and out.is_file())
        self.assertEqual(out.parent.parent,
                         F.project.parent / "sliced")

    def test_47_slice_failure_is_plain_words(self):
        self._patch_engine("fail")
        post("/project/slice")
        job = server.state["slice_job"]
        job.wait(30)
        status, _headers, body = get("/project/slice-report")
        self.assertEqual(status, 200)
        self.assertIn("Slicing failed", body)
        self.assertIn("boundary of plate", body,
                      "the engine's own message is quoted")
        self.assertIn("untouched", body)
        self.assertIn("/diagnostics", body)

    def test_48_slice_cancel(self):
        self._patch_engine("hang")
        post("/project/slice")
        job = server.state["slice_job"]
        self.assertEqual(job.status, "running")
        _s, _h, progress = get("/project/slicing")
        self.assertIn("Cancel slicing", progress)
        status, headers, _body = post("/project/slice-cancel")
        self.assertEqual(status, 303)
        self.assertIn("Slicing cancelled", location_of(headers))
        self.assertEqual(job.status, "cancelled")
        _s, _h, body = get("/project/slice-report")
        self.assertIn("Slicing cancelled", body)
        server.state["slice_job"] = None

    def test_49_send_sliced_without_a_file_is_refused(self):
        status, headers, _body = post("/project/send-sliced")
        self.assertEqual(status, 303)
        self.assertIn("no sliced file", location_of(headers))

    # ---------------------------------------- diagnostics + safety net

    def test_50_diagnostics_page_shows_the_sessions_activity(self):
        status, _headers, body = get("/diagnostics")
        self.assertEqual(status, 200)
        self.assertIn("Opened blind-benchy.3mf", body)
        self.assertIn("Saved blind-benchy.3mf", body)

    def test_51_unexpected_errors_get_a_plain_words_page_and_a_log_entry(self):
        real = webui.render_files

        def broken(*args, **kwargs):
            raise RuntimeError("simulated bug for the test")

        webui.render_files = broken
        try:
            status, _headers, body = get("/files")
        finally:
            webui.render_files = real
        self.assertEqual(status, 500)
        self.assertIn("Unexpected problem", body)
        self.assertIn("was not changed", body)
        self.assertIn("/diagnostics", body)
        self.assertNotIn("RuntimeError", body,
                         "raw tracebacks belong in the log, not on "
                         "screen")
        _s, _h, log_page = get("/diagnostics")
        self.assertIn("UNEXPECTED ERROR", log_page)
        self.assertIn("simulated bug for the test", log_page)

    def test_52_the_log_can_be_emptied(self):
        status, headers, _body = post("/diagnostics/clear")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers).startswith("/diagnostics"))
        status, _headers, body = get("/diagnostics")
        self.assertEqual(status, 200)
        self.assertIn("The log is empty", body)

    # ------------------------------------------------ stale-file guard

    def test_60_an_outside_change_raises_the_warning_banner(self):
        # Simulate Bambu Studio saving the open project behind our back.
        import json as _json
        import edit_bambu_3mf as engine
        entries = engine.load_entries(F.project)
        settings = _json.loads(entries[engine.PROJECT_SETTINGS_ENTRY])
        settings["wall_loops"] = "7"
        entries[engine.PROJECT_SETTINGS_ENTRY] = _json.dumps(
            settings).encode("utf-8")
        engine.save_entries(F.project, entries)

        status, _headers, body = get("/project/quality")
        self.assertEqual(status, 200)
        self.assertIn("changed outside AccessSlice", body)
        self.assertIn("Reload the file from disk", body)

    def test_61_saving_refuses_while_stale(self):
        status, headers, _body = post("/project/strength", wall_loops="5")
        self.assertEqual(status, 303, "staging itself stays possible")
        status, headers, _body = post("/project/save",
                                      return_to="/project/strength")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertIn("Problem", location)
        self.assertIn("changed on disk", location)
        self.assertEqual(ProjectSession(F.project).settings["wall_loops"],
                         "7", "the outside save must survive untouched")

    def test_62_reload_picks_up_the_newer_version(self):
        status, headers, _body = post("/project/reload",
                                      return_to="/project/strength")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/strength"))
        self.assertIn("Reloaded", location)
        self.assertIn("discarded", location)
        status, _headers, body = get("/project/strength")
        self.assertEqual(status, 200)
        self.assertNotIn("changed outside AccessSlice", body,
                         "the warning must clear after the reload")
        self.assertIn('value="7"', body,
                      "the tab must show the reloaded value")

    # --------------------------------------- new job from a model file

    def test_70_start_page_offers_the_model_file_path(self):
        _s, _h, body = get("/start")
        self.assertIn("Start from a model file", body)
        self.assertIn("model_path", body)
        self.assertIn("Or create the project in Bambu Studio", body)

    def test_71_new_job_without_a_donor_is_honest(self):
        model = F.sandbox / "part.stl"
        model.write_bytes(b"solid part")
        status, headers, _body = post(
            "/new-job", printer="Bambu Lab X1 Carbon 0.4 nozzle",
            model_path=str(model))
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertIn("No project saved for", location)
        self.assertIn("once", location)

    def test_72_new_job_rejects_a_non_model_file(self):
        bad = F.sandbox / "notes.txt"
        bad.write_text("hello")
        _status, headers, _body = post(
            "/new-job", printer="Bambu Lab H2C 0.4 nozzle",
            model_path=str(bad))
        self.assertIn("not a model file", location_of(headers))

    def test_73_new_job_creates_and_opens_the_project(self):
        import newjob
        merged = support.copy_test_project(F.sandbox,
                                           name="merged-fixture.3mf")
        support.add_duplicate_object(merged)
        script = support.make_fake_merge(F.sandbox)
        import sys as _sys
        orig = newjob.build_merge_command
        newjob.build_merge_command = (
            lambda exe, donor, models, out:
            [_sys.executable, str(script), str(merged), out, "success"])
        self.addCleanup(setattr, newjob, "build_merge_command", orig)

        model = F.sandbox / "part.stl"
        model.write_bytes(b"solid part")
        status, headers, _body = post(
            "/new-job", printer="Bambu Lab H2C 0.4 nozzle",
            model_path=str(model))
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers), "/building",
                         "the build runs in the background")
        server.state["build_job"].wait(60)
        _s, _h, api = get("/api/build")
        self.assertIn('"status": "done"', api)
        status, headers, _body = get("/building")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/quality"))
        self.assertIn("Created part.3mf", location)
        self.assertIn("Opened part.3mf", location)
        session = server.state["session"]
        self.assertEqual(session.path.name, "part.3mf")
        self.assertEqual([o["name"] for o in session.objects()],
                         ["second-model.stl"])
        self.assertEqual(session.settings.get("printer_settings_id"),
                         "Bambu Lab H2C 0.4 nozzle")
        self.assertIsNone(server.state["build_job"])
        _s, headers, _b = get("/building")
        self.assertEqual(location_of(headers), "/project/quality",
                         "a repeat of the last poll lands in the project")

    def _slow_new_job(self, name):
        # An engine that is still busy for as long as the test needs.
        import newjob
        import sys as _sys
        orig = newjob.build_merge_command
        newjob.build_merge_command = (
            lambda exe, donor, models, out:
            [_sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(setattr, newjob, "build_merge_command", orig)
        model = F.sandbox / name
        model.write_bytes(b"solid part")
        status, headers, _body = post(
            "/new-job", printer="Bambu Lab H2C 0.4 nozzle",
            model_path=str(model))
        self.assertEqual(location_of(headers), "/building")
        return model

    def test_74_the_building_page_speaks_and_cancels(self):
        # Built 2026-09-26: the Skyward Dragon kept the Start page
        # loading for eight silent minutes.
        open_before = server.state["session"]
        model = self._slow_new_job("dragon.stl")
        status, _h, body = get("/building")
        self.assertEqual(status, 200)
        self.assertIn('id="build-line" aria-live="polite"', body)
        self.assertIn("dragon.stl", body)
        self.assertIn("Cancel building", body)
        import time as _time
        for _ in range(100):  # the donor is read before the engine starts
            _s, _h, api = get("/api/build")
            if "reading the model" in api:
                break
            _time.sleep(0.1)
        self.assertIn('"status": "running"', api)
        self.assertIn("reading the model", api)

        _s, headers, _b = post("/new-job", printer="Bambu Lab H2C 0.4 "
                               "nozzle", model_path=str(model))
        self.assertIn("one build at a time", location_of(headers))

        status, headers, _b = post("/building/cancel")
        self.assertEqual(location_of(headers), "/building")
        _s, headers, _b = get("/building")
        location = location_of(headers)
        self.assertTrue(location.startswith("/start"))
        self.assertIn("was cancelled. Nothing was kept", location)
        self.assertFalse((F.sandbox / "projects" / "dragon.3mf")
                         .exists())
        self.assertIs(server.state["session"], open_before,
                      "the open project is untouched")

    def test_75_a_failed_build_says_why_on_the_start_page(self):
        import newjob
        import sys as _sys
        orig = newjob.build_merge_command
        newjob.build_merge_command = (
            lambda exe, donor, models, out: [_sys.executable, "-c", "pass"])
        self.addCleanup(setattr, newjob, "build_merge_command", orig)
        model = F.sandbox / "broken.stl"
        model.write_bytes(b"solid part")
        post("/new-job", printer="Bambu Lab H2C 0.4 nozzle",
             model_path=str(model))
        server.state["build_job"].wait(60)
        _s, headers, _b = get("/building")
        location = location_of(headers)
        self.assertTrue(location.startswith("/start"))
        self.assertIn("Problem:", location)
        self.assertIn("without writing its result", location)

    # ---- the Device page follows a send (Edis's 2026-09-23 print:
    # an H2C send while Studio watched an X1 Carbon print kept the X1
    # Carbon in the send window). The launch is replaced by a recorder
    # and the running check by a switch; the conf is the sandbox's.

    SERIALS = {"Bambu Lab H2C": "SER-H2C-001",
               "Bambu Lab X1 Carbon": "SER-X1C-002"}

    def _device_setup(self, running, selected="SER-X1C-002"):
        import studio_config
        status, _h, _b = post("/open", path=str(F.project))
        self.assertEqual(status, 303)
        config = config_store.load()
        old_serials = config.get("device_serials")
        config["device_serials"] = dict(self.SERIALS)
        config_store.save(config)

        def restore_serials():
            cfg = config_store.load()
            if old_serials is None:
                cfg.pop("device_serials", None)
            else:
                cfg["device_serials"] = old_serials
            config_store.save(cfg)
        self.addCleanup(restore_serials)

        app = {"user_last_selected_machine": selected} if selected else {}
        support.write_studio_conf(os.environ["APPDATA"], {
            "app": app,
            "presets": {"machine": "Bambu Lab X1 Carbon 0.4 nozzle",
                        "process": "0.20mm Standard @BBL X1C",
                        "filaments": ["Bambu PLA Matte @BBL X1C"]},
        })
        self.addCleanup(support.write_studio_conf, os.environ["APPDATA"], {
            "app": {}, "presets": {"machine": "Bambu Lab H2C 0.4 nozzle"},
        })

        launched = []
        old_launch = server.launch_slicer
        server.launch_slicer = (
            lambda config, file_path=None: launched.append(file_path))
        self.addCleanup(setattr, server, "launch_slicer", old_launch)
        old_running = studio_config.studio_running
        studio_config.studio_running = lambda exe: running
        self.addCleanup(setattr, studio_config, "studio_running",
                        old_running)
        return launched

    def _studio_device(self):
        import studio_config
        return studio_config.read_conf()["app"].get(
            "user_last_selected_machine", "")

    def test_74_send_while_studio_is_open_on_another_printer_asks(self):
        launched = self._device_setup(running=True)
        status, headers, _b = post("/project/send")
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers), "/project/send-device")
        self.assertEqual(launched, [], "nothing opens before the answer")
        status, _h, body = get("/project/send-device")
        self.assertEqual(status, 200)
        self.assertIn("This project is for the Bambu Lab H2C", body)
        self.assertIn("Device page is on the Bambu Lab X1 Carbon", body)
        self.assertIn("carries on", body)  # the X1C print is safe
        self.assertIn("Alt+F4", body)
        self.assertIn('name="device" value="switch"', body)
        self.assertIn('name="device" value="keep"', body)
        self.assertLess(body.index('value="switch"'),
                        body.index('value="keep"'),
                        "close-and-switch is the first, focused button")
        self.assertIn("autofocus", body)
        self.assertIn('href="/project/send"', body)
        # "Studio is closed now" while it still runs: back to the
        # question with a status line, still nothing opened.
        status, headers, _b = post("/project/send", device="switch")
        self.assertEqual(status, 303)
        location = location_of(headers)
        self.assertTrue(location.startswith("/project/send-device?msg="))
        self.assertIn("still running", location)
        self.assertEqual(launched, [])
        self.assertEqual(self._studio_device(), "SER-X1C-002")

    def test_75_open_anyway_keeps_the_device_and_warns_on_handoff(self):
        launched = self._device_setup(running=True)
        status, headers, _b = post("/project/send", device="keep")
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers),
                         "/handoff?second=1&device=kept")
        self.assertEqual(launched, [F.project])
        self.assertEqual(self._studio_device(), "SER-X1C-002",
                         "an open Studio's conf is never edited")
        _s, _h, body = get("/handoff?second=1&device=kept")
        self.assertIn("still shows the Bambu Lab X1 Carbon", body)
        self.assertIn("pick the Bambu Lab H2C at the top", body)
        self.assertIn("already open", body)

    def test_76_send_with_studio_closed_switches_the_device_first(self):
        launched = self._device_setup(running=False)
        status, headers, _b = post("/project/send")
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers), "/handoff?device=switched")
        self.assertEqual(launched, [F.project])
        self.assertEqual(self._studio_device(), "SER-H2C-001",
                         "the Device page was moved before the launch")
        import studio_config
        self.assertEqual(studio_config.current_printer(),
                         "Bambu Lab H2C 0.4 nozzle")
        _s, _h, body = get("/handoff?device=switched")
        self.assertIn("switched its Device page to the Bambu Lab H2C",
                      body)
        self.assertNotIn("still shows", body)
        # Asking the question when nothing is pending: back to the
        # send page in words.
        status, headers, _b = get("/project/send-device")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers).startswith(
            "/project/send?msg="))

    def test_77_send_with_the_device_already_right_is_silent(self):
        launched = self._device_setup(running=True, selected="SER-H2C-001")
        status, headers, _b = post("/project/send")
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers), "/handoff?second=1")
        self.assertEqual(launched, [F.project])
        _s, _h, body = get("/handoff?second=1")
        self.assertNotIn("Device page", body)

    def test_78_unknown_serial_opens_and_warns(self):
        # The page is on a known other printer, but the project's
        # printer has no serial in the map: only the user can move it.
        launched = self._device_setup(running=False)
        config = config_store.load()
        config["device_serials"] = {"Bambu Lab X1 Carbon": "SER-X1C-002"}
        config_store.save(config)
        status, headers, _b = post("/project/send")
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers), "/handoff?device=kept")
        self.assertEqual(launched, [F.project])
        self.assertEqual(self._studio_device(), "SER-X1C-002")
        _s, _h, body = get("/handoff?device=kept")
        self.assertIn("still shows the Bambu Lab X1 Carbon", body)

    def test_79_sliced_send_asks_the_same_question(self):
        launched = self._device_setup(running=True)
        out = F.sandbox / "benchy.gcode.3mf"
        out.write_bytes(b"")

        class Job:
            status = "done"

            def output_file(self):
                return out
        old_job = server.state["slice_job"]
        server.state["slice_job"] = Job()
        self.addCleanup(server.state.__setitem__, "slice_job", old_job)
        status, headers, _b = post("/project/send-sliced")
        self.assertEqual(status, 303)
        self.assertEqual(location_of(headers),
                         "/project/send-device?sliced=1")
        self.assertEqual(launched, [])
        _s, _h, body = get("/project/send-device?sliced=1")
        self.assertIn('action="/project/send-sliced"', body)
        self.assertIn('href="/project/slice-report"', body)
        status, headers, _b = post("/project/send-sliced", device="keep")
        self.assertEqual(location_of(headers),
                         "/handoff?sliced=1&second=1&device=kept")
        self.assertEqual(launched, [out])

    def test_99_close_project_returns_home(self):
        status, headers, _body = post("/project/close")
        self.assertEqual(status, 303)
        self.assertTrue(location_of(headers).startswith("/?msg="))
        self.assertIsNone(server.state["session"])
        status, headers, _body = get("/project/quality")
        self.assertEqual(status, 303)


if __name__ == "__main__":
    unittest.main()
