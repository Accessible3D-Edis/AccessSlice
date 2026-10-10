"""The build script's code-signing step, without running signtool.

What is pinned: no signing.json means an unsigned build (the first
release ships that way); a configuration turns into a signtool
command with a SHA-256 digest, an RFC 3161 timestamp and the
configured certificate selection; the same command is handed to Inno
Setup in its $f / $q spelling; a bad configuration is refused in
words; and the installer script only signs when the build passes
/DSign=1. tests\\test_packaged.py separately checks a built exe for a
signature when a signing configuration is present.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import support

PACKAGING = support.REPO_ROOT / "packaging"


def _load_build():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build", PACKAGING / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSigningStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = _load_build()
        cls.tmp = Path(tempfile.mkdtemp(prefix="accessslice-sign-"))
        cls.fake_tool = cls.tmp / "signtool.exe"
        cls.fake_tool.write_bytes(b"")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _config(self, **kw):
        path = self.tmp / "signing.json"
        data = {"signtool": str(self.fake_tool),
                "sign_args": ["/sha1", "ABCDEF0123456789"]}
        data.update(kw)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_no_configuration_means_an_unsigned_build(self):
        self.assertIsNone(self.build.signing_config(self.tmp / "absent.json"))

    def test_the_example_file_is_not_a_live_configuration(self):
        self.assertFalse((PACKAGING / "signing.json").exists()
                         and "THUMBPRINT-OF" in (PACKAGING / "signing.json")
                         .read_text(encoding="utf-8"),
                         "the example thumbprint must never be built with")
        example = json.loads((PACKAGING / "signing.example.json")
                             .read_text(encoding="utf-8"))
        self.assertIn("sign_args", example)

    def test_sign_command_has_digest_timestamp_certificate_and_file(self):
        cfg = self.build.signing_config(self._config())
        cmd = self.build.sign_command(cfg, r"C:\out\AccessSlice.exe")
        self.assertEqual(cmd[0], str(self.fake_tool))
        self.assertEqual(cmd[1], "sign")
        self.assertIn("/fd", cmd)
        self.assertEqual(cmd[cmd.index("/fd") + 1], "SHA256")
        self.assertIn("/tr", cmd)
        self.assertEqual(cmd[cmd.index("/tr") + 1],
                         self.build.DEFAULT_TIMESTAMP)
        self.assertEqual(cmd[cmd.index("/td") + 1], "SHA256")
        self.assertEqual(cmd[cmd.index("/sha1") + 1], "ABCDEF0123456789")
        self.assertEqual(cmd[-1], r"C:\out\AccessSlice.exe")

    def test_a_custom_timestamp_server_is_used(self):
        cfg = self.build.signing_config(
            self._config(timestamp="http://ts.example/rfc3161"))
        cmd = self.build.sign_command(cfg, "x.exe")
        self.assertEqual(cmd[cmd.index("/tr") + 1], "http://ts.example/rfc3161")

    def test_inno_gets_the_same_command_in_its_own_spelling(self):
        cfg = self.build.signing_config(self._config())
        inno = self.build.inno_sign_command(cfg)
        self.assertTrue(inno.endswith(" $f"))
        self.assertIn("/fd SHA256", inno)
        self.assertIn("/sha1 ABCDEF0123456789", inno)
        # Paths with spaces are wrapped in Inno's $q quotes.
        spaced = self.tmp / "with space" / "signtool.exe"
        spaced.parent.mkdir()
        spaced.write_bytes(b"")
        cfg2 = self.build.signing_config(self._config(signtool=str(spaced)))
        self.assertIn(f"$q{spaced}$q", self.build.inno_sign_command(cfg2))

    def test_bad_configurations_are_refused_in_words(self):
        with self.assertRaises(SystemExit) as ctx:
            self.build.signing_config(self._config(sign_args=[]))
        self.assertIn("sign_args", str(ctx.exception))
        with self.assertRaises(SystemExit) as ctx:
            self.build.signing_config(
                self._config(signtool=str(self.tmp / "missing.exe")))
        self.assertIn("signtool", str(ctx.exception))

    def test_the_installer_script_signs_only_when_told(self):
        iss = (PACKAGING / "AccessSlice.iss").read_text(encoding="utf-8")
        block = iss[iss.index("#ifdef Sign"):iss.index("#endif",
                                                       iss.index("#ifdef Sign"))]
        self.assertIn("SignTool=accessslice", block)
        self.assertIn("SignedUninstaller=yes", block)
        self.assertEqual(iss.count("SignTool="), 1)

    def test_signing_json_is_git_ignored(self):
        ignore = (support.REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("packaging/signing.json", ignore)


if __name__ == "__main__":
    unittest.main()
