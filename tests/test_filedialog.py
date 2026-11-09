"""Tests for filedialog - the Browse buttons beside the path fields.

The Windows dialog itself needs a person to answer it, so the tests pin
everything around it: the filter strings the dialog is handed, where it
opens, that every path field carries its (script-shown) button, and the
/browse route's answers - the dialog stood in for by a fake that
returns a path, a cancel or a failure. The route must also work while
another request holds the server's lock: a dialog waits for a person.
"""

import json
import threading
import unittest

import support
import filedialog
import server
import webui
import test_server_http as http_tests


class FilterTests(unittest.TestCase):

    def test_filters_end_with_all_files_and_double_nul(self):
        text = filedialog.filter_string([("Models", "*.stl")])
        self.assertEqual(text, "Models\0*.stl\0All files\0*.*\0\0")

    def test_every_kind_has_a_title(self):
        for kind, (title, filters) in filedialog.KINDS.items():
            self.assertTrue(title.startswith("Choose"), kind)

    def test_the_dialog_opens_where_the_field_points(self):
        sandbox = support.make_sandbox()
        self.addCleanup(__import__("shutil").rmtree, sandbox, True)
        project = support.copy_test_project(sandbox)
        self.assertEqual(filedialog.start_folder(str(project)),
                         str(sandbox))
        self.assertEqual(filedialog.start_folder(f'"{sandbox}"'),
                         str(sandbox))
        self.assertEqual(filedialog.start_folder(""), "")
        self.assertEqual(filedialog.start_folder("Z:/no/such/place.stl"),
                         "")


class ButtonMarkupTests(unittest.TestCase):

    def test_the_button_is_hidden_without_script_and_names_its_field(self):
        html = webui.browse_button("model-path", "model", "the model file")
        self.assertIn('type="button"', html)
        self.assertIn(" hidden ", html)
        self.assertIn('data-browse-for="model-path"', html)
        self.assertIn("Browse for the model file", html)

    def test_the_path_fields_carry_buttons(self):
        start = webui.render_start("Bambu Studio", [], "")
        self.assertIn('data-browse-for="model-path"', start)
        files = webui.render_files("C:/projects", [])
        self.assertIn('data-browse-for="path"', files)


class BrowseRouteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        http_tests.setUpModule()

    @classmethod
    def tearDownClass(cls):
        http_tests.tearDownModule()

    def setUp(self):
        self._orig = filedialog.choose
        self.addCleanup(setattr, filedialog, "choose", self._orig)

    def browse(self, **form):
        status, _h, body = http_tests.request("POST", "/browse", form=form)
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_the_chosen_path_comes_back(self):
        seen = {}

        def fake(kind, current=""):
            seen["kind"], seen["current"] = kind, current
            return r"C:\Models\relief.stl"
        filedialog.choose = fake
        data = self.browse(kind="model", current=r"C:\Models\old.stl")
        self.assertEqual(data["path"], r"C:\Models\relief.stl")
        self.assertEqual(seen, {"kind": "model",
                                "current": r"C:\Models\old.stl"})

    def test_cancel_is_an_empty_path(self):
        filedialog.choose = lambda kind, current="": ""
        self.assertEqual(self.browse(kind="project")["path"], "")

    def test_an_unknown_kind_still_opens_a_plain_dialog(self):
        seen = {}
        filedialog.choose = (lambda kind, current="":
                             seen.setdefault("kind", kind) and "")
        self.browse(kind="../../etc")
        self.assertEqual(seen["kind"], "any")

    def test_no_dialog_means_type_the_path(self):
        def broken(kind, current=""):
            raise filedialog.DialogUnavailable("no desktop")
        filedialog.choose = broken
        data = self.browse(kind="model")
        self.assertEqual(data["path"], "")
        self.assertIn("type the full path", data["problem"])

    def test_the_dialog_does_not_wait_for_the_server_lock(self):
        # A dialog stays open while the person looks; it must neither
        # need nor hold the lock every other action takes.
        filedialog.choose = lambda kind, current="": "C:/x.stl"
        result = {}
        with server.state["lock"]:
            t = threading.Thread(target=lambda: result.update(
                self.browse(kind="model")))
            t.start()
            t.join(5)
        self.assertEqual(result.get("path"), "C:/x.stl")

    def test_a_post_from_another_website_is_refused(self):
        filedialog.choose = lambda kind, current="": "C:/secret.txt"
        status, _h, body = http_tests.request(
            "POST", "/browse", form={"kind": "any"},
            headers={"Origin": "https://evil.example"})
        self.assertNotEqual(status, 200)
        self.assertNotIn("secret", body)


if __name__ == "__main__":
    unittest.main()
