"""Tests for the 3MF text-editing engine (tools/edit_bambu_3mf.py).

The engine is the layer everything else trusts: zip round-trips with
forward-slash entry names (backslash names corrupt the file for Bambu
Studio - learned the hard way), and structural verification that must
actually catch a broken file.
"""

import shutil
import unittest
import zipfile

import support
import edit_bambu_3mf as engine


class EngineTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.project = support.copy_test_project(self.sandbox)

    def test_load_entries_finds_the_three_edited_files(self):
        entries = engine.load_entries(self.project)
        for name in (engine.MODEL_ENTRY, engine.MODEL_SETTINGS_ENTRY,
                     engine.PROJECT_SETTINGS_ENTRY):
            self.assertIn(name, entries)
            self.assertGreater(len(entries[name]), 0)

    def test_save_round_trip_preserves_every_entry(self):
        entries = engine.load_entries(self.project)
        engine.save_entries(self.project, entries)
        after = engine.load_entries(self.project)
        self.assertEqual(set(entries), set(after))
        for name in entries:
            self.assertEqual(entries[name], after[name],
                             f"entry {name} changed in a plain round trip")

    def test_saved_zip_uses_forward_slash_entry_names_only(self):
        # Backslash entry names (what naive Windows zipping produces)
        # make Bambu Studio reject the file. The engine must never
        # write one.
        entries = engine.load_entries(self.project)
        engine.save_entries(self.project, entries)
        with zipfile.ZipFile(self.project) as z:
            for name in z.namelist():
                self.assertNotIn("\\", name,
                                 f"backslash in zip entry name: {name}")

    def test_verify_passes_on_a_good_file(self):
        entries = engine.load_entries(self.project)
        problems = engine.verify(self.project, list(entries.keys()))
        self.assertEqual(problems, [])

    def test_verify_catches_broken_json(self):
        entries = engine.load_entries(self.project)
        entries[engine.PROJECT_SETTINGS_ENTRY] = b"this is not json {"
        engine.save_entries(self.project, entries)
        problems = engine.verify(self.project, list(entries.keys()))
        self.assertTrue(problems, "verify accepted a file with broken "
                                  "project_settings.config")

    def test_verify_catches_broken_xml(self):
        entries = engine.load_entries(self.project)
        entries[engine.MODEL_ENTRY] = b"<model><unclosed>"
        engine.save_entries(self.project, entries)
        problems = engine.verify(self.project, list(entries.keys()))
        self.assertTrue(problems, "verify accepted a file with broken "
                                  "model XML")

    def _write_model_in_pieces(self, pieces):
        # Rewrites the model entry from an iterable of byte strings,
        # streamed so a huge entry is never held in memory whole.
        entries = engine.load_entries(self.project)
        with zipfile.ZipFile(self.project, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in entries.items():
                if name == engine.MODEL_ENTRY:
                    with z.open(name, "w", force_zip64=True) as f:
                        for piece in pieces:
                            f.write(piece)
                else:
                    z.writestr(name, data)
        return list(entries.keys())

    def test_verify_passes_a_model_over_one_gigabyte(self):
        # A 23-million-triangle mesh (Skyward Dragon, 2026-09-25) is a
        # 1.7 GB 3dmodel.model. Parsed as one buffer, expat refused it
        # with "out of memory: line 1, column 0" and a good build was
        # thrown away. Just over 1 GiB is the size that failed.
        row = b'<vertex x="123.456789" y="-12.345678" z="45.678901"/>\n'
        block = row * ((1 << 20) // len(row))
        count = (1 << 30) // len(block) + 64

        def pieces():
            yield (b'<?xml version="1.0" encoding="UTF-8"?>\n<model '
                   b'xmlns="http://schemas.microsoft.com/3dmanufacturing/'
                   b'core/2015/02"><resources><object id="1"><mesh>'
                   b'<vertices>')
            for _ in range(count):
                yield block
            yield b'</vertices></mesh></object></resources></model>'

        expected = self._write_model_in_pieces(pieces())
        self.assertEqual(engine.verify(self.project, expected), [])

    def test_verify_catches_broken_xml_past_the_first_piece(self):
        # The check reads in 1 MiB pieces; a break deep in the file must
        # still be found.
        filler = b"<v/>" * (3 << 20)
        expected = self._write_model_in_pieces(
            [b"<model>", filler, b"<unclosed></model>"])
        problems = engine.verify(self.project, expected)
        self.assertTrue(problems, "verify missed a break past 1 MiB")

    def test_verify_catches_a_missing_entry(self):
        entries = engine.load_entries(self.project)
        expected = list(entries.keys())
        del entries[engine.MODEL_SETTINGS_ENTRY]
        engine.save_entries(self.project, entries)
        problems = engine.verify(self.project, expected)
        self.assertTrue(problems, "verify accepted a file missing "
                                  "model_settings.config")


if __name__ == "__main__":
    unittest.main()
