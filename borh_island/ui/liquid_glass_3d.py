"""Ultra-fast pure optical 3D Snell's Law Liquid Glass lenses & Frost Glass blur renderer."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap

# Cached precomputed 2D float32 Snell displacement offsets keyed by (bw, bh, rad_px)
_LENS_OFFSET_CACHE: dict[tuple[int, int, int], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}

# Cached flat 1D gather plans keyed by (x0, y0, bw, bh, rad_px, img_w, img_h) -> ZERO index/weight math per frame!
_GATHER_PLAN_CACHE: dict[
    tuple[int, int, int, int, int, int, int],
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
] = {}


def _get_lens_offsets(bw: int, bh: int, rad: float, bevel_px: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Precompute or fetch cached 2D Snell refraction float offsets + smooth edge chromatic dispersion."""
    key = (bw, bh, int(round(rad)))
    cached = _LENS_OFFSET_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_LENS_OFFSET_CACHE) >= 192:
        _LENS_OFFSET_CACHE.clear()

    ys, xs = np.mgrid[0:bh, 0:bw].astype(np.float32)
    cx = (bw - 1) * 0.5
    cy = (bh - 1) * 0.5
    hx = max(2.0, cx - rad)
    hy = max(2.0, cy - rad)

    dx = np.abs(xs - cx) - hx
    dy = np.abs(ys - cy) - hy
    ox = np.maximum(dx, 0.0)
    oy = np.maximum(dy, 0.0)
    out_dist = np.hypot(ox, oy)
    in_dist = np.minimum(np.maximum(dx, dy), 0.0)
    sdf = out_dist + in_dist - rad  # negative inside rounded rect

    # C1-continuous 2D SDF gradient via central differences (zero corner/edge seams)
    gy, gx = np.gradient(sdf)
    gnorm = np.hypot(gx, gy)
    inv_g = np.where(gnorm > 1e-4, 1.0 / np.maximum(gnorm, 1e-4), 0.0)
    gx *= inv_g
    gy *= inv_g

    # Quintic smootherstep C2 meniscus around rim + smooth 2D spherical dome across X and Y
    b_eff = max(6.0, min(bevel_px, min(bw, bh) * 0.48))
    u = np.clip(-sdf / b_eff, 0.0, 1.0)
    t = 1.0 - u
    edge_falloff = t * t * t * (10.0 + t * (-15.0 + 6.0 * t))
    edge_slope = edge_falloff * 1.15
    # 2D radial dome curvature in both planes so left/right/top/bottom edges curve in X & Y
    rx = (xs - cx) / max(8.0, cx)
    ry = (ys - cy) / max(8.0, cy)
    dome_x = rx * 0.18
    dome_y = ry * 0.18

    nx = gx * edge_slope + dome_x
    ny = gy * edge_slope + dome_y
    nz = np.ones_like(sdf)
    inv_n = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    nx *= inv_n
    ny *= inv_n
    nz *= inv_n

    # Pure vertical incident ray I = (0, 0, -1) refracted via Snell's Law (IOR = 1.50), no light reflection
    eta = np.float32(1.0 / 1.50)
    cos_i = nz
    k = np.maximum(0.0, 1.0 - eta * eta * (1.0 - cos_i * cos_i))
    fac = eta * cos_i - np.sqrt(k)
    tx = fac * nx
    ty = fac * ny
    tz = np.minimum(-0.24, -eta + fac * nz)
    depth = min(24.0, max(8.0, min(bw, bh) * 0.24))
    step = -depth / tz

    dx_f = (tx * step).astype(np.float32)
    dy_f = (ty * step).astype(np.float32)
    rim_taper = np.clip(-sdf / 2.5, 0.0, 1.0) * edge_falloff
    disp_x = (dx_f * (0.042 * rim_taper)).astype(np.float32)
    disp_y = (dy_f * (0.042 * rim_taper)).astype(np.float32)
    res = (dy_f, dx_f, disp_y, disp_x)
    _LENS_OFFSET_CACHE[key] = res
    return res


def _get_gather_plan(
    x0: int, y0: int, bw: int, bh: int, rad_px: float, bevel_px: float, img_w: int, img_h: int
):
    """Build or fetch cached flat 1D bilinear + dispersion gather plan for exact card placement."""
    r_key = int(round(rad_px))
    pkey = (x0, y0, bw, bh, r_key, img_w, img_h)
    plan = _GATHER_PLAN_CACHE.get(pkey)
    if plan is not None:
        return plan
    if len(_GATHER_PLAN_CACHE) >= 320:
        _GATHER_PLAN_CACHE.clear()

    off_y, off_x, disp_y, disp_x = _get_lens_offsets(bw, bh, rad_px, bevel_px=bevel_px)
    base_y = (y0 + np.arange(bh, dtype=np.float32))[:, None]
    base_x = (x0 + np.arange(bw, dtype=np.float32))[None, :]
    sy = np.clip(base_y + off_y, 0.0, float(img_h - 1.001))
    sx = np.clip(base_x + off_x, 0.0, float(img_w - 1.001))

    y_lo = sy.astype(np.int32)
    x_lo = sx.astype(np.int32)
    fy = (sy - y_lo).astype(np.float32)
    fx = (sx - x_lo).astype(np.float32)
    one = np.float32(1.0)
    y_hi = np.minimum(y_lo + 1, img_h - 1)
    x_hi = np.minimum(x_lo + 1, img_w - 1)

    w10 = np.floor((fx * (one - fy)).reshape(-1, 1) * np.float32(256.0)).astype(np.uint16)
    w01 = np.floor((((one - fx) * fy).reshape(-1, 1)) * np.float32(256.0)).astype(np.uint16)
    w11 = np.floor((fx * fy).reshape(-1, 1) * np.float32(256.0)).astype(np.uint16)
    w00 = (np.uint16(256) - w10 - w01 - w11).astype(np.uint16)

    idx00 = (y_lo * img_w + x_lo).reshape(-1)
    idx10 = (y_lo * img_w + x_hi).reshape(-1)
    idx01 = (y_hi * img_w + x_lo).reshape(-1)
    idx11 = (y_hi * img_w + x_hi).reshape(-1)

    yr = np.clip(np.rint(sy - disp_y).astype(np.int32), 0, img_h - 1)
    xr = np.clip(np.rint(sx - disp_x).astype(np.int32), 0, img_w - 1)
    yb = np.clip(np.rint(sy + disp_y).astype(np.int32), 0, img_h - 1)
    xb = np.clip(np.rint(sx + disp_x).astype(np.int32), 0, img_w - 1)
    idx_r = (yr * img_w + xr).reshape(-1)
    idx_b = (yb * img_w + xb).reshape(-1)

    plan = (idx00, idx10, idx01, idx11, w00, w10, w01, w11, idx_r, idx_b)
    _GATHER_PLAN_CACHE[pkey] = plan
    return plan


def _get_refraction_tier(quality: str) -> tuple[float, bool, bool]:
    """Return (bg_scale, use_bilinear, use_dispersion) for refraction quality: low | medium | high | ultra."""
    q = str(quality or "ultra").lower()
    if q == "low":
        return 0.40, False, False
    if q == "medium":
        return 0.46, False, True
    if q == "high":
        return 0.50, True, False
    return 0.54, True, True  # ultra (optimized for weak PCs/laptops)


def render_3d_refraction_card(
    p: QPainter,
    card_rect: QRectF,
    rad: float,
    bg_flat: np.ndarray,
    img_w: int,
    img_h: int,
    bg_scale: float = 1.0,
    clip_rect: QRectF | None = None,
    quality: str = "ultra",
    bg_u8: np.ndarray | None = None,
) -> None:
    """Render a 3D optical refracting lens with selectable refraction quality (low/medium/high/ultra)."""
    s = max(0.35, float(bg_scale))
    _, use_bilinear, use_disp = _get_refraction_tier(quality)
    x0 = max(0, int(math_floor(card_rect.left() * s)))
    y0 = max(0, int(math_floor(card_rect.top() * s)))
    x1 = min(img_w, int(round(card_rect.right() * s)))
    y1 = min(img_h, int(round(card_rect.bottom() * s)))
    bw = x1 - x0
    bh = y1 - y0
    if bw < 6 or bh < 6 or x0 >= img_w or y0 >= img_h:
        return
    draw_rect = QRectF(x0 / s, y0 / s, bw / s, bh / s)

    idx00, idx10, idx01, idx11, w00, w10, w01, w11, idx_r, idx_b = _get_gather_plan(
        x0, y0, bw, bh, max(3.0, rad * s), 30.0 * s, img_w, img_h
    )

    # Small UI buttons (<7500 scaled px) gain 3-4x speed with direct gather from pre-smoothed bg_u8
    if use_bilinear and (bw * bh) >= 7500:
        bg_u16 = bg_flat if bg_flat.dtype == np.uint16 else bg_flat.astype(np.uint16)
        samp_u16 = bg_u16[idx00]
        samp_u16 *= w00
        tmp_u16 = bg_u16[idx10]
        tmp_u16 *= w10
        samp_u16 += tmp_u16
        tmp_u16 = bg_u16[idx01]
        tmp_u16 *= w01
        samp_u16 += tmp_u16
        tmp_u16 = bg_u16[idx11]
        tmp_u16 *= w11
        samp_u16 += tmp_u16
        samp_u16 >>= np.uint16(8)
        if use_disp:
            r_mix = samp_u16[:, 2]
            r_mix *= np.uint16(141)
            r_mix += bg_u16[idx_r, 2] * np.uint16(115)
            r_mix >>= np.uint16(8)
            b_mix = samp_u16[:, 0]
            b_mix *= np.uint16(141)
            b_mix += bg_u16[idx_b, 0] * np.uint16(115)
            b_mix >>= np.uint16(8)
        sampled = np.ascontiguousarray(samp_u16.astype(np.uint8).reshape((bh, bw, 4)))
    else:
        src8 = bg_u8 if bg_u8 is not None else bg_flat
        sampled_u8 = np.ascontiguousarray(src8[idx00], dtype=np.uint8)
        if use_disp:
            sampled_u8[:, 2] = src8[idx_r, 2]
            sampled_u8[:, 0] = src8[idx_b, 0]
        sampled = sampled_u8.reshape((bh, bw, 4))

    qimg = QImage(sampled.data, bw, bh, bw * 4, QImage.Format.Format_ARGB32_Premultiplied)

    cp = QPainterPath()
    cp.addRoundedRect(card_rect, rad, rad)
    p.save()
    if clip_rect is not None:
        p.setClipRect(clip_rect)
    p.setClipPath(cp, Qt.ClipOperation.IntersectClip)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage(draw_rect, qimg)
    p.restore()


def build_soft_liquid_bg_image(bg_src: QImage | QPixmap, full_w: float, quality: str = "ultra") -> tuple[QImage, float]:
    """Create a lightly softened background image at the scale matching refraction_quality."""
    scale_t, _, _ = _get_refraction_tier(quality)
    sw = max(48, int(round(full_w * scale_t)))
    sh = max(48, int(round(sw * (bg_src.height() / max(1, bg_src.width())))))
    img = bg_src if isinstance(bg_src, QImage) else bg_src.toImage()
    soft_img = (
        img.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    )
    return soft_img, float(sw) / max(1.0, float(full_w))


def run_refraction_benchmark() -> tuple[str, float]:
    """Measure CPU speed of the 3D refraction pipeline and return (recommended_quality, avg_ms)."""
    import time

    test_bg = QImage(980, 525, QImage.Format.Format_ARGB32_Premultiplied)
    test_bg.fill(QColor(30, 42, 64, 255))
    out_img = QImage(980, 525, QImage.Format.Format_ARGB32_Premultiplied)
    cards = [
        (QRectF(20, 70, 300, 140), 22.0),
        (QRectF(20, 225, 300, 140), 22.0),
        (QRectF(340, 70, 610, 295), 22.0),
    ] + [(QRectF(30 + (i % 6) * 145, 90 + (i // 6) * 44, 130, 32), 10.0) for i in range(14)]

    # Warm up cache once
    p = QPainter(out_img)
    bg_img, bg_scale = build_soft_liquid_bg_image(test_bg, 980.0, quality="ultra")
    ptr = bg_img.bits()
    ptr.setsize(bg_img.sizeInBytes())
    bg_flat = np.frombuffer(ptr, dtype=np.uint8).reshape((-1, 4)).astype(np.uint16)
    for cr, rad in cards:
        render_3d_refraction_card(p, cr, rad, bg_flat, bg_img.width(), bg_img.height(), bg_scale, quality="ultra")
    p.end()

    # Timed benchmark (4 frames)
    t0 = time.perf_counter()
    for _ in range(4):
        p = QPainter(out_img)
        bg_img, bg_scale = build_soft_liquid_bg_image(test_bg, 980.0, quality="ultra")
        ptr = bg_img.bits()
        ptr.setsize(bg_img.sizeInBytes())
        bg_flat = np.frombuffer(ptr, dtype=np.uint8).reshape((-1, 4)).astype(np.uint16)
        for cr, rad in cards:
            render_3d_refraction_card(p, cr, rad, bg_flat, bg_img.width(), bg_img.height(), bg_scale, quality="ultra")
        p.end()
    avg_ms = (time.perf_counter() - t0) / 4.0 * 1000.0

    if avg_ms <= 14.0:
        rec = "ultra"
    elif avg_ms <= 24.0:
        rec = "high"
    elif avg_ms <= 36.0:
        rec = "medium"
    else:
        rec = "low"
    return rec, round(avg_ms, 1)


def build_frost_bg_image(bg_src: QImage | QPixmap, quality: str = "ultra") -> QImage:
    """Create a smooth, shimmer-free blurred background QImage for Frost Glass tied to glass quality."""
    q = str(quality or "ultra").lower()
    if q == "low":
        scale_f, passes = 0.12, 1
    elif q == "medium":
        scale_f, passes = 0.14, 2
    elif q == "high":
        scale_f, passes = 0.16, 2
    else:
        scale_f, passes = 0.18, 2

    sw = max(24, int(round(bg_src.width() * scale_f)))
    sh = max(24, int(round(bg_src.height() * scale_f)))
    img = bg_src if isinstance(bg_src, QImage) else bg_src.toImage()
    # SmoothTransformation prevents thin swaying grass blades from aliasing/shimmering
    small = (
        img.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    )
    ptr = small.bits()
    ptr.setsize(small.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((sh, sw, 4)).astype(np.uint16)

    for _ in range(passes):
        padded_x = np.pad(arr, ((0, 0), (1, 1), (0, 0)), mode="edge")
        arr = (padded_x[:, :-2] + padded_x[:, 1:-1] + padded_x[:, 2:]) // 3
        padded_y = np.pad(arr, ((1, 1), (0, 0), (0, 0)), mode="edge")
        arr = (padded_y[:-2, :] + padded_y[1:-1, :] + padded_y[2:, :]) // 3

    # Pre-bake subtle translucent white frost veil (+16/255 white) directly in NumPy pass (eliminates 2nd drawPath pass)
    arr = ((arr * 239) + (255 * 16)) >> 8
    out_img = QImage(sw, sh, QImage.Format.Format_ARGB32_Premultiplied)
    optr = out_img.bits()
    optr.setsize(out_img.sizeInBytes())
    np.frombuffer(optr, dtype=np.uint8).reshape((sh, sw, 4))[:] = np.clip(arr, 0, 255).astype(np.uint8)
    return out_img


def render_frost_card(
    p: QPainter,
    card_rect: QRectF,
    rad: float,
    frost_img: QImage,
    full_w: float,
    full_h: float,
    clip_rect: QRectF | None = None,
) -> None:
    """Render frosted blurred glass behind one button or card."""
    if full_w <= 1.0 or full_h <= 1.0 or frost_img.isNull():
        return
    sx = card_rect.left() / full_w * frost_img.width()
    sy = card_rect.top() / full_h * frost_img.height()
    sw = card_rect.width() / full_w * frost_img.width()
    sh = card_rect.height() / full_h * frost_img.height()
    src_rect = QRectF(sx, sy, sw, sh)

    cp = QPainterPath()
    cp.addRoundedRect(card_rect, rad, rad)
    p.save()
    if clip_rect is not None:
        p.setClipRect(clip_rect)
    p.setClipPath(cp, Qt.ClipOperation.IntersectClip)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage(card_rect, frost_img, src_rect)
    p.fillRect(card_rect, QColor(255, 255, 255, 16))
    p.restore()


_FROST_BRUSH_CACHE: dict[tuple[int, int, int], tuple[QPixmap, object]] = {}


def render_frost_batch(
    p: QPainter,
    targets: list[tuple[QRectF, float, QRectF | None, float]],
    frost_img: QImage,
    full_w: float,
    full_h: float,
) -> None:
    """Batch-render blurred Frost Glass across many cards/buttons via cached scaled QBrush(QPixmap) without CPU upscale spikes."""
    if not targets or full_w <= 1.0 or full_h <= 1.0 or frost_img.isNull():
        return
    from PyQt6.QtGui import QBrush, QPixmap, QTransform

    tw = max(16, int(round(full_w)))
    th = max(16, int(round(full_h)))
    ck_img = (int(frost_img.cacheKey()), tw, th)
    cached_b = _FROST_BRUSH_CACHE.get(ck_img)
    if cached_b is None:
        if len(_FROST_BRUSH_CACHE) >= 12:
            _FROST_BRUSH_CACHE.clear()
        pm_s = QPixmap.fromImage(frost_img)
        tx = QTransform()
        tx.scale(full_w / max(1.0, float(pm_s.width())), full_h / max(1.0, float(pm_s.height())))
        brush = QBrush(pm_s)
        brush.setTransform(tx)
        cached_b = (pm_s, brush)
        _FROST_BRUSH_CACHE[ck_img] = cached_b
    else:
        _, brush = cached_b

    # Group by (clip_rect_tuple, opacity_bucket) while keeping draw order cleanly
    groups: dict[tuple[tuple[float, float, float, float] | None, int], tuple[QRectF | None, float, QPainterPath, QRectF]] = {}
    for rect, rad, clip_r, op in targets:
        if rect.width() < 2.0 or rect.height() < 2.0:
            continue
        ck = (round(clip_r.x(), 1), round(clip_r.y(), 1), round(clip_r.width(), 1), round(clip_r.height(), 1)) if clip_r is not None else None
        op_b = max(0, min(100, int(round(op * 100.0))))
        gkey = (ck, op_b)
        ent = groups.get(gkey)
        if ent is None:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.WindingFill)
            path.addRoundedRect(rect, rad, rad)
            groups[gkey] = (clip_r, op_b / 100.0, path, QRectF(rect))
        else:
            _, _, path, bbox = ent
            path.addRoundedRect(rect, rad, rad)
            groups[gkey] = (ent[0], ent[1], path, bbox.united(rect))

    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(brush)
    for clip_r, op, path, bbox in groups.values():
        eff_bbox = bbox.intersected(clip_r) if clip_r is not None else bbox
        if eff_bbox.isEmpty():
            continue
        p.save()
        p.setOpacity(op)
        if clip_r is not None:
            p.setClipRect(clip_r)
        p.drawPath(path)
        p.restore()
    p.restore()


def math_floor(v: float) -> int:
    return int(v) if v >= 0.0 or v == int(v) else int(v) - 1
