"""The printer password in the config is protected with DPAPI
(2026-09-12, brought over from the Prusa Accessible Control program):
saved encrypted, loaded decrypted, never on disk in plain text, and a
blob from another account is left out instead of crashing."""

import json
import shutil
import sys
import unittest

import support
import config_store


class ProtectedKeysTests(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.old_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = self.sandbox / "config.json"

    def tearDown(self):
        config_store.CONFIG_PATH = self.old_path
        shutil.rmtree(self.sandbox, ignore_errors=True)

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows only")
    def test_round_trip_keeps_the_plain_text_off_the_disk(self):
        config = {"slicer": "prusa",
                  "lan_printer_keys": {"prusa": "s3cretpw"}}
        config_store.save(config)
        # The caller's dict is untouched.
        self.assertEqual(config["lan_printer_keys"], {"prusa": "s3cretpw"})
        raw = config_store.CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("s3cretpw", raw)
        on_disk = json.loads(raw)
        self.assertEqual(on_disk["lan_printer_keys"], {})
        self.assertIn("prusa", on_disk[config_store.PROTECTED_KEYS])
        loaded = config_store.load()
        self.assertEqual(loaded["lan_printer_keys"], {"prusa": "s3cretpw"})
        self.assertNotIn(config_store.PROTECTED_KEYS, loaded)
        # Saving what was loaded protects it again, unchanged.
        config_store.save(loaded)
        self.assertEqual(config_store.load()["lan_printer_keys"],
                         {"prusa": "s3cretpw"})

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows only")
    def test_a_blob_from_elsewhere_is_left_out(self):
        config_store.CONFIG_PATH.write_text(json.dumps({
            "slicer": "prusa", "lan_printer_keys": {},
            config_store.PROTECTED_KEYS: {"prusa": "bm90IGEgYmxvYg=="}}),
            encoding="utf-8")
        loaded = config_store.load()
        self.assertEqual(loaded["lan_printer_keys"], {})
        self.assertEqual(loaded["slicer"], "prusa")

    def test_without_dpapi_the_key_stays_plain(self):
        old = config_store._dpapi
        config_store._dpapi = lambda: None
        try:
            config_store.save({"lan_printer_keys": {"prusa": "plainpw"}})
            on_disk = json.loads(
                config_store.CONFIG_PATH.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["lan_printer_keys"],
                             {"prusa": "plainpw"})
            self.assertNotIn(config_store.PROTECTED_KEYS, on_disk)
            self.assertEqual(config_store.load()["lan_printer_keys"],
                             {"prusa": "plainpw"})
        finally:
            config_store._dpapi = old

    def test_no_keys_means_no_protected_block(self):
        config_store.save({"slicer": "bambu"})
        on_disk = json.loads(
            config_store.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertNotIn(config_store.PROTECTED_KEYS, on_disk)
        self.assertEqual(config_store.load(), {"slicer": "bambu"})


if __name__ == "__main__":
    unittest.main()
