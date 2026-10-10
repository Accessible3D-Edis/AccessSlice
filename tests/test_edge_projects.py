"""The edge-project torture pass.

Projects other people's Studios will produce - multiple objects, empty
plates, multiple plates, odd list shapes, future settings keys, unicode
names - must open, render EVERY page, and say honestly what is not
supported. Never a crash, never a silent misread.

Each test mutates a fresh copy of the committed test project into one
edge shape, opens it, and runs the full page-render sweep directly
through webui (no server needed - the rendering functions are where
shape assumptions live).
"""

import json
import shutil
import unittest

import support
import edit_bambu_3mf as engine
import webui
from settings_catalog import TABS
from slicer_project import ProjectError, ProjectSession

# A presets-tab view with nothing in it - the tab must cope.
EMPTY_PRESET_VIEW = {
    "process": [], "filament_options": [], "filament_matches": None,
    "filament_search": "", "machine_options": [], "machine_matches": None,
    "printer_search": "",
}


def render_everything(session):
    """Render every project page; any exception fails the test."""
    pages = {}
    for tab_id, _name, entries in TABS:
        pages[tab_id] = webui.render_curated_tab(session, tab_id, entries)
    pages["filaments"] = webui.render_filaments_tab(session)
    pages["presets"] = webui.render_presets_tab(session, EMPTY_PRESET_VIEW)
    pages["placement"] = webui.render_placement_tab(session)
    pages["all"] = webui.render_all_tab(session, "")
    pages["all-search"] = webui.render_all_tab(session, "wall")
    pages["review"] = webui.render_review(session)
    pages["backups"] = webui.render_backups_tab(session)
    pages["send"] = webui.render_send(session, "Bambu Studio",
                                      bool(session.pending))
    for o in session.objects():
        pages[f"object-{o['id']}"] = webui.render_object_page(
            session, o["id"])
    for name, html in pages.items():
        if not html or "<html" not in html:
            raise AssertionError(f"page {name} rendered nothing")
    return pages


class EdgeCase(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)
        self.fake_exe = support.make_fake_studio(self.sandbox)

    def mutate(self, entry_name, old, new):
        entries = engine.load_entries(self.project)
        text = entries[entry_name].decode("utf-8")
        self.assertIn(old, text, "test fixture drifted from the file")
        entries[entry_name] = text.replace(old, new).encode("utf-8")
        engine.save_entries(self.project, entries)

    def mutate_settings(self, changes):
        entries = engine.load_entries(self.project)
        settings = json.loads(entries[engine.PROJECT_SETTINGS_ENTRY])
        settings.update(changes)
        entries[engine.PROJECT_SETTINGS_ENTRY] = json.dumps(
            settings).encode("utf-8")
        engine.save_entries(self.project, entries)

    def session(self):
        return ProjectSession(self.project, slicer_exe=str(self.fake_exe))


ITEM_LINE = ('<item objectid="2" '
             'p:UUID="00000002-b1ec-4553-aec9-835e5b724bb4" '
             'transform="1 0 0 0 1 0 0 0 1 165 160 38.4231987" '
             'printable="1"/>')


class MultiObjectTests(EdgeCase):

    def setUp(self):
        super().setUp()
        # A second build item whose object has no model_settings entry
        # at all - name and filament unknown. (Shape seen when projects
        # are assembled outside Studio.)
        self.mutate(
            engine.MODEL_ENTRY, ITEM_LINE,
            ITEM_LINE + "\n  "
            + ITEM_LINE.replace('objectid="2"', 'objectid="3"')
                       .replace("00000002", "00000003")
                       .replace("165 160", "100 100"))

    def test_both_objects_appear_and_every_page_renders(self):
        s = self.session()
        objs = s.objects()
        self.assertEqual(len(objs), 2)
        second = [o for o in objs if o["id"] == "3"][0]
        self.assertEqual(second["name"], "(unnamed object)",
                         "a nameless object gets an honest placeholder")
        self.assertIsNone(second["extruder"])
        render_everything(s)

    def test_the_extra_object_can_be_moved_and_saved(self):
        s = self.session()
        self.assertTrue(s.stage_move("3", 50.0, 60.0))
        s.save()
        fresh = ProjectSession(self.project)
        moved = [o for o in fresh.objects() if o["id"] == "3"][0]
        self.assertAlmostEqual(moved["x"], 50.0)
        self.assertAlmostEqual(moved["y"], 60.0)


class EmptyPlateTests(EdgeCase):

    def setUp(self):
        super().setUp()
        self.mutate(engine.MODEL_ENTRY, ITEM_LINE, "")

    def test_no_objects_is_announced_and_everything_renders(self):
        s = self.session()
        self.assertEqual(s.objects(), [])
        self.assertIn("no printable objects",
                      " ".join(s.structure_notes))
        render_everything(s)

    def test_settings_can_still_be_edited_and_saved(self):
        s = self.session()
        s.stage_setting("wall_loops", "3")
        lines = s.save()
        self.assertIn("Saved and verified", lines[0])


class MultiPlateTests(EdgeCase):

    def setUp(self):
        super().setUp()
        support.add_empty_plate(self.project, "2")

    def test_extra_plates_are_summarised_and_everything_renders(self):
        s = self.session()
        self.assertIn("2 build plates", " ".join(s.summary()))
        render_everything(s)


class OddListShapeTests(EdgeCase):

    def setUp(self):
        super().setUp()
        s = ProjectSession(self.project)
        temps = list(s.settings["nozzle_temperature"]) + ["230"]  # 13
        self.mutate_settings({"nozzle_temperature": temps})

    def test_the_odd_list_is_announced_and_editable_per_slot(self):
        s = self.session()
        self.assertIn("nozzle_temperature", " ".join(s.structure_notes))
        render_everything(s)
        html = webui.render_setting_page(s, "nozzle_temperature")
        self.assertIn("<html", html)
        # Per-slot editing still works even when blocks do not divide.
        new = list(s.settings["nozzle_temperature"])
        new[12] = "231"
        self.assertTrue(s.stage_setting("nozzle_temperature", new))
        s.save()


class FutureKeyTests(EdgeCase):

    def setUp(self):
        super().setUp()
        # A future Studio invents keys with shapes we never planned for.
        self.mutate_settings({
            "future_scalar_key": "whatever new format 5x2@7",
            "future_nested_key": {"nested": [1, 2, {"deep": True}]},
        })

    def test_unknown_shapes_render_without_crashing(self):
        s = self.session()
        render_everything(s)
        html = webui.render_all_tab(s, "future")
        self.assertIn("future_scalar_key", html)
        html = webui.render_setting_page(s, "future_scalar_key")
        self.assertIn("whatever new format", html)
        webui.render_setting_page(s, "future_nested_key")

    def test_the_scalar_future_key_is_even_editable(self):
        s = self.session()
        self.assertTrue(s.stage_setting("future_scalar_key", "other"))
        s.save()
        self.assertEqual(ProjectSession(self.project)
                         .settings["future_scalar_key"], "other")


class UnicodeNameTests(EdgeCase):

    def setUp(self):
        super().setUp()
        for entry in (engine.MODEL_ENTRY, engine.MODEL_SETTINGS_ENTRY):
            entries = engine.load_entries(self.project)
            text = entries[entry].decode("utf-8")
            if "blind-Benchy.stl" in text:
                self.mutate(entry, "blind-Benchy.stl",
                            "Ærø søjle nr. 3 😀.stl")

    def test_unicode_object_names_flow_through_everything(self):
        s = self.session()
        self.assertEqual(s.objects()[0]["name"], "Ærø søjle nr. 3 😀.stl")
        pages = render_everything(s)
        self.assertIn("Ærø søjle nr. 3", pages["placement"])
        s.stage_move("2", 100.0, 100.0)
        s.save()


class BrokenArchiveTests(EdgeCase):
    """Damage beyond repair must be a friendly refusal, never a crash."""

    def test_truncated_zip_is_a_friendly_error(self):
        data = self.project.read_bytes()
        self.project.write_bytes(data[: len(data) // 2])
        with self.assertRaises(ProjectError):
            self.session()

    def test_mangled_settings_json_is_a_friendly_error(self):
        entries = engine.load_entries(self.project)
        entries[engine.PROJECT_SETTINGS_ENTRY] = b'["a list, not an object"]'
        engine.save_entries(self.project, entries)
        with self.assertRaises(ProjectError) as ctx:
            self.session()
        self.assertIn("Bambu Studio", str(ctx.exception),
                      "the error must say the way out")


if __name__ == "__main__":
    unittest.main()
