"""The sliced preview (Phase V): reading G-code back, in both formats.

Ground truth is real engine output, trimmed and committed in testdata:

- blind-benchy-h2c-sliced-first-six-layers.gcode.3mf: Bambu Studio
  2.8.2's own .gcode.3mf for the H2C, cut after six layers.
- blind-benchy-core-one-sliced-first-blocks.bgcode: PrusaSlicer
  2.9.6's binary G-code for the Core One, the metadata blocks plus
  the first four G-code blocks (heatshrink 12/4 + MeatPack).
- blind-benchy-core-one-sliced-first-blocks.gcode: the same engine's
  ASCII export, cut to the same moves - so the binary decoder is
  checked against the engine's own text, move for move.

Then the parse (layers, kinds, arcs, wipes, extrusion, times), the
binary payload the page draws from, the facts it speaks, the parse
cache, the page over real HTTP in its three states, and the honest
refusals (no slice yet, an unreadable file).
"""

import http.client
import io
import json
import os
import shutil
import struct
import threading
import time
import unittest
import urllib.parse
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import support
import bgcode
import config_store
import preview
import server
import toolpath
import webui
from slicer_project import ProjectSession

TESTDATA = support.REPO_ROOT / "testdata"
BAMBU = TESTDATA / "blind-benchy-h2c-sliced-first-six-layers.gcode.3mf"
BGCODE = TESTDATA / "blind-benchy-core-one-sliced-first-blocks.bgcode"
ASCII = TESTDATA / "blind-benchy-core-one-sliced-first-blocks.gcode"


def _normalised_moves(text):
    """G1 lines with comments and spaces stripped: what the binary
    format keeps of the ASCII text."""
    out = []
    for line in text.splitlines():
        if line.startswith("G1"):
            out.append(line.split(";", 1)[0].replace(" ", "").strip())
    return out


class BinaryGcodeTests(unittest.TestCase):

    def test_heatshrink_literals_and_backrefs(self):
        # Hand-assembled 12/4 stream: literals 'a','b' then a back-
        # reference (offset 2, count 3 -> "aba"), then a reference
        # before the start (offset 6 -> two zero bytes then "ab").
        bits = ""
        for ch in b"ab":
            bits += "1" + format(ch, "08b")
        bits += "0" + format(2 - 1, "012b") + format(3 - 1, "04b")
        bits += "0" + format(6 - 1, "012b") + format(4 - 1, "04b")
        bits += "0" * (-len(bits) % 8)
        data = int(bits, 2).to_bytes(len(bits) // 8, "big")
        self.assertEqual(bgcode.heatshrink_decode(data, 12, 4),
                         b"ababa\0aba")

    def test_meatpack_tables_escapes_and_commands(self):
        # Packing on, "G1 X1" in the normal table: G,1 | space,X | 1
        packed = bytes([0xFF, 0xFF, 0xFB,
                        (0x1 << 4) | 0xD,       # 'G' then '1'
                        (0xE << 4) | 0xB,       # ' ' then 'X'
                        (0xF << 4) | 0x1, ord("\n")])  # '1' then full '\n'
        self.assertEqual(bgcode.meatpack_decode(packed), b"G1 X1\n")
        # No-spaces table: E takes the space's slot.
        packed = bytes([0xFF, 0xFF, 0xFB, 0xFF, 0xFF, 0xF7,
                        (0x1 << 4) | 0xB])
        self.assertEqual(bgcode.meatpack_decode(packed), b"E1")
        # A lone 0xFF before data: both nibbles escaped, two full bytes.
        packed = bytes([0xFF, 0xFF, 0xFB, 0xFF, ord("M"), ord("Y")])
        self.assertEqual(bgcode.meatpack_decode(packed), b"MY")
        # A low-nibble escape emits the full byte first, then the
        # packed second character.
        packed = bytes([0xFF, 0xFF, 0xFB, (0x2 << 4) | 0xF, ord("Y")])
        self.assertEqual(bgcode.meatpack_decode(packed), b"Y2")
        # Packing off: bytes pass through.
        packed = bytes([0xFF, 0xFF, 0xFA]) + b"; comment\n"
        self.assertEqual(bgcode.meatpack_decode(packed), b"; comment\n")

    def test_real_binary_file_blocks_decode_to_their_stated_sizes(self):
        data = BGCODE.read_bytes()
        self.assertTrue(bgcode.is_binary(data))
        # Sizes straight from the block headers.
        stated = []
        pos = 10
        while pos + 8 <= len(data):
            btype, comp, usize = struct.unpack("<HHI", data[pos:pos + 8])
            pos += 8
            csize = usize
            if comp:
                (csize,) = struct.unpack("<I", data[pos:pos + 4])
                pos += 4
            pos += {0: 2, 1: 2, 2: 2, 3: 2, 4: 2, 5: 6}.get(btype, 0)
            pos += csize + 4
            if btype == bgcode.BLOCK_GCODE:
                stated.append((comp, usize))
        gcode_blocks = [(c, len(raw)) for t, c, _p, raw
                        in bgcode.blocks(data) if t == bgcode.BLOCK_GCODE]
        self.assertEqual(len(stated), 4)
        self.assertEqual(gcode_blocks, stated)
        self.assertTrue(all(c == 3 for c, _ in stated),
                        "the engine writes heatshrink 12/4")

    def test_decoded_binary_matches_the_engines_ascii_export(self):
        binary = _normalised_moves(bgcode.gcode_text(BGCODE.read_bytes()))
        ascii_ = _normalised_moves(ASCII.read_text(encoding="utf-8"))
        self.assertGreater(len(binary), 5000)
        self.assertEqual(binary, ascii_)
        text = bgcode.gcode_text(BGCODE.read_bytes())
        self.assertIn(";LAYER_CHANGE", text)
        self.assertIn(";TYPE:External perimeter", text)
        self.assertEqual(text.count(";LAYER_CHANGE"), 9)

    def test_not_binary_is_refused_plainly(self):
        with self.assertRaises(ValueError):
            list(bgcode.blocks(b"G1 X1\n"))


class ParseTests(unittest.TestCase):

    def test_bambu_gcode_3mf_layers_kinds_and_times(self):
        self.assertEqual(toolpath.plates_in_output(BAMBU), ["1"])
        tp = toolpath.read(BAMBU)
        self.assertEqual(len(tp.layers), 6)
        self.assertEqual([round(l.z, 2) for l in tp.layers],
                         [0.2, 0.4, 0.6, 0.8, 1.0, 1.2])
        self.assertTrue(all(abs(l.height - 0.2) < 1e-6 for l in tp.layers))
        self.assertEqual(tp.total_min, 175)
        first = tp.layers[0]
        self.assertGreater(first.segments, 2000)
        self.assertEqual(first.remaining_min, 169)
        kinds = {toolpath.KINDS[k][0] for k in first.ext_by_kind}
        self.assertIn("support", kinds)
        self.assertIn("skirt_brim", kinds)
        self.assertIn("outer_wall", kinds)
        # Travel and wipes are present but never counted as extrusion.
        self.assertGreater(first.travel_mm, 100)
        self.assertNotIn(toolpath.TRAVEL, first.ext_by_kind)
        self.assertNotIn(toolpath.WIPE, first.ext_by_kind)
        self.assertIn(toolpath.WIPE, set(first.kinds))
        # The Benchy sits around the plate centre.
        minx, miny, maxx, maxy = tp.bbox
        self.assertTrue(130 < minx < maxx < 205)
        self.assertTrue(115 < miny < maxy < 215)

    def test_arcs_become_chords(self):
        text = ("; CHANGE_LAYER\n; Z_HEIGHT: 0.2\n; LAYER_HEIGHT: 0.2\n"
                "; FEATURE: Outer wall\nG1 X10 Y0 E0\n"
                "G3 X0 Y10 I-10 J0 E1\n")   # a quarter circle, r=10
        tp = toolpath.parse(text)
        layer = tp.layers[0]
        self.assertGreaterEqual(layer.segments, 6)
        # Chords end exactly at the arc's end point.
        self.assertAlmostEqual(layer.xy[-2], 0.0, places=5)
        self.assertAlmostEqual(layer.xy[-1], 10.0, places=5)
        # Its length is the arc length, not the chord.
        self.assertAlmostEqual(layer.extrusion_mm, 15.708, places=2)

    def test_prusa_binary_and_ascii_parse_identically(self):
        binary = toolpath.read(BGCODE)
        ascii_ = toolpath.read(ASCII)
        self.assertEqual(len(binary.layers), 9)
        self.assertEqual(len(ascii_.layers), 9)
        self.assertEqual(binary.segments, ascii_.segments)
        for a, b in zip(binary.layers, ascii_.layers):
            self.assertAlmostEqual(a.z, b.z, places=4)
            self.assertAlmostEqual(a.extrusion_mm, b.extrusion_mm, places=2)
            self.assertEqual(sorted(a.ext_by_kind), sorted(b.ext_by_kind))
        kinds = {toolpath.KINDS[k][0] for l in binary.layers
                 for k in l.ext_by_kind}
        # PrusaSlicer's names map onto the shared kinds.
        self.assertIn("outer_wall", kinds)
        self.assertIn("inner_wall", kinds)
        self.assertIn("solid_infill", kinds)
        self.assertEqual(binary.total_min, 101)
        self.assertEqual(binary.layers[0].remaining_min, 101)
        # Travel and wipes never count as extrusion.
        for layer in binary.layers:
            self.assertNotIn(toolpath.TRAVEL, layer.ext_by_kind)
            self.assertNotIn(toolpath.WIPE, layer.ext_by_kind)
        self.assertIn(toolpath.TRAVEL, set(binary.layers[0].kinds))

    def test_absolute_extrusion_and_unknown_features(self):
        text = ("M82\n; CHANGE_LAYER\n; Z_HEIGHT: 0.3\n"
                "; FEATURE: Something new\nG1 X0 Y0 E0\n"
                "G1 X10 Y0 E1.5\nG1 X10 Y10 E1.5\nG92 E0\nG1 X0 Y10 E0.5\n")
        tp = toolpath.parse(text)
        layer = tp.layers[0]
        self.assertEqual(layer.height, 0.3)  # from the z step
        self.assertEqual(list(layer.kinds), [toolpath.OTHER, toolpath.TRAVEL,
                                             toolpath.OTHER])
        self.assertAlmostEqual(layer.extrusion_mm, 20.0)
        self.assertAlmostEqual(layer.travel_mm, 10.0)

    def test_payload_layout_and_travel_dropping(self):
        tp = toolpath.read(BAMBU)
        blob = toolpath.payload(tp)
        self.assertEqual(blob[:4], toolpath.MAGIC)
        (count,) = struct.unpack("<I", blob[4:8])
        self.assertEqual(count, 6)
        pos = 8
        for layer in tp.layers:
            z, h, n = struct.unpack("<ffI", blob[pos:pos + 12])
            self.assertAlmostEqual(z, layer.z, places=4)
            self.assertEqual(n, layer.segments)
            pos += 12 + n * 16 + n + (-n % 4)
        self.assertEqual(pos, len(blob))
        lean = toolpath.payload(tp, include_travel=False)
        self.assertLess(len(lean), len(blob))
        (_z, _h, n0) = struct.unpack("<ffI", lean[8:20])
        self.assertEqual(n0, sum(1 for k in tp.layers[0].kinds
                                 if k not in (toolpath.TRAVEL,
                                              toolpath.WIPE)))

    def test_facts_and_layer_sentence(self):
        tp = toolpath.read(BAMBU)
        f = toolpath.facts(tp, plate_size=(330.0, 320.0))
        self.assertEqual(f["layer_count"], 6)
        self.assertEqual(f["top_z"], 1.2)
        self.assertEqual(f["layer_heights"], [0.2])
        self.assertEqual(f["plate_size"], [330.0, 320.0])
        self.assertEqual(f["total_min"], 175)
        self.assertEqual(len(f["layers"]), 6)
        self.assertEqual(f["layers"][0]["number"], 1)
        self.assertEqual(f["layers"][0]["start_min"], 6)
        self.assertEqual(f["totals"][0]["name"], "Support")
        names = [k["name"] for k in f["kinds"]]
        self.assertIn("Outer wall", names)
        sentence = webui.layer_sentence(f, 1)
        self.assertTrue(sentence.startswith(
            "Layer 1 of 6, top at 0.2 mm, 0.2 mm thick."))
        self.assertIn("Starts about 6 minutes into the print.", sentence)
        self.assertIn("of extrusion - support", sentence)
        self.assertIn("of travel.", sentence)
        self.assertEqual(toolpath.friendly_mm(2500), "2.5 m")
        self.assertEqual(toolpath.friendly_mm(42.4), "42 mm")

    def test_plate_choice_inside_a_multi_plate_output(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Metadata/plate_1.gcode",
                       "; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG1 X1 Y1 E1\n")
            z.writestr("Metadata/plate_3.gcode",
                       "; CHANGE_LAYER\n; Z_HEIGHT: 0.3\n"
                       "; CHANGE_LAYER\n; Z_HEIGHT: 0.6\n")
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        path = sandbox / "two.gcode.3mf"
        path.write_bytes(buf.getvalue())
        self.assertEqual(toolpath.plates_in_output(path), ["1", "3"])
        self.assertEqual(len(toolpath.read(path).layers), 1)
        self.assertEqual(len(toolpath.read(path, "3").layers), 2)
        with self.assertRaises(ValueError):
            toolpath.read(path, "2")


class CacheTests(unittest.TestCase):

    def test_parse_runs_once_per_file_and_plate(self):
        cache = preview.PreviewCache()
        entry = cache.ensure(BAMBU, "1", (330.0, 320.0))
        self.assertIn(entry["status"], ("working", "ready"))
        for _ in range(100):
            if entry["status"] != "working":
                break
            time.sleep(0.1)
        self.assertEqual(entry["status"], "ready", entry["error"])
        self.assertEqual(entry["facts"]["layer_count"], 6)
        self.assertTrue(entry["facts"]["travel_drawn"])
        self.assertEqual(entry["payload"][:4], toolpath.MAGIC)
        self.assertIs(cache.ensure(BAMBU, "1"), entry)
        self.assertIs(cache.current(BAMBU, "1"), entry)
        self.assertIsNone(cache.current(BAMBU, "2"))
        cache.reset()
        self.assertIsNone(cache.entry)

    def test_an_unreadable_file_fails_in_words(self):
        sandbox = support.make_sandbox()
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        bad = sandbox / "bad.gcode.3mf"
        bad.write_bytes(b"not a zip at all")
        cache = preview.PreviewCache()
        entry = cache.ensure(bad, "1")
        for _ in range(100):
            if entry["status"] != "working":
                break
            time.sleep(0.1)
        self.assertEqual(entry["status"], "failed")
        self.assertTrue(entry["error"])


class FakeJob:
    """What the preview needs of a finished slice job."""

    def __init__(self, out, status="done", plate=0):
        self._out = out
        self.status = status
        self.plate = plate

    def output_file(self):
        return self._out


class H:
    httpd = thread = port = sandbox = old_config_path = old_appdata = None


class PreviewPageTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        H.sandbox = support.make_sandbox()
        projects_dir = H.sandbox / "projects"
        projects_dir.mkdir()
        cls.project = support.copy_test_project(projects_dir)
        fake_exe = support.make_fake_studio(H.sandbox)
        H.old_config_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = H.sandbox / "config.json"
        config_store.save({"slicer": "bambu", "slicer_exe": str(fake_exe),
                           "projects_dir": str(projects_dir)})
        H.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(H.sandbox / "AppData")
        server.state["session"] = None
        server.state["slice_job"] = None
        server.state["preview"].reset()
        H.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        H.port = H.httpd.server_address[1]
        H.thread = threading.Thread(target=H.httpd.serve_forever,
                                    daemon=True)
        H.thread.start()

    @classmethod
    def tearDownClass(cls):
        if H.httpd:
            H.httpd.shutdown()
            H.httpd.server_close()
        server.state["session"] = None
        server.state["slice_job"] = None
        server.state["preview"].reset()
        server.state["watcher"].stop()
        config_store.CONFIG_PATH = H.old_config_path
        if H.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = H.old_appdata
        shutil.rmtree(H.sandbox, ignore_errors=True)

    def request(self, method, url, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", H.port, timeout=60)
        try:
            body = urllib.parse.urlencode(form) if form else None
            headers = {}
            if body is not None:
                headers = {"Content-Type": "application/x-www-form-urlencoded",
                           "Content-Length": str(len(body))}
            conn.request(method, url, body, headers)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def wait_ready(self, url):
        for _ in range(100):
            status, headers, body = self.request("GET", url)
            if b"Preparing the sliced preview" not in body:
                return status, headers, body
            time.sleep(0.1)
        self.fail("the preview never became ready")

    def test_the_preview_in_every_state(self):
        status, _h, _b = self.request("POST", "/open",
                                      {"path": str(self.project)})
        self.assertEqual(status, 303)
        # No slice yet: an honest redirect, and honest JSON.
        status, headers, _b = self.request("GET", "/project/preview")
        self.assertEqual(status, 303)
        self.assertIn("Slice%20the%20project%20first", headers["Location"])
        _s, _h, body = self.request("GET", "/api/toolpath")
        self.assertEqual(json.loads(body)["error"], "Nothing has been sliced yet.")

        # A finished slice (the trimmed real output stands in).
        server.state["slice_job"] = FakeJob(BAMBU)
        server.state["preview"].reset()
        status, _h, body = self.request("GET", "/project/preview")
        self.assertEqual(status, 200)
        # Either still preparing (self-refreshing, no JS needed) or
        # already ready on a quick machine.
        if b"Preparing the sliced preview" in body:
            self.assertIn(b'<meta http-equiv="refresh" content="2">', body)
        status, _h, body = self.wait_ready("/project/preview")
        page = body.decode("utf-8")
        self.assertIn("Sliced preview - blind-benchy.3mf", page)
        self.assertIn("6 layers, the top one ending at 1.2 mm.", page)
        self.assertIn("The whole print takes about 2 hours 55 minutes", page)
        self.assertIn("Where the filament goes", page)
        self.assertIn("<li>Support: ", page)
        # The layer facts default to the top layer, in words.
        self.assertIn("Layer 6 of 6, top at 1.2 mm, 0.2 mm thick.", page)
        # Real controls, the canvas hidden from screen readers.
        self.assertIn('<input type="range" id="layer-slider" min="1" max="6"',
                      page)
        self.assertIn('<input type="number" id="layer-number" name="layer" '
                      'min="1"', page)
        self.assertIn('<input type="checkbox" id="show-travel">', page)
        self.assertIn('class="kind-toggle" value="outer_wall" checked', page)
        self.assertNotIn('class="kind-toggle" value="ironing"', page)
        self.assertIn('<div class="view-stage" id="preview-stage" '
                      'aria-hidden="true"></div>', page)
        self.assertIn('<script src="/static/preview.js"></script>', page)
        self.assertIn('<main class="wide">', page)
        # The facts come before the picture.
        self.assertLess(page.index("Layer by layer"),
                        page.index('id="preview-panel"'))

        # A chosen layer, without JavaScript: the GET form.
        _s, _h, body = self.request("GET", "/project/preview?layer=2")
        self.assertIn(b"Layer 2 of 6, top at 0.4 mm", body)
        self.assertIn(b'value="2"', body)
        _s, _h, body = self.request("GET", "/project/preview?layer=99")
        self.assertIn(b"Layer 6 of 6", body)

        # The data the script draws from.
        status, headers, blob = self.request("GET", "/api/toolpath?plate=1")
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(blob[:4], toolpath.MAGIC)
        _s, _h, body = self.request("GET", "/api/toolpath-facts?plate=1")
        facts = json.loads(body)
        self.assertEqual(facts["layer_count"], 6)
        self.assertEqual(facts["plate_size"], [330.0, 320.0])
        self.assertEqual(len(facts["kinds"]), len(toolpath.KINDS))

        # The report page links here.
        server.state["slice_job"] = ReportJob(BAMBU)
        _s, _h, body = self.request("GET", "/project/slice-report")
        self.assertIn(b'href="/project/preview"', body)

        # A PrusaSlicer binary output previews the same way.
        server.state["slice_job"] = FakeJob(BGCODE)
        server.state["preview"].reset()
        _s, _h, body = self.wait_ready("/project/preview")
        self.assertIn(b"9 layers, the top one ending at 1.8 mm.", body)
        self.assertIn(b"Outer wall", body)

        # An unreadable sliced file: the reason in words, no crash.
        bad = H.sandbox / "bad.gcode.3mf"
        bad.write_bytes(b"PK fake gcode 3mf")
        server.state["slice_job"] = FakeJob(bad)
        server.state["preview"].reset()
        status, _h, body = self.request("GET", "/project/preview")
        # plates_in_output fails on a non-zip -> nothing to preview.
        self.assertEqual(status, 303)
        for name in ("preview.js",):
            status, headers, body = self.request("GET", f"/static/{name}")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "text/javascript")


class ReportJob(FakeJob):
    """Enough of a finished SliceJob for the report page to render."""

    def __init__(self, out):
        super().__init__(out)
        self.report = {"ok": True, "plates": [], "total_time_s": 60.0,
                       "total_grams": 1.0, "warnings": []}
        self.out_dir = out.parent

    def elapsed(self):
        return 3


if __name__ == "__main__":
    unittest.main()
