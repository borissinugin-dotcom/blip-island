"""Pure-Python + NumPy QR code encoder (ISO/IEC 18004) and screen/image decoder.

No external QR libraries required; supports UTF-8 Byte-mode encoding up to
Version 10 (EC Level M) and decodes clean or slightly rotated QR images via
1:1:3:1:1 finder-pattern detection, perspective sampling, and GF(256)
Reed-Solomon error correction (with optional OpenCV/zxingcpp fast paths).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from PIL import Image
from PyQt6.QtGui import QImage

# GF(256) tables with primitive polynomial 0x11D
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _gf_div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("GF division by zero")
    if a == 0:
        return 0
    return _GF_EXP[(_GF_LOG[a] - _GF_LOG[b]) % 255]


def _gf_poly_mul(p: list[int], q: list[int]) -> list[int]:
    r = [0] * (len(p) + len(q) - 1)
    for j, qj in enumerate(q):
        for i, pi in enumerate(p):
            r[i + j] ^= _gf_mul(pi, qj)
    return r


def _gf_poly_eval(poly: list[int], x: int) -> int:
    y = poly[0]
    for coeff in poly[1:]:
        y = _gf_mul(y, x) ^ coeff
    return y


def _rs_generator(nsym: int) -> list[int]:
    g = [1]
    for i in range(nsym):
        g = _gf_poly_mul(g, [1, _GF_EXP[i]])
    return g


def _rs_encode(data: list[int], nsym: int) -> list[int]:
    gen = _rs_generator(nsym)
    res = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data):]


def _rs_decode(msg: list[int], nsym: int) -> list[int] | None:
    """Decode Reed-Solomon block using syndrome + Berlekamp-Massey + Chien + Forney."""
    if len(msg) <= nsym:
        return None
    synd = [_gf_poly_eval(msg, _GF_EXP[i]) for i in range(nsym)]
    if max(synd) == 0:
        return list(msg[:-nsym])

    # Berlekamp-Massey for error locator polynomial
    err_loc = [1]
    old_loc = [1]
    for i in range(nsym):
        delta = synd[i]
        for j in range(1, len(err_loc)):
            delta ^= _gf_mul(err_loc[-(j + 1)], synd[i - j])
        old_loc.append(0)
        if delta != 0:
            if len(old_loc) > len(err_loc):
                new_loc = [_gf_mul(c, delta) for c in old_loc]
                old_loc = [_gf_mul(c, _gf_div(1, delta)) for c in err_loc]
                err_loc = new_loc
            for j in range(len(old_loc)):
                err_loc[-(j + 1)] ^= _gf_mul(delta, old_loc[-(j + 1)])

    errs = len(err_loc) - 1
    if errs * 2 > nsym:
        return None

    # Chien search
    n = len(msg)
    err_pos: list[int] = []
    for i in range(n):
        if _gf_poly_eval(err_loc, _GF_EXP[i]) == 0:
            err_pos.append(n - 1 - i)
    if len(err_pos) != errs:
        return None

    # Error evaluator polynomial Omega(x) = (S(x) * Lambda(x)) mod x^nsym
    # Using reversed syndromes S_rev
    s_rev = list(reversed(synd))
    # Compute Forney directly on polynomial form with low-degree first
    lam = list(reversed(err_loc))  # lam[0] == 1
    omega = [0] * nsym
    for i in range(nsym):
        acc = 0
        for j in range(min(i + 1, len(lam))):
            acc ^= _gf_mul(lam[j], synd[i - j])
        omega[i] = acc

    # Formal derivative of lam (low degree first: odd powers shifted down)
    lam_prime = [lam[i] if (i % 2 == 1) else 0 for i in range(len(lam))]
    corrected = list(msg)
    for pos in err_pos:
        x_i = _GF_EXP[n - 1 - pos]
        x_inv = _gf_div(1, x_i)
        # Eval omega(x_inv) (low-degree first)
        num = 0
        xp = 1
        for c in omega:
            num ^= _gf_mul(c, xp)
            xp = _gf_mul(xp, x_inv)
        # Eval formal derivative: sum_{odd k} lam[k] * x_inv^(k-1)
        den = 0
        for k in range(1, len(lam_prime), 2):
            val = lam_prime[k]
            if val:
                xp2 = 1
                for _ in range(k - 1):
                    xp2 = _gf_mul(xp2, x_inv)
                den ^= _gf_mul(val, xp2)
        if den == 0:
            return None
        magnitude = _gf_mul(x_i, _gf_div(num, den))
        corrected[pos] ^= magnitude

    # Verify repaired syndromes
    if max(_gf_poly_eval(corrected, _GF_EXP[i]) for i in range(nsym)) != 0:
        return None
    return corrected[:-nsym]


# (total_codewords, ec_per_block, group1_blocks, group1_data, group2_blocks, group2_data) for EC Level M
_VERSION_M_SPECS: dict[int, tuple[int, int, int, int, int, int]] = {
    1: (26, 10, 1, 16, 0, 0),
    2: (44, 16, 1, 28, 0, 0),
    3: (70, 26, 1, 44, 0, 0),
    4: (100, 18, 2, 32, 0, 0),
    5: (134, 24, 2, 43, 0, 0),
    6: (172, 16, 4, 27, 0, 0),
    7: (196, 18, 4, 31, 0, 0),
    8: (242, 22, 2, 38, 2, 39),
    9: (292, 22, 3, 36, 2, 37),
    10: (346, 26, 4, 43, 1, 44),
}

# Standard alignment pattern centers per version
_ALIGN_POS: dict[int, list[int]] = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

# Version info 18-bit BCH codes for versions 7..10
_VERSION_INFO_BITS: dict[int, int] = {
    7: 0x07C94,
    8: 0x085BC,
    9: 0x09A99,
    10: 0x0A4D3,
}

# Precomputed 15-bit BCH format info (EC Level M -> indicator 00) masked with 0x5412
def _compute_format_bits(ec_bits: int, mask: int) -> int:
    data = (ec_bits << 3) | mask
    rem = data << 10
    gen = 0b10100110111
    for bit in range(14, 9, -1):
        if rem & (1 << bit):
            rem ^= gen << (bit - 10)
    raw = (data << 10) | rem
    return raw ^ 0x5412


_FORMAT_BITS_M = [_compute_format_bits(0b00, m) for m in range(8)]
_EC_LEVEL_INDICATORS = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}


def _encode_byte_payload(payload: bytes) -> tuple[int, list[int]]:
    """Choose smallest version (1..10 EC-M) fitting Byte-mode UTF-8 payload and return (version, interleaved_codewords)."""
    n_bytes = len(payload)
    chosen_ver = None
    for ver in range(1, 11):
        _, _, g1_b, g1_d, g2_b, g2_d = _VERSION_M_SPECS[ver]
        cap_bytes = g1_b * g1_d + g2_b * g2_d
        count_bits = 8 if ver < 10 else 16
        # header bits: 4 mode + count_bits + 8*len + up to 4 terminator
        if 4 + count_bits + 8 * n_bytes <= cap_bytes * 8:
            chosen_ver = ver
            break
    if chosen_ver is None:
        raise ValueError(f"QR payload too large ({n_bytes} bytes > 213 byte limit for Version 10-M)")

    _, ec_per_blk, g1_b, g1_d, g2_b, g2_d = _VERSION_M_SPECS[chosen_ver]
    total_data_cw = g1_b * g1_d + g2_b * g2_d

    bits: list[int] = []

    def push(val: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((val >> shift) & 1)

    push(0b0100, 4)  # Byte mode
    push(n_bytes, 8 if chosen_ver < 10 else 16)
    for b in payload:
        push(b, 8)
    # Terminator up to 4 zeros
    rem = total_data_cw * 8 - len(bits)
    bits.extend([0] * min(4, rem))
    while len(bits) % 8 != 0:
        bits.append(0)

    data_cw: list[int] = []
    for i in range(0, len(bits), 8):
        byte_val = 0
        for j in range(8):
            byte_val = (byte_val << 1) | bits[i + j]
        data_cw.append(byte_val)

    pad_bytes = (0xEC, 0x11)
    p_idx = 0
    while len(data_cw) < total_data_cw:
        data_cw.append(pad_bytes[p_idx & 1])
        p_idx += 1

    # Split into blocks and compute RS parity
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    cursor = 0
    for count, dlen in ((g1_b, g1_d), (g2_b, g2_d)):
        for _ in range(count):
            blk = data_cw[cursor:cursor + dlen]
            cursor += dlen
            data_blocks.append(blk)
            ec_blocks.append(_rs_encode(blk, ec_per_blk))

    # Interleave data then EC codewords
    interleaved: list[int] = []
    max_d = max(len(b) for b in data_blocks)
    for col in range(max_d):
        for blk in data_blocks:
            if col < len(blk):
                interleaved.append(blk[col])
    for col in range(ec_per_blk):
        for blk in ec_blocks:
            interleaved.append(blk[col])

    return chosen_ver, interleaved


def _build_base_matrix(version: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (matrix, reserved_mask) with fixed patterns placed."""
    size = 17 + 4 * version
    mat = np.zeros((size, size), dtype=np.int8)
    reserved = np.zeros((size, size), dtype=bool)

    def set_mod(r: int, c: int, val: int) -> None:
        if 0 <= r < size and 0 <= c < size:
            mat[r, c] = val
            reserved[r, c] = True

    def place_finder(top: int, left: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = top + dr, left + dc
                if not (0 <= r < size and 0 <= c < size):
                    continue
                if dr in (-1, 7) or dc in (-1, 7):
                    set_mod(r, c, 0)
                elif dr in (0, 6) or dc in (0, 6):
                    set_mod(r, c, 1)
                elif 2 <= dr <= 4 and 2 <= dc <= 4:
                    set_mod(r, c, 1)
                else:
                    set_mod(r, c, 0)

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    # Alignment patterns
    centers = _ALIGN_POS[version]
    for cy in centers:
        for cx in centers:
            if (cy < 9 and cx < 9) or (cy < 9 and cx > size - 10) or (cy > size - 10 and cx < 9):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    val = 1 if (abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0)) else 0
                    set_mod(cy + dr, cx + dc, val)

    # Timing strips
    for i in range(8, size - 8):
        val = 1 if (i % 2 == 0) else 0
        if not reserved[6, i]:
            set_mod(6, i, val)
        if not reserved[i, 6]:
            set_mod(i, 6, val)

    # Dark module
    set_mod(4 * version + 9, 8, 1)

    # Reserve format info strips
    for i in range(9):
        if not reserved[8, i]:
            reserved[8, i] = True
        if not reserved[i, 8]:
            reserved[i, 8] = True
    for i in range(size - 8, size):
        reserved[8, i] = True
    for i in range(size - 7, size):
        reserved[i, 8] = True

    # Version info for V >= 7
    if version >= 7:
        vbits = _VERSION_INFO_BITS[version]
        for i in range(18):
            bit = (vbits >> i) & 1
            r = i // 3
            c = size - 11 + (i % 3)
            set_mod(r, c, bit)
            set_mod(c, r, bit)

    return mat, reserved


def _apply_format(mat: np.ndarray, fmt_bits: int) -> None:
    size = mat.shape[0]
    # Top-left format strip
    coords1 = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    for i, (r, c) in enumerate(coords1):
        mat[r, c] = (fmt_bits >> (14 - i)) & 1

    # Split format strips (bottom-left and top-right)
    for i in range(7):
        mat[size - 1 - i, 8] = (fmt_bits >> (14 - i)) & 1
    for i in range(8):
        mat[8, size - 8 + i] = (fmt_bits >> (7 - i)) & 1


def _mask_fn(mask: int, r: int, c: int) -> bool:
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return ((r * c) % 2 + (r * c) % 3) == 0
    if mask == 6:
        return (((r * c) % 2 + (r * c) % 3) % 2) == 0
    return (((r + c) % 2 + (r * c) % 3) % 2) == 0


def _penalty_score(mat: np.ndarray) -> int:
    n = mat.shape[0]
    score = 0
    # Rule 1: adjacent same-color modules in rows/cols
    for axis_mat in (mat, mat.T):
        for row in axis_mat:
            run = 1
            for k in range(1, n):
                if row[k] == row[k - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run = 1
            if run >= 5:
                score += 3 + (run - 5)
    # Rule 2: 2x2 blocks
    for r in range(n - 1):
        for c in range(n - 1):
            v = mat[r, c]
            if v == mat[r, c + 1] == mat[r + 1, c] == mat[r + 1, c + 1]:
                score += 3
    # Rule 3: finder-like 1:1:3:1:1 patterns
    p1 = (1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0)
    p2 = (0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1)
    for axis_mat in (mat, mat.T):
        for row in axis_mat:
            tup = tuple(int(v) for v in row)
            for k in range(n - 10):
                seg = tup[k:k + 11]
                if seg == p1 or seg == p2:
                    score += 40
    # Rule 4: dark ratio deviation from 50%
    dark = int(np.sum(mat))
    pct = (dark * 100.0) / (n * n)
    score += int(min(abs(pct - (int(pct // 5) * 5) - 50) // 5, abs(pct - ((int(pct // 5) + 1) * 5) - 50) // 5)) * 10
    return score


def encode_qr_matrix(text: str) -> np.ndarray:
    """Encode string into a boolean/0-1 numpy matrix (1 = dark module)."""
    payload = text.encode("utf-8")
    ver, codewords = _encode_byte_payload(payload)
    base, reserved = _build_base_matrix(ver)
    size = base.shape[0]

    bitstream: list[int] = []
    for cw in codewords:
        for s in range(7, -1, -1):
            bitstream.append((cw >> s) & 1)

    # Zig-zag bit coordinates
    data_coords: list[tuple[int, int]] = []
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not reserved[r, c]:
                    data_coords.append((r, c))
        upward = not upward
        col -= 2

    best_mat = None
    best_pen = 1 << 30
    for mask in range(8):
        cand = base.copy()
        for idx, (r, c) in enumerate(data_coords):
            bit = bitstream[idx] if idx < len(bitstream) else 0
            cand[r, c] = bit ^ (1 if _mask_fn(mask, r, c) else 0)
        _apply_format(cand, _FORMAT_BITS_M[mask])
        pen = _penalty_score(cand)
        if pen < best_pen:
            best_pen = pen
            best_mat = cand
    assert best_mat is not None
    return best_mat


def render_qr_pil(text: str, box_size: int = 8, border: int = 4) -> Image.Image:
    """Render QR code into a crisp black-on-white RGB PIL Image."""
    mat = encode_qr_matrix(text)
    padded = np.pad(mat, border, mode="constant", constant_values=0)
    gray = np.where(padded == 1, 0, 255).astype(np.uint8)
    scaled = np.repeat(np.repeat(gray, box_size, axis=0), box_size, axis=1)
    return Image.fromarray(scaled, mode="L").convert("RGB")


def render_qr_qimage(text: str, box_size: int = 8, border: int = 4) -> QImage:
    """Render QR code into a PyQt6 QImage."""
    pil_img = render_qr_pil(text, box_size=box_size, border=border).convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    return QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888).copy()


def render_qr_svg(text: str, box_size: int = 8, border: int = 4) -> str:
    """Render QR code into clean SVG markup."""
    mat = encode_qr_matrix(text)
    n = mat.shape[0]
    dim = (n + 2 * border) * box_size
    rects = [
        f'<rect x="{(c + border) * box_size}" y="{(r + border) * box_size}" '
        f'width="{box_size}" height="{box_size}" fill="#000"/>'
        for r in range(n)
        for c in range(n)
        if mat[r, c] == 1
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" viewBox="0 0 {dim} {dim}">'
        f'<rect width="100%" height="100%" fill="#fff"/>'
        + "".join(rects)
        + "</svg>"
    )


# ------------------------------------------------------------------ QR Decoder
# Standard EC tables (total_cw, ec_per_blk, g1_b, g1_d, g2_b, g2_d) for decoding all 4 EC levels
_EC_SPECS_V1_V10: dict[tuple[int, str], tuple[int, int, int, int, int, int]] = {
    (1, "L"): (26, 7, 1, 19, 0, 0),
    (1, "M"): (26, 10, 1, 16, 0, 0),
    (1, "Q"): (26, 13, 1, 13, 0, 0),
    (1, "H"): (26, 17, 1, 9, 0, 0),
    (2, "L"): (44, 10, 1, 34, 0, 0),
    (2, "M"): (44, 16, 1, 28, 0, 0),
    (2, "Q"): (44, 22, 1, 22, 0, 0),
    (2, "H"): (44, 28, 1, 16, 0, 0),
    (3, "L"): (70, 15, 1, 55, 0, 0),
    (3, "M"): (70, 26, 1, 44, 0, 0),
    (3, "Q"): (70, 18, 2, 17, 0, 0),
    (3, "H"): (70, 22, 2, 13, 0, 0),
    (4, "L"): (100, 20, 1, 80, 0, 0),
    (4, "M"): (100, 18, 2, 32, 0, 0),
    (4, "Q"): (100, 26, 2, 24, 0, 0),
    (4, "H"): (100, 16, 4, 9, 0, 0),
    (5, "L"): (134, 26, 1, 108, 0, 0),
    (5, "M"): (134, 24, 2, 43, 0, 0),
    (5, "Q"): (134, 18, 2, 15, 2, 16),
    (5, "H"): (134, 22, 2, 11, 2, 12),
    (6, "L"): (172, 18, 2, 68, 0, 0),
    (6, "M"): (172, 16, 4, 27, 0, 0),
    (6, "Q"): (172, 24, 4, 19, 0, 0),
    (6, "H"): (172, 28, 4, 15, 0, 0),
    (7, "L"): (196, 20, 2, 78, 0, 0),
    (7, "M"): (196, 18, 4, 31, 0, 0),
    (7, "Q"): (196, 18, 2, 14, 4, 15),
    (7, "H"): (196, 26, 4, 13, 1, 14),
    (8, "L"): (242, 24, 2, 97, 0, 0),
    (8, "M"): (242, 22, 2, 38, 2, 39),
    (8, "Q"): (242, 22, 4, 18, 2, 19),
    (8, "H"): (242, 26, 4, 14, 2, 15),
    (9, "L"): (292, 30, 2, 116, 0, 0),
    (9, "M"): (292, 22, 3, 36, 2, 37),
    (9, "Q"): (292, 20, 4, 16, 4, 17),
    (9, "H"): (292, 24, 4, 12, 4, 13),
    (10, "L"): (346, 18, 2, 68, 2, 69),
    (10, "M"): (346, 26, 4, 43, 1, 44),
    (10, "Q"): (346, 24, 6, 19, 2, 20),
    (10, "H"): (346, 28, 6, 15, 2, 16),
}

_ALL_FORMAT_CODES: list[tuple[int, str, int]] = [
    (_compute_format_bits(ec_bits, mask), ec_name, mask)
    for ec_bits, ec_name in _EC_LEVEL_INDICATORS.items()
    for mask in range(8)
]

_ALNUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


@dataclass
class _FinderCluster:
    x: float
    y: float
    module_size: float
    weight: int = 1


def _find_11311_centers(binary: np.ndarray) -> list[_FinderCluster]:
    """Scan horizontal & vertical lines for 1:1:3:1:1 finder ratios."""
    h, w = binary.shape
    raw_hits: list[tuple[float, float, float]] = []

    def check_runs(runs: list[int]) -> bool:
        total = sum(runs)
        if total < 7:
            return False
        mod = total / 7.0
        max_var = mod * 0.55
        return (
            abs(runs[0] - mod) < max_var
            and abs(runs[1] - mod) < max_var
            and abs(runs[2] - 3.0 * mod) < 3.0 * max_var
            and abs(runs[3] - mod) < max_var
            and abs(runs[4] - mod) < max_var
        )

    step = 1 if max(h, w) <= 450 else 2
    for y in range(0, h, step):
        row = binary[y]
        runs: list[int] = []
        cur_val = int(row[0])
        run_len = 1
        x_starts: list[int] = [0]
        for x in range(1, w):
            v = int(row[x])
            if v == cur_val:
                run_len += 1
            else:
                runs.append(run_len)
                x_starts.append(x)
                cur_val = v
                run_len = 1
        runs.append(run_len)
        # B W B W B starts where color == 1
        start_idx = 0 if int(row[0]) == 1 else 1
        for i in range(start_idx, len(runs) - 4, 2):
            seg = runs[i:i + 5]
            if check_runs(seg):
                tot = sum(seg)
                cx = x_starts[i] + seg[0] + seg[1] + seg[2] * 0.5
                mod = tot / 7.0
                # Cross-check vertical profile around cx
                ix = int(round(cx))
                if 0 <= ix < w and _verify_vertical(binary, ix, y, mod):
                    raw_hits.append((cx, float(y), mod))

    # Cluster nearby hits
    clusters: list[_FinderCluster] = []
    for x, y, mod in raw_hits:
        matched = None
        for cl in clusters:
            if abs(cl.x - x) < max(6.0, cl.module_size * 3.0) and abs(cl.y - y) < max(6.0, cl.module_size * 3.0):
                matched = cl
                break
        if matched is None:
            clusters.append(_FinderCluster(x=x, y=y, module_size=mod, weight=1))
        else:
            nw = matched.weight + 1
            matched.x = (matched.x * matched.weight + x) / nw
            matched.y = (matched.y * matched.weight + y) / nw
            matched.module_size = (matched.module_size * matched.weight + mod) / nw
            matched.weight = nw

    clusters.sort(key=lambda c: c.weight, reverse=True)
    return clusters[:12]


def _verify_vertical(binary: np.ndarray, ix: int, y0: int, mod: float) -> bool:
    h = binary.shape[0]
    if binary[y0, ix] != 1:
        return False
    # Measure 5-run vertical 1:1:3:1:1 around center y0
    up = y0
    while up > 0 and binary[up - 1, ix] == 1:
        up -= 1
    dn = y0
    while dn < h - 1 and binary[dn + 1, ix] == 1:
        dn += 1
    c_len = dn - up + 1
    if not (1.8 * mod <= c_len <= 4.5 * mod):
        return False
    # White gap above & below center
    uw = up - 1
    while uw >= 0 and binary[uw, ix] == 0:
        uw -= 1
    uw_len = (up - 1) - uw
    dw = dn + 1
    while dw < h and binary[dw, ix] == 0:
        dw += 1
    dw_len = dw - (dn + 1)
    if not (0.4 * mod <= uw_len <= 1.9 * mod and 0.4 * mod <= dw_len <= 1.9 * mod):
        return False
    # Outer dark border above & below
    ub = uw
    while ub >= 0 and binary[ub, ix] == 1:
        ub -= 1
    ub_len = uw - ub
    db = dw
    while db < h and binary[db, ix] == 1:
        db += 1
    db_len = db - dw
    return 0.4 * mod <= ub_len <= 1.9 * mod and 0.4 * mod <= db_len <= 1.9 * mod


def _order_three_finders(pts: list[_FinderCluster]) -> tuple[_FinderCluster, _FinderCluster, _FinderCluster] | None:
    """Identify (top_left, top_right, bottom_left) using right-angle vector cross product."""
    best = None
    best_score = -1.0
    a, b, c = pts[0], pts[1], pts[2]
    for tl, p1, p2 in ((a, b, c), (b, a, c), (c, a, b)):
        v1x, v1y = p1.x - tl.x, p1.y - tl.y
        v2x, v2y = p2.x - tl.x, p2.y - tl.y
        n1 = (v1x * v1x + v1y * v1y) ** 0.5
        n2 = (v2x * v2x + v2y * v2y) ** 0.5
        if n1 < 10 or n2 < 10:
            continue
        cos_ang = abs((v1x * v2x + v1y * v2y) / (n1 * n2))
        ratio = min(n1, n2) / max(n1, n2)
        score = (1.0 - cos_ang) * ratio
        if score > best_score:
            best_score = score
            cross = v1x * v2y - v1y * v2x
            best = (tl, p1, p2) if cross > 0 else (tl, p2, p1)
    return best


def _decode_bits_to_text(bits: list[int], version: int) -> str | None:
    """Parse QR data bit segments (Byte, Numeric, Alphanumeric, ECI) into text."""
    idx = 0
    n = len(bits)
    out: list[str] = []

    def read_int(width: int) -> int | None:
        nonlocal idx
        if idx + width > n:
            return None
        val = 0
        for _ in range(width):
            val = (val << 1) | bits[idx]
            idx += 1
        return val

    while idx + 4 <= n:
        mode = read_int(4)
        if mode is None or mode == 0:
            break
        if mode == 0b0100:  # Byte mode
            cw = 8 if version < 10 else 16
            cnt = read_int(cw)
            if cnt is None or idx + cnt * 8 > n:
                return None
            raw = bytes(read_int(8) or 0 for _ in range(cnt))
            try:
                out.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                out.append(raw.decode("latin-1", errors="replace"))
        elif mode == 0b0001:  # Numeric
            cw = 10 if version < 10 else 12
            cnt = read_int(cw)
            if cnt is None:
                return None
            while cnt >= 3:
                v = read_int(10)
                if v is None:
                    return None
                out.append(f"{v:03d}")
                cnt -= 3
            if cnt == 2:
                v = read_int(7)
                if v is None:
                    return None
                out.append(f"{v:02d}")
            elif cnt == 1:
                v = read_int(4)
                if v is None:
                    return None
                out.append(str(v))
        elif mode == 0b0010:  # Alphanumeric
            cw = 9 if version < 10 else 11
            cnt = read_int(cw)
            if cnt is None:
                return None
            while cnt >= 2:
                v = read_int(11)
                if v is None or v // 45 >= len(_ALNUM_CHARS):
                    return None
                out.append(_ALNUM_CHARS[v // 45] + _ALNUM_CHARS[v % 45])
                cnt -= 2
            if cnt == 1:
                v = read_int(6)
                if v is None or v >= len(_ALNUM_CHARS):
                    return None
                out.append(_ALNUM_CHARS[v])
        elif mode == 0b0111:  # ECI assignment - skip indicator
            first = read_int(8)
            if first is None:
                return None
            if first & 0xC0 == 0x80:
                read_int(8)
            elif first & 0xE0 == 0xC0:
                read_int(16)
        else:
            break
    joined = "".join(out)
    return joined if joined else None


def _decode_matrix(mat: np.ndarray) -> str | None:
    """Read format info, unmask, RS-decode codewords, and parse payload."""
    size = mat.shape[0]
    if size < 21 or (size - 17) % 4 != 0:
        return None
    ver = (size - 17) // 4
    if ver < 1 or ver > 10:
        return None

    # Read 15-bit format info (copy 1)
    coords1 = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    fmt1 = 0
    for r, c in coords1:
        fmt1 = (fmt1 << 1) | int(mat[r, c])

    # Copy 2
    fmt2 = 0
    for i in range(7):
        fmt2 = (fmt2 << 1) | int(mat[size - 1 - i, 8])
    for i in range(8):
        fmt2 = (fmt2 << 1) | int(mat[8, size - 8 + i])

    best_fmt = None
    best_dist = 99
    for candidate_bits, ec_name, mask_id in _ALL_FORMAT_CODES:
        d1 = (fmt1 ^ candidate_bits).bit_count()
        d2 = (fmt2 ^ candidate_bits).bit_count()
        d = min(d1, d2)
        if d < best_dist:
            best_dist = d
            best_fmt = (ec_name, mask_id)
    if best_fmt is None or best_dist > 3:
        return None

    ec_name, mask_id = best_fmt
    spec = _EC_SPECS_V1_V10.get((ver, ec_name))
    if not spec:
        return None
    total_cw, ec_per_blk, g1_b, g1_d, g2_b, g2_d = spec
    _, reserved = _build_base_matrix(ver)

    # Read bitstream in zig-zag order
    bits: list[int] = []
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not reserved[r, c]:
                    bit = int(mat[r, c]) ^ (1 if _mask_fn(mask_id, r, c) else 0)
                    bits.append(bit)
        upward = not upward
        col -= 2

    if len(bits) < total_cw * 8:
        return None

    codewords: list[int] = []
    for i in range(total_cw):
        cw = 0
        base_i = i * 8
        for j in range(8):
            cw = (cw << 1) | bits[base_i + j]
        codewords.append(cw)

    # De-interleave blocks
    n_blocks = g1_b + g2_b
    blocks: list[list[int]] = [[] for _ in range(n_blocks)]
    cursor = 0
    max_d = max(g1_d, g2_d)
    for col_i in range(max_d):
        for b_i in range(n_blocks):
            dlen = g1_d if b_i < g1_b else g2_d
            if col_i < dlen:
                blocks[b_i].append(codewords[cursor])
                cursor += 1
    for _ in range(ec_per_blk):
        for b_i in range(n_blocks):
            blocks[b_i].append(codewords[cursor])
            cursor += 1

    data_bytes: list[int] = []
    for blk in blocks:
        repaired = _rs_decode(blk, ec_per_blk)
        if repaired is None:
            return None
        data_bytes.extend(repaired)

    data_bits: list[int] = []
    for b in data_bytes:
        for s in range(7, -1, -1):
            data_bits.append((b >> s) & 1)
    return _decode_bits_to_text(data_bits, ver)


def decode_qr_from_image(image: Image.Image) -> str | None:
    """Decode a QR code from a PIL Image. Returns decoded text or None."""
    # Optional fast native libraries if user installed them
    try:
        import zxingcpp  # type: ignore

        res = zxingcpp.read_barcodes(image)
        if res:
            return res[0].text
    except Exception:
        pass
    try:
        import cv2  # type: ignore

        arr = np.array(image.convert("RGB"))[:, :, ::-1]
        detector = cv2.QRCodeDetector()
        text, _, _ = detector.detectAndDecode(arr)
        if text:
            return text
    except Exception:
        pass

    gray = np.array(image.convert("L"), dtype=np.float32)
    # Try multiple luminance thresholds so both dark-on-light and tinted images decode cleanly
    lo, hi = float(np.percentile(gray, 10)), float(np.percentile(gray, 90))
    if hi - lo < 10.0:
        return None
    mid = (lo + hi) * 0.5
    for thresh in (mid, (lo + mid) * 0.5, (hi + mid) * 0.5):
        binary = (gray < thresh).astype(np.uint8)
        clusters = _find_11311_centers(binary)
        if len(clusters) < 3:
            continue
        # Try top combinations of 3 finder centers
        candidates = clusters[:6]
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                for k in range(j + 1, len(candidates)):
                    ordered = _order_three_finders([candidates[i], candidates[j], candidates[k]])
                    if ordered is None:
                        continue
                    tl, tr, bl = ordered
                    mod_est = (tl.module_size + tr.module_size + bl.module_size) / 3.0
                    dist_x = ((tr.x - tl.x) ** 2 + (tr.y - tl.y) ** 2) ** 0.5
                    dim_est = round(dist_x / mod_est + 7.0)
                    ver = max(1, min(10, round((dim_est - 17) / 4.0)))
                    for v_try in sorted({ver, max(1, ver - 1), min(10, ver + 1)}):
                        size = 17 + 4 * v_try
                        # Affine mapping from module centers (3.5, 3.5), (size-3.5, 3.5), (3.5, size-3.5)
                        src = np.array([
                            [3.5, 3.5, 1.0],
                            [size - 3.5, 3.5, 1.0],
                            [3.5, size - 3.5, 1.0],
                        ], dtype=np.float64)
                        dst_x = np.array([tl.x, tr.x, bl.x], dtype=np.float64)
                        dst_y = np.array([tl.y, tr.y, bl.y], dtype=np.float64)
                        try:
                            ax = np.linalg.solve(src, dst_x)
                            ay = np.linalg.solve(src, dst_y)
                        except np.linalg.LinAlgError:
                            continue
                        grid_r, grid_c = np.meshgrid(np.arange(size) + 0.5, np.arange(size) + 0.5, indexing="ij")
                        px = np.rint(ax[0] * grid_c + ax[1] * grid_r + ax[2]).astype(np.int32)
                        py = np.rint(ay[0] * grid_c + ay[1] * grid_r + ay[2]).astype(np.int32)
                        if (
                            px.min() < 0
                            or py.min() < 0
                            or px.max() >= binary.shape[1]
                            or py.max() >= binary.shape[0]
                        ):
                            continue
                        sampled = binary[py, px]
                        decoded = _decode_matrix(sampled)
                        if decoded is not None:
                            return decoded
    return None


def decode_qr_from_file(path: str) -> str | None:
    """Load an image file and attempt QR decoding."""
    with Image.open(path) as img:
        return decode_qr_from_image(img)
