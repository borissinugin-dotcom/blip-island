"""Weather tab: Atoll 8-card bento Weather HUD + City search, Sky/Weather/Cloud & Graphics Quality controls."""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QConicalGradient, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.config import ConfigManager
from ...core.i18n import tr
from ...services.weather_service import GeoPlace, WeatherReport, WeatherService, describe_symbol
from ..atmosphere import SKY_MODES, WEATHER_MODES, calc_moon_phase, resolve_scene_state
from ..icons import get_icon


def _wind_cardinal_ru(deg: float, is_ru: bool = True) -> str:
    dirs_ru = ("С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ")
    dirs_en = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    idx = int((float(deg) + 22.5) // 45.0) % 8
    return dirs_ru[idx] if is_ru else dirs_en[idx]


class _WindCompassWidget(QWidget):
    """Atoll circular wind direction dial with glowing directional arrow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._deg = 180.0
        self.setFixedSize(46, 46)

    def set_deg(self, deg: float) -> None:
        self._deg = float(deg) % 360.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() * 0.5, self.height() * 0.5
        r = min(cx, cy) - 3.0
        p.setPen(QPen(QColor(255, 255, 255, 42), 1.3))
        p.setBrush(QColor(255, 255, 255, 10))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Cardinal letters
        f = p.font()
        f.setPointSize(7)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(160, 172, 194, 150))
        p.drawText(QRectF(cx - 8, cy - r - 1, 16, 12), Qt.AlignmentFlag.AlignCenter, "С")
        p.drawText(QRectF(cx - 8, cy + r - 11, 16, 12), Qt.AlignmentFlag.AlignCenter, "Ю")

        # Arrow pointing wind direction
        ang = math.radians(self._deg - 90.0)
        tip = QPointF(cx + math.cos(ang) * (r * 0.72), cy + math.sin(ang) * (r * 0.72))
        tail = QPointF(cx - math.cos(ang) * (r * 0.62), cy - math.sin(ang) * (r * 0.62))
        p.setPen(QPen(QColor("#F8FAFC"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(tail, tip)
        head1 = QPointF(tip.x() - math.cos(ang - 0.55) * 6.0, tip.y() - math.sin(ang - 0.55) * 6.0)
        head2 = QPointF(tip.x() - math.cos(ang + 0.55) * 6.0, tip.y() - math.sin(ang + 0.55) * 6.0)
        p.drawLine(tip, head1)
        p.drawLine(tip, head2)
        p.end()


class _PressureArcWidget(QWidget):
    """Atoll horseshoe barometer arc gauge showing pressure in mmHg."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mmhg = 752.0
        self._arc_frac: float | None = None
        self.setFixedSize(68, 60)

    def set_pressure(self, mmhg: float, arc_frac: float | None = None) -> None:
        self._mmhg = float(mmhg)
        self._arc_frac = None if arc_frac is None else max(0.0, min(1.0, float(arc_frac)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = float(min(self.width(), self.height()) - 10)
        x0 = (self.width() - side) * 0.5
        rect = QRectF(x0, 5.0, side, side)
        start_deg = 215 * 16
        span_deg = -250 * 16

        p.setPen(QPen(QColor(255, 255, 255, 36), 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, start_deg, span_deg)

        t = self._arc_frac if self._arc_frac is not None else max(0.05, min(1.0, (self._mmhg - 725.0) / 55.0))
        if t > 0.005:
            p.setPen(QPen(QColor("#F1F5F9"), 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, start_deg, int(span_deg * t))

        f = p.font()
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#F8FAFC"))
        p.drawText(QRectF(0, 5.0, self.width(), side), Qt.AlignmentFlag.AlignCenter, f"{int(round(self._mmhg))}")
        p.end()


class _SunArcWidget(QWidget):
    """Atoll parabolic solar trajectory arc with live sunlit dot."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.68
        self.setFixedHeight(38)

    def set_progress(self, frac: float) -> None:
        self._progress = max(0.0, min(1.0, float(frac)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad_x = 10.0
        base_y = h - 7.0
        span = max(20.0, w - 2.0 * pad_x)
        pts: list[QPointF] = []
        for i in range(25):
            u = i / 24.0
            px = pad_x + u * span
            py = base_y - math.sin(u * math.pi) * (h - 15.0)
            pts.append(QPointF(px, py))

        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        pen = QPen(QColor(255, 255, 255, 65), 1.6, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(path)

        # Horizon line
        p.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        p.drawLine(QPointF(pad_x - 4, base_y), QPointF(w - pad_x + 4, base_y))

        sx = pad_x + self._progress * span
        sy = base_y - math.sin(self._progress * math.pi) * (h - 15.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(251, 191, 36, 90))
        p.drawEllipse(QPointF(sx, sy), 6.5, 6.5)
        p.setBrush(QColor("#FACC15"))
        p.drawEllipse(QPointF(sx, sy), 3.6, 3.6)
        p.end()


class _UvScaleBar(QWidget):
    """Atoll rainbow UV spectrum bar with circular indicator dot."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._uv = 1.0
        self.setFixedHeight(14)

    def set_uv(self, uv: float) -> None:
        self._uv = max(0.0, min(11.0, float(uv)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(40, self.width() - 8)
        y = 5.0
        grad = QLinearGradient(4, y, 4 + w, y)
        grad.setColorAt(0.0, QColor("#22C55E"))
        grad.setColorAt(0.35, QColor("#EAB308"))
        grad.setColorAt(0.7, QColor("#F97316"))
        grad.setColorAt(1.0, QColor("#A855F7"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(4, y, w, 4.5), 2.2, 2.2)

        dx = 4.0 + (self._uv / 11.0) * w
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(QPen(QColor("#0F172A"), 1.2))
        p.drawEllipse(QPointF(dx, y + 2.2), 4.0, 4.0)
        p.end()


class _HumidityBar(QWidget):
    """Atoll thin glowing cyan moisture bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pct = 63.0
        self.setFixedHeight(10)

    def set_pct(self, pct: float) -> None:
        self._pct = max(0.0, min(100.0, float(pct)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(24, self.width() - 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 36))
        p.drawRoundedRect(QRectF(2, 3, w, 4.0), 2.0, 2.0)
        fw = max(6.0, (self._pct / 100.0) * w)
        p.setBrush(QColor("#38BDF8"))
        p.drawRoundedRect(QRectF(2, 3, fw, 4.0), 2.0, 2.0)
        p.end()


def _make_bento_card(title: str = "") -> tuple[QFrame, QVBoxLayout, QLabel | None]:
    card = QFrame()
    card.setObjectName("Card")
    card.setMinimumHeight(110)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(6)
    hdr = None
    if title:
        hdr = QLabel(title)
        hdr.setObjectName("Muted")
        hdr.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 0.6px;")
        lay.addWidget(hdr)
    return card, lay, hdr


class WeatherTab(QWidget):
    """Atoll 8-card bento weather display + live Atmosphere Sky/Cloud/Quality settings."""

    search_ready = pyqtSignal(list)  # list[GeoPlace]
    weather_updated = pyqtSignal(object)  # WeatherReport | None
    atmosphere_changed = pyqtSignal()
    auto_place_ready = pyqtSignal(object)  # GeoPlace | None

    def __init__(self, weather_service: WeatherService, config_mgr: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.weather_service = weather_service
        self.config_mgr = config_mgr
        self._places: list[GeoPlace] = []

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        scroll.setWidget(host)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 4, 6, 8)
        layout.setSpacing(8)

        # --- City search bar (preserves saved city automatically!) ---
        search_row = QHBoxLayout()
        search_row.setSpacing(5)
        self.city_edit = QLineEdit()
        self.city_edit.setPlaceholderText(tr("weather_search"))
        if config_mgr.config.weather_city:
            self.city_edit.setText(config_mgr.config.weather_city)
        self.city_edit.returnPressed.connect(self._search_city)
        search_btn = QPushButton("Поиск")
        search_btn.setIcon(get_icon("globe", 13, "#F2F5FA", "#60A5FA"))
        search_btn.clicked.connect(self._search_city)
        ref_btn = QPushButton("")
        ref_btn.setIcon(get_icon("refresh", 14, "#F2F5FA", "#60A5FA"))
        ref_btn.setFixedSize(28, 28)
        ref_btn.setStyleSheet("min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;")
        ref_btn.clicked.connect(lambda: self.refresh_weather(force=True))
        search_row.addWidget(self.city_edit, 1)
        search_row.addWidget(search_btn)
        search_row.addWidget(ref_btn)
        layout.addLayout(search_row)

        self.city_results = QListWidget()
        self.city_results.setMaximumHeight(90)
        self.city_results.hide()
        self.city_results.itemClicked.connect(self._pick_city)
        self.city_results.itemActivated.connect(self._pick_city)
        layout.addWidget(self.city_results)

        # --- Atoll 2x4 Bento Grid ---
        grid = QGridLayout()
        grid.setSpacing(10)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        # 1. City + Temp + Feels
        c1, l1, self.city_lbl = _make_bento_card(config_mgr.config.weather_city or tr("weather_choose"))
        self.city_lbl.setObjectName("Muted")
        t_row = QHBoxLayout()
        t_row.setSpacing(4)
        self.cond_ico_lbl = QLabel()
        self.cond_ico_lbl.setFixedSize(22, 22)
        self.temp_lbl = QLabel("— °")
        self.temp_lbl.setStyleSheet("font-size: 24px; font-weight: 700;")
        t_row.addWidget(self.cond_ico_lbl)
        t_row.addWidget(self.temp_lbl)
        t_row.addStretch(1)
        l1.addLayout(t_row)
        self.cond_state_lbl = QLabel("—")
        self.cond_state_lbl.setStyleSheet("font-size: 11px; font-weight: 600;")
        self.desc_lbl = QLabel("")
        self.desc_lbl.setObjectName("Muted")
        self.desc_lbl.setStyleSheet("font-size: 9px;")
        self.desc_lbl.setWordWrap(True)
        l1.addWidget(self.cond_state_lbl)
        l1.addWidget(self.desc_lbl)
        grid.addWidget(c1, 0, 0)

        # 2. Wind Compass
        c2, l2, _ = _make_bento_card("ВЕТЕР")
        w_row = QHBoxLayout()
        w_row.setSpacing(6)
        self.wind_compass = _WindCompassWidget()
        w_col = QVBoxLayout()
        w_col.setSpacing(1)
        self.wind_spd_lbl = QLabel("— м/с")
        self.wind_spd_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
        self.wind_dir_lbl = QLabel("С")
        self.wind_dir_lbl.setObjectName("Muted")
        self.wind_dir_lbl.setStyleSheet("font-size: 10px;")
        w_col.addWidget(self.wind_spd_lbl)
        w_col.addWidget(self.wind_dir_lbl)
        w_row.addWidget(self.wind_compass)
        w_row.addLayout(w_col, 1)
        l2.addLayout(w_row)
        grid.addWidget(c2, 0, 1)

        # 3. Humidity
        c3, l3, _ = _make_bento_card("ВЛАЖНОСТЬ")
        self.hum_val_lbl = QLabel("—%")
        self.hum_val_lbl.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.hum_bar = _HumidityBar()
        self.hum_desc_lbl = QLabel("Комфортно")
        self.hum_desc_lbl.setObjectName("Muted")
        self.hum_desc_lbl.setStyleSheet("font-size: 10px;")
        l3.addWidget(self.hum_val_lbl)
        l3.addWidget(self.hum_bar)
        l3.addWidget(self.hum_desc_lbl)
        grid.addWidget(c3, 0, 2)

        # 4. Pressure
        c4, l4, _ = _make_bento_card("ДАВЛЕНИЕ")
        self.press_gauge = _PressureArcWidget()
        self.press_unit_lbl = QLabel("мм рт. ст.")
        self.press_unit_lbl.setObjectName("Muted")
        self.press_unit_lbl.setStyleSheet("font-size: 9px;")
        self.press_unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l4.addWidget(self.press_gauge, 0, Qt.AlignmentFlag.AlignCenter)
        l4.addWidget(self.press_unit_lbl)
        grid.addWidget(c4, 0, 3)

        # 5. Sun
        c5, l5, _ = _make_bento_card("СОЛНЦЕ")
        self.sun_arc = _SunArcWidget()
        sun_times = QHBoxLayout()
        self.sunrise_lbl = QLabel("↑ 07:00")
        self.sunrise_lbl.setObjectName("Muted")
        self.sunrise_lbl.setStyleSheet("font-size: 10px;")
        self.sunset_lbl = QLabel("↓ 18:00")
        self.sunset_lbl.setObjectName("Muted")
        self.sunset_lbl.setStyleSheet("font-size: 10px;")
        sun_times.addWidget(self.sunrise_lbl)
        sun_times.addStretch(1)
        sun_times.addWidget(self.sunset_lbl)
        l5.addWidget(self.sun_arc)
        l5.addLayout(sun_times)
        grid.addWidget(c5, 1, 0)

        # 6. UV Index
        c6, l6, _ = _make_bento_card("УФ-ИНДЕКС")
        self.uv_val_lbl = QLabel("1")
        self.uv_val_lbl.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.uv_desc_lbl = QLabel("Низкий")
        self.uv_desc_lbl.setObjectName("Muted")
        self.uv_desc_lbl.setStyleSheet("font-size: 10px;")
        self.uv_bar = _UvScaleBar()
        l6.addWidget(self.uv_val_lbl)
        l6.addWidget(self.uv_desc_lbl)
        l6.addWidget(self.uv_bar)
        grid.addWidget(c6, 1, 1)

        # 7. Precipitation
        c7, l7, _ = _make_bento_card("ОСАДКИ")
        self.precip_val_lbl = QLabel("0 мм")
        self.precip_val_lbl.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.precip_sub_lbl = QLabel("Вероятность осадков 0%")
        self.precip_sub_lbl.setObjectName("Muted")
        self.precip_sub_lbl.setStyleSheet("font-size: 9px;")
        self.precip_sub_lbl.setWordWrap(True)
        l7.addWidget(self.precip_val_lbl)
        l7.addWidget(self.precip_sub_lbl)
        l7.addStretch(1)
        grid.addWidget(c7, 1, 2)

        # 8. Hourly / Week Toggle Card
        c8 = QFrame()
        c8.setObjectName("Card")
        c8.setStyleSheet("QFrame#Card { border-radius: 16px; }")
        l8 = QVBoxLayout(c8)
        l8.setContentsMargins(10, 8, 10, 8)
        l8.setSpacing(6)
        hdr8 = QHBoxLayout()
        hdr8.setSpacing(4)
        self._fc_title = QLabel("ПО ЧАСАМ")
        self._fc_title.setObjectName("Muted")
        self._fc_title.setStyleSheet("font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self._fc_mode = "hourly"
        self._last_rep: WeatherReport | None = None
        self.btn_fc_hours = QPushButton("Часы")
        self.btn_fc_week = QPushButton("Неделя")
        for fb, md in ((self.btn_fc_hours, "hourly"), (self.btn_fc_week, "week")):
            fb.setCheckable(True)
            fb.setChecked(md == "hourly")
            fb.setFixedHeight(20)
            fb.setCursor(Qt.CursorShape.PointingHandCursor)
            fb.setStyleSheet(
                "QPushButton { min-height: 18px; max-height: 20px; padding: 0px 7px; border-radius: 9px; font-size: 9px; font-weight: 600; }"
            )
            fb.clicked.connect(lambda _, m=md: self._set_forecast_mode(m))
        hdr8.addWidget(self._fc_title, 1)
        hdr8.addWidget(self.btn_fc_hours)
        hdr8.addWidget(self.btn_fc_week)
        l8.addLayout(hdr8)
        self._hour_cols_row = QHBoxLayout()
        self._hour_cols_row.setSpacing(4)
        self._hour_slots: list[tuple[QLabel, QLabel, QLabel]] = []
        for _ in range(4):
            col = QVBoxLayout()
            col.setSpacing(2)
            hl_t = QLabel("--")
            hl_t.setObjectName("Muted")
            hl_t.setStyleSheet("font-size: 9px;")
            hl_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl_i = QLabel()
            hl_i.setFixedSize(16, 16)
            hl_i.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl_v = QLabel("—°")
            hl_v.setStyleSheet("font-size: 11px; font-weight: 700;")
            hl_v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(hl_t)
            col.addWidget(hl_i, 0, Qt.AlignmentFlag.AlignCenter)
            col.addWidget(hl_v)
            self._hour_cols_row.addLayout(col)
            self._hour_slots.append((hl_t, hl_i, hl_v))
        l8.addLayout(self._hour_cols_row)
        grid.addWidget(c8, 1, 3)

        layout.addLayout(grid, 1)

        self.alert_lbl = QLabel("")
        self.alert_lbl.setStyleSheet(
            "background: rgba(245, 158, 11, 0.22); border: 1px solid #F59E0B; "
            "border-radius: 9px; padding: 5px 9px; color: #FCD34D; font-weight: 600; font-size: 11px;"
        )
        self.alert_lbl.hide()
        layout.addWidget(self.alert_lbl)

        self.hourly_list = QListWidget()
        self.hourly_list.hide()  # Retained for signal/compatibility

        self._disposed = False
        self._hud_state: dict = {}
        self._hud_anim_start = 0.0
        self.search_ready.connect(self._on_search_results)
        self.weather_updated.connect(self._on_weather_ready)
        if getattr(self.weather_service, "_last_report", None) is not None:
            self._on_weather_ready(self.weather_service._last_report)
        QTimer.singleShot(150, self.refresh_weather)
        self.auto_place_ready.connect(self._on_auto_place_ready)
        self._hud_anim_dur = 0.85
        self._hud_anim_forecast_only = False
        self._hud_anim_timer = QTimer(self)
        self._hud_anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._hud_anim_timer.setInterval(8)
        self._hud_anim_timer.timeout.connect(self._on_hud_anim_tick)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(900_000)
        self._refresh_timer.timeout.connect(lambda: self.refresh_weather(force=False))
        self._refresh_timer.start()

        if config_mgr.config.weather_city:
            self.refresh_weather(force=False)
        else:
            self._maybe_autodetect_city()

    def dispose(self) -> None:
        self._disposed = True
        if self._hud_anim_timer.isActive():
            self._hud_anim_timer.stop()
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()

    def stop(self) -> None:
        self.dispose()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._last_rep is not None:
            self._start_hud_animation(forecast_only=False)
        elif self.config_mgr.config.weather_city and self.temp_lbl.text().strip() in ("—°", "— °", "--°", ""):
            self.refresh_weather(force=True)

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._hud_anim_timer.isActive():
            self._hud_anim_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.dispose()
        super().closeEvent(event)

    def sync_atmosphere_combo(self) -> None:
        return

    def _maybe_autodetect_city(self) -> None:
        def work() -> None:
            place = self.weather_service.detect_city_by_ip()
            try:
                self.auto_place_ready.emit(place)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_auto_place_ready(self, place: object) -> None:
        if self._disposed or not isinstance(place, GeoPlace) or self.config_mgr.config.weather_city:
            return
        if not self.config_mgr.config_path.parent.exists():
            return
        self.config_mgr.config.weather_city = place.name
        self.config_mgr.config.weather_lat = place.lat
        self.config_mgr.config.weather_lon = place.lon
        self.config_mgr.save()
        self.city_edit.setText(place.name)
        self.refresh_weather(force=True)

    def _search_city(self) -> None:
        q = self.city_edit.text().strip()
        if len(q) < 2:
            return

        def work() -> None:
            places = self.weather_service.search_cities(q)
            try:
                self.search_ready.emit(places)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_search_results(self, places: list) -> None:
        if self._disposed:
            return
        self._places = places
        self.city_results.clear()
        if not places:
            self.city_results.hide()
            return
        for idx, pl in enumerate(places):
            it = QListWidgetItem(f"{pl.name} ({pl.lat:.2f}, {pl.lon:.2f})")
            it.setData(Qt.ItemDataRole.UserRole, idx)
            self.city_results.addItem(it)
        # Auto-save top match so user doesn't have to re-enter their city on restart
        top = places[0]
        self.config_mgr.config.weather_city = top.name
        self.config_mgr.config.weather_lat = top.lat
        self.config_mgr.config.weather_lon = top.lon
        self.config_mgr.save()
        self.city_lbl.setText(top.name.upper())
        self.refresh_weather(force=True)
        self.city_results.show()

    def _pick_city(self, item: QListWidgetItem) -> None:
        raw_idx = item.data(Qt.ItemDataRole.UserRole)
        row = int(raw_idx) if isinstance(raw_idx, int) else self.city_results.row(item)
        if 0 <= row < len(self._places):
            pl = self._places[row]
            self.config_mgr.config.weather_city = pl.name
            self.config_mgr.config.weather_lat = pl.lat
            self.config_mgr.config.weather_lon = pl.lon
            self.config_mgr.config.atmosphere_preview = "auto"
            self.config_mgr.config.atmosphere_sky_mode = "auto"
            self.config_mgr.config.atmosphere_weather_mode = "auto"
            self.config_mgr.config.atmosphere_moon_mode = "auto"
            self.config_mgr.save()
            self.sync_atmosphere_combo()
            self.city_edit.setText(pl.name)
            self.city_results.hide()
            self.atmosphere_changed.emit()
            self.refresh_weather(force=True)

    def refresh_weather(self, force: bool = False) -> None:
        city = self.config_mgr.config.weather_city
        lat = self.config_mgr.config.weather_lat
        lon = self.config_mgr.config.weather_lon
        if not city:
            return

        def work() -> None:
            rep = self.weather_service.fetch_weather(city, lat, lon, force=force)
            try:
                self.weather_updated.emit(rep)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _count_int(target: int, ease_t: float) -> int:
        if target == 0 or ease_t >= 0.999:
            return int(target)
        start = 1 if target > 0 else -1
        return int(round(start + (target - start) * ease_t))

    @staticmethod
    def _count_float(target: float, ease_t: float) -> float:
        if abs(target) < 0.05 or ease_t >= 0.999:
            return float(target)
        start = (1.0 if abs(target) >= 1.2 else 0.1) * (1.0 if target >= 0 else -1.0)
        return start + (target - start) * ease_t

    def _on_weather_ready(self, rep: object) -> None:
        if self._disposed or not isinstance(rep, WeatherReport):
            return
        is_ru = self.config_mgr.config.language == "ru"
        self.city_lbl.setText(rep.city.upper())
        sym_l = (rep.symbol or "").lower()
        main_ico = "sun" if ("clear" in sym_l or "fair" in sym_l) else "cloud"
        self.cond_ico_lbl.setPixmap(get_icon(main_ico, 20, "#F8FAFC", "#FACC15" if main_ico == "sun" else "#93C5FD").pixmap(20, 20))
        desc = rep.description_ru if is_ru else rep.description_en
        self.cond_state_lbl.setText(desc)
        temps = [h.temp_c for h in rep.hourly[:8]] or [rep.temp_c]
        hi, lo = int(round(max(temps))), int(round(min(temps)))

        self.wind_dir_lbl.setText(_wind_cardinal_ru(rep.wind_deg, is_ru))
        self.hum_desc_lbl.setText("Сухо" if rep.humidity < 35 else ("Влажно" if rep.humidity > 72 else "Комфортно"))
        self.sunrise_lbl.setText(f"↑ {rep.sunrise_str}")
        self.sunset_lbl.setText(f"↓ {rep.sunset_str}")
        try:
            now_m = datetime.now().hour * 60 + datetime.now().minute
            sr_h, sr_m = map(int, rep.sunrise_str.split(":"))
            ss_h, ss_m = map(int, rep.sunset_str.split(":"))
            sr = sr_h * 60 + sr_m
            ss = ss_h * 60 + ss_m
            frac = max(0.0, min(1.0, (now_m - sr) / max(60.0, ss - sr)))
        except Exception:
            frac = 0.5

        uv_int = int(round(rep.uv_index))
        self.uv_desc_lbl.setText("Низкий" if uv_int <= 2 else ("Умеренный" if uv_int <= 5 else "Высокий"))

        pr_tot = sum(h.precip_mm for h in rep.hourly[:8])
        if pr_tot > 0:
            pct_prob = min(95, max(15, int(round(pr_tot * 18))))
        elif rep.precip_next_hour:
            pct_prob = 45
        else:
            sym_l = (rep.symbol or "").lower()
            hum_v = float(rep.humidity or 0.0)
            if any(k in sym_l for k in ("rain", "snow", "sleet", "thunder")):
                pct_prob = max(55, min(90, int(round(hum_v * 0.85))))
            elif "fog" in sym_l:
                pct_prob = max(18, min(35, int(round((hum_v - 40.0) * 0.55))))
            elif "cloudy" in sym_l and "partly" not in sym_l:
                pct_prob = max(10, min(32, int(round((hum_v - 42.0) * 0.5))))
            elif "partly" in sym_l:
                pct_prob = max(4, min(22, int(round((hum_v - 48.0) * 0.35))))
            else:
                pct_prob = max(0, min(10, int(round((hum_v - 72.0) * 0.3))))

        new_sig = (
            rep.city,
            int(round(rep.temp_c)),
            int(round(rep.feels_like_c)),
            round(rep.wind_ms, 1),
            int(round(rep.humidity)),
            int(round(rep.pressure_mmhg)),
            uv_int,
            round(rep.precip_mm, 1),
            pct_prob,
        )
        old_sig = self._hud_state.get("sig") if self._hud_state else None
        self._hud_state = {
            "sig": new_sig,
            "is_ru": is_ru,
            "temp": int(round(rep.temp_c)),
            "feels": int(round(rep.feels_like_c)),
            "hi": hi,
            "lo": lo,
            "wind_deg": float(rep.wind_deg),
            "wind_ms": float(rep.wind_ms),
            "humidity": int(round(rep.humidity)),
            "hum_f": float(rep.humidity),
            "mmhg": int(round(rep.pressure_mmhg)),
            "press_arc": max(0.05, min(1.0, (float(rep.pressure_mmhg) - 725.0) / 55.0)),
            "sun_frac": frac,
            "uv_int": uv_int,
            "uv_f": float(rep.uv_index),
            "precip_mm": float(rep.precip_mm),
            "pct_prob": pct_prob,
            "slot_temps": [0] * len(self._hour_slots),
        }

        self._last_rep = rep
        self._render_forecast_slots(animate=False)

        if rep.precip_next_hour:
            self.alert_lbl.setText(tr("weather_rain_soon"))
            self.alert_lbl.show()
        else:
            self.alert_lbl.hide()

        if self.isVisible():
            if not (self._hud_anim_timer.isActive() and old_sig == new_sig):
                self._start_hud_animation(forecast_only=False)
        else:
            self._apply_hud_values(1.0, forecast_only=False)

    def _set_forecast_mode(self, mode: str) -> None:
        self._fc_mode = "week" if mode == "week" else "hourly"
        self.btn_fc_hours.setChecked(self._fc_mode == "hourly")
        self.btn_fc_week.setChecked(self._fc_mode == "week")
        self._fc_title.setText("НЕДЕЛЯ" if self._fc_mode == "week" else "ПО ЧАСАМ")
        self._render_forecast_slots(animate=self.isVisible())

    def _render_forecast_slots(self, *, animate: bool = True) -> None:
        rep = self._last_rep
        if rep is None:
            return
        hourly = rep.hourly or []
        if self._fc_mode == "hourly":
            samples = [(h.time_local[:5], h.symbol, h.temp_c) for h in hourly[:4]]
        else:
            # Build up to 4 multi-day summaries from hourly buckets (~6-24h apart)
            samples = []
            step = max(1, min(6, len(hourly) // 4)) if len(hourly) > 4 else 1
            day_names = ["Сегодня", "Завтра", "Послез.", "Дальше"]
            for i in range(4):
                idx = min(len(hourly) - 1, i * step)
                if idx < len(hourly):
                    h = hourly[idx]
                    lbl = day_names[i] if len(hourly) > 8 else h.time_local[:5]
                    samples.append((lbl, h.symbol, h.temp_c))
        slot_targets: list[int | None] = []
        for idx, (ht, hi_lbl, hv) in enumerate(self._hour_slots):
            if idx < len(samples):
                t_txt, sym, tc = samples[idx]
                ht.setText(t_txt)
                hsym = (sym or "").lower()
                ico = "sun" if ("clear" in hsym or "fair" in hsym) else "cloud"
                hi_lbl.setPixmap(get_icon(ico, 14, "#F8FAFC", "#FACC15" if ico == "sun" else "#93C5FD").pixmap(14, 14))
                t_int = int(round(tc))
                slot_targets.append(t_int)
                if not animate:
                    hv.setText(f"{t_int}°")
            else:
                slot_targets.append(None)
        if self._hud_state:
            self._hud_state["slot_temps"] = slot_targets
        if animate:
            self._start_hud_animation(forecast_only=True)

    def _start_hud_animation(self, *, forecast_only: bool = False) -> None:
        if not self._hud_state:
            return
        self._hud_anim_forecast_only = forecast_only
        self._hud_anim_dur = 0.48 if forecast_only else 0.85
        self._hud_anim_start = time.monotonic()
        self._apply_hud_values(0.0, forecast_only=forecast_only)
        if not self._hud_anim_timer.isActive():
            self._hud_anim_timer.start()

    def _on_hud_anim_tick(self) -> None:
        if self._disposed or not self._hud_state:
            self._hud_anim_timer.stop()
            return
        raw_t = max(0.0, min(1.0, (time.monotonic() - self._hud_anim_start) / max(0.1, self._hud_anim_dur)))
        ease_t = 1.0 - (1.0 - raw_t) ** 4
        self._apply_hud_values(ease_t, forecast_only=self._hud_anim_forecast_only)
        if raw_t >= 1.0:
            self._hud_anim_timer.stop()

    def _apply_hud_values(self, ease_t: float, *, forecast_only: bool = False) -> None:
        st = self._hud_state
        if not st:
            return
        if not forecast_only:
            is_ru = st["is_ru"]
            cur_t = self._count_int(st["temp"], ease_t)
            cur_feels = self._count_int(st["feels"], ease_t)
            cur_hi = self._count_int(st["hi"], ease_t)
            cur_lo = self._count_int(st["lo"], ease_t)
            self.temp_lbl.setText(f"{cur_t:d}°")
            self.desc_lbl.setText(
                f"Ощущ. {cur_feels}° • ↑{cur_hi}° ↓{cur_lo}°" if is_ru else f"Feels {cur_feels}° • ↑{cur_hi}° ↓{cur_lo}°"
            )

            cur_wind = self._count_float(st["wind_ms"], ease_t)
            self.wind_spd_lbl.setText(f"{cur_wind:.1f} {'м/с' if is_ru else 'm/s'}")
            self.wind_compass.set_deg(st["wind_deg"] * ease_t)

            cur_hum = self._count_int(st["humidity"], ease_t)
            self.hum_val_lbl.setText(f"{cur_hum}%")
            self.hum_bar.set_pct(1.0 + (st["hum_f"] - 1.0) * ease_t if st["hum_f"] > 1.0 else st["hum_f"] * ease_t)

            cur_mmhg = self._count_int(st["mmhg"], ease_t)
            self.press_gauge.set_pressure(cur_mmhg, arc_frac=st["press_arc"] * ease_t)

            self.sun_arc.set_progress(st["sun_frac"] * ease_t)

            cur_uv = self._count_int(st["uv_int"], ease_t)
            self.uv_val_lbl.setText(str(cur_uv))
            self.uv_bar.set_uv(st["uv_f"] * ease_t)

            if st["precip_mm"] > 0.0:
                cur_pr = self._count_float(st["precip_mm"], ease_t)
                self.precip_val_lbl.setText(f"{cur_pr:.1f} мм")
            else:
                self.precip_val_lbl.setText("0 мм")
            cur_prob = self._count_int(st["pct_prob"], ease_t)
            self.precip_sub_lbl.setText(f"Вероятность {cur_prob}%" if is_ru else f"Chance {cur_prob}%")

        slot_targets = st.get("slot_temps") or []
        for idx, (_ht, _hi_lbl, hv) in enumerate(self._hour_slots):
            if idx < len(slot_targets) and slot_targets[idx] is not None:
                val = self._count_int(slot_targets[idx], ease_t)
                hv.setText(f"{val}°")
