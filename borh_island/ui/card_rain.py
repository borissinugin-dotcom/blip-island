"""Ultra-fast 3D crystal rainwater droplets & prominent wet wakes sliding down UI cards during rain."""

from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractScrollArea, QFrame, QWidget

_PEN_SOLID = Qt.PenStyle.SolidLine
_PEN_ROUND = Qt.PenCapStyle.RoundCap
_PEN_NONE = Qt.PenStyle.NoPen
_BRUSH_NONE = Qt.BrushStyle.NoBrush
_CLIP_INTERSECT = Qt.ClipOperation.IntersectClip
_ANTIALIAS = QPainter.RenderHint.Antialiasing
_SMOOTH_PIX = QPainter.RenderHint.SmoothPixmapTransform
_FMT_PREMULT = QImage.Format.Format_ARGB32_Premultiplied

# Pre-cached QPen palette tiers for ultra-fast batched wet trail rendering
_TRAIL_PENS: dict[tuple[int, int, int, int], QPen] = {}

# Global shared crystal 3D droplet QPixmap cache: (rx_px, ry_px, scale_q, flash_q) -> (QPixmap, cx_loc_dip, cy_loc_dip)
_DROP_PIXMAP_CACHE: dict[tuple[int, int, int, int], tuple[QPixmap, float, float]] = {}


def _get_trail_pen(r: int, g: int, b: int, alpha: int, width: float) -> QPen:
    w_q = max(1, int(round(width * 10.0)))
    a_q = max(4, min(255, int(alpha)))
    key = (r, g, b, (a_q << 12) | w_q)
    pen = _TRAIL_PENS.get(key)
    if pen is None:
        if len(_TRAIL_PENS) >= 160:
            _TRAIL_PENS.clear()
        pen = QPen(QColor(r, g, b, a_q), w_q / 10.0, _PEN_SOLID, _PEN_ROUND)
        _TRAIL_PENS[key] = pen
    return pen


def is_rain_weather(wkind: str, weather_symbol: str = "", precip_kind: str = "") -> bool:
    """Return True strictly for rainy weather conditions (rain, showers, drizzle, thunder, lightning/storm)."""
    wk = (wkind or "").strip().lower()
    if wk in ("rain", "thunder", "lightning"):
        return True
    if wk in ("clear", "snow", "fog"):
        return False
    sym = (weather_symbol or "").strip().lower()
    if any(k in sym for k in ("rain", "drizzle", "shower", "thunder", "lightning")):
        return True
    if (precip_kind or "").strip().lower() == "rain" and wk in ("", "partly", "cloudy", "auto"):
        return True
    return False


def _get_crystal_drop_pixmap(rx_d: float, ry_d: float, scale: float, flash_hi: float) -> tuple[QPixmap, float, float]:
    """Fetch or bake once a translucent 3D crystal water bead QPixmap (zero per-frame NumPy or pixel distortion)."""
    rx_i = max(2, min(18, int(round(rx_d * scale))))
    ry_i = max(2, min(24, int(round(ry_d * scale))))
    scale_q = int(round(scale * 100.0))
    flash_q = int(round(flash_hi * 3.0)) if flash_hi > 0.12 else 0
    key = (rx_i, ry_i, scale_q, flash_q)
    cached = _DROP_PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_DROP_PIXMAP_CACHE) >= 160:
        _DROP_PIXMAP_CACHE.clear()

    pad_l = max(2, int(round(rx_i * 0.28)))
    pad_r = max(3, int(round(rx_i * 0.50)))
    pad_t = max(2, int(round(ry_i * 0.22)))
    pad_b = max(3, int(round(ry_i * 0.68)))

    bw = rx_i * 2 + 1 + pad_l + pad_r
    bh = ry_i * 2 + 1 + pad_t + pad_b
    cx_loc = rx_i + pad_l
    cy_loc = ry_i + pad_t

    yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
    u = (xx - float(cx_loc)) / max(1.4, float(rx_i) - 0.1)
    v = (yy - float(cy_loc)) / max(1.4, float(ry_i) - 0.1)

    # Natural teardrop dome contour (gently tapered top, softly heavier bottom belly)
    upper = np.clip(-v, 0.0, 1.0)
    lower = np.clip((v - 0.15) / 0.85, 0.0, 1.0)
    shape_w = (1.0 - 0.21 * (upper ** 1.45)) * (1.0 + 0.06 * lower)
    ux = u / np.maximum(0.45, shape_w)
    r2 = ux * ux + v * v
    r = np.sqrt(np.clip(r2, 0.0, 6.0))

    edge_scale = max(1.75, float(min(rx_i, ry_i)) * 0.95)
    alpha_bead = np.where(
        r < 1.08,
        np.clip((1.0 - r) * edge_scale + 0.42, 0.0, 1.0),
        0.0,
    ).astype(np.float32)

    # Soft directional 3D drop shadow + transmitted lens focus glow beneath lower rim
    sh_u = (xx - float(cx_loc)) / max(2.0, float(rx_i) * 1.18) - 0.18
    sh_v = (yy - float(cy_loc)) / max(2.0, float(ry_i) * 1.16) - 0.34
    sh_r2 = sh_u * sh_u + sh_v * sh_v
    sh_cov = np.clip(np.exp(-np.maximum(0.0, sh_r2 - 0.30) * 2.55), 0.0, 1.0)
    dir_w = np.clip(0.32 + 0.68 * np.clip(v * 0.72 + u * 0.25 + 0.35, 0.0, 1.0), 0.0, 1.0)
    sh_alpha = np.where(r < 0.72, 0.0, sh_cov * dir_w * 0.44).astype(np.float32)

    # Focused transmitted caustic spot inside bottom shadow
    caust_spot = np.exp(-((u - 0.08) ** 2 / 0.26 + (v - 1.10) ** 2 / 0.16)) * (1.0 - alpha_bead) * (0.20 + 0.08 * (flash_q / 3.0))
    sh_a_tot = np.clip(sh_alpha + caust_spot, 0.0, 0.58)
    spot_val = caust_spot * 238.0
    shadow_bgra = np.stack(
        (spot_val * 1.06, spot_val * 1.00, spot_val * 0.92, sh_a_tot * 255.0),
        axis=-1,
    ).astype(np.float32)

    # Crisp 3D Fresnel edges + brighter bottom caustic meniscus + sharp specular catchlight
    rim_t = np.clip((r - 0.62) / 0.38, 0.0, 1.0)
    upper_arc = np.clip(0.88 - 0.64 * v - 0.18 * u, 0.0, 1.0)
    dark_rim_a = (rim_t ** 1.30) * upper_arc * 0.58

    low_right = np.clip((u * 0.42 + v * 0.92 - 0.08) / 0.92, 0.0, 1.0)
    bright_rim = np.exp(-((r - 0.89) ** 2) / 0.009) * (low_right ** 1.25) * 0.68

    # Bottom internal caustic meniscus arc (gives palpable 3D liquid dome bulge)
    bot_t = np.clip((v - 0.08) / 0.92, 0.0, 1.0)
    caust_arc = np.clip((r - 0.50) / 0.40, 0.0, 1.0) * (1.0 - np.clip((r - 0.94) / 0.08, 0.0, 1.0))
    caust = caust_arc * (bot_t ** 1.32) * np.exp(-ux * ux * 1.25) * (0.44 + 0.24 * (flash_q / 3.0))

    # Glossy upper-left sky catchlight + bottom-right bounce spark
    dx_hi = (u + 0.34) * 0.85 - (v + 0.35) * 0.52
    dy_hi = (u + 0.34) * 0.52 + (v + 0.35) * 0.85
    sig_hi = max(0.038, 0.125 / max(1.0, float(rx_i) * 0.25))
    spec_main = np.exp(-(dx_hi * dx_hi * 0.65 + dy_hi * dy_hi * 2.1) / sig_hi) * 0.98
    spec_halo = 0.24 * np.exp(-((u + 0.30) ** 2 + (v + 0.31) ** 2) / (sig_hi * 2.2))
    spec_bounce = 0.62 * np.exp(-((u - 0.26) ** 2 + (v - 0.44) ** 2) / (sig_hi * 1.10))

    spec_hi = np.clip(spec_main + spec_halo + spec_bounce + bright_rim + caust, 0.0, 1.0)

    # Combine transparent interior tint (~11%) + dark Fresnel edge + bright highlights/caustics
    tint_a = 0.10 + 0.06 * (r ** 2)
    bead_a_norm = np.clip(np.maximum(tint_a, np.maximum(dark_rim_a, spec_hi)), 0.0, 1.0)
    # Dark rim pulls RGB toward cool deep slate; speculars/caustic pull toward crisp ice-white (BGR)
    light_w = spec_hi / np.maximum(1e-4, bead_a_norm)
    tint_w = np.maximum(0.0, (tint_a - dark_rim_a * 0.65)) / np.maximum(1e-4, bead_a_norm)
    # Premultiplied bead BGRA inside dome
    bead_b = np.clip(light_w * 255.0 + tint_w * 195.0, 0.0, 255.0) * bead_a_norm
    bead_g = np.clip(light_w * 252.0 + tint_w * 172.0, 0.0, 255.0) * bead_a_norm
    bead_r = np.clip(light_w * 246.0 + tint_w * 145.0, 0.0, 255.0) * bead_a_norm
    bead_bgra = np.stack((bead_b, bead_g, bead_r, bead_a_norm * 255.0), axis=-1)

    aa = alpha_bead[..., None]
    inv_aa = 1.0 - aa
    comp = bead_bgra * aa + shadow_bgra * inv_aa
    # Strictly uphold premultiplied RGB <= Alpha
    comp[..., :3] = np.minimum(comp[..., :3], comp[..., 3:4])
    u8 = np.clip(comp, 0.0, 255.0).astype(np.uint8)

    img = QImage(bw, bh, _FMT_PREMULT)
    ptr = img.bits()
    ptr.setsize(img.sizeInBytes())
    np.frombuffer(ptr, dtype=np.uint8).reshape((bh, bw, 4))[:] = u8
    img.setDevicePixelRatio(scale)
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(scale)

    res = (pm, float(cx_loc) / scale, float(cy_loc) / scale)
    _DROP_PIXMAP_CACHE[key] = res
    return res


@dataclass
class _MicroBead:
    x: float
    y: float
    r: float
    birth_t: float
    life: float


@dataclass
class _SlidingDrop:
    x: float
    y: float
    r: float
    vy: float
    target_vy: float
    wiggle_phase: float
    wiggle_freq: float
    pause_until: float
    next_pause_t: float
    edge_stall_t: float
    life_alpha: float
    trail: deque = field(default_factory=lambda: deque(maxlen=42))
    last_trail_y: float = -999.0
    next_pinch_dist: float = 11.0


@dataclass
class _CardDropField:
    w: float
    h: float
    drops: list[_SlidingDrop] = field(default_factory=list)
    micro_beads: list[_MicroBead] = field(default_factory=list)
    last_seen_t: float = 0.0


class CardRainSystem:
    """Ultra-fast physics & cached 3D crystal water droplet renderer with prominent wet wakes."""

    def __init__(self) -> None:
        self._rng = random.Random(202611)
        self._cards: dict[int, _CardDropField] = {}
        self._intensity: float = 1.0
        self._stormy: bool = False
        self._active: bool = False
        self._last_step_t: float = 0.0

    @property
    def is_active(self) -> bool:
        return self._active or bool(self._cards)

    def clear(self) -> None:
        self._cards.clear()
        self._active = False

    def set_params(self, *, active: bool, intensity: float = 1.0, stormy: bool = False) -> None:
        self._active = bool(active)
        self._intensity = max(0.0, min(1.5, float(intensity)))
        self._stormy = bool(stormy)

    def _spawn_drop(self, field_obj: _CardDropField, now: float, *, top_biased: bool = True) -> _SlidingDrop:
        cw, ch = field_obj.w, field_obj.h
        min_dim = min(cw, ch)
        # Small crystal droplets (2.8..4.8 px radius -> ~6-10 px diameter)
        r_hi = min(4.8 if self._stormy else 4.4, max(3.0, min_dim * 0.048))
        r_lo = min(2.8, max(2.2, r_hi * 0.62))
        margin = max(6.5, r_hi + 2.0)
        x = self._rng.uniform(margin, max(margin + 2.0, cw - margin))
        y = (
            self._rng.uniform(4.0, max(7.0, ch * 0.34))
            if top_biased
            else self._rng.uniform(6.0, max(10.0, ch * 0.72))
        )
        r = self._rng.uniform(r_lo, r_hi)
        base_spd = (16.5 + r * 3.6) * (1.22 if self._stormy else 1.0)
        vy = base_spd * self._rng.uniform(0.76, 1.28)
        d = _SlidingDrop(
            x=x,
            y=y,
            r=r,
            vy=vy * 0.52,
            target_vy=vy,
            wiggle_phase=self._rng.uniform(0.0, math.tau),
            wiggle_freq=self._rng.uniform(3.4, 7.0),
            pause_until=0.0,
            next_pause_t=now + self._rng.uniform(0.45, 1.50),
            edge_stall_t=0.0,
            life_alpha=0.05,
            last_trail_y=y,
            next_pinch_dist=self._rng.uniform(7.5, 14.5),
        )
        d.trail.append((x, y, now, max(0.85, r * 0.38)))
        return d

    def _spawn_micro_bead(self, field_obj: _CardDropField, now: float) -> _MicroBead:
        cw, ch = field_obj.w, field_obj.h
        r_hi = min(2.3, max(1.5, min(cw, ch) * 0.024))
        margin = max(5.0, r_hi + 1.5)
        return _MicroBead(
            x=self._rng.uniform(margin, max(margin + 2.0, cw - margin)),
            y=self._rng.uniform(4.0, max(7.0, ch - 5.0)),
            r=self._rng.uniform(1.05, r_hi),
            birth_t=now,
            life=self._rng.uniform(4.5, 9.0),
        )

    def _sync_card_targets(self, cards: list[tuple[int, QRectF, float, QRectF | None, object]], now: float) -> None:
        seen_ids: set[int] = set()
        for item in cards:
            cid, cr = item[0], item[1]
            seen_ids.add(cid)
            cw, ch = cr.width(), cr.height()
            if cw < 28.0 or ch < 28.0:
                continue
            fobj = self._cards.get(cid)
            if fobj is None:
                fobj = _CardDropField(w=cw, h=ch, last_seen_t=now)
                if self._active:
                    area = cw * ch
                    n_init_drops = max(1, min(5, int(round((area / 25000.0) * (2.5 if self._stormy else 2.0)))))
                    n_init_beads = max(2, min(7, int(round((area / 16000.0) * (2.4 if self._stormy else 1.9)))))
                    for _ in range(n_init_drops):
                        sd = self._spawn_drop(fobj, now, top_biased=False)
                        sd.life_alpha = self._rng.uniform(0.55, 1.0)
                        fobj.drops.append(sd)
                    for _ in range(n_init_beads):
                        fobj.micro_beads.append(self._spawn_micro_bead(fobj, now))
                self._cards[cid] = fobj
            else:
                if abs(fobj.w - cw) > 2.0 or abs(fobj.h - ch) > 2.0:
                    sx = cw / max(1.0, fobj.w)
                    sy = ch / max(1.0, fobj.h)
                    for d in fobj.drops:
                        d.x = max(5.0, min(cw - 5.0, d.x * sx))
                        d.y = max(4.0, min(ch - 4.0, d.y * sy))
                        d.last_trail_y = d.y
                        d.trail = deque(
                            ((tx * sx, ty * sy, bt, hw) for tx, ty, bt, hw in d.trail),
                            maxlen=d.trail.maxlen,
                        )
                    for mb in fobj.micro_beads:
                        mb.x = max(4.0, min(cw - 4.0, mb.x * sx))
                        mb.y = max(4.0, min(ch - 4.0, mb.y * sy))
                    fobj.w = cw
                    fobj.h = ch
                fobj.last_seen_t = now

        stale = [cid for cid, fobj in self._cards.items() if cid not in seen_ids and (now - fobj.last_seen_t) > 5.0]
        for cid in stale:
            self._cards.pop(cid, None)

    def step(
        self,
        cards: list[tuple[int, QRectF, float, QRectF | None, object]] | list[tuple[int, QRectF, float, QRectF | None]],
        now: float | None = None,
    ) -> bool:
        """Advance sliding water droplets, longer wet trails & evaporation; returns True while visible beads remain."""
        t_now = time.monotonic() if now is None else float(now)
        dt = 0.036 if self._last_step_t <= 0.0 else max(0.005, min(0.08, t_now - self._last_step_t))
        self._last_step_t = t_now

        self._sync_card_targets(cards, t_now)
        if not self._active and not self._cards:
            return False

        trail_max_age = 5.4
        any_visible = False

        for item in cards:
            _cid = item[0]
            fobj = self._cards.get(_cid)
            if fobj is None:
                continue
            cw, ch = fobj.w, fobj.h
            area = cw * ch
            want_drops = max(1, min(5, int(round((area / 24500.0) * (2.6 if self._stormy else 2.0) * self._intensity)))) if self._active else 0
            want_beads = max(2, min(7, int(round((area / 15500.0) * (2.4 if self._stormy else 1.9) * self._intensity)))) if self._active else 0

            if self._active and len(fobj.drops) < want_drops and self._rng.random() < (0.26 if self._stormy else 0.16):
                fobj.drops.append(self._spawn_drop(fobj, t_now, top_biased=True))
            if self._active and len(fobj.micro_beads) < want_beads and self._rng.random() < 0.28:
                fobj.micro_beads.append(self._spawn_micro_bead(fobj, t_now))

            kept_beads: list[_MicroBead] = []
            for mb in fobj.micro_beads:
                age = t_now - mb.birth_t
                if not self._active:
                    mb.life = min(mb.life, age + 0.9)
                if age < mb.life and 2.0 <= mb.x <= cw - 2.0 and 2.0 <= mb.y <= ch - 2.0:
                    kept_beads.append(mb)
            fobj.micro_beads = kept_beads

            kept_drops: list[_SlidingDrop] = []
            for d in fobj.drops:
                if self._active:
                    d.life_alpha = min(1.0, d.life_alpha + dt * 3.6)
                else:
                    d.life_alpha = max(0.0, d.life_alpha - dt * 1.45)

                if t_now < d.pause_until:
                    eff_vy = d.vy * 0.05
                else:
                    if t_now >= d.next_pause_t and d.y < ch - 18.0:
                        if self._rng.random() < 0.54:
                            d.pause_until = t_now + self._rng.uniform(0.14, 0.48)
                        d.next_pause_t = t_now + self._rng.uniform(0.45, 1.45)
                    d.vy += (d.target_vy - d.vy) * min(1.0, dt * 4.5)
                    eff_vy = d.vy

                if d.y >= ch - 8.5:
                    if d.edge_stall_t <= 0.0:
                        d.edge_stall_t = t_now
                    eff_vy *= 0.15
                    if (t_now - d.edge_stall_t) > 0.28:
                        d.life_alpha = max(0.0, d.life_alpha - dt * 4.4)

                dy = eff_vy * dt
                if dy > 0.01:
                    d.y += dy
                    wobble = math.sin(d.y * 0.11 + d.wiggle_phase) * 0.55 + math.cos(t_now * d.wiggle_freq + d.wiggle_phase) * 0.35
                    pad_x = max(4.5, d.r * 0.72)
                    nx_d = d.x + wobble * min(1.0, dy * 0.42) * 0.40
                    if nx_d <= pad_x:
                        d.x = min(cw - pad_x, pad_x + (pad_x - nx_d) * 0.8)
                        d.wiggle_phase += math.pi
                    elif nx_d >= cw - pad_x:
                        d.x = max(pad_x, (cw - pad_x) - (nx_d - (cw - pad_x)) * 0.8)
                        d.wiggle_phase += math.pi
                    else:
                        d.x = nx_d

                if fobj.micro_beads and dy > 0.01:
                    eat_r2 = (d.r + 2.8) ** 2
                    rem_beads: list[_MicroBead] = []
                    for mb in fobj.micro_beads:
                        if ((mb.x - d.x) ** 2 + (mb.y - d.y) ** 2) <= eat_r2:
                            d.r = min(5.8, math.hypot(d.r, mb.r * 0.34))
                            d.vy = max(d.vy, d.target_vy * 1.45)
                            d.pause_until = 0.0
                        else:
                            rem_beads.append(mb)
                    fobj.micro_beads = rem_beads

                if abs(d.y - d.last_trail_y) >= 2.0:
                    d.last_trail_y = d.y
                    d.trail.append((d.x, d.y, t_now, max(0.85, d.r * 0.38)))
                    d.next_pinch_dist -= dy
                    if d.next_pinch_dist <= 0.0 and len(fobj.micro_beads) < want_beads + 6 and self._active:
                        d.next_pinch_dist = self._rng.uniform(8.5, 16.0)
                        fobj.micro_beads.append(
                            _MicroBead(
                                x=d.x + self._rng.uniform(-0.45, 0.45),
                                y=max(4.0, d.y - d.r * 1.15),
                                r=self._rng.uniform(1.15, 2.05),
                                birth_t=t_now,
                                life=self._rng.uniform(2.6, 4.4),
                            )
                        )

                while d.trail and (t_now - d.trail[0][2]) > trail_max_age:
                    d.trail.popleft()

                alive = (d.life_alpha > 0.03) and (d.y <= ch + 4.0) and (len(d.trail) > 0 or self._active)
                if alive:
                    kept_drops.append(d)
                elif self._active:
                    kept_drops.append(self._spawn_drop(fobj, t_now, top_biased=True))

            fobj.drops = kept_drops
            if fobj.drops or fobj.micro_beads:
                any_visible = True

        if not self._active and not any_visible:
            self._cards.clear()
        return any_visible

    def paint(
        self,
        p: QPainter,
        width_dip: int,
        height_dip: int,
        dpr: float,
        cards: list[tuple[int, QRectF, float, QRectF | None, QPainterPath | None]] | list[tuple[int, QRectF, float, QRectF | None]],
        _unused_under=None,
        *,
        lightning_flash: float = 0.0,
        portal_rect: QRectF | None = None,
        under_epoch: int = 0,
        master_opacity: float = 1.0,
    ) -> None:
        """Render prominent batched wet wakes & globally cached 3D crystal QPixmap droplets."""
        del _unused_under, under_epoch
        if width_dip < 8 or height_dip < 8 or not cards or master_opacity <= 0.02:
            return
        t_now = time.monotonic()
        scale = max(1.0, float(dpr))
        flash_hi = max(0.0, min(1.0, float(lightning_flash)))
        spec_flash = int(round(44 * flash_hi))

        p.save()
        p.setRenderHint(_ANTIALIAS, True)
        p.setRenderHint(_SMOOTH_PIX, True)

        trail_max_age = 5.4
        tier_bounds = (0.34, 0.70)
        p_draw_pm = p.drawPixmap
        p_set_op = p.setOpacity
        p_draw_path = p.drawPath
        p_set_pen = p.setPen

        for item in cards:
            cid, cr, rad, clip_r = item[0], item[1], item[2], item[3]
            pre_clip_path = item[4] if len(item) >= 5 else None
            fobj = self._cards.get(cid)
            if fobj is None or (not fobj.drops and not fobj.micro_beads):
                continue
            if portal_rect is not None and portal_rect.contains(cr.center()):
                continue

            p.save()
            if pre_clip_path is not None:
                p.setClipPath(pre_clip_path)
            else:
                path = QPainterPath()
                path.addRoundedRect(cr, rad, rad)
                if clip_r is not None:
                    p.setClipRect(clip_r)
                    p.setClipPath(path, _CLIP_INTERSECT)
                else:
                    p.setClipPath(path)

            ox, oy = cr.left(), cr.top()

            # ------------------------------------------------------------------
            # Pass 1: 2-tier batched glistening wet wakes (optimized for 120 FPS)
            # ------------------------------------------------------------------
            body_paths: list[QPainterPath | None] = [None, None]
            tier_counts = [0, 0]
            tier_w_sum = [0.0, 0.0]

            for d in fobj.drops:
                pts = d.trail
                n_pts = len(pts)
                if n_pts < 2:
                    continue
                la = max(0.28, d.life_alpha)
                prev_tier = -1
                for idx in range(n_pts - 1):
                    x0_l, y0_l, bt0, hw0 = pts[idx]
                    x1_l, y1_l, _bt1, hw1 = pts[idx + 1]
                    age = t_now - bt0
                    if age >= trail_max_age:
                        continue
                    fade = ((1.0 - age / trail_max_age) ** 1.18) * la
                    if fade <= 0.04:
                        continue
                    ti = 0 if fade < 0.48 else 1
                    w_tr = max(0.95, (hw0 + hw1) * 1.15 * fade + 0.55)
                    tier_w_sum[ti] += w_tr
                    tier_counts[ti] += 1

                    bp = body_paths[ti]
                    if bp is None:
                        bp = QPainterPath()
                        body_paths[ti] = bp
                    if prev_tier != ti:
                        bp.moveTo(ox + x0_l, oy + y0_l)
                    bp.lineTo(ox + x1_l, oy + y1_l)
                    prev_tier = ti

            if any(tier_counts):
                p.setBrush(_BRUSH_NONE)
                p_set_op(master_opacity)
                tier_fades = (0.34, 0.88)
                for ti in range(2):
                    bp = body_paths[ti]
                    if bp is None or tier_counts[ti] == 0:
                        continue
                    f_t = tier_fades[ti]
                    w_t = tier_w_sum[ti] / tier_counts[ti]
                    p_set_pen(_get_trail_pen(168, 216, 255, int(round((40 + spec_flash * 0.55) * f_t)), max(0.90, w_t * 0.90)))
                    p_draw_path(bp)
                    p_set_pen(_get_trail_pen(244, 251, 255, int(round((152 + spec_flash) * f_t)), max(0.50, w_t * 0.36)))
                    p_draw_path(bp)

            # ------------------------------------------------------------------
            # Pass 2: Pre-cached crystal micro-beads along condensation & wakes
            # ------------------------------------------------------------------
            for mb in fobj.micro_beads:
                age = t_now - mb.birth_t
                if age >= mb.life:
                    continue
                fade = min(1.0, age * 4.0) * min(1.0, (mb.life - age) * 1.4) * master_opacity
                if fade <= 0.04:
                    continue
                pm, cx_off, cy_off = _get_crystal_drop_pixmap(mb.r, mb.r * 1.08, scale, 0.0)
                p_set_op(fade)
                p_draw_pm(QPointF(ox + mb.x - cx_off, oy + mb.y - cy_off), pm)

            # ------------------------------------------------------------------
            # Pass 3: Pre-cached 3D volumetric sliding drops (zero NumPy per frame)
            # ------------------------------------------------------------------
            for d in fobj.drops:
                if d.life_alpha <= 0.03:
                    continue
                rx = d.r
                ry = d.r * (1.24 if t_now >= d.pause_until else 1.09)
                pm, cx_off, cy_off = _get_crystal_drop_pixmap(rx, ry, scale, flash_hi)
                p_set_op(min(1.0, d.life_alpha * master_opacity))
                p_draw_pm(QPointF(ox + d.x - cx_off, oy + d.y - cy_off), pm)

            p.restore()

        p.restore()


class CardRainOverlay(QWidget):
    """Click-through ultra-fast volumetric rainwater droplet overlay across all UI cards in ExpandedPanel."""

    def __init__(self, stack_widget: QWidget, panel: QWidget) -> None:
        super().__init__(panel)
        self._stack = stack_widget
        self._panel = panel
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._rain = CardRainSystem()
        self._lightning_flash: float = 0.0
        self._geo_cache = None
        self._suspended: bool = False
        self._reveal_start_t: float = 0.0

        if stack_widget is not None:
            stack_widget.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PyQt6.QtCore import QEvent

        if obj is self._stack and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self.sync_geometry()
        return super().eventFilter(obj, event)

    def set_backdrop_provider(self, _cb) -> None:
        """Retained for API compatibility; lightweight crystal QPixmap sprites require no pixel grab."""
        return None

    def set_suspended(self, suspended: bool) -> None:
        """Pause simulation & hide overlay cleanly during open/close window morphs so droplets never shift horizontally."""
        want = bool(suspended)
        if want == self._suspended:
            if want and self.isVisible():
                self.hide()
            return
        self._suspended = want
        self._geo_cache = None
        if want:
            if self.isVisible():
                self.hide()
        else:
            self._reveal_start_t = time.monotonic()
            self.sync_geometry()

    def mark_underlay_dirty(self, *, immediate: bool = False) -> None:
        """Invalidate cached card geometry when scrolling, switching tabs, or relayouting."""
        del immediate
        self._geo_cache = None

    def sync_geometry(self) -> None:
        if self._stack is None:
            return
        g = self._stack.geometry()
        if g.isValid() and self.geometry() != g:
            self.setGeometry(g)
            self._geo_cache = None
        self.raise_()

    def set_rain_state(self, active: bool, *, intensity: float = 1.0, stormy: bool = False, lightning_flash: float = 0.0) -> None:
        self._lightning_flash = max(0.0, min(1.0, float(lightning_flash)))
        self._rain.set_params(active=active, intensity=intensity, stormy=stormy)
        if self._suspended:
            if self.isVisible():
                self.hide()
            return
        if active:
            self.sync_geometry()
            pro_page = getattr(self._panel, "pro_page", None)
            if pro_page is not None and pro_page.isVisible():
                self.hide()
                return
            if not self.isVisible():
                self.show()
                self.raise_()

    def step_frame(self, now: float | None = None) -> bool:
        """Advance card droplets and schedule repaint; returns False once rain ends or while suspended."""
        if self._suspended:
            if self.isVisible():
                self.hide()
            return False
        t_now = time.monotonic() if now is None else float(now)
        pro_page = getattr(self._panel, "pro_page", None)
        if pro_page is not None and pro_page.isVisible():
            if self.isVisible():
                self.hide()
            return False

        cards = self._collect_visible_cards()
        if not cards and not self._rain.is_active:
            if self.isVisible():
                self.hide()
            return False

        still_alive = self._rain.step(cards, t_now)
        if still_alive:
            if not self.isVisible():
                self.sync_geometry()
                self.show()
                self.raise_()
            self.update()
        else:
            if self.isVisible():
                self.hide()
        return still_alive

    def _collect_visible_cards(self) -> list[tuple[int, QRectF, float, QRectF | None, QPainterPath | None]]:
        self.sync_geometry()
        stack = self._stack
        if stack is None or not stack.isVisible() or stack.width() < 240:
            return []
        slide_overlay = getattr(stack, "_overlay", None)
        if slide_overlay is not None and slide_overlay.isVisible():
            return []
        page = stack.currentWidget() if hasattr(stack, "currentWidget") else None
        if page is None or not page.isVisible():
            return []

        scroll_sig = 0
        for sa in getattr(self._panel, "_smooth_scrollers", ()):
            sb = sa[0].verticalScrollBar()
            if sb is not None:
                scroll_sig = (scroll_sig * 31) ^ sb.value()

        now_g = time.monotonic()
        g_key = (id(page), self.width(), self.height(), scroll_sig)
        cached = self._geo_cache
        if cached is not None and cached[0] == g_key and (now_g - cached[1]) < 2.5:
            return cached[2]

        def _is_visible_in_page(w: QWidget) -> bool:
            cur: QWidget | None = w
            while cur is not None and cur is not page:
                if cur.isHidden():
                    return False
                cur = cur.parentWidget()
            return True

        def _has_card_ancestor(w: QWidget) -> bool:
            par = w.parentWidget()
            while par is not None and par is not page:
                if par.objectName() == "Card":
                    return True
                par = par.parentWidget()
            return False

        raw_cards: list[tuple[int, QRectF, float, QRectF | None]] = []
        ov_bounds = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        page_tl = page.mapTo(self._stack, QPoint(0, 0))
        px_off, py_off = float(page_tl.x()), float(page_tl.y())
        for fr in page.findChildren(QFrame):
            oname = fr.objectName()
            if oname not in ("Card", "Surface"):
                continue
            if oname == "Surface" and _has_card_ancestor(fr):
                continue
            if fr.width() < 28 or fr.height() < 28 or not _is_visible_in_page(fr):
                continue
            tl = fr.mapTo(page, QPoint(0, 0))
            cr = QRectF(float(tl.x()) + px_off, float(tl.y()) + py_off, float(fr.width()), float(fr.height()))
            if not ov_bounds.intersects(cr):
                continue

            clip_r: QRectF | None = ov_bounds
            par = fr.parentWidget()
            clipped_out = False
            while par is not None and par is not page and par is not self._panel:
                if isinstance(par, QAbstractScrollArea):
                    vp = par.viewport()
                    if vp is not None:
                        vtl = vp.mapTo(page, QPoint(0, 0))
                        vr = QRectF(float(vtl.x()) + px_off, float(vtl.y()) + py_off, float(vp.width()), float(vp.height()))
                        clip_r = vr.intersected(clip_r) if clip_r is not None else vr
                        if clip_r.isEmpty() or not cr.intersects(clip_r):
                            clipped_out = True
                            break
                par = par.parentWidget()
            if clipped_out:
                continue

            rad = (13.0 if cr.height() <= 130.0 else 22.0) if oname == "Card" else 16.0
            raw_cards.append((id(fr), cr, rad, clip_r))

        out: list[tuple[int, QRectF, float, QRectF | None, QPainterPath | None]] = []
        for idx_c, (cid, cr, rad, clip_r) in enumerate(raw_cards):
            cpath = QPainterPath()
            cpath.addRoundedRect(cr, rad, rad)
            for idx_o, (_, other_cr, other_rad, _) in enumerate(raw_cards):
                if idx_o != idx_c and cr.contains(other_cr.center()) and other_cr.width() < cr.width() - 4.0:
                    hole = QPainterPath()
                    hole.addRoundedRect(other_cr, other_rad, other_rad)
                    cpath = cpath.subtracted(hole)
            if clip_r is not None:
                clip_p = QPainterPath()
                clip_p.addRect(clip_r)
                cpath = cpath.intersected(clip_p)
            out.append((cid, cr, rad, clip_r, cpath))

        self._geo_cache = (g_key, now_g, out)
        return out

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if self._suspended or not self._rain.is_active:
            return
        cards = self._collect_visible_cards()
        if not cards:
            return
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))

        portal_r: QRectF | None = None
        chat_tab = getattr(self._panel, "chat_tab", None)
        if chat_tab is not None and hasattr(chat_tab, "portal_local_rect"):
            try:
                pr = chat_tab.portal_local_rect(self)
                if pr is not None and pr.isValid():
                    portal_r = QRectF(pr)
            except Exception:
                portal_r = None

        master_op = 1.0
        if self._reveal_start_t > 0.0:
            el = max(0.0, time.monotonic() - self._reveal_start_t)
            if el < 0.22:
                master_op = el / 0.22
            else:
                self._reveal_start_t = 0.0

        p = QPainter(self)
        self._rain.paint(
            p,
            self.width(),
            self.height(),
            dpr,
            cards,
            lightning_flash=self._lightning_flash,
            portal_rect=portal_r,
            master_opacity=master_op,
        )
        p.end()
