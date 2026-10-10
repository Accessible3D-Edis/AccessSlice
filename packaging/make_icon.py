"""make_icon.py - draw accessslice.ico from the CSS logo mark.

The app's logo mark is drawn in CSS (style.css, .app-mark): a rounded
accent-coloured square with two horizontal gaps, a plate with a layer
stack. This script draws the same mark as a multi-size Windows icon
so the program, its shortcuts and the installer carry it. Standard
library only: each icon size is a PNG entry (Windows Vista and later
read PNG-in-ICO), written by hand.

    python packaging\\make_icon.py

Re-run it when the accent colour changes; commit the result.
"""

import struct
import zlib
from pathlib import Path

ACCENT = (0x0B, 0x5E, 0xA8)   # --accent in style.css (light theme)
WHITE = (255, 255, 255)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _png(size, pixels):
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px)
                   for row in pixels)

    def chunk(tag, data):
        body = struct.pack(">I", len(data)) + tag + data
        return body + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _mark(size):
    """The rounded square; the CSS gradient's transparent bands (30-40%
    and 70-80% of the height) are drawn white so the mark reads on any
    desktop background. Edges get a four-sample anti-alias."""
    radius = size * 0.18
    rows = []
    for y in range(size):
        fy = (y + 0.5) / size
        in_gap = 0.30 <= fy < 0.40 or 0.70 <= fy < 0.80
        row = []
        for x in range(size):
            cover = 0
            for sx, sy in ((0.25, 0.25), (0.75, 0.25),
                           (0.25, 0.75), (0.75, 0.75)):
                px, py = x + sx, y + sy
                cx = min(max(px, radius), size - radius)
                cy = min(max(py, radius), size - radius)
                if (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius:
                    cover += 1
            alpha = cover * 64 - 1 if cover else 0
            row.append((*(WHITE if in_gap else ACCENT), alpha))
        rows.append(row)
    return rows


def build(target):
    images = [_png(s, _mark(s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries = b""
    for size, img in zip(SIZES, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                               1, 32, len(img), offset)
        offset += len(img)
    Path(target).write_bytes(header + entries + b"".join(images))


if __name__ == "__main__":
    out = Path(__file__).with_name("accessslice.ico")
    build(out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
