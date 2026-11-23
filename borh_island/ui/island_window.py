"""Main Dynamic Island floating pill + expandable overlay window with liquid droplet physics."""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable

_OWN_PID = os.getpid()

TARGET_FPS = 120
_ACTIVE_FRAME_MS = 8  # True 120 Hz pacing for fluid physics, scrolling, and droplet cadence
_EXPANDED_MUSIC_MS = 8  # True 120 Hz smooth audio spectrum & edge waves when music plays
_EXPANDED_IDLE_MS = 16  # 60 Hz responsive idle watcher, waking immediately on mouse proximity/hover
_SAMPLE_RECT_CACHE: dict[tuple[float, float, float, float, float, int], list[tuple[float, float, float, float]]] = {}

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.config import ConfigManager
from ..core.native_bridge import NativeBridge
from ..core.win32_api import (
    SC_MAXIMIZE,
    SNIPPING_OVERLAY_EXES,
    WM_SYSCOMMAND,
    get_foreground_window_info,
    get_idle_seconds,
    get_keyboard_layout_code,
    is_caps_lock_on,
    is_left_mouse_button_down,
    is_snipping_overlay_active,
    set_exclude_from_capture,
    set_overlay_window_flags,
)
from ..services.media_service import MediaStateSnapshot
from ..services.system_service import SystemStatsSnapshot
from ..services.weather_service import WeatherReport, describe_symbol
from .atmosphere import (
    ATMO_MODES,
    AtmosphereAnimator,
    AtmosphericScenePainter,
    CloudSmokeField,
    GrassSkeletonSim,
    describe_atmosphere_badge,
    resolve_scene_state,
)
from .card_rain import is_rain_weather
from .characters import CharacterWidget
from .expanded_panel import ExpandedPanel
from .icons import get_icon, get_pixmap
from .theme import ThemePalette, resolve_palette, resolve_time_accent

# Generous transparent perimeter margin reserved for liquid droplet, side curving & music waves
_DROP_PAD_X = 165
_DROP_PAD_TOP = 26
_DROP_PAD_BOTTOM = 165

_VIDEO_PLAYER_EXES = frozenset({
    "vlc.exe", "mpc-hc64.exe", "mpc-hc.exe", "mpc-be64.exe", "mpc-be.exe",
    "mpv.exe", "mpvnet.exe", "potplayermini64.exe", "potplayermini.exe",
    "potplayer64.exe", "potplayer.exe", "kmplayer64.exe", "kmplayer.exe",
    "wmplayer.exe", "video.ui.exe", "moviesandtv.exe", "smplayer.exe",
    "plex.exe", "stremio.exe", "kodi.exe", "jellyfinmediaplayer.exe",
})
_VIDEO_BROWSER_EXES = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "opera_gx.exe",
    "brave.exe", "vivaldi.exe", "yandex.exe", "browser.exe", "arc.exe", "zen.exe",
})
_VIDEO_TITLE_HINTS = (
    "youtube", "twitch", "rutube", "vk видео", "vk video", "кинопоиск", "kinopoisk",
    "netflix", "ivi", "okko", "prime video", "vimeo", "tiktok", "reels", "shorts",
    "video", "видео", "плеер", "player", "movie", "фильм", "сериал", "anime", "аниме",
    "stream", "стрим", "смотреть", "hdrezka", "kodik",
)


def _smoothstep_01(edge0: float, edge1: float, x: float) -> float:
    if edge1 <= edge0:
        return 1.0 if x >= edge1 else 0.0
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _RoundedPillSlider(accent: str) -> str:
    return (
        "QSlider::groove:horizontal { height: 5px; border-radius: 2px; background: rgba(255,255,255,0.18); }"
        f"QSlider::sub-page:horizontal {{ height: 5px; border-radius: 2px; background: {accent}; }}"
        "QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: #FFFFFF; }"
    )


def _fmt_sec(sec: float) -> str:
    s = max(0, int(round(sec)))
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


def _rounded_thumb(pix: QPixmap, size: int = 24, radius: int = 6, dpr: float = 1.0) -> QPixmap:
    """Scale artwork at physical screen DPR with smooth filtering and antialiased rounded corners."""
    if pix.isNull():
        return pix
    scale = max(1.0, float(dpr or 1.0))
    phys = max(1, int(round(size * scale)))
    phys_rad = float(radius) * scale
    scaled = pix.scaled(
        phys,
        phys,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(phys, phys)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(phys), float(phys), phys_rad, phys_rad)
    p.setClipPath(path)
    ox = (phys - scaled.width()) // 2
    oy = (phys - scaled.height()) // 2
    p.drawPixmap(ox, oy, scaled)
    p.end()
    out.setDevicePixelRatio(scale)
    return out


def _apply_lyric_chip_style(lbl: QLabel, accent_hex: str, *, in_strip: bool = False) -> None:
    """Apply clean rounded glass-pill typography to live synced lyrics in pill and strip views."""
    c = QColor(accent_hex or "#38BDF8")
    if not c.isValid():
        c = QColor("#38BDF8")
    r, g, b = c.red(), c.green(), c.blue()
    pad = "2px 10px" if in_strip else "1px 9px"
    rad = "10px" if in_strip else "9px"
    lbl.setStyleSheet(
        f"color: #F8FAFC; background: rgba({r}, {g}, {b}, 34); "
        f"border: 1px solid rgba({r}, {g}, {b}, 88); "
        f"border-radius: {rad}; padding: {pad}; font-size: 11px; font-weight: 600;"
    )


class _CollapsedEqualizer(QWidget):
    """Tiny animated 6-bar equalizer shown on pill edge while music plays."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bands = [0.2] * 6
        self._accent = QColor(resolve_time_accent())
        self.setFixedSize(26, 18)

    def set_bands(self, bands: list[float], playing: bool) -> None:
        if playing and bands:
            step = max(1, len(bands) // 6)
            self._bands = [max(0.15, bands[i * step]) for i in range(6)]
        else:
            self._bands = [0.18] * 6
        self.update()

    def set_accent(self, hex_color: str) -> None:
        c = QColor(hex_color)
        if c.isValid():
            self._accent = c

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        h = self.height()
        for i, v in enumerate(self._bands):
            bh = max(3, int(v * (h - 2)))
            p.drawRoundedRect(i * 4 + 1, (h - bh) // 2, 2, bh, 1, 1)
        p.end()


class _GameTelemetryPill(QWidget):
    """Live inline CPU / RAM / GPU sparkline graphs displayed inside the top pill during active games."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cpu_hist: deque[float] = deque([0.0] * 18, maxlen=18)
        self._ram_hist: deque[float] = deque([0.0] * 18, maxlen=18)
        self._gpu_hist: deque[float] = deque([0.0] * 18, maxlen=18)
        self._cpu_val = 0.0
        self._ram_val = 0.0
        self._gpu_val = -1.0
        self._game_name = ""
        self._compact = False
        self._show_cpu = True
        self._show_ram = True
        self._show_gpu = True
        self.setFixedSize(238, 26)

    def _resize_for_metrics(self) -> None:
        n_vis = max(1, int(self._show_cpu) + int(self._show_ram) + int(self._show_gpu))
        if n_vis == 3:
            w = 178 if self._compact else 238
        elif n_vis == 2:
            w = 120 if self._compact else 160
        else:
            w = 64 if self._compact else 84
        if self.width() != w:
            self.setFixedSize(w, 26)

    def set_visible_metrics(self, *, cpu: bool = True, ram: bool = True, gpu: bool = True) -> None:
        c, r, g = bool(cpu), bool(ram), bool(gpu)
        if not (c or r or g):
            c = r = g = True
        if (c, r, g) == (self._show_cpu, self._show_ram, self._show_gpu):
            return
        self._show_cpu, self._show_ram, self._show_gpu = c, r, g
        self._resize_for_metrics()
        self.update()

    def set_compact(self, compact: bool) -> None:
        if self._compact == compact:
            return
        self._compact = compact
        self._resize_for_metrics()
        self.update()

    def push_sample(self, cpu_pct: float, ram_pct: float, gpu_pct: float, game_name: str = "") -> None:
        self._cpu_val = max(0.0, min(100.0, float(cpu_pct)))
        self._ram_val = max(0.0, min(100.0, float(ram_pct)))
        self._gpu_val = float(gpu_pct) if gpu_pct >= 0 else -1.0
        self._game_name = game_name
        self._cpu_hist.append(self._cpu_val)
        self._ram_hist.append(self._ram_val)
        self._gpu_hist.append(max(0.0, min(100.0, gpu_pct)) if gpu_pct >= 0 else 0.0)
        self.update()

    @staticmethod
    def _draw_spark(
        p: QPainter,
        rect: QRectF,
        label: str,
        val: float,
        hist: deque[float],
        col: QColor,
        *,
        compact: bool = False,
    ) -> None:
        p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 65), 1.0))
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(rect, 6.0, 6.0)

        txt = f"{label} {int(round(val))}%" if val >= 0 else f"{label} —"
        p.setPen(QColor("#E2E8F0"))
        font = QFont("Segoe UI")
        font.setPointSizeF(6.5 if compact else 7.2)
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRectF(rect.x() + 3, rect.y() + 1, rect.width() - 5, 11), Qt.AlignmentFlag.AlignLeft, txt)

        gx0 = rect.x() + 4
        gx1 = rect.right() - 4
        gy0 = rect.y() + 13
        gy1 = rect.bottom() - 3
        gh = max(4.0, gy1 - gy0)
        gw = max(8.0, gx1 - gx0)
        pts = list(hist)
        if len(pts) < 2:
            return
        poly = QPainterPath()
        fill = QPainterPath()
        for idx, v in enumerate(pts):
            x = gx0 + (idx / max(1, len(pts) - 1)) * gw
            y = gy1 - (max(0.0, min(100.0, v)) / 100.0) * gh
            if idx == 0:
                poly.moveTo(x, y)
                fill.moveTo(x, gy1)
                fill.lineTo(x, y)
            else:
                poly.lineTo(x, y)
                fill.lineTo(x, y)
        fill.lineTo(gx1, gy1)
        fill.closeSubpath()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 55))
        p.drawPath(fill)
        p.setPen(QPen(col, 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(poly)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        items: list[tuple[str, float, deque[float], QColor]] = []
        if self._show_cpu:
            items.append(("CPU", self._cpu_val, self._cpu_hist, QColor("#7DD3FC")))
        if self._show_ram:
            items.append(("RAM", self._ram_val, self._ram_hist, QColor("#86EFAC")))
        if self._show_gpu:
            items.append(("GPU", self._gpu_val, self._gpu_hist, QColor("#FDE68A")))
        if not items:
            items = [
                ("CPU", self._cpu_val, self._cpu_hist, QColor("#7DD3FC")),
                ("RAM", self._ram_val, self._ram_hist, QColor("#86EFAC")),
                ("GPU", self._gpu_val, self._gpu_hist, QColor("#FDE68A")),
            ]
        n = len(items)
        gap = 3.0 if self._compact else 5.0
        cw = (w - max(0, n - 1) * gap) / n
        for idx_m, (lbl, val, hist, col) in enumerate(items):
            self._draw_spark(p, QRectF(idx_m * (cw + gap), 0.0, cw, h), lbl, val, hist, col, compact=self._compact)
        p.end()


class _LaptopBatteryDot(QWidget):
    """Atoll green circular battery ring shown next to companion on laptops (hidden on desktops)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._percent: int = -1
        self._plugged: bool = False
        self.setFixedSize(20, 20)
        self.hide()

    def set_battery(self, percent: int | None, plugged: bool = False) -> bool:
        val = int(percent) if (percent is not None and int(percent) >= 0) else -1
        vis_before = self.isVisible()
        changed = (val != self._percent) or (bool(plugged) != self._plugged)
        self._percent = val
        self._plugged = bool(plugged)
        if val < 0:
            self.hide()
        else:
            self.setToolTip(f"Батарея: {val}%{' (зарядка)' if self._plugged else ''}")
            self.show()
            if changed:
                self.update()
        return vis_before != self.isVisible()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._percent < 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor("#4ADE80" if self._percent > 20 else "#F59E0B")
        rect = QRectF(2.5, 2.5, 15.0, 15.0)
        p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 55), 2.2))
        p.drawEllipse(rect)
        span_deg = int(round(-360.0 * 16.0 * max(0, min(100, self._percent)) / 100.0))
        p.setPen(QPen(col, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 90 * 16, span_deg)
        f = p.font()
        f.setPointSize(6)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#F8FAFC"))
        txt = f"{self._percent}" if self._percent < 100 else "99+"
        p.drawText(QRectF(0.0, 0.0, 20.0, 20.0), Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class IslandWindow(QWidget):
    """Top-center Dynamic Island pill with taskbar presence, liquid drop deformation, live status ticker, and panel."""

    geometry_changed = pyqtSignal(int, int)  # (center_x, bottom_y)
    wheel_volume_changed = pyqtSignal(int)
    media_prev_requested = pyqtSignal()
    media_play_pause_requested = pyqtSignal()
    media_next_requested = pyqtSignal()
    media_open_source_requested = pyqtSignal()
    media_seek_requested = pyqtSignal(float)
    music_palette_changed = pyqtSignal(object)  # ThemePalette
    _strip_volume_loaded = pyqtSignal(int, bool)

    def __init__(
        self,
        config_mgr: ConfigManager,
        bridge: NativeBridge,
        panel: ExpandedPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.bridge = bridge
        self.panel = panel
        self.palette: ThemePalette = resolve_palette(config_mgr.config.theme)
        self._expanded = False
        self._accent_hex = resolve_time_accent()
        self._playing = False
        self._has_media = False
        self._recording = False
        self._dictating = False
        self._timer_text = ""
        self._weather_text = ""
        self._weather_symbol = ""
        self._wind_ms = 3.0
        self._atmo_phase = 0.0
        self._cloud_phase = 0.0
        self._precip_kind = ""
        self._hover_active = False
        self._hidden_by_autohide = False
        self._hidden_by_fullscreen = False
        self._in_game = False
        self._game_name = ""
        self._last_wheel_t = 0.0
        self._estimated_vol = 50
        self._expand_ts = 0.0
        self._prev_lmb_down = False
        self._last_kb_layout = get_keyboard_layout_code()
        self._last_caps_on = is_caps_lock_on()
        self._last_caps_poll_ts = 0.0
        self._last_fg_poll_ts = 0.0
        self._last_ui_paint_ts = 0.0
        self._last_clock_str = datetime.now().strftime("%H:%M")
        self._last_palette_key = ""
        self._last_time_bucket = -1
        self._art_lookup: Callable[[str], QImage | None] | None = None

        # Current media display state (saved while transient activity toast is shown)
        self._media_title_full = "Blip Island"
        self._media_song_only = ""
        self._media_lyric = ""
        self._media_source_id = ""
        self._media_source_text = ""
        self._media_source_color = resolve_time_accent()
        self._media_thumb: QPixmap = QPixmap()
        self._media_art_raw: QPixmap = QPixmap()
        self._media_pos = 0.0
        self._media_dur = 0.0
        self._media_song = ""
        self._media_artist = ""
        self._music_strip_open = False
        self._strip_seeking = False
        self._strip_vol_muted = False
        self._strip_vol_dragging = False
        self._strip_vol_pending: int | None = None
        self._strip_vol_timer = QTimer(self)
        self._strip_vol_timer.setSingleShot(True)
        self._strip_vol_timer.setInterval(28)
        self._strip_vol_timer.timeout.connect(self._flush_strip_volume)
        self._strip_volume_loaded.connect(self._set_strip_volume_ui)
        self._alarm_active = False

        # Liquid droplet auto-hide / reveal state machine (sketch splash animation)
        self._hide_state = "shown"  # shown | hiding | hidden | showing
        self._hide_p = 0.0
        self._hide_last_ts = time.monotonic()
        self._hover_hide_grace_until = 0.0
        self._cached_fg_video_fs = False
        self._cached_fg_excluded = False
        self._cached_fg_screen_covered = False
        self._fg_screen_covered = False
        self._pill_morph_pixmap = QPixmap()
        self._pill_morph_rect = QRectF()

        # Transient activity banner state (clipboard copy, screenshot, keyboard layout)
        self._activity_active = False
        self._activity_badge = ""
        self._activity_text = ""
        self._activity_icon = ""
        self._activity_accent = "#38BDF8"
        self._activity_thumb: QPixmap = QPixmap()
        self._activity_cb: Callable[[], None] | None = None
        self._activity_timer = QTimer(self)
        self._activity_timer.setSingleShot(True)
        self._activity_timer.timeout.connect(self._clear_activity)

        # Liquid droplet spring-damper & bottom-edge music wave state
        self._drop_dir_x = 0.0
        self._drop_dir_y = 1.0
        self._drop_amp = 0.0
        self._drop_vel = 0.0
        self._drop_dist = 999.0
        self._prev_drop_dist = 999.0
        self._pushing_in = False
        self._indent_amp = 0.0
        self._indent_vel = 0.0
        self._indent_anchor = QPointF(0.0, 0.0)
        self._indent_nx = 0.0
        self._indent_ny = 0.0
        self._spotify_sticky_hex = ""
        self._modal_hold_count = 0
        self._last_media_sig: tuple | None = None
        self._grass_sim = GrassSkeletonSim()
        self._cloud_sim = CloudSmokeField()
        self._atmo_anim = AtmosphereAnimator()
        self._last_atmo_ts = time.monotonic()
        self._last_liquid_ts = time.monotonic()
        self._drop_wobble = 0.0
        self._drop_phase = 0.0
        self._drop_cursor_local = QPointF(-999.0, -999.0)
        self._tether_latched = False
        self._tether_anchor = QPointF(0.0, 0.0)
        self._tether_cooldown = 0.0
        self._lmb_was_down = False
        self._snap_shake_amp = 0.0
        self._snap_shake_phase = 0.0
        self._panel_shake_dx = 0
        self._panel_shake_dy = 0
        self._prev_cursor_local = QPointF(-999.0, -999.0)
        self._fluid_vx = 0.0
        self._fluid_vy = 0.0
        self._fluid_swirl = 0.0
        self._fluid_x_norm = 0.5
        self._fluid_y_norm = 0.5
        self._music_beat = 0.0
        self._music_bands: list[float] = [0.0] * 20
        self._music_energy = 0.0
        self._prev_energy = 0.0
        self._music_phase = 0.0
        self._music_vis_env = 0.0
        self._music_aggro = 0.0
        self._music_bass = 0.0

        # Tray-only frameless overlay window (hidden from Windows Taskbar; lives in system tray)
        self.setWindowTitle("Blip Island — Borh-edition")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(False)
        self.setMinimumSize(0, 0)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(_DROP_PAD_X + 10, _DROP_PAD_TOP + 2, _DROP_PAD_X + 10, _DROP_PAD_BOTTOM)
        self._root.setSpacing(2)

        # --- Pill Header Strip (always visible) ---
        self.pill_bar = QWidget()
        self.pill_bar.setFixedHeight(32)
        pb = QHBoxLayout(self.pill_bar)
        pb.setContentsMargins(2, 0, 2, 0)
        pb.setSpacing(6)

        self.character = CharacterWidget(config_mgr.config.character_id)
        self.character.setFixedSize(28, 28)
        self.character.setCursor(Qt.CursorShape.PointingHandCursor)
        self.character.setToolTip("Персонаж и настройки острова")
        self.character.poked.connect(self._on_character_poked)
        pb.addWidget(self.character)

        self.battery_dot = _LaptopBatteryDot(self.pill_bar)
        pb.addWidget(self.battery_dot)

        self.art_thumb = QLabel()
        self.art_thumb.setFixedSize(24, 24)
        self.art_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art_thumb.hide()
        pb.addWidget(self.art_thumb)

        # Inline miniplayer transport buttons positioned on the left next to album art
        self.mini_controls = QWidget()
        mc_lay = QHBoxLayout(self.mini_controls)
        mc_lay.setContentsMargins(0, 0, 0, 0)
        mc_lay.setSpacing(2)
        self.btn_prev = self._make_mini_btn("prev", "Previous Track", self.media_prev_requested.emit)
        self.btn_play = self._make_mini_btn("play", "Play / Pause", self.media_play_pause_requested.emit)
        self.btn_next = self._make_mini_btn("next", "Next Track", self.media_next_requested.emit)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            mc_lay.addWidget(b)
        self.mini_controls.hide()
        pb.addWidget(self.mini_controls)

        self.source_icon_lbl = QLabel("")
        self.source_icon_lbl.setFixedSize(16, 16)
        self.source_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_icon_lbl.hide()
        pb.addWidget(self.source_icon_lbl)

        self.source_badge = QLabel("")
        self.source_badge.setFixedHeight(19)
        self.source_badge.setStyleSheet(
            "background: rgba(59, 130, 246, 0.25); color: #F2F5FA; "
            "border-radius: 9px; padding: 0px 7px; font-size: 10px; font-weight: 700;"
        )
        self.source_badge.hide()
        pb.addWidget(self.source_badge)

        self.pill_text = QLabel(self._pill_brand_text())
        self.pill_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.pill_text.setMinimumWidth(0)
        self.pill_text.setStyleSheet("color: #F2F5FA; font-size: 12px; font-weight: 600;")
        pb.addWidget(self.pill_text, 1)

        self.pill_lyric_lbl = QLabel("")
        self.pill_lyric_lbl.setFixedHeight(21)
        self.pill_lyric_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pill_lyric_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.pill_lyric_lbl.setMinimumWidth(0)
        _apply_lyric_chip_style(self.pill_lyric_lbl, self._media_source_color, in_strip=False)
        self.pill_lyric_lbl.hide()
        pb.addWidget(self.pill_lyric_lbl, 2)

        self.game_hud = _GameTelemetryPill()
        self.game_hud.hide()
        pb.addWidget(self.game_hud)

        self.badge_lbl = QLabel("")
        self.badge_lbl.setStyleSheet("color: #93C5FD; font-size: 11px; font-weight: 600;")
        self.badge_lbl.hide()
        pb.addWidget(self.badge_lbl)

        self.clock_lbl = QLabel(self._last_clock_str)
        self.clock_lbl.setStyleSheet("color: #CBD5E1; font-size: 11px; font-weight: 600;")
        pb.addWidget(self.clock_lbl)

        self.atmo_badge_btn = QPushButton("")
        self.atmo_badge_btn.setFixedHeight(22)
        self.atmo_badge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.atmo_badge_btn.setToolTip("Нажмите, чтобы переключить: Авто / Закат / Восход / Ясно / Облачно / Пасмурно / Дождь / Фазы луны")
        self.atmo_badge_btn.clicked.connect(self._cycle_atmosphere_preview)
        self.atmo_badge_btn.hide()
        pb.addWidget(self.atmo_badge_btn)

        self.privacy_dot = QLabel("")
        self.privacy_dot.setFixedSize(10, 10)
        self.privacy_dot.hide()
        pb.addWidget(self.privacy_dot)

        self.eq_mini = _CollapsedEqualizer()
        self.eq_mini.hide()
        pb.addWidget(self.eq_mini)

        self._celestial_spacer = QWidget()
        self._celestial_spacer.setFixedSize(42, 24)
        self._celestial_spacer.setStyleSheet("background: transparent;")
        self._celestial_spacer.hide()
        pb.addWidget(self._celestial_spacer)

        self._root.addWidget(self.pill_bar)

        # --- Stage-1 Music Player Strip (between collapsed pill & full panel) ---
        self.music_strip_card = QFrame()
        self.music_strip_card.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
        )
        ms_lay = QHBoxLayout(self.music_strip_card)
        ms_lay.setContentsMargins(6, 4, 6, 4)
        ms_lay.setSpacing(10)

        self.strip_art_lbl = QLabel()
        self.strip_art_lbl.setFixedSize(58, 58)
        self.strip_art_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.strip_art_lbl.setStyleSheet("background: rgba(255,255,255,0.08); border-radius: 12px;")
        ms_lay.addWidget(self.strip_art_lbl)

        ms_right = QVBoxLayout()
        ms_right.setContentsMargins(0, 0, 0, 0)
        ms_right.setSpacing(5)

        ms_top = QHBoxLayout()
        ms_top.setSpacing(8)
        ms_title_col = QVBoxLayout()
        ms_title_col.setContentsMargins(0, 0, 0, 0)
        ms_title_col.setSpacing(1)
        self.strip_title_lbl = QLabel("No Track")
        self.strip_title_lbl.setStyleSheet("color: #F8FAFC; font-size: 13px; font-weight: 700;")
        self.strip_artist_lbl = QLabel("")
        self.strip_artist_lbl.setStyleSheet("color: #CBD5E1; font-size: 11px;")
        ms_title_col.addWidget(self.strip_title_lbl)
        ms_title_col.addWidget(self.strip_artist_lbl)
        ms_top.addLayout(ms_title_col, 1)

        self.strip_lyric_lbl = QLabel("")
        self.strip_lyric_lbl.setFixedHeight(24)
        self.strip_lyric_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _apply_lyric_chip_style(self.strip_lyric_lbl, self._media_source_color, in_strip=True)
        self.strip_lyric_lbl.setMaximumWidth(285)
        ms_top.addWidget(self.strip_lyric_lbl)
        ms_right.addLayout(ms_top)

        ms_bot = QHBoxLayout()
        ms_bot.setSpacing(5)
        self.strip_pos_lbl = QLabel("0:00")
        self.strip_pos_lbl.setStyleSheet("color: #CBD5E1; font-size: 10px; font-weight: 600;")
        self.strip_seek = QSlider(Qt.Orientation.Horizontal)
        self.strip_seek.setRange(0, 1000)
        self.strip_seek.setMinimumWidth(84)
        self.strip_seek.setStyleSheet(_RoundedPillSlider("#38BDF8"))
        self.strip_seek.sliderPressed.connect(lambda: setattr(self, "_strip_seeking", True))
        self.strip_seek.sliderReleased.connect(self._on_strip_seek_released)
        self.strip_dur_lbl = QLabel("0:00")
        self.strip_dur_lbl.setStyleSheet("color: #CBD5E1; font-size: 10px; font-weight: 600;")
        self.strip_btn_prev = self._make_mini_btn("prev", "Previous Track", self.media_prev_requested.emit)
        self.strip_btn_play = self._make_mini_btn("play", "Play / Pause", self.media_play_pause_requested.emit)
        self.strip_btn_next = self._make_mini_btn("next", "Next Track", self.media_next_requested.emit)
        for b in (self.strip_btn_prev, self.strip_btn_play, self.strip_btn_next):
            b.setFixedSize(24, 24)
        self.strip_vol_btn = self._make_mini_btn("volume", "Громкость / Без звука", self._on_strip_mute_clicked)
        self.strip_vol_btn.setFixedSize(22, 22)
        self.strip_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.strip_vol_slider.setRange(0, 100)
        self.strip_vol_slider.setSingleStep(2)
        self.strip_vol_slider.setPageStep(10)
        self.strip_vol_slider.setFixedWidth(68)
        self.strip_vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.strip_vol_slider.setToolTip("Громкость")
        self.strip_vol_slider.setStyleSheet(_RoundedPillSlider("#38BDF8"))
        self.strip_vol_slider.sliderPressed.connect(lambda: setattr(self, "_strip_vol_dragging", True))
        self.strip_vol_slider.sliderReleased.connect(self._on_strip_vol_released)
        self.strip_vol_slider.valueChanged.connect(self._on_strip_vol_changed)
        self.strip_vol_lbl = QLabel("50%")
        self.strip_vol_lbl.setFixedWidth(28)
        self.strip_vol_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.strip_vol_lbl.setStyleSheet("color: #CBD5E1; font-size: 10px; font-weight: 600;")
        vol_sep = QFrame()
        vol_sep.setFixedSize(1, 14)
        vol_sep.setStyleSheet("background: rgba(255,255,255,0.16);")
        ms_bot.addWidget(self.strip_pos_lbl)
        ms_bot.addWidget(self.strip_seek, 1)
        ms_bot.addWidget(self.strip_dur_lbl)
        ms_bot.addWidget(self.strip_btn_prev)
        ms_bot.addWidget(self.strip_btn_play)
        ms_bot.addWidget(self.strip_btn_next)
        ms_bot.addSpacing(2)
        ms_bot.addWidget(vol_sep)
        ms_bot.addSpacing(2)
        ms_bot.addWidget(self.strip_vol_btn)
        ms_bot.addWidget(self.strip_vol_slider)
        ms_bot.addWidget(self.strip_vol_lbl)
        ms_right.addLayout(ms_bot)
        ms_lay.addLayout(ms_right, 1)
        self.music_strip_card.hide()
        self._root.addWidget(self.music_strip_card)

        # --- Expanded Body ---
        self.panel.setParent(self)
        self.panel.hide()
        self._root.addWidget(self.panel, 1)
        if hasattr(self.panel, "card_rain"):
            self.panel.card_rain.set_backdrop_provider(self._backdrop_for_panel_rain)
        if hasattr(self.panel, "tall_mode_changed"):
            self.panel.tall_mode_changed.connect(lambda _: self.reposition(animate=True) if self._expanded else None)

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(235)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(230)
        self._opacity_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        # Cursor proximity + liquid drop physics + visibility watcher (switches Coarse/Precise dynamically)
        self._watch_timer = QTimer(self)
        self._watch_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._watch_timer.setInterval(85)
        self._watch_timer.timeout.connect(self._on_watch_tick)
        self._watch_timer.start()
        self._fg_tick = 0

        self._hover_collapse_block_until = 0.0
        self._hover_expand_timer = QTimer(self)
        self._hover_expand_timer.setSingleShot(True)
        self._hover_expand_timer.setInterval(150)
        self._hover_expand_timer.timeout.connect(self._on_hover_expand_timeout)

        self.apply_config()

    @staticmethod
    def _make_mini_btn(icon_name: str, tip: str, callback: Callable[[], None]) -> QPushButton:
        b = QPushButton("")
        b.setIcon(get_icon(icon_name, size=13, color="#F2F5FA", accent="#60A5FA"))
        b.setIconSize(QSize(13, 13))
        b.setToolTip(tip)
        b.setFixedSize(22, 22)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); color: #F2F5FA; border: none; "
            "border-radius: 11px; padding: 0px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.22); }"
        )
        b.clicked.connect(callback)
        return b

    def set_art_resolver(self, resolver: Callable[[str], QImage | None]) -> None:
        self._art_lookup = resolver

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_win32_styles()

    def hideEvent(self, event) -> None:  # noqa: N802
        if not self._expanded:
            self._grass_sim.reseed()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._watch_timer.stop()
        self._hover_expand_timer.stop()
        self._activity_timer.stop()
        self._strip_vol_timer.stop()
        self._anim.stop()
        self._opacity_anim.stop()
        self.character.set_animated(False)
        if self.panel is not None:
            self.panel.close()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            # Never allow Windows Snap / double-click / state glitch to stretch island full-screen
            if self.windowState() & Qt.WindowState.WindowMaximized:
                QTimer.singleShot(0, self._clear_maximized_state)
                return
            if not self.isMinimized():
                QTimer.singleShot(0, self._on_restored_from_taskbar)
        elif event.type() == QEvent.Type.WindowDeactivate and self._expanded:
            QTimer.singleShot(45, self._maybe_collapse_on_deactivate)

    def push_modal_hold(self) -> None:
        self._modal_hold_count = getattr(self, "_modal_hold_count", 0) + 1

    def pop_modal_hold(self) -> None:
        self._modal_hold_count = max(0, getattr(self, "_modal_hold_count", 0) - 1)

    def _maybe_collapse_on_deactivate(self) -> None:
        if not self._expanded and not self._music_strip_open:
            return
        if getattr(self, "_modal_hold_count", 0) > 0:
            return
        if (time.monotonic() - self._expand_ts) < 0.35:
            return
        # Never close on PrintScreen / Win+Shift+S snipping overlays
        if is_snipping_overlay_active():
            return
        if hasattr(self.panel, "chat_tab") and self.panel.chat_tab.has_active_webview():
            return
        # Keep expanded only if an active popup/modal dialog (e.g., combo dropdown or file picker) is open
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return
        if self._music_strip_open and not self._expanded:
            self._close_music_strip()
        elif self._expanded:
            self.set_expanded(False)

    def _on_outside_click(self, global_pos: QPoint) -> None:
        """Collapse expanded panel or stage-1 music strip immediately on click outside Blip's body."""
        if (not self._expanded and not self._music_strip_open) or (time.monotonic() - self._expand_ts) < 0.35:
            return
        if getattr(self, "_modal_hold_count", 0) > 0:
            return
        if is_snipping_overlay_active():
            return
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return
        core = self._core_rect()
        core_global = QRect(self.mapToGlobal(core.topLeft().toPoint()), core.size().toSize())
        if core_global.contains(global_pos):
            return
        for w in QApplication.topLevelWidgets():
            if w is self or not w.isVisible():
                continue
            if w.geometry().contains(global_pos):
                return
        if self._music_strip_open and not self._expanded:
            self._close_music_strip()
        else:
            self.set_expanded(False)

    def _clear_maximized_state(self) -> None:
        if self.windowState() & Qt.WindowState.WindowMaximized:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
        self.reposition(animate=False)

    def _on_restored_from_taskbar(self) -> None:
        self._apply_win32_styles()
        self.reposition(animate=False)

    def nativeEvent(self, eventType, message):  # type: ignore[override]
        # Reject SC_MAXIMIZE so Windows cannot maximize the island window across the entire screen
        if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG") and message is not None:
            try:
                import ctypes
                import ctypes.wintypes as wt

                msg = ctypes.cast(int(message), ctypes.POINTER(wt.MSG)).contents
                if msg.message == WM_SYSCOMMAND and (int(msg.wParam) & 0xFFF0) == SC_MAXIMIZE:
                    return True, 0
            except Exception:
                pass
        return False, 0

    def _apply_win32_styles(self) -> None:
        hwnd = int(self.winId())
        cfg = self.config_mgr.config
        set_overlay_window_flags(
            hwnd,
            click_through=(cfg.click_through and not self._expanded),
            no_activate=False,
            app_window=False,
        )
        set_exclude_from_capture(hwnd, cfg.exclude_from_capture)

    def _use_weather_appearance(self) -> bool:
        from ..core.licensing import get_license_manager

        cfg = self.config_mgr.config
        return str(getattr(cfg, "island_appearance_mode", "weather") or "weather").lower() == "weather" and get_license_manager().is_feature_allowed("weather")

    def _resolve_effective_weather(self) -> tuple[str, str, float | None, str]:
        from ..core.licensing import get_license_manager

        lic = get_license_manager()
        cfg = self.config_mgr.config
        eff_weather = (
            "clear"
            if (cfg.atmosphere_weather_mode == "auto" and not lic.is_feature_allowed("realtime_weather"))
            else cfg.atmosphere_weather_mode
        )
        seg, wkind, forced_moon = resolve_scene_state(
            cfg.atmosphere_preview,
            self._weather_symbol,
            lat=cfg.weather_lat,
            lon=cfg.weather_lon,
            sky_mode=cfg.atmosphere_sky_mode,
            weather_mode=eff_weather,
            moon_mode=cfg.atmosphere_moon_mode,
        )
        return seg, wkind, forced_moon, eff_weather

    def _is_card_rain_active(self) -> tuple[bool, str, float]:
        _seg, wkind, _moon, eff_w = self._resolve_effective_weather()
        precip = self._precip_kind if eff_w == "auto" else ""
        raining = is_rain_weather(wkind, self._weather_symbol if eff_w == "auto" else "", precip)
        flash = (
            AtmosphericScenePainter.current_storm_flash(self._atmo_phase, wkind, anim_state=self._atmo_anim)
            if (raining and wkind in ("thunder", "lightning"))
            else 0.0
        )
        return raining, wkind, flash

    def _backdrop_for_panel_rain(self) -> tuple[QImage | None, QPoint]:
        img = getattr(self, "_exp_composite_img", None) or getattr(self, "_glass_bg_img", None)
        if img is not None and not img.isNull():
            return img, QPoint(0, 0)
        return None, QPoint(0, 0)

    def _step_card_rain(self, now_a: float) -> tuple[bool, bool]:
        raining, wkind, flash_now = self._is_card_rain_active()
        atmo_rain_anim = bool(self._expanded and self.isVisible() and self._use_weather_appearance() and raining)
        rain_anim = False
        cr = getattr(self.panel, "card_rain", None)
        if cr is not None:
            anim_busy = (
                self._anim.state() == QPropertyAnimation.State.Running
                or self._hide_state in ("hiding", "showing")
            )
            if not self._expanded or not self.isVisible() or getattr(self, "_fg_screen_covered", False) or self._hide_state == "hidden":
                cr.set_suspended(True)
                cr.set_rain_state(False)
                if cr.isVisible():
                    cr.hide()
                return False, False
            elif anim_busy:
                cr.set_suspended(True)
                cr.set_rain_state(raining, intensity=(1.25 if wkind in ("thunder", "lightning") else 1.0), stormy=(wkind in ("thunder", "lightning")), lightning_flash=flash_now)
            else:
                cr.set_suspended(False)
                stormy = wkind in ("thunder", "lightning")
                cr.set_rain_state(
                    raining,
                    intensity=(1.25 if stormy else 1.0),
                    stormy=stormy,
                    lightning_flash=flash_now,
                )
                rain_anim = cr.step_frame(now_a)
        return rain_anim, atmo_rain_anim

    def _effective_appearance_mode(self) -> str:
        from ..core.licensing import get_license_manager

        cfg = self.config_mgr.config
        m = str(getattr(cfg, "island_appearance_mode", "weather") or "weather").lower()
        lic = get_license_manager()
        if m == "weather" and not lic.is_feature_allowed("weather"):
            return "color"
        if m == "video" and not lic.is_feature_allowed("weather"):
            return "image" if getattr(cfg, "custom_bg_image_path", "") else "color"
        return m if m in ("weather", "music", "color", "image", "video") else "color"

    def preview_accent_color(self, hex_col: str) -> None:
        """Ultra-fast lag-free live preview while dragging the color wheel (skips heavy full-panel QSS rebuild)."""
        hx = str(hex_col or "").strip()
        if not (hx.startswith("#") and len(hx) == 7):
            return
        cfg = self.config_mgr.config
        cfg.accent_color = hx
        cfg.island_appearance_mode = "color"
        pal = resolve_palette(cfg.theme, hx, color_tint=True)
        self.palette = pal
        self.character.set_accent(pal.accent)
        self.eq_mini.set_accent(pal.accent)
        self.update()

    @staticmethod
    def _extract_image_accent(img: QImage) -> str:
        if img.isNull():
            return ""
        small = img.scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        r_sum = g_sum = b_sum = w_sum = 0.0
        for y in range(small.height()):
            for x in range(small.width()):
                c = small.pixelColor(x, y)
                sat = c.saturationF()
                val = c.valueF()
                w = 0.25 + sat * 2.5 + (0.4 if 0.2 < val < 0.92 else 0.0)
                r_sum += c.red() * w
                g_sum += c.green() * w
                b_sum += c.blue() * w
                w_sum += w
        if w_sum <= 0:
            return ""
        out = QColor(int(r_sum / w_sum), int(g_sum / w_sum), int(b_sum / w_sum))
        if out.valueF() < 0.42:
            out = QColor.fromHsvF(max(0.0, out.hueF()), min(1.0, out.saturationF() + 0.18), 0.68)
        return out.name().upper()

    def _stop_custom_bg_players(self) -> None:
        mov = getattr(self, "_custom_bg_movie", None)
        if mov is not None:
            try:
                mov.stop()
                mov.deleteLater()
            except Exception:
                pass
            self._custom_bg_movie = None
        vt = getattr(self, "_custom_bg_video_timer", None)
        if vt is not None:
            vt.stop()
        cap = getattr(self, "_custom_bg_cv_cap", None)
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
            self._custom_bg_cv_cap = None

    def _sync_custom_bg_media(self) -> None:
        from pathlib import Path
        from PyQt6.QtGui import QMovie

        cfg = self.config_mgr.config
        mode = self._effective_appearance_mode()
        target_path = ""
        if mode == "image":
            target_path = str(getattr(cfg, "custom_bg_image_path", "") or "").strip()
        elif mode == "video":
            target_path = str(getattr(cfg, "custom_bg_video_path", "") or "").strip()

        stat_sig = ""
        if target_path:
            try:
                st = Path(target_path).stat()
                stat_sig = f"|{st.st_mtime_ns}|{st.st_size}"
            except OSError:
                stat_sig = ""
        cur_key = f"{mode}|{target_path}{stat_sig}"
        if getattr(self, "_custom_bg_media_key", "") == cur_key:
            return
        self._custom_bg_media_key = cur_key
        self._stop_custom_bg_players()
        self._custom_bg_img: QImage = QImage()
        self._custom_bg_scaled_cache: tuple[int, int, int, QImage] | None = None
        self._exp_composite_cache = None
        self._video_glass_cache = None
        self._custom_bg_frame_seq = 0
        self._custom_bg_accent = ""

        if not target_path or not Path(target_path).is_file():
            return

        ext = Path(target_path).suffix.lower()
        if mode == "image" or ext in (".png", ".jpg", ".jpeg", ".bmp"):
            img = QImage(target_path)
            if not img.isNull():
                self._custom_bg_img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                self._custom_bg_frame_seq += 1
                self._custom_bg_accent = self._extract_image_accent(self._custom_bg_img)
                return
            if mode == "image" and ext != ".webp":
                return

        if ext in (".gif", ".webp"):
            mov = QMovie(target_path)
            if mov.isValid() and mov.frameCount() != 1:
                self._custom_bg_movie = mov
                mov.frameChanged.connect(self._on_custom_bg_movie_frame)
                mov.start()
                first = mov.currentImage()
                if not first.isNull():
                    self._custom_bg_img = first
                    self._custom_bg_frame_seq += 1
                    self._custom_bg_accent = self._extract_image_accent(first)
                return
            # Static WebP fallback
            img = QImage(target_path)
            if not img.isNull():
                self._custom_bg_img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                self._custom_bg_frame_seq += 1
                self._custom_bg_accent = self._extract_image_accent(self._custom_bg_img)
            return

        # Video file (.mp4, .webm, .mov, .avi, .mkv) via OpenCV looped decoder
        try:
            import cv2

            cap = cv2.VideoCapture(target_path)
            if cap.isOpened():
                self._custom_bg_cv_cap = cap
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
                if fps <= 4.0 or fps > 90.0:
                    fps = 30.0
                if not hasattr(self, "_custom_bg_video_timer") or self._custom_bg_video_timer is None:
                    self._custom_bg_video_timer = QTimer(self)
                    self._custom_bg_video_timer.setTimerType(Qt.TimerType.CoarseTimer)
                    self._custom_bg_video_timer.timeout.connect(self._tick_custom_bg_video)
                self._custom_bg_video_fps = min(20.0, fps)
                want_iv = max(50, int(round(1000.0 / self._custom_bg_video_fps))) if self._expanded else 100
                self._custom_bg_video_timer.setInterval(want_iv)
                self._tick_custom_bg_video(first_frame=True)
                self._custom_bg_video_timer.start()
        except Exception:
            pass

    def _on_custom_bg_movie_frame(self, _frame_no: int) -> None:
        if not self.isVisible() or self._hide_state == "hidden" or getattr(self, "_fg_screen_covered", False):
            return
        mov = getattr(self, "_custom_bg_movie", None)
        if mov is None:
            return
        im = mov.currentImage()
        if not im.isNull():
            self._custom_bg_img = im
            self._custom_bg_frame_seq = getattr(self, "_custom_bg_frame_seq", 0) + 1
            if not getattr(self, "_custom_bg_accent", ""):
                self._custom_bg_accent = self._extract_image_accent(im)
            if self.isVisible() and self._hide_state != "hidden":
                self.update()

    def _tick_custom_bg_video(self, *, first_frame: bool = False) -> None:
        cap = getattr(self, "_custom_bg_cv_cap", None)
        if cap is None:
            return
        v_timer = getattr(self, "_custom_bg_video_timer", None)
        if not first_frame:
            if not self.isVisible() or self._hide_state == "hidden" or getattr(self, "_fg_screen_covered", False):
                if v_timer is not None and v_timer.interval() != 320:
                    v_timer.setInterval(320)
                return
            fps_cap = float(getattr(self, "_custom_bg_video_fps", 20.0) or 20.0)
            want_iv = max(50, int(round(1000.0 / min(20.0, fps_cap)))) if self._expanded else 100
            if v_timer is not None and v_timer.interval() != want_iv:
                v_timer.setInterval(want_iv)
        try:
            import cv2

            ok, frame = cap.read()
            if not ok or frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            if ok and frame is not None:
                fh, fw = frame.shape[:2]
                if self._expanded or self._music_strip_open:
                    tw = max(480, int(round(self.width() - _DROP_PAD_X * 2)))
                    th = max(320, int(round(self.height() - _DROP_PAD_TOP - _DROP_PAD_BOTTOM)))
                else:
                    tw, th = 360, 72
                if fw > tw + 32 and fh > th + 32:
                    sc = max(tw / float(fw), th / float(fh))
                    nw = max(2, int(round(fw * sc)))
                    nh = max(2, int(round(fh * sc)))
                    if nw < fw and nh < fh:
                        frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
                h, w, _ch = rgb.shape
                qimg = QImage(rgb.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
                self._custom_bg_img = qimg
                self._custom_bg_frame_seq = getattr(self, "_custom_bg_frame_seq", 0) + 1
                if first_frame or not getattr(self, "_custom_bg_accent", ""):
                    self._custom_bg_accent = self._extract_image_accent(qimg)
                if not first_frame:
                    self.update()
        except Exception:
            pass

    def _paint_custom_bg_media(self, painter: QPainter, bounds: QRectF, *, veil_alpha: int = 96) -> bool:
        mode = self._effective_appearance_mode()
        if mode not in ("image", "video"):
            return False
        self._sync_custom_bg_media()
        src: QImage = getattr(self, "_custom_bg_img", QImage())
        if src.isNull():
            return False
        bw = max(1, int(round(bounds.width())))
        bh = max(1, int(round(bounds.height())))
        seq = int(getattr(self, "_custom_bg_frame_seq", 0))
        cache = getattr(self, "_custom_bg_scaled_cache", None)
        if cache is not None and cache[0] == seq and cache[1] == bw and cache[2] == bh:
            fitted = cache[3]
        else:
            tr_mode = (
                Qt.TransformationMode.FastTransformation
                if mode == "video"
                else Qt.TransformationMode.SmoothTransformation
            )
            scaled = src.scaled(bw, bh, Qt.AspectRatioMode.KeepAspectRatioByExpanding, tr_mode)
            cx = max(0, (scaled.width() - bw) // 2)
            cy = max(0, (scaled.height() - bh) // 2)
            fitted = scaled.copy(cx, cy, bw, bh)
            self._custom_bg_scaled_cache = (seq, bw, bh, fitted)
        painter.drawImage(QPointF(bounds.left(), bounds.top()), fitted)
        if veil_alpha > 0:
            painter.fillRect(bounds, QColor(8, 11, 18, veil_alpha))
        return True

    def _compute_palettes(self) -> tuple[ThemePalette, ThemePalette]:
        """Return (pill_palette, panel_palette) respecting PRO weather/video, Music-adaptive, and custom image/color modes."""
        cfg = self.config_mgr.config
        from ..core.licensing import get_license_manager

        lic = get_license_manager()
        self._sync_custom_bg_media()
        eff_mode = self._effective_appearance_mode()
        use_weather = eff_mode == "weather"
        if use_weather:
            eff_weather = (
                "clear"
                if (cfg.atmosphere_weather_mode == "auto" and not lic.is_feature_allowed("realtime_weather"))
                else cfg.atmosphere_weather_mode
            )
            time_pal = resolve_palette(
                cfg.theme,
                weather_symbol=self._weather_symbol,
                preview_mode=cfg.atmosphere_preview,
                lat=cfg.weather_lat,
                lon=cfg.weather_lon,
                sky_mode=cfg.atmosphere_sky_mode,
                weather_mode=eff_weather,
                moon_mode=cfg.atmosphere_moon_mode,
            )
        elif eff_mode == "music":
            # Keep full interface themed to current or last music track even when music is paused/stopped
            music_acc = (
                (self._accent_hex if (self._has_media and self._accent_hex) else "")
                or self._spotify_sticky_hex
                or str(getattr(cfg, "music_last_accent", "") or "").strip()
                or str(getattr(cfg, "accent_color", "") or "").strip()
                or resolve_time_accent()
            )
            time_pal = resolve_palette(cfg.theme, music_acc, music_tint=True)
        elif eff_mode in ("image", "video"):
            media_acc = getattr(self, "_custom_bg_accent", "") or str(getattr(cfg, "accent_color", "") or "").strip() or resolve_time_accent()
            time_pal = resolve_palette(cfg.theme, media_acc, color_tint=True)
        else:
            custom_acc = str(getattr(cfg, "accent_color", "") or "").strip() or resolve_time_accent()
            time_pal = resolve_palette(cfg.theme, custom_acc, color_tint=True)

        if self._activity_active:
            pill_pal = resolve_palette(cfg.theme, self._activity_accent, music_tint=True)
        elif eff_mode == "music":
            pill_pal = time_pal
        elif self._playing and self._accent_hex:
            pill_pal = resolve_palette(cfg.theme, self._accent_hex, music_tint=True)
        elif self._spotify_sticky_hex:
            pill_pal = resolve_palette(cfg.theme, self._spotify_sticky_hex, music_tint=True)
        else:
            pill_pal = time_pal
        return pill_pal, time_pal

    def _sync_palettes(self, *, force_panel: bool = False) -> None:
        pill_pal, time_pal = self._compute_palettes()
        cfg = self.config_mgr.config
        eff_mode = self._effective_appearance_mode()
        music_expanded_pal = time_pal if eff_mode != "weather" else (
            resolve_palette(cfg.theme, self._accent_hex, music_tint=True)
            if (self._playing and self._accent_hex)
            else time_pal
        )
        self.palette = music_expanded_pal if self._expanded else pill_pal
        pill_sig = (
            self.palette.accent,
            self.palette.text_primary,
            pill_pal.accent,
            pill_pal.text_primary,
            self._strip_vol_muted,
        )
        pill_changed = force_panel or (pill_sig != getattr(self, "_last_pill_sig", None))
        if pill_changed:
            self._last_pill_sig = pill_sig
            self.character.set_accent(self.palette.accent)
            self.eq_mini.set_accent(pill_pal.accent)
            self.strip_seek.setStyleSheet(_RoundedPillSlider(pill_pal.accent))
            self.strip_vol_slider.setStyleSheet(_RoundedPillSlider(pill_pal.accent))
            self.strip_vol_btn.setIcon(
                get_icon("mute" if self._strip_vol_muted else "volume", size=13, color="#F2F5FA", accent=pill_pal.accent)
            )
            self.pill_text.setStyleSheet(f"color: {self.palette.text_primary}; font-size: 12px; font-weight: 600;")
            _apply_lyric_chip_style(self.pill_lyric_lbl, pill_pal.accent, in_strip=False)
            _apply_lyric_chip_style(self.strip_lyric_lbl, pill_pal.accent, in_strip=True)
        pal_sig = f"{music_expanded_pal.accent}|{music_expanded_pal.bg_island}|{eff_mode}"
        panel_changed = force_panel or (pal_sig != self._last_palette_key)
        if panel_changed:
            self._last_palette_key = pal_sig
            self._exp_composite_cache = None
            self._exp_surface_cache = None
            self._video_glass_cache = None
            self._exp_off_cache = None
            self.panel.apply_palette(music_expanded_pal)
            if eff_mode == "music":
                try:
                    self.music_palette_changed.emit(music_expanded_pal)
                except Exception:
                    pass
        if pill_changed or panel_changed:
            self.update()

    def apply_config(self) -> None:
        from ..core.licensing import get_license_manager
        from ..core.win32_api import apply_laptop_performance_mode

        cfg = self.config_mgr.config
        apply_laptop_performance_mode(bool(getattr(cfg, "remove_power_limits", True)))
        self._sync_palettes(force_panel=True)
        eff_char = cfg.character_id if get_license_manager().is_character_allowed(cfg.character_id) else "liquid"
        self.character.set_character(eff_char)
        self.character.set_precip(self._precip_kind if cfg.weather_rain_drip else "")
        if hasattr(self.panel, "character_tab") and hasattr(self.panel.character_tab, "sync_from_config"):
            self.panel.character_tab.sync_from_config()
        if hasattr(self.panel, "card_rain"):
            self.panel.card_rain.mark_underlay_dirty()
        self.setWindowOpacity(cfg.expanded_opacity if self._expanded else cfg.collapsed_opacity)
        if self.isVisible():
            self._apply_win32_styles()
        self.game_hud.set_visible_metrics(
            cpu=bool(getattr(cfg, "show_game_cpu", True)),
            ram=bool(getattr(cfg, "show_game_ram", True)),
            gpu=bool(getattr(cfg, "show_game_gpu", True)),
        )
        show_game_cfg = bool(getattr(cfg, "show_game_stats_in_pill", True)) and bool(
            getattr(cfg, "show_game_cpu", True)
            or getattr(cfg, "show_game_ram", True)
            or getattr(cfg, "show_game_gpu", True)
        )
        if not show_game_cfg:
            self._in_game = False
        self._refresh_pill_views()
        self.reposition(animate=False)

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def _animate_opacity(self, target_op: float, duration_ms: int = 230) -> None:
        tgt = max(0.15, min(1.0, float(target_op)))
        self._opacity_anim.stop()
        if abs(self.windowOpacity() - tgt) < 0.01 or not self.isVisible():
            self.setWindowOpacity(tgt)
            return
        self._opacity_anim.setDuration(duration_ms)
        self._opacity_anim.setStartValue(self.windowOpacity())
        self._opacity_anim.setEndValue(tgt)
        self._opacity_anim.start()

    def _open_music_strip(self) -> None:
        if self._expanded or not self._has_media or self._music_strip_open:
            return
        self._cancel_hide_immediately()
        self._music_strip_open = True
        self._expand_ts = time.monotonic()
        self.music_strip_card.show()
        self._animate_opacity(self.config_mgr.config.expanded_opacity, 240)
        self.trigger_drop_impulse(0.62)
        self._refresh_pill_views()
        self._refresh_strip_volume_async()
        curve = QEasingCurve(QEasingCurve.Type.OutBack)
        curve.setOvershoot(0.36)
        self._anim.stop()
        self._anim.setDuration(310)
        self._anim.setEasingCurve(curve)
        self.reposition(animate=True)

    def _close_music_strip(self) -> None:
        if not self._music_strip_open:
            return
        self._music_strip_open = False
        self.music_strip_card.hide()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._animate_opacity(self.config_mgr.config.collapsed_opacity, 240)
        self.trigger_drop_impulse(0.45)
        self._refresh_pill_views()
        self._anim.stop()
        self._anim.setDuration(305)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuint)
        self.reposition(animate=True)

    def _refresh_strip_volume_async(self) -> None:
        dev = self.config_mgr.config.output_device_id or "default"
        proc_hint = self._media_source_id if self._has_media else ""

        def _work() -> None:
            try:
                cur = self.bridge.get_app_volume(proc_hint, dev) if proc_hint else {"found": False}
                if not cur.get("found"):
                    cur = self.bridge.get_volume("render", dev)
                vol = max(0, min(100, int(cur.get("volume", self._estimated_vol))))
                muted = bool(cur.get("muted", False))
                self._strip_volume_loaded.emit(vol, muted)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _set_strip_volume_ui(self, vol: int, muted: bool) -> None:
        if self._strip_vol_dragging:
            return
        v = max(0, min(100, int(vol)))
        self._estimated_vol = v
        self._strip_vol_muted = bool(muted)
        self.strip_vol_slider.blockSignals(True)
        self.strip_vol_slider.setValue(v)
        self.strip_vol_slider.blockSignals(False)
        self.strip_vol_lbl.setText("Выкл" if self._strip_vol_muted else f"{v}%")
        self.strip_vol_btn.setIcon(
            get_icon("mute" if self._strip_vol_muted else "volume", size=13, color="#F2F5FA", accent=self.palette.accent)
        )

    def _on_strip_vol_changed(self, val: int) -> None:
        v = max(0, min(100, int(val)))
        self._estimated_vol = v
        if self._strip_vol_muted and v > 0:
            self._strip_vol_muted = False
            self.strip_vol_btn.setIcon(get_icon("volume", size=13, color="#F2F5FA", accent=self.palette.accent))
            dev = self.config_mgr.config.output_device_id or "default"
            proc_hint = self._media_source_id if self._has_media else ""

            def _unmute() -> None:
                if proc_hint and self.bridge.set_app_mute(proc_hint, False, dev):
                    return
                self.bridge.set_mute(False, "render", dev)

            threading.Thread(target=_unmute, daemon=True).start()
        self.strip_vol_lbl.setText("Выкл" if self._strip_vol_muted else f"{v}%")
        self._strip_vol_pending = v
        if not self._strip_vol_timer.isActive():
            self._strip_vol_timer.start()

    def _on_strip_vol_released(self) -> None:
        self._strip_vol_dragging = False
        self._strip_vol_pending = max(0, min(100, int(self.strip_vol_slider.value())))
        self._flush_strip_volume()

    def _flush_strip_volume(self) -> None:
        if self._strip_vol_pending is None:
            return
        vol = max(0, min(100, int(self._strip_vol_pending)))
        self._strip_vol_pending = None
        dev = self.config_mgr.config.output_device_id or "default"
        proc_hint = self._media_source_id if self._has_media else ""

        def _apply() -> None:
            if proc_hint and self.bridge.set_app_volume(proc_hint, vol, dev):
                return
            self.bridge.set_volume(vol, "render", dev)

        threading.Thread(target=_apply, daemon=True).start()

    def _on_strip_mute_clicked(self) -> None:
        self._strip_vol_muted = not self._strip_vol_muted
        muted = self._strip_vol_muted
        v = int(self.strip_vol_slider.value())
        self.strip_vol_lbl.setText("Выкл" if muted else f"{v}%")
        self.strip_vol_btn.setIcon(
            get_icon("mute" if muted else "volume", size=13, color="#F2F5FA", accent=self.palette.accent)
        )
        dev = self.config_mgr.config.output_device_id or "default"
        proc_hint = self._media_source_id if self._has_media else ""

        def _apply_mute() -> None:
            if proc_hint and self.bridge.set_app_mute(proc_hint, muted, dev):
                return
            self.bridge.set_mute(muted, "render", dev)

        threading.Thread(target=_apply_mute, daemon=True).start()

    def _on_strip_seek_released(self) -> None:
        self._strip_seeking = False
        if self._media_dur > 0:
            sec = (self.strip_seek.value() / 1000.0) * self._media_dur
            self.media_seek_requested.emit(sec)

    def _cancel_hide_immediately(self) -> None:
        self._hide_state = "shown"
        self._hide_p = 0.0
        self._pill_morph_pixmap = QPixmap()
        self.pill_bar.setVisible(True)
        if not self.isVisible() and not self.isMinimized():
            self.show()

    def set_expanded(self, expand: bool) -> None:
        if self._expanded == expand:
            if expand and self._hide_state != "shown":
                self._cancel_hide_immediately()
            return
        self._music_strip_open = False
        self.music_strip_card.hide()
        self._expanded = expand
        cfg = self.config_mgr.config
        self._anim.stop()
        # Reset layout size constraints cleanly so collapsing NEVER stays wide
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if expand:
            self._cancel_hide_immediately()
            self._grass_sim.reseed()
            self._expand_ts = time.monotonic()
            self.panel.show()
            if hasattr(self.panel, "card_rain"):
                # Pause CardRain while window width animates so right-column cards don't sweep droplets rightward
                self.panel.card_rain.set_suspended(True)
                self.panel.card_rain.sync_geometry()
            self._animate_opacity(cfg.expanded_opacity, 250)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            curve = QEasingCurve(QEasingCurve.Type.OutBack)
            curve.setOvershoot(0.42)
            self._anim.setDuration(340)
            self._anim.setEasingCurve(curve)
        else:
            # Hide panel immediately so Qt layout minSize does not clamp collapse animation!
            if hasattr(self._atmo_anim, "on_island_collapsed"):
                self._atmo_anim.on_island_collapsed()
            self._hover_collapse_block_until = time.monotonic() + 0.65
            self._hover_expand_timer.stop()
            if hasattr(self.panel, "card_rain"):
                self.panel.card_rain.set_suspended(True)
                self.panel.card_rain.set_rain_state(False)
                self.panel.card_rain.hide()
            self.panel.hide()
            self._animate_opacity(cfg.collapsed_opacity, 240)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self._anim.setDuration(310)
            self._anim.setEasingCurve(QEasingCurve.Type.OutQuint)

        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()

        self._exp_composite_cache = None
        self._glass_geo_cache = None
        self._glass_tpl_cache = None
        self._glass_nav_cache = None
        self._apply_win32_styles()
        if expand:
            self.raise_()
            self.activateWindow()
        self._sync_palettes(force_panel=expand)
        self._refresh_pill_views()
        self.trigger_drop_impulse(0.78 if expand else 0.56)
        if expand and getattr(self, "_custom_bg_cv_cap", None) is not None:
            self._tick_custom_bg_video()
        self.reposition(animate=True)

    def _on_anim_finished(self) -> None:
        self._anim.setDuration(235)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if not self._expanded and not self._music_strip_open:
            self._grass_sim.reseed()
            self.panel.hide()
            self.music_strip_card.hide()
            self.setMinimumSize(0, 0)
            target = self._target_geometry()
            if self.geometry() != target:
                self.setGeometry(target)
        elif self._expanded and self.isVisible():
            if hasattr(self.panel, "card_rain"):
                if self.panel.layout() is not None:
                    self.panel.layout().activate()
                cr = self.panel.card_rain
                cr._geo_cache = None
                cr.sync_geometry()
                cr.set_suspended(False)
                self._step_card_rain(time.monotonic())
            # Refresh full settled glass refraction after fast morph transition finishes
            self._exp_composite_cache = None
            self.update()
        core = self._core_rect()
        self.geometry_changed.emit(int(round(self.x() + core.center().x())), int(round(self.y() + core.bottom())))

    def _core_rect(self) -> QRectF:
        """Inner visible island silhouette rect inside the transparent droplet animation margin."""
        r = self.rect()
        return QRectF(
            float(_DROP_PAD_X),
            float(_DROP_PAD_TOP),
            float(max(40, r.width() - 2 * _DROP_PAD_X)),
            float(max(30, r.height() - _DROP_PAD_TOP - _DROP_PAD_BOTTOM)),
        )

    def _target_geometry(self) -> QRect:
        cfg = self.config_mgr.config
        screens = QGuiApplication.screens()
        scr = screens[cfg.monitor_index] if 0 <= cfg.monitor_index < len(screens) else QGuiApplication.primaryScreen()
        geo = scr.geometry() if scr else QRect(0, 0, 1920, 1080)
        user_scale = max(0.8, min(2.0, float(cfg.scale)))
        auto_fit = min(1.0, float(geo.width()) / 1680.0, float(geo.height()) / 960.0)
        scale = max(0.68, min(2.0, user_scale * auto_fit))

        if self._expanded:
            tall = bool(hasattr(self.panel, "wants_tall_island") and self.panel.wants_tall_island())
            core_w = int(round((1120 if tall else 980) * scale))
            core_h = int(round((700 if tall else 568) * scale))
            core_w = min(core_w, max(360, int(geo.width() * 0.84) - 2 * _DROP_PAD_X))
            core_h = min(core_h, max(320, int(geo.height() * 0.74) - _DROP_PAD_TOP - _DROP_PAD_BOTTOM))
        elif self._music_strip_open and self._has_media:
            core_w = min(int(round(690 * scale)), max(360, geo.width() - 2 * _DROP_PAD_X - 24))
            core_h = int(round(134 * scale))
        else:
            if self._activity_active:
                base_w = 370
            elif self._in_game and not self._expanded:
                lyrics_on = bool(cfg.show_lyrics_in_pill and self._has_media and self._media_lyric)
                if not self._has_media:
                    base_w = 435
                elif lyrics_on:
                    lyric_px = min(330, self.pill_lyric_lbl.fontMetrics().horizontalAdvance(f"♪  {self._media_lyric}") + 36)
                    base_w = max(720, 490 + lyric_px)
                else:
                    base_w = 648
            elif self._has_media:
                lyrics_on = bool(cfg.show_lyrics_in_pill and (self._playing or bool(self._media_lyric)) and self._media_lyric)
                if lyrics_on:
                    lyric_px = min(330, self.pill_lyric_lbl.fontMetrics().horizontalAdvance(f"♪  {self._media_lyric}") + 36)
                    min_w = 560 if (cfg.pill_mode == "miniplayer" or self._hover_active) else 505
                    base_w = max(min_w, 260 + lyric_px)
                elif cfg.pill_mode == "miniplayer" or self._hover_active:
                    base_w = 495
                elif cfg.pill_mode == "compact":
                    base_w = 370
                else:
                    base_w = 440
            else:
                base_w = 252 if cfg.pill_mode == "compact" else 285
            if self._timer_text:
                base_w += 52
            elif self._weather_text and not self._has_media and not self._activity_active:
                base_w += 42
            if self.battery_dot.isVisible():
                base_w += 22
            core_w = min(int(round(base_w * scale)), max(220, geo.width() - 2 * _DROP_PAD_X - 32))
            core_h = int(round(42 * scale))

        win_w = core_w + 2 * _DROP_PAD_X
        win_h = core_h + _DROP_PAD_TOP + _DROP_PAD_BOTTOM
        cx = geo.x() + geo.width() // 2 + cfg.offset_x
        x = max(geo.x() + 4, min(geo.x() + geo.width() - win_w - 4, cx - win_w // 2))
        y = geo.y() + 14 - _DROP_PAD_TOP
        return QRect(x, y, win_w, win_h)

    def reposition(self, animate: bool = True) -> None:
        target = self._target_geometry()
        if not animate or not self.isVisible():
            self._anim.stop()
            self.setGeometry(target)
            core = self._core_rect()
            self.geometry_changed.emit(target.x() + int(core.center().x()), target.y() + int(core.bottom()))
            return
        if self._anim.state() == QPropertyAnimation.State.Running and self._anim.endValue() == target:
            return
        if self.geometry() == target and self._anim.state() != QPropertyAnimation.State.Running:
            return
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    # ------------------------------------------------------------------ Live Updates & Activity Banner
    def show_activity(
        self,
        badge: str,
        text: str,
        accent_hex: str = "#38BDF8",
        thumb: QPixmap | QImage | None = None,
        duration_ms: int = 4200,
        action_cb: Callable[[], None] | None = None,
        icon_name: str = "",
    ) -> None:
        """Flash a live status banner inside the top pill (clipboard copy, screenshot, keyboard layout, etc.)."""
        self._activity_active = True
        self._activity_badge = badge
        self._activity_text = text
        self._activity_icon = icon_name
        self._activity_accent = accent_hex
        if isinstance(thumb, QImage) and not thumb.isNull():
            self._activity_thumb = _rounded_thumb(QPixmap.fromImage(thumb))
        elif isinstance(thumb, QPixmap) and not thumb.isNull():
            self._activity_thumb = _rounded_thumb(thumb)
        else:
            self._activity_thumb = QPixmap()
        self._activity_cb = action_cb
        self.trigger_drop_impulse(1.1)
        self._sync_palettes()
        self._refresh_pill_views()
        if duration_ms > 0:
            self._activity_timer.start(duration_ms)
        if not self._expanded:
            self.reposition(animate=True)

    def _clear_activity(self) -> None:
        self._activity_active = False
        self._activity_icon = ""
        self._activity_cb = None
        self._activity_thumb = QPixmap()
        self._sync_palettes()
        self._refresh_pill_views()
        if not self._expanded:
            self.reposition(animate=True)

    def _refresh_pill_views(self) -> None:
        cfg = self.config_mgr.config
        if self._activity_active:
            self.game_hud.hide()
            self.pill_lyric_lbl.hide()
            self.pill_text.setMaximumWidth(16777215)
            if self._activity_icon:
                self.source_icon_lbl.setPixmap(
                    get_pixmap(self._activity_icon, size=14, color="#F8FAFC", accent=self._activity_accent)
                )
                self.source_icon_lbl.show()
            else:
                self.source_icon_lbl.hide()
            self.source_badge.setText(self._activity_badge)
            self.source_badge.setStyleSheet(
                f"background: {self._activity_accent}44; color: #FFFFFF; "
                f"border: 1px solid {self._activity_accent}99; border-radius: 9px; "
                "padding: 0px 7px; font-size: 10px; font-weight: 700;"
            )
            self.source_badge.setVisible(bool(self._activity_badge))
            self.pill_text.setText(self._activity_text)
            if not self._activity_thumb.isNull():
                self.art_thumb.setPixmap(self._activity_thumb)
                self.art_thumb.show()
            else:
                self.art_thumb.hide()
            self.mini_controls.hide()
        elif self._has_media:
            in_game_now = self._in_game and not self._expanded
            strip_mode = bool(self._music_strip_open and not self._expanded)
            lyrics_on = bool(
                cfg.show_lyrics_in_pill
                and self._media_lyric
                and (self._playing or bool(self._media_lyric))
                and not self._expanded
                and not strip_mode
            )
            self.game_hud.set_visible_metrics(
                cpu=bool(getattr(cfg, "show_game_cpu", True)),
                ram=bool(getattr(cfg, "show_game_ram", True)),
                gpu=bool(getattr(cfg, "show_game_gpu", True)),
            )
            self.game_hud.set_compact(in_game_now)
            self.game_hud.setVisible(in_game_now)
            src_ico_name = "spotify" if self._media_source_id == "spotify" else "music"
            self.source_icon_lbl.setPixmap(
                get_pixmap(src_ico_name, size=14, color="#F8FAFC", accent=self._media_source_color)
            )
            # In strip mode keep clean header branding without duplicating cover, title, transport, or lyrics
            self.source_icon_lbl.setVisible(strip_mode or (not lyrics_on and not in_game_now))
            if self._media_source_text and (strip_mode or (not in_game_now and not lyrics_on)):
                self.source_badge.setText(self._media_source_text)
                self.source_badge.setStyleSheet(
                    f"background: {self._media_source_color}38; color: #F8FAFC; "
                    f"border: 1px solid {self._media_source_color}88; border-radius: 9px; "
                    "padding: 0px 7px; font-size: 10px; font-weight: 700;"
                )
                self.source_badge.show()
            else:
                self.source_badge.hide()

            if strip_mode:
                self.pill_text.setMaximumWidth(16777215)
                self.pill_text.setText("")
                self.pill_lyric_lbl.hide()
            elif lyrics_on:
                self.pill_text.setMaximumWidth(108 if in_game_now else 145)
                self.pill_text.setText(self._media_song_only or self._media_title_full)
                self.pill_lyric_lbl.setMaximumWidth(330)
                elided_lyr = self.pill_lyric_lbl.fontMetrics().elidedText(
                    f"♪  {self._media_lyric}", Qt.TextElideMode.ElideRight, 316
                )
                self.pill_lyric_lbl.setText(elided_lyr)
                _apply_lyric_chip_style(self.pill_lyric_lbl, self.palette.accent, in_strip=False)
                self.pill_lyric_lbl.show()
            else:
                self.pill_text.setMaximumWidth(16777215)
                self.pill_text.setText(self._media_title_full)
                self.pill_lyric_lbl.hide()
            if not self._media_thumb.isNull() and not strip_mode:
                self.art_thumb.setPixmap(self._media_thumb)
                self.art_thumb.show()
            else:
                self.art_thumb.hide()
            show_mini = (
                (not self._expanded)
                and (not strip_mode)
                and (not in_game_now)
                and (self._hover_active or cfg.pill_mode == "miniplayer")
            )
            self.mini_controls.setVisible(show_mini)
            self.btn_play.setIcon(
                get_icon("pause" if self._playing else "play", size=13, color="#F2F5FA", accent=self.palette.accent)
            )
        elif self._in_game and not self._expanded:
            self.pill_lyric_lbl.hide()
            self.pill_text.setMaximumWidth(16777215)
            self.game_hud.set_compact(False)
            self.source_icon_lbl.hide()
            self.source_badge.setText(self._game_name or "GAME")
            self.source_badge.setStyleSheet(
                "background: rgba(56, 189, 248, 0.25); color: #F8FAFC; "
                "border: 1px solid rgba(56, 189, 248, 0.55); border-radius: 9px; "
                "padding: 0px 7px; font-size: 10px; font-weight: 700;"
            )
            self.source_badge.show()
            self.art_thumb.hide()
            self.mini_controls.hide()
            self.pill_text.setText("")
            self.game_hud.show()
        else:
            self.pill_lyric_lbl.hide()
            self.pill_text.setMaximumWidth(16777215)
            self.game_hud.hide()
            self.source_icon_lbl.hide()
            self.source_badge.hide()
            self.art_thumb.hide()
            self.mini_controls.hide()
            self.pill_text.setText(self._pill_brand_text())

        self._refresh_badges()
        full_style = self._is_builtin_full_pill()
        self.character.setVisible(not full_style)
        self.pill_text.setVisible(not full_style)
        self.clock_lbl.setVisible(not full_style)
        if full_style:
            self.battery_dot.hide()
            self.atmo_badge_btn.hide()
        self._sync_character_state()

    def _is_builtin_full_pill(self) -> bool:
        return bool(
            (not self._expanded)
            and (not self._music_strip_open)
            and self.character.character_id.startswith("builtin_")
            and (not self._activity_active)
            and (not self._has_media)
            and (not self._in_game)
            and (not self._timer_text)
        )

    def _pill_brand_text(self) -> str:
        from ..core.licensing import get_license_manager

        st = get_license_manager().get_status()
        hide_pro = bool(getattr(self.config_mgr.config, "hide_pro_badge", False))
        if st.is_pro:
            return "Blip Island" if hide_pro else "Blip Island PRO"
        return "Blip Island FREE"

    def update_media(self, snap: MediaStateSnapshot) -> None:
        cfg = self.config_mgr.config
        act = snap.active
        was_has_media = self._has_media
        self._has_media = bool(act and (act.title or act.artist))
        self._playing = bool(act and act.status == "Playing")
        has_spotify_session = any(s.source_id == "spotify" for s in (snap.sessions or []))

        if act and (act.title or act.artist):
            self._accent_hex = act.accent_color
            if act.accent_color and act.accent_color.startswith("#"):
                cfg.music_last_accent = act.accent_color
            self._media_source_id = act.source_id or ""
            self._media_source_color = act.accent_color or resolve_time_accent()
            self._media_source_text = (act.source_name or act.source_id or "Music").strip()
            if self._media_source_id == "spotify":
                self._spotify_sticky_hex = act.accent_color or self._spotify_sticky_hex
            elif self._playing:
                self._spotify_sticky_hex = ""

            qimg = self._art_lookup(act.art_key) if self._art_lookup and act.art_key else None
            dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
            if qimg is not None and not qimg.isNull():
                thumb_key = (act.art_key or "", qimg.cacheKey(), round(dpr, 2))
                if getattr(self, "_last_thumb_key", None) != thumb_key:
                    self._last_thumb_key = thumb_key
                    self._media_art_raw = QPixmap.fromImage(qimg)
                    self._media_thumb = _rounded_thumb(self._media_art_raw, 24, 6, dpr=dpr)
                    self._media_thumb_strip = _rounded_thumb(self._media_art_raw, 58, 12, dpr=dpr)
            else:
                self._last_thumb_key = None
                self._media_art_raw = QPixmap()
                self._media_thumb = QPixmap()
                self._media_thumb_strip = QPixmap()

            self._media_song = act.title or ""
            self._media_artist = act.artist or ""
            self._media_pos = float(act.position or 0.0)
            self._media_dur = float(act.duration or 0.0)
            self._media_song_only = act.title or act.artist or "Music"
            self._media_lyric = (snap.active_lyric or "").strip() if cfg.show_lyrics_in_pill else ""
            if act.artist and cfg.pill_mode != "compact":
                self._media_title_full = f"{act.title} — {act.artist}".strip(" —")
            else:
                self._media_title_full = self._media_song_only
            want_eq = bool(cfg.edge_equalizer and self._playing)
            if self.eq_mini.isVisible() != want_eq:
                self.eq_mini.setVisible(want_eq)
            art_sig = f"{act.art_key or ''}:{self._media_art_raw.width()}x{self._media_art_raw.height()}"
        else:
            if not has_spotify_session:
                self._spotify_sticky_hex = ""
            self._last_thumb_key = None
            self._media_song = ""
            self._media_artist = ""
            self._media_pos = 0.0
            self._media_dur = 0.0
            self._accent_hex = self._spotify_sticky_hex or resolve_time_accent()
            self._media_source_id = "spotify" if self._spotify_sticky_hex else ""
            self._media_source_text = ""
            self._media_song_only = ""
            self._media_lyric = ""
            self._media_art_raw = QPixmap()
            self._media_thumb = QPixmap()
            self._media_thumb_strip = QPixmap()
            self._media_title_full = "Blip Island"
            if self.eq_mini.isVisible():
                self.eq_mini.hide()
            art_sig = ""
            if self._music_strip_open:
                self._close_music_strip()

        if self._has_media:
            strip_title = self._media_song or self._media_title_full
            if self.strip_title_lbl.text() != strip_title:
                self.strip_title_lbl.setText(strip_title)
            src_txt = f" • {self._media_source_text}" if self._media_source_text else ""
            strip_artist = (self._media_artist or "Сейчас играет") + src_txt
            if self.strip_artist_lbl.text() != strip_artist:
                self.strip_artist_lbl.setText(strip_artist)
            lyr_acc = self._accent_hex or self.palette.accent
            strip_lyr_sig = (self._media_lyric, lyr_acc)
            if getattr(self, "_last_strip_lyr_sig", None) != strip_lyr_sig:
                self._last_strip_lyr_sig = strip_lyr_sig
                if self._media_lyric:
                    elided_s = self.strip_lyric_lbl.fontMetrics().elidedText(
                        f"♪  {self._media_lyric}", Qt.TextElideMode.ElideRight, 260
                    )
                    self.strip_lyric_lbl.setText(elided_s)
                    self.strip_lyric_lbl.setToolTip(self._media_lyric)
                    _apply_lyric_chip_style(self.strip_lyric_lbl, lyr_acc, in_strip=True)
                    self.strip_lyric_lbl.show()
                else:
                    self.strip_lyric_lbl.clear()
                    self.strip_lyric_lbl.setToolTip("")
                    self.strip_lyric_lbl.hide()
            strip_art_sig = getattr(self, "_last_thumb_key", None)
            if getattr(self, "_last_strip_art_sig", None) != strip_art_sig:
                self._last_strip_art_sig = strip_art_sig
                strip_pm = getattr(self, "_media_thumb_strip", QPixmap())
                if not strip_pm.isNull():
                    self.strip_art_lbl.setPixmap(strip_pm)
                else:
                    self.strip_art_lbl.clear()
            pos_txt = _fmt_sec(self._media_pos)
            if self.strip_pos_lbl.text() != pos_txt:
                self.strip_pos_lbl.setText(pos_txt)
            dur_txt = _fmt_sec(self._media_dur) if self._media_dur > 0 else "--:--"
            if self.strip_dur_lbl.text() != dur_txt:
                self.strip_dur_lbl.setText(dur_txt)
            if not self._strip_seeking:
                val = int(round(min(1.0, max(0.0, self._media_pos / self._media_dur)) * 1000)) if self._media_dur > 0 else 0
                if self.strip_seek.value() != val:
                    self.strip_seek.blockSignals(True)
                    self.strip_seek.setValue(val)
                    self.strip_seek.blockSignals(False)
            play_ico_sig = (self._playing, self.palette.accent)
            if getattr(self, "_last_strip_play_ico_sig", None) != play_ico_sig:
                self._last_strip_play_ico_sig = play_ico_sig
                self.strip_btn_play.setIcon(
                    get_icon("pause" if self._playing else "play", size=14, color="#F2F5FA", accent=self.palette.accent)
                )

        sig = (
            self._has_media,
            self._playing,
            self._accent_hex,
            self._spotify_sticky_hex,
            self._media_source_id,
            self._media_title_full,
            self._media_lyric,
            cfg.show_lyrics_in_pill,
            not self._media_thumb.isNull(),
            art_sig,
            self._expanded,
            self._in_game,
            cfg.pill_mode,
        )
        if sig != self._last_media_sig:
            self._last_media_sig = sig
            self._sync_palettes(force_panel=(self._effective_appearance_mode() == "music"))
            self._refresh_pill_views()
            self._sync_character_state()
            if hasattr(self.panel, "card_rain"):
                self.panel.card_rain.mark_underlay_dirty()
        if not self._expanded and was_has_media != self._has_media:
            self.reposition(animate=True)

    def update_spectrum(self, bands: list[float]) -> None:
        old_bands = self._music_bands
        if not self._playing:
            # Ignore Discord voice & other background audio completely whenever music playback is inactive
            self._music_bands = [x * 0.45 if x > 0.015 else 0.0 for x in old_bands]
            self._music_energy = self._music_energy * 0.45 if self._music_energy > 0.01 else 0.0
            self._music_bass = self._music_bass * 0.45 if self._music_bass > 0.01 else 0.0
            self._music_beat = self._music_beat * 0.45 if self._music_beat > 0.01 else 0.0
            self._music_aggro = self._music_aggro * 0.45 if self._music_aggro > 0.01 else 0.0
            self._music_vis_env = max(0.0, self._music_vis_env * 0.70 - 0.04)
            self.character.set_audio_level(0.0)
            self.eq_mini.set_bands([0.0] * 20, False)
            if self._music_vis_env > 0.008:
                self.update()
            return

        if bands:
            padded = (list(bands) + [0.0] * 20)[:20]
            raw_flux = sum(abs(max(0.0, min(1.0, float(new))) - old) for old, new in zip(old_bands, padded)) / 20.0
            # Smoothly track spectral wave heights for velvety bottom-edge vibration
            self._music_bands = [
                0.55 * old + 0.45 * max(0.0, min(1.0, float(new)))
                for old, new in zip(old_bands, padded)
            ]
        else:
            raw_flux = 0.0
            self._music_bands = [x * 0.72 for x in old_bands]

        bass_avg = sum(self._music_bands[:3]) / 3.0
        low_avg = sum(self._music_bands[:6]) / 6.0
        high_avg = sum(self._music_bands[11:]) / 9.0
        full_avg = sum(self._music_bands[:14]) / 14.0

        self._prev_energy = self._music_energy
        self._music_energy = max(0.0, min(1.0, 0.5 * self._music_energy + 0.5 * max(low_avg * 1.15, full_avg)))
        self._music_bass = max(0.0, min(1.0, 0.45 * self._music_bass + 0.55 * bass_avg))

        # Adaptive track dynamics from real un-smoothed transient flux + energy + bass + beat
        raw_aggro = max(
            0.0,
            min(
                1.0,
                raw_flux * 8.5
                + max(0.0, self._music_energy - 0.16) * 1.35
                + max(0.0, bass_avg - 0.18) * 0.90
                + max(0.0, high_avg - 0.12) * 0.65
                + self._music_beat * 0.55,
            ),
        )
        aggro_rate = 0.36 if raw_aggro > self._music_aggro else 0.08
        self._music_aggro = (1.0 - aggro_rate) * self._music_aggro + aggro_rate * raw_aggro

        if self._playing and self._music_energy > self._prev_energy + 0.05:
            jump = self._music_energy - self._prev_energy
            self._drop_wobble = max(-1.6, min(1.6, self._drop_wobble + 0.48))
            self._music_beat = min(1.25, self._music_beat + min(0.95, jump * 2.4 + 0.25))

        if self._playing:
            self._music_vis_env = min(1.0, self._music_vis_env + 0.12)
        else:
            self._music_vis_env = max(0.0, self._music_vis_env * 0.86 - 0.025)

        self.character.set_audio_level(self._music_energy if self._playing else 0.0)
        self.eq_mini.set_bands(bands, self._playing)
        if self._playing or self._music_vis_env > 0.008:
            self._music_phase += 0.22 + 0.36 * self._music_energy
            active_ms, _, _ = self._fps_timing()
            if self._watch_timer.interval() > active_ms:
                self._watch_timer.setInterval(active_ms)
            self.update()

    def update_timer_badge(self, remaining_sec: int, running: bool, label: str = "") -> None:
        had_timer = bool(self._timer_text)
        if running and remaining_sec > 0:
            m, s = divmod(remaining_sec, 60)
            short_lbl = f" {label[:8]}" if label else ""
            self._timer_text = f"{m}:{s:02d}{short_lbl}"
        else:
            self._timer_text = ""
        self._refresh_badges()
        if not self._expanded and had_timer != bool(self._timer_text):
            self.reposition(animate=True)

    def update_weather(self, rep: WeatherReport | None) -> None:
        if rep is None:
            return
        from ..core.i18n import get_language
        unit = "м/с" if get_language() == "ru" else "m/s"
        self._weather_text = f"{rep.temp_c:+.0f}° • {rep.wind_ms:.0f} {unit}"
        self._weather_symbol = rep.symbol or ""
        self._wind_ms = max(0.0, float(rep.wind_ms))
        self._precip_kind = rep.precip_kind if rep.precip_next_hour else ""
        self.character.set_precip(self._precip_kind if self.config_mgr.config.weather_rain_drip else "")
        self._sync_palettes(force_panel=True)
        self._refresh_badges()
        if hasattr(self.panel, "card_rain"):
            self.panel.card_rain.mark_underlay_dirty()
        self.update()

    def set_battery_state(self, percent: int | None, plugged: bool = False) -> None:
        vis_changed = self.battery_dot.set_battery(percent, plugged)
        if vis_changed and not self._expanded:
            self.reposition(animate=False)

    def update_system_badges(self, snap: SystemStatsSnapshot) -> None:
        self.set_battery_state(snap.battery_percent, snap.battery_plugged)
        if snap.cam_in_use:
            self.privacy_dot.setStyleSheet("background: #22C55E; border-radius: 5px;")
            self.privacy_dot.setToolTip(f"Camera: {snap.cam_app}")
            self.privacy_dot.show()
        elif snap.mic_in_use:
            self.privacy_dot.setStyleSheet("background: #EF4444; border-radius: 5px;")
            self.privacy_dot.setToolTip(f"Microphone: {snap.mic_app}")
            self.privacy_dot.show()
        else:
            self.privacy_dot.hide()

        from ..core.licensing import get_license_manager

        cfg = self.config_mgr.config
        show_game_cfg = bool(getattr(cfg, "show_game_stats_in_pill", True)) and bool(
            getattr(cfg, "show_game_cpu", True)
            or getattr(cfg, "show_game_ram", True)
            or getattr(cfg, "show_game_gpu", True)
        )
        was_in_game = self._in_game
        self._in_game = bool(snap.in_game) and show_game_cfg and get_license_manager().is_feature_allowed("game_stats")
        self._game_name = snap.active_game_name or ""
        self.game_hud.set_visible_metrics(
            cpu=bool(getattr(cfg, "show_game_cpu", True)),
            ram=bool(getattr(cfg, "show_game_ram", True)),
            gpu=bool(getattr(cfg, "show_game_gpu", True)),
        )
        self.game_hud.push_sample(snap.cpu_percent, snap.ram_percent, snap.gpu_load, self._game_name)
        if self._in_game and not self.isVisible() and not self.isMinimized():
            self.show()
        if was_in_game != self._in_game or self._in_game:
            self._refresh_pill_views()
            self._sync_character_state()
            if was_in_game != self._in_game and not self._expanded:
                self.reposition(animate=True)

    def set_recording_indicator(self, active: bool) -> None:
        self._recording = active
        self._refresh_badges()
        full_style = self._is_builtin_full_pill()
        self.character.setVisible(not full_style)
        self.pill_text.setVisible(not full_style)
        self.clock_lbl.setVisible(not full_style)
        if full_style:
            self.battery_dot.hide()
            self.atmo_badge_btn.hide()
        self._sync_character_state()
        self._refresh_pill_views()

    def set_dictating_indicator(self, active: bool) -> None:
        self._dictating = active
        self._refresh_badges()
        full_style = self._is_builtin_full_pill()
        self.character.setVisible(not full_style)
        self.pill_text.setVisible(not full_style)
        self.clock_lbl.setVisible(not full_style)
        if full_style:
            self.battery_dot.hide()
            self.atmo_badge_btn.hide()
        self._sync_character_state()
        self._refresh_pill_views()

    def set_ai_thinking(self, active: bool) -> None:
        self.character.set_thinking(active)

    def set_timer_alarm(self, active: bool) -> None:
        self._alarm_active = bool(active)
        if hasattr(self.character, "set_alarm"):
            self.character.set_alarm(self._alarm_active)
        if active:
            self._cancel_hide_immediately()
            self.trigger_drop_impulse(1.1)
        self.update()

    def _sync_character_state(self) -> None:
        if hasattr(self.character, "set_alarm"):
            self.character.set_alarm(self._alarm_active)
        if self._recording or self._dictating or self._activity_active:
            state = "working"
        elif self._in_game:
            state = "game"
        elif self._playing:
            state = "music"
        elif self._hover_active:
            state = "curious"
        else:
            hour = datetime.now().hour
            idle_sec = get_idle_seconds()
            if hour >= 23 or hour < 7 or idle_sec >= 60.0:
                state = "sleeping"
            else:
                state = "idle"
        self.character.set_activity_state(state)

    def _refresh_badges(self) -> None:
        parts: list[str] = []
        if self._recording:
            parts.append("REC")
        if self._dictating:
            parts.append("MIC")
        if self._timer_text:
            parts.append(self._timer_text)
        if (
            self.config_mgr.config.show_weather_in_pill
            and self._weather_text
            and not self._expanded
            and not self._activity_active
            and not self._has_media
        ):
            parts.append(self._weather_text)
        if parts:
            self.badge_lbl.setText("  ".join(parts))
            self.badge_lbl.show()
        else:
            self.badge_lbl.hide()

        show_clock = (not self._expanded) and (not self._activity_active) and (not self._in_game)
        self.clock_lbl.setText(datetime.now().strftime("%H:%M"))
        self.clock_lbl.setStyleSheet(f"color: {self.palette.text_secondary}; font-size: 11px; font-weight: 600;")
        self.clock_lbl.setVisible(show_clock)

        show_atmo = False
        self.atmo_badge_btn.setVisible(False)
        self._celestial_spacer.setVisible(self._expanded)
        if show_atmo:
            from ..core.licensing import get_license_manager

            cfg = self.config_mgr.config
            eff_weather = (
                "clear"
                if (
                    cfg.atmosphere_weather_mode == "auto"
                    and not get_license_manager().is_feature_allowed("realtime_weather")
                )
                else cfg.atmosphere_weather_mode
            )
            self.atmo_badge_btn.setText(
                describe_atmosphere_badge(
                    cfg.atmosphere_preview,
                    self._weather_symbol,
                    lat=cfg.weather_lat,
                    lon=cfg.weather_lon,
                    sky_mode=cfg.atmosphere_sky_mode,
                    weather_mode=eff_weather,
                    moon_mode=cfg.atmosphere_moon_mode,
                )
            )
            self.atmo_badge_btn.setStyleSheet(
                f"QPushButton {{ background: {self.palette.bg_surface}; color: {self.palette.text_primary}; "
                f"border: 1px solid {self.palette.border}; border-radius: 11px; "
                "padding: 2px 9px; font-size: 10.5px; font-weight: 600; }}"
                f"QPushButton:hover {{ border-color: {self.palette.accent}; }}"
            )

    def _on_character_poked(self) -> None:
        if not self._expanded:
            self.set_expanded(True)
        self.panel.switch_tab_by_key("character")

    def _cycle_atmosphere_preview(self) -> None:
        modes = [k for k, _ in ATMO_MODES]
        cfg = self.config_mgr.config
        cur = (cfg.atmosphere_preview or "auto").strip().lower()
        nxt = modes[(modes.index(cur) + 1) % len(modes)] if cur in modes else modes[0]
        cfg.atmosphere_preview = nxt
        cfg.atmosphere_sky_mode = "auto"
        cfg.atmosphere_weather_mode = "auto"
        if nxt == "night_full_moon":
            cfg.atmosphere_moon_mode = "full"
        elif nxt == "night_new_moon":
            cfg.atmosphere_moon_mode = "new"
        elif nxt == "night_crescent":
            cfg.atmosphere_moon_mode = "waxing_crescent"
        else:
            cfg.atmosphere_moon_mode = "auto"
        self.config_mgr.save()
        if hasattr(self.panel, "weather_tab"):
            wt = self.panel.weather_tab
            if hasattr(wt, "sync_atmosphere_combo"):
                wt.sync_atmosphere_combo()
            if hasattr(wt, "atmosphere_changed"):
                wt.atmosphere_changed.emit()
                return
        self.apply_config()

    def _fps_timing(self) -> tuple[int, int, float]:
        """Return (active_ms, idle_ms, min_paint_dt) from configured target_fps (30|60|120|240)."""
        raw = int(getattr(self.config_mgr.config, "target_fps", 120) or 120)
        fps = min((30, 60, 120, 240), key=lambda v: abs(v - raw))
        active_ms = max(4, int(round(1000.0 / float(fps))))
        idle_ms = max(active_ms, 16 if fps >= 60 else 33)
        min_paint_dt = max(0.0038, (1.0 / float(fps)) * 0.90)
        return active_ms, idle_ms, min_paint_dt

    # ------------------------------------------------------------------ Liquid Droplet Physics
    def trigger_drop_impulse(self, strength: float = 0.8) -> None:
        """Apply a momentary surface-tension liquid ripple."""
        self._drop_wobble = max(-1.8, min(1.8, self._drop_wobble + strength))
        self._drop_vel += strength * 0.50
        active_ms, _, _ = self._fps_timing()
        if self._watch_timer.interval() > active_ms:
            self._watch_timer.setInterval(active_ms)

    def _update_liquid_physics(self, global_cursor: QPointF, *, _from_timer: bool = False) -> None:
        suspended_full = (
            bool(getattr(self, "_fg_screen_covered", False))
            or self._hide_state == "hidden"
            or self.isMinimized()
        )
        if suspended_full and self._hide_state not in ("hiding", "showing") and self._anim.state() != QPropertyAnimation.State.Running:
            now_s = time.monotonic()
            self._last_atmo_ts = now_s
            self._last_liquid_ts = now_s
            if (
                self._drop_amp != 0.0
                or self._drop_vel != 0.0
                or self._indent_amp != 0.0
                or self._indent_vel != 0.0
                or self._drop_wobble != 0.0
                or self._snap_shake_amp != 0.0
                or self._fluid_swirl != 0.0
            ):
                self._drop_amp = 0.0
                self._drop_vel = 0.0
                self._indent_amp = 0.0
                self._indent_vel = 0.0
                self._drop_wobble = 0.0
                self._snap_shake_amp = 0.0
                self._fluid_vx = 0.0
                self._fluid_vy = 0.0
                self._fluid_swirl = 0.0
            if self._watch_timer.timerType() != Qt.TimerType.CoarseTimer:
                self._watch_timer.setTimerType(Qt.TimerType.CoarseTimer)
            if self._watch_timer.interval() != 320:
                self._watch_timer.setInterval(320)
            return

        cfg = self.config_mgr.config
        local = QPointF(float(global_cursor.x() - self.x()), float(global_cursor.y() - self.y()))
        self._drop_cursor_local = local
        core = self._core_rect()

        if not cfg.liquid_drop:
            if (
                self._drop_amp != 0.0
                or self._drop_wobble != 0.0
                or self._snap_shake_amp != 0.0
                or self._fluid_swirl != 0.0
                or self._fluid_vx != 0.0
                or self._fluid_vy != 0.0
            ):
                self._drop_amp = 0.0
                self._drop_wobble = 0.0
                self._snap_shake_amp = 0.0
                self._tether_latched = False
                self._tether_cooldown = 0.0
                self._lmb_was_down = False
                self._fluid_vx = 0.0
                self._drop_dist = 999.0
                self._prev_drop_dist = 999.0
                self._pushing_in = False
                self._indent_amp = 0.0
                self._indent_vel = 0.0
                self._fluid_vy = 0.0
                self._fluid_swirl = 0.0
                if (self._panel_shake_dx, self._panel_shake_dy) != (0, 0):
                    self._panel_shake_dx = 0
                    self._panel_shake_dy = 0
                self.update()
            now_a = time.monotonic()
            dt_a = max(0.001, min(0.08, now_a - self._last_atmo_ts))
            self._last_atmo_ts = now_a
            self._last_liquid_ts = now_a
            step_scale = dt_a / 0.028
            self._music_beat = self._music_beat * (0.86 ** step_scale) if self._playing else 0.0
            if self._playing or self._expanded:
                if self._playing:
                    self._music_phase += (0.24 + 0.36 * self._music_energy) * step_scale
                rain_anim, atmo_rain_anim = self._step_card_rain(now_a)
                if self._expanded:
                    self._atmo_phase += dt_a * 1.12
                    eff_wind = self._wind_ms
                    self._cloud_phase += dt_a * 1.12 * (0.08 + min(35.0, max(0.0, eff_wind)) * 0.52)
                active_ms, idle_ms, min_paint_dt = self._fps_timing()
                scroll_active = bool(getattr(self.panel, "_smooth_scroll_active", False))
                if self._playing or self._atmo_anim.is_transitioning() or scroll_active:
                    desired = active_ms
                elif rain_anim:
                    desired = active_ms
                elif atmo_rain_anim:
                    desired = idle_ms
                elif self._expanded:
                    desired = idle_ms
                else:
                    desired = 95
                want_timer_type = Qt.TimerType.PreciseTimer if desired < 16 else Qt.TimerType.CoarseTimer
                if self._watch_timer.timerType() != want_timer_type:
                    self._watch_timer.setTimerType(want_timer_type)
                if self._watch_timer.interval() != desired:
                    self._watch_timer.setInterval(desired)
                use_atmo = self._use_weather_appearance()
                if self._playing or self._atmo_anim.is_transitioning() or scroll_active or rain_anim:
                    if (now_a - self._last_ui_paint_ts) >= min_paint_dt:
                        self._last_ui_paint_ts = now_a
                        self.update()
                elif atmo_rain_anim and (now_a - self._last_ui_paint_ts) >= max(min_paint_dt, 0.016):
                    self._last_ui_paint_ts = now_a
                    self.update()
                elif not self._expanded and (now_a - self._last_ui_paint_ts) >= 0.45:
                    self._last_ui_paint_ts = now_a
                    self.update()
                elif self._expanded and use_atmo and (now_a - self._last_ui_paint_ts) >= max(min_paint_dt, 0.022):
                    self._last_ui_paint_ts = now_a
                    self.update()
            elif not self.isVisible():
                self._step_card_rain(now_a)
                if self._watch_timer.interval() != 220:
                    self._watch_timer.setInterval(220)
            else:
                self._step_card_rain(now_a)
                if self._watch_timer.interval() != 95:
                    self._watch_timer.setInterval(95)
            return

        now_l = time.monotonic()
        raw_dt_l = now_l - self._last_liquid_ts
        if _from_timer:
            dt_l = max(0.002, min(0.08, raw_dt_l))
        else:
            dt_l = 0.028 if raw_dt_l < 0.012 else min(0.08, raw_dt_l)
        self._last_liquid_ts = now_l
        step_scale = dt_l / 0.028

        # Closest point on core perimeter to cursor
        nx = max(core.left(), min(core.right(), local.x()))
        ny = max(core.top(), min(core.bottom(), local.y()))
        vx = local.x() - nx
        vy = local.y() - ny
        dist = math.hypot(vx, vy)
        prev_dist = self._prev_drop_dist
        raw_radial_v = ((dist - prev_dist) / max(0.25, step_scale)) if prev_dist < 900.0 else 0.0
        self._prev_drop_dist = dist
        self._drop_dist = dist

        lmb_down = is_left_mouse_button_down() if sys.platform == "win32" else False

        if self._tether_cooldown > 0.0:
            self._tether_cooldown = max(0.0, self._tether_cooldown - dt_l)
            if dist > 115.0:
                self._tether_cooldown = 0.0

        if dist <= 0.5:
            dl = local.x() - core.left()
            dr = core.right() - local.x()
            dt = local.y() - core.top()
            db = core.bottom() - local.y()
            m_in = min(dl, dr, dt, db)
            if m_in == dl:
                edge_pt = QPointF(core.left(), max(core.top(), min(core.bottom(), local.y())))
                edge_nx, edge_ny = -1.0, 0.0
            elif m_in == dr:
                edge_pt = QPointF(core.right(), max(core.top(), min(core.bottom(), local.y())))
                edge_nx, edge_ny = 1.0, 0.0
            elif m_in == dt:
                edge_pt = QPointF(max(core.left(), min(core.right(), local.x())), core.top())
                edge_nx, edge_ny = 0.0, -1.0
            else:
                edge_pt = QPointF(max(core.left(), min(core.right(), local.x())), core.bottom())
                edge_nx, edge_ny = 0.0, 1.0
        else:
            m_in = 0.0
            edge_pt = QPointF(nx, ny)
            edge_nx = vx / dist
            edge_ny = vy / dist

        catch_dist = 34.0 if self._expanded else 22.0   # tight magnetic catch around visible island
        break_dist = 52.0 if self._expanded else 38.0   # calm stretch distance before droplet snaps & ricochets
        target_amp = 0.0
        target_indent = 0.0
        active_vis = self.isVisible() and not self.isMinimized()
        fast_exit = prev_dist < 900.0 and prev_dist <= 22.0 and 0.5 < dist <= 135.0 and raw_radial_v >= 2.0
        fast_enter = prev_dist < 900.0 and prev_dist > 1.5 and dist <= 0.5 and raw_radial_v <= -4.0

        # Directional Push-In Dent with hysteresis (eliminates sharp edge tremor when hovering still)
        if active_vis:
            if (
                (0.5 <= dist <= 18.0 and raw_radial_v < -2.6) or fast_enter
            ) and (not self._tether_latched or self._drop_amp < 0.35):
                self._tether_latched = False
                self._pushing_in = True
                self._indent_anchor = QPointF(edge_pt)
                self._indent_nx, self._indent_ny = edge_nx, edge_ny
                if fast_enter:
                    self._indent_vel = max(self._indent_vel, 0.28)
                    self._fluid_swirl = min(1.35, self._fluid_swirl + 0.45)
                target_indent = max(0.28, min(0.95, (20.0 - dist) / 18.0))
            elif self._pushing_in:
                if (dist <= 0.5 and m_in <= 24.0) or (0.5 <= dist <= 18.0 and raw_radial_v <= 1.8):
                    self._indent_anchor = QPointF(edge_pt)
                    self._indent_nx, self._indent_ny = edge_nx, edge_ny
                    target_indent = min(0.95, 0.38 + m_in / 34.0) if dist <= 0.5 else max(0.16, 1.0 - dist / 22.0)
                else:
                    self._pushing_in = False
        else:
            self._pushing_in = False

        if not self._tether_latched and not self._pushing_in:
            if active_vis and (
                (self._tether_cooldown <= 0.0 and 1.5 <= dist <= catch_dist and raw_radial_v >= -0.8)
                or fast_exit
            ):
                self._tether_latched = True
                self._fast_tether = bool(fast_exit)
                self._tether_cooldown = 0.0
                self._tether_anchor = QPointF(edge_pt)
                self._drop_dir_x, self._drop_dir_y = edge_nx, edge_ny
                if fast_exit:
                    kick_amp = min(1.08, max(0.36, dist / 95.0))
                    self._drop_amp = max(self._drop_amp, kick_amp * 0.78)
                    self._drop_vel = max(self._drop_vel, 0.28)
                    self._fluid_swirl = min(1.35, self._fluid_swirl + 0.35)
            elif active_vis and self._tether_cooldown <= 0.0 and dist <= 0.5 and m_in <= 6.0:
                self._tether_anchor = QPointF(edge_pt)
                ox = edge_pt.x() - core.center().x()
                oy = edge_pt.y() - core.center().y()
                omag = max(1e-4, math.hypot(ox, oy))
                self._drop_dir_x = ox / omag
                self._drop_dir_y = oy / omag
                target_amp = 0.12 * max(0.0, 1.0 - m_in / 6.0)

        cur_dx = 0.0
        cur_dy = 0.0
        if self._prev_cursor_local.x() > -900.0:
            cur_dx = max(-45.0, min(45.0, local.x() - self._prev_cursor_local.x()))
            cur_dy = max(-45.0, min(45.0, local.y() - self._prev_cursor_local.y()))

        if self._tether_latched:
            released_btn = self._lmb_was_down and not lmb_down and self._drop_amp > 0.16
            if not active_vis or (dist <= 0.5 and m_in > 18.0):
                self._tether_latched = False
                self._fast_tether = False
                target_amp = 0.0
            else:
                slide_rate = 1.0 - (0.76 ** step_scale)
                ax = self._tether_anchor.x() + (edge_pt.x() - self._tether_anchor.x()) * slide_rate
                ay = self._tether_anchor.y() + (edge_pt.y() - self._tether_anchor.y()) * slide_rate
                self._tether_anchor = QPointF(
                    max(core.left(), min(core.right(), ax)),
                    max(core.top(), min(core.bottom(), ay)),
                )
                dx_a = local.x() - self._tether_anchor.x()
                dy_a = local.y() - self._tether_anchor.y()
                pull_len = math.hypot(dx_a, dy_a)
                if raw_radial_v >= 2.5:
                    self._fast_tether = True
                eff_break = 148.0 if getattr(self, "_fast_tether", False) else break_dist

                if pull_len > eff_break or released_btn:
                    snap_str = max(0.75, min(1.25, self._drop_amp))
                    self._tether_latched = False
                    self._fast_tether = False
                    self._tether_cooldown = 0.55
                    self._drop_vel = -0.30 * snap_str
                    target_amp = 0.0
                    self._indent_anchor = QPointF(self._tether_anchor)
                    self._indent_nx, self._indent_ny = self._drop_dir_x, self._drop_dir_y
                    self._recoil_indent = min(1.05, 0.82 * snap_str)
                    self._drop_wobble = 1.35 * snap_str
                    self._snap_shake_amp = 1.15 * snap_str
                    self._snap_shake_phase = 0.0
                    self._fluid_vx -= self._drop_dir_x * 24.0 * snap_str
                    self._fluid_vy -= self._drop_dir_y * 20.0 * snap_str
                    self._fluid_swirl = min(1.35, self._fluid_swirl + 0.85 * snap_str)
                    if hasattr(self, "_char_widget") and self._char_widget is not None:
                        self._char_widget.poke(min(1.0, snap_str))
                else:
                    if pull_len > 1.2:
                        tx, ty = dx_a / pull_len, dy_a / pull_len
                    elif dist > 1.2:
                        tx, ty = vx / dist, vy / dist
                    else:
                        tx, ty = self._drop_dir_x, self._drop_dir_y
                    dir_rate = 1.0 - (0.76 ** step_scale)
                    self._drop_dir_x += (tx - self._drop_dir_x) * dir_rate
                    self._drop_dir_y += (ty - self._drop_dir_y) * dir_rate
                    dmag = math.hypot(self._drop_dir_x, self._drop_dir_y)
                    if dmag > 1e-4:
                        self._drop_dir_x /= dmag
                        self._drop_dir_y /= dmag
                    target_amp = min(1.08, max(0.16, (pull_len + 5.0) / 58.0))
        self._lmb_was_down = lmb_down

        # Calm fluid momentum: zero sinusoidal standing vibration when cursor hovers still!
        ambient_range = 120.0 if self._expanded else 44.0
        near_fluid = active_vis and dist <= ambient_range
        if near_fluid:
            tx_norm = max(0.0, min(1.0, (local.x() - core.left()) / max(1.0, core.width())))
            ty_norm = max(0.0, min(1.0, (local.y() - core.top()) / max(1.0, core.height())))
            norm_rate = 1.0 - (0.84 ** step_scale)
            self._fluid_x_norm += (tx_norm - self._fluid_x_norm) * norm_rate
            self._fluid_y_norm += (ty_norm - self._fluid_y_norm) * norm_rate
            vx_scaled = cur_dx / max(0.25, step_scale)
            vy_scaled = cur_dy / max(0.25, step_scale)
            near_w = 1.0 if dist <= 0.5 else max(0.20, 1.0 - dist / ambient_range)
            speed_s = math.hypot(vx_scaled, vy_scaled)
            f_decay = 0.85 ** step_scale
            self._fluid_vx = self._fluid_vx * f_decay + vx_scaled * 0.24 * near_w * step_scale
            self._fluid_vy = self._fluid_vy * f_decay + vy_scaled * 0.22 * near_w * step_scale
            target_swirl = min(1.25, near_w * max(0.0, speed_s - 0.65) * 0.045)
            base_rate = 0.14 if target_swirl > self._fluid_swirl else 0.065
            swirl_rate = 1.0 - ((1.0 - base_rate) ** step_scale)
            self._fluid_swirl += (target_swirl - self._fluid_swirl) * swirl_rate
        else:
            f_decay = 0.88 ** step_scale
            s_decay = 0.90 ** step_scale
            self._fluid_vx *= f_decay
            self._fluid_vy *= f_decay
            self._fluid_swirl *= s_decay
            if abs(self._fluid_vx) < 0.05:
                self._fluid_vx = 0.0
            if abs(self._fluid_vy) < 0.05:
                self._fluid_vy = 0.0
            if self._fluid_swirl < 0.008:
                self._fluid_swirl = 0.0
        self._prev_cursor_local = QPointF(local)
        self._music_beat = self._music_beat * (0.86 ** step_scale) if self._playing else 0.0

        recoil_i = float(getattr(self, "_recoil_indent", 0.0))
        if recoil_i > 0.01 and not self._pushing_in:
            if self._drop_amp <= 0.16:
                target_indent = max(target_indent, recoil_i)
                self._indent_vel = max(self._indent_vel, 0.11 * recoil_i * step_scale)
                recoil_i *= 0.90 ** step_scale
            else:
                target_indent = max(target_indent, recoil_i * 0.25)
            self._recoil_indent = 0.0 if recoil_i < 0.015 else recoil_i

        # Viscous slime dynamics scaled cleanly by real elapsed dt for 240 FPS parity
        if target_indent > self._indent_amp:
            self._indent_vel = (self._indent_vel + (target_indent - self._indent_amp) * 0.22 * step_scale) * (0.80 ** step_scale)
        else:
            self._indent_vel = (self._indent_vel + (target_indent - self._indent_amp) * 0.08 * step_scale) * (0.87 ** step_scale)
        self._indent_amp = max(0.0, min(1.05, self._indent_amp + self._indent_vel * step_scale))

        if target_amp > self._drop_amp:
            force = (target_amp - self._drop_amp) * 0.125 * step_scale
            self._drop_vel = (self._drop_vel + force) * (0.82 ** step_scale)
        else:
            force = (target_amp - self._drop_amp) * 0.042 * step_scale
            self._drop_vel = (self._drop_vel + force) * (0.905 ** step_scale)
        self._drop_amp = max(0.0, min(1.35, self._drop_amp + self._drop_vel * step_scale))
        if self._drop_amp <= 0.0 and self._drop_vel < 0.0 and target_amp == 0.0:
            self._drop_vel = 0.0
        self._drop_wobble *= 0.88 ** step_scale
        if abs(self._drop_wobble) < 0.025 and target_amp == 0.0:
            self._drop_wobble = 0.0
        self._drop_phase += 0.12 * step_scale
        if self._drop_amp < 0.004 and abs(self._drop_vel) < 0.008 and target_amp == 0.0:
            self._drop_amp = 0.0
            self._drop_vel = 0.0
        if self._indent_amp < 0.004 and abs(self._indent_vel) < 0.008 and target_indent == 0.0:
            self._indent_amp = 0.0
            self._indent_vel = 0.0

        # Never translate the inner sky/earth/weather canvas during cursor hover or membrane pull
        if self._snap_shake_amp > 0.015:
            self._snap_shake_amp *= 0.92 ** step_scale
            self._snap_shake_phase += 0.56 * step_scale
            sx = int(round(math.sin(self._snap_shake_phase) * self._snap_shake_amp * 2.8))
            sy = int(round(math.cos(self._snap_shake_phase * 1.15) * self._snap_shake_amp * 2.4))
        else:
            self._snap_shake_amp = 0.0
            sx, sy = 0, 0
        sx = max(-6, min(6, sx))
        sy = max(-4, min(4, sy))
        self._panel_shake_dx, self._panel_shake_dy = sx, sy

        if self._playing:
            self._music_vis_env = min(1.0, self._music_vis_env + 0.08 * step_scale)
            self._music_phase += (0.22 + 0.36 * self._music_energy) * step_scale
        elif self._music_vis_env > 0.0:
            self._music_vis_env = max(0.0, self._music_vis_env * (0.86 ** step_scale) - 0.03 * step_scale)
            self._music_bass *= 0.85 ** step_scale
            self._music_energy *= 0.85 ** step_scale
            self._music_phase += 0.12 * step_scale
        now_a = time.monotonic()
        dt_a = max(0.0, min(0.08, now_a - self._last_atmo_ts))
        self._last_atmo_ts = now_a
        if self._expanded:
            self._atmo_phase += dt_a * 1.12
            eff_wind = self._wind_ms
            self._cloud_phase += dt_a * 1.12 * (0.08 + min(35.0, max(0.0, eff_wind)) * 0.52)

        near_grass = (
            self._expanded
            and core.left() <= local.x() <= core.right()
            and core.bottom() - 50.0 <= local.y() <= core.bottom()
        )
        near_sky = (
            self._expanded
            and core.left() - 36.0 <= local.x() <= core.right() + 36.0
            and core.top() - 28.0 <= local.y() <= core.top() + 145.0
        )
        self._last_near_grass = near_grass
        self._last_near_sky = near_sky
        cursor_moved = abs(cur_dx) > 0.35 or abs(cur_dy) > 0.35
        active_physics = (
            abs(self._drop_amp) > 0.008
            or abs(self._drop_vel) > 0.008
            or abs(self._indent_amp) > 0.008
            or abs(self._indent_vel) > 0.008
            or abs(self._drop_wobble) > 0.025
            or self._snap_shake_amp > 0.015
            or (dist < catch_dist and self._tether_cooldown <= 0.0 and dist > 0.5)
            or (near_fluid and cursor_moved)
            or self._fluid_swirl > 0.012
            or abs(self._fluid_vx) > 0.15
            or abs(self._fluid_vy) > 0.15
            or (near_grass and cursor_moved)
            or self._grass_sim.is_settling()
            or (near_sky and cursor_moved)
            or self._cloud_sim.is_dispersing()
            or self._atmo_anim.is_transitioning()
            or self._music_vis_env > 0.008
            or self._hide_state in ("hiding", "showing")
        )
        rain_anim, atmo_rain_anim = self._step_card_rain(now_a)
        scroll_active = bool(getattr(self.panel, "_smooth_scroll_active", False))
        edge_proximity = (dist < 135.0) if not self._expanded else (0.5 < dist < 135.0)
        active_ms, idle_ms, min_paint_dt = self._fps_timing()
        if not self.isVisible():
            desired_ms = 240
        elif self._hide_state in ("hiding", "showing") or self._anim.state() == QPropertyAnimation.State.Running:
            desired_ms = active_ms
        elif active_physics or scroll_active or (not self._expanded and edge_proximity):
            desired_ms = active_ms
        elif self._playing or self._music_vis_env > 0.008:
            desired_ms = active_ms
        elif rain_anim:
            desired_ms = active_ms
        elif atmo_rain_anim:
            desired_ms = idle_ms
        elif self._expanded:
            desired_ms = idle_ms
        else:
            desired_ms = 95
        want_timer_type = Qt.TimerType.PreciseTimer if desired_ms < 16 else Qt.TimerType.CoarseTimer
        if self._watch_timer.timerType() != want_timer_type:
            self._watch_timer.setTimerType(want_timer_type)
        if self._watch_timer.interval() != desired_ms:
            self._watch_timer.setInterval(desired_ms)
        collapsed_synced = (not self._expanded) and (active_physics or self._playing) and self.pill_bar.isVisible()
        if self.isVisible() and (self._expanded or collapsed_synced):
            dt_scale = (dt_a / 0.033) if self._expanded else ((dt_a / 0.033) * 0.50)
            self.character.step_frame_synced(dt_scale)
        has_video_bg = bool(
            self._expanded
            and self._effective_appearance_mode() in ("image", "video")
            and (getattr(self, "_custom_bg_cv_cap", None) is not None or getattr(self, "_custom_bg_movie", None) is not None)
        )
        if active_physics or scroll_active or self._playing or rain_anim or self._anim.state() == QPropertyAnimation.State.Running:
            if (now_a - self._last_ui_paint_ts) >= min_paint_dt:
                self._last_ui_paint_ts = now_a
                self.update()
        elif atmo_rain_anim and not has_video_bg and (now_a - self._last_ui_paint_ts) >= max(min_paint_dt, 0.016):
            self._last_ui_paint_ts = now_a
            self.update()
        elif self._expanded and not has_video_bg and self._use_weather_appearance() and (now_a - self._last_ui_paint_ts) >= max(min_paint_dt, 0.022):
            self._last_ui_paint_ts = now_a
            self.update()

    # ------------------------------------------------------------------ Interactions & Auto-hide
    def _on_watch_tick(self) -> None:
        pos = QCursor.pos()
        if self._is_builtin_full_pill():
            c_glob = self.mapToGlobal(self._core_rect().center().toPoint())
            cx = float(c_glob.x())
            cy = float(c_glob.y())
        elif self.character.isVisible():
            c_glob = self.character.mapToGlobal(self.character.rect().center())
            cx = float(c_glob.x())
            cy = float(c_glob.y())
        else:
            core = self._core_rect()
            cx = self.x() + core.center().x()
            cy = self.y() + core.center().y()
        if not (self._hide_state == "hidden" or self.isMinimized()):
            core_now = self._core_rect()
            over_core_now = core_now.adjusted(-8, -8, 8, 8).contains(
                QPointF(float(pos.x() - self.x()), float(pos.y() - self.y()))
            )
            if self.isVisible() and self._hide_state == "shown" and over_core_now != self._hover_active:
                self._hover_active = over_core_now
                self._sync_character_state()
                self._refresh_pill_views()
                if not self._expanded and not self._music_strip_open and self._has_media:
                    self.reposition(animate=True)
            dx = max(-1.0, min(1.0, (pos.x() - cx) / 320.0))
            dy = max(-1.0, min(1.0, (pos.y() - cy) / 220.0))
            prev_c = getattr(self, "_last_char_norm", (-9.0, -9.0))
            if abs(dx - prev_c[0]) > 0.015 or abs(dy - prev_c[1]) > 0.015:
                self._last_char_norm = (dx, dy)
                self.character.set_cursor_norm(dx, dy)
                if self._is_builtin_full_pill():
                    self.update()
        self._update_liquid_physics(QPointF(pos.x(), pos.y()), _from_timer=True)

        # Global click-outside-to-collapse detector (survives frameless focus quirks)
        lmb = is_left_mouse_button_down()
        if lmb and not self._prev_lmb_down and (self._expanded or self._music_strip_open):
            self._on_outside_click(pos)
        self._prev_lmb_down = lmb

        # Collapsed/partially-open character animation is stepped smoothly via real elapsed dt
        # in _update_liquid_physics (when playing/animating) or CharacterWidget._tick (when idle).

        self._fg_tick += 1

        now_m = time.monotonic()

        # Caps Lock + keyboard layout transient pill notifications (gated cleanly in time)
        if (now_m - self._last_caps_poll_ts) >= 0.24:
            self._last_caps_poll_ts = now_m
            caps_now = is_caps_lock_on()
            if caps_now != self._last_caps_on:
                self._last_caps_on = caps_now
                self.show_activity(
                    badge="CAPS",
                    text="Caps Lock: ВКЛ" if caps_now else "Caps Lock: ВЫКЛ",
                    accent_hex="#F59E0B" if caps_now else "#64748B",
                    duration_ms=1700,
                    icon_name="caps",
                )
            kb = get_keyboard_layout_code()
            if kb:
                if self._last_kb_layout and kb != self._last_kb_layout:
                    self._last_kb_layout = kb
                    self.show_activity(
                        badge=kb,
                        text=f"Раскладка: {kb}",
                        accent_hex="#38BDF8",
                        duration_ms=2200,
                        icon_name="keyboard",
                    )
                elif not self._last_kb_layout:
                    self._last_kb_layout = kb

        # Circadian time-of-day palette refresh, clock HH:MM sync & character idle/sleeping update
        if self._fg_tick % 30 == 0:
            now_dt = datetime.now()
            hm = now_dt.strftime("%H:%M")
            if hm != self._last_clock_str:
                self._last_clock_str = hm
                self.clock_lbl.setText(hm)
            time_bucket = now_dt.hour * 12 + now_dt.minute // 5
            if time_bucket != self._last_time_bucket:
                self._last_time_bucket = time_bucket
                self._sync_palettes()
            self._sync_character_state()
            self._refresh_pill_views()

        self._step_auto_hide_fsm(now_m, pos, cx)

    @staticmethod
    def _is_fullscreen_video(fg, playing: bool, source_id: str, in_game: bool) -> bool:
        if fg is None or not fg.is_fullscreen or in_game:
            return False
        exe = (fg.exe_name or "").lower()
        if exe in _VIDEO_PLAYER_EXES:
            return True
        if exe in _VIDEO_BROWSER_EXES:
            tlow = (fg.title or "").lower()
            if any(k in tlow for k in _VIDEO_TITLE_HINTS):
                return True
            if playing and source_id in ("youtube", "twitch", "vk", "browser", "chrome", "msedge", "firefox", "opera", "brave"):
                return True
        return False

    def _step_auto_hide_fsm(self, now_m: float, pos: QPoint, cx: float) -> None:
        cfg = self.config_mgr.config
        if (not self._expanded and not self._music_strip_open and not self.isMinimized()) and (
            (now_m - self._last_fg_poll_ts) >= 0.35
        ):
            self._last_fg_poll_ts = now_m
            fg = get_foreground_window_info()
            self._cached_fg_screen_covered = bool(
                fg is not None and fg.pid != _OWN_PID and fg.is_fullscreen
            )
            if cfg.hide_in_fullscreen or cfg.auto_hide:
                excl = {x.strip().lower() for x in cfg.auto_hide_excluded_apps}
                self._cached_fg_excluded = bool(fg and fg.exe_name.lower() in excl)
                self._cached_fg_video_fs = bool(
                    cfg.hide_in_fullscreen
                    and self._is_fullscreen_video(fg, self._playing, self._media_source_id, self._in_game)
                )
            else:
                self._cached_fg_excluded = False
                self._cached_fg_video_fs = False

        # Suspend background particle/weather/character generation whenever any fullscreen or maximized window owns screen
        screen_covered_now = bool(
            getattr(self, "_cached_fg_screen_covered", False)
            and not self._expanded
            and not self._music_strip_open
            and not self.isMinimized()
        )
        self._fg_screen_covered = screen_covered_now
        suspended_gen = bool(screen_covered_now or self._hide_state == "hidden" or self.isMinimized())
        if getattr(self, "_last_char_anim_enabled", True) != (not suspended_gen):
            self._last_char_anim_enabled = not suspended_gen
            self.character.set_animated(not suspended_gen)
        mov = getattr(self, "_custom_bg_movie", None)
        if mov is not None and hasattr(mov, "setPaused"):
            try:
                if getattr(self, "_last_movie_paused", False) != suspended_gen:
                    self._last_movie_paused = suspended_gen
                    mov.setPaused(suspended_gen)
            except Exception:
                pass
        if suspended_gen and hasattr(self.panel, "card_rain"):
            self.panel.card_rain.set_suspended(True)

        if not self._expanded and not self._music_strip_open and not self.isMinimized():
            screens = QGuiApplication.screens()
            scr = screens[cfg.monitor_index] if 0 <= cfg.monitor_index < len(screens) else QGuiApplication.primaryScreen()
            scr_y = scr.geometry().y() if scr else 0
            core_r = self._core_rect()
            core_global = QRect(
                self.x() + int(core_r.left()),
                self.y() + int(core_r.top()),
                int(core_r.width()),
                int(core_r.height()),
            ).adjusted(-10, -10, 10, 10)
            in_hover_zone = (
                (self.isVisible() and core_global.contains(pos))
                or (scr_y <= pos.y() <= scr_y + 18 and abs(pos.x() - cx) <= 210)
            )
            if in_hover_zone:
                self._hover_hide_grace_until = now_m + 0.42

            snip_active = is_snipping_overlay_active()
            want_hide = bool(
                (cfg.hide_in_fullscreen or cfg.auto_hide)
                and (self._cached_fg_video_fs or (cfg.auto_hide and not self._cached_fg_excluded))
                and not in_hover_zone
                and now_m >= self._hover_hide_grace_until
                and not self._recording
                and not self._dictating
                and not self._alarm_active
                and not snip_active
                and not self._in_game
                and not self._activity_active
            )

            if want_hide:
                if self._hide_state in ("shown", "showing"):
                    if self._hide_state == "shown" and self.pill_bar.isVisible() and self.pill_bar.width() > 4:
                        try:
                            self._pill_morph_pixmap = self.pill_bar.grab()
                            self._pill_morph_rect = QRectF(self.pill_bar.geometry())
                        except Exception:
                            self._pill_morph_pixmap = QPixmap()
                    self._hide_state = "hiding"
                    self._hide_last_ts = now_m
            else:
                if self._hide_state in ("hidden", "hiding"):
                    self._hide_state = "showing"
                    self._hide_last_ts = now_m
                    if not self.isVisible():
                        self.show()

        # Advance liquid droplet hide / reveal progress cleanly (true 120 Hz frame-paced)
        if self._hide_state in ("hiding", "showing"):
            dt_h = max(0.001, min(0.033, now_m - self._hide_last_ts))
            self._hide_last_ts = now_m
            if self._hide_state == "hiding":
                self._hide_p = min(1.0, self._hide_p + dt_h / 0.76)
                self.pill_bar.setVisible(self._hide_p < 0.015)
                if self._hide_p >= 1.0:
                    self._hide_state = "hidden"
                    self.pill_bar.setVisible(False)
                    self._pill_morph_pixmap = QPixmap()
                    self.hide()
            else:
                self._hide_p = max(0.0, self._hide_p - dt_h / 0.72)
                u_rev = 1.0 - self._hide_p
                self.pill_bar.setVisible(u_rev >= 0.94)
                if self._hide_p <= 0.0:
                    self._hide_state = "shown"
                    self._pill_morph_pixmap = QPixmap()
                    self.pill_bar.setVisible(True)
                    self._drop_dir_x, self._drop_dir_y = 0.0, 1.0
                    self.trigger_drop_impulse(0.20)
            active_ms, _, _ = self._fps_timing()
            if self._watch_timer.interval() != active_ms:
                self._watch_timer.setInterval(active_ms)
            self.update()
        elif self._hide_state == "hidden":
            want_ms = 75 if (cfg.auto_hide or cfg.hide_in_fullscreen) else 240
            if self._watch_timer.interval() != want_ms:
                self._watch_timer.setInterval(want_ms)

    def _on_hover_expand_timeout(self) -> None:
        if (
            self._expanded
            or not self._hover_active
            or not self.isVisible()
            or self.config_mgr.config.click_through
            or self._in_game
            or self._hide_state != "shown"
            or time.monotonic() < self._hover_collapse_block_until
        ):
            return
        local_pos = self.mapFromGlobal(QCursor.pos())
        if self._core_rect().adjusted(-10, -10, 10, 10).contains(QPointF(local_pos)):
            self.set_expanded(True)

    def enterEvent(self, event) -> None:  # noqa: N802
        core = self._core_rect()
        over_core = core.adjusted(-10, -10, 10, 10).contains(QPointF(self.mapFromGlobal(QCursor.pos())))
        if not over_core and not self._expanded:
            super().enterEvent(event)
            return
        self._hover_active = True
        if self._hide_state in ("hidden", "hiding"):
            self._hide_state = "showing"
            self._hide_last_ts = time.monotonic()
            if not self.isVisible():
                self.show()
        if not self._expanded:
            self.trigger_drop_impulse(0.22)
        self._sync_character_state()
        self._refresh_pill_views()
        if not self._expanded and not self._music_strip_open and self._has_media:
            self.reposition(animate=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_active = False
        self._hover_expand_timer.stop()
        self._sync_character_state()
        self._refresh_pill_views()
        if not self._expanded and not self._music_strip_open and self._has_media:
            self.reposition(animate=True)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.position().y()
            core = self._core_rect()
            pill_h = core.height() if not self._expanded else max(30.0, min(54.0, 42.0 * max(0.68, min(1.5, float(self.config_mgr.config.scale)))))
            in_pill_y = (core.top() - 4.0) <= y <= (core.top() + pill_h + 4.0)
            if self._expanded:
                if in_pill_y:
                    self.set_expanded(False)
                    event.accept()
                    return
            elif self._music_strip_open:
                # 2nd tap anywhere on strip background -> open full ExpandedPanel (Home)
                self.set_expanded(True)
                event.accept()
                return
            elif in_pill_y and self._hide_state == "shown":
                # 1st tap: Music Strip if media is playing/loaded, otherwise full Panel directly
                if self._has_media and not self._activity_active:
                    self._open_music_strip()
                else:
                    self.set_expanded(True)
                event.accept()
                return
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        core = self._core_rect()
        pill_h = max(30.0, min(54.0, 42.0 * max(0.68, min(1.5, float(self.config_mgr.config.scale)))))
        if self._expanded and event.position().y() > (core.top() + pill_h + 4.0):
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        now = time.monotonic()
        step = 3 if delta > 0 else -3
        dev = self.config_mgr.config.output_device_id or "default"
        proc_hint = self._media_source_id if self._has_media else ""

        if now - self._last_wheel_t > 1.5:
            try:
                cur = (
                    self.bridge.get_app_volume(proc_hint, dev)
                    if proc_hint
                    else {"found": False}
                )
                if cur.get("found"):
                    self._estimated_vol = int(cur.get("volume", self._estimated_vol))
                else:
                    cur = self.bridge.get_volume("render", dev)
                    self._estimated_vol = int(cur.get("volume", self._estimated_vol))
            except Exception:
                pass
        self._last_wheel_t = now
        self._estimated_vol = max(0, min(100, self._estimated_vol + step))
        vol = self._estimated_vol

        def _apply_vol() -> None:
            if proc_hint and self.bridge.set_app_volume(proc_hint, vol, dev):
                return
            self.bridge.set_volume(vol, "render", dev)

        threading.Thread(target=_apply_vol, daemon=True).start()
        self._set_strip_volume_ui(vol, False)
        self.wheel_volume_changed.emit(vol)
        event.accept()

    def _drag_noop(self) -> None:
        return

    # ------------------------------------------------------------------ Liquid Shape Painting
    @staticmethod
    def _sample_rounded_rect(rect: QRectF, radius: float, n_points: int = 56) -> list[tuple[float, float, float, float]]:
        """Sample points + unit outward normals clockwise along a rounded rectangle (cached)."""
        w, h = max(8.0, rect.width()), max(8.0, rect.height())
        r = min(radius, w * 0.5 - 0.5, h * 0.5 - 0.5)
        x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
        ck = (round(x0, 1), round(y0, 1), round(w, 1), round(h, 1), round(r, 2), n_points)
        cached = _SAMPLE_RECT_CACHE.get(ck)
        if cached is not None:
            return cached
        if len(_SAMPLE_RECT_CACHE) >= 32:
            _SAMPLE_RECT_CACHE.clear()
        top_len = max(0.0, w - 2 * r)
        side_len = max(0.0, h - 2 * r)
        arc_len = 0.5 * math.pi * r
        total = 2 * top_len + 2 * side_len + 4 * arc_len
        pts: list[tuple[float, float, float, float]] = []

        for i in range(n_points):
            s = (i / n_points) * total
            # 1. Top edge (left->right)
            if s < top_len:
                pts.append((x0 + r + s, y0, 0.0, -1.0))
                continue
            s -= top_len
            # 2. Top-right arc
            if s < arc_len and r > 0:
                ang = -0.5 * math.pi + (s / r)
                pts.append((x1 - r + r * math.cos(ang), y0 + r + r * math.sin(ang), math.cos(ang), math.sin(ang)))
                continue
            s -= arc_len
            # 3. Right edge
            if s < side_len:
                pts.append((x1, y0 + r + s, 1.0, 0.0))
                continue
            s -= side_len
            # 4. Bottom-right arc
            if s < arc_len and r > 0:
                ang = s / r
                pts.append((x1 - r + r * math.cos(ang), y1 - r + r * math.sin(ang), math.cos(ang), math.sin(ang)))
                continue
            s -= arc_len
            # 5. Bottom edge (right->left)
            if s < top_len:
                pts.append((x1 - r - s, y1, 0.0, 1.0))
                continue
            s -= top_len
            # 6. Bottom-left arc
            if s < arc_len and r > 0:
                ang = 0.5 * math.pi + (s / r)
                pts.append((x0 + r + r * math.cos(ang), y1 - r + r * math.sin(ang), math.cos(ang), math.sin(ang)))
                continue
            s -= arc_len
            # 7. Left edge (bottom->top)
            if s < side_len:
                pts.append((x0, y1 - r - s, -1.0, 0.0))
                continue
            s -= side_len
            # 8. Top-left arc
            if r > 0:
                ang = math.pi + (s / r)
                pts.append((x0 + r + r * math.cos(ang), y0 + r + r * math.sin(ang), math.cos(ang), math.sin(ang)))
            else:
                pts.append((x0, y0, -0.7, -0.7))
        _SAMPLE_RECT_CACHE[ck] = pts
        return pts

    def _build_liquid_path(self, core: QRectF, radius: float) -> QPainterPath:
        cfg = self.config_mgr.config
        shake_active = cfg.liquid_drop and self._snap_shake_amp > 0.015
        drop_active = cfg.liquid_drop and (
            self._drop_amp >= 0.008 or abs(self._drop_wobble) >= 0.025 or shake_active
        )
        fluid_active = cfg.liquid_drop and (
            self._fluid_swirl > 0.012 or abs(self._fluid_vx) > 0.15 or abs(self._fluid_vy) > 0.15
        )
        indent_active = cfg.liquid_drop and self._indent_amp >= 0.008
        vis = max(self._music_vis_env, 1.0 if self._playing else 0.0)
        if not drop_active and not indent_active and not fluid_active and vis <= 0.008 and not self._alarm_active:
            idle_key = (
                round(core.x(), 2),
                round(core.y(), 2),
                round(core.width(), 2),
                round(core.height(), 2),
                round(radius, 2),
            )
            if getattr(self, "_idle_liquid_key", None) == idle_key:
                return self._idle_liquid_path  # type: ignore[return-value]
            path = QPainterPath()
            path.addRoundedRect(core, radius, radius)
            self._idle_liquid_key = idle_key
            self._idle_liquid_path = path
            return path

        max_bulge = 26.0 if not self._expanded else 124.0
        bulge = (self._drop_amp * max_bulge) if drop_active else 0.0
        n_pts = (144 if self._expanded else 96) if vis > 0.008 else (108 if self._expanded else 72)
        samples = self._sample_rounded_rect(core, radius, n_points=n_pts)
        warped: list[tuple[QPointF, float]] = []
        n = len(samples)
        w_core = max(1.0, core.width())
        h_core = max(1.0, core.height())
        n_bands = len(self._music_bands)

        if self._tether_anchor.x() > 0.0 or self._tether_anchor.y() > 0.0:
            ax, ay = self._tether_anchor.x(), self._tether_anchor.y()
        else:
            ax = core.center().x() + self._drop_dir_x * core.width() * 0.42
            ay = core.center().y() + self._drop_dir_y * core.height() * 0.42
        sigma = 48.0 if not self._expanded else 68.0
        inv_2s2 = 1.0 / (2.0 * sigma * sigma)
        inv_2w2 = 1.0 / (2.0 * (sigma * 1.95) ** 2)
        broad_sigma = 110.0 if not self._expanded else 170.0
        inv_2b2 = 1.0 / (2.0 * broad_sigma * broad_sigma)

        has_local = self._drop_cursor_local.x() > -900.0
        d_left_c = abs(self._drop_cursor_local.x() - core.left()) if has_local else 999.0
        d_right_c = abs(core.right() - self._drop_cursor_local.x()) if has_local else 999.0
        left_prox = max(0.0, 1.0 - d_left_c / 240.0) if has_local else 0.0
        right_prox = max(0.0, 1.0 - d_right_c / 240.0) if has_local else 0.0
        if has_local and self._expanded:
            left_prox = max(left_prox, 0.25 if self._fluid_swirl > 0.02 else 0.0)
            right_prox = max(right_prox, 0.25 if self._fluid_swirl > 0.02 else 0.0)

        for idx, (px, py, nx, ny) in enumerate(samples):
            pull_dx = 0.0
            pull_dy = 0.0
            fluid_dx = 0.0
            fluid_dy = 0.0
            ripple = 0.0
            shiver = 0.0
            u_x = max(0.0, min(1.0, (px - core.left()) / w_core))
            u_y = max(0.0, min(1.0, (py - core.top()) / h_core))

            if drop_active:
                dx_a = px - ax
                dy_a = py - ay
                dist2 = dx_a * dx_a + dy_a * dy_a
                gauss = math.exp(-dist2 * inv_2s2)
                broad_g = math.exp(-dist2 * inv_2b2)
                skirt = math.exp(-dist2 * inv_2w2) - gauss
                wall_w = max(0.0, nx * self._drop_dir_x + ny * self._drop_dir_y)
                loc_str = gauss * (0.35 + 0.65 * wall_w) * bulge * 0.85
                # Coupled viscous slime chain deformation across the entire interface
                chain_str = (0.36 * broad_g + 0.16 * wall_w) * bulge * 0.28
                tot_str = loc_str + chain_str

                pull_dx = self._drop_dir_x * tot_str - nx * skirt * wall_w * bulge * 0.10
                pull_dy = self._drop_dir_y * tot_str - ny * skirt * wall_w * bulge * 0.10

                # Slow, broad gel sway instead of fast choppy water ripples
                ripple_mask = max(gauss, 0.35 * broad_g, 0.35 if shake_active else 0.0)
                if self._playing and self._drop_amp < 0.05 and not shake_active:
                    ripple_mask *= max(0.0, (ny - 0.20) / 0.80) if self._expanded else 1.0
                ripple = (
                    ripple_mask
                    * math.sin(idx * 0.12 + self._drop_phase)
                    * self._drop_wobble
                    * (1.8 if not self._expanded else 1.5)
                )
                if shake_active:
                    shiver = math.sin(idx * 0.16 + self._snap_shake_phase) * self._snap_shake_amp * 2.8

            # Continuous liquid side-wall & bottom meniscus curving when moving inside OR hovering nearby
            if fluid_active:
                bell_y = math.exp(-((u_y - self._fluid_y_norm) ** 2) / (2.0 * 0.25 * 0.25))
                bell_x = math.exp(-((u_x - self._fluid_x_norm) ** 2) / (2.0 * 0.24 * 0.24))
                top_fade = min(1.0, u_y / 0.14) if self._expanded else min(1.0, max(0.35, u_y / 0.20))
                if abs(nx) > 0.18:
                    s_prox = left_prox if nx < 0 else right_prox
                    wave_s = math.sin(u_y * math.pi * 2.2 - self._drop_phase * 0.72 + (0.0 if nx > 0 else 1.8) + self._fluid_vy * 0.04)
                    outward_m = self._fluid_swirl * (bell_y * (4.0 + 9.5 * s_prox) + wave_s * (3.0 + 3.8 * s_prox))
                    slosh_signed = (1.0 if nx > 0 else -1.0) * (self._fluid_vx * 0.60) * bell_y
                    signed_out = max(-4.8, min(19.5, (outward_m + slosh_signed) * top_fade))
                    fluid_dx = (1.0 if nx > 0 else -1.0) * signed_out * abs(nx)
                if ny > 0.22:
                    wave_b = math.sin(u_x * math.pi * 2.5 + self._drop_phase * 0.65)
                    signed_bot = max(-3.0, min(14.5, self._fluid_swirl * (bell_x * 5.0 + wave_b * 3.2) + self._fluid_vy * 0.28 * bell_x))
                    fluid_dy = signed_bot * ny

            # Music waves & adaptive spike/protruding forms (calm tracks -> rolling waves; energetic/rock -> spikes & side-edge bass)
            music_dx = 0.0
            music_dy = 0.0
            spike_sharp = 0.0
            if vis > 0.008:
                f_idx = u_x * max(1, n_bands - 1)
                i0 = int(f_idx)
                i1 = min(n_bands - 1, i0 + 1)
                frac = f_idx - i0
                band_val = self._music_bands[i0] * (1.0 - frac) + self._music_bands[i1] * frac
                band_lo = self._music_bands[max(0, min(n_bands - 1, i0 // 2))]
                band_hi = self._music_bands[max(0, min(n_bands - 1, (n_bands - 1) - (i0 // 3)))]
                energy = max(0.14, self._music_energy)
                beat = self._music_beat
                ph = self._music_phase
                aggro = self._music_aggro
                bass = self._music_bass
                bump_mix = max(0.32, min(1.0, 0.32 + aggro * 0.85))
                spike_mix = max(0.0, min(1.0, (aggro - 0.16) / 0.44))
                calm_wave_gain = 1.15 - 0.28 * spike_mix

                if self._expanded:
                    edge_taper = min(1.0, u_x / 0.055) * min(1.0, (1.0 - u_x) / 0.055)
                    bottom_mask = max(0.0, (ny - 0.14) / 0.86) * (0.25 + 0.75 * edge_taper)
                    if bottom_mask > 0.0:
                        # Flowing multi-harmonic waves + soft rounded spectral bumps (present even on calm sections)
                        swell = 0.5 + 0.5 * math.sin(u_x * math.pi * 2.2 - ph * 0.75)
                        wave1 = 0.5 + 0.5 * math.sin(u_x * math.pi * 4.5 - ph * 1.15)
                        wave2 = 0.5 + 0.5 * math.sin(u_x * math.pi * 8.2 + ph * 1.65)
                        wave_slope = math.cos(u_x * math.pi * 4.5 - ph * 1.15)
                        bumps = (
                            (0.5 + 0.5 * math.sin(u_x * math.pi * 5.6 - ph * 0.72)) ** 1.35
                            * (0.35 + 1.35 * max(band_val, band_lo) + 1.10 * beat)
                            * bump_mix
                        )
                        wave_disp = (
                            (swell * 0.34 + wave1 * 0.34 + wave2 * 0.16 + band_val * 0.45)
                            * (11.0 + energy * 24.0 + beat * 9.0)
                            * calm_wave_gain
                            + bumps * (12.0 + 18.0 * energy + 14.0 * beat)
                        )

                        # 4 distinct spike & geometric protruding forms smoothly scaled by track intensity (spike_mix)
                        p_needle = u_x * 9.0 + ph * 0.16
                        c_needle = math.cos(p_needle * math.pi)
                        s_needle = (max(0.0, c_needle) ** 10) * (0.45 + 1.15 * band_hi + 0.80 * beat)
                        p_fin = (u_x * 4.6 - ph * 0.14) % 1.0
                        fin_tri = max(0.0, 1.0 - abs(p_fin - 0.68) / (0.24 if p_fin < 0.68 else 0.11))
                        s_fin = (fin_tri ** 1.35) * (0.48 + 1.05 * band_val + 0.60 * energy)
                        fin_tilt = fin_tri * (12.0 * energy + 6.5 * beat) * spike_mix
                        p_fang = (u_x * 6.2 + ph * 0.09 + 0.35) % 1.0
                        s_fang = (max(0.0, 1.0 - abs(p_fang - 0.5) / 0.23) ** 1.55) * (0.40 + 1.10 * band_lo + 0.85 * beat)
                        p_cryst = abs(math.sin(u_x * math.pi * 7.4 - ph * 0.28 + math.sin(u_x * 9.0) * 0.35))
                        s_cryst = ((1.0 - p_cryst) ** 4.2) * (0.40 + 0.95 * band_val)

                        spike_raw = s_needle * 18.0 + s_fin * 19.5 + s_fang * 18.5 + s_cryst * 14.0
                        spike_disp = min(68.0, spike_raw * (0.70 + 1.15 * energy + 0.68 * beat)) * spike_mix
                        sharp_mix = min(1.0, (s_needle * 1.15 + s_fin * 0.95 + s_fang * 0.85 + s_cryst * 0.95)) * spike_mix
                        spike_sharp = sharp_mix * bottom_mask * vis

                        total_disp = bottom_mask * (wave_disp + spike_disp) * vis
                        music_dx = bottom_mask * vis * (
                            nx * total_disp * 0.25
                            + fin_tilt
                            + wave_slope * (4.5 + energy * 6.5)
                        )
                        music_dy = max(0.0, ny) * total_disp

                    # Subtle left & right side-edge bass waves when expanded (keeps vertical edges alive without hiding UI)
                    if abs(nx) > 0.45 and u_y > 0.10:
                        side_mask = (min(1.0, (u_y - 0.10) / 0.20) * min(1.0, (1.0 - u_y) / 0.12)) * (abs(nx) - 0.45) / 0.55
                        side_wave = 0.5 + 0.5 * math.sin(u_y * math.pi * 3.5 - ph * 1.1 + (0.0 if nx > 0 else 1.57))
                        side_disp = side_mask * vis * (bass * 6.8 + side_wave * (1.8 + 4.2 * bass + 2.5 * beat))
                        music_dx += (1.0 if nx > 0 else -1.0) * side_disp
                else:
                    # Collapsed pill: smooth waves + expressive bumps on drops + crisp micro-spikes + subtle side bass pulse
                    theta = math.atan2(py - core.center().y(), px - core.center().x())
                    wave_a = 0.5 + 0.5 * math.sin(theta * 3.0 - ph * 1.15)
                    wave_b = 0.5 + 0.5 * math.cos(theta * 5.5 + ph * 1.75)
                    bot_focus = max(0.0, (ny + 0.14) / 1.14) * min(1.0, u_x / 0.06) * min(1.0, (1.0 - u_x) / 0.06)
                    pill_bumps = (
                        (0.5 + 0.5 * math.sin(u_x * math.pi * 4.8 - ph * 0.78)) ** 1.3
                        * (0.35 + 1.25 * max(band_val, band_lo) + 1.0 * beat)
                        * bump_mix
                    )
                    c_needle = (max(0.0, math.cos(u_x * math.pi * 8.0 - ph * 0.50)) ** 8) * (0.5 + 1.0 * band_val + beat)
                    c_fin = (max(0.0, math.sin(u_x * math.pi * 4.5 + ph * 0.60)) ** 4.5) * (0.5 + 0.9 * energy)
                    mini_bumps_and_spikes = bot_focus * (
                        pill_bumps * (7.5 + 9.0 * energy + 8.0 * beat)
                        + (c_needle * 14.0 + c_fin * 11.5) * (0.80 + 0.95 * energy) * spike_mix
                    )
                    full_disp = (
                        (band_val * 0.44 + wave_a * 0.34 + wave_b * 0.22) * energy * 16.0
                        + energy * 5.0
                        + bass * 4.2
                        + beat * 6.8 * (0.38 + 0.62 * spike_mix)
                    )
                    jit_x = math.cos(ph * 1.6) * (energy * 2.0 + beat * 1.6 * spike_mix)
                    jit_y = math.sin(ph * 1.9) * (energy * 1.6 + beat * 1.4 * spike_mix)
                    spike_sharp = min(1.0, bot_focus * (c_needle + c_fin) * 0.95) * spike_mix * vis
                    music_dx = vis * (nx * full_disp * 0.92 + jit_x + (bot_focus * c_fin * 5.5 * spike_mix))
                    music_dy = vis * (ny * full_disp * (0.65 if ny < 0 else 1.25) + jit_y + mini_bumps_and_spikes)

            disp_n = ripple + shiver
            if self._alarm_active:
                t_al = time.monotonic() * 14.0
                al_spike = max(0.0, math.sin(u_x * math.pi * 11.0 + t_al)) ** 6
                disp_n += al_spike * (8.5 if not self._expanded else 13.0)
                spike_sharp = max(spike_sharp, al_spike * 0.9)
            indent_dx = 0.0
            indent_dy = 0.0
            if indent_active:
                idx_a = px - self._indent_anchor.x()
                idy_a = py - self._indent_anchor.y()
                id2 = idx_a * idx_a + idy_a * idy_a
                radius_ig = 54.0 if self._expanded else 40.0
                ig = math.exp(-id2 / (2.0 * radius_ig * radius_ig))
                i_wall = max(0.0, nx * self._indent_nx + ny * self._indent_ny)
                depth = ig * i_wall * self._indent_amp * (34.0 if self._expanded else 13.5)
                indent_dx = -nx * depth
                indent_dy = -ny * depth
            dx = pull_dx + fluid_dx + indent_dx + nx * disp_n + music_dx
            dy = pull_dy + fluid_dy + indent_dy + ny * disp_n + music_dy
            rx = px + dx
            ry = py + dy
            if self.width() > 20:
                min_x, max_x = 3.5, self.width() - 3.5
                if rx < min_x + 14.0:
                    rx = (min_x + 14.0) - 14.0 * math.tanh((min_x + 14.0 - rx) / 14.0)
                elif rx > max_x - 14.0:
                    rx = (max_x - 14.0) + 14.0 * math.tanh((rx - (max_x - 14.0)) / 14.0)
            if self.height() > 10:
                min_y, max_y = 1.5, self.height() - 3.5
                if ry < min_y + 12.0:
                    ry = (min_y + 12.0) - 12.0 * math.tanh((min_y + 12.0 - ry) / 12.0)
                elif ry > max_y - 14.0:
                    ry = (max_y - 14.0) + 14.0 * math.tanh((ry - (max_y - 14.0)) / 14.0)
            warped.append((QPointF(rx, ry), spike_sharp))

        # Hybrid contour: smooth quadratic spline on waves, crisp vertices at spike tips
        path = QPainterPath()
        first_mid = QPointF(0.5 * (warped[-1][0].x() + warped[0][0].x()), 0.5 * (warped[-1][0].y() + warped[0][0].y()))
        path.moveTo(first_mid)
        for i in range(n):
            curr, sharp = warped[i]
            nxt, _ = warped[(i + 1) % n]
            mid = QPointF(0.5 * (curr.x() + nxt.x()), 0.5 * (curr.y() + nxt.y()))
            if sharp > 0.45:
                path.lineTo(curr)
                path.lineTo(mid)
            else:
                path.quadTo(curr, mid)
        path.closeSubpath()
        return path

    def _paint_glass_overlay(
        self,
        p: QPainter,
        island_path: QPainterPath,
        core: QRectF,
        bg_src: QImage | QPixmap,
        glass_mode: str,
        *,
        bg_sig: object = None,
    ) -> None:
        """Render per-card and per-button Liquid Glass (pure optical refraction) or Frost Glass blur across all tabs."""
        from PyQt6.QtWidgets import QAbstractScrollArea, QPushButton, QToolButton
        from .liquid_glass_3d import (
            build_frost_bg_image,
            build_soft_liquid_bg_image,
            render_3d_refraction_card,
            render_frost_batch,
        )

        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        def _is_visible_in_page(w: QWidget, page: QWidget) -> bool:
            cur: QWidget | None = w
            while cur is not None and cur is not page:
                if cur.isHidden():
                    return False
                cur = cur.parentWidget()
            return True

        # Collect container cards on active/sliding tab(s) + all individual visible buttons (clipped to scroll area / stack)
        card_targets: list[tuple[QRectF, float, QRectF | None, float]] = []
        btn_targets: list[tuple[QRectF, float, QRectF | None, float]] = []

        stack = getattr(self.panel, "stack", None)
        slide_overlay = getattr(stack, "_overlay", None) if stack is not None else None
        is_sliding = bool(slide_overlay is not None and slide_overlay.isVisible())

        def _collect_page_targets(
            page: QWidget | None,
            page_dx: float,
            page_origin: QPointF,
            bounds_clip: QRectF | None,
            opacity: float,
            out_tpl: tuple[list, list] | None = None,
        ) -> None:
            if page is None:
                return

            def _page_scroll_clip(w: QWidget, r: QRectF, *, keep_clipped: bool = True) -> tuple[bool, QRectF | None, int | None]:
                clip_out = bounds_clip
                sb_id: int | None = None
                par = w.parentWidget()
                while par is not None and par is not page and par is not self:
                    if isinstance(par, QAbstractScrollArea):
                        if sb_id is None and par.verticalScrollBar() is not None:
                            sb_id = id(par.verticalScrollBar())
                        vp = par.viewport()
                        if vp is not None:
                            vpt = vp.mapTo(page, QPoint(0, 0))
                            vr = QRectF(
                                page_origin.x() + float(vpt.x()) + page_dx,
                                page_origin.y() + float(vpt.y()),
                                float(vp.width()),
                                float(vp.height()),
                            )
                            if clip_out is not None:
                                vr = vr.intersected(clip_out)
                            if clip_out is not None and clip_out.isEmpty():
                                return False, None, sb_id
                            if keep_clipped and (vr.isEmpty() or not r.intersects(vr)):
                                return False, None, sb_id
                            clip_out = vr
                    par = par.parentWidget()
                if keep_clipped and clip_out is not None and (clip_out.isEmpty() or not r.intersects(clip_out)):
                    return False, None, sb_id
                return True, clip_out, sb_id

            for fr in page.findChildren(QFrame):
                if fr.objectName() not in ("Card", "Surface"):
                    continue
                if fr.width() < 24 or fr.height() < 24 or not _is_visible_in_page(fr, page):
                    continue
                tl = fr.mapTo(page, QPoint(0, 0))
                cr = QRectF(
                    page_origin.x() + float(tl.x()) + page_dx,
                    page_origin.y() + float(tl.y()),
                    float(fr.width()),
                    float(fr.height()),
                )
                ok, clip_r, sb_id = _page_scroll_clip(fr, cr, keep_clipped=(out_tpl is None))
                if not ok:
                    continue
                rad = (13.0 if cr.height() <= 130.0 else 22.0) if fr.objectName() == "Card" else 16.0
                if out_tpl is not None:
                    out_tpl[0].append((cr, rad, clip_r, opacity, sb_id))
                if core.intersects(cr) and (clip_r is None or cr.intersects(clip_r)):
                    card_targets.append((cr, rad, clip_r, opacity))

            for btn in page.findChildren((QPushButton, QToolButton)):
                if btn.width() < 18 or btn.height() < 18 or not _is_visible_in_page(btn, page):
                    continue
                tl = btn.mapTo(page, QPoint(0, 0))
                br_r = QRectF(
                    page_origin.x() + float(tl.x()) + page_dx,
                    page_origin.y() + float(tl.y()),
                    float(btn.width()),
                    float(btn.height()),
                )
                ok, clip_r, sb_id = _page_scroll_clip(btn, br_r, keep_clipped=(out_tpl is None))
                if not ok:
                    continue
                if abs(br_r.width() - br_r.height()) <= 6.0 and br_r.width() >= 36.0:
                    rad = min(br_r.width(), br_r.height()) * 0.5
                else:
                    rad = 10.0 if br_r.height() <= 30.0 else min(14.0, br_r.height() * 0.45)
                if out_tpl is not None:
                    out_tpl[1].append((br_r, rad, clip_r, opacity, sb_id))
                if core.intersects(br_r) and (clip_r is None or br_r.intersects(clip_r)):
                    btn_targets.append((br_r, rad, clip_r, opacity))

        if is_sliding and stack is not None:
            stack_tl = stack.mapTo(self, QPoint(0, 0))
            stack_origin = QPointF(float(stack_tl.x()), float(stack_tl.y()))
            stack_w = max(1.0, float(stack.width()))
            stack_rect = QRectF(stack_origin.x(), stack_origin.y(), stack_w, float(stack.height())).intersected(core)
            off_x = float(getattr(slide_overlay, "_offset_x", 0.0))
            adj_side = 1 if int(getattr(slide_overlay, "_adj_side", 1)) >= 0 else -1
            prog = min(1.0, abs(off_x) / stack_w)
            cur_w = getattr(slide_overlay, "_cur_widget", None)
            adj_w = getattr(slide_overlay, "_adj_widget", None)
            _collect_page_targets(cur_w, off_x, stack_origin, stack_rect, max(0.28, 1.0 - 0.55 * prog))
            _collect_page_targets(
                adj_w, off_x + adj_side * stack_w, stack_origin, stack_rect, max(0.28, 0.45 + 0.55 * prog)
            )
        else:
            active_tab = stack.currentWidget() if stack is not None else None
            if active_tab is not None and active_tab.isVisible():
                now_gc = time.monotonic()
                sb_cache = getattr(self, "_tab_scrollbars_cache", None)
                if sb_cache is None:
                    sb_cache = {}
                    self._tab_scrollbars_cache = sb_cache
                sbars = sb_cache.get(id(active_tab))
                if sbars is None:
                    sbars = [sa.verticalScrollBar() for sa in active_tab.findChildren(QAbstractScrollArea) if sa.verticalScrollBar() is not None]
                    sb_cache[id(active_tab)] = sbars
                cur_vals = {id(sb): sb.value() for sb in sbars}
                tab_v_scroll = 0
                for sb in sbars:
                    tab_v_scroll = (tab_v_scroll * 31) ^ sb.value()
                base_key = (id(active_tab), self.width(), self.height(), round(core.x(), 1), round(core.y(), 1))
                gc_key = (base_key, tab_v_scroll)
                cached_gc = getattr(self, "_glass_geo_cache", None)
                tpl_cache = getattr(self, "_glass_tpl_cache", None)
                if cached_gc is not None and cached_gc[0] == gc_key and (now_gc - cached_gc[1]) < 5.0:
                    card_targets = list(cached_gc[2])
                    btn_targets = list(cached_gc[3])
                elif tpl_cache is not None and tpl_cache[0] == base_key and (now_gc - tpl_cache[1]) < 0.75:
                    _, _, cap_vals, tpl_cards, tpl_btns = tpl_cache
                    deltas = {k: float(cur_vals.get(k, 0) - cap_vals.get(k, 0)) for k in cap_vals}
                    for r, rad, clip_r, op, sb_id in tpl_cards:
                        dy = deltas.get(sb_id, 0.0) if sb_id is not None else 0.0
                        rr = r.translated(0.0, -dy) if dy != 0.0 else r
                        if core.intersects(rr) and (clip_r is None or rr.intersects(clip_r)):
                            card_targets.append((rr, rad, clip_r, op))
                    for r, rad, clip_r, op, sb_id in tpl_btns:
                        dy = deltas.get(sb_id, 0.0) if sb_id is not None else 0.0
                        rr = r.translated(0.0, -dy) if dy != 0.0 else r
                        if core.intersects(rr) and (clip_r is None or rr.intersects(clip_r)):
                            btn_targets.append((rr, rad, clip_r, op))
                    self._glass_geo_cache = (gc_key, now_gc, list(card_targets), list(btn_targets))
                else:
                    tab_tl = active_tab.mapTo(self, QPoint(0, 0))
                    out_tpl: tuple[list, list] = ([], [])
                    _collect_page_targets(active_tab, 0.0, QPointF(float(tab_tl.x()), float(tab_tl.y())), None, 1.0, out_tpl=out_tpl)
                    self._glass_tpl_cache = (base_key, now_gc, cur_vals, out_tpl[0], out_tpl[1])
                    self._glass_geo_cache = (gc_key, now_gc, list(card_targets), list(btn_targets))

        # Top navigation tab pills also get individual lenses/frost (cached per window size)
        nav_btns = list(getattr(self.panel, "tab_buttons", []))
        nav_key = (self.width(), self.height(), round(core.x(), 1), round(core.y(), 1), len(nav_btns))
        nav_cache = getattr(self, "_glass_nav_cache", None)
        if nav_cache is not None and nav_cache[0] == nav_key:
            btn_targets.extend(nav_cache[1])
        else:
            nav_targets: list[tuple[QRectF, float, QRectF | None, float]] = []
            for nb in nav_btns:
                if not nb.isVisible() or nb.width() < 16 or nb.height() < 16:
                    continue
                tl = nb.mapTo(self, QPoint(0, 0))
                nr = QRectF(float(tl.x()), float(tl.y()), float(nb.width()), float(nb.height()))
                if core.intersects(nr):
                    nav_targets.append((nr, nr.height() * 0.5, None, 1.0))
            self._glass_nav_cache = (nav_key, nav_targets)
            btn_targets.extend(nav_targets)

        ref_q = str(getattr(self.config_mgr.config, "refraction_quality", "ultra") or "ultra").lower()
        morph_running = self._anim.state() == QPropertyAnimation.State.Running
        liquid_pull = (self._drop_amp > 0.02) or (self._indent_amp > 0.02)
        scroll_running = bool(getattr(self.panel, "_smooth_scroll_active", False))
        fast_motion = morph_running or scroll_running or liquid_pull
        # Fast cached Frost Glass across all cards & buttons (Liquid Glass removed to eliminate render stutter)
        f_q = "low" if fast_motion else ref_q
        fk = (bg_sig, f_q, bg_src.width(), bg_src.height()) if bg_sig is not None else None
        fc = getattr(self, "_frost_bg_cache", None)
        if fk is not None and fc is not None and fc[0] == fk:
            frost_img = fc[1]
        else:
            frost_img = build_frost_bg_image(bg_src, quality=f_q)
            if fk is not None:
                self._frost_bg_cache = (fk, frost_img)
        fw, fh = float(self.width()), float(self.height())
        render_frost_batch(p, card_targets, frost_img, fw, fh)
        render_frost_batch(p, btn_targets, frost_img, fw, fh)

        p.restore()

    @staticmethod
    def _build_top_meniscus_bridge(cx: float, top_edge: float, orb_cy: float, skirt_hw: float, pinch_w: float) -> QPainterPath:
        """C1-smooth hourglass liquid bridge anchoring an emerging/retracting droplet flush to top_edge."""
        path = QPainterPath()
        h = orb_cy - top_edge
        if h <= 1.5 or skirt_hw <= 1.5:
            return path
        top_l = QPointF(cx - skirt_hw, top_edge)
        top_r = QPointF(cx + skirt_hw, top_edge)
        bot_l = QPointF(cx - pinch_w, orb_cy)
        bot_r = QPointF(cx + pinch_w, orb_cy)
        path.moveTo(top_l)
        path.lineTo(top_r)
        path.cubicTo(
            QPointF(cx + skirt_hw * 0.42, top_edge + h * 0.08),
            QPointF(cx + pinch_w * 0.92, top_edge + h * 0.58),
            bot_r,
        )
        path.lineTo(bot_l)
        path.cubicTo(
            QPointF(cx - pinch_w * 0.92, top_edge + h * 0.58),
            QPointF(cx - skirt_hw * 0.42, top_edge + h * 0.08),
            top_l,
        )
        path.closeSubpath()
        return path

    @staticmethod
    def _build_top_splash_wave(
        cx: float, top_edge: float, wave_hw: float, pts_y_fn
    ) -> QPainterPath:
        n_wave = 108
        pts: list[QPointF] = []
        for i in range(n_wave + 1):
            u = -1.0 + 2.0 * (i / n_wave)
            px = cx + u * wave_hw
            env_edge = max(0.0, 1.0 - u * u) ** 1.45
            py = top_edge + max(0.0, float(pts_y_fn(u)) * env_edge)
            pts.append(QPointF(px, py))

        path = QPainterPath()
        path.moveTo(QPointF(cx - wave_hw, top_edge))
        for j in range(len(pts) - 1):
            c_pt = pts[j]
            n_pt = pts[j + 1]
            mid = QPointF(0.5 * (c_pt.x() + n_pt.x()), 0.5 * (c_pt.y() + n_pt.y()))
            path.quadTo(c_pt, mid)
        path.lineTo(QPointF(cx + wave_hw, top_edge))
        path.closeSubpath()
        return path

    def _paint_droplet_hide_morph(
        self, p: QPainter, core: QRectF, prog: float, bg: QColor, border: QColor, eff_hex: str
    ) -> None:
        """Seamless C1 liquid-bezel animation: emerges straight out of desktop top edge & hides with splash ripple + drop."""
        prog = max(0.0, min(1.0, prog))
        cx = core.center().x()
        base_cy = core.center().y()  # 14 + 21 == 35.0
        ch = core.height()
        cw = core.width()
        accent_c = QColor(eff_hex or self.palette.accent)
        top_edge = 0.0  # flush top bezel of monitor / desktop

        fill_grad = QLinearGradient(QPointF(cx, top_edge), QPointF(cx, base_cy + 40.0))
        c_top = QColor(bg)
        c_bot = QColor(bg.red(), bg.green(), bg.blue(), max(210, bg.alpha()))
        fill_grad.setColorAt(0.0, c_top)
        fill_grad.setColorAt(1.0, c_bot)

        # ---- Branch 1: REVEAL ("showing") -> extrudes fluidly straight down out of top desktop edge ----
        if self._hide_state == "showing":
            u = 1.0 - prog  # 0.0 (completely inside desktop top bezel) -> 1.0 (resting pill)
            extrude_t = _smoothstep_01(0.0, 0.42, u)      # bulb emerges down from y=0 to resting cy
            detach_t = _smoothstep_01(0.26, 0.56, u)      # neck narrows cleanly back into top edge
            spread_t = _smoothstep_01(0.44, 0.96, u)      # orb stretches horizontally into capsule
            spread_q = 1.0 - (1.0 - spread_t) ** 3.1

            orb_r_h = ch * (0.12 + 0.38 * _smoothstep_01(0.0, 0.42, u))
            cur_w = max(orb_r_h * 2.0, (orb_r_h * 2.0) + (cw - orb_r_h * 2.0) * spread_q)
            cur_h = max(3.5, orb_r_h * 2.0)
            bounce = math.sin(_smoothstep_01(0.42, 0.96, u) * math.pi) * 2.4 * (1.0 - 0.55 * _smoothstep_01(0.74, 1.0, u))
            orb_cy = (3.5 * (1.0 - extrude_t)) + base_cy * extrude_t + bounce
            rad = min(cur_w * 0.5, cur_h * 0.5)

            pill_path = QPainterPath()
            pill_path.addRoundedRect(QRectF(cx - cur_w * 0.5, orb_cy - cur_h * 0.5, cur_w, cur_h), rad, rad)

            combined = pill_path
            neck_str = 1.0 - detach_t
            if neck_str > 0.015:
                skirt_hw = min(132.0, 32.0 + 82.0 * math.sin(min(1.0, u / 0.52) * math.pi * 0.72))
                pinch_w = max(1.8, min(cur_w * 0.42, (8.0 + 18.0 * extrude_t) * neck_str))
                bridge = self._build_top_meniscus_bridge(cx, top_edge, orb_cy, skirt_hw, pinch_w)
                if not bridge.isEmpty():
                    combined = combined.united(bridge)

            rip_fade = _smoothstep_01(0.18, 0.38, u) * (1.0 - _smoothstep_01(0.48, 0.72, u))
            if rip_fade > 0.015:
                rip_hw = min(145.0, cw * 0.54 + 48.0)
                r_ph = (u - 0.18) * 16.0
                wave = self._build_top_splash_wave(
                    cx,
                    top_edge,
                    rip_hw,
                    lambda ux: rip_fade * (13.0 * math.exp(-(ux * ux) / 0.07) + 4.2 * math.cos(abs(ux) * math.pi * 2.3 - r_ph)),
                )
                if not wave.isEmpty():
                    combined = combined.united(wave)

            p.setPen(QPen(border, 1.65))
            p.setBrush(fill_grad)
            p.drawPath(combined)

            # Subtle specular highlight + sliding avatar & placeholder shimmer bars clipped cleanly inside combined
            p.save()
            p.setClipPath(combined)
            sheen = QLinearGradient(QPointF(cx, top_edge), QPointF(cx, orb_cy + cur_h * 0.3))
            sheen.setColorAt(0.0, QColor(255, 255, 255, 42))
            sheen.setColorAt(0.45, QColor(accent_c.red(), accent_c.green(), accent_c.blue(), 16))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sheen)
            p.drawPath(combined)

            if 0.52 < u < 0.96:
                av_t = _smoothstep_01(0.52, 0.90, u)
                av_fade = 1.0 - _smoothstep_01(0.84, 0.96, u)
                av_alpha = int(155 * math.sin(min(1.0, (u - 0.52) / 0.24) * math.pi * 0.5) * av_fade)
                if av_alpha > 4:
                    target_ax = cx - cur_w * 0.5 + 22.0
                    cur_ax = cx + (target_ax - cx) * av_t
                    p.setPen(QPen(QColor(accent_c.red(), accent_c.green(), accent_c.blue(), av_alpha), 1.4))
                    p.setBrush(QColor(255, 255, 255, max(8, av_alpha // 5)))
                    p.drawEllipse(QPointF(cur_ax, orb_cy), 11.5, 11.5)
                    bar_w = max(0.0, (cur_w - 56.0) * _smoothstep_01(0.58, 0.90, u))
                    if bar_w > 12.0:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(255, 255, 255, max(6, int(av_alpha * 0.22))))
                        p.drawRoundedRect(QRectF(cur_ax + 18.0, orb_cy - 4.0, bar_w * 0.72, 8.0), 4.0, 4.0)
            p.restore()
            return

        # ---- Branch 2: HIDE ("hiding") -> squeeze pill content into orb -> absorb into top edge with wave splash -> pinch drop ----
        sq_t = _smoothstep_01(0.0, 0.32, prog)           # 0..1: capsule shrinks horizontally -> centered orb
        lift_t = _smoothstep_01(0.22, 0.56, prog)        # 0..1: orb merges all the way up inside top meniscus
        shrink_t = _smoothstep_01(0.36, 0.58, prog)      # 0..1: smooth radius taper inside splash bowl (zero snap!)
        global_fade = 1.0 - _smoothstep_01(0.86, 1.0, prog)

        sq_e = sq_t * sq_t * (3.0 - 2.0 * sq_t)
        cur_w = max(ch, cw + (ch - cw) * sq_e)
        orb_cy = base_cy * (1.0 - lift_t) + (top_edge + 2.0) * lift_t
        orb_scale = 1.0 - 0.88 * shrink_t
        cur_h = max(2.5, ch * orb_scale)
        eff_w = max(cur_h, cur_w * (1.0 - 0.84 * shrink_t))
        rad = min(eff_w * 0.5, cur_h * 0.5)

        # Subtle concentric squeeze pressure rings while compressing horizontally into center orb
        if prog < 0.38:
            echo_alpha = int(58 * math.sin(_smoothstep_01(0.0, 0.36, prog) * math.pi))
            if echo_alpha > 4:
                for sc_e in (1.14, 1.28):
                    er = QRectF(cx - eff_w * sc_e * 0.5, orb_cy - cur_h * sc_e * 0.48, eff_w * sc_e, cur_h * sc_e * 0.96)
                    p.setPen(QPen(QColor(accent_c.red(), accent_c.green(), accent_c.blue(), echo_alpha // 2), 1.0))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(er, er.height() * 0.5, er.height() * 0.5)

        combined = QPainterPath()
        if prog < 0.58:
            combined.addRoundedRect(QRectF(cx - eff_w * 0.5, orb_cy - cur_h * 0.5, eff_w, cur_h), rad, rad)
            attach_t = _smoothstep_01(0.18, 0.40, prog)
            if attach_t > 0.01:
                skirt_hw = min(138.0, eff_w * 0.55 + 92.0 * attach_t)
                pinch_w = min(eff_w * 0.46, 9.0 + 19.0 * attach_t)
                bridge = self._build_top_meniscus_bridge(cx, top_edge, orb_cy, skirt_hw, pinch_w)
                if not bridge.isEmpty():
                    combined = combined.united(bridge)

        # Wide top-bezel splash waves + hanging neck + V-funnel cusp (prog 0.26 -> 1.0)
        if prog > 0.26:
            wave_hw = min(max(108.0, cw * 0.64), core.width() * 0.74 + 50.0)
            absorb_bowl = math.sin(_smoothstep_01(0.26, 0.50, prog) * math.pi)
            splash_env = _smoothstep_01(0.30, 0.52, prog) * (1.0 - _smoothstep_01(0.72, 1.0, prog))
            neck_env = math.sin(_smoothstep_01(0.40, 0.62, prog) * math.pi) if 0.40 <= prog <= 0.62 else 0.0
            funnel_env = math.sin(_smoothstep_01(0.54, 0.76, prog) * math.pi) if 0.54 <= prog <= 0.76 else 0.0
            ripple_env = _smoothstep_01(0.50, 0.68, prog) * (1.0 - _smoothstep_01(0.78, 1.0, prog))
            calm_u = _smoothstep_01(0.66, 0.80, prog) * (1.0 - _smoothstep_01(0.80, 1.0, prog))
            r_ph = (prog - 0.50) * 18.0

            def _hide_wave_y(ux: float) -> float:
                bowl = absorb_bowl * 23.0 * math.exp(-(ux * ux) / 0.17)
                wings = (
                    splash_env * 12.0 * math.exp(-(abs(ux) - 0.34) ** 2 / 0.058)
                    + ripple_env * 10.0 * math.cos(abs(ux) * math.pi * 2.2 - r_ph)
                )
                neck = neck_env * 36.0 * math.exp(-(ux * ux) / 0.015)
                funnel = funnel_env * 28.0 * max(0.0, 1.0 - abs(ux) / 0.38) ** 1.65
                calm = calm_u * 8.0 * math.exp(-(ux * ux) / 0.28)
                return bowl + wings + neck + funnel + calm

            wave = self._build_top_splash_wave(cx, top_edge, wave_hw, _hide_wave_y)
            combined = wave if combined.isEmpty() else combined.united(wave)

        if global_fade > 0.02 and not combined.isEmpty():
            b_pen = QColor(border.red(), border.green(), border.blue(), int(228 * global_fade))
            f_top = QColor(c_top.red(), c_top.green(), c_top.blue(), int(c_top.alpha() * global_fade))
            f_bot = QColor(c_bot.red(), c_bot.green(), c_bot.blue(), int(c_bot.alpha() * global_fade))
            fg_grad = QLinearGradient(QPointF(cx, top_edge), QPointF(cx, base_cy + 40.0))
            fg_grad.setColorAt(0.0, f_top)
            fg_grad.setColorAt(1.0, f_bot)
            p.setPen(QPen(b_pen, 1.65))
            p.setBrush(fg_grad)
            p.drawPath(combined)

            # Clipped inner pill snapshot compression + subtle top mercury sheen
            p.save()
            p.setClipPath(combined)
            sheen = QLinearGradient(QPointF(cx, top_edge), QPointF(cx, base_cy + 14.0))
            sheen.setColorAt(0.0, QColor(255, 255, 255, int(44 * global_fade)))
            sheen.setColorAt(0.45, QColor(accent_c.red(), accent_c.green(), accent_c.blue(), int(16 * global_fade)))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sheen)
            p.drawPath(combined)

            pm = getattr(self, "_pill_morph_pixmap", None)
            if pm is not None and not pm.isNull() and prog < 0.26:
                pm_alpha = 1.0 - _smoothstep_01(0.02, 0.24, prog)
                if pm_alpha > 0.02:
                    p.setOpacity(pm_alpha)
                    pm_rect = getattr(self, "_pill_morph_rect", QRectF())
                    src_w = max(float(ch), pm_rect.width() if pm_rect.width() > 4 else cw)
                    src_h = max(12.0, pm_rect.height() if pm_rect.height() > 4 else ch * 0.76)
                    comp_ratio = max(0.18, eff_w / max(1.0, cw))
                    draw_w = max(cur_h * 0.85, src_w * (0.38 + 0.62 * comp_ratio))
                    draw_h = src_h * min(1.0, cur_h / max(1.0, ch))
                    tgt_pm = QRectF(cx - draw_w * 0.5, orb_cy - draw_h * 0.5, draw_w, draw_h)
                    p.drawPixmap(tgt_pm.toRect(), pm)
                    p.setOpacity(1.0)
            p.restore()

        # Continuous pinched bulb (0.42..0.58) -> exact seamless detachment into falling teardrop (0.58..1.00)
        if 0.42 <= prog <= 0.58:
            f_t = _smoothstep_01(0.42, 0.58, prog)
            by = top_edge + 28.0 + 10.0 * f_t
            br = 7.6 - 1.0 * f_t  # ends at exact 6.6 at top_edge + 38.0!
            p.setPen(QPen(QColor(border.red(), border.green(), border.blue(), int(225 * global_fade)), 1.5))
            p.setBrush(fill_grad)
            p.drawEllipse(QPointF(cx, by), br * (0.94 - 0.04 * f_t), br * (1.06 + 0.14 * f_t))
        elif prog > 0.58:
            drop_prog = (prog - 0.58) / 0.42
            drop_y = top_edge + 38.0 + 56.0 * (drop_prog ** 1.65)
            drop_r = max(2.2, 6.6 * (1.0 - 0.58 * drop_prog))
            drop_alpha = max(0, min(245, int(245 * (1.0 - _smoothstep_01(0.45, 1.0, drop_prog)))))
            if drop_alpha > 4:
                d_fill = QColor(bg.red(), bg.green(), bg.blue(), drop_alpha)
                d_edge = QColor(accent_c.red(), accent_c.green(), accent_c.blue(), drop_alpha)
                p.setPen(QPen(d_edge, 1.5))
                p.setBrush(d_fill)
                p.drawEllipse(QPointF(cx, drop_y), drop_r * 0.90, drop_r * (1.20 + 0.18 * drop_prog))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._panel_shake_dx or self._panel_shake_dy:
            p.translate(self._panel_shake_dx * 0.45, self._panel_shake_dy * 0.45)
        core = self._core_rect()
        bg = QColor(self.palette.bg_island)
        collapsed_tint = (not self._expanded) and bool(self._playing or self._spotify_sticky_hex)
        eff_hex = (
            self._activity_accent
            if self._activity_active
            else ((self._accent_hex or self._spotify_sticky_hex) if collapsed_tint else self.palette.accent)
        )
        border = QColor(eff_hex if (collapsed_tint or self._activity_active or self._drop_amp > 0.22 or self._alarm_active) else self.palette.border)
        border.setAlpha(235 if (collapsed_tint or self._activity_active or self._drop_amp > 0.22 or self._alarm_active) else 165)

        if not self._expanded and not self._music_strip_open and self._hide_state in ("hiding", "showing") and self._hide_p > 0.004:
            self._paint_droplet_hide_morph(p, core, self._hide_p, bg, border, eff_hex)
            p.end()
            return
        if not self._expanded and self._hide_state == "hidden":
            p.end()
            return

        radius = 21.5 if (not self._expanded and not self._music_strip_open) else 18.0
        path = self._build_liquid_path(core, radius)

        # Collapsed pill: full-island themed geometry for built-in styles, otherwise pure solid black
        if not self._expanded:
            if self.character.character_id.startswith("builtin_"):
                self.character.paint_full_builtin_island(p, core, path, draw_eyes=self._is_builtin_full_pill())
            else:
                b_col = border if (collapsed_tint or self._activity_active or self._drop_amp > 0.22 or self._alarm_active) else QColor(255, 255, 255, 34)
                p.setPen(QPen(b_col, 1.5))
                p.setBrush(QColor(0, 0, 0, 255))
                p.drawPath(path)
        elif self._expanded:
            from ..core.licensing import get_license_manager

            lic = get_license_manager()
            cfg = self.config_mgr.config
            use_weather_atmo = self._use_weather_appearance()
            glass_mode = str(getattr(cfg, "glass_mode", "frost") or "frost").lower()
            if glass_mode == "liquid":
                glass_mode = "frost"
            if not lic.is_feature_allowed("glass"):
                glass_mode = "off"
            eff_weather = (
                "clear"
                if (cfg.atmosphere_weather_mode == "auto" and not lic.is_feature_allowed("realtime_weather"))
                else cfg.atmosphere_weather_mode
            )
            use_glass = glass_mode == "frost"
            p.save()
            p.setClipPath(path)
            canvas_rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))

            def _paint_expanded_surface(painter: QPainter) -> None:
                if self._paint_custom_bg_media(painter, canvas_rect, veil_alpha=94):
                    return
                if use_weather_atmo:
                    AtmosphericScenePainter.paint_expanded_atmosphere(
                        painter,
                        core,
                        preview_mode=cfg.atmosphere_preview,
                        weather_symbol=self._weather_symbol,
                        phase_t=self._atmo_phase,
                        lat=cfg.weather_lat,
                        lon=cfg.weather_lon,
                        sky_mode=cfg.atmosphere_sky_mode,
                        weather_mode=eff_weather,
                        moon_mode=cfg.atmosphere_moon_mode,
                        wind_ms=self._wind_ms,
                        cloud_phase=self._cloud_phase,
                        outer_bottom=canvas_rect.bottom(),
                        outer_rect=canvas_rect,
                        cursor_local=self._drop_cursor_local,
                        music_energy=self._music_energy if self._playing else 0.0,
                        music_bands=self._music_bands if self._playing else None,
                        grass_sim=self._grass_sim,
                        atmo_anim=self._atmo_anim,
                        cloud_sim=self._cloud_sim,
                        quality=max(0.25, min(1.0, float(getattr(cfg, "atmosphere_quality", 100)) / 100.0)),
                    )
                else:
                    sg = QLinearGradient(canvas_rect.topLeft(), canvas_rect.bottomRight())
                    c1 = QColor(self.palette.bg_island)
                    acc_c = QColor(self.palette.accent)
                    mix_k = 0.34 if self._effective_appearance_mode() == "music" else 0.18
                    c2 = QColor(
                        int(c1.red() * (1.0 - mix_k) + acc_c.red() * mix_k),
                        int(c1.green() * (1.0 - mix_k) + acc_c.green() * mix_k),
                        int(c1.blue() * (1.0 - mix_k) + acc_c.blue() * mix_k),
                        c1.alpha(),
                    )
                    sg.setColorAt(0.0, c1)
                    if self._effective_appearance_mode() == "music":
                        mid_c = QColor(
                            int(c1.red() * 0.72 + acc_c.red() * 0.28),
                            int(c1.green() * 0.72 + acc_c.green() * 0.28),
                            int(c1.blue() * 0.72 + acc_c.blue() * 0.28),
                            c1.alpha(),
                        )
                        sg.setColorAt(0.55, mid_c)
                    sg.setColorAt(1.0, c2)
                    painter.fillRect(canvas_rect, sg)

            if use_glass:
                dpr = min(1.25, max(1.0, float(self.devicePixelRatioF())))
                pw = max(1, int(round(self.width() * dpr)))
                ph = max(1, int(round(self.height() * dpr)))

                stack = getattr(self.panel, "stack", None)
                active_tab = stack.currentWidget() if stack is not None else None
                slide_overlay = getattr(stack, "_overlay", None) if stack is not None else None
                is_sliding = bool(slide_overlay is not None and slide_overlay.isVisible())
                now_c = time.monotonic()

                _, eff_wkind, _, _ = self._resolve_effective_weather()
                storm_rain_motion = bool(use_weather_atmo and eff_wkind in ("rain", "thunder", "lightning"))
                liquid_active = (
                    self._drop_amp >= 0.008
                    or self._indent_amp >= 0.008
                    or abs(self._drop_wobble) >= 0.025
                    or self._snap_shake_amp > 0.015
                    or self._fluid_swirl > 0.012
                )
                live_scene_motion = bool(
                    self._playing
                    or is_sliding
                    or liquid_active
                    or self._anim.state() == QPropertyAnimation.State.Running
                    or self._grass_sim.is_settling()
                    or self._cloud_sim.is_dispersing()
                    or self._atmo_anim.is_transitioning()
                    or getattr(self, "_last_near_grass", False)
                    or getattr(self, "_last_near_sky", False)
                    or storm_rain_motion
                    or getattr(self, "_custom_bg_cv_cap", None) is not None
                    or getattr(self, "_custom_bg_movie", None) is not None
                )
                # Include active tab vertical scroll positions cheaply without findChildren tree walks
                tab_scroll_sig = 0
                if active_tab is not None:
                    from PyQt6.QtWidgets import QAbstractScrollArea

                    sb_cache = getattr(self, "_tab_scrollbars_cache", None)
                    if sb_cache is None:
                        sb_cache = {}
                        self._tab_scrollbars_cache = sb_cache
                    sbars = sb_cache.get(id(active_tab))
                    if sbars is None:
                        sbars = [sa.verticalScrollBar() for sa in active_tab.findChildren(QAbstractScrollArea) if sa.verticalScrollBar() is not None]
                        sb_cache[id(active_tab)] = sbars
                    for sb in sbars:
                        tab_scroll_sig = (tab_scroll_sig * 31) ^ sb.value()

                surf_key = (
                    pw,
                    ph,
                    round(dpr, 2),
                    round(core.x(), 1),
                    round(core.y(), 1),
                    round(core.width(), 1),
                    round(core.height(), 1),
                    self._effective_appearance_mode(),
                    getattr(self, "_custom_bg_frame_seq", 0),
                    self.palette.accent,
                    self.palette.bg_island,
                    eff_weather,
                    self._weather_symbol,
                    str(getattr(cfg, "atmosphere_preview", "auto") or "auto"),
                    str(getattr(cfg, "atmosphere_sky_mode", "auto") or "auto"),
                    str(getattr(cfg, "atmosphere_moon_mode", "auto") or "auto"),
                    int(getattr(cfg, "atmosphere_quality", 100)),
                )
                comp_key = (
                    surf_key,
                    glass_mode,
                    str(getattr(cfg, "refraction_quality", "ultra") or "ultra").lower(),
                    id(active_tab) if active_tab is not None else 0,
                    tab_scroll_sig,
                )
                comp_cache = getattr(self, "_exp_composite_cache", None)
                scroll_running = bool(getattr(self.panel, "_smooth_scroll_active", False))
                fast_glass = (self._anim.state() == QPropertyAnimation.State.Running) or scroll_running
                _, _, min_paint_dt = self._fps_timing()
                if is_sliding or fast_glass:
                    max_age = min_paint_dt
                elif live_scene_motion or use_weather_atmo:
                    max_age = max(min_paint_dt, 0.016)
                else:
                    max_age = 0.50
                if scroll_running:
                    surf_age = 0.075
                elif live_scene_motion or use_weather_atmo:
                    surf_age = max(min_paint_dt, 0.016)
                else:
                    surf_age = 0.50
                is_video_stream = bool(
                    self._effective_appearance_mode() in ("image", "video")
                    and (getattr(self, "_custom_bg_cv_cap", None) is not None or getattr(self, "_custom_bg_movie", None) is not None)
                )
                if is_video_stream and not is_sliding:
                    self._exp_backdrop_img = None
                    _paint_expanded_surface(p)
                    vid_glass_key = (
                        pw,
                        ph,
                        round(dpr, 2),
                        round(core.x(), 1),
                        round(core.y(), 1),
                        round(core.width(), 1),
                        round(core.height(), 1),
                        glass_mode,
                        str(getattr(cfg, "refraction_quality", "ultra") or "ultra").lower(),
                        id(active_tab) if active_tab is not None else 0,
                        tab_scroll_sig,
                    )
                    vg_cache = getattr(self, "_video_glass_cache", None)
                    if vg_cache is not None and vg_cache[0] == vid_glass_key and (now_c - vg_cache[1]) < 0.15:
                        p.drawImage(0, 0, vg_cache[2])
                    else:
                        bg_img = getattr(self, "_glass_bg_img", None)
                        if (
                            bg_img is None
                            or bg_img.width() != pw
                            or bg_img.height() != ph
                            or abs(bg_img.devicePixelRatio() - dpr) > 0.01
                        ):
                            bg_img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
                            bg_img.setDevicePixelRatio(dpr)
                            self._glass_bg_img = bg_img
                        bg_img.fill(0)
                        bp = QPainter(bg_img)
                        bp.setRenderHint(QPainter.RenderHint.Antialiasing)
                        _paint_expanded_surface(bp)
                        bp.end()

                        vg_img = getattr(self, "_video_glass_img", None)
                        if (
                            vg_img is None
                            or vg_img.width() != pw
                            or vg_img.height() != ph
                            or abs(vg_img.devicePixelRatio() - dpr) > 0.01
                        ):
                            vg_img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
                            vg_img.setDevicePixelRatio(dpr)
                            self._video_glass_img = vg_img
                        vg_img.fill(0)
                        vp = QPainter(vg_img)
                        vp.setRenderHint(QPainter.RenderHint.Antialiasing)
                        self._paint_glass_overlay(vp, path, core, bg_img, glass_mode)
                        vp.end()
                        self._video_glass_cache = (vid_glass_key, now_c, vg_img)
                        p.drawImage(0, 0, vg_img)
                elif (
                    (not fast_glass)
                    and comp_cache is not None
                    and comp_cache[0] == comp_key
                    and (now_c - comp_cache[1]) < max_age
                ):
                    p.drawImage(0, 0, comp_cache[2])
                else:
                    surf_cache = getattr(self, "_exp_surface_cache", None)
                    if surf_cache is not None and surf_cache[0] == surf_key and (now_c - surf_cache[1]) < surf_age:
                        bg_img = surf_cache[2]
                        bg_sig = (surf_key, surf_cache[1])
                    else:
                        bg_img = getattr(self, "_glass_bg_img", None)
                        if (
                            bg_img is None
                            or bg_img.width() != pw
                            or bg_img.height() != ph
                            or abs(bg_img.devicePixelRatio() - dpr) > 0.01
                        ):
                            bg_img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
                            bg_img.setDevicePixelRatio(dpr)
                            self._glass_bg_img = bg_img
                        bg_img.fill(0)
                        bp = QPainter(bg_img)
                        bp.setRenderHint(QPainter.RenderHint.Antialiasing)
                        _paint_expanded_surface(bp)
                        bp.end()
                        self._exp_surface_cache = (surf_key, now_c, bg_img)
                        bg_sig = (surf_key, now_c)

                    if fast_glass:
                        # During morph / scroll, skip heavy numpy 3D refraction so weak PCs stay 60/120+ FPS
                        self._exp_composite_cache = None
                        self._exp_backdrop_img = bg_img
                        p.drawImage(0, 0, bg_img)
                        if self._anim.state() != QPropertyAnimation.State.Running:
                            self._paint_glass_overlay(p, path, core, bg_img, glass_mode, bg_sig=bg_sig)
                    else:
                        comp_img = getattr(self, "_exp_composite_img", None)
                        if (
                            comp_img is None
                            or comp_img.width() != pw
                            or comp_img.height() != ph
                            or abs(comp_img.devicePixelRatio() - dpr) > 0.01
                        ):
                            comp_img = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
                            comp_img.setDevicePixelRatio(dpr)
                            self._exp_composite_img = comp_img
                        comp_img.fill(0)
                        cp = QPainter(comp_img)
                        cp.setRenderHint(QPainter.RenderHint.Antialiasing)
                        cp.drawImage(0, 0, bg_img)
                        self._paint_glass_overlay(cp, path, core, bg_img, glass_mode, bg_sig=bg_sig)
                        cp.end()

                        self._exp_composite_cache = (comp_key, now_c, comp_img, canvas_rect)
                        self._exp_backdrop_img = bg_img
                        p.drawImage(0, 0, comp_img)
            else:
                # Cache backdrop cleanly when glass_mode='off' too so scroll-edge fade strips & static re-paints hit cache
                dpr_o = max(1.0, float(self.devicePixelRatioF()))
                pw_o = max(1, int(round(self.width() * dpr_o)))
                ph_o = max(1, int(round(self.height() * dpr_o)))
                now_o = time.monotonic()
                is_dyn_bg = bool(
                    self._playing
                    or self._anim.state() == QPropertyAnimation.State.Running
                    or use_weather_atmo
                    or getattr(self, "_custom_bg_cv_cap", None) is not None
                    or getattr(self, "_custom_bg_movie", None) is not None
                )
                off_key = (
                    pw_o,
                    ph_o,
                    round(dpr_o, 2),
                    round(core.x(), 1),
                    round(core.y(), 1),
                    round(core.width(), 1),
                    round(core.height(), 1),
                    self._effective_appearance_mode(),
                    getattr(self, "_custom_bg_frame_seq", 0),
                    self.palette.accent,
                    self.palette.bg_island,
                    eff_weather,
                    self._weather_symbol,
                    str(getattr(cfg, "atmosphere_preview", "auto") or "auto"),
                    str(getattr(cfg, "atmosphere_sky_mode", "auto") or "auto"),
                    str(getattr(cfg, "atmosphere_moon_mode", "auto") or "auto"),
                    int(getattr(cfg, "atmosphere_quality", 100)),
                )
                off_cache = getattr(self, "_exp_off_cache", None)
                off_age = 0.042 if is_dyn_bg else 0.55
                if off_cache is not None and off_cache[0] == off_key and (now_o - off_cache[1]) < off_age:
                    self._exp_backdrop_img = off_cache[2]
                    p.drawImage(0, 0, off_cache[2])
                else:
                    off_img = getattr(self, "_exp_off_img", None)
                    if off_img is None or off_img.width() != pw_o or off_img.height() != ph_o or abs(off_img.devicePixelRatio() - dpr_o) > 0.01:
                        off_img = QImage(pw_o, ph_o, QImage.Format.Format_ARGB32_Premultiplied)
                        off_img.setDevicePixelRatio(dpr_o)
                        self._exp_off_img = off_img
                    off_img.fill(0)
                    op_p = QPainter(off_img)
                    op_p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    _paint_expanded_surface(op_p)
                    op_p.end()
                    self._exp_off_cache = (off_key, now_o, off_img)
                    self._exp_backdrop_img = off_img
                    p.drawImage(0, 0, off_img)
            p.restore()
            p.setPen(QPen(border, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            from ..core.licensing import get_license_manager

            cfg = self.config_mgr.config
            lic = get_license_manager()
            # Collapsed island uses only Frost Glass or opaque dark pill (Liquid Glass removed from pill style)
            eff_gm = str(getattr(cfg, "glass_mode", "frost") or "frost").lower()
            if not lic.is_feature_allowed("glass"):
                eff_gm = "off"
            if self._effective_appearance_mode() in ("image", "video") and not getattr(self, "_custom_bg_img", QImage()).isNull():
                p.save()
                p.setClipPath(path)
                self._paint_custom_bg_media(p, path.boundingRect(), veil_alpha=78)
                p.restore()
                p.setPen(QPen(border, 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            elif eff_gm in ("frost", "liquid"):
                grad = QLinearGradient(core.topLeft(), core.bottomLeft())
                grad.setColorAt(0.0, QColor(72, 82, 98, 178))
                grad.setColorAt(1.0, QColor(42, 48, 60, 192))
                p.setPen(QPen(QColor(255, 255, 255, 92), 1.4))
                p.setBrush(grad)
                p.drawPath(path)
            else:
                p.setPen(QPen(border, 1.5))
                p.setBrush(bg)
                p.drawPath(path)

        # Bottom-edge neon music wave & spike aura reflection when music plays (no extra clip mask overhead)
        if self._playing:
            bot_edge = path.boundingRect().bottom()
            b_grad = QLinearGradient(core.left(), core.bottom() - 18.0, core.left(), max(core.bottom() + 26.0, bot_edge))
            b_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            b_col = QColor(eff_hex if not self._expanded else self.palette.accent)
            b_col.setAlpha(68 + int(75 * self._music_energy))
            b_hi = QColor(eff_hex if not self._expanded else self.palette.accent)
            b_hi.setAlpha(135 + int(95 * min(1.0, self._music_energy + self._music_beat * 0.5)))
            b_grad.setColorAt(0.55, b_col)
            b_grad.setColorAt(1.0, b_hi)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(b_grad)
            p.drawPath(path)

        # Glossy liquid specular highlight at the stretch anchor
        if self.config_mgr.config.liquid_drop and self._drop_amp > 0.08:
            anchor = (
                QPointF(self._tether_anchor)
                if (self._tether_anchor.x() > 0.0 or self._tether_anchor.y() > 0.0)
                else QPointF(core.center().x(), core.bottom())
            )
            grad = QRadialGradient(anchor, 42.0)
            hi = QColor(eff_hex if (self._playing or self._activity_active) else self.palette.accent)
            hi.setAlpha(int(88 * min(1.0, self._drop_amp)))
            grad.setColorAt(0.0, hi)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawPath(path)

        # Cut an alpha=0 portal over active native docked Chat browser so typing/clicking goes straight to top-level Chrome while IslandWindow stays TOPMOST above Chrome's caption
        if self._expanded and hasattr(self.panel, "chat_tab") and hasattr(self.panel.chat_tab, "portal_local_rect"):
            portal_r = self.panel.chat_tab.portal_local_rect(self)
            if portal_r is not None and portal_r.isValid():
                p.save()
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                portal_path = QPainterPath()
                portal_path.addRoundedRect(QRectF(portal_r), 14.0, 14.0)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                p.fillPath(portal_path, Qt.GlobalColor.transparent)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                p.setPen(QPen(QColor(255, 255, 255, 42), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(portal_path)
                p.restore()

        p.end()
