"""No private data in the repository - a publish-safety net.

Born 2026-09-01 from Edis's rule for the K2 LAN work: the printer's
IP address "must never be exposed" when the repo reaches GitHub. The
suite fails the moment a private-network address appears in any
tracked text file, so a slip is caught the same day it is made, not
at publish time. Loopback (127.0.0.1) is the test suite's own and
stays allowed.

Extended 2026-09-07 for the pre-publication scrub: Bambu Lab printer
SERIAL NUMBERS (15 characters: two digits, a letter, twelve more
letters or digits - the shape of every serial seen on this machine)
are private too; a serial plus the printer's access code gives LAN
control of that printer. They live only in the user's config.json.
"""

import re
import zipfile
import unittest

import support

PRIVATE_IP = re.compile(
    rb"\b(?:192\.168\.\d{1,3}\.\d{1,3}"
    rb"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    rb"|10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

# Two digits, one capital letter, then twelve capitals or digits.
BAMBU_SERIAL = re.compile(rb"\b[0-9]{2}[A-Z][A-Z0-9]{12}\b")

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".html", ".js",
                 ".css", ".cfg", ".yaml", ".yml", ".ini", ".iss", ".spec"}
SKIP_DIRS = {".git", "__pycache__", "backups", "sliced", "build", "dist",
             "output"}


def _tracked_text_files():
    for path in sorted(support.REPO_ROOT.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def _hits(pattern):
    hits = []
    for path in _tracked_text_files():
        data = path.read_bytes()
        rel = path.relative_to(support.REPO_ROOT)
        for match in pattern.finditer(data):
            line = data.count(b"\n", 0, match.start()) + 1
            hits.append(f"{rel}:{line}: {match.group(0).decode('ascii')}")
    return hits


# Extended 2026-10-10 for the first public push: a path into a user's
# own Windows profile (C:\Users\<name>, any name but Public) names the
# person, and a slicer project saved while signed in carries the
# account's number as DesignerUserId. Both are checked in the public
# files and inside every test project's zip entries.
USER_PATH = re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b)"
                       rb"[A-Za-z][^\\/\"'\s]*[\\/]")
ACCOUNT_ID = re.compile(rb'name="DesignerUserId">\s*[^<\s]')
PUBLIC_PARTS = ("app", "tests", "testdata", "packaging", "tools")
PUBLIC_FILES = ("README.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md")


def _public_text_files():
    for path in _tracked_text_files():
        rel = path.relative_to(support.REPO_ROOT)
        if rel.parts[0] in PUBLIC_PARTS or str(rel) in PUBLIC_FILES:
            yield rel, path.read_bytes()


def _test_project_entries():
    for path in sorted((support.REPO_ROOT / "testdata").glob("*.3mf")):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                yield f"{path.name} [{name}]", z.read(name)


def _find(pattern, entries):
    return [f"{label}: {m.group(0).decode('utf-8', 'replace')}"
            for label, data in entries for m in pattern.finditer(data)]


class TestNoPrivateData(unittest.TestCase):
    def test_no_user_profile_paths_in_public_files(self):
        hits = _find(USER_PATH, _public_text_files())
        self.assertEqual(hits, [], "Paths into a user's profile: "
                         + "; ".join(hits))

    def test_no_user_profile_paths_inside_test_projects(self):
        hits = _find(USER_PATH, _test_project_entries())
        self.assertEqual(hits, [], "Paths into a user's profile: "
                         + "; ".join(hits))

    def test_no_slicer_account_ids_inside_test_projects(self):
        hits = _find(ACCOUNT_ID, _test_project_entries())
        self.assertEqual(hits, [], "Slicer account ids: "
                         + "; ".join(hits))

    def test_the_path_pattern_spares_public_and_example_paths(self):
        made_up = b"C:" + b"\\Users\\" + b"someone\\Desktop\\x.stl"
        self.assertTrue(USER_PATH.search(made_up))
        self.assertFalse(USER_PATH.search(
            rb"C:\Users\Public\Desktop\Creality Print 7.1.lnk"))
        self.assertFalse(USER_PATH.search(
            rb"C:\Program Files\Bambu Studio\bambu-studio.exe"))

    def test_no_private_network_addresses_in_tracked_text(self):
        hits = _hits(PRIVATE_IP)
        self.assertEqual(hits, [], (
            "Private network addresses found in the repository - "
            "remove them before anything is committed or published: "
            + "; ".join(hits)))

    def test_no_bambu_printer_serials_in_tracked_text(self):
        hits = _hits(BAMBU_SERIAL)
        self.assertEqual(hits, [], (
            "Printer serial numbers found in the repository - remove "
            "them before anything is committed or published: "
            + "; ".join(hits)))

    def test_the_serial_pattern_matches_the_real_shape(self):
        # Shaped like a real serial (made up, and assembled here so the
        # scan above never finds it in this file) - caught; a plain
        # number, a commit hash and a version - not.
        made_up = b"01" + b"A" + b"2B3C4D5E6F7G"
        self.assertTrue(BAMBU_SERIAL.search(b"serial " + made_up + b" here"))
        self.assertFalse(BAMBU_SERIAL.search(b"874237060546875"))
        self.assertFalse(BAMBU_SERIAL.search(b"b0aaad3c4f1335e"))
        self.assertFalse(BAMBU_SERIAL.search(b"02.08.00.52"))


if __name__ == "__main__":
    unittest.main()
