"""Minimal baseline JPEG encoder — pure Python, stdlib only.

Implements a baseline (sequential, Huffman-coded, 8-bit) JPEG encoder
with standard quantization and Huffman tables.  RGB input uses YCbCr
conversion with 4:2:0 chroma subsampling.  Grayscale input produces a
single-component JPEG.

This encoder prioritises correctness and simplicity over speed.
"""

from __future__ import annotations

import math
import struct

# ---------------------------------------------------------------------------
# Standard tables from JPEG specification (ITU-T T.81, Annex K)
# ---------------------------------------------------------------------------

# Luminance quantization table (Table K.1)
_LUMA_QUANT = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]

# Chrominance quantization table (Table K.2)
_CHROMA_QUANT = [
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
]

# Zigzag order for 8x8 block
_ZIGZAG = [
    0,  1,  8, 16,  9,  2,  3, 10,
    17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]

# Standard DC Huffman tables (Annex K, Table K.3 / K.4)
_DC_LUMA_BITS = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
_DC_LUMA_VALS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

_DC_CHROMA_BITS = [0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
_DC_CHROMA_VALS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

# Standard AC Huffman tables (Annex K, Table K.5 / K.6)
_AC_LUMA_BITS = [0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 125]
_AC_LUMA_VALS = [
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12,
    0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
    0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
    0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0,
    0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16,
    0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
    0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
    0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
    0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98,
    0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7,
    0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
    0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5,
    0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4,
    0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
    0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA,
    0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8,
    0xF9, 0xFA,
]

_AC_CHROMA_BITS = [0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 119]
_AC_CHROMA_VALS = [
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21,
    0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
    0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xA1, 0xB1, 0xC1, 0x09, 0x23, 0x33, 0x52, 0xF0,
    0x15, 0x62, 0x72, 0xD1, 0x0A, 0x16, 0x24, 0x34,
    0xE1, 0x25, 0xF1, 0x17, 0x18, 0x19, 0x1A, 0x26,
    0x27, 0x28, 0x29, 0x2A, 0x35, 0x36, 0x37, 0x38,
    0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
    0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78,
    0x79, 0x7A, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96,
    0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5,
    0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4,
    0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3,
    0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2,
    0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA,
    0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9,
    0xEA, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8,
    0xF9, 0xFA,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scale_quant_table(base: list[int], quality: int) -> list[int]:
    """Scale a quantization table by *quality* (1–100)."""
    quality = max(1, min(100, quality))
    if quality < 50:
        scale = 5000 // quality
    else:
        scale = 200 - quality * 2
    return [max(1, min(255, (v * scale + 50) // 100)) for v in base]


def _build_huffman_table(bits: list[int], vals: list[int]) -> dict[int, tuple[int, int]]:
    """Build {symbol: (code, length)} from JPEG bits/vals specification."""
    table: dict[int, tuple[int, int]] = {}
    code = 0
    vi = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            table[vals[vi]] = (code, length)
            vi += 1
            code += 1
        code <<= 1
    return table


# Pre-built Huffman lookup tables
_DC_LUMA_HT = _build_huffman_table(_DC_LUMA_BITS, _DC_LUMA_VALS)
_DC_CHROMA_HT = _build_huffman_table(_DC_CHROMA_BITS, _DC_CHROMA_VALS)
_AC_LUMA_HT = _build_huffman_table(_AC_LUMA_BITS, _AC_LUMA_VALS)
_AC_CHROMA_HT = _build_huffman_table(_AC_CHROMA_BITS, _AC_CHROMA_VALS)


# Precompute cosine table for DCT
_COS_TABLE: list[float] = []
for _u in range(8):
    for _x in range(8):
        _COS_TABLE.append(math.cos((2 * _x + 1) * _u * math.pi / 16))


def _dct8x8(block: list[int]) -> list[float]:
    """Forward DCT on an 8x8 block (level-shifted by -128)."""
    result = [0.0] * 64
    for u in range(8):
        cu = 1.0 / math.sqrt(2) if u == 0 else 1.0
        for v in range(8):
            cv = 1.0 / math.sqrt(2) if v == 0 else 1.0
            s = 0.0
            for x in range(8):
                cos_u = _COS_TABLE[u * 8 + x]
                for y in range(8):
                    s += (block[x * 8 + y] - 128) * cos_u * _COS_TABLE[v * 8 + y]
            result[u * 8 + v] = s * 0.25 * cu * cv
    return result


def _quantize(dct: list[float], qtable: list[int]) -> list[int]:
    """Quantize DCT coefficients and reorder in zigzag."""
    zigzag = [0] * 64
    for i in range(64):
        zigzag[i] = round(dct[_ZIGZAG[i]] / qtable[_ZIGZAG[i]])
    return zigzag


# ---------------------------------------------------------------------------
# Bitstream writer
# ---------------------------------------------------------------------------

class _BitWriter:
    """Accumulate bits and emit bytes with JPEG byte-stuffing."""

    __slots__ = ("buf", "_bits", "_nbits")

    def __init__(self) -> None:
        self.buf = bytearray()
        self._bits = 0
        self._nbits = 0

    def write(self, code: int, length: int) -> None:
        self._bits = (self._bits << length) | code
        self._nbits += length
        while self._nbits >= 8:
            self._nbits -= 8
            byte = (self._bits >> self._nbits) & 0xFF
            self.buf.append(byte)
            if byte == 0xFF:
                self.buf.append(0x00)  # byte stuffing

    def flush(self) -> None:
        if self._nbits > 0:
            # Pad remaining bits with 1s
            self.write(0x7F, 7 - (self._nbits - 1) % 8 + (self._nbits - 1) % 8 % 1)
            # Simpler: pad to byte boundary with 1-bits
            pad = 8 - self._nbits % 8
            if pad < 8:
                self._bits = (self._bits << pad) | ((1 << pad) - 1)
                self._nbits += pad
            while self._nbits >= 8:
                self._nbits -= 8
                byte = (self._bits >> self._nbits) & 0xFF
                self.buf.append(byte)
                if byte == 0xFF:
                    self.buf.append(0x00)


def _encode_value(val: int) -> tuple[int, int]:
    """Return (category, bit_pattern) for a DC/AC coefficient value."""
    if val == 0:
        return 0, 0
    abs_val = abs(val)
    cat = abs_val.bit_length()
    if val < 0:
        bits = val + (1 << cat) - 1
    else:
        bits = val
    return cat, bits


def _encode_block(
    bw: _BitWriter,
    block: list[int],
    dc_prev: int,
    dc_ht: dict[int, tuple[int, int]],
    ac_ht: dict[int, tuple[int, int]],
) -> int:
    """Encode one 8x8 block (already quantized + zigzagged). Returns new DC value."""
    # DC coefficient (difference from previous)
    dc_diff = block[0] - dc_prev
    cat, bits = _encode_value(dc_diff)
    code, length = dc_ht[cat]
    bw.write(code, length)
    if cat > 0:
        bw.write(bits, cat)

    # AC coefficients
    zero_run = 0
    for i in range(1, 64):
        if block[i] == 0:
            zero_run += 1
        else:
            while zero_run >= 16:
                code, length = ac_ht[0xF0]  # ZRL
                bw.write(code, length)
                zero_run -= 16
            cat, bits = _encode_value(block[i])
            symbol = (zero_run << 4) | cat
            code, length = ac_ht[symbol]
            bw.write(code, length)
            if cat > 0:
                bw.write(bits, cat)
            zero_run = 0

    if zero_run > 0:
        code, length = ac_ht[0x00]  # EOB
        bw.write(code, length)

    return block[0]


# ---------------------------------------------------------------------------
# Image block extraction
# ---------------------------------------------------------------------------

def _extract_block_gray(pixels: bytes, width: int, height: int,
                        bx: int, by: int) -> list[int]:
    """Extract 8x8 grayscale block, replicating edge pixels for padding."""
    block = [0] * 64
    for r in range(8):
        y = min(by + r, height - 1)
        for c in range(8):
            x = min(bx + c, width - 1)
            block[r * 8 + c] = pixels[y * width + x]
    return block


def _extract_block_channel(channel: list[int], width: int, height: int,
                           bx: int, by: int) -> list[int]:
    """Extract 8x8 block from a planar channel buffer."""
    block = [0] * 64
    for r in range(8):
        y = min(by + r, height - 1)
        for c in range(8):
            x = min(bx + c, width - 1)
            block[r * 8 + c] = channel[y * width + x]
    return block


def _rgb_to_ycbcr(pixels: bytes, width: int, height: int
                  ) -> tuple[list[int], list[int], list[int]]:
    """Convert interleaved RGB to planar Y, Cb, Cr channels."""
    n = width * height
    y_ch = [0] * n
    cb_ch = [0] * n
    cr_ch = [0] * n
    for i in range(n):
        off = i * 3
        r, g, b = pixels[off], pixels[off + 1], pixels[off + 2]
        y_ch[i] = max(0, min(255, round(0.299 * r + 0.587 * g + 0.114 * b)))
        cb_ch[i] = max(0, min(255, round(-0.1687 * r - 0.3313 * g + 0.5 * b + 128)))
        cr_ch[i] = max(0, min(255, round(0.5 * r - 0.4187 * g - 0.0813 * b + 128)))
    return y_ch, cb_ch, cr_ch


def _downsample_2x2(channel: list[int], width: int, height: int
                    ) -> tuple[list[int], int, int]:
    """Downsample a channel by 2x2 averaging (for 4:2:0)."""
    dw = (width + 1) // 2
    dh = (height + 1) // 2
    out = [0] * (dw * dh)
    for dy in range(dh):
        for dx in range(dw):
            sx, sy = dx * 2, dy * 2
            s = channel[sy * width + sx]
            n = 1
            if sx + 1 < width:
                s += channel[sy * width + sx + 1]
                n += 1
            if sy + 1 < height:
                s += channel[(sy + 1) * width + sx]
                n += 1
                if sx + 1 < width:
                    s += channel[(sy + 1) * width + sx + 1]
                    n += 1
            out[dy * dw + dx] = (s + n // 2) // n
    return out, dw, dh


# ---------------------------------------------------------------------------
# JPEG markers
# ---------------------------------------------------------------------------

def _marker(code: int, data: bytes = b"") -> bytes:
    if data:
        return struct.pack(">BBH", 0xFF, code, len(data) + 2) + data
    return struct.pack(">BB", 0xFF, code)


def _dqt_segment(table_id: int, qtable: list[int]) -> bytes:
    """Build a DQT marker segment for one 8-bit quantization table."""
    data = bytes([table_id]) + bytes(qtable[_ZIGZAG[i]] for i in range(64))
    return _marker(0xDB, data)


def _sof0_segment(width: int, height: int, components: list[tuple[int, int, int, int]]) -> bytes:
    """Build SOF0 marker. components: [(id, h_samp, v_samp, qt_id), ...]."""
    data = struct.pack(">BHH", 8, height, width)
    data += bytes([len(components)])
    for comp_id, h, v, qt in components:
        data += bytes([comp_id, (h << 4) | v, qt])
    return _marker(0xC0, data)


def _dht_segment(table_class: int, table_id: int,
                 bits: list[int], vals: list[int]) -> bytes:
    """Build a DHT marker segment."""
    data = bytes([(table_class << 4) | table_id])
    data += bytes(bits)
    data += bytes(vals)
    return _marker(0xC4, data)


def _sos_segment(component_ids: list[tuple[int, int, int]]) -> bytes:
    """Build SOS marker. component_ids: [(id, dc_ht_id, ac_ht_id), ...]."""
    data = bytes([len(component_ids)])
    for comp_id, dc_id, ac_id in component_ids:
        data += bytes([comp_id, (dc_id << 4) | ac_id])
    data += bytes([0, 63, 0])  # Ss, Se, Ah/Al
    return _marker(0xDA, data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encode_jpeg(
    pixels: bytes,
    width: int,
    height: int,
    color_type: int,
    quality: int = 85,
) -> bytes:
    """Encode raw pixels as baseline JPEG.

    *pixels* is raw pixel data: 1 byte/pixel for grayscale (color_type=0),
    3 bytes/pixel RGB for color (color_type=2).

    Returns complete JFIF file bytes.
    """
    luma_qt = _scale_quant_table(_LUMA_QUANT, quality)
    chroma_qt = _scale_quant_table(_CHROMA_QUANT, quality)

    out = bytearray()

    # SOI
    out += _marker(0xD8)

    # APP0 (JFIF)
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    out += _marker(0xE0, app0)

    if color_type == 0:
        # Grayscale
        out += _dqt_segment(0, luma_qt)
        out += _sof0_segment(width, height, [(1, 1, 1, 0)])
        out += _dht_segment(0, 0, _DC_LUMA_BITS, _DC_LUMA_VALS)
        out += _dht_segment(1, 0, _AC_LUMA_BITS, _AC_LUMA_VALS)
        out += _sos_segment([(1, 0, 0)])

        bw = _BitWriter()
        dc_prev = 0
        for by in range(0, height, 8):
            for bx in range(0, width, 8):
                block = _extract_block_gray(pixels, width, height, bx, by)
                dct = _dct8x8(block)
                qblock = _quantize(dct, luma_qt)
                dc_prev = _encode_block(bw, qblock, dc_prev, _DC_LUMA_HT, _AC_LUMA_HT)
        bw.flush()
        out += bw.buf
    else:
        # RGB → YCbCr 4:2:0
        y_ch, cb_ch, cr_ch = _rgb_to_ycbcr(pixels, width, height)
        cb_ds, cb_w, cb_h = _downsample_2x2(cb_ch, width, height)
        cr_ds, cr_w, cr_h = _downsample_2x2(cr_ch, width, height)

        out += _dqt_segment(0, luma_qt)
        out += _dqt_segment(1, chroma_qt)
        out += _sof0_segment(width, height, [
            (1, 2, 2, 0),  # Y: 2x2 sampling
            (2, 1, 1, 1),  # Cb: 1x1
            (3, 1, 1, 1),  # Cr: 1x1
        ])
        out += _dht_segment(0, 0, _DC_LUMA_BITS, _DC_LUMA_VALS)
        out += _dht_segment(1, 0, _AC_LUMA_BITS, _AC_LUMA_VALS)
        out += _dht_segment(0, 1, _DC_CHROMA_BITS, _DC_CHROMA_VALS)
        out += _dht_segment(1, 1, _AC_CHROMA_BITS, _AC_CHROMA_VALS)
        out += _sos_segment([(1, 0, 0), (2, 1, 1), (3, 1, 1)])

        bw = _BitWriter()
        dc_y = dc_cb = dc_cr = 0

        # Process MCUs (16x16 pixel blocks for 4:2:0)
        for mcu_y in range(0, height, 16):
            for mcu_x in range(0, width, 16):
                # 4 Y blocks (2x2)
                for dy in range(2):
                    for dx in range(2):
                        block = _extract_block_channel(
                            y_ch, width, height,
                            mcu_x + dx * 8, mcu_y + dy * 8,
                        )
                        dct = _dct8x8(block)
                        qblock = _quantize(dct, luma_qt)
                        dc_y = _encode_block(bw, qblock, dc_y, _DC_LUMA_HT, _AC_LUMA_HT)

                # 1 Cb block
                block = _extract_block_channel(
                    cb_ds, cb_w, cb_h, mcu_x // 2, mcu_y // 2,
                )
                dct = _dct8x8(block)
                qblock = _quantize(dct, chroma_qt)
                dc_cb = _encode_block(bw, qblock, dc_cb, _DC_CHROMA_HT, _AC_CHROMA_HT)

                # 1 Cr block
                block = _extract_block_channel(
                    cr_ds, cr_w, cr_h, mcu_x // 2, mcu_y // 2,
                )
                dct = _dct8x8(block)
                qblock = _quantize(dct, chroma_qt)
                dc_cr = _encode_block(bw, qblock, dc_cr, _DC_CHROMA_HT, _AC_CHROMA_HT)

        bw.flush()
        out += bw.buf

    # EOI
    out += _marker(0xD9)
    return bytes(out)
