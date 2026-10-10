"""Tests for studio_config.py - editing BambuStudio.conf safely.

All tests point %APPDATA% into the sandbox, so the user's REAL
BambuStudio.conf can never be read or written here. The conf fixture is
written in Studio's exact format (JSON + CRLF + MD5-of-LF-body checksum
line) and every mutating test verifies format, checksum, and backup.
"""

import hashlib
import json
import os
import shutil
import unittest

import support
import studio_config


SERIALS = {
    "Bambu Lab H2C": "SER-H2C-001",
    "Bambu Lab X1 Carbon": "SER-X1C-002",
}


def conf_data_x1c():
    return {
        "app": {"user_last_selected_machine": "SER-X1C-002"},
        "presets": {
            "machine": "Bambu Lab X1 Carbon 0.4 nozzle",
            "process": "0.20mm Standard @BBL X1C",
            "filaments": ["Bambu PLA Matte @BBL X1C"],
        },
    }


class SandboxedConfCase(unittest.TestCase):

    def setUp(self):
        self.sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)
        self.fake_exe = str(support.make_fake_studio(self.sandbox))
        self._old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(self.sandbox / "AppData")
        self.addCleanup(self._restore_appdata)

    def _restore_appdata(self):
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata

    def write_conf(self, data):
        return support.write_studio_conf(os.environ["APPDATA"], data)


class NameParsingTests(unittest.TestCase):

    def test_model_token_longest_match_wins(self):
        self.assertEqual(
            studio_config.model_token("Bambu Lab X1 Carbon 0.4 nozzle"),
            "X1C")
        self.assertEqual(
            studio_config.model_token("Bambu Lab X1 0.4 nozzle"), "X1")
        self.assertEqual(
            studio_config.model_token("Bambu Lab H2C 0.4 nozzle"), "H2C")
        self.assertEqual(studio_config.model_token("Voron 2.4"), "")

    def test_model_name_strips_the_nozzle_part(self):
        self.assertEqual(
            studio_config.model_name("Bambu Lab H2C 0.4 nozzle"),
            "Bambu Lab H2C")
        self.assertEqual(
            studio_config.model_name("Bambu Lab X1 Carbon 0.2 nozzle"),
            "Bambu Lab X1 Carbon")


class ConfReadingTests(SandboxedConfCase):

    def test_missing_conf_reads_as_none(self):
        self.assertIsNone(studio_config.read_conf())
        self.assertEqual(studio_config.current_printer(), "")

    def test_current_printer_reads_the_machine_preset(self):
        self.write_conf(conf_data_x1c())
        self.assertEqual(studio_config.current_printer(),
                         "Bambu Lab X1 Carbon 0.4 nozzle")

    def test_pending_changes_detects_preset_and_device_drift(self):
        self.write_conf(conf_data_x1c())
        # Different machine: an edit is needed.
        self.assertTrue(studio_config.pending_changes(
            "Bambu Lab H2C 0.4 nozzle", SERIALS))
        # Same machine, same serial: nothing to do.
        self.assertFalse(studio_config.pending_changes(
            "Bambu Lab X1 Carbon 0.4 nozzle", SERIALS))
        # Same machine but the Device page points elsewhere (Edis's
        # stranded state of 2026-08-26): still an edit.
        data = conf_data_x1c()
        data["app"]["user_last_selected_machine"] = "SER-H2C-001"
        self.write_conf(data)
        self.assertTrue(studio_config.pending_changes(
            "Bambu Lab X1 Carbon 0.4 nozzle", SERIALS))


    def test_current_device_is_the_selected_serial_s_model(self):
        self.write_conf(conf_data_x1c())
        self.assertEqual(studio_config.current_device(SERIALS),
                         "Bambu Lab X1 Carbon")
        # A serial outside the map, or no selection at all: unknown.
        self.assertEqual(studio_config.current_device({}), "")
        data = conf_data_x1c()
        data["app"] = {}
        self.write_conf(data)
        self.assertEqual(studio_config.current_device(SERIALS), "")

    def test_device_state_is_what_a_send_checks(self):
        # Edis's 2026-09-23 send: the project for the H2C, Studio's
        # Device page on the X1 Carbon. The preset is the file's own
        # business; only the Device page needs moving.
        self.write_conf(conf_data_x1c())
        self.assertEqual(studio_config.device_state(
            "Bambu Lab H2C 0.4 nozzle", SERIALS), "other")
        self.assertEqual(studio_config.device_state(
            "Bambu Lab X1 Carbon 0.4 nozzle", SERIALS), "same")
        # A printer whose serial is not in the map while the page is
        # on a known other printer: only the user can move it.
        self.assertEqual(studio_config.device_state(
            "Bambu Lab P1S 0.4 nozzle", SERIALS), "unknown")
        # No map at all: nothing on disk says the page is wrong.
        self.assertEqual(studio_config.device_state(
            "Bambu Lab H2C 0.4 nozzle", {}), "same")
        # No selection saved yet, serial known: the page needs setting.
        data = conf_data_x1c()
        data["app"] = {}
        self.write_conf(data)
        self.assertEqual(studio_config.device_state(
            "Bambu Lab H2C 0.4 nozzle", SERIALS), "other")


class SetPrinterTests(SandboxedConfCase):

    def read_conf_file(self):
        path = studio_config.conf_path()
        return path, path.read_bytes().decode("utf-8")

    def assert_valid_studio_format(self, text):
        """The written file must keep Studio's exact format."""
        self.assertIn("\r\n", text, "file must be CRLF on disk")
        body, _sep, tail = text.rpartition("\r\n# MD5 checksum ")
        stated = tail.strip()
        recomputed = hashlib.md5(
            body.replace("\r\n", "\n").encode("utf-8")).hexdigest().upper()
        self.assertEqual(stated, recomputed,
                         "checksum line does not match the body")

    def test_switch_x1c_to_h2c_remaps_everything(self):
        self.write_conf(conf_data_x1c())
        messages = studio_config.set_printer(
            "Bambu Lab H2C 0.4 nozzle", self.fake_exe, SERIALS)
        text = " ".join(messages)
        self.assertIn("printer set to Bambu Lab H2C 0.4 nozzle", text)

        path, content = self.read_conf_file()
        self.assert_valid_studio_format(content)
        data, _end = json.JSONDecoder().raw_decode(content)
        self.assertEqual(data["presets"]["machine"],
                         "Bambu Lab H2C 0.4 nozzle")
        self.assertEqual(data["presets"]["process"],
                         "0.20mm Standard @BBL H2C")
        self.assertEqual(data["presets"]["filaments"],
                         ["Bambu PLA Matte @BBL H2C"])
        self.assertEqual(data["app"]["user_last_selected_machine"],
                         "SER-H2C-001")
        backup = path.with_suffix(".conf.pre-accessslice-backup")
        self.assertTrue(backup.is_file(), "backup must be written")

    def test_round_trip_back_to_x1c(self):
        self.write_conf(conf_data_x1c())
        studio_config.set_printer("Bambu Lab H2C 0.4 nozzle",
                                  self.fake_exe, SERIALS)
        studio_config.set_printer("Bambu Lab X1 Carbon 0.4 nozzle",
                                  self.fake_exe, SERIALS)
        _path, content = self.read_conf_file()
        self.assert_valid_studio_format(content)
        data, _end = json.JSONDecoder().raw_decode(content)
        self.assertEqual(data["presets"], conf_data_x1c()["presets"])
        self.assertEqual(data["app"]["user_last_selected_machine"],
                         "SER-X1C-002")

    def test_device_only_fix_when_preset_already_matches(self):
        data = conf_data_x1c()
        data["app"]["user_last_selected_machine"] = "somewhere-else"
        self.write_conf(data)
        messages = studio_config.set_printer(
            "Bambu Lab X1 Carbon 0.4 nozzle", self.fake_exe, SERIALS)
        self.assertTrue(any("Device page" in m for m in messages))
        _path, content = self.read_conf_file()
        self.assert_valid_studio_format(content)
        parsed, _end = json.JSONDecoder().raw_decode(content)
        self.assertEqual(parsed["app"]["user_last_selected_machine"],
                         "SER-X1C-002")

    def test_no_op_when_everything_already_matches(self):
        self.write_conf(conf_data_x1c())
        before = studio_config.conf_path().read_bytes()
        messages = studio_config.set_printer(
            "Bambu Lab X1 Carbon 0.4 nozzle", self.fake_exe, SERIALS)
        self.assertTrue(any("already set" in m for m in messages))
        self.assertEqual(studio_config.conf_path().read_bytes(), before,
                         "a no-op must not rewrite the conf")

    def test_unknown_model_gets_the_pick_by_hand_hint(self):
        self.write_conf(conf_data_x1c())
        messages = studio_config.set_printer(
            "Bambu Lab H2C 0.4 nozzle", self.fake_exe, {})
        self.assertTrue(any("No device serial known" in m
                            for m in messages))

    def test_an_unverified_conf_version_adds_a_caution(self):
        data = conf_data_x1c()
        data["app"]["version"] = "03.00.00.01"
        self.write_conf(data)
        messages = studio_config.set_printer(
            "Bambu Lab H2C 0.4 nozzle", self.fake_exe, SERIALS)
        self.assertTrue(any("not been verified" in m for m in messages),
                        "an unverified Studio version must add a "
                        "caution to the messages")
        # A caution, not a refusal: the switch itself still happened.
        self.assertEqual(studio_config.current_printer(),
                         "Bambu Lab H2C 0.4 nozzle")

    def test_a_verified_conf_version_stays_quiet(self):
        data = conf_data_x1c()
        data["app"]["version"] = "02.08.02.61"
        self.write_conf(data)
        messages = studio_config.set_printer(
            "Bambu Lab H2C 0.4 nozzle", self.fake_exe, SERIALS)
        self.assertFalse(any("not been verified" in m for m in messages))

    def test_missing_conf_raises_with_guidance(self):
        with self.assertRaises(studio_config.StudioConfError):
            studio_config.set_printer("Bambu Lab H2C 0.4 nozzle",
                                      self.fake_exe, SERIALS)


class StudioRunningTests(unittest.TestCase):

    def test_a_nonexistent_process_reads_as_not_running(self):
        self.assertFalse(studio_config.studio_running(
            r"C:\nowhere\accessslice-test-not-a-real-program.exe"))


if __name__ == "__main__":
    unittest.main()
