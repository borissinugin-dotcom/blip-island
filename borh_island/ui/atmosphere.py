"""Dynamic atmospheric scene painter: time-of-day sky, sunrise/sunset, clouds, constellations, lunar phases, rain, and wet grass."""

from __future__ import annotations

import math
import random
import secrets
import time
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

# Synodic month length and known reference New Moon epoch (2000-01-06 18:14 UTC)
_SYNODIC_DAYS = 29.53058867
_NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)

# Generous pure-RAM procedural render caches (zero file I/O; ~15-35 MB total, no 100 MB ceiling)
_SMOKE_PUFF_CACHE: dict[tuple[int, int, int, int], QPixmap] = {}
_SMOKE_SPRITE_CACHE: dict[tuple[int, int, int, int, int], QPixmap] = {}
_SMOKE_WHITE_MASTERS: dict[tuple[int, int], QImage] = {}
_SMOKE_SPRITE_IMG_CACHE: dict[tuple[int, int, int, int, int], QImage] = {}
_GLOW_DISC_CACHE: dict[tuple[int, int, int, int, int], QImage] = {}
_SUNBEAM_CACHE: dict[tuple[int, int, int, int], QImage] = {}
_SMOKE_BUF_IMG: QImage | None = None
_MOON_CACHE: dict[tuple[int, int], QPixmap] = {}
_SUN_CACHE: dict[tuple[int, int], QPixmap] = {}
_STAR_HALO_CACHE: dict[tuple[int, int, int], QPixmap] = {}
_STAR_HALO_IMG_CACHE: dict[tuple[int, int, int], QImage] = {}
_SNOW_SPRITE_CACHE: dict[tuple[int, int], QPixmap] = {}
_SOIL_CRUST_CACHE: dict[tuple[int, bool, bool, str, int], QPixmap] = {}
_LIGHTNING_BOLT_CACHE: dict[tuple[int, int, int, int], QPixmap] = {}
_CLOUD_SPIDER_CACHE: dict[tuple[int, int, int], QPixmap] = {}
_THUNDER_GLOW_CACHE: dict[tuple[int, int, int], QPixmap] = {}
_GRASS_REST_CACHE: dict[tuple[int, int, str, bool, float, int], QPixmap] = {}
_STARFIELD_CACHE: dict[tuple, QImage] = {}
_SOIL_BACKDROP_CACHE: dict[tuple, QImage] = {}
_CACHED_PX_PER_CM: float | None = None


def _px_per_cm() -> float:
    """Resolve & cache safe screen pixels per centimeter (~37.8 px/cm at 96 DPI)."""
    global _CACHED_PX_PER_CM
    if _CACHED_PX_PER_CM is not None:
        return _CACHED_PX_PER_CM
    try:
        app = QGuiApplication.instance()
        scr = app.primaryScreen() if app is not None else None
        if scr is not None:
            dpi = float(scr.logicalDotsPerInchY())
            if 60.0 <= dpi <= 240.0:
                _CACHED_PX_PER_CM = max(32.0, min(44.0, dpi / 2.54))
                return _CACHED_PX_PER_CM
    except Exception:
        pass
    _CACHED_PX_PER_CM = 37.8
    return _CACHED_PX_PER_CM


def _evict_oldest(cache: dict, max_size: int, drop_n: int = 48) -> None:
    """Bounded FIFO eviction so smooth transitions never purge active sprites cleanly."""
    if len(cache) < max_size:
        return
    for k in list(cache.keys())[:drop_n]:
        cache.pop(k, None)


def _get_smoke_white_master(variant: int, tilt_bucket: int) -> QImage:
    """Build & retain the 39 static fine-tilted (3.5-deg) white alpha vapor shapes in RAM."""
    var = int(variant) % 3
    tb = max(-6, min(6, int(tilt_bucket)))
    key = (var, tb)
    cached = _SMOKE_WHITE_MASTERS.get(key)
    if cached is not None:
        return cached

    puff = QImage(96, 96, QImage.Format.Format_ARGB32_Premultiplied)
    puff.fill(0)
    pt = QPainter(puff)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pt.setPen(Qt.PenStyle.NoPen)
    offsets = (
        ((-4.5, -3.75, 39.0, 185), (10.5, 3.0, 30.0, 130), (-11.25, 8.25, 27.0, 108)),
        ((3.75, -5.25, 37.5, 185), (-10.5, 2.25, 31.5, 128), (9.0, 10.5, 26.25, 104)),
        ((0.0, -2.25, 40.5, 182), (12.0, -7.5, 27.0, 118), (-9.0, -6.75, 28.5, 120)),
    )[var]
    for ox, oy, rad, peak_a in offsets:
        cx = 48.0 + ox
        cy = 48.0 + oy
        g = QRadialGradient(QPointF(cx, cy), rad)
        g.setColorAt(0.0, QColor(255, 255, 255, peak_a))
        g.setColorAt(0.42, QColor(255, 255, 255, int(peak_a * 0.56)))
        g.setColorAt(0.76, QColor(255, 255, 255, int(peak_a * 0.16)))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        pt.setBrush(g)
        pt.drawEllipse(QPointF(cx, cy), rad, rad)
    pt.end()

    sp = QImage(88, 54, QImage.Format.Format_ARGB32_Premultiplied)
    sp.fill(0)
    st = QPainter(sp)
    st.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    st.translate(44.0, 27.0)
    st.rotate(tb * 3.5)
    st.drawImage(QRectF(-36.0, -20.25, 72.0, 40.5), puff, QRectF(0.0, 0.0, 96.0, 96.0))
    st.end()
    _SMOKE_WHITE_MASTERS[key] = sp
    return sp


def _get_smoke_sprite_img(rgb: tuple[int, int, int], variant: int, tilt_bucket: int) -> QImage:
    """Fast-tint a cached white alpha vapor shape into QImage (zero gradient/rotation rebuilds)."""
    r_c = max(0, min(255, int(rgb[0]))) & 0xFC
    g_c = max(0, min(255, int(rgb[1]))) & 0xFC
    b_c = max(0, min(255, int(rgb[2]))) & 0xFC
    var = int(variant) % 3
    tb = max(-6, min(6, int(tilt_bucket)))
    key = (r_c, g_c, b_c, var, tb)
    cached = _SMOKE_SPRITE_IMG_CACHE.get(key)
    if cached is not None:
        return cached
    _evict_oldest(_SMOKE_SPRITE_IMG_CACHE, 600, 96)

    sp = _get_smoke_white_master(var, tb).copy()
    pt = QPainter(sp)
    pt.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    pt.fillRect(0, 0, 88, 54, QColor(r_c, g_c, b_c, 255))
    pt.end()
    _SMOKE_SPRITE_IMG_CACHE[key] = sp
    return sp


_SMOKE_LAYER_LUT_CACHE: dict[tuple[int, int, int], tuple[QImage, ...]] = {}


def _get_smoke_layer_lut(rgb: tuple[int, int, int]) -> tuple[QImage, ...]:
    """Return flat 39-entry (variant*13 + tilt+6) QImage lookup tuple for smooth shimmer-free puff compositing."""
    r_c = max(0, min(255, int(rgb[0]))) & 0xFC
    g_c = max(0, min(255, int(rgb[1]))) & 0xFC
    b_c = max(0, min(255, int(rgb[2]))) & 0xFC
    key = (r_c, g_c, b_c)
    cached = _SMOKE_LAYER_LUT_CACHE.get(key)
    if cached is not None:
        return cached
    _evict_oldest(_SMOKE_LAYER_LUT_CACHE, 64, 16)
    lut = tuple(
        _get_smoke_sprite_img(key, var, tb)
        for var in range(3)
        for tb in range(-6, 7)
    )
    _SMOKE_LAYER_LUT_CACHE[key] = lut
    return lut


def _get_glow_disc_img(rgb: tuple[int, int, int], peak_alpha: int, mid_alpha: int) -> QImage:
    """Cache 128x128 radial atmospheric halo QImage (eliminates per-frame QRadialGradient)."""
    r_c = max(0, min(255, int(rgb[0]))) & 0xF0
    g_c = max(0, min(255, int(rgb[1]))) & 0xF0
    b_c = max(0, min(255, int(rgb[2]))) & 0xF0
    pa = max(0, min(255, int(peak_alpha))) & 0xE0
    ma = max(0, min(255, int(mid_alpha))) & 0xE0
    key = (r_c, g_c, b_c, pa, ma)
    cached = _GLOW_DISC_CACHE.get(key)
    if cached is not None:
        return cached
    _evict_oldest(_GLOW_DISC_CACHE, 512, 64)

    img = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    pt = QPainter(img)
    pt.setPen(Qt.PenStyle.NoPen)
    g = QRadialGradient(QPointF(64.0, 64.0), 64.0)
    g.setColorAt(0.0, QColor(r_c, g_c, b_c, pa))
    g.setColorAt(0.42, QColor(r_c, g_c, b_c, ma))
    g.setColorAt(1.0, QColor(r_c, g_c, b_c, 0))
    pt.setBrush(g)
    pt.drawEllipse(QPointF(64.0, 64.0), 64.0, 64.0)
    pt.end()
    _GLOW_DISC_CACHE[key] = img
    return img


def _get_sunbeam_img(rgb: tuple[int, int, int], mid_alpha: int) -> QImage:
    """Cache feathered pastel sunbeam shaft QImage so clouds/sky draw zero radial gradients per frame."""
    r_c = max(0, min(255, int(rgb[0]))) & 0xF8
    g_c = max(0, min(255, int(rgb[1]))) & 0xF8
    b_c = max(0, min(255, int(rgb[2]))) & 0xF8
    ma = max(0, min(255, int(mid_alpha))) & 0xFE
    key = (r_c, g_c, b_c, ma)
    cached = _SUNBEAM_CACHE.get(key)
    if cached is not None:
        return cached
    _evict_oldest(_SUNBEAM_CACHE, 32, 8)

    img = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    pt = QPainter(img)
    pt.setPen(Qt.PenStyle.NoPen)
    rg = QRadialGradient(QPointF(32.0, 32.0), 31.0)
    rg.setColorAt(0.0, QColor(r_c, g_c, b_c, ma))
    rg.setColorAt(0.50, QColor(r_c, g_c, b_c, max(1, int(ma * 0.35))))
    rg.setColorAt(1.0, QColor(r_c, g_c, b_c, 0))
    pt.setBrush(rg)
    pt.drawEllipse(QPointF(32.0, 32.0), 31.0, 31.0)
    pt.end()
    _SUNBEAM_CACHE[key] = img
    return img


def _get_smoke_puff_pixmap(rgb: tuple[int, int, int], variant: int) -> QPixmap:
    """Build & cache a 128x128 organic multi-lobe volumetric vapor puff texture in RAM (zero file I/O)."""
    r_c = max(0, min(255, int(rgb[0])))
    g_c = max(0, min(255, int(rgb[1])))
    b_c = max(0, min(255, int(rgb[2])))
    var = int(variant) % 3
    key = (r_c, g_c, b_c, var)
    cached = _SMOKE_PUFF_CACHE.get(key)
    if cached is not None:
        return cached
    _evict_oldest(_SMOKE_PUFF_CACHE, 192, 48)

    pm = QPixmap(128, 128)
    pm.fill(Qt.GlobalColor.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pt.setPen(Qt.PenStyle.NoPen)

    offsets = (
        ((-6.0, -5.0, 52.0, 185), (14.0, 4.0, 40.0, 130), (-15.0, 11.0, 36.0, 108)),
        ((5.0, -7.0, 50.0, 185), (-14.0, 3.0, 42.0, 128), (12.0, 14.0, 35.0, 104)),
        ((0.0, -3.0, 54.0, 182), (16.0, -10.0, 36.0, 118), (-12.0, -9.0, 38.0, 120)),
    )[var]
    for ox, oy, rad, peak_a in offsets:
        cx = 64.0 + ox
        cy = 64.0 + oy
        g = QRadialGradient(QPointF(cx, cy), rad)
        g.setColorAt(0.0, QColor(r_c, g_c, b_c, peak_a))
        g.setColorAt(0.42, QColor(r_c, g_c, b_c, int(peak_a * 0.56)))
        g.setColorAt(0.76, QColor(r_c, g_c, b_c, int(peak_a * 0.16)))
        g.setColorAt(1.0, QColor(r_c, g_c, b_c, 0))
        pt.setBrush(g)
        pt.drawEllipse(QPointF(cx, cy), rad, rad)
    pt.end()
    _SMOKE_PUFF_CACHE[key] = pm
    return pm


def _get_smoke_sprite(rgb: tuple[int, int, int], variant: int, tilt_bucket: int) -> QPixmap:
    """Compat wrapper returning cached stretched & rotated oval vapor QPixmap."""
    r_c = max(0, min(255, int(rgb[0]))) & 0xFC
    g_c = max(0, min(255, int(rgb[1]))) & 0xFC
    b_c = max(0, min(255, int(rgb[2]))) & 0xFC
    var = int(variant) % 3
    tb = max(-6, min(6, int(tilt_bucket)))
    key = (r_c, g_c, b_c, var, tb)
    cached = _SMOKE_SPRITE_CACHE.get(key)
    if cached is not None:
        return cached
    _evict_oldest(_SMOKE_SPRITE_CACHE, 256, 48)
    sp = QPixmap.fromImage(_get_smoke_sprite_img((r_c, g_c, b_c), var, tb))
    _SMOKE_SPRITE_CACHE[key] = sp
    return sp


def _get_star_halo_img(col_rgb: tuple[int, int, int]) -> QImage:
    """Cache 20x20 soft spectral star bloom QImage (zero Pixmap-Image conversion in raster paint)."""
    key = (col_rgb[0], col_rgb[1], col_rgb[2])
    cached = _STAR_HALO_IMG_CACHE.get(key)
    if cached is not None:
        return cached
    _evict_oldest(_STAR_HALO_IMG_CACHE, 96, 24)
    img = QImage(20, 20, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    pt = QPainter(img)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pt.setPen(Qt.PenStyle.NoPen)
    g = QRadialGradient(QPointF(10.0, 10.0), 10.0)
    g.setColorAt(0.0, QColor(key[0], key[1], key[2], 235))
    g.setColorAt(0.45, QColor(key[0], key[1], key[2], 96))
    g.setColorAt(1.0, QColor(key[0], key[1], key[2], 0))
    pt.setBrush(g)
    pt.drawEllipse(QPointF(10.0, 10.0), 10.0, 10.0)
    pt.end()
    _STAR_HALO_IMG_CACHE[key] = img
    return img


def _get_star_halo_pixmap(col_rgb: tuple[int, int, int]) -> QPixmap:
    """Cache 20x20 soft spectral star bloom sprite to eliminate per-frame QRadialGradient allocations."""
    key = (col_rgb[0], col_rgb[1], col_rgb[2])
    cached = _STAR_HALO_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_STAR_HALO_CACHE) >= 96:
        _STAR_HALO_CACHE.clear()
    pm = QPixmap.fromImage(_get_star_halo_img(key))
    _STAR_HALO_CACHE[key] = pm
    return pm


def _get_snow_sprite_pixmap(variant: int, size: int = 24) -> QPixmap:
    """Cache realistic dendritic crystal & soft out-of-focus bokeh snowflake stamps in RAM."""
    sz = max(12, min(32, int(size)))
    var = int(variant) % 4
    key = (var, sz)
    cached = _SNOW_SPRITE_CACHE.get(key)
    if cached is not None:
        return cached
    pm = QPixmap(sz, sz)
    pm.fill(Qt.GlobalColor.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    c = sz * 0.5
    if var < 2:
        # Soft atmospheric out-of-focus foreground powder flake
        rad = c * 0.88
        g = QRadialGradient(QPointF(c, c), rad)
        g.setColorAt(0.0, QColor(255, 255, 255, 235 if var == 0 else 195))
        g.setColorAt(0.45, QColor(236, 245, 255, 165 if var == 0 else 120))
        g.setColorAt(1.0, QColor(220, 236, 255, 0))
        pt.setPen(Qt.PenStyle.NoPen)
        pt.setBrush(g)
        pt.drawEllipse(QPointF(c, c), rad, rad)
    else:
        # Crisp 6-pointed crystalline ice dendrite with secondary side barbs
        rad = c * 0.86
        soft_g = QRadialGradient(QPointF(c, c), rad * 0.65)
        soft_g.setColorAt(0.0, QColor(255, 255, 255, 130))
        soft_g.setColorAt(1.0, QColor(224, 240, 255, 0))
        pt.setPen(Qt.PenStyle.NoPen)
        pt.setBrush(soft_g)
        pt.drawEllipse(QPointF(c, c), rad * 0.65, rad * 0.65)
        pt.setPen(QPen(QColor(246, 250, 255, 235), max(0.8, sz * 0.055), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        rot_off = 0.26 if var == 3 else 0.0
        for arm in range(6):
            ang = rot_off + arm * (math.pi / 3.0)
            ca, sa = math.cos(ang), math.sin(ang)
            pt.drawLine(QPointF(c, c), QPointF(c + ca * rad, c + sa * rad))
            bx, by = c + ca * (rad * 0.56), c + sa * (rad * 0.56)
            b_len = rad * 0.30
            for sgn in (-1.0, 1.0):
                ba = ang + sgn * 0.68
                pt.drawLine(QPointF(bx, by), QPointF(bx + math.cos(ba) * b_len, by + math.sin(ba) * b_len))
    pt.end()
    _SNOW_SPRITE_CACHE[key] = pm
    return pm


def _get_realistic_sun_pixmap(pix_size: int, warm_key: int) -> QPixmap:
    """Synthesize a high-detail 3D solar photosphere with astrophysical limb darkening and plasma granulation."""
    sz = max(48, min(144, int(pix_size)))
    wk = int(warm_key) % 3  # 0=day, 1=sunrise, 2=sunset
    key = (sz, wk)
    cached = _SUN_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_SUN_CACHE) >= 64:
        _SUN_CACHE.clear()

    img = QImage(sz, sz, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    half = sz * 0.5
    disc_r = half - 2.0

    # Photosphere temperature gradients (hot white-gold core -> amber/vermilion limb)
    if wk == 1:
        c_core, c_mid, c_limb = (255, 245, 224), (253, 184, 92), (242, 82, 68)
    elif wk == 2:
        c_core, c_mid, c_limb = (255, 242, 214), (251, 162, 68), (226, 78, 22)
    else:
        c_core, c_mid, c_limb = (255, 254, 246), (255, 222, 102), (244, 152, 18)

    for py in range(sz):
        dy = (py + 0.5 - half) / max(1.0, disc_r)
        dy2 = dy * dy
        for px in range(sz):
            dx = (px + 0.5 - half) / max(1.0, disc_r)
            r2 = dx * dx + dy2
            if r2 > 1.08:
                continue
            r = math.sqrt(r2)
            edge_alpha = max(0.0, min(1.0, (1.03 - r) * disc_r * 0.72))
            if edge_alpha <= 0.01:
                continue
            mu = math.sqrt(max(0.0, 1.0 - min(1.0, r2)))
            # Eddington-like photospheric limb darkening curve + fine multi-scale granulation convection cells
            limb_w = 0.36 + 0.64 * (mu ** 0.56)
            gx = dx * 13.0
            gy = dy * 13.0
            gran = (
                math.sin(gx * 1.1 + gy * 0.4) * math.cos(gy * 1.15 - gx * 0.3) * 0.55
                + math.sin(gx * 2.3 - gy * 1.9) * math.cos(gx * 1.7 + gy * 2.4) * 0.45
            )
            # Subtle solar chromosphere bright faculae ring near limb + convective grain modulation
            facula = max(0.0, (1.0 - abs(r - 0.86) * 6.5)) * 0.055 * (1.0 + gran * 0.5)
            t = max(0.0, min(1.0, limb_w + gran * 0.036 * (0.4 + 0.6 * mu) + facula))

            if t >= 0.64:
                u = (t - 0.64) / 0.36
                rr = c_mid[0] + (c_core[0] - c_mid[0]) * u
                gg = c_mid[1] + (c_core[1] - c_mid[1]) * u
                bb = c_mid[2] + (c_core[2] - c_mid[2]) * u
            else:
                u = t / 0.64
                rr = c_limb[0] + (c_mid[0] - c_limb[0]) * u
                gg = c_limb[1] + (c_mid[1] - c_limb[1]) * u
                bb = c_limb[2] + (c_mid[2] - c_limb[2]) * u

            alpha = int(round(255 * edge_alpha))
            r_b = max(0, min(255, int(round(rr))))
            g_b = max(0, min(255, int(round(gg))))
            b_b = max(0, min(255, int(round(bb))))
            pa = alpha / 255.0
            argb = (
                (alpha << 24)
                | (int(r_b * pa) << 16)
                | (int(g_b * pa) << 8)
                | int(b_b * pa)
            )
            img.setPixel(px, py, argb)

    pm = QPixmap.fromImage(img)
    _SUN_CACHE[key] = pm
    return pm


def _eval_storm_state(phase_t: float, th_amt: float, li_amt: float) -> tuple[float, float, int, int, float, float, int]:
    """Return realistic multi-pulse storm flicker metrics:
    (cloud_flash, ground_flash, strike_id, active_cell, distant_flash, distant_ground, cell_b_idx).
    Uses uneven non-robotic storm clusters and real stepped-leader / restrike envelopes."""
    if th_amt <= 0.05 and li_amt <= 0.05:
        return 0.0, 0.0, 0, 0, 0.0, 0.0, 0

    t = max(0.0, float(phase_t))
    # Primary storm cell: ~3.1s windows with jittered trigger offset so thunder never looks metronomic
    win_len = 3.10
    win_idx = int(t // win_len)
    win_u = (t - win_idx * win_len) / win_len  # 0..1
    h1 = ((win_idx * 2654435761 + 17) & 0xFFFF) / 65535.0
    h2 = (((win_idx * 2246822519) + 97) & 0xFFFF) / 65535.0
    start_u = 0.08 + h1 * 0.28
    # Some quiet dark lulls (~18% of thunder windows stay moody dark before next cluster)
    has_burst = (h2 > 0.14) or (li_amt > 0.45)

    def _pulse_curve(tau: float) -> float:
        # tau in seconds from burst onset (duration ~0.88s):
        # pre-leader flicker -> blinding main return stroke -> dark dip -> dart restrike -> ionized fade
        if tau < 0.0 or tau > 0.92:
            return 0.0
        if tau < 0.08:
            return 0.22 + 0.22 * math.sin((tau / 0.08) * math.pi)
        if tau < 0.16:
            u = (tau - 0.08) / 0.08
            return 0.28 + 0.72 * math.sin(u * math.pi * 0.5)
        if tau < 0.23:
            u = (tau - 0.16) / 0.07
            return 1.0 - 0.82 * u
        if tau < 0.32:
            u = (tau - 0.23) / 0.09
            return 0.18 + 0.68 * math.sin(u * math.pi)
        if tau < 0.43:
            u = (tau - 0.32) / 0.11
            return 0.24 + 0.34 * math.sin(u * math.pi)
        u = (tau - 0.43) / 0.49
        return max(0.0, 0.30 * ((1.0 - u) ** 2.1))

    cloud_flash = 0.0
    ground_flash = 0.0
    if has_burst:
        tau = (win_u - start_u) * win_len
        env = _pulse_curve(tau)
        cloud_flash = env * max(th_amt, li_amt)
        # Cloud-to-ground bolt shoots on ~65% of storm windows when lightning mode is active
        if li_amt > 0.15 and ((win_idx * 7 + int(h1 * 10)) % 3 != 0):
            ground_flash = env * li_amt

    # Secondary asynchronous distant cumulonimbus sheet-thunder cell (~2.45s window)
    w2_len = 2.45
    w2_idx = int((t + 1.13) // w2_len)
    w2_u = ((t + 1.13) - w2_idx * w2_len) / w2_len
    h3 = (((w2_idx * 3266489917) + 41) & 0xFFFF) / 65535.0
    distant_flash = 0.0
    distant_ground = 0.0
    if h3 > 0.32:
        tau2 = (w2_u - (0.12 + h3 * 0.32)) * w2_len
        if 0.0 <= tau2 <= 0.62:
            u2 = tau2 / 0.62
            sub_flicker = 1.0 if u2 < 0.22 else (0.28 if u2 < 0.36 else (0.76 if u2 < 0.52 else max(0.0, (1.0 - u2) * 1.4)))
            distant_flash = sub_flicker * max(th_amt * 0.85, li_amt * 0.65)
            if li_amt > 0.40 and (w2_idx % 2 == 0) and u2 < 0.48:
                distant_ground = sub_flicker * 0.55 * li_amt

    active_cell = (win_idx * 5 + int(h2 * 13)) & 0xFF
    cell_b = (w2_idx * 7 + 3) & 0xFF
    return cloud_flash, ground_flash, win_idx, active_cell, distant_flash, distant_ground, cell_b


def _build_fractal_bolt(
    p0: QPointF,
    p1: QPointF,
    seed: int,
    max_jitter: float,
    depth: int = 5,
    branch_chance: float = 0.58,
) -> tuple[list[tuple[QPointF, QPointF, float]], QPainterPath, QPainterPath, list[tuple[QPointF, float]]]:
    """Generate a high-detail tapered stepped-leader lightning bolt with 1st/2nd-order forks and plasma elbow hotspots."""
    rng = random.Random(seed)
    pts: list[QPointF] = [QPointF(p0), QPointF(p1)]
    disp = float(max_jitter)
    for _ in range(max(2, int(depth))):
        nxt: list[QPointF] = [pts[0]]
        for idx in range(len(pts) - 1):
            a = pts[idx]
            b = pts[idx + 1]
            dx = b.x() - a.x()
            dy = b.y() - a.y()
            ln = max(1.0, math.hypot(dx, dy))
            nx, ny = -dy / ln, dx / ln
            off = rng.uniform(-disp, disp)
            # Natural stepped-leader asymmetry: sharper jagged elbows instead of soft sine waves
            if rng.random() < 0.32:
                off *= 1.35
            mx = 0.5 * (a.x() + b.x()) + nx * off + rng.uniform(-disp * 0.20, disp * 0.20)
            my = 0.5 * (a.y() + b.y()) + ny * (off * 0.38)
            nxt.append(QPointF(mx, my))
            nxt.append(b)
        pts = nxt
        disp *= 0.54

    n_segs = max(1, len(pts) - 1)
    main_segs: list[tuple[QPointF, QPointF, float]] = []
    hotspots: list[tuple[QPointF, float]] = []
    for idx in range(n_segs):
        t_pos = idx / max(1, n_segs - 1)
        taper = max(0.45, 1.0 - 0.45 * t_pos)
        main_segs.append((pts[idx], pts[idx + 1], taper))
        if 1 <= idx < n_segs - 1 and rng.random() < 0.16:
            hotspots.append((pts[idx], taper * rng.uniform(1.2, 2.2)))

    branches = QPainterPath()
    twigs = QPainterPath()
    total_len = max(16.0, math.hypot(p1.x() - p0.x(), p1.y() - p0.y()))
    base_ang = math.atan2(p1.y() - p0.y(), p1.x() - p0.x())
    last_branch_idx = -4
    for idx in range(3, len(pts) - 3):
        if idx - last_branch_idx < 3:
            continue
        if rng.random() > branch_chance:
            continue
        t_prog = idx / max(1, len(pts) - 1)
        if t_prog > 0.78:
            continue
        last_branch_idx = idx
        start = pts[idx]
        side = 1.0 if ((idx + rng.randint(0, 1)) % 2 == 0) else -1.0
        ang = base_ang + side * rng.uniform(0.32, 0.78)
        b_len = total_len * rng.uniform(0.18, 0.36) * (1.05 - 0.55 * t_prog)
        cx, cy = start.x(), start.y()
        branches.moveTo(cx, cy)
        n_b = 5
        for s_i in range(1, n_b + 1):
            step_ang = ang + rng.uniform(-0.32, 0.32)
            cx += math.cos(step_ang) * (b_len / n_b)
            cy += math.sin(step_ang) * (b_len / n_b)
            branches.lineTo(cx, cy)
            # Fine 2nd-order secondary ionized filament
            if s_i == 2 and rng.random() < 0.52:
                tang = step_ang + (-side if rng.random() < 0.5 else side) * rng.uniform(0.28, 0.65)
                tx, ty = cx, cy
                twigs.moveTo(tx, ty)
                for _t in range(3):
                    ta = tang + rng.uniform(-0.34, 0.34)
                    tx += math.cos(ta) * (b_len * 0.14)
                    ty += math.sin(ta) * (b_len * 0.14)
                    twigs.lineTo(tx, ty)

    # Subtle upward ground connecting streamers near bottom impact point
    if max_jitter >= 12.0:
        for _u in range(2):
            ux0 = p1.x() + rng.uniform(-12.0, 12.0)
            uy0 = p1.y()
            twigs.moveTo(ux0, uy0)
            u_ang = -math.pi * 0.5 + rng.uniform(-0.48, 0.48)
            for _us in range(2):
                u_ang += rng.uniform(-0.28, 0.28)
                ux0 += math.cos(u_ang) * rng.uniform(4.0, 7.0)
                uy0 += math.sin(u_ang) * rng.uniform(4.0, 7.0)
                twigs.lineTo(ux0, uy0)

    return main_segs, branches, twigs, hotspots


def _get_lightning_bolt_pixmap(w: int, h: int, seed: int, heavy: bool) -> QPixmap:
    """Pre-bake branched optical plasma lightning bolt sprite into RAM QPixmap (zero per-frame stroke cost)."""
    cw = max(64, int(round(w / 16.0)) * 16)
    ch = max(48, int(round(h / 16.0)) * 16)
    key = (cw, ch, int(seed) % 24, 1 if heavy else 0)
    cached = _LIGHTNING_BOLT_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_LIGHTNING_BOLT_CACHE) > 96:
        _LIGHTNING_BOLT_CACHE.clear()

    img = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    rng = random.Random(1013 + key[2] * 313 + (77 if heavy else 0))
    sx = cw * rng.uniform(0.32, 0.68)
    sy = ch * 0.04
    ex = max(24.0, min(cw - 24.0, sx + cw * rng.uniform(-0.20, 0.20)))
    ey = ch * (0.95 if heavy else 0.58)
    main_segs, br_b, twig_b, hotspots = _build_fractal_bolt(
        QPointF(sx, sy),
        QPointF(ex, ey),
        seed=5009 + key[2] * 97 + (401 if heavy else 0),
        max_jitter=max(11.0, cw * (0.088 if heavy else 0.062)),
        depth=5 if heavy else 4,
        branch_chance=0.38 if heavy else 0.28,
    )

    main_full = QPainterPath()
    if main_segs:
        main_full.moveTo(main_segs[0][0])
        for _, p_b, _ in main_segs:
            main_full.lineTo(p_b)

    # Layer 1: Soft wide violet-indigo atmospheric halo -> electric cyan sheath
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(102, 134, 255, 44 if heavy else 32), 18.0 if heavy else 10.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(main_full)
    p.setPen(QPen(QColor(152, 204, 255, 118 if heavy else 88), 5.6 if heavy else 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(main_full)

    # Layer 2: Delicate secondary forks (soft glow + crisp thin ionized filaments)
    p.setPen(QPen(QColor(125, 178, 255, 68), 2.4 if heavy else 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(br_b)
    p.setPen(QPen(QColor(230, 245, 255, 195 if heavy else 165), 0.90, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(br_b)
    p.setPen(QPen(QColor(175, 215, 255, 110), 0.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(twig_b)

    # Layer 3: Tapered hot white core + tiny plasma elbow beads
    core_top = 1.95 if heavy else 1.35
    for a, b, taper in main_segs:
        p.setPen(QPen(QColor(250, 253, 255, 248), core_top * taper, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(a, b)

    p.setPen(Qt.PenStyle.NoPen)
    for hp, rad in hotspots[:4]:
        rr = rad * 2.8
        rg = QRadialGradient(hp, rr)
        rg.setColorAt(0.0, QColor(255, 255, 255, 185))
        rg.setColorAt(0.45, QColor(168, 214, 255, 72))
        rg.setColorAt(1.0, QColor(110, 155, 245, 0))
        p.setBrush(rg)
        p.drawEllipse(hp, rr, rr)

    # Feather top 14% smoothly so the bolt emerges organically from cloud depth (no abrupt top start)
    top_fade = QLinearGradient(QPointF(0.0, 0.0), QPointF(0.0, ch * 0.16))
    top_fade.setColorAt(0.0, QColor(255, 255, 255, 0))
    top_fade.setColorAt(1.0, QColor(255, 255, 255, 255))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.fillRect(0, 0, cw, int(ch * 0.18), top_fade)
    p.end()

    pm = QPixmap.fromImage(img)
    _LIGHTNING_BOLT_CACHE[key] = pm
    return pm


def _get_cloud_spider_pixmap(w: int, h: int, seed: int) -> QPixmap:
    """Pre-bake delicate intra-cloud spider crawler veins with full-border feathering (zero clip seams)."""
    cw = max(160, int(round(w / 24.0)) * 24)
    ch = max(80, int(round(h / 16.0)) * 16)
    key = (cw, ch, int(seed) % 16)
    cached = _CLOUD_SPIDER_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_CLOUD_SPIDER_CACHE) > 48:
        _CLOUD_SPIDER_CACHE.clear()

    img = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    rng = random.Random(3319 + key[2] * 211)
    left_to_right = (key[2] % 2 == 0)
    x_start = cw * rng.uniform(0.16, 0.28) if left_to_right else cw * rng.uniform(0.72, 0.84)
    x_end = cw * rng.uniform(0.70, 0.84) if left_to_right else cw * rng.uniform(0.16, 0.30)
    y_start = ch * rng.uniform(0.32, 0.56)
    y_end = ch * rng.uniform(0.32, 0.62)

    main_segs, br_b, twig_b, hotspots = _build_fractal_bolt(
        QPointF(x_start, y_start),
        QPointF(x_end, y_end),
        seed=7411 + key[2] * 149,
        max_jitter=max(7.0, ch * 0.14),
        depth=4,
        branch_chance=0.34,
    )
    main_full = QPainterPath()
    if main_segs:
        main_full.moveTo(main_segs[0][0])
        for _, pb, _ in main_segs:
            main_full.lineTo(pb)

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(112, 148, 255, 36), 9.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(main_full)
    p.setPen(QPen(QColor(162, 210, 255, 92), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(main_full)
    p.setPen(QPen(QColor(135, 186, 255, 58), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(br_b)
    p.setPen(QPen(QColor(232, 246, 255, 160), 0.80, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(br_b)
    p.setPen(QPen(QColor(180, 218, 255, 84), 0.50, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(twig_b)
    for a, b, taper in main_segs:
        p.setPen(QPen(QColor(246, 251, 255, 215), 1.15 * taper, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(a, b)

    p.setPen(Qt.PenStyle.NoPen)
    for hp, rad in hotspots[:3]:
        rr = min(14.0, rad * 2.4)
        rg = QRadialGradient(hp, rr)
        rg.setColorAt(0.0, QColor(240, 250, 255, 145))
        rg.setColorAt(0.5, QColor(150, 198, 255, 48))
        rg.setColorAt(1.0, QColor(100, 140, 240, 0))
        p.setBrush(rg)
        p.drawEllipse(hp, rr, rr)

    # Full-border vignette mask so zero box/circle clip seams ever appear
    edge_v = QLinearGradient(QPointF(0.0, 0.0), QPointF(0.0, float(ch)))
    edge_v.setColorAt(0.0, QColor(255, 255, 255, 0))
    edge_v.setColorAt(0.24, QColor(255, 255, 255, 255))
    edge_v.setColorAt(0.70, QColor(255, 255, 255, 255))
    edge_v.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.fillRect(0, 0, cw, ch, edge_v)
    edge_h = QLinearGradient(QPointF(0.0, 0.0), QPointF(float(cw), 0.0))
    edge_h.setColorAt(0.0, QColor(255, 255, 255, 0))
    edge_h.setColorAt(0.14, QColor(255, 255, 255, 255))
    edge_h.setColorAt(0.86, QColor(255, 255, 255, 255))
    edge_h.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(0, 0, cw, ch, edge_h)
    p.end()

    pm = QPixmap.fromImage(img)
    _CLOUD_SPIDER_CACHE[key] = pm
    return pm


def _get_thunder_glow_pixmap(w: int, h: int, seed: int) -> QPixmap:
    """Pre-render multi-lobe volumetric cumulonimbus internal subsurface sheet thunder glow into RAM QPixmap."""
    cw = max(120, int(round(w / 24.0)) * 24)
    ch = max(72, int(round(h / 16.0)) * 16)
    key = (cw, ch, int(seed) % 16)
    cached = _THUNDER_GLOW_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_THUNDER_GLOW_CACHE) > 48:
        _THUNDER_GLOW_CACHE.clear()

    img = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rng = random.Random(8807 + key[2] * 179)
    p.setPen(Qt.PenStyle.NoPen)
    # Main hot lightning core inside storm anvil + surrounding rolling cumulonimbus scatter lobes
    primary_x = cw * rng.uniform(0.24, 0.76)
    primary_y = ch * rng.uniform(0.24, 0.44)
    lobes = [
        (primary_x, primary_y, cw * 0.22, ch * 0.34, (220, 236, 255, 118), (146, 184, 255, 58), (96, 130, 235, 0)),
        (primary_x + cw * rng.uniform(-0.16, 0.16), primary_y + ch * rng.uniform(-0.04, 0.10), cw * 0.28, ch * 0.40, (182, 210, 255, 82), (114, 150, 240, 38), (74, 102, 210, 0)),
        (cw * rng.uniform(0.20, 0.80), ch * rng.uniform(0.26, 0.48), cw * 0.20, ch * 0.34, (162, 196, 255, 68), (108, 142, 232, 30), (68, 96, 200, 0)),
    ]
    for cx, cy, rx, ry_lim, c0, c1, c2 in lobes:
        ry = min(rx * 0.46, ry_lim)
        grad = QRadialGradient(0.0, 0.0, rx)
        grad.setColorAt(0.0, QColor(*c0))
        grad.setColorAt(0.40, QColor(*c1))
        grad.setColorAt(1.0, QColor(*c2))
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, ry / max(1.0, rx))
        p.setBrush(grad)
        p.drawEllipse(QPointF(0.0, 0.0), rx, rx)
        p.restore()
    # Clean vignette on all edges -> 100% seamless interior cloud illumination!
    fade_v = QLinearGradient(QPointF(0.0, 0.0), QPointF(0.0, float(ch)))
    fade_v.setColorAt(0.0, QColor(255, 255, 255, 140))
    fade_v.setColorAt(0.32, QColor(255, 255, 255, 255))
    fade_v.setColorAt(0.62, QColor(255, 255, 255, 255))
    fade_v.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.fillRect(0, 0, cw, ch, fade_v)
    fade_h = QLinearGradient(QPointF(0.0, 0.0), QPointF(float(cw), 0.0))
    fade_h.setColorAt(0.0, QColor(255, 255, 255, 0))
    fade_h.setColorAt(0.10, QColor(255, 255, 255, 255))
    fade_h.setColorAt(0.90, QColor(255, 255, 255, 255))
    fade_h.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(0, 0, cw, ch, fade_h)
    p.end()

    pm = QPixmap.fromImage(img)
    _THUNDER_GLOW_CACHE[key] = pm
    return pm


_LUNAR_CRATERS: tuple[tuple[float, float, float, float], ...] = (
    (-0.28, -0.32, 0.22, 0.55),  # Mare Imbrium basin
    (0.14, -0.26, 0.16, 0.48),   # Mare Serenitatis
    (0.32, -0.06, 0.19, 0.52),   # Mare Tranquillitatis
    (-0.46, 0.08, 0.18, 0.45),   # Oceanus Procellarum lobe
    (-0.16, 0.12, 0.11, 0.62),   # Copernicus crater
    (-0.08, 0.56, 0.085, 0.78),  # Tycho highland crater
    (0.06, -0.52, 0.09, 0.65),   # Plato dark basin
    (-0.32, 0.34, 0.075, 0.60),
    (0.24, 0.36, 0.085, 0.58),
    (0.48, -0.22, 0.10, 0.54),
    (0.02, -0.06, 0.065, 0.56),
)


def _get_realistic_moon_pixmap(pix_size: int, phase: float) -> QPixmap:
    """Synthesize & cache a 3D spherical cratered Moon texture with earthshine and realistic phase terminator."""
    sz = max(48, min(144, int(round(pix_size / 8.0) * 8)))
    phase_norm = float(phase) % 1.0
    bucket = int(round(phase_norm * 72.0)) % 72
    key = (sz, bucket)
    cached = _MOON_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_MOON_CACHE) >= 96:
        _MOON_CACHE.clear()

    ph = bucket / 72.0
    ang = ph * 2.0 * math.pi
    sx = math.sin(ang)
    sy = -0.14
    sz_v = -math.cos(ang)
    s_len = math.sqrt(sx * sx + sy * sy + sz_v * sz_v)
    sx, sy, sz_v = sx / s_len, sy / s_len, sz_v / s_len

    buf = bytearray(sz * sz * 4)
    half = sz * 0.5
    inv_r = 1.0 / max(1.0, half - 1.5)

    for py in range(sz):
        ny_base = (py + 0.5 - half) * inv_r
        ny2 = ny_base * ny_base
        row_off = py * sz * 4
        for px in range(sz):
            nx_base = (px + 0.5 - half) * inv_r
            r2 = nx_base * nx_base + ny2
            if r2 > 1.04:
                continue
            r = math.sqrt(r2)
            edge_aa = max(0.0, min(1.0, (1.015 - r) * half * 0.62))
            if edge_aa <= 0.004:
                continue
            rc = min(0.999, r)
            nz = math.sqrt(max(0.0, 1.0 - rc * rc))

            # Procedural spherical regolith albedo: basaltic lunar maria + dusty highland feldspar
            m1 = math.sin(nx_base * 3.1 + ny_base * 1.4 - 0.5) * math.cos(ny_base * 3.5 - nz * 2.1 + 0.4)
            m2 = math.sin(nx_base * 7.4 - nz * 4.2 + 1.1) * math.cos(ny_base * 8.1 + nx_base * 3.6)
            m3 = math.sin(nx_base * 17.5 + ny_base * 15.2) * math.cos(nz * 14.3 - nx_base * 9.8)
            maria_blend = max(0.0, min(1.0, 0.50 + 0.42 * m1 + 0.24 * m2 + 0.09 * m3))
            # Darken maria gently on northwestern/central lunar disc
            basin_bias = math.exp(-((nx_base + 0.16) ** 2 + (ny_base + 0.12) ** 2) * 1.9)
            albedo = 0.72 + 0.26 * maria_blend - 0.18 * basin_bias + 0.05 * m3

            # Bright Tycho rays near southern hemisphere
            tycho_dx = nx_base + 0.08
            tycho_dy = ny_base - 0.56
            tycho_d = math.hypot(tycho_dx, tycho_dy)
            if tycho_d < 0.45:
                ray_a = math.atan2(tycho_dy, tycho_dx)
                ray_w = max(0.0, math.cos(ray_a * 7.0 + 0.4)) ** 4
                albedo += 0.11 * max(0.0, 1.0 - tycho_d / 0.45) * ray_w

            # 3D crater relief perturbing surface normal (pops sharply near terminator!)
            dnx, dny, dnz = nx_base, ny_base, nz
            for cx, cy, cr, depth in _LUNAR_CRATERS:
                dx = nx_base - cx
                dy = ny_base - cy
                dist2 = dx * dx + dy * dy
                if dist2 < cr * cr * 1.35:
                    d = math.sqrt(dist2) / cr
                    if d < 0.85:
                        # Bowl interior slopes away from crater rim center
                        slope = depth * d * 0.55
                        inv_d = 1.0 / max(0.08, d * cr)
                        dnx -= dx * inv_d * slope
                        dny -= dy * inv_d * slope
                        albedo -= 0.06 * depth * (1.0 - d)
                    elif d < 1.15:
                        # Raised bright rim wall
                        rim_k = (1.0 - abs(d - 0.96) / 0.19) * depth * 0.48
                        inv_d = 1.0 / max(0.08, d * cr)
                        dnx += dx * inv_d * rim_k
                        dny += dy * inv_d * rim_k
                        albedo += 0.07 * max(0.0, rim_k)

            n_len = math.sqrt(dnx * dnx + dny * dny + dnz * dnz)
            dnx, dny, dnz = dnx / n_len, dny / n_len, dnz / n_len

            dot = dnx * sx + dny * sy + dnz * sz_v
            # Soft penumbra across spherical lunar terminator + dusty Lommel-Seeliger regolith scatter
            penumbra = max(0.0, min(1.0, (dot + 0.065) / 0.15))
            smooth_t = penumbra * penumbra * (3.0 - 2.0 * penumbra)
            lit = max(0.0, dot)
            lommel = lit / max(0.18, lit + nz * 0.65)
            sunlit = smooth_t * (0.42 * (lit ** 0.58) + 0.58 * lommel)

            # Silvery-blue Earthshine ("пепельный свет Луны") on the unlit night side + thin grazing rim glow
            limb_fresnel = ((1.0 - nz) ** 2.6) * max(0.0, 0.35 - dot)
            earthshine = (0.095 + 0.045 * maria_blend) * (0.65 + 0.35 * nz) + 0.14 * limb_fresnel

            lum_sun = sunlit * albedo * 1.18
            # Warm dusty sunlit ivory-silver vs cool pale-cobalt earthshine tint
            rf = min(255.0, (lum_sun * 246.0 + earthshine * 158.0))
            gf = min(255.0, (lum_sun * 243.0 + earthshine * 176.0))
            bf = min(255.0, (lum_sun * 234.0 + earthshine * 212.0))
            af = edge_aa * (222.0 + 33.0 * min(1.0, sunlit * 1.4))

            idx = row_off + px * 4
            buf[idx] = int(max(0, min(255, rf)))
            buf[idx + 1] = int(max(0, min(255, gf)))
            buf[idx + 2] = int(max(0, min(255, bf)))
            buf[idx + 3] = int(max(0, min(255, af)))

    qimg = QImage(bytes(buf), sz, sz, sz * 4, QImage.Format.Format_RGBA8888)
    pm = QPixmap.fromImage(qimg)
    _MOON_CACHE[key] = pm
    return pm


# Normalized (x, y) constellation coordinates in upper sky band (0..1 width, 0..1 top band height)
_CONSTELLATIONS: tuple[tuple[str, tuple[tuple[float, float, float], ...], tuple[tuple[int, int], ...]], ...] = (
    (
        "Ursa Major",
        (
            (0.06, 0.24, 1.0),
            (0.12, 0.20, 0.92),
            (0.17, 0.27, 0.90),
            (0.22, 0.34, 1.0),
            (0.29, 0.33, 0.88),
            (0.30, 0.47, 0.95),
            (0.22, 0.48, 0.96),
        ),
        ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 3)),
    ),
    (
        "Cassiopeia",
        (
            (0.42, 0.20, 0.92),
            (0.46, 0.33, 0.96),
            (0.51, 0.23, 1.0),
            (0.55, 0.35, 0.90),
            (0.60, 0.19, 0.95),
        ),
        ((0, 1), (1, 2), (2, 3), (3, 4)),
    ),
    (
        "Orion",
        (
            (0.73, 0.17, 1.0),
            (0.84, 0.20, 0.92),
            (0.77, 0.35, 0.96),
            (0.80, 0.34, 0.95),
            (0.83, 0.33, 0.96),
            (0.74, 0.54, 0.92),
            (0.86, 0.51, 1.0),
        ),
        ((0, 2), (1, 4), (2, 3), (3, 4), (2, 5), (4, 6), (5, 6), (0, 1)),
    ),
    (
        "Lyra",
        (
            (0.34, 0.16, 1.0),
            (0.37, 0.26, 0.84),
            (0.32, 0.30, 0.82),
            (0.35, 0.40, 0.85),
            (0.39, 0.37, 0.88),
        ),
        ((0, 1), (0, 2), (1, 2), (2, 3), (3, 4), (4, 1)),
    ),
    (
        "Cygnus",
        (
            (0.63, 0.44, 0.96),
            (0.66, 0.55, 0.90),
            (0.60, 0.59, 0.86),
            (0.71, 0.53, 0.88),
            (0.68, 0.69, 0.92),
        ),
        ((0, 1), (1, 4), (2, 1), (1, 3)),
    ),
)

def _build_night_stars() -> tuple[tuple[float, float, float], ...]:
    rng = random.Random(20261114)
    stars: list[tuple[float, float, float]] = []
    # Rich multi-layer field stars distributed cleanly across upper & mid night canopy
    for i in range(118):
        sx = (i * 0.61803398875 + rng.uniform(-0.025, 0.025)) % 0.98 + 0.01
        sy = rng.uniform(0.04, 0.92)
        mag = rng.choice((0.32, 0.38, 0.45, 0.52, 0.60, 0.72, 0.84, 0.95))
        stars.append((round(sx, 4), round(sy, 4), mag))
    # Subtle faint Milky Way band arcing diagonally across sky
    for _ in range(42):
        t = rng.uniform(0.03, 0.97)
        band_y = 0.66 - t * 0.48 + rng.gauss(0.0, 0.085)
        if 0.03 <= band_y <= 0.94:
            stars.append((round(t, 4), round(band_y, 4), round(rng.uniform(0.24, 0.54), 2)))
    return tuple(stars)

_BACKGROUND_STARS: tuple[tuple[float, float, float], ...] = _build_night_stars()


def calc_moon_phase(now: datetime | None = None, *, force_phase: float | None = None) -> tuple[float, str, str]:
    """Return (phase_0_to_1, ru_label, en_label) from real astronomical synodic cycle."""
    if force_phase is not None:
        p = max(0.0, min(0.999, float(force_phase)))
    else:
        dt = now or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.astimezone(timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        days = (dt - _NEW_MOON_EPOCH).total_seconds() / 86400.0
        p = (days % _SYNODIC_DAYS) / _SYNODIC_DAYS

    if p < 0.04 or p >= 0.96:
        return p, "Новолуние", "New Moon"
    if p < 0.22:
        return p, "Молодой серп", "Waxing Crescent"
    if p < 0.29:
        return p, "Первая четверть", "First Quarter"
    if p < 0.46:
        return p, "Прибывающая луна", "Waxing Gibbous"
    if p <= 0.54:
        return p, "Полнолуние", "Full Moon"
    if p < 0.72:
        return p, "Убывающая луна", "Waning Gibbous"
    if p < 0.79:
        return p, "Последняя четверть", "Last Quarter"
    return p, "Старый серп", "Waning Crescent"


def solar_elevation_deg(lat: float, lon: float, now: datetime | None = None) -> float:
    """Compute approximate solar altitude (degrees above horizon) from WGS84 lat/lon using NOAA equations."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    n_day = dt.timetuple().tm_yday
    hr_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    gamma = (2.0 * math.pi / 365.0) * (n_day - 1 + (hr_utc - 12.0) / 24.0)

    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.001480 * math.sin(3.0 * gamma)
    )
    tst = (hr_utc * 60.0 + eqtime + 4.0 * lon) % 1440.0
    ha = math.radians(tst / 4.0 - 180.0)
    lat_r = math.radians(lat)
    sin_alt = (
        math.sin(lat_r) * math.sin(decl)
        + math.cos(lat_r) * math.cos(decl) * math.cos(ha)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def resolve_time_segment(
    now: datetime | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
) -> str:
    """Classify local solar state into 'sunrise' | 'day' | 'sunset' | 'night' using coordinates when present."""
    if lat is not None and lon is not None and not (abs(lat) < 1e-6 and abs(lon) < 1e-6):
        dt = now or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.astimezone(timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        alt = solar_elevation_deg(lat, lon, dt)
        if alt >= 5.0:
            return "day"
        if alt < -6.0:
            return "night"
        n_day = dt.timetuple().tm_yday
        hr_utc = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
        gamma = (2.0 * math.pi / 365.0) * (n_day - 1 + (hr_utc - 12.0) / 24.0)
        eqtime = 229.18 * (
            0.000075
            + 0.001868 * math.cos(gamma)
            - 0.032077 * math.sin(gamma)
            - 0.014615 * math.cos(2.0 * gamma)
            - 0.040849 * math.sin(2.0 * gamma)
        )
        tst = (hr_utc * 60.0 + eqtime + 4.0 * lon) % 1440.0
        ha_deg = tst / 4.0 - 180.0
        return "sunrise" if ha_deg < 0.0 else "sunset"

    dt = now or datetime.now()
    hf = dt.hour + dt.minute / 60.0
    if 5.25 <= hf < 8.5:
        return "sunrise"
    if 8.5 <= hf < 17.0:
        return "day"
    if 17.0 <= hf < 20.5:
        return "sunset"
    return "night"


def resolve_weather_kind(symbol: str = "") -> str:
    """Normalize MET Norway symbol string into 'clear'|'partly'|'cloudy'|'fog'|'rain'|'thunder'|'lightning'|'snow'."""
    s = (symbol or "").split("_")[0].lower()
    if "lightning" in s:
        return "lightning"
    if "thunder" in s:
        return "thunder"
    if "rain" in s or "drizzle" in s or "shower" in s:
        return "rain"
    if "snow" in s or "sleet" in s:
        return "snow"
    if "fog" in s:
        return "fog"
    if "partly" in s or "fair" in s:
        return "partly"
    if "cloudy" in s or "overcast" in s:
        return "cloudy"
    if "clear" in s:
        return "clear"
    return "partly" if s else "partly"


ATMO_MODES: tuple[tuple[str, str], ...] = (
    ("auto", "Авто (погода и время)"),
    ("sunset", "Закат (солнце и трава)"),
    ("sunrise", "Восход"),
    ("day_clear", "День • Ясно (солнце и облака)"),
    ("day_cloudy", "День • Облачно / рассеянность"),
    ("day_overcast", "День • Пасмурно"),
    ("day_rain", "Дождь • Мокрая трава"),
    ("day_thunder", "Гром • Молнии в облаках"),
    ("day_lightning", "Шторм • Молнии в землю"),
    ("night_full_moon", "Ночь • Созвездия + Полнолуние"),
    ("night_new_moon", "Ночь • Созвездия + Новолуние"),
    ("night_crescent", "Ночь • Созвездия + Молодой серп"),
)

SKY_MODES: tuple[tuple[str, str], ...] = (
    ("auto", "Авто (город)"),
    ("sunrise", "Восход"),
    ("day", "День"),
    ("sunset", "Закат"),
    ("night", "Ночь"),
)

WEATHER_MODES: tuple[tuple[str, str], ...] = (
    ("auto", "Авто (город)"),
    ("clear", "Ясно"),
    ("partly", "Облачно"),
    ("cloudy", "Пасмурно"),
    ("rain", "Дождь"),
    ("thunder", "Гром (молнии в облаках)"),
    ("lightning", "Шторм (молнии в землю)"),
    ("snow", "Снег"),
)

MOON_MODES: tuple[tuple[str, str], ...] = (
    ("auto", "Авто (реальная фаза)"),
    ("new", "Новолуние"),
    ("waxing_crescent", "Молодой серп"),
    ("first_quarter", "Первая четверть"),
    ("waxing_gibbous", "Прибывающая луна"),
    ("full", "Полнолуние"),
    ("waning_gibbous", "Убывающая луна"),
    ("last_quarter", "Последняя четверть"),
    ("waning_crescent", "Старый серп"),
)

_MOON_PHASE_MAP: dict[str, float] = {
    "new": 0.00,
    "waxing_crescent": 0.12,
    "first_quarter": 0.25,
    "waxing_gibbous": 0.38,
    "full": 0.50,
    "waning_gibbous": 0.62,
    "last_quarter": 0.75,
    "waning_crescent": 0.88,
}


def moon_mode_to_phase(moon_mode: str = "auto") -> float | None:
    """Map manual lunar phase key to normalized [0..1) synodic phase or None when auto."""
    key = (moon_mode or "auto").strip().lower()
    return _MOON_PHASE_MAP.get(key)


def resolve_scene_state(
    preview: str = "auto",
    weather_symbol: str = "",
    now: datetime | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
    sky_mode: str = "auto",
    weather_mode: str = "auto",
    moon_mode: str = "auto",
) -> tuple[str, str, float | None]:
    """Return (time_segment, weather_kind, forced_moon_phase) honoring city coordinates, live weather, and manual overrides."""
    mode = (preview or "auto").strip().lower()
    if mode == "sunrise":
        seg, wkind, forced_moon = "sunrise", "partly", None
    elif mode == "day_clear":
        seg, wkind, forced_moon = "day", "clear", None
    elif mode == "day_cloudy":
        seg, wkind, forced_moon = "day", "partly", None
    elif mode == "day_overcast":
        seg, wkind, forced_moon = "day", "cloudy", None
    elif mode == "day_rain":
        seg, wkind, forced_moon = "day", "rain", None
    elif mode == "day_thunder":
        seg, wkind, forced_moon = "day", "thunder", None
    elif mode == "day_lightning":
        seg, wkind, forced_moon = "day", "lightning", None
    elif mode == "sunset":
        seg, wkind, forced_moon = "sunset", "partly", None
    elif mode == "night_full_moon":
        seg, wkind, forced_moon = "night", "clear", 0.50
    elif mode == "night_new_moon":
        seg, wkind, forced_moon = "night", "clear", 0.00
    elif mode == "night_crescent":
        seg, wkind, forced_moon = "night", "clear", 0.12
    else:
        seg = resolve_time_segment(now, lat=lat, lon=lon)
        wkind = resolve_weather_kind(weather_symbol)
        forced_moon = None

    sky_ov = (sky_mode or "auto").strip().lower()
    if sky_ov in ("sunrise", "day", "sunset", "night"):
        seg = sky_ov

    w_ov = (weather_mode or "auto").strip().lower()
    if w_ov in ("clear", "partly", "cloudy", "overcast", "fog", "rain", "thunder", "lightning", "snow"):
        wkind = "cloudy" if w_ov == "overcast" else w_ov

    m_ov = moon_mode_to_phase(moon_mode)
    if m_ov is not None:
        forced_moon = m_ov

    return seg, wkind, forced_moon


def describe_atmosphere_badge(
    preview: str = "auto",
    weather_symbol: str = "",
    now: datetime | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
    sky_mode: str = "auto",
    weather_mode: str = "auto",
    moon_mode: str = "auto",
) -> str:
    """Short Russian pill label showing live sky state, clouds/clear, sun, or lunar phase."""
    segment, wkind, forced_moon = resolve_scene_state(
        preview,
        weather_symbol,
        now,
        lat=lat,
        lon=lon,
        sky_mode=sky_mode,
        weather_mode=weather_mode,
        moon_mode=moon_mode,
    )
    if segment == "night":
        _, moon_ru, _ = calc_moon_phase(now, force_phase=forced_moon)
        if wkind in ("rain", "thunder", "lightning"):
            return f"{'Гроза' if wkind in ('thunder', 'lightning') else 'Дождь'} • {moon_ru}"
        if wkind in ("cloudy", "fog"):
            return f"Пасмурно • {moon_ru}"
        return f"Ночь • {moon_ru}"

    w_tag = {
        "clear": "Ясно",
        "partly": "Облачно",
        "cloudy": "Пасмурно",
        "fog": "Пасмурно",
        "rain": "Дождь",
        "thunder": "Гром",
        "lightning": "Молнии",
        "snow": "Снег",
    }.get(wkind, "Облачно")

    if segment == "sunrise":
        return f"Восход • {w_tag}"
    if segment == "sunset":
        return f"Закат • {w_tag}"
    if wkind == "clear":
        return "Ясно • Солнце"
    if wkind == "lightning":
        return "Молнии • Пасмурно"
    if wkind in ("rain", "thunder"):
        return f"{w_tag} • Пасмурно"
    if wkind == "snow":
        return "Снег • Пасмурно"
    return f"Пасмурно • {w_tag}" if wkind in ("cloudy", "fog") else w_tag


def _solar_night_span_u(
    now_utc: datetime,
    lat: float,
    lon: float,
) -> tuple[float, bool]:
    """Return (progress_0_to_1, is_daylight_or_twilight) calibrated to actual local sunset..sunrise."""
    alt_now = solar_elevation_deg(lat, lon, now_utc)
    if alt_now >= -6.0:
        # Locate previous sunrise (-6 deg) and next sunset (-6 deg) within +/- 18h for continuous day arc
        step = timedelta(minutes=15)
        sr = now_utc
        for _ in range(72):
            if solar_elevation_deg(lat, lon, sr - step) < -6.0 <= solar_elevation_deg(lat, lon, sr):
                break
            sr -= step
        ss = now_utc
        for _ in range(72):
            if solar_elevation_deg(lat, lon, ss) >= -6.0 > solar_elevation_deg(lat, lon, ss + step):
                ss += step
                break
            ss += step
        span_s = max(3600.0, (ss - sr).total_seconds())
        return max(0.0, min(1.0, (now_utc - sr).total_seconds() / span_s)), True

    # Nighttime: locate previous sunset (-6 deg) and next sunrise (-6 deg)
    step = timedelta(minutes=15)
    prev_ss = now_utc
    for _ in range(72):
        if solar_elevation_deg(lat, lon, prev_ss - step) >= -6.0 > solar_elevation_deg(lat, lon, prev_ss):
            break
        prev_ss -= step
    next_sr = now_utc
    for _ in range(72):
        if solar_elevation_deg(lat, lon, next_sr) < -6.0 <= solar_elevation_deg(lat, lon, next_sr + step):
            next_sr += step
            break
        next_sr += step
    span_s = max(3600.0, (next_sr - prev_ss).total_seconds())
    return max(0.0, min(1.0, (now_utc - prev_ss).total_seconds() / span_s)), False


def compute_celestial_layout(
    core: QRectF,
    segment: str,
    now: datetime | None = None,
    *,
    preview_mode: str = "auto",
    sky_mode: str = "auto",
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[QPointF | None, QPointF | None]:
    """Return continuous smooth centered positions (sun_center, moon_center) with strict mutual exclusion.

    Moon NEVER appears while Sun is present; once Sun sets/leaves, Moon rises smoothly across the sky.
    """
    dt = now or datetime.now()
    exact_min = dt.hour * 60.0 + dt.minute + dt.second / 60.0
    w = max(80.0, core.width())
    h = max(120.0, core.height())
    y_bot = core.top() + h * 0.67
    y_mid = core.top() + h * 0.36
    y_top = core.top() + max(24.0, h * 0.085)

    manual_seg = (sky_mode or "auto").strip().lower()
    if manual_seg == "auto" and preview_mode and preview_mode != "auto":
        manual_seg = segment
    else:
        manual_seg = manual_seg if manual_seg in ("sunrise", "day", "sunset", "night") else "auto"

    minute_nudge = ((dt.minute + dt.second / 60.0) / 59.0 - 0.5) * 0.08
    cx = core.center().x() + math.sin((exact_min / 1440.0) * 2.0 * math.pi) * min(22.0, w * 0.04)

    if manual_seg != "auto":
        if manual_seg == "sunrise":
            u = max(0.02, min(0.24, 0.12 + minute_nudge * 0.6))
            sun_y = y_bot + (y_mid - y_bot) * (u / 0.30)
            return QPointF(cx, sun_y), None
        if manual_seg == "day":
            u = max(0.32, min(0.68, 0.50 + minute_nudge))
            if u <= 0.50:
                sun_y = y_bot + (y_mid - y_bot) * (u / 0.50)
            else:
                sun_y = y_mid + (y_top - y_mid) * ((u - 0.50) / 0.50)
            return QPointF(cx, sun_y), None
        if manual_seg == "sunset":
            # Sun still visible lowering/exiting -> strictly no Moon until Sun is gone
            u = max(0.76, min(0.96, 0.86 + minute_nudge * 0.6))
            sun_y = y_mid + (y_top - y_mid) * ((u - 0.50) / 0.50)
            return QPointF(cx, sun_y), None
        # Night manual preview: continuous moon glide from horizon to zenith across 9h cycle
        night_phase = (((exact_min - 1020.0) % 720.0) / 720.0)
        moon_y = y_bot + (y_top - y_bot) * (0.14 + 0.72 * night_phase)
        return None, QPointF(cx, moon_y)

    # Automatic solar-calibrated or local clock progression
    has_coords = lat is not None and lon is not None and not (abs(lat) < 1e-6 and abs(lon) < 1e-6)
    if has_coords:
        dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.astimezone(timezone.utc)
        u_span, is_sun_up = _solar_night_span_u(dt_utc, float(lat), float(lon))
        if is_sun_up and segment != "night":
            if u_span <= 0.50:
                sun_y = y_bot + (y_mid - y_bot) * (u_span / 0.50)
            else:
                sun_y = y_mid + (y_top - y_mid) * ((u_span - 0.50) / 0.50)
            return QPointF(cx, sun_y), None
        # Sun is gone -> smooth moon glide across true sunset..sunrise night span (never frozen!)
        moon_y = y_bot + (y_top - y_bot) * (0.06 + 0.86 * u_span)
        return None, QPointF(cx, moon_y)

    if 330.0 <= exact_min <= 1230.0 and segment != "night":
        day_u = (exact_min - 330.0) / 900.0
        if day_u <= 0.50:
            sun_y = y_bot + (y_mid - y_bot) * (day_u / 0.50)
        else:
            sun_y = y_mid + (y_top - y_mid) * ((day_u - 0.50) / 0.50)
        return QPointF(cx, sun_y), None

    night_min = (exact_min - 1230.0) % 1440.0
    night_u = max(0.0, min(1.0, night_min / 540.0))
    moon_y = y_bot + (y_top - y_bot) * (0.06 + 0.86 * night_u)
    return None, QPointF(cx, moon_y)


def compute_blade_joints(
    bx: float,
    root_y: float,
    bh: float,
    angle: float,
    curl: float,
) -> list[tuple[float, float, float]]:
    """Forward kinematics for a 4-bone, 5-joint articulated grass stem with constant arc length."""
    seg_fracs = (0.24, 0.27, 0.27, 0.22)
    bone_angles = (
        angle * 0.42 + curl * 0.08,
        angle * 0.78 + curl * 0.36,
        angle * 1.06 + curl * 0.78,
        angle * 1.24 + curl * 1.18,
    )
    joints: list[tuple[float, float, float]] = [(bx, root_y, bone_angles[0])]
    cx, cy = bx, root_y
    floor_y = root_y - 1.5
    for s_frac, th in zip(seg_fracs, bone_angles):
        seg_l = bh * s_frac
        cx = cx + seg_l * math.sin(th)
        cy = min(floor_y, cy - seg_l * math.cos(th))
        joints.append((cx, cy, th))
    return joints


class GrassSkeletonSim:
    """Stateful per-blade damped spring-damper skeletal simulation (randomized varied heights, reseeded invisibly on open)."""

    __slots__ = (
        "n_blades",
        "angles",
        "ang_vels",
        "curls",
        "curl_vels",
        "target_angles",
        "target_curls",
        "sides",
        "heights",
        "x_jits",
        "w_scales",
        "shade_indices",
        "bias_rads",
        "_last_ts",
        "_prev_cx",
        "_prev_cy",
        "_has_prev_cx",
        "_cursor_vx",
        "_settling",
        "_canopy_img",
        "_canopy_key",
        "_canopy_phase",
    )

    def __init__(self) -> None:
        self.n_blades = 0
        self.angles: list[float] = []
        self.ang_vels: list[float] = []
        self.curls: list[float] = []
        self.curl_vels: list[float] = []
        self.target_angles: list[float] = []
        self.target_curls: list[float] = []
        self.sides: list[float] = []
        self.heights: list[float] = []
        self.x_jits: list[float] = []
        self.w_scales: list[float] = []
        self.shade_indices: list[int] = []
        self.bias_rads: list[float] = []
        self._last_ts = 0.0
        self._prev_cx = -999.0
        self._prev_cy = -999.0
        self._has_prev_cx = False
        self._cursor_vx = 0.0
        self._settling = False
        self._canopy_img: QImage | None = None
        self._canopy_key: tuple | None = None
        self._canopy_phase: float = -1e9

    @staticmethod
    def _sample_blade_traits(rng: random.Random, idx: int, px_cm: float) -> tuple[float, float, float, int, float]:
        """Generate random blade length (0.85cm..6.92cm, always < 7cm max edge), jitter, width, tone, bias."""
        tier = rng.random()
        if tier < 0.35:
            cm_len = rng.uniform(0.85, 2.35)
        elif tier < 0.76:
            cm_len = rng.uniform(2.35, 4.75)
        else:
            # Tall meadow stalks capped cleanly in 4.85..6.92 cm (< 7.0 cm ceiling)
            cm_len = rng.uniform(4.85, 6.92)
        bh = max(16.0, min(px_cm * 6.95, cm_len * px_cm))
        x_jit = rng.uniform(-3.1, 3.1)
        w_sc = rng.uniform(0.60, 1.32) if cm_len > 1.4 else rng.uniform(0.55, 1.05)
        shade_i = (idx + rng.randint(0, 5)) % 6
        bias = rng.uniform(-0.14, 0.14)
        return bh, x_jit, w_sc, shade_i, bias

    def reseed(self) -> None:
        """Silently regenerate randomized grass heights & organic layout for the next island open."""
        self.n_blades = 0
        self.angles.clear()
        self.ang_vels.clear()
        self.curls.clear()
        self.curl_vels.clear()
        self.target_angles.clear()
        self.target_curls.clear()
        self.sides.clear()
        self.heights.clear()
        self.x_jits.clear()
        self.w_scales.clear()
        self.shade_indices.clear()
        self.bias_rads.clear()
        self._prev_cx = -999.0
        self._prev_cy = -999.0
        self._has_prev_cx = False
        self._cursor_vx = 0.0
        self._settling = False
        self._canopy_img = None
        self._canopy_key = None
        self._canopy_phase = -1e9

    def ensure_blades(self, count: int) -> None:
        if count == self.n_blades:
            return
        old_n = self.n_blades
        self.n_blades = count
        rng = random.Random(secrets.randbits(64))
        px_cm = _px_per_cm()
        if old_n == 0:
            self.heights = []
            self.x_jits = []
            self.w_scales = []
            self.shade_indices = []
            self.bias_rads = []
            for i in range(count):
                bh, xj, ws, sh, br = self._sample_blade_traits(rng, i, px_cm)
                self.heights.append(bh)
                self.x_jits.append(xj)
                self.w_scales.append(ws)
                self.shade_indices.append(sh)
                self.bias_rads.append(br)
            self.angles = list(self.bias_rads)
            self.ang_vels = [0.0] * count
            self.curls = [b * 0.35 for b in self.bias_rads]
            self.curl_vels = [0.0] * count
            self.target_angles = list(self.angles)
            self.target_curls = list(self.curls)
            self.sides = [0.0] * count
            return
        keep = min(old_n, count)
        self.angles = self.angles[:keep]
        self.ang_vels = self.ang_vels[:keep]
        self.curls = self.curls[:keep]
        self.curl_vels = self.curl_vels[:keep]
        self.target_angles = self.target_angles[:keep]
        self.target_curls = self.target_curls[:keep]
        self.sides = self.sides[:keep]
        self.heights = self.heights[:keep]
        self.x_jits = self.x_jits[:keep]
        self.w_scales = self.w_scales[:keep]
        self.shade_indices = self.shade_indices[:keep]
        self.bias_rads = self.bias_rads[:keep]
        for i in range(keep, count):
            bh, xj, ws, sh, br = self._sample_blade_traits(rng, i, px_cm)
            self.heights.append(bh)
            self.x_jits.append(xj)
            self.w_scales.append(ws)
            self.shade_indices.append(sh)
            self.bias_rads.append(br)
            self.angles.append(br)
            self.ang_vels.append(0.0)
            self.curls.append(br * 0.35)
            self.curl_vels.append(0.0)
            self.target_angles.append(br)
            self.target_curls.append(br * 0.35)
            self.sides.append(0.0)

    def is_settling(self) -> bool:
        return self._settling

    def step(
        self,
        n_blades: int,
        eff_left: float,
        eff_width: float,
        root_y: float,
        wet: bool,
        phase_t: float,
        cursor_local: QPointF | None,
        vert_inf: float,
        has_cursor: bool,
        music_energy: float,
        music_bands: list[float] | None,
        dt: float | None = None,
    ) -> list[tuple[float, float, float, float]]:
        """Advance spring physics for all blades and return [(bx, bh, angle, curl), ...]."""
        self.ensure_blades(n_blades)
        now_ts = time.monotonic()
        if dt is None:
            raw_dt = (now_ts - self._last_ts) if self._last_ts > 0.0 else 0.026
            dt_eff = max(0.008, min(0.034, raw_dt))
        else:
            dt_eff = max(0.008, min(0.034, float(dt)))
        self._last_ts = now_ts

        prev_cx = self._prev_cx
        has_prev = self._has_prev_cx
        if has_cursor and cursor_local is not None and vert_inf > 0.02:
            if self._has_prev_cx:
                raw_dx = float(cursor_local.x()) - self._prev_cx
                # Clamp single-frame jump so fast swipe momentum stays smooth & bounded
                clamped_dx = max(-85.0, min(85.0, raw_dx))
                inst_vx = max(-680.0, min(680.0, clamped_dx / max(0.014, dt_eff)))
                self._cursor_vx = self._cursor_vx * 0.72 + inst_vx * 0.28
            else:
                self._cursor_vx = 0.0
                self._has_prev_cx = True
                prev_cx = float(cursor_local.x())
            self._prev_cx = float(cursor_local.x())
            self._prev_cy = float(cursor_local.y())
        else:
            self._has_prev_cx = False
            self._cursor_vx *= 0.80

        wind_rad_amp = 0.088 if wet else 0.056
        has_music = music_energy > 0.02
        n_mb = len(music_bands) if music_bands else 0
        part_radius = 48.0
        sub_dt = dt_eff * 0.5
        any_moving = False
        out: list[tuple[float, float, float, float]] = []
        append_out = out.append

        cx_val = float(cursor_local.x()) if cursor_local is not None else -999.0
        cy_val = float(cursor_local.y()) if cursor_local is not None else -999.0
        cursor_active = bool(has_cursor and vert_inf > 0.02 and cursor_local is not None)
        seg_min = min(prev_cx, cx_val) if (cursor_active and has_prev and abs(cx_val - prev_cx) <= 130.0) else cx_val
        seg_max = max(prev_cx, cx_val) if (cursor_active and has_prev and abs(cx_val - prev_cx) <= 130.0) else cx_val

        m_sin = math.sin
        m_cos = math.cos
        max_h_cap = _px_per_cm() * 6.98
        wet_h_mult = 1.06 if wet else 1.0
        wind_freq = 2.05 if wet else 1.32
        ease_r = min(1.0, dt_eff * 5.8)
        follow = min(1.0, dt_eff * 24.0)
        angles = self.angles
        ang_vels = self.ang_vels
        curls = self.curls
        curl_vels = self.curl_vels
        target_angles = self.target_angles
        target_curls = self.target_curls
        sides = self.sides
        heights = self.heights
        x_jits = self.x_jits
        bias_rads = self.bias_rads

        for i in range(n_blades):
            frac = (i + 0.5) / n_blades
            bx = eff_left + frac * eff_width + x_jits[i]
            if has_music:
                band_v = float(music_bands[min(n_mb - 1, int(frac * n_mb))]) if n_mb > 0 else music_energy
                bh = min(max_h_cap, heights[i] * wet_h_mult * (1.0 + 0.10 * music_energy + 0.16 * band_v))
                wind_rad = (
                    m_sin(phase_t * wind_freq + i * 0.43) * wind_rad_amp
                    + m_sin(phase_t * 3.8 + i * 0.55) * (0.036 + 0.09 * band_v + 0.048 * music_energy)
                )
            else:
                bh = min(max_h_cap, heights[i] * wet_h_mult)
                wind_rad = m_sin(phase_t * wind_freq + i * 0.43) * wind_rad_amp
            bias_rad = bias_rads[i]
            rest_angle = bias_rad + wind_rad
            rest_curl = wind_rad * 0.55 + bias_rad * 0.35

            ang = angles[i]
            avel = ang_vels[i]
            crl = curls[i]
            cvel = curl_vels[i]
            t_ang = target_angles[i]
            t_crl = target_curls[i]
            pushed = False

            if cursor_active:
                if bx < seg_min:
                    adx = seg_min - bx
                elif bx > seg_max:
                    adx = bx - seg_max
                else:
                    adx = 0.0
                dx = bx - cx_val

                if adx < part_radius:
                    pushed = True
                    side = sides[i]
                    if side == 0.0:
                        if abs(ang - rest_angle) > 0.07:
                            side = 1.0 if (ang - rest_angle) > 0.0 else -1.0
                        elif abs(self._cursor_vx) > 65.0:
                            side = 1.0 if self._cursor_vx > 0.0 else -1.0
                        elif abs(dx) > 4.0:
                            side = 1.0 if dx > 0.0 else -1.0
                        else:
                            side = 1.0 if (i % 2 == 0) else -1.0
                        sides[i] = side
                    bell = 0.5 * (1.0 + m_cos(math.pi * adx / part_radius)) * vert_inf
                    hit_frac = max(0.18, min(1.0, (root_y - cy_val) / max(20.0, bh)))
                    swipe = max(-0.28, min(0.28, self._cursor_vx / 1350.0)) * bell
                    goal_ang = rest_angle + side * bell * (1.08 + 0.44 * (1.0 - hit_frac * 0.45)) + swipe
                    goal_crl = rest_curl + side * bell * (0.54 + 0.54 * hit_frac)
                    t_ang += (goal_ang - t_ang) * follow
                    t_crl += (goal_crl - t_crl) * follow
                else:
                    if abs(ang - rest_angle) < 0.045 and abs(avel) < 0.14:
                        sides[i] = 0.0
                    t_ang += (rest_angle - t_ang) * ease_r
                    t_crl += (rest_curl - t_crl) * ease_r
            else:
                if abs(ang - rest_angle) < 0.045 and abs(avel) < 0.14:
                    sides[i] = 0.0
                t_ang += (rest_angle - t_ang) * ease_r
                t_crl += (rest_curl - t_crl) * ease_r

            # Asymmetric critically-damped recovery (fast push response, smooth recovery)
            k_a = 128.0 if pushed else 28.0
            c_a = 19.5 if pushed else 11.4
            k_c = 138.0 if pushed else 32.0
            c_c = 20.5 if pushed else 12.2

            for _ in range(2):
                acc = (t_ang - ang) * k_a - avel * c_a
                avel += acc * sub_dt
                ang += avel * sub_dt

                c_acc = (t_crl - crl) * k_c - cvel * c_c
                cvel += c_acc * sub_dt
                crl += cvel * sub_dt

            if ang < -1.38:
                ang = -1.38
            elif ang > 1.38:
                ang = 1.38
            if crl < -1.26:
                crl = -1.26
            elif crl > 1.26:
                crl = 1.26
            target_angles[i] = t_ang
            target_curls[i] = t_crl
            angles[i] = ang
            ang_vels[i] = avel
            curls[i] = crl
            curl_vels[i] = cvel

            if (
                pushed
                or sides[i] != 0.0
                or abs(ang - rest_angle) > 0.105
                or abs(crl - rest_curl) > 0.105
                or abs(avel) > 0.22
                or abs(cvel) > 0.22
            ):
                any_moving = True

            append_out((bx, bh, ang, crl))

        self._settling = any_moving
        return out


class _CloudWisp:
    """Compact slotted cloud smoke particle supporting fast attribute access + legacy dict indexing."""

    __slots__ = (
        "u",
        "u_dir",
        "y_off",
        "dx",
        "dy",
        "vx",
        "vy",
        "radius",
        "stretch",
        "rot",
        "v_rot",
        "age",
        "life",
        "disp",
        "density",
        "layer",
        "seed",
    )

    def __init__(
        self,
        u: float,
        y_off: float,
        vx: float,
        vy: float,
        radius: float,
        stretch: float,
        rot: float,
        v_rot: float,
        age: float,
        life: float,
        density: float,
        layer: int,
        seed: float,
        u_dir: float = 1.0,
    ) -> None:
        self.u = u
        self.u_dir = 1.0 if u_dir >= 0.0 else -1.0
        self.y_off = y_off
        self.dx = 0.0
        self.dy = 0.0
        self.vx = vx
        self.vy = vy
        self.radius = radius
        self.stretch = stretch
        self.rot = rot
        self.v_rot = v_rot
        self.age = age
        self.life = life
        self.disp = 0.0
        self.density = density
        self.layer = layer
        self.seed = seed

    def __getitem__(self, key: str) -> float | int:
        return getattr(self, key)

    def __setitem__(self, key: str, value: float | int) -> None:
        setattr(self, key, value)


class CloudSmokeField:
    """Volumetric 2x translucent cloud smoke simulation streaming rightward by wind m/s and dispersing around cursor."""

    _POOL_SIZE = 368

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._last_ts = 0.0
        self._prev_cursor: QPointF | None = None
        self._cursor_vx = 0.0
        self._cursor_vy = 0.0
        self._wisps: list[_CloudWisp] = []
        self._active = False
        self._buf_img: QImage | None = None
        self._buf_key: tuple | None = None
        self._buf_u_shift: float = 0.0
        self._buf_ts: float = 0.0
        self._buf_loci: list[QPointF] = []
        self._seed_wisps()

    def _spawn_wisp(self, idx: int, *, age_frac: float = 0.0) -> _CloudWisp:
        life = self._rng.uniform(5.4, 11.6)
        layer = 0 if (idx % 4 == 0) else (2 if (idx % 3 == 0) else 1)
        band_y = (
            self._rng.uniform(20.0, 96.0)
            if layer == 0
            else (self._rng.uniform(10.0, 80.0) if layer == 1 else self._rng.uniform(4.0, 62.0))
        )
        return _CloudWisp(
            u=0.04 + (idx / max(1, self._POOL_SIZE)) * 0.92 + self._rng.uniform(-0.025, 0.025),
            y_off=band_y,
            vx=self._rng.uniform(-1.5, 5.5),
            vy=self._rng.uniform(-4.0, 2.2),
            radius=self._rng.uniform(34.0, 86.0),
            stretch=self._rng.uniform(1.36, 2.52),
            rot=self._rng.uniform(-21.0, 21.0),
            v_rot=self._rng.uniform(-4.0, 4.0),
            age=life * age_frac,
            life=life,
            density=self._rng.uniform(0.76, 1.0),
            layer=layer,
            seed=self._rng.uniform(0.0, 6.283),
            u_dir=1.0 if (idx % 5 != 0) else -1.0,
        )

    def _seed_wisps(self) -> None:
        self._wisps = [self._spawn_wisp(i, age_frac=self._rng.uniform(0.05, 0.88)) for i in range(self._POOL_SIZE)]

    def is_dispersing(self) -> bool:
        return self._active

    def step(
        self,
        sky_rect: QRectF,
        cloud_amt: float,
        wind_ms: float,
        phase_t: float,
        cursor_local: QPointF | None,
        *,
        dt: float | None = None,
        drift_offset: float = 0.0,
        quality: float = 1.0,
        outer_rect: QRectF | None = None,
    ) -> list[tuple[float, float, float, float, float, float, float, int]]:
        """Advance volumetric cloud smoke particles and return drawable wisp tuples."""
        now_ts = time.monotonic()
        if dt is None:
            raw_dt = (now_ts - self._last_ts) if self._last_ts > 0.0 else 0.032
            dt_eff = max(0.010, min(0.065, raw_dt))
        else:
            dt_eff = max(0.010, min(0.12, float(dt)))
        self._last_ts = now_ts

        w = max(120.0, sky_rect.width())
        x0 = sky_rect.left()
        y0 = sky_rect.top()
        eff_left = min(x0, outer_rect.left()) if (outer_rect is not None and not outer_rect.isEmpty()) else x0
        eff_right = max(x0 + w, outer_rect.right()) if (outer_rect is not None and not outer_rect.isEmpty()) else (x0 + w)
        top_limit = y0 + 6.0
        bot_limit = max(top_limit + 28.0, y0 + min(158.0, sky_rect.height() * 0.44))

        has_cursor = (
            cursor_local is not None
            and cursor_local.x() > -120.0
            and (y0 - 45.0) <= cursor_local.y() <= (y0 + 185.0)
        )
        if has_cursor and cursor_local is not None:
            if self._prev_cursor is not None:
                alpha_v = min(1.0, dt_eff * 14.0)
                vx_inst = max(-950.0, min(950.0, (cursor_local.x() - self._prev_cursor.x()) / max(0.012, dt_eff)))
                vy_inst = max(-950.0, min(950.0, (cursor_local.y() - self._prev_cursor.y()) / max(0.012, dt_eff)))
                self._cursor_vx += (vx_inst - self._cursor_vx) * alpha_v
                self._cursor_vy += (vy_inst - self._cursor_vy) * alpha_v
            self._prev_cursor = QPointF(cursor_local)
        else:
            decay_v = math.exp(-dt_eff / 0.14)
            self._cursor_vx *= decay_v
            self._cursor_vy *= decay_v
            self._prev_cursor = None

        q_cl = max(0.25, min(1.0, float(quality)))
        base_n = 112 + (self._POOL_SIZE - 112) * max(0.0, min(1.0, cloud_amt))
        active_count = max(32, min(self._POOL_SIZE, int(round(base_n * q_cl))))
        wind_eff = max(0.0, min(40.0, float(wind_ms)))
        # Strictly wind-scaled rightward velocity (+x in px/s converted to normalized span u)
        wind_px_per_s = 2.5 + wind_eff * 9.5 + (wind_eff ** 1.18) * 1.35
        drift_u_per_s = wind_px_per_s / w
        drift_step = drift_u_per_s * dt_eff + drift_offset
        self._buf_u_shift += drift_step
        calm_sway = min(1.85, 0.08 + wind_eff * 0.09)
        part_r = 152.0
        part_ry_cap = 152.0 / 1.18
        any_disp = False

        # Hoist loop-invariant exponentials and math bindings (eliminates ~1,100 math.exp calls/frame)
        drag_v = math.exp(-dt_eff / 0.95)
        drag_dx = math.exp(-dt_eff / 2.6)
        drag_dy = math.exp(-dt_eff / 2.4)
        phase_y = phase_t * 0.45 * calm_sway
        phase_ax = phase_t * 0.65 * calm_sway
        phase_ay = phase_t * 0.55 * calm_sway
        amp_y = 2.6 * calm_sway
        amp_ax = 10.0 * calm_sway * dt_eff
        amp_ay = 7.0 * calm_sway * dt_eff
        buoy_ay = 1.8 * calm_sway * dt_eff
        disp_decay = dt_eff * 0.22
        rot_cur_k = self._cursor_vx * 0.015
        has_cur = bool(has_cursor and cursor_local is not None)
        cur_x = cursor_local.x() if has_cur and cursor_local is not None else -9999.0
        cur_y = cursor_local.y() if has_cur and cursor_local is not None else -9999.0
        cur_vx024 = self._cursor_vx * 0.24
        cur_vy022 = self._cursor_vy * 0.22
        cur_vx_disp = abs(self._cursor_vx) * 0.0018
        dt32 = dt_eff * 3.2

        m_sin = math.sin
        m_cos = math.cos
        m_hypot = math.hypot
        wisps = self._wisps
        l0: list[tuple[float, float, float, float, float, float, float, int]] = []
        l1: list[tuple[float, float, float, float, float, float, float, int]] = []
        l2: list[tuple[float, float, float, float, float, float, float, int]] = []

        for i in range(active_count):
            ws = wisps[i]
            age = ws.age + dt_eff
            life = ws.life
            disp = ws.disp

            # Respawn a freshly nucleated smoke wisp once old vapor dissolves or fully disperses
            if age >= life or disp >= 0.97:
                ws = self._spawn_wisp(i, age_frac=0.0)
                ws.vx = wind_eff * 1.6 + self._rng.uniform(-1.5, 4.5)
                ws.vy = self._rng.uniform(-4.5, 2.0)
                wisps[i] = ws
                age = 0.0
                life = ws.life
                disp = 0.0

            u = ws.u + ws.u_dir * drift_step
            if u >= 0.97:
                u = max(0.03, 0.97 - (u - 0.97))
                ws.u_dir = -1.0
                ws.vx = -max(18.0, abs(ws.vx) * 0.72 + wind_px_per_s * 0.30)
            elif u <= 0.03:
                u = min(0.97, 0.03 + (0.03 - u))
                ws.u_dir = 1.0
                ws.vx = max(18.0, abs(ws.vx) * 0.72 + wind_px_per_s * 0.30)

            seed = ws.seed
            base_cx = x0 + u * w
            base_cy = y0 + ws.y_off + m_sin(phase_y + seed) * amp_y

            dx = ws.dx
            dy = ws.dy
            vx = ws.vx
            vy = ws.vy

            # Ambient turbulent curl + soft buoyancy scaled gently by wind speed
            vx += m_cos(phase_ax + seed + base_cy * 0.05) * amp_ax
            vy += m_sin(phase_ay + seed * 1.4 + base_cx * 0.03) * amp_ay - buoy_ay

            cx = base_cx + dx
            cy = base_cy + dy
            if has_cur:
                rx = cx - cur_x
                if -part_r < rx < part_r:
                    ry = cy - cur_y
                    if -part_ry_cap < ry < part_ry_cap:
                        dist = m_hypot(rx, ry * 1.18)
                        if dist < part_r:
                            norm = 1.0 - dist / part_r
                            if norm > 0.0:
                                inf = norm * norm * (1.25 if dist > 18.0 else 1.45)
                                ux = (rx / dist) if dist > 2.5 else (1.0 if (i & 1) == 0 else -1.0)
                                uy = (ry / dist) if dist > 2.5 else (-0.8 if (i % 3 == 0) else 0.7)
                                swirl_sign = 1.0 if (i & 1) == 0 else -1.0
                                push = 290.0 * inf
                                vx += (ux * push - uy * swirl_sign * 95.0 * inf + cur_vx024 * norm) * dt32
                                vy += (uy * push + ux * swirl_sign * 75.0 * inf + cur_vy022 * norm) * dt32
                                disp = disp + dt_eff * (1.45 * inf + cur_vx_disp * norm)
                                if disp > 1.0:
                                    disp = 1.0

            # Aerodynamic drag + gentle rebound + real window-edge collision bounce
            vx *= drag_v
            vy *= drag_v
            dx = (dx + vx * dt_eff) * drag_dx
            dy = (dy + vy * dt_eff) * drag_dy

            pad_wx = min(w * 0.08, max(8.0, ws.radius * 0.22))
            pad_wy = max(5.0, ws.radius * 0.14)
            left_w = eff_left + pad_wx
            right_w = max(left_w + 16.0, eff_right - pad_wx)
            top_w = top_limit + pad_wy
            bot_w = max(top_w + 16.0, bot_limit - pad_wy)

            cx_out = base_cx + dx
            if cx_out >= right_w:
                dx = (right_w - base_cx) - min(8.0, (cx_out - right_w) * 0.55)
                vx = -max(22.0, abs(vx) * 0.78 + wind_px_per_s * 0.25)
                ws.u_dir = -1.0
                ws.v_rot = -abs(ws.v_rot) - 2.0
            elif cx_out <= left_w:
                dx = (left_w - base_cx) + min(8.0, (left_w - cx_out) * 0.55)
                vx = max(22.0, abs(vx) * 0.78 + wind_px_per_s * 0.25)
                ws.u_dir = 1.0
                ws.v_rot = abs(ws.v_rot) + 2.0

            cy_out = base_cy + dy
            if cy_out <= top_w:
                dy = (top_w - base_cy) + min(8.0, (top_w - cy_out) * 0.55)
                vy = max(16.0, abs(vy) * 0.74)
            elif cy_out >= bot_w:
                dy = (bot_w - base_cy) - min(8.0, (cy_out - bot_w) * 0.55)
                vy = -max(16.0, abs(vy) * 0.74)

            if disp > disp_decay:
                disp -= disp_decay
            else:
                disp = 0.0

            if disp > 0.04:
                any_disp = True

            ws.u = u
            ws.age = age
            ws.dx = dx
            ws.dy = dy
            ws.vx = vx
            ws.vy = vy
            ws.disp = disp
            rot = ws.rot + ws.v_rot * dt_eff + (rot_cur_k * disp if disp > 0.05 else 0.0)
            ws.rot = rot

            life_u = age / (life if life > 0.2 else 0.2)
            fade_in = 1.0 if life_u >= 0.22 else life_u / 0.22
            fade_out = 0.0 if life_u >= 1.0 else (1.0 if life_u <= 0.74 else (1.0 - life_u) / 0.26)
            life_env = fade_in * fade_out
            # Smoke blooms outward and becomes more translucent as it disperses
            eff_alpha = ws.density * life_env * ((1.0 - disp) ** 1.55)
            if eff_alpha <= 0.004:
                continue
            rad = ws.radius * (0.72 + 0.34 * life_u) * (1.0 + disp * 0.92)
            layer = ws.layer
            item = (
                base_cx + dx,
                base_cy + dy,
                rad,
                ws.stretch,
                rot,
                eff_alpha,
                disp,
                layer,
            )
            if layer == 0:
                l0.append(item)
            elif layer == 1:
                l1.append(item)
            else:
                l2.append(item)

        self._active = any_disp
        return l0 + l1 + l2


class AtmosphereAnimator:
    """Smooth continuous transition controller for sky brightness, rain/snow tapering, and Sun->Moon crossfades."""

    __slots__ = (
        "initialized",
        "_last_ts",
        "seg_weights",
        "rain_amt",
        "thunder_amt",
        "lightning_amt",
        "snow_amt",
        "snow_accum",
        "snowman_p",
        "tree_vis",
        "cloud_amt",
        "clear_amt",
        "sun_alpha",
        "moon_alpha",
        "sun_pos",
        "moon_pos",
    )

    def __init__(self) -> None:
        self.initialized = False
        self._last_ts = 0.0
        self.seg_weights: dict[str, float] = {"sunrise": 0.0, "day": 1.0, "sunset": 0.0, "night": 0.0}
        self.rain_amt = 0.0
        self.thunder_amt = 0.0
        self.lightning_amt = 0.0
        self.snow_amt = 0.0
        self.snow_accum = 0.0
        self.snowman_p = 0.0
        self.tree_vis = 0.0
        self.cloud_amt = 0.35
        self.clear_amt = 1.0
        self.sun_alpha = 0.0
        self.moon_alpha = 0.0
        self.sun_pos: QPointF | None = None
        self.moon_pos: QPointF | None = None

    @staticmethod
    def _exp_lerp(cur: float, target: float, dt: float, tau: float) -> float:
        a = 1.0 - math.exp(-dt / max(0.05, tau))
        return cur + (target - cur) * a

    @staticmethod
    def _lerp_pt(cur: QPointF | None, target: QPointF | None, dt: float, tau: float = 0.85) -> QPointF | None:
        if target is None:
            return cur
        if cur is None:
            return QPointF(target)
        a = 1.0 - math.exp(-dt / max(0.05, tau))
        return QPointF(cur.x() + (target.x() - cur.x()) * a, cur.y() + (target.y() - cur.y()) * a)

    def step(
        self,
        core: QRectF,
        preview_mode: str = "auto",
        weather_symbol: str = "",
        now: datetime | None = None,
        *,
        lat: float | None = None,
        lon: float | None = None,
        sky_mode: str = "auto",
        weather_mode: str = "auto",
        moon_mode: str = "auto",
        dt: float | None = None,
    ) -> tuple[str, str, float | None]:
        seg, wkind, forced_moon = resolve_scene_state(
            preview_mode,
            weather_symbol,
            now,
            lat=lat,
            lon=lon,
            sky_mode=sky_mode,
            weather_mode=weather_mode,
            moon_mode=moon_mode,
        )
        t_sun, t_moon = compute_celestial_layout(
            core,
            seg,
            now,
            preview_mode=preview_mode,
            sky_mode=sky_mode,
            lat=lat,
            lon=lon,
        )
        now_ts = time.monotonic()
        if dt is None:
            raw_dt = (now_ts - self._last_ts) if self._last_ts > 0.0 else 0.03
            dt_eff = max(0.008, min(0.10, raw_dt))
            snow_dt = max(0.008, min(2.0, raw_dt))
        else:
            dt_val = max(0.008, float(dt))
            dt_eff = min(0.25, dt_val)
            snow_dt = min(120.0, dt_val)
        self._last_ts = now_ts

        t_rain = 1.0 if wkind in ("rain", "thunder", "lightning") else 0.0
        t_th = 1.0 if wkind in ("thunder", "lightning") else 0.0
        t_li = 1.0 if wkind == "lightning" else 0.0
        t_sn = 1.0 if wkind == "snow" else 0.0
        t_cl = 0.14 if wkind == "clear" else (0.48 if wkind == "partly" else (0.86 if wkind in ("cloudy", "fog", "snow") else 1.0))
        t_br = 1.0 if wkind == "clear" else (0.82 if wkind == "partly" else (0.42 if wkind in ("cloudy", "fog", "snow") else 0.18))

        if not self.initialized:
            self.initialized = True
            for k in self.seg_weights:
                self.seg_weights[k] = 1.0 if k == seg else 0.0
            self.rain_amt = t_rain
            self.thunder_amt = t_th
            self.lightning_amt = t_li
            self.snow_amt = t_sn
            self.snow_accum = 0.0
            self.snowman_p = 0.0
            self.tree_vis = 1.0 if t_sn > 0.5 else 0.0
            self.cloud_amt = t_cl
            self.clear_amt = t_br
            self.sun_alpha = 1.0 if t_sun is not None else 0.0
            self.moon_alpha = 1.0 if (t_sun is None and t_moon is not None) else 0.0
            self.sun_pos = QPointF(t_sun) if t_sun is not None else None
            self.moon_pos = QPointF(t_moon) if t_moon is not None else None
            return seg, wkind, forced_moon

        for k in self.seg_weights:
            self.seg_weights[k] = self._exp_lerp(self.seg_weights[k], 1.0 if k == seg else 0.0, dt_eff, 1.15)
        self.rain_amt = self._exp_lerp(self.rain_amt, t_rain, dt_eff, 1.05)
        self.thunder_amt = self._exp_lerp(self.thunder_amt, t_th, dt_eff, 0.85)
        self.lightning_amt = self._exp_lerp(self.lightning_amt, t_li, dt_eff, 0.70)
        self.snow_amt = self._exp_lerp(self.snow_amt, t_sn, dt_eff, 1.05)
        if self.snow_amt > 0.25:
            # Gradually build snowdrifts (~0 -> 1.0 over ~60s of sustained snowfall)
            self.snow_accum = min(1.0, self.snow_accum + (snow_dt / 60.0) * self.snow_amt)
            # Live self-rolling snowman builds right in front of the user (~9s)
            self.snowman_p = min(1.0, self.snowman_p + (dt_eff / 9.0) * self.snow_amt)
        elif self.rain_amt > 0.25:
            # Rain rapidly washes/melts snowdrifts
            self.snow_accum = max(0.0, self.snow_accum - snow_dt / 14.0)
            self.snowman_p = max(0.0, self.snowman_p - dt_eff / 2.2)
            self.tree_vis = max(0.0, self.tree_vis - dt_eff / 1.5)
        else:
            # Gentle ambient melt after snowfall stops
            self.snow_accum = max(0.0, self.snow_accum - snow_dt / 40.0)
            self.snowman_p = max(0.0, self.snowman_p - dt_eff / 3.5)
            if self.snow_amt <= 0.08:
                self.tree_vis = max(0.0, self.tree_vis - dt_eff / 1.8)
        self.cloud_amt = self._exp_lerp(self.cloud_amt, t_cl, dt_eff, 1.20)
        self.clear_amt = self._exp_lerp(self.clear_amt, t_br, dt_eff, 1.15)

        # Mutual exclusion gate: Sun fades out BEFORE Moon fades in (Moon never shown while Sun exists!)
        self.sun_alpha = self._exp_lerp(self.sun_alpha, 1.0 if t_sun is not None else 0.0, dt_eff, 0.85)
        allow_moon = (t_moon is not None) and (self.sun_alpha <= 0.03)
        self.moon_alpha = self._exp_lerp(self.moon_alpha, 1.0 if allow_moon else 0.0, dt_eff, 0.90)
        self.sun_pos = self._lerp_pt(self.sun_pos, t_sun, dt_eff, 0.80)
        self.moon_pos = self._lerp_pt(self.moon_pos, t_moon, dt_eff, 0.80)
        return seg, wkind, forced_moon

    def on_island_collapsed(self) -> None:
        """Called when the user closes/collapses the island so the New Year tree appears off-screen."""
        if self.snow_amt > 0.25:
            self.tree_vis = 1.0

    def is_transitioning(self) -> bool:
        return (
            0.02 < self.rain_amt < 0.98
            or 0.02 < self.snow_amt < 0.98
            or (self.snow_amt > 0.25 and 0.0 < self.snowman_p < 1.0)
            or 0.02 < self.sun_alpha < 0.98
            or 0.02 < self.moon_alpha < 0.98
            or max(self.seg_weights.values()) < 0.96
        )


class AtmosphericScenePainter:
    """Paints live animated sky, sun/clouds/rays, night constellations/lunar phase, precipitation, and wet grass."""

    @staticmethod
    def _seg_palette(segment: str, bright: float) -> list[tuple[float, tuple[int, int, int]]]:
        """Compute RGB sky stops blending storm/overcast vs bright clear sky."""
        if segment == "sunrise":
            return [(0.0, (50, 40, 43)), (0.52, (98, 71, 66)), (0.86, (158, 112, 89)), (1.0, (187, 136, 102))]
        if segment == "sunset":
            return [(0.0, (46, 36, 41)), (0.50, (92, 62, 61)), (0.84, (156, 101, 78)), (1.0, (186, 125, 89))]
        if segment == "night":
            b = max(0.0, min(1.0, bright))
            return [
                (0.0, (round(28 + 2 * b), round(28 + 1 * b), round(30 + 5 * b))),
                (0.55, (round(42 + 2 * b), round(41 + 0 * b), round(41 + 9 * b))),
                (1.0, (round(54 + 5 * b), round(52 + 2 * b), round(48 + 14 * b))),
            ]
        # Day: interpolate continuously from rainy dark slate (b=0.18) -> cloudy taupe -> bright sunlit beige (b=1.0)
        b = max(0.0, min(1.0, bright))
        return [
            (0.0, (round(46 + 21 * b), round(45 + 19 * b), round(41 + 16 * b))),
            (0.52, (round(69 + 37 * b), round(67 + 34 * b), round(60 + 30 * b))),
            (1.0, (round(92 + 56 * b), round(88 + 50 * b), round(76 + 43 * b))),
        ]

    @staticmethod
    def paint_sky_backdrop(
        p: QPainter,
        rect: QRectF,
        segment: str,
        weather_kind: str,
        alpha_scale: float = 1.0,
        *,
        anim_state: AtmosphereAnimator | None = None,
        grad_rect: QRectF | None = None,
        flash_amt: float = 0.0,
    ) -> None:
        g_ref = grad_rect if grad_rect is not None else rect
        grad = QLinearGradient(g_ref.topLeft(), g_ref.bottomLeft())
        if anim_state is not None and anim_state.initialized:
            bright = anim_state.clear_amt
            weights = anim_state.seg_weights
            w_tot = max(1e-5, sum(weights.values()))
            pals = {s: AtmosphericScenePainter._seg_palette(s, bright) for s in weights}
            stop_pos = (0.0, 0.52, 0.85, 1.0)
            for idx, pos in enumerate(stop_pos):
                r_acc = g_acc = b_acc = 0.0
                for s, w in weights.items():
                    wn = w / w_tot
                    st = pals[s][min(idx, len(pals[s]) - 1)][1]
                    r_acc += st[0] * wn
                    g_acc += st[1] * wn
                    b_acc += st[2] * wn
                c = QColor(max(0, min(255, round(r_acc))), max(0, min(255, round(g_acc))), max(0, min(255, round(b_acc))), int(round(244 * alpha_scale)))
                grad.setColorAt(pos, c)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRect(rect)
            if flash_amt > 0.02:
                f_op = max(0.0, min(0.45, flash_amt * 0.34))
                f_grad = QLinearGradient(g_ref.topLeft(), QPointF(g_ref.left(), g_ref.top() + g_ref.height() * 0.72))
                f_grad.setColorAt(0.0, QColor(156, 192, 255, int(round(145 * f_op))))
                f_grad.setColorAt(0.45, QColor(102, 134, 232, int(round(78 * f_op))))
                f_grad.setColorAt(1.0, QColor(72, 95, 185, 0))
                p.fillRect(rect, f_grad)
            return

        if segment == "sunrise":
            stops = [(0.0, "#32282B"), (0.52, "#624742"), (0.86, "#9E7059"), (1.0, "#BB8866")]
        elif segment == "sunset":
            stops = [(0.0, "#2E2429"), (0.50, "#5C3E3D"), (0.84, "#9C654E"), (1.0, "#BA7D59")]
        elif segment == "night":
            if weather_kind in ("rain", "thunder", "lightning"):
                stops = [(0.0, "#1D1E1F"), (0.58, "#2C2B29"), (1.0, "#383630")]
            else:
                stops = [(0.0, "#1E1D23"), (0.55, "#2C2932"), (1.0, "#3B363E")]
        else:  # day (warm pastel sand / linen-taupe tones)
            if weather_kind in ("rain", "thunder", "lightning", "fog"):
                stops = [(0.0, "#2F2E2A"), (0.55, "#47453D"), (1.0, "#5F5B4F")]
            elif weather_kind in ("cloudy", "partly"):
                stops = [(0.0, "#3F3B34"), (0.55, "#60594C"), (1.0, "#837967")]
            else:  # clear day pastel sand-khaki (keeps R < 200 at center)
                stops = [(0.0, "#434039"), (0.50, "#6A655A"), (1.0, "#948A77")]

        for pos, hx in stops:
            c = QColor(hx)
            c.setAlpha(int(round(244 * alpha_scale)))
            grad.setColorAt(pos, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRect(rect)
        if flash_amt > 0.02:
            f_op = max(0.0, min(0.45, flash_amt * 0.34))
            f_grad = QLinearGradient(g_ref.topLeft(), QPointF(g_ref.left(), g_ref.top() + g_ref.height() * 0.72))
            f_grad.setColorAt(0.0, QColor(156, 192, 255, int(round(145 * f_op))))
            f_grad.setColorAt(0.45, QColor(102, 134, 232, int(round(78 * f_op))))
            f_grad.setColorAt(1.0, QColor(72, 95, 185, 0))
            p.fillRect(rect, f_grad)

    @staticmethod
    def paint_moon(p: QPainter, center: QPointF, radius: float, phase: float) -> None:
        """Render a 3D spherical cratered Moon with true terminator shadows, maria, earthshine, and atmospheric glow."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        illum = 0.5 * (1.0 - math.cos(2.0 * math.pi * (float(phase) % 1.0)))

        # Atmospheric optical moonlight bloom scaled cleanly by lunar illuminated fraction
        halo_r = radius * (2.1 + 1.15 * illum)
        halo = QRadialGradient(center, halo_r)
        halo_col = QColor(178, 204, 240, int(round(28 + 96 * illum)))
        halo_mid = QColor(148, 178, 224, int(round(12 + 42 * illum)))
        halo.setColorAt(0.0, halo_col)
        halo.setColorAt(0.45, halo_mid)
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(center, halo_r, halo_r)

        pix_sz = max(64, min(144, int(round(radius * 4.5 / 8.0) * 8)))
        moon_pm = _get_realistic_moon_pixmap(pix_sz, phase)
        dest = QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)
        p.drawPixmap(dest, moon_pm, QRectF(0.0, 0.0, float(moon_pm.width()), float(moon_pm.height())))
        p.restore()

    @staticmethod
    def paint_constellations(p: QPainter, sky_rect: QRectF, phase_t: float, visibility: float = 1.0) -> None:
        """Draw dense multi-spectral night starfield, Milky Way veil, and individually scintillating constellations."""
        if visibility <= 0.05:
            return
        w = sky_rect.width()
        h = sky_rect.height()
        if w < 4.0 or h < 4.0:
            return
        pad = 28.0
        dpr_s = max(1.0, float(p.device().devicePixelRatioF())) if p.device() is not None else 1.0
        vis_q = max(1, min(20, int(round(visibility * 20.0))))
        phase_q = int(phase_t * 4.0)
        star_key = (int(round(w)), int(round(h)), round(dpr_s, 2), vis_q, phase_q)
        cached_sf = _STARFIELD_CACHE.get(star_key)
        if cached_sf is not None:
            p.drawImage(QPointF(sky_rect.left() - pad, sky_rect.top() - pad), cached_sf)
            return

        _evict_oldest(_STARFIELD_CACHE, 16, 4)
        img_w = max(1, int(round((w + pad * 2.0) * dpr_s)))
        img_h = max(1, int(round((h + pad * 2.0) * dpr_s)))
        sf_img = QImage(img_w, img_h, QImage.Format.Format_ARGB32_Premultiplied)
        sf_img.setDevicePixelRatio(dpr_s)
        sf_img.fill(0)

        ip = QPainter(sf_img)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x0 = pad
        y0 = pad

        # Seamless diagonal Milky Way stardust veil using cached 128x128 glow disc (zero per-frame gradient!)
        mw_c = QPointF(x0 + w * 0.52, y0 + h * 0.42)
        mw_r = max(54.0, min(w, h) * 0.46)
        mw_img = _get_glow_disc_img((178, 206, 240), int(round(26 * visibility)), int(round(12 * visibility)))
        ip.save()
        ip.translate(mw_c)
        ip.rotate(-14.0)
        ip.scale(1.42, 0.58)
        ip.drawImage(QRectF(-mw_r, -mw_r, mw_r * 2.0, mw_r * 2.0), mw_img)
        ip.restore()

        # Spectral star palette (hot O/B blue-white, A diamond-white, G champagne, K warm gold)
        spectral = (
            (202, 228, 255),
            (248, 250, 255),
            (255, 245, 224),
            (255, 228, 186),
        )
        halo_src = QRectF(0.0, 0.0, 20.0, 20.0)

        # Rich background starfield with diverse independent multi-harmonic scintillation
        m_sin = math.sin
        m_cos = math.cos
        p_draw_img = ip.drawImage
        p_set_op = ip.setOpacity
        p_set_brush = ip.setBrush
        p_draw_ellipse = ip.drawEllipse
        p_draw_line = ip.drawLine
        glint_pen = QPen(QColor(255, 255, 255, 180), 0.7)
        for idx, (sx, sy, mag) in enumerate(_BACKGROUND_STARS):
            f1 = 0.85 + ((idx * 17) % 23) * 0.17
            f2 = 2.15 + ((idx * 29) % 19) * 0.23
            p1 = (idx * 1.618) % 6.283
            p2 = (idx * 2.718) % 6.283
            wave = 0.50 + 0.30 * m_sin(phase_t * f1 + p1) + 0.20 * m_cos(phase_t * f2 + p2)
            s_gl = max(0.0, m_sin(phase_t * (f2 * 1.12) + p1 * 1.7))
            glint = s_gl * s_gl * s_gl
            tw = wave + 0.44 * glint
            if tw < 0.18:
                tw = 0.18
            elif tw > 1.35:
                tw = 1.35
            col = spectral[(idx * 7) % 4]
            a = max(12, min(255, int(round(175 * mag * tw * visibility))))
            pos = QPointF(x0 + sx * w, y0 + sy * h)
            r = 0.72 + 0.95 * mag * (0.90 + 0.18 * glint)
            if mag >= 0.72:
                hr = r * 2.8
                p_set_op(max(0.05, min(1.0, (a / 255.0) * 0.65)))
                p_draw_img(QRectF(pos.x() - hr, pos.y() - hr, hr * 2.0, hr * 2.0), _get_star_halo_img(col), halo_src)
                p_set_op(1.0)
            p_set_brush(QColor(col[0], col[1], col[2], a))
            p_draw_ellipse(pos, r, r)
            if glint > 0.32 and mag >= 0.76:
                sp_a = max(0, min(230, int(round(200 * glint * mag * visibility))))
                sp_len = (2.8 + 3.2 * mag) * (0.55 + 0.45 * glint)
                glint_pen.setColor(QColor(255, 255, 255, sp_a))
                ip.setPen(glint_pen)
                p_draw_line(QPointF(pos.x() - sp_len, pos.y()), QPointF(pos.x() + sp_len, pos.y()))
                p_draw_line(QPointF(pos.x(), pos.y() - sp_len), QPointF(pos.x(), pos.y() + sp_len))
                ip.setPen(Qt.PenStyle.NoPen)

        # Named constellations with variable twinkling nodes & delicate optical spikes
        for c_idx, (_name, stars, edges) in enumerate(_CONSTELLATIONS):
            pts = [QPointF(x0 + sx * w, y0 + sy * h) for sx, sy, _ in stars]
            line_tw = 0.72 + 0.28 * m_sin(phase_t * (0.65 + c_idx * 0.14) + c_idx * 1.4)
            line_pen = QPen(QColor(147, 197, 253, int(round(68 * line_tw * visibility))), 0.85, Qt.PenStyle.DashLine)
            ip.setPen(line_pen)
            for i0, i1 in edges:
                p_draw_line(pts[i0], pts[i1])

            ip.setPen(Qt.PenStyle.NoPen)
            for idx, ((_, _, mag), pt) in enumerate(zip(stars, pts)):
                seed_i = c_idx * 13 + idx
                f1 = 1.1 + (seed_i % 9) * 0.27
                f2 = 2.6 + (seed_i % 7) * 0.34
                wave = 0.62 + 0.24 * m_sin(phase_t * f1 + seed_i * 1.37) + 0.16 * m_cos(phase_t * f2 + seed_i * 2.11)
                s_gl = max(0.0, m_sin(phase_t * (f2 * 1.18) + seed_i * 0.91))
                glint = s_gl * s_gl * s_gl
                tw = wave + 0.48 * glint
                if tw < 0.32:
                    tw = 0.32
                elif tw > 1.38:
                    tw = 1.38
                col = spectral[(seed_i * 3) % 4]
                r = (1.65 + 1.05 * mag) * (0.94 + 0.14 * glint)
                halo_r = r * (2.6 + 0.8 * glint)
                p_set_op(max(0.08, min(1.0, mag * tw * visibility * 0.86)))
                p_draw_img(QRectF(pt.x() - halo_r, pt.y() - halo_r, halo_r * 2.0, halo_r * 2.0), _get_star_halo_img(col), halo_src)
                p_set_op(1.0)
                p_set_brush(QColor(255, 255, 255, min(255, int(round(238 * min(1.1, tw) * visibility)))))
                p_draw_ellipse(pt, r * 0.55, r * 0.55)
                if glint > 0.22:
                    sp_a = min(240, int(round(215 * glint * visibility)))
                    sp_l = (3.8 + 3.6 * mag) * (0.5 + 0.5 * glint)
                    glint_pen.setColor(QColor(col[0], col[1], col[2], sp_a))
                    ip.setPen(glint_pen)
                    p_draw_line(QPointF(pt.x() - sp_l, pt.y()), QPointF(pt.x() + sp_l, pt.y()))
                    p_draw_line(QPointF(pt.x(), pt.y() - sp_l), QPointF(pt.x(), pt.y() + sp_l))
                    ip.setPen(Qt.PenStyle.NoPen)
        ip.end()
        _STARFIELD_CACHE[star_key] = sf_img
        p.drawImage(QPointF(sky_rect.left() - pad, sky_rect.top() - pad), sf_img)

    @staticmethod
    def paint_sun(
        p: QPainter,
        center: QPointF,
        radius: float,
        segment: str,
        weather_kind: str,
        phase_t: float,
    ) -> None:
        """Render photorealistic solar photosphere with Eddington limb darkening, granulation, and atmospheric Mie corona."""
        if segment == "night":
            return
        vis = 1.0 if weather_kind == "clear" else (0.78 if weather_kind == "partly" else (0.30 if weather_kind in ("cloudy", "fog") else 0.14))
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        warm_key = 1 if segment == "sunrise" else (2 if segment == "sunset" else 0)
        warm = warm_key > 0
        rim_rgb = (244, 63, 94) if segment == "sunrise" else ((234, 88, 12) if segment == "sunset" else (245, 158, 11))

        # Broad atmospheric Mie scattering corona via cached radial QImage (zero per-frame QRadialGradient)
        halo_r = radius * (4.4 if weather_kind in ("clear", "partly") else 2.7)
        halo_rgb = (255, 240, 198) if not warm else (254, 212, 166)
        halo_img = _get_glow_disc_img(halo_rgb, int(round(128 * vis)), int(round(48 * vis)))
        p.drawImage(QRectF(center.x() - halo_r, center.y() - halo_r, halo_r * 2.0, halo_r * 2.0), halo_img)

        # Delicate volumetric inner solar flare shimmer (cached shaft QImage)
        if weather_kind in ("clear", "partly"):
            flare_w = radius * (3.1 + 0.14 * math.sin(phase_t * 0.8))
            flare_h = radius * 0.42
            flare_img = _get_sunbeam_img((255, 244, 206) if not warm else (254, 214, 168), int(round(44 * vis)))
            p.save()
            p.translate(center)
            p.rotate(14.0 + math.sin(phase_t * 0.25) * 4.0)
            p.scale(1.0, flare_h / max(1.0, flare_w))
            p.drawImage(QRectF(-flare_w, -flare_w, flare_w * 2.0, flare_w * 2.0), flare_img)
            p.restore()

        # Inner chromospheric aureole hugging the limb
        aureole_r = radius * 1.48
        aur_img = _get_glow_disc_img(rim_rgb, int(round(190 * max(0.3, vis))), int(round(92 * max(0.25, vis))))
        p.drawImage(QRectF(center.x() - aureole_r, center.y() - aureole_r, aureole_r * 2.0, aureole_r * 2.0), aur_img)

        # Cached 3D photosphere disc with physical limb darkening & solar plasma granulation
        pix_sz = max(64, min(144, int(round(radius * 4.5 / 8.0) * 8)))
        sun_pm = _get_realistic_sun_pixmap(pix_sz, warm_key)
        p.setOpacity(max(0.28, vis))
        dest = QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)
        p.drawPixmap(dest, sun_pm, QRectF(0.0, 0.0, float(sun_pm.width()), float(sun_pm.height())))
        p.restore()

    @staticmethod
    def paint_top_clouds_and_light(
        p: QPainter,
        sky_rect: QRectF,
        segment: str,
        weather_kind: str,
        phase_t: float,
        *,
        wind_ms: float = 3.0,
        cloud_phase: float | None = None,
        cursor_local: QPointF | None = None,
        sun_center: QPointF | None = None,
        anim_state: AtmosphereAnimator | None = None,
        cloud_sim: CloudSmokeField | None = None,
        quality: float = 1.0,
        outer_rect: QRectF | None = None,
    ) -> None:
        """Render warm pastel horizon glow, sunbeams, and volumetric translucent smoke clouds that stream & disperse from the mouse."""
        w = sky_rect.width()
        h = sky_rect.height()
        x0 = sky_rect.left()
        y0 = sky_rect.top()

        clear_glow = anim_state.clear_amt if (anim_state is not None and anim_state.initialized) else (1.0 if weather_kind == "clear" else (0.82 if weather_kind == "partly" else 0.25))
        th_amt = anim_state.thunder_amt if (anim_state is not None and anim_state.initialized) else (1.0 if weather_kind in ("thunder", "lightning") else 0.0)
        # Sun / Horizon glow + scattered rays in warm pastel champagne tones (cached QImages -> zero gradient rebuilds)
        if segment in ("sunrise", "sunset"):
            horizon_pt = sun_center if sun_center is not None else QPointF(x0 + w * 0.5, y0 + h * (0.72 if segment == "sunrise" else 0.16))
            h_rad = max(130.0, w * 0.52)
            h_rgb = (248, 206, 168) if segment == "sunrise" else (242, 184, 146)
            h_img = _get_glow_disc_img(h_rgb, int(round(64 + 68 * clear_glow)), int(round(36 + 26 * clear_glow)))
            p.drawImage(QRectF(horizon_pt.x() - h_rad, horizon_pt.y() - h_rad, h_rad * 2.0, h_rad * 2.0), h_img)
        elif segment != "night" and clear_glow > 0.30:
            sun_c = sun_center if sun_center is not None else QPointF(x0 + w * 0.5, y0 + h * 0.36)
            s_rad = 108.0 + 30.0 * clear_glow
            s_rgb = (248, 228, 189) if clear_glow > 0.85 else (239, 226, 206)
            s_img = _get_glow_disc_img(s_rgb, int(round(96 * clear_glow)), int(round(34 * clear_glow)))
            p.drawImage(QRectF(sun_c.x() - s_rad, sun_c.y() - s_rad, s_rad * 2.0, s_rad * 2.0), s_img)

            # Scattered volumetric pastel sunbeams ("рассеянность") — feather-soft cached radial shafts
            beam_rgb = (250, 236, 212) if clear_glow > 0.85 else (239, 230, 216)
            beam_img = _get_sunbeam_img(beam_rgb, int(round(20 * clear_glow)))
            m_sin = math.sin
            p.save()
            for beam_idx, (ang_deg, bw, blen) in enumerate(((-22.0, 26.0, 175.0), (0.0, 34.0, 205.0), (22.0, 24.0, 170.0))):
                drift_deg = m_sin(phase_t * 0.4 + beam_idx * 1.7) * 3.5
                p.save()
                p.translate(sun_c)
                p.rotate(ang_deg + drift_deg)
                p.scale(1.0, blen / max(1.0, bw))
                p.drawImage(QRectF(-bw, -bw * 0.28, bw * 2.0, bw * 2.0), beam_img)
                p.restore()
            p.restore()

        global _SMOKE_BUF_IMG
        cloud_amt = anim_state.cloud_amt if (anim_state is not None and anim_state.initialized) else (0.14 if weather_kind == "clear" else (0.48 if weather_kind == "partly" else 0.92))
        rain_mix = anim_state.rain_amt if (anim_state is not None and anim_state.initialized) else (1.0 if weather_kind in ("rain", "thunder", "lightning") else 0.0)
        li_amt = anim_state.lightning_amt if (anim_state is not None and anim_state.initialized) else (1.0 if weather_kind == "lightning" else 0.0)

        # Weather-driven cloud darkness: bright sunlit clear clouds -> brooding slate overcast -> deep charcoal storm anvils
        overcast_mix = max(0.0, min(1.0, (cloud_amt - 0.34) / 0.56))
        if weather_kind in ("cloudy", "fog") and rain_mix < 0.2:
            overcast_mix = max(overcast_mix, 0.82)
        dark_fac = max(rain_mix * 0.88, th_amt * 0.94, li_amt * 1.0, overcast_mix * 0.62)

        def _lerp_rgb(a: tuple[int, int, int], b: tuple[int, int, int], u: float) -> tuple[int, int, int]:
            k = max(0.0, min(1.0, u))
            return (round(a[0] + (b[0] - a[0]) * k), round(a[1] + (b[1] - a[1]) * k), round(a[2] + (b[2] - a[2]) * k))

        if segment in ("sunrise", "sunset"):
            top_rgb = _lerp_rgb((252, 224, 200), (118, 102, 110), dark_fac)
            mid_rgb = _lerp_rgb((242, 202, 182), (82, 72, 82), dark_fac)
            belly_rgb = _lerp_rgb((184, 150, 146), (48, 42, 52), dark_fac)
        elif segment == "night":
            top_rgb = _lerp_rgb((188, 200, 220), (88, 96, 114), dark_fac)
            mid_rgb = _lerp_rgb((142, 156, 180), (58, 64, 80), dark_fac)
            belly_rgb = _lerp_rgb((90, 102, 126), (32, 36, 48), dark_fac)
        else:
            top_rgb = _lerp_rgb((248, 246, 240), (86, 92, 104), dark_fac)
            mid_rgb = _lerp_rgb((222, 216, 210), (56, 62, 74), dark_fac)
            belly_rgb = _lerp_rgb((186, 180, 176), (32, 37, 47), dark_fac)

        sim = cloud_sim
        is_transient = sim is None
        if sim is None:
            sim = CloudSmokeField(seed=17)
            drift_arg = float(cloud_phase) if cloud_phase is not None else float(phase_t)
            static_u = drift_arg * (0.004 + max(0.0, float(wind_ms)) * 0.022)
            wisps = sim.step(sky_rect, cloud_amt, wind_ms, phase_t, cursor_local, dt=0.04, drift_offset=static_u, quality=quality, outer_rect=None)
        else:
            wisps = sim.step(sky_rect, cloud_amt, wind_ms, phase_t, cursor_local, quality=quality, outer_rect=None)

        night_scale = 0.72 if segment == "night" else 1.0
        base_opacity = (0.48 if weather_kind == "clear" else (0.76 if weather_kind == "partly" else (0.98 if (rain_mix > 0.25 or dark_fac > 0.55) else 0.88))) * night_scale

        # Bilinear 0.30x offscreen smoke buffer compositor with temporal reuse when calm (~31 Hz smoke, 120+ FPS composite)
        buf_x0 = x0 - 28.0
        buf_x1 = x0 + w + 28.0
        if outer_rect is not None and (outer_rect.left() < x0 - 2.0 or outer_rect.right() > x0 + w + 2.0):
            buf_x0 = min(buf_x0, outer_rect.left() - 28.0)
            buf_x1 = max(buf_x1, outer_rect.right() + 28.0)
        buf_y0 = y0 - 18.0
        buf_w = max(80.0, buf_x1 - buf_x0)
        buf_h = max(60.0, min(h * 0.62, 230.0) + 36.0)
        buf_scale = 0.15 + 0.09 * max(0.25, min(1.0, float(quality)))
        iw = max(16, int(math.ceil(buf_w * buf_scale)))
        ih = max(16, int(math.ceil(buf_h * buf_scale)))

        is_storm = weather_kind in ("thunder", "lightning")
        now_b = time.monotonic()
        buf_key = (iw, ih, round(buf_x0), round(buf_y0), top_rgb, mid_rgb, belly_rgb, round(base_opacity, 2), is_storm, round(wind_ms, 1))
        if (
            not is_transient
            and not sim.is_dispersing()
            and sim._buf_img is not None
            and sim._buf_img.width() == iw
            and sim._buf_img.height() == ih
            and sim._buf_key == buf_key
            and (now_b - sim._buf_ts) < 0.095
        ):
            buf_target = sim._buf_img
            smoke_loci = sim._buf_loci
        else:
            if is_transient:
                if _SMOKE_BUF_IMG is None or _SMOKE_BUF_IMG.width() != iw or _SMOKE_BUF_IMG.height() != ih:
                    _SMOKE_BUF_IMG = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
                buf_target = _SMOKE_BUF_IMG
            else:
                if sim._buf_img is None or sim._buf_img.width() != iw or sim._buf_img.height() != ih:
                    sim._buf_img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
                buf_target = sim._buf_img
            buf_target.fill(0)

            bp = QPainter(buf_target)
            # Outer drawImage already applies bilinear smoothing; skip inner SmoothPixmapTransform on weak GPUs/CPUs
            rx_k = buf_scale * 1.18
            ry_k = buf_scale * 1.32
            op_base = base_opacity * 0.74
            layer_luts = (
                _get_smoke_layer_lut(belly_rgb),
                _get_smoke_layer_lut(mid_rgb),
                _get_smoke_layer_lut(top_rgb),
            )
            smoke_loci = []
            sp_src = QRectF(0.0, 0.0, 88.0, 54.0)
            bp_draw_img = bp.drawImage
            bp_set_op = bp.setOpacity
            x_hi = x0 + w + 20.0
            x_lo = x0 - 20.0
            for cx, cy, rad, stretch, rot, eff_a, disp, layer in wisps:
                # Stable per-wisp hash so spawning/dissolving neighbors never shift active wisp shapes
                w_id = int(stretch * 1000.0)
                if is_storm and layer >= 1 and disp < 0.55 and x_lo <= cx <= x_hi and (w_id & 1) == 0:
                    smoke_loci.append(QPointF(cx, cy))

                bx = (cx - buf_x0) * buf_scale
                rx = rad * (1.0 if stretch < 1.724 else stretch * 0.58) * rx_k
                if bx + rx <= 0.0 or bx - rx >= iw:
                    continue
                by = (cy - buf_y0) * buf_scale
                ry = rad * ry_k
                if by + ry <= 0.0 or by - ry >= ih:
                    continue

                tb = round(rot / 3.5)
                if tb < -6:
                    tb = -6
                elif tb > 6:
                    tb = 6
                sp_img = layer_luts[layer][(w_id % 3) * 13 + (tb + 6)]
                op = op_base * eff_a
                if op <= 0.004:
                    continue
                if op > 0.88:
                    op = 0.88
                bp_set_op(op)
                bp_draw_img(QRectF(bx - rx, by - ry, rx * 2.0, ry * 2.0), sp_img, sp_src)
            bp.end()

            if not is_transient:
                sim._buf_loci = smoke_loci
                sim._buf_key = buf_key
                sim._buf_ts = now_b

        storm_state = _eval_storm_state(phase_t, th_amt, li_amt) if (th_amt > 0.05 or li_amt > 0.05 or is_storm) else (0.0, 0.0, 0, 0, 0.0, 0.0, 0)
        c_flash, g_flash, strike_idx, active_cell, d_flash, _dg_flash, cell_b = storm_state
        max_cloud_flash = max(c_flash, d_flash)

        # Pass A (BEHIND storm clouds): wide diffused back-lighting + spider crawler veins BEFORE translucent clouds
        # Drawing main spider veins BEFORE the volumetric storm puffs embeds lightning INSIDE cloud bellies!
        if max_cloud_flash > 0.02:
            sheet_h = min(195.0, max(96.0, h * 0.48))
            p.save()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            if c_flash > 0.02:
                back_x = smoke_loci[active_cell % len(smoke_loci)].x() if smoke_loci else (x0 + w * (0.22 + (active_cell % 56) / 100.0))
                back_y = y0 + min(72.0, h * 0.19)
                bg_img = _get_glow_disc_img((142, 180, 255), int(round(min(155.0, 130.0 * c_flash))), int(round(min(82.0, 68.0 * c_flash))))
                br_x = max(135.0, w * 0.40)
                br_y = max(68.0, min(124.0, h * 0.30))
                p.drawImage(QRectF(back_x - br_x, back_y - br_y * 0.65, br_x * 2.0, br_y * 1.45), bg_img)

                if smoke_loci and (c_flash > 0.18 or g_flash > 0.18):
                    n_loci = len(smoke_loci)
                    n_spiders = 2 if weather_kind == "thunder" else 1
                    for si in range(n_spiders):
                        if si > 0 and c_flash < 0.42:
                            continue
                        locus = smoke_loci[(active_cell + si * 7) % n_loci]
                        sp_w = min(max(185.0, w * (0.48 if si == 0 else 0.36)), 330.0)
                        sp_h = min(104.0, sheet_h * 0.58)
                        sp_pm = _get_cloud_spider_pixmap(int(sp_w), int(sp_h), strike_idx * 5 + si * 3)
                        sp_x = max(x0 - 8.0, min(x0 + w - sp_w + 8.0, locus.x() - sp_w * 0.5))
                        sp_y = max(y0 + 4.0, min(y0 + sheet_h - sp_h, locus.y() - sp_h * 0.44))
                        p.setOpacity(max(0.12, min(0.86, (0.78 if si == 0 else 0.48) * c_flash)))
                        p.drawPixmap(QRectF(sp_x, sp_y, sp_w, sp_h), sp_pm, QRectF(sp_pm.rect()))
            if d_flash > 0.03:
                d_x = smoke_loci[cell_b % len(smoke_loci)].x() if smoke_loci else (x0 + w * (0.25 + ((cell_b * 13) % 52) / 100.0))
                d_y = y0 + min(64.0, h * 0.16)
                dg_img = _get_glow_disc_img((112, 146, 238), int(round(min(108.0, 88.0 * d_flash))), int(round(min(50.0, 40.0 * d_flash))))
                dr_x = max(110.0, w * 0.32)
                dr_y = max(56.0, min(96.0, h * 0.24))
                p.drawImage(QRectF(d_x - dr_x, d_y - dr_y * 0.65, dr_x * 2.0, dr_y * 1.40), dg_img)
            p.restore()

        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(buf_x0, buf_y0, buf_w, buf_h), buf_target)
        p.restore()

        # Pass B (THROUGH clouds): soft volumetric subsurface cumulonimbus illumination + gentle fissure peek
        if (th_amt > 0.05 or li_amt > 0.05 or is_storm) and max_cloud_flash > 0.02:
            sheet_h = min(195.0, max(96.0, h * 0.48))
            p.save()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            if c_flash > 0.02:
                sheet_pm = _get_thunder_glow_pixmap(max(160, int(w)), int(sheet_h), strike_idx)
                p.setOpacity(max(0.06, min(0.84, 0.72 * c_flash)))
                p.drawPixmap(QRectF(x0, y0, w, sheet_h), sheet_pm, QRectF(sheet_pm.rect()))

                if smoke_loci:
                    n_loci = len(smoke_loci)
                    core_l = smoke_loci[active_cell % n_loci]
                    core_glow = _get_glow_disc_img((196, 224, 255), int(round(min(155.0, 128.0 * c_flash))), int(round(min(78.0, 62.0 * c_flash))))
                    r_cx = max(84.0, min(155.0, w * 0.25))
                    r_cy = r_cx * 0.48
                    p.setOpacity(max(0.10, min(0.75, 0.62 * c_flash)))
                    p.drawImage(QRectF(core_l.x() - r_cx, max(y0, core_l.y() - r_cy), r_cx * 2.0, r_cy * 2.0), core_glow)
                    # Subtle foreground vein peeking through cloud fissure at peak stroke
                    if c_flash > 0.52 and weather_kind == "thunder":
                        sp_pm = _get_cloud_spider_pixmap(int(min(max(175.0, w * 0.42), 300.0)), int(min(96.0, sheet_h * 0.54)), strike_idx * 5)
                        sp_x = max(x0, min(x0 + w - sp_pm.width(), core_l.x() - sp_pm.width() * 0.5))
                        sp_y = max(y0 + 6.0, min(y0 + sheet_h - sp_pm.height(), core_l.y() - sp_pm.height() * 0.45))
                        p.setOpacity(min(0.55, (c_flash - 0.42) * 0.90))
                        p.drawPixmap(QRectF(sp_x, sp_y, float(sp_pm.width()), float(sp_pm.height())), sp_pm, QRectF(sp_pm.rect()))

            if d_flash > 0.04:
                sheet_b = _get_thunder_glow_pixmap(max(160, int(w)), int(sheet_h), strike_idx + 11 + (cell_b % 5))
                p.setOpacity(max(0.05, min(0.62, 0.52 * d_flash)))
                p.drawPixmap(QRectF(x0, y0, w, sheet_h * 0.92), sheet_b, QRectF(sheet_b.rect()))
            p.restore()

    @staticmethod
    def paint_precipitation(
        p: QPainter,
        rect: QRectF,
        weather_kind: str,
        phase_t: float,
        *,
        turf_y: float | None = None,
        anim_state: AtmosphereAnimator | None = None,
        cursor_local: QPointF | None = None,
        wind_ms: float = 3.0,
        quality: float = 1.0,
    ) -> None:
        if anim_state is not None and anim_state.initialized:
            rain_amt = anim_state.rain_amt
            th_amt = anim_state.thunder_amt
            li_amt = anim_state.lightning_amt
            snow_amt = anim_state.snow_amt
        else:
            rain_amt = 1.0 if weather_kind in ("rain", "thunder", "lightning") else 0.0
            th_amt = 1.0 if weather_kind in ("thunder", "lightning") else 0.0
            li_amt = 1.0 if weather_kind == "lightning" else 0.0
            snow_amt = 1.0 if weather_kind == "snow" else 0.0

        if rain_amt <= 0.02 and snow_amt <= 0.02:
            return
        w = rect.width()
        h = rect.height()
        x0 = rect.left()
        y0 = rect.top()

        from ..core.licensing import get_license_manager

        has_shield = (
            cursor_local is not None
            and rect.adjusted(-40.0, -40.0, 40.0, 40.0).contains(cursor_local)
            and get_license_manager().is_feature_allowed("cursor_shield")
        )
        cx_u = cursor_local.x() if cursor_local is not None else -9999.0
        cy_u = cursor_local.y() if cursor_local is not None else -9999.0
        shield_r = 12.0
        shield_r2 = shield_r * shield_r
        wind_eff = max(0.0, min(20.0, float(wind_ms)))
        q_p = max(0.25, min(1.0, float(quality)))
        w_left = x0 + 4.0
        w_right = x0 + max(24.0, w - 4.0)
        span_x = max(20.0, w_right - w_left)
        w_top = y0 + 4.0

        if rain_amt > 0.02:
            c_flash, g_flash, strike_idx, active_cell, d_flash, dg_flash, cell_b = _eval_storm_state(
                phase_t, th_amt, li_amt
            )
            flash_vis = max(c_flash, g_flash, d_flash * 0.7)

            # Batched multi-depth realistic rain (slender translucent streaks + window-edge collision splashes)
            ground_y = float(turf_y) if turf_y is not None else (y0 + h - 8.0)
            drop_span_y = max(40.0, ground_y - (w_top + 6.0))
            base_count = int(round(46 + 12 * th_amt + 14 * li_amt))
            count = max(1, int(round(base_count * rain_amt * q_p)))

            # Subtle aerodynamic wind drift rather than unnatural hard 30-degree diagonal lines
            vx_fg = 18.0 + wind_eff * 6.5
            vx_bg = 11.0 + wind_eff * 4.2
            tilt_fg = 1.4 + min(4.6, wind_eff * 0.44)
            tilt_bg = 0.9 + min(3.0, wind_eff * 0.28)

            bg_path = QPainterPath()
            tail_path = QPainterPath()
            head_path = QPainterPath()
            splash_path = QPainterPath()
            bounce_beads: list[tuple[float, float, float]] = []
            period_rx = span_x * 2.0

            for i in range(count):
                fg_drop = (i % 3 != 0)
                vy = (355.0 + (i % 5) * 28.0) if fg_drop else (235.0 + (i % 4) * 20.0)
                vx = vx_fg if fg_drop else vx_bg
                m_rx = (i * 61.3 + ((i * 19) % 31) * 3.7 + phase_t * vx) % period_rx
                moving_right = m_rx <= span_x
                rx = (w_left + m_rx) if moving_right else (w_right - (m_rx - span_x))
                ry = (w_top + 6.0) + ((i * 47.9 + phase_t * vy) % drop_span_y)
                dy_len = (16.5 + (i % 4) * 3.0) if fg_drop else (10.5 + (i % 3) * 2.2)
                dx_slant = (tilt_fg if fg_drop else tilt_bg) * (1.0 if moving_right else -1.0)

                # Local invisible circular shield around pointer: deflect/bounce colliding drops outward & off window walls
                if has_shield:
                    dx_s = rx - cx_u
                    if abs(dx_s) < shield_r:
                        y_dome = cy_u - math.sqrt(max(0.0, shield_r2 - dx_s * dx_s))
                        if ry + dy_len >= y_dome - 2.0 and ry <= cy_u + shield_r * 1.45:
                            s_def = max(0.0, (ry + dy_len * 0.5) - y_dome)
                            sgn = 1.0 if dx_s > 0.5 else (-1.0 if dx_s < -0.5 else (1.0 if i % 2 == 0 else -1.0))
                            ux = abs(dx_s) / shield_r
                            t_hit = min(1.0, s_def / 28.0)
                            bx = rx + sgn * (4.5 + (1.0 - ux * 0.40) * s_def * 0.90)
                            by = y_dome - (1.0 - t_hit) * 5.2 * t_hit + 0.030 * (s_def * s_def)
                            if bx < w_left:
                                bx = w_left + (w_left - bx)
                            elif bx > w_right:
                                bx = w_right - (bx - w_right)
                            if by < w_top:
                                by = w_top + (w_top - by)
                            if by <= ground_y:
                                bounce_beads.append((bx, by, 1.15 if fg_drop else 0.85))
                                if s_def < 15.0:
                                    # Crisp impact splash arc right on the invisible circle shell
                                    splash_path.moveTo(rx, y_dome)
                                    splash_path.lineTo(rx + sgn * 5.6, y_dome - 4.4)
                                    splash_path.moveTo(rx, y_dome)
                                    splash_path.lineTo(rx + sgn * 2.5, y_dome - 5.6)
                            continue

                end_x = rx + dx_slant
                end_y = ry + dy_len
                if end_x >= w_right:
                    hit_y = min(ground_y - 2.0, ry + dy_len * 0.5)
                    end_x = max(w_left, w_right - (end_x - w_right))
                    bounce_beads.append((w_right - 2.4, hit_y, 1.0 if fg_drop else 0.78))
                    splash_path.moveTo(w_right, hit_y)
                    splash_path.lineTo(w_right - 4.2, hit_y - 3.2)
                    splash_path.moveTo(w_right, hit_y)
                    splash_path.lineTo(w_right - 3.6, hit_y + 2.8)
                elif end_x <= w_left:
                    hit_y = min(ground_y - 2.0, ry + dy_len * 0.5)
                    end_x = min(w_right, w_left + (w_left - end_x))
                    bounce_beads.append((w_left + 2.4, hit_y, 1.0 if fg_drop else 0.78))
                    splash_path.moveTo(w_left, hit_y)
                    splash_path.lineTo(w_left + 4.2, hit_y - 3.2)
                    splash_path.moveTo(w_left, hit_y)
                    splash_path.lineTo(w_left + 3.6, hit_y + 2.8)
                if fg_drop:
                    # Slender aerodynamic rain streak + tiny concentrated tip
                    mid_x = rx + (end_x - rx) * 0.72
                    mid_y = ry + dy_len * 0.72
                    tail_path.moveTo(rx, ry)
                    tail_path.lineTo(mid_x, mid_y)
                    head_path.moveTo(mid_x, mid_y)
                    head_path.lineTo(end_x, end_y)
                    if end_y >= ground_y - 5.0:
                        ix = end_x
                        splash_path.moveTo(ix, ground_y)
                        splash_path.lineTo(ix - 3.8, ground_y - 3.4)
                        splash_path.moveTo(ix, ground_y)
                        splash_path.lineTo(ix + 3.4, ground_y - 3.0)
                        splash_path.moveTo(ix, ground_y)
                        splash_path.lineTo(ix + (1.2 if i % 2 == 0 else -1.2), ground_y - 4.6)
                else:
                    bg_path.moveTo(rx, ry)
                    bg_path.lineTo(end_x, end_y)

            p.save()
            flash_boost = min(1.0, flash_vis) * 52.0
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(min(255, int(round(186 + flash_boost * 0.7))), min(255, int(round(214 + flash_boost * 0.6))), min(255, int(round(240 + flash_boost * 0.3))), int(round((54 + 16 * li_amt + 26 * flash_vis) * rain_amt))), 0.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPath(bg_path)
            p.setPen(QPen(QColor(min(255, int(round(198 + flash_boost * 0.8))), min(255, int(round(226 + flash_boost * 0.5))), 255, int(round((72 + 20 * li_amt + 32 * flash_vis) * rain_amt))), 0.80, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPath(tail_path)
            p.setPen(QPen(QColor(min(255, int(round(224 + flash_boost * 0.6))), min(255, int(round(242 + flash_boost * 0.25))), 255, int(round((128 + 28 * li_amt + 44 * flash_vis) * rain_amt))), 1.05, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPath(head_path)
            p.setPen(QPen(QColor(230, 246, 255, int(round((142 + 24 * li_amt + 45 * flash_vis) * rain_amt))), 0.90, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPath(splash_path)
            if bounce_beads:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(232, 246, 255, int(round(min(255, 175 + 55 * flash_vis) * rain_amt))))
                for bx, by, br in bounce_beads:
                    p.drawEllipse(QPointF(bx, by), br, br * 1.12)
            p.restore()

            # Volumetric storm cloud flashes + additive forked bolts with cloud corona & ground plasma flash
            if dg_flash > 0.04 and li_amt > 0.25:
                dist_x = x0 + w * (0.14 + ((cell_b * 29) % 72) / 100.0)
                dist_top = y0 + 26.0
                dist_h = max(80.0, (ground_y - dist_top) * 0.84)
                dist_pm = _get_lightning_bolt_pixmap(144, int(dist_h), cell_b + 5, heavy=False)
                p.save()
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                p.setOpacity(max(0.08, min(0.42, dg_flash * 0.55)))
                p.drawPixmap(QRectF(dist_x - 72.0, dist_top, 144.0, dist_h), dist_pm, QRectF(dist_pm.rect()))
                p.restore()

            if g_flash > 0.04 and li_amt > 0.15:
                sx = x0 + w * (0.18 + ((strike_idx * 37 + (active_cell % 11)) % 64) / 100.0)
                top_y = y0 + 24.0 + ((strike_idx * 7) % 16)
                bolt_h = max(92.0, ground_y - top_y)
                bolt_pm = _get_lightning_bolt_pixmap(224, int(bolt_h), strike_idx * 3 + (1 if g_flash > 0.72 else 0), heavy=True)
                p.save()
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                if c_flash > 0.12:
                    sheet_pm = _get_thunder_glow_pixmap(max(180, int(w)), 150, strike_idx)
                    p.setOpacity(max(0.10, min(0.72, 0.58 * c_flash)))
                    p.drawPixmap(QRectF(x0, y0, w, min(185.0, h * 0.45)), sheet_pm, QRectF(sheet_pm.rect()))
                # Diffuse cloud-base illumination where bolt emerges from cloud anvil (elliptical glow disc stays soft inside sky dome)
                cb_glow = _get_glow_disc_img((168, 206, 255), int(round(min(140.0, 112.0 * g_flash))), int(round(min(70.0, 52.0 * g_flash))))
                p.setOpacity(max(0.12, min(0.88, g_flash)))
                p.drawImage(QRectF(sx - 105.0, max(y0 - 12.0, top_y - 34.0), 210.0, 88.0), cb_glow)
                p.drawPixmap(QRectF(sx - 112.0, top_y, 224.0, bolt_h), bolt_pm, QRectF(bolt_pm.rect()))
                # Ground plasma flash reflection when main return stroke hits earth
                if g_flash > 0.28:
                    gf_img = _get_glow_disc_img((212, 234, 255), int(round(min(175.0, 145.0 * g_flash))), int(round(min(82.0, 66.0 * g_flash))))
                    p.drawImage(QRectF(sx - 86.0, ground_y - 24.0, 172.0, 42.0), gf_img)
                p.restore()

        if snow_amt > 0.02:
            # Detailed multi-plane New Year snowfall: fine mist, crisp dendritic crystals, soft bokeh & sparkling glints
            ground_y = float(turf_y) if turf_y is not None else (y0 + h - 6.0)
            span_y = max(40.0, ground_y - w_top)
            n_snow = max(14, int(round(98 * snow_amt * q_p)))
            snow_src = QRectF(0.0, 0.0, 24.0, 24.0)
            p.save()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            bg_snow = QPainterPath()
            glint_snow = QPainterPath()
            period_sx = span_x * 2.0
            for i in range(n_snow):
                plane = i % 6  # 0,1 = fine far dust; 2,3 = crisp crystals; 4 = soft foreground 3D bokeh; 5 = ice glints
                # Golden-ratio / Knuth multiplicative hash avoids periodic horizontal banding
                hx = ((i * 2654435761) & 0xFFFF) / 65535.0
                hy = (((i * 2246822519) + 12347) & 0xFFFF) / 65535.0
                hv = (((i * 3266489917) + 97) & 0xFF) / 255.0
                fall_v = 20.0 + plane * 6.8 + hv * 12.0
                eddy_x = (
                    math.sin(phase_t * (0.85 + hv * 0.95) + hx * 6.28) * (7.5 + plane * 2.8)
                    + math.cos(phase_t * 1.45 + hy * 6.28) * 4.2
                )
                wind_drift = phase_t * (3.5 + wind_eff * 2.2) * (0.55 + plane * 0.15)
                mx_s = (hx * span_x + eddy_x + wind_drift) % period_sx
                sx = (w_left + mx_s) if mx_s <= span_x else (w_right - (mx_s - span_x))
                sy = w_top + ((hy * span_y + phase_t * fall_v) % span_y)
                wall_prox = (mx_s if mx_s <= (span_x * 0.5) else (span_x - mx_s)) if mx_s <= span_x else min(mx_s - span_x, period_sx - mx_s)
                if wall_prox < 6.0:
                    sy = max(w_top + 2.0, sy - (6.0 - wall_prox) * 0.75)

                # Local invisible circular shield around pointer deflects snowflakes cleanly off & around the cursor
                if has_shield:
                    dx_s = sx - cx_u
                    dy_s = sy - cy_u
                    dist_c = math.hypot(dx_s, dy_s)
                    swirl_r = shield_r + 8.0
                    if dist_c < swirl_r:
                        if dist_c < 1.0:
                            nx, ny = (1.0 if i % 2 == 0 else -1.0), -0.6
                        else:
                            nx, ny = dx_s / dist_c, dy_s / dist_c
                        push = max(0.0, swirl_r - dist_c)
                        # Bounce away radially & slightly sideways/up when landing atop invisible shield
                        sx += nx * (push * 1.25)
                        sy += ny * (push * 0.85) - max(0.0, -ny) * push * 0.35

                pad_s = 1.4 if plane <= 1 else (5.5 if plane == 4 else 3.8)
                if sx < w_left + pad_s:
                    sx = (w_left + pad_s) + ((w_left + pad_s) - sx) * 0.75
                elif sx > w_right - pad_s:
                    sx = (w_right - pad_s) - (sx - (w_right - pad_s)) * 0.75
                if sy < w_top + pad_s:
                    sy = (w_top + pad_s) + ((w_top + pad_s) - sy) * 0.75
                elif sy > ground_y - pad_s:
                    sy = (ground_y - pad_s) - (sy - (ground_y - pad_s)) * 0.65

                if plane <= 1:
                    r_bg = 0.95 + (i % 2) * 0.45
                    bg_snow.addEllipse(QPointF(sx, sy), r_bg, r_bg)
                elif plane == 5:
                    r_gl = 1.15 + (i % 2) * 0.55
                    glint_snow.addEllipse(QPointF(sx, sy), r_gl, r_gl)
                else:
                    var = 0 if plane == 4 else (2 + (i % 2))
                    pm_sn = _get_snow_sprite_pixmap(var, 24)
                    sz_draw = (5.8 + (i % 3) * 1.6) if plane == 4 else (3.4 + (i % 2) * 0.95)
                    alpha_op = max(0.25, min(0.92, snow_amt * (0.58 if plane == 4 else 0.86)))
                    p.setOpacity(alpha_op)
                    p.drawPixmap(QRectF(sx - sz_draw, sy - sz_draw, sz_draw * 2.0, sz_draw * 2.0), pm_sn, snow_src)

            p.setOpacity(1.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(238, 245, 255, int(round(145 * snow_amt))))
            p.drawPath(bg_snow)
            p.setBrush(QColor(255, 255, 255, int(round(215 * snow_amt))))
            p.drawPath(glint_snow)
            p.restore()

    @staticmethod
    def paint_bottom_grass(
        p: QPainter,
        core: QRectF,
        segment: str,
        weather_kind: str,
        phase_t: float,
        *,
        outer_bottom: float | None = None,
        outer_rect: QRectF | None = None,
        cursor_local: QPointF | None = None,
        music_energy: float = 0.0,
        music_bands: list[float] | None = None,
        grass_sim: GrassSkeletonSim | None = None,
        anim_state: AtmosphereAnimator | None = None,
        quality: float = 1.0,
    ) -> None:
        """Draw dense realistic 3-4 cm grass, wet rainwater earth/puddles, ~60s snowdrifts, and cached humus strata."""
        rain_vis = anim_state.rain_amt if (anim_state is not None and anim_state.initialized) else (1.0 if weather_kind in ("rain", "thunder", "lightning") else 0.0)
        snow_vis = anim_state.snow_amt if (anim_state is not None and anim_state.initialized) else (1.0 if weather_kind == "snow" else 0.0)
        wet = weather_kind in ("rain", "thunder", "lightning", "fog") or rain_vis > 0.25
        snowy = weather_kind == "snow" or snow_vis > 0.25
        if anim_state is not None and anim_state.initialized:
            snow_accum = max(0.0, min(1.0, anim_state.snow_accum))
        elif weather_kind == "snow":
            snow_accum = min(0.75, max(0.06, phase_t / 60.0))
        else:
            snow_accum = 0.0

        turf_y = core.bottom() - 5.0
        eff_left = min(core.left(), outer_rect.left()) - 24.0 if outer_rect is not None else core.left() - 24.0
        eff_right = max(core.right(), outer_rect.right()) + 24.0 if outer_rect is not None else core.right() + 24.0
        eff_width = max(100.0, eff_right - eff_left)
        soil_bottom = max(
            core.bottom(),
            float(outer_bottom) if outer_bottom is not None else core.bottom(),
            outer_rect.bottom() if outer_rect is not None else core.bottom(),
        ) + 16.0
        ground_top = turf_y - 108.0
        soil_world_x0 = core.left() - 96.0
        soil_world_w = max(240.0, core.width() + 192.0)

        def _turf_y_at(xv: float) -> float:
            rel = xv - core.left()
            return turf_y + math.sin(rel * 0.032) * 1.45 + math.cos(rel * 0.082) * 0.60

        step_px = 16.0
        k_start = math.ceil((eff_left - core.left()) / step_px)
        k_end = math.floor((eff_right - core.left()) / step_px)

        soil_dpr = max(1.0, float(p.device().devicePixelRatioF())) if p.device() is not None else 1.0
        accum_q = int(round(snow_accum * 24.0))
        core_w_q = int(round(core.width()))
        rain_q = int(round(rain_vis * 12.0)) if rain_vis > 0.08 else 0
        soil_img_left = min(eff_left, soil_world_x0) - 4.0
        soil_img_right = max(eff_right, soil_world_x0 + soil_world_w) + 4.0
        soil_img_top = min(ground_top, turf_y - 44.0) - 4.0
        soil_img_bot = max(soil_bottom, turf_y + 68.0) + 4.0
        soil_key = (
            core_w_q,
            int(round(eff_left - core.left())),
            int(round(eff_right - core.left())),
            int(round(soil_bottom - core.bottom())),
            bool(wet),
            bool(snowy),
            segment,
            accum_q,
            rain_q,
            round(soil_dpr, 2),
        )
        soil_img = _SOIL_BACKDROP_CACHE.get(soil_key)
        if soil_img is None:
            _evict_oldest(_SOIL_BACKDROP_CACHE, 16, 4)
            sw_px = max(16, int(math.ceil((soil_img_right - soil_img_left) * soil_dpr)))
            sh_px = max(16, int(math.ceil((soil_img_bot - soil_img_top) * soil_dpr)))
            soil_img = QImage(sw_px, sh_px, QImage.Format.Format_ARGB32_Premultiplied)
            soil_img.setDevicePixelRatio(soil_dpr)
            soil_img.fill(0)
            sp = QPainter(soil_img)
            sp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            sp.translate(-soil_img_left, -soil_img_top)

            ground_rect = QRectF(eff_left, ground_top, eff_width, max(12.0, soil_bottom - ground_top))
            # Anchor vertical soil gradient strictly to fixed `core` coordinates so stretching outer_rect NEVER shifts interior pixels!
            core_soil_h = max(24.0, (core.bottom() + 26.0) - ground_top)
            turf_stop = max(0.25, min(0.85, (turf_y - ground_top) / core_soil_h))
            ground_grad = QLinearGradient(QPointF(eff_left, ground_top), QPointF(eff_left, ground_top + core_soil_h))
            ground_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            if wet:
                ground_grad.setColorAt(max(0.05, turf_stop * 0.55), QColor(34, 30, 26, 108))
                ground_grad.setColorAt(turf_stop, QColor(36, 28, 21, 236))
                ground_grad.setColorAt(1.0, QColor(18, 14, 10, 250))
            elif snowy:
                ground_grad.setColorAt(max(0.05, turf_stop * 0.55), QColor(74, 72, 68, 88))
                ground_grad.setColorAt(turf_stop, QColor(76, 66, 54, 224))
                ground_grad.setColorAt(1.0, QColor(44, 38, 31, 244))
            elif segment in ("sunrise", "sunset"):
                ground_grad.setColorAt(max(0.05, turf_stop * 0.55), QColor(88, 66, 48, 88))
                ground_grad.setColorAt(turf_stop, QColor(84, 60, 40, 225))
                ground_grad.setColorAt(1.0, QColor(48, 34, 23, 245))
            else:
                ground_grad.setColorAt(max(0.05, turf_stop * 0.55), QColor(76, 68, 50, 88))
                ground_grad.setColorAt(turf_stop, QColor(78, 62, 46, 225))
                ground_grad.setColorAt(1.0, QColor(46, 36, 27, 245))
            sp.setPen(Qt.PenStyle.NoPen)
            sp.setBrush(ground_grad)
            sp.drawRect(ground_rect)

            # Anchor interior polygon steps strictly to core.left() + k*step_px so outer_rect stretch preserves exact horizon geometry
            earth_poly = QPainterPath()
            earth_ridge = QPainterPath()
            first_y = _turf_y_at(eff_left)
            earth_poly.moveTo(eff_left, first_y)
            earth_ridge.moveTo(eff_left, first_y)
            for ki in range(k_start, k_end + 1):
                ex = core.left() + ki * step_px
                if ex <= eff_left + 0.01 or ex >= eff_right - 0.01:
                    continue
                ey = _turf_y_at(ex)
                earth_poly.lineTo(ex, ey)
                earth_ridge.lineTo(ex, ey)
            last_y = _turf_y_at(eff_right)
            earth_poly.lineTo(eff_right, last_y)
            earth_ridge.lineTo(eff_right, last_y)
            earth_poly.lineTo(eff_right, soil_bottom)
            earth_poly.lineTo(eff_left, soil_bottom)
            earth_poly.closeSubpath()

            shelf_grad = QLinearGradient(QPointF(core.left(), turf_y - 6.0), QPointF(core.left(), core.bottom() + 26.0))
            if snowy:
                shelf_grad.setColorAt(0.0, QColor(208, 224, 242, 232))
                shelf_grad.setColorAt(0.48, QColor(186, 206, 230, 244))
                shelf_grad.setColorAt(1.0, QColor(156, 180, 210, 250))
                humus_rgb = (224, 236, 248)
                mist_rgb = (186, 206, 230)
            elif wet:
                shelf_grad.setColorAt(0.0, QColor(24, 62, 34, 236))
                shelf_grad.setColorAt(0.48, QColor(18, 48, 26, 246))
                shelf_grad.setColorAt(1.0, QColor(12, 34, 18, 252))
                humus_rgb = (30, 74, 40)
                mist_rgb = (44, 96, 56)
            elif segment in ("sunrise", "sunset"):
                shelf_grad.setColorAt(0.0, QColor(42, 86, 40, 232))
                shelf_grad.setColorAt(0.48, QColor(32, 68, 32, 244))
                shelf_grad.setColorAt(1.0, QColor(22, 48, 24, 250))
                humus_rgb = (54, 102, 46)
                mist_rgb = (78, 118, 58)
            elif segment == "night":
                shelf_grad.setColorAt(0.0, QColor(18, 46, 30, 234))
                shelf_grad.setColorAt(0.48, QColor(13, 35, 24, 246))
                shelf_grad.setColorAt(1.0, QColor(8, 24, 16, 252))
                humus_rgb = (22, 58, 38)
                mist_rgb = (34, 72, 50)
            else:
                shelf_grad.setColorAt(0.0, QColor(38, 102, 46, 232))
                shelf_grad.setColorAt(0.48, QColor(28, 82, 36, 244))
                shelf_grad.setColorAt(1.0, QColor(18, 60, 26, 250))
                humus_rgb = (48, 122, 56)
                mist_rgb = (68, 144, 72)
            sp.setPen(Qt.PenStyle.NoPen)
            sp.setBrush(shelf_grad)
            sp.drawPath(earth_poly)

            # Sloped 3D meadow grass-tuft texture & snowdrift crust (no underground dirt/bones/gems)
            crust_key = (core_w_q, bool(wet), bool(snowy), segment, accum_q)
            crust_pm = _SOIL_CRUST_CACHE.get(crust_key)
            crust_w = max(240, int(math.ceil(soil_world_w)))
            crust_h = 112
            crust_top = turf_y - 44.0
            if crust_pm is None:
                if len(_SOIL_CRUST_CACHE) >= 32:
                    _SOIL_CRUST_CACHE.clear()
                crust_pm = QPixmap(crust_w, crust_h)
                crust_pm.fill(Qt.GlobalColor.transparent)
                cp = QPainter(crust_pm)
                cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                cp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                cp.setPen(Qt.PenStyle.NoPen)
                src_puff = QRectF(0.0, 0.0, 128.0, 128.0)
                n_soil_puffs = max(40, int(round(soil_world_w / 16.0)))
                for gi in range(n_soil_puffs):
                    rel_x = ((gi * 0.61803398875) % 1.0) * soil_world_w
                    gx = soil_world_x0 + rel_x
                    layer_s = gi % 3
                    depth_off = 2.0 + ((gi * 19 + 5) % 34) * (1.2 if layer_s == 0 else 0.72)
                    gy_world = _turf_y_at(gx) + (depth_off - 4.5 if layer_s == 2 else depth_off)
                    gy_loc = gy_world - crust_top
                    rw = 32.0 + ((gi * 17) % 24)
                    rh = 9.0 + ((gi * 11) % 8)
                    rgb_s = humus_rgb if layer_s != 1 else mist_rgb
                    puff_s = _get_smoke_puff_pixmap(rgb_s, gi % 3)
                    cp.save()
                    cp.setOpacity(0.32 if layer_s == 0 else (0.26 if layer_s == 1 else 0.38))
                    cp.translate(rel_x, gy_loc)
                    cp.drawPixmap(QRectF(-rw, -rh, rw * 2.0, rh * 2.0), puff_s, src_puff)
                    cp.restore()

                # Subtle perspective grass blades / snow speckles along the sloped foreground meadow
                n_grains = max(44, int(round(soil_world_w / 12.0)))
                for gi in range(n_grains):
                    rel_x = (gi * 47.3 + 13.0) % soil_world_w
                    gx = soil_world_x0 + rel_x
                    gy_loc = (_turf_y_at(gx) + 3.0 + ((gi * 19.7) % 42.0)) - crust_top
                    cp.setBrush(QColor(mist_rgb[0], mist_rgb[1], mist_rgb[2], 95))
                    cp.drawEllipse(QPointF(rel_x, gy_loc), 2.2, 0.85)

                # Progressive undulating snowdrifts built over ~60s of snowfall
                if accum_q > 0:
                    s_dep = accum_q / 24.0
                    drift_base = QPainterPath()
                    base_loc_y = (turf_y - crust_top) + 10.0
                    drift_base.moveTo(0.0, base_loc_y)
                    for sx_i in range(0, crust_w + 8, 8):
                        wx = soil_world_x0 + sx_i
                        rel = wx - core.left()
                        dune = (
                            0.48
                            + 0.34 * math.sin(rel * 0.021 + 0.5)
                            + 0.18 * math.cos(rel * 0.053 - 0.8)
                        )
                        drift_h = (2.2 + 16.5 * s_dep * dune) * min(1.0, s_dep * 1.4)
                        dy_loc = (_turf_y_at(wx) - drift_h) - crust_top
                        drift_base.lineTo(float(sx_i), dy_loc)
                    drift_base.lineTo(float(crust_w), base_loc_y)
                    drift_base.closeSubpath()

                    drift_grad = QLinearGradient(QPointF(0.0, (turf_y - crust_top) - 20.0), QPointF(0.0, base_loc_y))
                    top_a = min(248, int(round(135 + 115 * s_dep)))
                    drift_grad.setColorAt(0.0, QColor(248, 251, 255, top_a))
                    drift_grad.setColorAt(0.55, QColor(216, 228, 244, top_a))
                    drift_grad.setColorAt(1.0, QColor(156, 174, 198, min(230, int(round(90 + 135 * s_dep)))))
                    cp.setBrush(drift_grad)
                    cp.drawPath(drift_base)

                    n_sp = max(12, int(round(44 * s_dep)))
                    cp.setBrush(QColor(255, 255, 255, 215))
                    for sp_i in range(n_sp):
                        rel_x = ((sp_i * 61.7 + 17.0) % soil_world_w)
                        wx = soil_world_x0 + rel_x
                        rel = wx - core.left()
                        dune = 0.48 + 0.34 * math.sin(rel * 0.021 + 0.5) + 0.18 * math.cos(rel * 0.053 - 0.8)
                        dh = (2.2 + 16.5 * s_dep * dune) * min(1.0, s_dep * 1.4)
                        dy_s = (_turf_y_at(wx) - dh * (0.25 + (sp_i % 5) * 0.14)) - crust_top
                        cp.drawEllipse(QPointF(rel_x, dy_s), 0.9, 0.9)
                cp.end()
                _SOIL_CRUST_CACHE[crust_key] = crust_pm

            sp.drawPixmap(QPointF(soil_world_x0, crust_top), crust_pm)
            sp.setBrush(Qt.BrushStyle.NoBrush)
            crown_glow = QColor(mist_rgb[0], mist_rgb[1], mist_rgb[2], 78)
            sp.setPen(QPen(crown_glow, 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            sp.drawPath(earth_ridge)
            if rain_q > 0:
                rv_stat = rain_q / 12.0
                wet_sheen = QColor(168, 202, 232, int(round(76 * rv_stat)))
                sp.setPen(QPen(wet_sheen, 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                sp.drawPath(earth_ridge)
                for px_u, pw in ((0.22, 34.0), (0.54, 42.0), (0.80, 30.0)):
                    px_p = core.left() + core.width() * px_u
                    py_p = _turf_y_at(px_p) + 1.4
                    pg = QRadialGradient(QPointF(px_p, py_p), pw)
                    pg.setColorAt(0.0, QColor(162, 198, 230, int(round(92 * rv_stat))))
                    pg.setColorAt(0.6, QColor(82, 108, 134, int(round(56 * rv_stat))))
                    pg.setColorAt(1.0, QColor(0, 0, 0, 0))
                    sp.setPen(Qt.PenStyle.NoPen)
                    sp.setBrush(pg)
                    sp.drawEllipse(QPointF(px_p, py_p), pw, pw * 0.16)
            sp.end()
            _SOIL_BACKDROP_CACHE[soil_key] = soil_img

        p.drawImage(QPointF(soil_img_left, soil_img_top), soil_img)
        if rain_vis > 0.08:
            p.setBrush(Qt.BrushStyle.NoBrush)
            for ri in range(6):
                rx = core.left() + core.width() * (0.11 + ri * 0.155)
                ry = _turf_y_at(rx) - 1.2
                prog = (phase_t * 1.05 + ri * 0.37) % 1.0
                rad = 3.2 + prog * 15.5
                ra = int((1.0 - prog) * 140 * rain_vis)
                p.setPen(QPen(QColor(214, 236, 255, ra), 1.0))
                p.drawEllipse(QPointF(rx, ry), rad, rad * 0.25)

        # Full-height grass interaction zone across all visible blade heights (strictly inside island bottom!)
        top_y = turf_y - 185.0
        has_cursor = (
            cursor_local is not None
            and eff_left <= cursor_local.x() <= (eff_left + eff_width)
            and top_y <= cursor_local.y() <= (turf_y + 8.0)
        )
        vert_inf = 0.0
        if has_cursor and cursor_local is not None:
            t_vert = max(0.0, min(1.0, (cursor_local.y() - top_y) / max(1.0, turf_y - top_y)))
            vert_inf = 0.72 + 0.38 * (t_vert ** 0.65)

        # Realistic natural botanical tones (back occluded -> rich mid meadow -> sunlit/wet/frosted front)
        if wet:
            back_c, mid_c, tip_c = QColor(24, 42, 22, 242), QColor(54, 82, 44, 232), QColor(148, 178, 128, 218)
            sheen = QColor(232, 246, 242, 195)
        elif snowy:
            frost = min(1.0, 0.25 + snow_accum * 0.75)
            back_c = QColor(56, 62, 54, 228)
            mid_c = QColor(round(108 + 26 * frost), round(114 + 24 * frost), round(102 + 28 * frost), 218)
            tip_c = QColor(round(205 + 42 * frost), round(212 + 38 * frost), round(210 + 44 * frost), 210)
            sheen = QColor(246, 248, 252, 185)
        elif segment in ("sunrise", "sunset"):
            back_c, mid_c, tip_c = QColor(58, 52, 32, 232), QColor(124, 106, 64, 220), QColor(230, 192, 136, 200)
            sheen = QColor(250, 218, 168, 150)
        elif segment == "night":
            back_c, mid_c, tip_c = QColor(34, 38, 28, 230), QColor(72, 78, 58, 216), QColor(138, 142, 114, 185)
            sheen = QColor(202, 210, 204, 120)
        else:
            back_c, mid_c, tip_c = QColor(48, 58, 34, 232), QColor(102, 112, 70, 224), QColor(198, 196, 146, 206)
            sheen = QColor(238, 230, 192, 140)

        # Ultra-dense 2x tilted 3D meadow blades filling the sloped clearing with zero bare ground
        grass_left = core.left() - 36.0
        grass_width = max(120.0, core.width() + 72.0)
        q_g = max(0.25, min(1.0, float(quality)))
        n_blades = max(96, int(round(max(260.0, grass_width / 1.95) * q_g)))
        root_y = turf_y + 4.0
        has_music = music_energy > 0.02
        n_mb = len(music_bands) if music_bands else 0
        px_cm = _px_per_cm()
        max_cm_px = px_cm * 6.98

        if grass_sim is not None:
            blade_states = grass_sim.step(
                n_blades,
                grass_left,
                grass_width,
                root_y,
                wet,
                phase_t,
                cursor_local,
                vert_inf,
                has_cursor,
                music_energy,
                music_bands,
            )
            w_scales = grass_sim.w_scales
            shade_indices = grass_sim.shade_indices
        else:
            blade_states = []
            w_scales = []
            shade_indices = []
            part_radius = 28.0
            wind_rad_amp = 0.088 if wet else 0.056
            rng_st = random.Random(7331)
            for i in range(n_blades):
                frac = (i + 0.5) / n_blades
                bx = grass_left + frac * grass_width + rng_st.uniform(-2.8, 2.8)
                tier = rng_st.random()
                cm_len = rng_st.uniform(0.85, 2.35) if tier < 0.35 else (rng_st.uniform(2.35, 4.75) if tier < 0.76 else rng_st.uniform(4.85, 6.92))
                band_v = float(music_bands[min(n_mb - 1, int(frac * n_mb))]) if (has_music and n_mb > 0) else music_energy
                music_h = (1.0 + 0.10 * music_energy + 0.16 * band_v) if has_music else 1.0
                bh = min(max_cm_px, max(16.0, cm_len * px_cm) * (1.06 if wet else 1.0) * music_h)
                wind_rad = math.sin(phase_t * (2.05 if wet else 1.32) + i * 0.43) * wind_rad_amp
                if has_music:
                    wind_rad += math.sin(phase_t * 3.8 + i * 0.55) * (0.036 + 0.09 * band_v + 0.048 * music_energy)
                ang = rng_st.uniform(-0.14, 0.14) + wind_rad
                crl = wind_rad * 0.55
                if has_cursor and vert_inf > 0.02 and cursor_local is not None:
                    dx = bx - cursor_local.x()
                    adx = abs(dx)
                    if adx < part_radius:
                        bell = 0.5 * (1.0 + math.cos(math.pi * adx / part_radius)) * vert_inf
                        side = (1.0 if dx >= 0.0 else -1.0) if adx > 3.5 else (1.0 if (i % 2 == 0) else -1.0)
                        hit_frac = max(0.18, min(1.0, (root_y - cursor_local.y()) / max(20.0, bh)))
                        ang += side * bell * (0.62 + 0.34 * (1.0 - hit_frac * 0.40))
                        crl += side * bell * (0.36 + 0.48 * hit_frac)
                blade_states.append((bx, bh, max(-1.38, min(1.38, ang)), max(-1.26, min(1.26, crl))))
                w_scales.append(rng_st.uniform(0.60, 1.30))
                shade_indices.append(i % 6)

        # Fill side-stretched liquid bulge with seamless grass ONLY when cursor pulls bulge past core
        has_bulge_ext = bool(
            outer_rect is not None
            and (outer_rect.left() < grass_left + 4.0 or outer_rect.right() > grass_left + grass_width - 4.0)
        )
        if has_bulge_ext and outer_rect is not None:
            blade_states = list(blade_states)
            w_scales = list(w_scales)
            shade_indices = list(shade_indices)
            ext_step = 4.6
            ext_points: list[tuple[float, int]] = []
            if outer_rect.left() < grass_left + 4.0:
                k_lo = math.floor((outer_rect.left() - 12.0 - grass_left) / ext_step)
                for k in range(k_lo, 0):
                    ext_points.append((grass_left + k * ext_step, abs(k * 131 + 17)))
            if outer_rect.right() > grass_left + grass_width - 4.0:
                k_hi = math.ceil((outer_rect.right() + 12.0 - (grass_left + grass_width)) / ext_step)
                right_base = grass_left + grass_width
                for k in range(1, k_hi + 1):
                    ext_points.append((right_base + k * ext_step, abs(k * 131 + 53)))
            for bx, seed_v in ext_points:
                r01 = ((seed_v * 1103515245 + 12345) & 0x7FFF) / 32767.0
                r02 = ((seed_v * 214013 + 2531011) & 0x7FFF) / 32767.0
                if r01 < 0.35:
                    cm_len = 0.85 + r02 * 1.50
                elif r01 < 0.76:
                    cm_len = 2.35 + r02 * 2.40
                else:
                    cm_len = 4.85 + r02 * 2.07
                bh = min(max_cm_px, max(16.0, cm_len * px_cm) * (1.06 if wet else 1.0))
                w_rad = math.sin(phase_t * (2.05 if wet else 1.32) + seed_v * 0.43) * (0.088 if wet else 0.056)
                ang = ((seed_v % 9) - 4) * 0.032 + w_rad
                crl = w_rad * 0.55
                if has_cursor and vert_inf > 0.02 and cursor_local is not None:
                    dx_c = bx - cursor_local.x()
                    adx_c = abs(dx_c)
                    if adx_c < 44.0:
                        bell_e = 0.5 * (1.0 + math.cos(math.pi * adx_c / 44.0)) * vert_inf
                        push_dir = (1.0 if dx_c >= 0.0 else -1.0) if adx_c > 3.5 else (1.0 if (seed_v % 2 == 0) else -1.0)
                        hit_f = max(0.18, min(1.0, (root_y - cursor_local.y()) / max(20.0, bh)))
                        ang += push_dir * bell_e * (0.78 + 0.44 * (1.0 - hit_f * 0.55))
                        crl += push_dir * bell_e * (0.36 + 0.48 * hit_f)
                blade_states.append((bx, bh, max(-1.38, min(1.38, ang)), max(-1.26, min(1.26, crl))))
                w_scales.append(0.60 + r02 * 0.70)
                shade_indices.append(seed_v % 6)

        # Calm meadow offscreen canopy layer cache on GrassSkeletonSim (covers side canvas cleanly for bulge/idle)
        can_x0 = min(grass_left - 24.0, eff_left - 8.0)
        can_x1 = max(grass_left + grass_width + 24.0, eff_right + 8.0)
        can_y0 = turf_y - max_cm_px - 16.0
        can_w = max(120, int(math.ceil(can_x1 - can_x0)))
        can_h = max(48, int(math.ceil(max_cm_px + 34.0)))
        can_calm = (
            grass_sim is not None
            and not grass_sim._settling
            and not (has_cursor and vert_inf > 0.01)
            and not has_music
        )
        can_key = (
            can_w,
            can_h,
            n_blades,
            int(round(eff_left - core.left())),
            int(round(eff_right - core.left())),
            bool(wet),
            bool(snowy),
            segment,
            round(snow_accum * 24.0),
            round(core.top()),
            round(core.bottom()),
            round(core.left()),
        )
        if (
            can_calm
            and grass_sim is not None
            and grass_sim._canopy_img is not None
            and grass_sim._canopy_key == can_key
            and abs(phase_t - grass_sim._canopy_phase) < 0.048
        ):
            p.drawImage(QPointF(can_x0, can_y0), grass_sim._canopy_img)
        else:
            canopy_grads: list[QLinearGradient] = []
            top_y_g = turf_y - max_cm_px
            root_y_g = turf_y + 4.0
            for g_idx in range(6):
                g_col = QLinearGradient(QPointF(core.left(), root_y_g), QPointF(core.left(), top_y_g))
                if g_idx < 2:
                    root_col = QColor(max(0, back_c.red() - 18), max(0, back_c.green() - 18), max(0, back_c.blue() - 14), 238)
                    mid_col = QColor(back_c.red(), back_c.green(), back_c.blue(), 220)
                    up_col = QColor(mid_c.red(), mid_c.green(), mid_c.blue(), 196)
                elif g_idx < 4:
                    root_col = QColor(max(0, back_c.red() - 12), max(0, back_c.green() - 12), max(0, back_c.blue() - 10), 236)
                    mid_col = mid_c
                    up_col = QColor(
                        min(255, round(mid_c.red() * 0.35 + tip_c.red() * 0.65)),
                        min(255, round(mid_c.green() * 0.35 + tip_c.green() * 0.65)),
                        min(255, round(mid_c.blue() * 0.35 + tip_c.blue() * 0.65)),
                        210,
                    )
                else:
                    root_col = QColor(back_c.red(), back_c.green(), back_c.blue(), 230)
                    mid_col = QColor(min(255, mid_c.red() + 14), min(255, mid_c.green() + 12), min(255, mid_c.blue() + 8), 222)
                    up_col = tip_c
                g_col.setColorAt(0.0, root_col)
                g_col.setColorAt(0.42, mid_col)
                g_col.setColorAt(1.0, up_col)
                canopy_grads.append(g_col)

            rib_col = QColor(sheen.red(), sheen.green(), sheen.blue(), 128 if wet else 54)
            # Store compact tuples & draw with one reused QPainterPath + single merged rib_path
            shade_buckets: list[list[tuple[float, float, float, float, float, float, float, float, float, float]]] = [[] for _ in range(6)]
            rib_path = QPainterPath()
            has_ribs = False
            dew_glints: list[tuple[float, float, float]] = []

            m_cos = math.cos
            m_sin = math.sin
            hw0_base = 2.60
            hw2_base = 1.28
            has_w = bool(w_scales)
            has_sh = bool(shade_indices)
            for i, (bx, bh, ang, crl) in enumerate(blade_states):
                ws = w_scales[i] if has_w else 1.0
                sh_i = shade_indices[i] if has_sh else (i % 6)
                by_root = _turf_y_at(bx) + 1.8
                floor_y = by_root - 1.5
                th0 = ang * 0.42 + crl * 0.08
                th1 = ang * 0.78 + crl * 0.36
                th2 = ang * 1.06 + crl * 0.78
                th3 = ang * 1.24 + crl * 1.18
                s0 = bh * 0.24
                s1 = bh * 0.27
                s3 = bh * 0.22
                c1x = bx + s0 * m_sin(th0)
                y_raw = by_root - s0 * m_cos(th0)
                c1y = floor_y if y_raw > floor_y else y_raw
                j2x = c1x + s1 * m_sin(th1)
                y_raw = c1y - s1 * m_cos(th1)
                j2y = floor_y if y_raw > floor_y else y_raw
                j3x = j2x + s1 * m_sin(th2)
                y_raw = j2y - s1 * m_cos(th2)
                j3y = floor_y if y_raw > floor_y else y_raw
                tx = j3x + s3 * m_sin(th3)
                y_raw = j3y - s3 * m_cos(th3)
                ty = floor_y if y_raw > floor_y else y_raw

                j0x, j0y = bx, by_root
                hw0 = hw0_base * ws
                n0x, n0y = m_cos(th0) * hw0, m_sin(th0) * hw0
                l0x, l0y = j0x - n0x, j0y - n0y
                r0x, r0y = j0x + n0x, j0y + n0y

                hw2 = hw2_base * ws
                n2x, n2y = m_cos(th1) * hw2, m_sin(th1) * hw2
                l2x, l2y = j2x - n2x, j2y - n2y
                r2x, r2y = j2x + n2x, j2y + n2y

                shade_buckets[sh_i].append((
                    l0x,
                    l0y,
                    2.0 * l2x - 0.5 * (l0x + tx),
                    2.0 * l2y - 0.5 * (l0y + ty),
                    tx,
                    ty,
                    2.0 * r2x - 0.5 * (r0x + tx),
                    2.0 * r2y - 0.5 * (r0y + ty),
                    r0x,
                    r0y,
                ))

                if bh > 52.0 and sh_i >= 3:
                    rib_path.moveTo(j0x, j0y - 1.0)
                    rib_path.quadTo(j2x, j2y, tx, ty)
                    has_ribs = True

                if wet and bh > 36.0 and sh_i >= 3 and (i % 4 == 0):
                    shimmer = 0.5 + 0.5 * m_sin(phase_t * 2.8 + i * 1.7)
                    if shimmer > 0.25:
                        gx_d, gy_d = (j3x, j3y) if (i % 2 == 0) else (j2x, j2y)
                        dew_glints.append((gx_d, gy_d, 0.70 + 0.45 * shimmer))

            def _draw_canopy(cp: QPainter) -> None:
                cp.setPen(Qt.PenStyle.NoPen)
                cp_draw = cp.drawPath
                fp = QPainterPath()
                fp_clear = fp.clear
                fp_move = fp.moveTo
                fp_quad = fp.quadTo
                fp_close = fp.closeSubpath
                for sh_i in range(6):
                    bucket = shade_buckets[sh_i]
                    if not bucket:
                        continue
                    cp.setBrush(canopy_grads[sh_i])
                    for l0x, l0y, cx1, cy1, tx_b, ty_b, cx2, cy2, r0x, r0y in bucket:
                        fp_clear()
                        fp_move(l0x, l0y)
                        fp_quad(cx1, cy1, tx_b, ty_b)
                        fp_quad(cx2, cy2, r0x, r0y)
                        fp_close()
                        cp_draw(fp)
                if has_ribs:
                    cp.setBrush(Qt.BrushStyle.NoBrush)
                    cp.setPen(QPen(rib_col, 0.85 if wet else 0.56))
                    cp_draw(rib_path)
                if dew_glints:
                    cp.setPen(Qt.PenStyle.NoPen)
                    drop_alpha = int(round(190 * max(0.45, rain_vis)))
                    cp.setBrush(QColor(186, 226, 255, int(round(drop_alpha * 0.55))))
                    for gx, gy, gr in dew_glints:
                        cp.drawEllipse(QPointF(gx, gy), gr * 1.45, gr * 1.65)
                    cp.setBrush(QColor(246, 252, 255, drop_alpha))
                    for gx, gy, gr in dew_glints:
                        cp.drawEllipse(QPointF(gx - gr * 0.32, gy - gr * 0.35), gr * 0.52, gr * 0.52)

            if can_calm and grass_sim is not None:
                dpr_c = max(1.0, float(p.device().devicePixelRatioF())) if p.device() is not None else 1.0
                pw = max(1, int(round(can_w * dpr_c)))
                ph = max(1, int(round(can_h * dpr_c)))
                c_img = grass_sim._canopy_img
                if (
                    c_img is None
                    or c_img.width() != pw
                    or c_img.height() != ph
                    or abs(c_img.devicePixelRatio() - dpr_c) > 0.01
                ):
                    c_img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
                    c_img.setDevicePixelRatio(dpr_c)
                    grass_sim._canopy_img = c_img
                c_img.fill(0)
                cp = QPainter(c_img)
                cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                cp.translate(-can_x0, -can_y0)
                _draw_canopy(cp)
                cp.end()
                grass_sim._canopy_key = can_key
                grass_sim._canopy_phase = phase_t
                p.drawImage(QPointF(can_x0, can_y0), c_img)
            else:
                _draw_canopy(p)

        # Foreground pillowed snowdrift rim partially blanketing base of stalks after prolonged snow (2x deep drifts)
        if snow_accum > 0.12:
            fg_drift = QPainterPath()
            bot_d = turf_y + 14.0
            fg_drift.moveTo(eff_left, bot_d)
            for si_x in range(k_start, k_end + 1):
                wx = core.left() + si_x * step_px
                rel = wx - core.left()
                ridge = (
                    0.45
                    + 0.35 * math.cos(rel * 0.026 - 0.7)
                    + 0.20 * math.sin(rel * 0.061 + 0.3)
                )
                dy_f = _turf_y_at(wx) - (1.0 + 19.0 * snow_accum * ridge)
                fg_drift.lineTo(wx, dy_f)
            fg_drift.lineTo(eff_right, bot_d)
            fg_drift.closeSubpath()
            fg_grad = QLinearGradient(QPointF(core.left(), turf_y - 22.0), QPointF(core.left(), bot_d))
            fg_a = min(240, int(round(75 + 165 * snow_accum)))
            fg_grad.setColorAt(0.0, QColor(248, 251, 255, fg_a))
            fg_grad.setColorAt(0.55, QColor(224, 234, 246, fg_a))
            fg_grad.setColorAt(1.0, QColor(178, 194, 216, int(fg_a * 0.85)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fg_grad)
            p.drawPath(fg_drift)

    @staticmethod
    def current_storm_flash(
        phase_t: float,
        weather_kind: str,
        anim_state: AtmosphereAnimator | None = None,
    ) -> float:
        if anim_state is not None and anim_state.initialized:
            th_amt = anim_state.thunder_amt
            li_amt = anim_state.lightning_amt
        else:
            th_amt = 1.0 if weather_kind in ("thunder", "lightning") else 0.0
            li_amt = 1.0 if weather_kind == "lightning" else 0.0
        if th_amt <= 0.05 and li_amt <= 0.05:
            return 0.0
        c_f, g_f, _, _, d_f, dg_f, _ = _eval_storm_state(phase_t, th_amt, li_amt)
        return max(c_f, g_f, d_f * 0.75, dg_f * 0.65)

    @classmethod
    def paint_expanded_atmosphere(
        cls,
        p: QPainter,
        core: QRectF,
        preview_mode: str = "auto",
        weather_symbol: str = "",
        phase_t: float = 0.0,
        now: datetime | None = None,
        *,
        lat: float | None = None,
        lon: float | None = None,
        sky_mode: str = "auto",
        weather_mode: str = "auto",
        moon_mode: str = "auto",
        wind_ms: float = 3.0,
        cloud_phase: float | None = None,
        outer_bottom: float | None = None,
        outer_rect: QRectF | None = None,
        cursor_local: QPointF | None = None,
        music_energy: float = 0.0,
        music_bands: list[float] | None = None,
        grass_sim: GrassSkeletonSim | None = None,
        atmo_anim: AtmosphereAnimator | None = None,
        cloud_sim: CloudSmokeField | None = None,
        quality: float = 1.0,
    ) -> None:
        """Render complete weather + time-of-day scene clipped cleanly inside the expanded island silhouette."""
        if atmo_anim is not None:
            segment, wkind, forced_moon = atmo_anim.step(
                core,
                preview_mode,
                weather_symbol,
                now,
                lat=lat,
                lon=lon,
                sky_mode=sky_mode,
                weather_mode=weather_mode,
                moon_mode=moon_mode,
            )
            sun_c = atmo_anim.sun_pos
            moon_c = atmo_anim.moon_pos
            sun_alpha = atmo_anim.sun_alpha
            moon_alpha = atmo_anim.moon_alpha
            night_w = atmo_anim.seg_weights.get("night", 0.0)
        else:
            segment, wkind, forced_moon = resolve_scene_state(
                preview_mode,
                weather_symbol,
                now,
                lat=lat,
                lon=lon,
                sky_mode=sky_mode,
                weather_mode=weather_mode,
                moon_mode=moon_mode,
            )
            sun_c, moon_c = compute_celestial_layout(
                core,
                segment,
                now,
                preview_mode=preview_mode,
                sky_mode=sky_mode,
                lat=lat,
                lon=lon,
            )
            sun_alpha = 1.0 if sun_c is not None else 0.0
            # Strict mutual exclusion: Moon is NEVER drawn if Sun is present
            moon_alpha = 1.0 if (sun_c is None and moon_c is not None) else 0.0
            night_w = 1.0 if segment == "night" else 0.0

        scene_bounds = QRectF(core)
        if outer_rect is not None and not outer_rect.isEmpty():
            scene_bounds = scene_bounds.united(outer_rect)
        if outer_bottom is not None:
            scene_bounds.setBottom(max(scene_bounds.bottom(), float(outer_bottom)))
        scene_rect = scene_bounds.adjusted(-24.0, -16.0, 24.0, 22.0)
        flash_now = cls.current_storm_flash(phase_t, wkind, anim_state=atmo_anim)
        # Keep sky gradient and precipitation anchored strictly to fixed `core` so cursor slime stretch NEVER moves rain/snow/earth!
        cls.paint_sky_backdrop(
            p,
            scene_rect,
            segment,
            wkind,
            alpha_scale=1.0,
            anim_state=atmo_anim,
            grad_rect=core,
            flash_amt=flash_now,
        )

        top_band = QRectF(core.left(), core.top(), core.width(), min(195.0, core.height() * 0.42))
        if night_w > 0.04:
            clear_vis = 0.25 if wkind in ("rain", "thunder", "lightning", "fog") else (0.60 if wkind in ("cloudy", "partly") else 1.0)
            cls.paint_constellations(p, top_band, phase_t, visibility=clear_vis * night_w)

        if sun_c is not None and sun_alpha > 0.02 and wkind not in ("thunder", "lightning"):
            p.save()
            p.setOpacity(max(0.0, min(1.0, sun_alpha)))
            cls.paint_sun(p, sun_c, 13.5, "day" if segment == "night" else segment, wkind, phase_t)
            p.restore()
        elif moon_c is not None and moon_alpha > 0.02 and sun_alpha <= 0.02:
            if wkind not in ("thunder", "lightning"):
                p.save()
                p.setOpacity(max(0.0, min(1.0, moon_alpha)))
                moon_phase, _, _ = calc_moon_phase(now, force_phase=forced_moon)
                cls.paint_moon(p, moon_c, 16.5, moon_phase)
                p.restore()

        cls.paint_top_clouds_and_light(
            p,
            core,
            segment,
            wkind,
            phase_t,
            wind_ms=wind_ms,
            cloud_phase=cloud_phase,
            cursor_local=cursor_local,
            sun_center=sun_c,
            anim_state=atmo_anim,
            cloud_sim=cloud_sim,
            quality=quality,
            outer_rect=outer_rect,
        )
        snow_vis = atmo_anim.snow_amt if (atmo_anim is not None and atmo_anim.initialized) else (1.0 if wkind == "snow" else 0.0)
        snow_acc = atmo_anim.snow_accum if (atmo_anim is not None and atmo_anim.initialized) else (0.55 if wkind == "snow" else 0.0)
        rain_vis = atmo_anim.rain_amt if (atmo_anim is not None and atmo_anim.initialized) else (1.0 if wkind in ("rain", "thunder", "lightning") else 0.0)
        snowman_p = atmo_anim.snowman_p if atmo_anim is not None else (1.0 if wkind == "snow" else 0.0)
        tree_vis = atmo_anim.tree_vis if atmo_anim is not None else (1.0 if wkind == "snow" else 0.0)
        sunset_w = atmo_anim.seg_weights.get("sunset", 0.0) if (atmo_anim is not None and atmo_anim.initialized) else (1.0 if segment == "sunset" else 0.0)
        light_w = max(0.0, min(1.0, night_w + sunset_w * 0.55))
        cls._paint_background_scene(
            p,
            core,
            turf_y=core.bottom() - 5.0,
            phase_t=phase_t,
            wind_ms=wind_ms,
            cursor_local=cursor_local,
            light_w=light_w,
            rain_amt=rain_vis,
            snow_amt=snow_vis,
            snow_accum=snow_acc,
            snowman_p=snowman_p,
            tree_vis=tree_vis,
        )
        precip_rect = QRectF(core)
        cls.paint_precipitation(
            p,
            precip_rect,
            wkind,
            phase_t,
            turf_y=core.bottom() - 5.0,
            anim_state=atmo_anim,
            cursor_local=cursor_local,
            wind_ms=wind_ms,
            quality=quality,
        )
        cls.paint_bottom_grass(
            p,
            core,
            segment,
            wkind,
            phase_t,
            outer_bottom=outer_bottom,
            outer_rect=outer_rect,
            cursor_local=cursor_local,
            music_energy=music_energy,
            music_bands=music_bands,
            grass_sim=grass_sim,
            anim_state=atmo_anim,
            quality=quality,
        )

    @staticmethod
    def _paint_background_scene(
        p: QPainter,
        core: QRectF,
        *,
        turf_y: float,
        phase_t: float,
        wind_ms: float = 3.0,
        cursor_local: QPointF | None = None,
        light_w: float,
        rain_amt: float,
        snow_amt: float,
        snow_accum: float,
        snowman_p: float,
        tree_vis: float,
    ) -> None:
        """Paint 3D sloped meadow clearing, distant soft conifer forest, 3/4 angled log cabin, seasonal trees & rolling snowman."""
        if core.width() <= 300.0:
            return

        def _clamp01(v: float) -> float:
            return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)

        def _smooth(v: float) -> float:
            t = _clamp01(v)
            return t * t * (3.0 - 2.0 * t)

        sc = max(0.55, min(1.15, float(core.height()) / 560.0))
        wind_force = max(0.0, min(1.0, abs(float(wind_ms)) / 20.0))
        wind_dir = 1.0 if wind_ms >= 0.0 else -1.0

        # -1. Distant misty 2D conifer forest spanning full liquid stretch width (-190..+190 beyond core)
        dpr_f = max(1.0, float(p.device().devicePixelRatioF())) if p.device() is not None else 1.0
        horizon_y = turf_y + 4.0 * sc
        pad_forest_x = 190.0
        f_w = max(64, int(math.ceil(core.width() + pad_forest_x * 2.0)))
        f_h = max(160, int(math.ceil(290.0 * sc)))
        forest_key = (f_w, f_h, round(dpr_f, 2), round(light_w * 4.0), round(snow_amt * 4.0), round(sc, 2))
        cache_dict = getattr(AtmosphericScenePainter, "_forest_cache", None)
        if cache_dict is None:
            cache_dict = {}
            AtmosphericScenePainter._forest_cache = cache_dict
        cached_layers = cache_dict.get(forest_key)
        if cached_layers is None:
            if len(cache_dict) > 6:
                cache_dict.clear()
            cached_layers = []
            layer_specs = (
                (0.68, 125, 0.0, (24, 68, 52), (16, 46, 36)),
                (0.86, 175, 4.0, (20, 60, 44), (11, 36, 26)),
                (1.04, 220, 8.0, (16, 52, 36), (8, 28, 20)),
            )
            for layer_idx, (l_scale, l_alpha, y_off, top_rgb, bot_rgb) in enumerate(layer_specs):
                img_f = QImage(
                    max(1, int(round(f_w * dpr_f))),
                    max(1, int(round(f_h * dpr_f))),
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                img_f.setDevicePixelRatio(dpr_f)
                img_f.fill(0)
                fp_p = QPainter(img_f)
                fp_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                c_top = QColor(
                    int(round(top_rgb[0] * (1.0 - light_w * 0.62) + 32 * snow_amt)),
                    int(round(top_rgb[1] * (1.0 - light_w * 0.58) + 36 * snow_amt)),
                    int(round(top_rgb[2] * (1.0 - light_w * 0.46) + 42 * snow_amt)),
                    l_alpha,
                )
                c_bot = QColor(
                    int(round(bot_rgb[0] * (1.0 - light_w * 0.62) + 18 * snow_amt)),
                    int(round(bot_rgb[1] * (1.0 - light_w * 0.58) + 22 * snow_amt)),
                    int(round(bot_rgb[2] * (1.0 - light_w * 0.46) + 28 * snow_amt)),
                    min(250, l_alpha + 25),
                )
                f_grad = QLinearGradient(QPointF(0.0, float(f_h) * 0.15), QPointF(0.0, float(f_h)))
                f_grad.setColorAt(0.0, c_top)
                f_grad.setColorAt(1.0, c_bot)
                trunk_batch = QPainterPath()
                trunk_batch.setFillRule(Qt.FillRule.WindingFill)
                batched = QPainterPath()
                batched.setFillRule(Qt.FillRule.WindingFill)
                ridge_batch = QPainterPath()
                ridge_batch.setFillRule(Qt.FillRule.WindingFill)
                snow_batched = QPainterPath()
                snow_batched.setFillRule(Qt.FillRule.WindingFill)
                step_f = 28.0 * sc * l_scale
                n_trees_bg = int(f_w / max(9.0, step_f)) + 4
                base_fy = float(f_h) - 2.0
                for fi in range(n_trees_bg):
                    fx = 8.0 + fi * step_f + ((fi * 19 + layer_idx * 11) % 9 - 4) * 1.9 * sc
                    fh = (138.0 + ((fi * 37 + layer_idx * 23) % 92)) * sc * l_scale
                    fw = (48.0 + ((fi * 17 + layer_idx * 7) % 24)) * sc * l_scale
                    tw_f = max(3.5, fw * 0.13)
                    trunk_batch.addRoundedRect(QRectF(fx - tw_f * 0.5, base_fy - fh * 0.32, tw_f, fh * 0.34), 2.0, 2.0)
                    # Detailed 6-tier jagged spruce boughs with feathered branch tips
                    n_tiers = 6
                    for t_i in range(n_tiers):
                        u_t = t_i / float(n_tiers - 1)
                        t_top_y = base_fy - fh * (1.0 - u_t * 0.72)
                        t_bot_y = base_fy - fh * (0.74 - u_t * 0.64)
                        t_hw = fw * (0.22 + u_t * 0.56)
                        batched.moveTo(fx, t_top_y)
                        batched.quadTo(fx + t_hw * 0.46, t_top_y + (t_bot_y - t_top_y) * 0.44, fx + t_hw, t_bot_y - 2.5 * sc)
                        n_teeth = 5 + (t_i // 2)
                        for j_t in range(n_teeth * 2 + 1):
                            fr_t = j_t / float(n_teeth * 2)
                            bx_t = (fx + t_hw) - fr_t * (t_hw * 2.0)
                            tooth = (4.2 * sc * l_scale) if (j_t % 2 == 1) else (-1.8 * sc * l_scale)
                            arch = math.sin(fr_t * math.pi) * (4.5 * sc * l_scale)
                            batched.lineTo(bx_t, t_bot_y + tooth + arch)
                        batched.quadTo(fx - t_hw * 0.46, t_top_y + (t_bot_y - t_top_y) * 0.44, fx, t_top_y)
                        batched.closeSubpath()
                        if layer_idx >= 1 and t_i < 5:
                            rh_w = t_hw * 0.64
                            ridge_batch.moveTo(fx, t_top_y + (t_bot_y - t_top_y) * 0.14)
                            ridge_batch.quadTo(fx + rh_w * 0.45, t_top_y + (t_bot_y - t_top_y) * 0.52, fx + rh_w, t_bot_y - 3.0 * sc)
                            ridge_batch.quadTo(fx, t_bot_y + 1.5 * sc, fx - rh_w, t_bot_y - 3.0 * sc)
                            ridge_batch.quadTo(fx - rh_w * 0.45, t_top_y + (t_bot_y - t_top_y) * 0.52, fx, t_top_y + (t_bot_y - t_top_y) * 0.14)
                            ridge_batch.closeSubpath()
                    if snow_amt > 0.15 and layer_idx >= 1:
                        snow_batched.moveTo(fx, base_fy - fh)
                        snow_batched.quadTo(fx + fw * 0.18, base_fy - fh * 0.86, fx + fw * 0.24, base_fy - fh * 0.78)
                        snow_batched.quadTo(fx, base_fy - fh * 0.75, fx - fw * 0.24, base_fy - fh * 0.78)
                        snow_batched.quadTo(fx - fw * 0.18, base_fy - fh * 0.86, fx, base_fy - fh)
                        snow_batched.closeSubpath()
                fp_p.setPen(Qt.PenStyle.NoPen)
                fp_p.setBrush(QColor(int(48 * (1.0 - light_w * 0.5)), int(30 * (1.0 - light_w * 0.5)), int(20 * (1.0 - light_w * 0.4)), l_alpha))
                fp_p.drawPath(trunk_batch)
                fp_p.setBrush(f_grad)
                fp_p.drawPath(batched)
                if not ridge_batch.isEmpty():
                    hi_c = QColor(min(255, c_top.red() + 14), min(255, c_top.green() + 22), min(255, c_top.blue() + 14), int(l_alpha * 0.52))
                    fp_p.setBrush(hi_c)
                    fp_p.drawPath(ridge_batch)
                if not snow_batched.isEmpty():
                    fp_p.setBrush(QColor(232, 242, 255, int(round(115 * snow_amt))))
                    fp_p.drawPath(snow_batched)
                fp_p.end()
                cached_layers.append((img_f, y_off))
            cache_dict[forest_key] = cached_layers

        for layer_idx, (img_f, y_off) in enumerate(cached_layers):
            sway_x = math.sin(phase_t * (1.2 + wind_force * 2.2) + layer_idx * 1.4) * (1.6 + 5.5 * wind_force) * sc
            p.drawImage(QPointF(core.left() - pad_forest_x + sway_x, horizon_y + y_off * sc - float(f_h) + 6.0 * sc), img_f)

        hx = core.center().x()
        hy = turf_y + 2.0 * sc

        def _draw_snowball(cx: float, cy: float, r: float, rot: float = 0.0) -> None:
            p.save()
            grad = QLinearGradient(QPointF(cx - r * 0.55, cy - r), QPointF(cx + r * 0.65, cy + r))
            grad.setColorAt(0.0, QColor(253, 254, 255))
            grad.setColorAt(0.48, QColor(235, 244, 254))
            grad.setColorAt(0.84, QColor(204, 220, 240))
            grad.setColorAt(1.0, QColor(168, 190, 218))
            p.setPen(QPen(QColor(255, 255, 255, 195), 1.4))
            p.setBrush(grad)
            p.drawEllipse(QPointF(cx, cy), r, r * 0.96)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 110))
            p.drawEllipse(QPointF(cx - r * 0.28, cy - r * 0.32), r * 0.36, r * 0.24)
            for k in range(10):
                ang = rot + k * 0.6283 + (k % 3) * 0.41
                dist_f = 0.26 + ((k * 37) % 10) * 0.055
                px = cx + math.cos(ang) * r * dist_f
                py = cy + math.sin(ang) * r * dist_f * 0.92
                p.setBrush(QColor(185, 206, 232, 95) if k % 2 == 0 else QColor(255, 255, 255, 130))
                pr = max(1.5, r * (0.045 + (k % 3) * 0.018))
                p.drawEllipse(QPointF(px, py), pr * 1.15, pr * 0.85)
            p.restore()

        # 0. Flat 2D Front-Facing Old Wooden Log Cabin on the Horizon (cached static body + live interactive chimney smoke)
        cab_w = 176.0 * sc
        cab_h = 132.0 * sc
        roof_h = 72.0 * sc
        cab_l = hx - cab_w * 0.50
        cab_t = hy - cab_h
        ch_x = hx + cab_w * 0.22
        ch_w = 24.0 * sc
        ch_top = cab_t - roof_h * 0.86

        # Cloud-style volumetric smoke rising from chimney up into the clouds, interacting with mouse cursor!
        smoke_rise_h = max(130.0 * sc, ch_top - core.top() - 10.0 * sc)
        smoke_rgb = (132, 138, 150) if light_w < 0.4 else (105, 112, 126)
        for sk in range(12):
            st = (phase_t * 0.13 + sk / 12.0) % 1.0
            drift_r = (st ** 1.45) * (95.0 + wind_force * 175.0) * sc
            curl_x = math.sin(phase_t * 1.6 + sk * 1.15 + st * 4.5) * (5.0 + st * 11.0) * sc
            sx_s = ch_x + ch_w * 0.5 + drift_r + curl_x
            sy_s = ch_top - 4.0 * sc - (st ** 0.85) * smoke_rise_h
            if cursor_local is not None:
                dx_c = sx_s - cursor_local.x()
                dy_c = sy_s - cursor_local.y()
                d_c = math.hypot(dx_c, dy_c)
                if 1.0 < d_c < 64.0 * sc:
                    push = (64.0 * sc - d_c) * 0.68
                    sx_s += (dx_c / d_c) * push
                    sy_s += (dy_c / d_c) * push * 0.65
            sr_s = (14.0 + st * 38.0) * sc
            fade_env = math.sin(min(1.0, st * 4.5) * math.pi * 0.5) * (1.0 - max(0.0, (st - 0.72) / 0.28))
            sa = int(round(max(0.0, min(145.0, fade_env * 136.0))))
            if sa > 4:
                sm_img = _get_glow_disc_img(smoke_rgb, sa, 36)
                p.drawImage(QRectF(sx_s - sr_s, sy_s - sr_s * 0.72, sr_s * 2.0, sr_s * 1.44), sm_img)

        roof_snow = max(snow_amt * 0.65, snow_accum)
        cab_pad_x = int(math.ceil(64.0 * sc))
        cab_pad_top = int(math.ceil(roof_h + 26.0 * sc))
        cab_pad_bot = int(math.ceil(28.0 * sc))
        cab_img_w = max(64, int(math.ceil(cab_w + cab_pad_x * 2)))
        cab_img_h = max(64, int(math.ceil(cab_h + cab_pad_top + cab_pad_bot)))
        cab_ox = cab_l - cab_pad_x
        cab_oy = cab_t - cab_pad_top
        cabin_key = (cab_img_w, cab_img_h, round(dpr_f, 2), round(sc, 2), round(light_w * 5.0), round(rain_amt * 3.0), round(roof_snow * 5.0))
        cabin_cache = getattr(AtmosphericScenePainter, "_cabin_cache", None)
        if cabin_cache is None or cabin_cache[0] != cabin_key:
            cab_img = QImage(
                max(1, int(round(cab_img_w * dpr_f))),
                max(1, int(round(cab_img_h * dpr_f))),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            cab_img.setDevicePixelRatio(dpr_f)
            cab_img.fill(0)
            cp = QPainter(cab_img)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing)
            cp.translate(-cab_ox, -cab_oy)

            cp.setPen(QPen(QColor(38, 42, 50), 1.2))
            cp.setBrush(QColor(84, 88, 98))
            cp.drawRoundedRect(QRectF(ch_x, ch_top, ch_w, 58.0 * sc), 2.0, 2.0)
            cp.setPen(QPen(QColor(52, 56, 66, 180), 1.0))
            for br_y in range(1, 5):
                yy = ch_top + br_y * 10.0 * sc
                cp.drawLine(QPointF(ch_x + 1.0, yy), QPointF(ch_x + ch_w - 1.0, yy))
            cp.setPen(QPen(QColor(38, 42, 50), 1.2))
            cp.setBrush(QColor(102, 106, 118))
            cp.drawRoundedRect(QRectF(ch_x - 3.5 * sc, ch_top - 4.5 * sc, ch_w + 7.0 * sc, 7.0 * sc), 2.0, 2.0)

            cp.setPen(QPen(QColor(42, 46, 54), 1.2))
            cp.setBrush(QColor(74, 78, 88))
            cp.drawRoundedRect(QRectF(cab_l - 4.0 * sc, hy - 10.0 * sc, cab_w + 8.0 * sc, 13.0 * sc), 2.0, 2.0)

            n_logs = 10
            log_h = (cab_h - 8.0 * sc) / float(n_logs)
            wet_dim = 1.0 - 0.12 * rain_amt
            for li in range(n_logs):
                ly = cab_t + li * log_h
                shade = ((li * 29) % 5) * 4
                lg = QLinearGradient(QPointF(cab_l, ly), QPointF(cab_l, ly + log_h))
                lg.setColorAt(0.0, QColor(int((156 - shade) * wet_dim), int((100 - shade) * wet_dim), int((58 - shade // 2) * wet_dim)))
                lg.setColorAt(0.45, QColor(int((126 - shade) * wet_dim), int((76 - shade) * wet_dim), int((42 - shade // 2) * wet_dim)))
                lg.setColorAt(1.0, QColor(int((76 - shade) * wet_dim), int((44 - shade // 2) * wet_dim), int((24 - shade // 3) * wet_dim)))
                cp.setPen(QPen(QColor(46, 26, 14, 235), max(1.2, 1.4 * sc)))
                cp.setBrush(lg)
                cp.drawRoundedRect(QRectF(cab_l - 11.0 * sc, ly, cab_w + 22.0 * sc, log_h + 0.5), log_h * 0.38, log_h * 0.38)
                cp.setPen(QPen(QColor(58, 34, 18, 115), 0.9))
                g_off = ((li * 19) % 24) * sc
                cp.drawLine(QPointF(cab_l + 8.0 * sc + g_off, ly + log_h * 0.35), QPointF(cab_l + cab_w * 0.48 + g_off, ly + log_h * 0.35))
                cp.drawLine(QPointF(cab_l + cab_w * 0.55 - g_off * 0.5, ly + log_h * 0.68), QPointF(cab_l + cab_w - 10.0 * sc, ly + log_h * 0.68))
                cp.setPen(QPen(QColor(56, 32, 18, 190), 1.0))
                cp.setBrush(QColor(174, 120, 74, 210))
                cp.drawEllipse(QRectF(cab_l - 11.0 * sc, ly + 1.0, log_h * 0.72, log_h - 2.0))
                cp.drawEllipse(QRectF(cab_l + cab_w + 11.0 * sc - log_h * 0.72, ly + 1.0, log_h * 0.72, log_h - 2.0))

            gable = QPainterPath()
            gable.moveTo(cab_l + 4.0 * sc, cab_t + 2.0)
            gable.lineTo(hx, cab_t - roof_h + 6.0 * sc)
            gable.lineTo(cab_l + cab_w - 4.0 * sc, cab_t + 2.0)
            gable.closeSubpath()
            cp.setPen(QPen(QColor(58, 34, 18), 1.2))
            cp.setBrush(QColor(int(116 * wet_dim), int(70 * wet_dim), int(38 * wet_dim)))
            cp.drawPath(gable)

            door_w = 38.0 * sc
            door_h = 74.0 * sc
            door_x = hx - cab_w * 0.22 - door_w * 0.5
            door_y = hy - door_h - 4.0 * sc
            cp.setPen(QPen(QColor(38, 24, 15), max(1.5, 2.0 * sc)))
            cp.setBrush(QColor(68, 42, 26))
            cp.drawRoundedRect(QRectF(door_x, door_y, door_w, door_h), 3.0 * sc, 3.0 * sc)
            cp.setPen(QPen(QColor(40, 24, 14, 170), 1.0))
            cp.drawLine(QPointF(door_x + door_w * 0.33, door_y + 2.0), QPointF(door_x + door_w * 0.33, door_y + door_h - 2.0))
            cp.drawLine(QPointF(door_x + door_w * 0.66, door_y + 2.0), QPointF(door_x + door_w * 0.66, door_y + door_h - 2.0))
            cp.setPen(Qt.PenStyle.NoPen)
            cp.setBrush(QColor(190, 165, 110))
            cp.drawEllipse(QPointF(door_x + door_w - 6.5 * sc, door_y + door_h * 0.54), 2.4 * sc, 2.4 * sc)

            win_w = 46.0 * sc
            win_h = 42.0 * sc
            win_x = hx + cab_w * 0.18 - win_w * 0.5
            win_y = cab_t + 44.0 * sc
            attic_r = 14.0 * sc
            attic_y = cab_t - roof_h * 0.36

            if light_w > 0.08:
                w_glow = _get_glow_disc_img((255, 196, 88), int(round(145 * light_w)), 64)
                gs = 110.0 * sc
                cp.drawImage(QRectF(win_x + win_w * 0.5 - gs * 0.5, win_y + win_h * 0.5 - gs * 0.5, gs, gs), w_glow)
                cp.drawImage(QRectF(hx - gs * 0.35, attic_y - gs * 0.35, gs * 0.7, gs * 0.7), w_glow)
                lan_x, lan_y = door_x + door_w + 10.0 * sc, door_y + 14.0 * sc
                cp.drawImage(QRectF(lan_x - 24.0 * sc, lan_y - 24.0 * sc, 48.0 * sc, 48.0 * sc), w_glow)
                cp.setBrush(QColor(255, 224, 130, int(round(235 * light_w))))
                cp.drawEllipse(QPointF(lan_x, lan_y), 3.6 * sc, 4.5 * sc)

            day_glass = QColor(72, 108, 138, 220)
            night_glass = QColor(255, 206, 98, 248)
            wg = QColor(
                int(round(day_glass.red() * (1.0 - light_w) + night_glass.red() * light_w)),
                int(round(day_glass.green() * (1.0 - light_w) + night_glass.green() * light_w)),
                int(round(day_glass.blue() * (1.0 - light_w) + night_glass.blue() * light_w)),
                int(round(day_glass.alpha() * (1.0 - light_w) + night_glass.alpha() * light_w)),
            )
            cp.setPen(QPen(QColor(44, 28, 18), max(2.0, 3.0 * sc)))
            cp.setBrush(wg)
            cp.drawRoundedRect(QRectF(win_x, win_y, win_w, win_h), 3.0 * sc, 3.0 * sc)
            cp.setPen(QPen(QColor(52, 34, 22), max(1.5, 2.2 * sc)))
            cp.drawLine(QPointF(win_x + win_w * 0.5, win_y), QPointF(win_x + win_w * 0.5, win_y + win_h))
            cp.drawLine(QPointF(win_x, win_y + win_h * 0.5), QPointF(win_x + win_w, win_y + win_h * 0.5))
            cp.setBrush(QColor(72, 46, 28))
            cp.drawRoundedRect(QRectF(win_x - 4.0 * sc, win_y + win_h, win_w + 8.0 * sc, 5.5 * sc), 2.0, 2.0)

            cp.setPen(QPen(QColor(44, 28, 18), max(1.8, 2.4 * sc)))
            cp.setBrush(wg)
            cp.drawEllipse(QPointF(hx, attic_y), attic_r, attic_r)
            cp.drawLine(QPointF(hx - attic_r, attic_y), QPointF(hx + attic_r, attic_y))
            cp.drawLine(QPointF(hx, attic_y - attic_r), QPointF(hx, attic_y + attic_r))

            overhang = 22.0 * sc
            roof_p = QPainterPath()
            roof_p.moveTo(hx, cab_t - roof_h - 4.0 * sc)
            roof_p.lineTo(cab_l + cab_w + overhang, cab_t + 8.0 * sc)
            roof_p.lineTo(cab_l + cab_w + overhang - 6.0 * sc, cab_t + 16.0 * sc)
            roof_p.lineTo(hx, cab_t - roof_h + 7.0 * sc)
            roof_p.lineTo(cab_l - overhang + 6.0 * sc, cab_t + 16.0 * sc)
            roof_p.lineTo(cab_l - overhang, cab_t + 8.0 * sc)
            roof_p.closeSubpath()
            rg = QLinearGradient(QPointF(hx, cab_t - roof_h), QPointF(hx, cab_t + 14.0 * sc))
            rg.setColorAt(0.0, QColor(int(76 * wet_dim), int(48 * wet_dim), int(34 * wet_dim)))
            rg.setColorAt(1.0, QColor(int(48 * wet_dim), int(30 * wet_dim), int(22 * wet_dim)))
            cp.setPen(QPen(QColor(30, 18, 12), 1.4))
            cp.setBrush(rg)
            cp.drawPath(roof_p)
            cp.save()
            cp.setClipPath(roof_p)
            cp.setPen(QPen(QColor(32, 18, 10, 140), 1.0))
            for sr in range(1, 6):
                ry = (cab_t - roof_h) + sr * (roof_h / 5.5)
                cp.drawLine(QPointF(cab_l - overhang, ry), QPointF(cab_l + cab_w + overhang, ry))
            cp.restore()

            if roof_snow > 0.04:
                sn_alpha = int(round(242 * min(1.0, roof_snow * 1.45)))
                sn_thick = (5.0 + 13.0 * roof_snow) * sc
                sn_roof = QPainterPath()
                sn_roof.moveTo(hx, cab_t - roof_h - 6.0 * sc)
                sn_roof.lineTo(cab_l + cab_w + overhang + 2.0 * sc, cab_t + 7.0 * sc)
                for k_s in range(6, -1, -1):
                    u_s = k_s / 6.0
                    sx_e = hx + (cab_l + cab_w + overhang - hx) * u_s
                    sy_e = (cab_t - roof_h - 4.0 * sc) + (roof_h + 12.0 * sc) * u_s
                    bulge = sn_thick * (0.45 + 0.55 * (u_s ** 0.7)) * (0.85 + 0.25 * math.sin(u_s * 12.5))
                    sn_roof.lineTo(sx_e, sy_e + bulge)
                for k_s in range(1, 7):
                    u_s = k_s / 6.0
                    sx_e = hx - (hx - (cab_l - overhang)) * u_s
                    sy_e = (cab_t - roof_h - 4.0 * sc) + (roof_h + 12.0 * sc) * u_s
                    bulge = sn_thick * (0.45 + 0.55 * (u_s ** 0.7)) * (0.85 + 0.25 * math.cos(u_s * 11.0))
                    sn_roof.lineTo(sx_e, sy_e + bulge)
                sn_roof.lineTo(cab_l - overhang - 2.0 * sc, cab_t + 7.0 * sc)
                sn_roof.closeSubpath()

                sg_r = QLinearGradient(QPointF(hx, cab_t - roof_h - 6.0 * sc), QPointF(hx, cab_t + 22.0 * sc))
                sg_r.setColorAt(0.0, QColor(253, 254, 255, sn_alpha))
                sg_r.setColorAt(0.72, QColor(232, 242, 254, sn_alpha))
                sg_r.setColorAt(1.0, QColor(194, 212, 236, sn_alpha))
                cp.setPen(QPen(QColor(255, 255, 255, int(sn_alpha * 0.6)), 0.9))
                cp.setBrush(sg_r)
                cp.drawPath(sn_roof)

                cp.setPen(Qt.PenStyle.NoPen)
                cp.setBrush(QColor(246, 251, 255, sn_alpha))
                cp.drawRoundedRect(QRectF(ch_x - 4.0 * sc, ch_top - 8.0 * sc, ch_w + 8.0 * sc, 5.5 * sc), 2.5, 2.5)
                cp.drawRoundedRect(QRectF(win_x - 4.0 * sc, win_y + win_h - 3.5 * sc, win_w + 8.0 * sc, 5.5 * sc), 2.5, 2.5)
                cp.setBrush(QColor(210, 232, 255, int(round(sn_alpha * 0.78))))
                for ic in range(5):
                    ix = cab_l - overhang + (cab_w + overhang * 2.0) * (0.08 + ic * 0.21)
                    ih_ic = (5.0 + (ic % 3) * 3.0) * sc * min(1.0, roof_snow * 1.4)
                    ic_p = QPainterPath()
                    ic_p.moveTo(ix - 1.6 * sc, cab_t + 14.0 * sc)
                    ic_p.lineTo(ix + 1.6 * sc, cab_t + 14.0 * sc)
                    ic_p.lineTo(ix, cab_t + 14.0 * sc + ih_ic)
                    ic_p.closeSubpath()
                    cp.drawPath(ic_p)
            cp.end()
            cabin_cache = (cabin_key, cab_img)
            AtmosphericScenePainter._cabin_cache = cabin_cache

        p.drawImage(QPointF(cab_ox, cab_oy), cabin_cache[1])

        # Lush 2D layered deciduous trees with sculpted cloud-foliage crowns & gentle wind sway (zero polka-dot loops)
        leafy_vis = max(0.0, 1.0 - max(snow_amt, tree_vis if snow_amt > 0.1 else 0.0))
        if leafy_vis > 0.02:
            def _draw_leafy_tree(tx_l: float, scale_t: float, seed_off: float) -> None:
                p.save()
                p.setOpacity(leafy_vis)
                by_l = turf_y + 8.0 * sc
                ts = sc * scale_t
                gust = math.sin(phase_t * (1.5 + wind_force * 2.2) + seed_off)
                lean = wind_dir * (2.5 + 22.0 * (wind_force ** 1.35)) * ts + gust * (2.5 + 6.5 * wind_force) * ts

                # Tapered wood trunk with natural root flare & gracefully forked branches
                p.setPen(Qt.PenStyle.NoPen)
                tr_g = QLinearGradient(QPointF(tx_l - 12 * ts, by_l), QPointF(tx_l + 12 * ts, by_l))
                tr_g.setColorAt(0.0, QColor(96, 62, 38))
                tr_g.setColorAt(0.45, QColor(122, 80, 50))
                tr_g.setColorAt(1.0, QColor(58, 36, 22))
                p.setBrush(tr_g)
                trunk_p = QPainterPath()
                trunk_p.moveTo(tx_l - 14.0 * ts, by_l + 4.0)
                trunk_p.quadTo(tx_l - 6.5 * ts + lean * 0.22, by_l - 54.0 * ts, tx_l - 4.5 * ts + lean * 0.60, by_l - 118.0 * ts)
                trunk_p.lineTo(tx_l + 4.5 * ts + lean * 0.60, by_l - 118.0 * ts)
                trunk_p.quadTo(tx_l + 6.5 * ts + lean * 0.22, by_l - 54.0 * ts, tx_l + 14.0 * ts, by_l + 4.0)
                trunk_p.closeSubpath()
                p.drawPath(trunk_p)

                p.setPen(QPen(QColor(82, 52, 32), max(2.0, 4.4 * ts), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(tx_l + lean * 0.42, by_l - 86.0 * ts), QPointF(tx_l - 36.0 * ts + lean * 0.82, by_l - 134.0 * ts))
                p.drawLine(QPointF(tx_l + lean * 0.46, by_l - 90.0 * ts), QPointF(tx_l + 38.0 * ts + lean * 0.82, by_l - 136.0 * ts))
                p.drawLine(QPointF(tx_l + lean * 0.58, by_l - 112.0 * ts), QPointF(tx_l - 14.0 * ts + lean * 0.90, by_l - 160.0 * ts))
                p.drawLine(QPointF(tx_l + lean * 0.58, by_l - 112.0 * ts), QPointF(tx_l + 16.0 * ts + lean * 0.90, by_l - 162.0 * ts))

                # 3-tier sculpted 2D foliage canopy domes (deep shadow base -> rich emerald mid -> sunlit top crown)
                dim_f = int(100 + 68 * light_w) if light_w > 0.22 else 100
                c_shadow = QColor(16, 78, 42).darker(dim_f)
                c_deep = QColor(22, 108, 56).darker(dim_f)
                c_mid = QColor(32, 138, 70).darker(dim_f)
                c_bright = QColor(54, 168, 88).darker(dim_f)
                c_rim = QColor(94, 204, 122, 185).darker(dim_f)

                cx_c = tx_l + lean * 0.88
                cy_c = by_l - 156.0 * ts
                p.setPen(Qt.PenStyle.NoPen)

                # Back/lower shadow foliage clusters
                p.setBrush(c_shadow)
                for ox, oy, rx, ry in (
                    (-38.0, 20.0, 32.0, 22.0),
                    (38.0, 20.0, 32.0, 22.0),
                    (0.0, 24.0, 44.0, 24.0),
                    (-24.0, 4.0, 36.0, 26.0),
                    (26.0, 4.0, 36.0, 26.0),
                ):
                    p.drawEllipse(QPointF(cx_c + ox * ts, cy_c + oy * ts), rx * ts, ry * ts)

                # Mid emerald foliage puffs with soft vertical gradient
                mid_g = QLinearGradient(QPointF(cx_c, cy_c - 38.0 * ts), QPointF(cx_c, cy_c + 24.0 * ts))
                mid_g.setColorAt(0.0, c_mid)
                mid_g.setColorAt(1.0, c_deep)
                p.setBrush(mid_g)
                for ox, oy, rx, ry in (
                    (-32.0, 6.0, 30.0, 23.0),
                    (32.0, 8.0, 30.0, 23.0),
                    (-18.0, -12.0, 34.0, 26.0),
                    (20.0, -10.0, 34.0, 26.0),
                    (0.0, 4.0, 38.0, 27.0),
                    (0.0, -24.0, 32.0, 24.0),
                ):
                    p.drawEllipse(QPointF(cx_c + ox * ts, cy_c + oy * ts), rx * ts, ry * ts)

                # Sunlit upper-left & crown highlight domes
                top_g = QLinearGradient(QPointF(cx_c - 20.0 * ts, cy_c - 46.0 * ts), QPointF(cx_c + 16.0 * ts, cy_c + 6.0 * ts))
                top_g.setColorAt(0.0, c_rim)
                top_g.setColorAt(0.45, c_bright)
                top_g.setColorAt(1.0, c_mid)
                p.setBrush(top_g)
                for ox, oy, rx, ry in (
                    (-24.0, -4.0, 22.0, 17.0),
                    (-12.0, -20.0, 25.0, 19.0),
                    (4.0, -28.0, 24.0, 18.0),
                    (18.0, -14.0, 22.0, 17.0),
                    (-4.0, -8.0, 26.0, 19.0),
                ):
                    p.drawEllipse(QPointF(cx_c + ox * ts, cy_c + oy * ts), rx * ts, ry * ts)
                p.restore()

            _draw_leafy_tree(core.right() - 128.0 * sc, 0.92, 0.0)
            _draw_leafy_tree(core.left() + 132.0 * sc, 0.80, 1.9)
            _draw_leafy_tree(hx - 148.0 * sc, 0.56, 3.4)
            _draw_leafy_tree(hx + 168.0 * sc, 0.52, 4.8)

        if snow_amt <= 0.04:
            return

        def _draw_snowball(cx: float, cy: float, r: float, rot: float = 0.0) -> None:
            p.save()
            grad = QLinearGradient(QPointF(cx - r * 0.55, cy - r), QPointF(cx + r * 0.65, cy + r))
            grad.setColorAt(0.0, QColor(253, 254, 255))
            grad.setColorAt(0.48, QColor(235, 244, 254))
            grad.setColorAt(0.84, QColor(204, 220, 240))
            grad.setColorAt(1.0, QColor(168, 190, 218))
            p.setPen(QPen(QColor(255, 255, 255, 195), 1.4))
            p.setBrush(grad)
            p.drawEllipse(QPointF(cx, cy), r, r * 0.96)

            # Soft specular highlight on upper-left
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 110))
            p.drawEllipse(QPointF(cx - r * 0.28, cy - r * 0.32), r * 0.36, r * 0.24)

            # Detailed snow grain / frost texture speckles so rotation and surface read clearly at large scale
            for k in range(10):
                ang = rot + k * 0.6283 + (k % 3) * 0.41
                dist_f = 0.26 + ((k * 37) % 10) * 0.055
                px = cx + math.cos(ang) * r * dist_f
                py = cy + math.sin(ang) * r * dist_f * 0.92
                p.setBrush(QColor(185, 206, 232, 95) if k % 2 == 0 else QColor(255, 255, 255, 130))
                pr = max(1.5, r * (0.045 + (k % 3) * 0.018))
                p.drawEllipse(QPointF(px, py), pr * 1.15, pr * 0.85)
            p.restore()

        # 1. Large Right-Side New Year Pine Tree with visible needles, ornaments & twinkling garland (behind grass)
        if tree_vis > 0.02 and core.width() > 300.0:
            p.save()
            p.setOpacity(max(0.0, min(1.0, tree_vis * snow_amt)))
            tx = core.right() - 128.0 * sc
            by = turf_y + 10.0 * sc

            # Warm ambient tree glow
            glow_img = _get_glow_disc_img((255, 220, 120), 68, 72)
            p.drawImage(QRectF(tx - 150.0 * sc, by - 320.0 * sc, 300.0 * sc, 320.0 * sc), glow_img)

            # Textured wood trunk
            tw = 22.0 * sc
            th = 42.0 * sc
            trunk_g = QLinearGradient(QPointF(tx - tw * 0.5, by), QPointF(tx + tw * 0.5, by))
            trunk_g.setColorAt(0.0, QColor(72, 44, 28))
            trunk_g.setColorAt(0.45, QColor(102, 64, 42))
            trunk_g.setColorAt(1.0, QColor(54, 32, 20))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(trunk_g)
            p.drawRoundedRect(QRectF(tx - tw * 0.5, by - th, tw, th + 6.0), 4.0, 4.0)

            # 5 lush pine tiers with jagged branch silhouettes, wind lean & swaying pine needles
            pine_gust = math.sin(phase_t * (2.0 + wind_force * 2.8))
            pine_lean = wind_dir * (2.5 + 22.0 * (wind_force ** 1.35)) * sc + pine_gust * (2.5 + 6.5 * wind_force) * sc
            tiers = (
                (by - 28.0 * sc, 112.0 * sc, 82.0 * sc, 0.12),
                (by - 82.0 * sc, 94.0 * sc, 74.0 * sc, 0.30),
                (by - 134.0 * sc, 76.0 * sc, 66.0 * sc, 0.52),
                (by - 182.0 * sc, 58.0 * sc, 58.0 * sc, 0.74),
                (by - 224.0 * sc, 40.0 * sc, 52.0 * sc, 0.96),
            )
            for t_idx, (t_bot, half_w, tier_h, lean_k) in enumerate(tiers):
                t_top = t_bot - tier_h
                tx_t = tx + pine_lean * lean_k
                tx_b = tx + pine_lean * max(0.0, lean_k - 0.14)

                # Jagged feathery bough silhouette
                bough = QPainterPath()
                bough.moveTo(tx_t, t_top)
                bough.lineTo(tx_t + half_w * 0.45, t_top + tier_h * 0.42)
                bough.lineTo(tx_b + half_w, t_bot - 4.0 * sc)
                n_teeth = 7 - (t_idx // 2)
                for j in range(n_teeth * 2 + 1):
                    frac = j / float(n_teeth * 2)
                    bx_t = (tx_b + half_w) - frac * (half_w * 2.0)
                    tooth_drop = (6.5 * sc) if (j % 2 == 1) else (-3.0 * sc)
                    arch = math.sin(frac * math.pi) * (7.0 * sc)
                    bough.lineTo(bx_t, t_bot + tooth_drop + arch)
                bough.lineTo(tx_t - half_w * 0.45, t_top + tier_h * 0.42)
                bough.closeSubpath()

                tg = QLinearGradient(QPointF(tx_t - half_w, t_top), QPointF(tx_b + half_w, t_bot))
                tg.setColorAt(0.0, QColor(32, 116, 68))
                tg.setColorAt(0.50, QColor(18, 82, 48))
                tg.setColorAt(1.0, QColor(10, 52, 32))
                p.setBrush(tg)
                p.setPen(QPen(QColor(46, 146, 88, 145), 1.0))
                p.drawPath(bough)

                # Realistic layered pine bough ridges (clean shaded spruce tiers without spiky stick needles)
                ridge_p = QPainterPath()
                ridge_p.moveTo(tx_t, t_top + tier_h * 0.16)
                ridge_p.quadTo(tx_t + half_w * 0.34, t_top + tier_h * 0.52, tx_b + half_w * 0.72, t_bot - 6.0 * sc)
                ridge_p.quadTo(tx_b, t_bot - 2.0 * sc, tx_b - half_w * 0.72, t_bot - 6.0 * sc)
                ridge_p.quadTo(tx_t - half_w * 0.34, t_top + tier_h * 0.52, tx_t, t_top + tier_h * 0.16)
                ridge_p.closeSubpath()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(28, 112, 66, 175))
                p.drawPath(ridge_p)

                # Soft snow dusting on upper bough ridges
                snow_cap = QPainterPath()
                snow_cap.moveTo(tx_t, t_top)
                snow_cap.lineTo(tx_t + half_w * 0.44, t_top + tier_h * 0.36)
                snow_cap.quadTo(tx_t + half_w * 0.16, t_top + tier_h * 0.44, tx_t, t_top + tier_h * 0.38)
                snow_cap.quadTo(tx_t - half_w * 0.16, t_top + tier_h * 0.44, tx_t - half_w * 0.44, t_top + tier_h * 0.36)
                snow_cap.closeSubpath()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(242, 249, 255, 195))
                p.drawPath(snow_cap)

            # Draped garland strands + twinkling bulbs
            strand_bulbs = [
                # Top strand
                [(tx - 24 * sc, by - 238 * sc, (255, 215, 90)), (tx - 4 * sc, by - 230 * sc, (248, 113, 113)), (tx + 20 * sc, by - 234 * sc, (56, 189, 248))],
                # Upper-mid strand
                [(tx - 44 * sc, by - 190 * sc, (52, 211, 153)), (tx - 14 * sc, by - 178 * sc, (255, 215, 90)), (tx + 16 * sc, by - 179 * sc, (244, 114, 182)), (tx + 44 * sc, by - 188 * sc, (56, 189, 248))],
                # Mid strand
                [(tx - 62 * sc, by - 140 * sc, (248, 113, 113)), (tx - 26 * sc, by - 126 * sc, (56, 189, 248)), (tx + 8 * sc, by - 124 * sc, (255, 215, 90)), (tx + 40 * sc, by - 130 * sc, (52, 211, 153)), (tx + 62 * sc, by - 141 * sc, (244, 114, 182))],
                # Lower strand
                [(tx - 82 * sc, by - 88 * sc, (255, 215, 90)), (tx - 42 * sc, by - 72 * sc, (248, 113, 113)), (tx + 2 * sc, by - 68 * sc, (52, 211, 153)), (tx + 44 * sc, by - 73 * sc, (56, 189, 248)), (tx + 82 * sc, by - 88 * sc, (255, 215, 90))],
                # Bottom strand
                [(tx - 96 * sc, by - 36 * sc, (56, 189, 248)), (tx - 48 * sc, by - 22 * sc, (244, 114, 182)), (tx, by - 18 * sc, (255, 215, 90)), (tx + 48 * sc, by - 22 * sc, (248, 113, 113)), (tx + 96 * sc, by - 36 * sc, (52, 211, 153))],
            ]
            def _pine_dx(yv: float) -> float:
                return pine_lean * max(0.0, min(1.04, (by - yv) / max(1.0, 260.0 * sc)))

            bulb_idx = 0
            for strand in strand_bulbs:
                shifted_strand = [(bx_s + _pine_dx(by_s), by_s, rgb_s) for bx_s, by_s, rgb_s in strand]
                p.setPen(QPen(QColor(22, 42, 32, 200), max(1.2, 1.8 * sc)))
                for si in range(len(shifted_strand) - 1):
                    p.drawLine(QPointF(shifted_strand[si][0], shifted_strand[si][1]), QPointF(shifted_strand[si + 1][0], shifted_strand[si + 1][1]))
                p.setPen(Qt.PenStyle.NoPen)
                for bx, by_b, rgb in shifted_strand:
                    tw = 0.50 + 0.50 * math.sin(phase_t * 3.8 + bulb_idx * 1.1)
                    b_glow = _get_glow_disc_img(rgb, int(round(95 + 115 * tw)), 24)
                    gs = 24.0 * sc
                    p.drawImage(QRectF(bx - gs * 0.5, by_b - gs * 0.5, gs, gs), b_glow)
                    p.setBrush(QColor(rgb[0], rgb[1], rgb[2], int(round(180 + 75 * tw))))
                    p.drawEllipse(QPointF(bx, by_b), 4.2 * sc, 4.2 * sc)
                    p.setBrush(QColor(255, 255, 255, int(round(140 * tw))))
                    p.drawEllipse(QPointF(bx - 1.1 * sc, by_b - 1.1 * sc), 1.3 * sc, 1.3 * sc)
                    bulb_idx += 1

            # Glossy ornaments (swaying together with the pine boughs)
            ornaments = [
                (tx - 18 * sc, by - 208 * sc, QColor(239, 68, 68)),
                (tx + 24 * sc, by - 160 * sc, QColor(250, 204, 21)),
                (tx - 38 * sc, by - 110 * sc, QColor(59, 130, 246)),
                (tx + 18 * sc, by - 102 * sc, QColor(236, 72, 153)),
                (tx - 62 * sc, by - 58 * sc, QColor(250, 204, 21)),
                (tx + 58 * sc, by - 56 * sc, QColor(239, 68, 68)),
                (tx - 16 * sc, by - 46 * sc, QColor(56, 189, 248)),
            ]
            for ox_raw, oy, ocol in ornaments:
                ox = ox_raw + _pine_dx(oy)
                r_o = 5.6 * sc
                p.setPen(QPen(ocol.darker(145), 1.0))
                p.setBrush(ocol)
                p.drawEllipse(QPointF(ox, oy), r_o, r_o)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 165))
                p.drawEllipse(QPointF(ox - r_o * 0.32, oy - r_o * 0.32), r_o * 0.28, r_o * 0.28)

            # Radiant golden star on top (swaying in lockstep with the tree apex)
            star_y = by - 280.0 * sc
            star_x = tx + _pine_dx(star_y)
            star_tw = 0.72 + 0.28 * math.sin(phase_t * 2.8)
            s_glow = _get_glow_disc_img((255, 232, 115), int(round(145 * star_tw)), 40)
            s_sz = 46.0 * sc
            p.drawImage(QRectF(star_x - s_sz * 0.5, star_y - s_sz * 0.5, s_sz, s_sz), s_glow)
            star_p = QPainterPath()
            for k in range(10):
                ang = -math.pi * 0.5 + k * (math.pi / 5.0)
                rad = (15.5 if k % 2 == 0 else 6.8) * sc
                pt = QPointF(star_x + math.cos(ang) * rad, star_y + math.sin(ang) * rad)
                if k == 0:
                    star_p.moveTo(pt)
                else:
                    star_p.lineTo(pt)
            star_p.closeSubpath()
            p.setPen(QPen(QColor(245, 158, 11, 210), 1.2))
            p.setBrush(QColor(253, 224, 71))
            p.drawPath(star_p)
            p.restore()

        # 2. Large Self-Rolling & Self-Decorating Snowman on Left-Center Background (behind grass)
        if snowman_p > 0.01 and core.width() > 300.0:
            p.save()
            p.setOpacity(max(0.0, min(1.0, snow_amt)))
            sm_x = core.left() + 148.0 * sc
            ground_y = turf_y + 8.0 * sc
            r1, r2, r3 = 48.0 * sc, 35.0 * sc, 25.0 * sc

            # Stage 1 (0.00..0.30): Bottom snowball rolls in from left
            t1 = _smooth(snowman_p / 0.30)
            b1_x = (core.left() + 26.0 * sc) + (sm_x - (core.left() + 26.0 * sc)) * t1
            b1_r = r1 * (0.42 + 0.58 * t1)
            b1_y = ground_y - b1_r
            _draw_snowball(b1_x, b1_y, b1_r, rot=t1 * 6.28)

            # Stage 2 (0.30..0.56): Middle torso snowball rolls in & hops onto bottom ball
            torso_y = ground_y - r1 * 1.72 - r2
            if snowman_p > 0.30:
                t2 = _smooth((snowman_p - 0.30) / 0.26)
                b2_x = (core.left() + 34.0 * sc) + (sm_x - (core.left() + 34.0 * sc)) * t2
                hop2 = math.sin(t2 * math.pi) * (54.0 * sc) if t2 < 1.0 else 0.0
                b2_y = (ground_y - r2) + (torso_y - (ground_y - r2)) * (t2 ** 1.35) - hop2
                _draw_snowball(b2_x, b2_y, r2 * (0.52 + 0.48 * t2), rot=t2 * 7.5)

            # Stage 3 (0.56..0.78): Head snowball rolls in & hops onto torso
            head_y = torso_y - r2 * 0.84 - r3
            if snowman_p > 0.56:
                t3 = _smooth((snowman_p - 0.56) / 0.22)
                b3_x = (sm_x + 125.0 * sc) + (sm_x - (sm_x + 125.0 * sc)) * t3
                hop3 = math.sin(t3 * math.pi) * (62.0 * sc) if t3 < 1.0 else 0.0
                b3_y = (ground_y - r3) + (head_y - (ground_y - r3)) * (t3 ** 1.35) - hop3
                _draw_snowball(b3_x, b3_y, r3 * (0.60 + 0.40 * t3), rot=-t3 * 8.0)

            # Stage 4 (0.78..1.00): Self-decorating accessories
            if snowman_p > 0.78:
                f_coal = _smooth((snowman_p - 0.78) / 0.07)
                p.save()
                p.setOpacity(f_coal * snow_amt)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(24, 28, 36))
                p.drawEllipse(QPointF(sm_x - 8.2 * sc, head_y - 5.5 * sc), 3.6 * sc, 3.6 * sc)
                p.drawEllipse(QPointF(sm_x + 8.2 * sc, head_y - 5.5 * sc), 3.6 * sc, 3.6 * sc)
                p.setBrush(QColor(255, 255, 255, 190))
                p.drawEllipse(QPointF(sm_x - 9.2 * sc, head_y - 6.6 * sc), 1.1 * sc, 1.1 * sc)
                p.drawEllipse(QPointF(sm_x + 7.2 * sc, head_y - 6.6 * sc), 1.1 * sc, 1.1 * sc)
                p.setBrush(QColor(24, 28, 36))
                for m in range(-2, 3):
                    p.drawEllipse(QPointF(sm_x + m * 4.6 * sc, head_y + 8.8 * sc - abs(m) * 1.1 * sc), 2.1 * sc, 2.1 * sc)
                for by_off in (-11.0 * sc, 2.0 * sc, 15.0 * sc):
                    p.drawEllipse(QPointF(sm_x, torso_y + by_off), 4.0 * sc, 4.0 * sc)
                p.restore()

            if snowman_p > 0.83:
                f_nose = _smooth((snowman_p - 0.83) / 0.06)
                nose_len = 24.0 * sc * f_nose
                nose = QPainterPath()
                nose.moveTo(sm_x, head_y - 2.2 * sc)
                nose.lineTo(sm_x + nose_len, head_y + 2.5 * sc)
                nose.lineTo(sm_x, head_y + 6.2 * sc)
                nose.closeSubpath()
                p.setPen(QPen(QColor(194, 65, 12, 180), 1.0))
                p.setBrush(QColor(249, 115, 22))
                p.drawPath(nose)

            if snowman_p > 0.87:
                f_arms = _smooth((snowman_p - 0.87) / 0.06)
                wave = math.sin(phase_t * 2.4) * (6.5 * sc) * f_arms
                p.setPen(QPen(QColor(102, 66, 42), max(2.2, 4.5 * sc), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                lx0, ly0 = sm_x - r2 + 3.0 * sc, torso_y - 3.0 * sc
                lx1, ly1 = sm_x - r2 - 34.0 * sc * f_arms, torso_y - 20.0 * sc * f_arms + wave
                rx0, ry0 = sm_x + r2 - 3.0 * sc, torso_y - 3.0 * sc
                rx1, ry1 = sm_x + r2 + 34.0 * sc * f_arms, torso_y - 20.0 * sc * f_arms - wave
                p.drawLine(QPointF(lx0, ly0), QPointF(lx1, ly1))
                p.drawLine(QPointF(rx0, ry0), QPointF(rx1, ry1))
                # Little branch fingers
                p.setPen(QPen(QColor(102, 66, 42), max(1.5, 2.6 * sc), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(lx1 + 8.0 * sc, ly1 + 4.0 * sc), QPointF(lx1 - 4.0 * sc, ly1 - 9.0 * sc))
                p.drawLine(QPointF(rx1 - 8.0 * sc, ry1 + 4.0 * sc), QPointF(rx1 + 4.0 * sc, ry1 - 9.0 * sc))

            if snowman_p > 0.91:
                f_scarf = _smooth((snowman_p - 0.91) / 0.05)
                neck_y = head_y + r3 * 0.78
                p.setPen(QPen(QColor(153, 27, 27, int(round(200 * f_scarf))), 1.0))
                p.setBrush(QColor(220, 38, 38, int(round(245 * f_scarf))))
                p.drawRoundedRect(QRectF(sm_x - 25.0 * sc * f_scarf, neck_y - 6.0 * sc, 50.0 * sc * f_scarf, 12.5 * sc), 5.0 * sc, 5.0 * sc)
                p.drawRoundedRect(QRectF(sm_x + 7.5 * sc, neck_y + 3.0 * sc, 11.5 * sc, 27.0 * sc * f_scarf), 4.0 * sc, 4.0 * sc)
                # Gold stripe on scarf
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(250, 204, 21, int(round(215 * f_scarf))))
                p.drawRect(QRectF(sm_x + 7.5 * sc, neck_y + 22.0 * sc * f_scarf, 11.5 * sc, 3.0 * sc))

            if snowman_p > 0.95:
                f_hat = _smooth((snowman_p - 0.95) / 0.05)
                hat_y = head_y - r3 + 3.0 * sc - (1.0 - f_hat) * (40.0 * sc)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(26, 30, 40, int(round(248 * f_hat))))
                p.drawRoundedRect(QRectF(sm_x - 28.0 * sc, hat_y - 6.0 * sc, 56.0 * sc, 9.0 * sc), 4.0 * sc, 4.0 * sc)
                p.drawRoundedRect(QRectF(sm_x - 18.0 * sc, hat_y - 32.0 * sc, 36.0 * sc, 27.0 * sc), 4.5 * sc, 4.5 * sc)
                p.setBrush(QColor(220, 38, 38, int(round(245 * f_hat))))
                p.drawRect(QRectF(sm_x - 18.0 * sc, hat_y - 12.0 * sc, 36.0 * sc, 5.5 * sc))
                # Snow dusting on hat brim
                p.setBrush(QColor(242, 249, 255, int(round(210 * f_hat))))
                p.drawRoundedRect(QRectF(sm_x - 16.0 * sc, hat_y - 33.5 * sc, 32.0 * sc, 4.0 * sc), 2.0 * sc, 2.0 * sc)

            p.restore()
