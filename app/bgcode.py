"""bgcode.py - reading PrusaSlicer's binary G-code back into text.

The Core One asks for binary G-code (binary_gcode = 1), so what the
PrusaSlicer engine writes is a .bgcode file: a GCDE header, then
blocks - file/printer/print metadata, thumbnails, and the G-code
itself in blocks of a few tens of kilobytes each. prusa_engine reads
the metadata blocks for the slice report; this module decodes the
G-CODE blocks so the sliced preview can draw the toolpaths and count
the layers, without asking the engine for a second, ASCII export.

Two layers to undo, both from the libbgcode specification:

- Compression 3 = heatshrink with an 11-bit window and 4-bit
  lookahead, compression 2 = heatshrink 12/4 (the encoder's choice;
  the block header says which), compression 1 = Deflate. Heatshrink
  is a tiny LZSS: a stream of bits, MSB first, where a 1 bit
  announces a literal byte and a 0 bit a back-reference made of a
  (window-bits) offset and a (lookahead-bits) length, both stored one
  less than their value.
- Encoding 1 and 2 = MeatPack: two common G-code characters per byte
  (digits, the point, space or E, newline, G and X), a 0xF nibble
  meaning "the next whole byte is the character", and 0xFF 0xFF
  command sequences switching packing and the no-spaces table.

Pure stdlib; verified against a real Core One Benchy: the decoded
text equals the engine's own ASCII export line for line
(tests\\test_toolpath.py carries the trimmed pair).
"""

import struct
import zlib

BLOCK_FILE_METADATA = 0
BLOCK_GCODE = 1
BLOCK_PRINTER_METADATA = 3
BLOCK_PRINT_METADATA = 4
BLOCK_THUMBNAIL = 5

# MeatPack command bytes (libbgcode meatpack.cpp).
_MP_SIGNAL = 0xFF
_MP_ENABLE_PACKING = 0xFB
_MP_DISABLE_PACKING = 0xFA
_MP_RESET_ALL = 0xF9
_MP_QUERY_CONFIG = 0xF8
_MP_ENABLE_NO_SPACES = 0xF7
_MP_DISABLE_NO_SPACES = 0xF6

# The packed table: code 0xB is the space normally; in "no spaces"
# mode the spaces are dropped from the stream and E takes the slot.
_TABLE = b"0123456789. \nGX\0"
_TABLE_NO_SPACES = b"0123456789.E\nGX\0"


def heatshrink_decode(data, window_bits, lookahead_bits):
    """Decompress one heatshrink stream (window/lookahead as the block
    header states). A straightforward, reasonably quick bit reader:
    an integer accumulator refilled a byte at a time."""
    out = bytearray()
    acc = 0
    nbits = 0
    pos = 0
    n = len(data)
    mask_w = (1 << window_bits) - 1
    mask_l = (1 << lookahead_bits) - 1
    total_bits = window_bits + lookahead_bits
    append = out.append
    extend = out.extend
    while True:
        # Need one tag bit.
        if nbits < 1:
            if pos >= n:
                break
            acc = (acc << 8) | data[pos]
            pos += 1
            nbits += 8
        nbits -= 1
        tag = (acc >> nbits) & 1
        if tag:
            # Literal: 8 bits.
            while nbits < 8:
                if pos >= n:
                    return bytes(out)
                acc = (acc << 8) | data[pos]
                pos += 1
                nbits += 8
            nbits -= 8
            append((acc >> nbits) & 0xFF)
        else:
            while nbits < total_bits:
                if pos >= n:
                    return bytes(out)
                acc = (acc << 8) | data[pos]
                pos += 1
                nbits += 8
            nbits -= total_bits
            both = (acc >> nbits) & ((1 << total_bits) - 1)
            offset = ((both >> lookahead_bits) & mask_w) + 1
            count = (both & mask_l) + 1
            start = len(out) - offset
            if start < 0:
                # The window starts zero-filled, and packed G-code
                # contains real zero bytes (two '0' digits pack to
                # 0x00), so a reference before the start is legitimate
                # and reads zeros.
                zeros = min(-start, count)
                extend(b"\0" * zeros)
                count -= zeros
                start = len(out) - offset
            if count <= 0:
                pass
            elif count <= offset:
                extend(out[start:start + count])
            else:
                for _ in range(count):
                    append(out[start])
                    start += 1
        # Keep the accumulator small.
        acc &= (1 << nbits) - 1
    return bytes(out)


def meatpack_decode(data):
    """Unpack a MeatPack stream to plain G-code bytes."""
    out = bytearray()
    append = out.append
    unpacking = False
    table = _TABLE
    cmd_count = 0
    cmd_active = False
    full_pending = 0        # whole bytes still to read for a 0xF nibble
    after_full = None       # the second (packed) char to emit after them
    for c in data:
        if c == _MP_SIGNAL:
            if cmd_count > 0:
                cmd_active = True
                cmd_count = 0
            else:
                cmd_count += 1
            continue
        if cmd_active:
            cmd_active = False
            if c == _MP_ENABLE_PACKING:
                unpacking = True
            elif c == _MP_DISABLE_PACKING:
                unpacking = False
            elif c == _MP_ENABLE_NO_SPACES:
                table = _TABLE_NO_SPACES
            elif c == _MP_DISABLE_NO_SPACES:
                table = _TABLE
            elif c == _MP_RESET_ALL:
                unpacking = False
                table = _TABLE
            # Query config and unknown commands: nothing to do.
            continue
        if cmd_count > 0:
            # A single 0xFF followed by a data byte: a packed byte whose
            # two nibbles are both escapes - two whole bytes follow.
            cmd_count = 0
            full_pending = 2
            after_full = None
        if full_pending > 0:
            append(c)
            full_pending -= 1
            if full_pending == 0 and after_full is not None:
                append(after_full)
                after_full = None
            continue
        if not unpacking:
            append(c)
            continue
        lo = c & 0x0F
        hi = (c >> 4) & 0x0F
        if lo == 0x0F and hi == 0x0F:
            full_pending = 2
        elif lo == 0x0F:
            full_pending = 1
            after_full = table[hi]
        elif hi == 0x0F:
            append(table[lo])
            full_pending = 1
        else:
            append(table[lo])
            append(table[hi])
    return bytes(out)


def blocks(data):
    """Yield (type, compression, parameters bytes, raw uncompressed
    payload) for every block of a binary G-code file. Raises
    ValueError when the header is not GCDE."""
    if data[:4] != b"GCDE":
        raise ValueError("not a binary G-code file")
    _version, checksum_type = struct.unpack("<IH", data[4:10])
    crc_len = 4 if checksum_type == 1 else 0
    pos = 10
    n = len(data)
    while pos + 8 <= n:
        btype, comp, usize = struct.unpack("<HHI", data[pos:pos + 8])
        pos += 8
        csize = usize
        if comp != 0:
            if pos + 4 > n:
                break
            (csize,) = struct.unpack("<I", data[pos:pos + 4])
            pos += 4
        plen = {0: 2, 1: 2, 2: 2, 3: 2, 4: 2, 5: 6}.get(btype, 0)
        params = data[pos:pos + plen]
        pos += plen
        blob = data[pos:pos + csize]
        pos += csize + crc_len
        if comp == 0:
            raw = bytes(blob)
        elif comp == 1:
            try:
                raw = zlib.decompress(blob)
            except zlib.error:
                raw = zlib.decompress(blob, -15)
        elif comp == 2:
            raw = heatshrink_decode(blob, 11, 4)
        elif comp == 3:
            raw = heatshrink_decode(blob, 12, 4)
        else:
            continue
        yield btype, comp, params, raw


def gcode_text(data):
    """The whole G-code of a .bgcode file as text, decoded block by
    block (encoding read from each block's parameters)."""
    parts = []
    for btype, _comp, params, raw in blocks(data):
        if btype != BLOCK_GCODE:
            continue
        encoding = struct.unpack("<H", params[:2])[0] if len(params) >= 2 else 0
        if encoding in (1, 2):
            raw = meatpack_decode(raw)
        parts.append(raw)
    return b"".join(parts).decode("utf-8", "replace")


def is_binary(data):
    return data[:4] == b"GCDE"
