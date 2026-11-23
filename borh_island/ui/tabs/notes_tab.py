"""Clock, Stopwatch, and 3-column Drum Timer tab (2x large layout, drag-scrollable wheels, custom vector icons)."""

from __future__ import annotations

import math
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.i18n import tr
from ...core.storage import LocalStorage
from ...services.reminder_parser import parse_duration_seconds
from ..icons import get_icon, get_pixmap

_RU_MONTHS = (
    "янв.", "февр.", "мар.", "апр.", "мая", "июн.",
    "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.",
)


class _RoundActionBtn(QPushButton):
    """Custom QPainter stadium/circle button that never gets overridden into a square by global QSS."""

    def __init__(self, icon_name: str, w: int = 136, h: int = 50, primary: bool = True, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._icon_name = icon_name
        self._primary = primary
        self._accent = "#3B82F6"
        self._hover = False
        self.setFixedSize(w, h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_icon_name(self, name: str) -> None:
        if self._icon_name != name:
            self._icon_name = name
            self.update()

    def set_accent(self, accent_hex: str) -> None:
        self._accent = accent_hex
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        rad = r.height() * 0.5
        if self._primary:
            bg = QColor(255, 255, 255, 42 if self._hover else 28)
            bd = QColor(self._accent if self._hover else "#FFFFFF")
            bd.setAlpha(150 if self._hover else 58)
            ico_col = self._accent
        else:
            bg = QColor(255, 255, 255, 30 if self._hover else 18)
            bd = QColor(255, 255, 255, 72 if self._hover else 38)
            ico_col = "#CBD5E1"
        p.setPen(QPen(bd, 1.4))
        p.setBrush(bg)
        p.drawRoundedRect(r, rad, rad)
        pm = get_pixmap(self._icon_name, size=20, color=ico_col, accent=self._accent)
        p.drawPixmap(int((self.width() - 20) * 0.5), int((self.height() - 20) * 0.5), pm)


class _AnalogClockWidget(QWidget):
    """Large circular analog clock face matching the phone World Clock reference."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(230, 230)
        self.setMaximumSize(280, 280)
        self._accent = QColor("#3B82F6")
        self._fg = QColor("#F8FAFC")
        self._bg = QColor(255, 255, 255, 16)

    def set_colors(self, accent_hex: str, fg_hex: str = "#F8FAFC") -> None:
        self._accent = QColor(accent_hex)
        self._fg = QColor(fg_hex)
        self.update()

    def paintEvent(self, event) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        sz = min(self.width(), self.height()) - 10.0
        cx = self.width() * 0.5
        cy = self.height() * 0.5
        r = sz * 0.5

        p.setPen(QPen(QColor(255, 255, 255, 34), 1.6))
        p.setBrush(self._bg)
        p.drawEllipse(QPointF(cx, cy), r, r)

        for m in range(60):
            ang = math.radians(m * 6.0 - 90.0)
            is_hr = (m % 5 == 0)
            r_out = r - 5.0
            r_in = r - (14.0 if is_hr else 8.5)
            p.setPen(QPen(QColor(255, 255, 255, 200 if is_hr else 75), 1.8 if is_hr else 1.1))
            p.drawLine(
                QPointF(cx + math.cos(ang) * r_in, cy + math.sin(ang) * r_in),
                QPointF(cx + math.cos(ang) * r_out, cy + math.sin(ang) * r_out),
            )

        p.setPen(self._fg)
        p.setFont(QFont("Segoe UI", max(10, int(round(r * 0.145))), QFont.Weight.DemiBold))
        r_num = r - 28.0
        for h_num in range(1, 13):
            ang = math.radians(h_num * 30.0 - 90.0)
            nx = cx + math.cos(ang) * r_num
            ny = cy + math.sin(ang) * r_num
            p.drawText(QRectF(nx - 18.0, ny - 13.0, 36.0, 26.0), Qt.AlignmentFlag.AlignCenter, str(h_num))

        now = datetime.now()
        sec = now.second + now.microsecond / 1_000_000.0
        minute = now.minute + sec / 60.0
        hour = (now.hour % 12) + minute / 60.0

        h_ang = math.radians(hour * 30.0 - 90.0)
        p.setPen(QPen(self._fg, 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(h_ang) * r * 0.50, cy + math.sin(h_ang) * r * 0.50))

        m_ang = math.radians(minute * 6.0 - 90.0)
        p.setPen(QPen(self._fg, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(m_ang) * r * 0.72, cy + math.sin(m_ang) * r * 0.72))

        s_ang = math.radians(sec * 6.0 - 90.0)
        p.setPen(QPen(self._accent, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(
            QPointF(cx - math.cos(s_ang) * r * 0.15, cy - math.sin(s_ang) * r * 0.15),
            QPointF(cx + math.cos(s_ang) * r * 0.80, cy + math.sin(s_ang) * r * 0.80),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)


class _DrumWheelColumn(QWidget):
    """Click-and-drag + wheel scrollable phone-style drum number picker column (2x large) with smooth 60fps animation."""

    value_changed = pyqtSignal(int)

    def __init__(self, max_val: int, value: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max = max(1, max_val)
        self._val = value % (self._max + 1)
        self._dragging = False
        self._drag_last_y = 0.0
        self._drag_accum = 0.0
        self._anim_offset = 0.0  # continuous fractional row offset, smoothly decays to 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick_anim)
        self.setFixedSize(126, 212)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def value(self) -> int:
        return self._val

    def set_value(self, v: int, animate_dir: float = 0.0) -> None:
        nv = v % (self._max + 1)
        if nv != self._val:
            self._val = nv
            if animate_dir != 0.0:
                self._anim_offset = max(-1.8, min(1.8, self._anim_offset + animate_dir))
                if not self._anim_timer.isActive():
                    self._anim_timer.start()
            self.update()
            self.value_changed.emit(self._val)

    def _tick_anim(self) -> None:
        if self._dragging:
            return
        self._anim_offset *= 0.76
        if abs(self._anim_offset) < 0.015:
            self._anim_offset = 0.0
            self._anim_timer.stop()
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta != 0:
            step = -1 if delta > 0 else 1
            self.set_value(self._val + step, animate_dir=float(step))
            event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_last_y = event.position().y()
            self._drag_accum = 0.0
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            y = event.position().y()
            dy = y - self._drag_last_y
            self._drag_last_y = y
            self._drag_accum += dy
            step_px = 34.0
            if abs(self._drag_accum) >= step_px:
                steps = int(self._drag_accum / step_px)
                self._drag_accum -= steps * step_px
                self.set_value(self._val - steps, animate_dir=0.0)
            self._anim_offset = self._drag_accum / step_px
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_accum = 0.0
            if abs(self._anim_offset) > 0.015 and not self._anim_timer.isActive():
                self._anim_timer.start()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            event.accept()

    def paintEvent(self, event) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.height())
        mod = self._max + 1

        for off in (-3, -2, -1, 0, 1, 2, 3):
            eff = float(off) + self._anim_offset
            if abs(eff) > 2.6:
                continue
            num = (self._val + off) % mod
            dist = min(1.0, abs(eff) / 2.2)
            y_frac = 0.50 + eff * 0.19
            pt_sz = int(round(44.0 - dist * 26.0))
            alpha = int(round(max(25.0, 255.0 * (1.0 - dist * 0.82))))
            col = QColor(248, 250, 252, alpha) if abs(eff) < 0.45 else QColor(148, 163, 184, alpha)
            p.setPen(col)
            p.setFont(QFont("Segoe UI", pt_sz, QFont.Weight.DemiBold if abs(eff) < 0.45 else QFont.Weight.Normal))
            cy = h * y_frac
            p.drawText(QRectF(0.0, cy - 30.0, w, 60.0), Qt.AlignmentFlag.AlignCenter, f"{num:02d}")


class NotesTab(QWidget):
    """Phone-style 2x large Clock tab: World Clock, Stopwatch, and 3-column Drag Drum Timer."""

    timer_updated = pyqtSignal(int, bool, str)
    timer_finished = pyqtSignal(str)
    reminder_triggered = pyqtSignal(object)

    def __init__(self, storage: LocalStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self._accent_hex = "#3B82F6"
        self._icon_fg = "#F8FAFC"

        self._timer_total = 300
        self._timer_remaining = 300
        self._timer_running = False
        self._timer_label = tr("timer")

        self._sw_running = False
        self._sw_elapsed_ms = 0
        self._sw_last_ts = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 10)
        root.setSpacing(10)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # ---- Page 0: World Clock (Мировые часы) ----
        pg_clock = QWidget()
        cl_lay = QVBoxLayout(pg_clock)
        cl_lay.setContentsMargins(0, 4, 0, 4)
        cl_lay.setSpacing(8)
        cl_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._analog_clock = _AnalogClockWidget()
        self._digital_lbl = QLabel("00:00:00")
        self._digital_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._digital_lbl.setStyleSheet("font-size: 36px; font-weight: 600; color: #F8FAFC; background: transparent;")
        self._local_date_lbl = QLabel("")
        self._local_date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._local_date_lbl.setStyleSheet("font-size: 14px; color: #94A3B8; background: transparent;")
        cl_lay.addWidget(self._analog_clock, 0, Qt.AlignmentFlag.AlignCenter)
        cl_lay.addWidget(self._digital_lbl)
        cl_lay.addWidget(self._local_date_lbl)
        self.stack.addWidget(pg_clock)

        # ---- Page 1: Stopwatch (Секундомер) ----
        pg_sw = QWidget()
        sw_lay = QVBoxLayout(pg_sw)
        sw_lay.setContentsMargins(0, 12, 0, 12)
        sw_lay.setSpacing(26)
        sw_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._sw_lbl = QLabel("00:00.00")
        self._sw_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sw_lbl.setStyleSheet("font-size: 64px; font-weight: 500; color: #F8FAFC; background: transparent;")
        sw_btn_row = QHBoxLayout()
        sw_btn_row.setSpacing(16)
        sw_btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sw_play_btn = _RoundActionBtn("play", 144, 52, primary=True)
        self._sw_play_btn.clicked.connect(self._toggle_stopwatch)
        self._sw_reset_btn = _RoundActionBtn("refresh", 64, 52, primary=False)
        self._sw_reset_btn.clicked.connect(self._reset_stopwatch)
        sw_btn_row.addWidget(self._sw_play_btn)
        sw_btn_row.addWidget(self._sw_reset_btn)
        sw_lay.addWidget(self._sw_lbl)
        sw_lay.addLayout(sw_btn_row)
        self.stack.addWidget(pg_sw)

        # ---- Page 2: 3-column Drag Drum Timer (Таймер) ----
        pg_tm = QWidget()
        tm_lay = QVBoxLayout(pg_tm)
        tm_lay.setContentsMargins(0, 4, 0, 4)
        tm_lay.setSpacing(14)
        tm_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        drum_row = QHBoxLayout()
        drum_row.setSpacing(18)
        drum_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._col_h = _DrumWheelColumn(23, 0)
        self._col_m = _DrumWheelColumn(59, 5)
        self._col_s = _DrumWheelColumn(59, 0)
        for col_w in (self._col_h, self._col_m, self._col_s):
            col_w.value_changed.connect(self._on_drum_changed)
        sep1 = QLabel(":")
        sep2 = QLabel(":")
        for sp in (sep1, sep2):
            sp.setStyleSheet("font-size: 40px; font-weight: 700; color: #F8FAFC; background: transparent;")
            sp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drum_row.addWidget(self._col_h)
        drum_row.addWidget(sep1)
        drum_row.addWidget(self._col_m)
        drum_row.addWidget(sep2)
        drum_row.addWidget(self._col_s)
        tm_lay.addLayout(drum_row)

        tm_btn_row = QHBoxLayout()
        tm_btn_row.setSpacing(16)
        tm_btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_start_btn = _RoundActionBtn("play", 144, 52, primary=True)
        self.timer_start_btn.clicked.connect(self._toggle_timer)
        self.timer_reset_btn = _RoundActionBtn("refresh", 64, 52, primary=False)
        self.timer_reset_btn.clicked.connect(self._reset_timer)
        tm_btn_row.addWidget(self.timer_start_btn)
        tm_btn_row.addWidget(self.timer_reset_btn)
        tm_lay.addLayout(tm_btn_row)
        self.stack.addWidget(pg_tm)

        # ---- Bottom Phone-style Segmented Pill Bar with Custom Vector Icons ----
        nav_frame = QFrame()
        nav_frame.setObjectName("ClockNavBar")
        nav_frame.setStyleSheet(
            "QFrame#ClockNavBar { background: rgba(255, 255, 255, 0.08); "
            "border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 24px; }"
        )
        nav_lay = QHBoxLayout(nav_frame)
        nav_lay.setContentsMargins(6, 5, 6, 5)
        nav_lay.setSpacing(6)

        self.btn_mode_clock = QPushButton("  Мировые часы")
        self.btn_mode_sw = QPushButton("  Секундомер")
        self.btn_mode_timer = QPushButton("  Таймер")
        self.btn_mode_notes = self.btn_mode_clock
        self.btn_mode_rem = self.btn_mode_sw

        for idx, b in enumerate((self.btn_mode_clock, self.btn_mode_sw, self.btn_mode_timer)):
            b.setCheckable(True)
            b.setMinimumHeight(40)
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=idx: self._switch_mode(i))
            nav_lay.addWidget(b, 1)

        root.addWidget(nav_frame)

        self._ui_tick = QTimer(self)
        self._ui_tick.setInterval(45)
        self._ui_tick.timeout.connect(self._on_ui_tick)
        self._ui_tick.start()

        self._countdown_tick = QTimer(self)
        self._countdown_tick.setInterval(1000)
        self._countdown_tick.timeout.connect(self._on_countdown_tick)

        self._switch_mode(0)
        self.apply_palette(None)
        self._refresh_clock_labels()

    def _switch_mode(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate((self.btn_mode_clock, self.btn_mode_sw, self.btn_mode_timer)):
            b.setChecked(i == idx)
        self._style_buttons()

    def _style_buttons(self) -> None:
        self._sw_play_btn.set_accent(self._accent_hex)
        self._sw_reset_btn.set_accent(self._accent_hex)
        self.timer_start_btn.set_accent(self._accent_hex)
        self.timer_reset_btn.set_accent(self._accent_hex)

        nav_qss = (
            "QPushButton { min-height: 40px; max-height: 40px; border-radius: 20px; background: transparent; "
            "color: #94A3B8; font-size: 12px; font-weight: 600; border: none; padding: 0px 14px; }"
            "QPushButton:hover { color: #F8FAFC; }"
            "QPushButton:checked { background: rgba(255, 255, 255, 0.18); color: #FFFFFF; }"
        )
        icons = ("clock", "timer", "bell")
        for i, b in enumerate((self.btn_mode_clock, self.btn_mode_sw, self.btn_mode_timer)):
            b.setStyleSheet(nav_qss)
            fg = "#FFFFFF" if b.isChecked() else "#94A3B8"
            b.setIcon(get_icon(icons[i], size=16, color=fg, accent=self._accent_hex))

    def apply_palette(self, pal) -> None:
        if pal is not None:
            self._accent_hex = getattr(pal, "accent", "#3B82F6")
            self._icon_fg = getattr(pal, "text_primary", "#F8FAFC")
        self._analog_clock.set_colors(self._accent_hex, self._icon_fg)
        self._style_buttons()

    def retranslate_ui(self) -> None:
        self.btn_mode_clock.setText("  Мировые часы")
        self.btn_mode_sw.setText("  Секундомер")
        self.btn_mode_timer.setText("  Таймер")

    def _refresh_clock_labels(self) -> None:
        now = datetime.now()
        self._digital_lbl.setText(now.strftime("%H:%M:%S"))
        mon = _RU_MONTHS[now.month - 1]
        self._local_date_lbl.setText(f"Местное время  {now.day} {mon}")

    def _on_ui_tick(self) -> None:
        idx = self.stack.currentIndex()
        if idx == 0 and self.isVisible():
            self._analog_clock.update()
            self._refresh_clock_labels()
        if self._sw_running:
            now_m = time.monotonic()
            self._sw_elapsed_ms += int(round((now_m - self._sw_last_ts) * 1000.0))
            self._sw_last_ts = now_m
            tot_cs = self._sw_elapsed_ms // 10
            cs = tot_cs % 100
            sec = (tot_cs // 100) % 60
            mins = (tot_cs // 6000) % 60
            hrs = tot_cs // 360000
            if hrs > 0:
                self._sw_lbl.setText(f"{hrs:02d}:{mins:02d}:{sec:02d}.{cs:02d}")
            else:
                self._sw_lbl.setText(f"{mins:02d}:{sec:02d}.{cs:02d}")

    def _toggle_stopwatch(self) -> None:
        if self._sw_running:
            self._sw_running = False
            self._sw_play_btn.set_icon_name("play")
        else:
            self._sw_running = True
            self._sw_last_ts = time.monotonic()
            self._sw_play_btn.set_icon_name("pause")

    def _reset_stopwatch(self) -> None:
        self._sw_running = False
        self._sw_elapsed_ms = 0
        self._sw_play_btn.set_icon_name("play")
        self._sw_lbl.setText("00:00.00")

    def _on_drum_changed(self, _v: int = 0) -> None:
        if not self._timer_running:
            self._timer_total = self._col_h.value() * 3600 + self._col_m.value() * 60 + self._col_s.value()
            self._timer_remaining = self._timer_total

    def _toggle_timer(self) -> None:
        if self._timer_running:
            self._timer_running = False
            self._countdown_tick.stop()
            self.timer_start_btn.set_icon_name("play")
            self.timer_updated.emit(self._timer_remaining, False, self._timer_label)
        else:
            if self._timer_remaining <= 0:
                self._on_drum_changed()
            if self._timer_remaining <= 0:
                return
            self._timer_running = True
            self._countdown_tick.start()
            self.timer_start_btn.set_icon_name("pause")
            self.timer_updated.emit(self._timer_remaining, True, self._timer_label)

    def _reset_timer(self) -> None:
        self._timer_running = False
        self._countdown_tick.stop()
        self.timer_start_btn.set_icon_name("play")
        self._col_h.set_value(0)
        self._col_m.set_value(5)
        self._col_s.set_value(0)
        self._timer_total = 300
        self._timer_remaining = 300
        self.timer_updated.emit(0, False, self._timer_label)

    def _on_countdown_tick(self) -> None:
        if not self._timer_running:
            return
        self._timer_remaining = max(0, self._timer_remaining - 1)
        self._update_timer_display()
        self.timer_updated.emit(self._timer_remaining, self._timer_remaining > 0, self._timer_label)
        if self._timer_remaining <= 0:
            self._timer_running = False
            self._countdown_tick.stop()
            self.timer_start_btn.set_icon_name("play")
            self.timer_finished.emit(self._timer_label)

    def _update_timer_display(self) -> None:
        rem = max(0, int(self._timer_remaining))
        h = rem // 3600
        m = (rem % 3600) // 60
        s = rem % 60
        for col_w, val in ((self._col_h, h), (self._col_m, m), (self._col_s, s)):
            col_w.blockSignals(True)
            col_w.set_value(val)
            col_w.blockSignals(False)

    def start_timer(self, seconds: int, label: str = "") -> None:
        self._timer_total = max(1, int(seconds))
        self._timer_remaining = self._timer_total
        self._timer_label = label or tr("timer")
        self._timer_running = True
        self._update_timer_display()
        self._countdown_tick.start()
        self.timer_start_btn.set_icon_name("pause")
        self._switch_mode(2)
        self.timer_updated.emit(self._timer_remaining, True, self._timer_label)

    def refresh_reminders(self) -> None:
        return None

    def _reload_notes(self) -> None:
        return None

    def add_reminder_phrase(self, phrase: str) -> bool:
        sec = parse_duration_seconds(phrase)
        if sec and sec > 0:
            self.start_timer(sec, phrase)
            return True
        return False
