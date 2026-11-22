"""Clock, Stopwatch, and 3-column Drum Timer tab (adapted from phone Clock UI)."""

from __future__ import annotations

import math
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
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
from ..icons import get_icon

_RU_MONTHS = (
    "янв.", "февр.", "мар.", "апр.", "мая", "июн.",
    "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.",
)


class _AnalogClockWidget(QWidget):
    """Crisp circular analog clock face matching the phone World Clock reference."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(176, 176)
        self.setMaximumSize(220, 220)
        self._accent = QColor("#3B82F6")
        self._fg = QColor("#F8FAFC")
        self._muted = QColor("#94A3B8")
        self._bg = QColor(255, 255, 255, 14)

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

        # Subtle dial face
        p.setPen(QPen(QColor(255, 255, 255, 28), 1.4))
        p.setBrush(self._bg)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # 60 minute/hour tick marks
        for m in range(60):
            ang = math.radians(m * 6.0 - 90.0)
            is_hr = (m % 5 == 0)
            r_out = r - 4.0
            r_in = r - (11.0 if is_hr else 7.0)
            p.setPen(QPen(QColor(255, 255, 255, 190 if is_hr else 70), 1.6 if is_hr else 1.0))
            p.drawLine(
                QPointF(cx + math.cos(ang) * r_in, cy + math.sin(ang) * r_in),
                QPointF(cx + math.cos(ang) * r_out, cy + math.sin(ang) * r_out),
            )

        # 1..12 numerals
        p.setPen(self._fg)
        p.setFont(QFont("Segoe UI", max(8, int(round(r * 0.145))), QFont.Weight.DemiBold))
        r_num = r - 22.0
        for h_num in range(1, 13):
            ang = math.radians(h_num * 30.0 - 90.0)
            nx = cx + math.cos(ang) * r_num
            ny = cy + math.sin(ang) * r_num
            p.drawText(QRectF(nx - 14.0, ny - 10.0, 28.0, 20.0), Qt.AlignmentFlag.AlignCenter, str(h_num))

        now = datetime.now()
        sec = now.second + now.microsecond / 1_000_000.0
        minute = now.minute + sec / 60.0
        hour = (now.hour % 12) + minute / 60.0

        # Hour hand
        h_ang = math.radians(hour * 30.0 - 90.0)
        p.setPen(QPen(self._fg, 3.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(h_ang) * r * 0.50, cy + math.sin(h_ang) * r * 0.50))

        # Minute hand
        m_ang = math.radians(minute * 6.0 - 90.0)
        p.setPen(QPen(self._fg, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(m_ang) * r * 0.72, cy + math.sin(m_ang) * r * 0.72))

        # Blue second hand (matching phone reference)
        s_ang = math.radians(sec * 6.0 - 90.0)
        p.setPen(QPen(self._accent, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(
            QPointF(cx - math.cos(s_ang) * r * 0.14, cy - math.sin(s_ang) * r * 0.14),
            QPointF(cx + math.cos(s_ang) * r * 0.80, cy + math.sin(s_ang) * r * 0.80),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        p.drawEllipse(QPointF(cx, cy), 3.8, 3.8)


class _DrumWheelColumn(QWidget):
    """Scrollable phone-style drum number picker column (prev, active, next)."""

    value_changed = pyqtSignal(int)

    def __init__(self, max_val: int, value: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max = max(1, max_val)
        self._val = value % (self._max + 1)
        self.setFixedSize(76, 156)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def value(self) -> int:
        return self._val

    def set_value(self, v: int) -> None:
        nv = v % (self._max + 1)
        if nv != self._val:
            self._val = nv
            self.update()
            self.value_changed.emit(self._val)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta != 0:
            self.set_value(self._val + (-1 if delta > 0 else 1))
            event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.position().y()
            if y < self.height() * 0.36:
                self.set_value(self._val - 1)
            elif y > self.height() * 0.64:
                self.set_value(self._val + 1)
            event.accept()

    def paintEvent(self, event) -> None:
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.height())
        mod = self._max + 1

        rows = (
            (-2, 0.14, 14, QColor(148, 163, 184, 55)),
            (-1, 0.30, 20, QColor(148, 163, 184, 155)),
            (0, 0.50, 32, QColor(248, 250, 252, 255)),
            (1, 0.70, 20, QColor(148, 163, 184, 155)),
            (2, 0.86, 14, QColor(148, 163, 184, 55)),
        )
        for off, y_frac, pt_sz, col in rows:
            num = (self._val + off) % mod
            p.setPen(col)
            p.setFont(QFont("Segoe UI", pt_sz, QFont.Weight.DemiBold if off == 0 else QFont.Weight.Normal))
            cy = h * y_frac
            p.drawText(QRectF(0.0, cy - 20.0, w, 40.0), Qt.AlignmentFlag.AlignCenter, f"{num:02d}")


class NotesTab(QWidget):
    """Phone-style Clock tab: World Clock, Stopwatch, and 3-column Drum Timer (keeps NotesTab API for main.py)."""

    timer_updated = pyqtSignal(int, bool, str)  # (remaining_seconds, running, label)
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
        root.setContentsMargins(10, 6, 10, 8)
        root.setSpacing(8)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # ---- Page 0: World Clock (Мировые часы) ----
        pg_clock = QWidget()
        cl_lay = QVBoxLayout(pg_clock)
        cl_lay.setContentsMargins(0, 2, 0, 2)
        cl_lay.setSpacing(6)
        cl_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._analog_clock = _AnalogClockWidget()
        self._digital_lbl = QLabel("00:00:00")
        self._digital_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._digital_lbl.setStyleSheet("font-size: 26px; font-weight: 600; color: #F8FAFC; background: transparent;")
        self._local_date_lbl = QLabel("")
        self._local_date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._local_date_lbl.setStyleSheet("font-size: 12px; color: #94A3B8; background: transparent;")
        cl_lay.addWidget(self._analog_clock, 0, Qt.AlignmentFlag.AlignCenter)
        cl_lay.addWidget(self._digital_lbl)
        cl_lay.addWidget(self._local_date_lbl)
        self.stack.addWidget(pg_clock)

        # ---- Page 1: Stopwatch (Секундомер) ----
        pg_sw = QWidget()
        sw_lay = QVBoxLayout(pg_sw)
        sw_lay.setContentsMargins(0, 8, 0, 8)
        sw_lay.setSpacing(18)
        sw_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._sw_lbl = QLabel("00:00.00")
        self._sw_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sw_lbl.setStyleSheet("font-size: 42px; font-weight: 500; color: #F8FAFC; background: transparent;")
        sw_btn_row = QHBoxLayout()
        sw_btn_row.setSpacing(12)
        sw_btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sw_play_btn = QPushButton("▶")
        self._sw_play_btn.setFixedSize(96, 42)
        self._sw_play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sw_play_btn.clicked.connect(self._toggle_stopwatch)
        self._sw_reset_btn = QPushButton("↺")
        self._sw_reset_btn.setFixedSize(48, 42)
        self._sw_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sw_reset_btn.clicked.connect(self._reset_stopwatch)
        sw_btn_row.addWidget(self._sw_play_btn)
        sw_btn_row.addWidget(self._sw_reset_btn)
        sw_lay.addWidget(self._sw_lbl)
        sw_lay.addLayout(sw_btn_row)
        self.stack.addWidget(pg_sw)

        # ---- Page 2: 3-column Drum Timer (Таймер) ----
        pg_tm = QWidget()
        tm_lay = QVBoxLayout(pg_tm)
        tm_lay.setContentsMargins(0, 4, 0, 4)
        tm_lay.setSpacing(10)
        tm_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        drum_row = QHBoxLayout()
        drum_row.setSpacing(6)
        drum_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._col_h = _DrumWheelColumn(23, 0)
        self._col_m = _DrumWheelColumn(59, 5)
        self._col_s = _DrumWheelColumn(59, 0)
        for col_w in (self._col_h, self._col_m, self._col_s):
            col_w.value_changed.connect(self._on_drum_changed)
        sep1 = QLabel(":")
        sep2 = QLabel(":")
        for sp in (sep1, sep2):
            sp.setStyleSheet("font-size: 28px; font-weight: 700; color: #F8FAFC; background: transparent;")
            sp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drum_row.addWidget(self._col_h)
        drum_row.addWidget(sep1)
        drum_row.addWidget(self._col_m)
        drum_row.addWidget(sep2)
        drum_row.addWidget(self._col_s)
        tm_lay.addLayout(drum_row)

        tm_btn_row = QHBoxLayout()
        tm_btn_row.setSpacing(12)
        tm_btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_start_btn = QPushButton("▶")
        self.timer_start_btn.setFixedSize(96, 42)
        self.timer_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timer_start_btn.clicked.connect(self._toggle_timer)
        self.timer_reset_btn = QPushButton("↺")
        self.timer_reset_btn.setFixedSize(48, 42)
        self.timer_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timer_reset_btn.clicked.connect(self._reset_timer)
        tm_btn_row.addWidget(self.timer_start_btn)
        tm_btn_row.addWidget(self.timer_reset_btn)
        tm_lay.addLayout(tm_btn_row)
        self.stack.addWidget(pg_tm)

        # ---- Bottom Phone-style Segmented Pill Bar ----
        nav_frame = QFrame()
        nav_frame.setObjectName("ClockNavBar")
        nav_frame.setStyleSheet(
            "QFrame#ClockNavBar { background: rgba(255, 255, 255, 0.08); "
            "border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 22px; }"
        )
        nav_lay = QHBoxLayout(nav_frame)
        nav_lay.setContentsMargins(5, 4, 5, 4)
        nav_lay.setSpacing(4)

        self.btn_mode_clock = QPushButton("Мировые часы")
        self.btn_mode_sw = QPushButton("Секундомер")
        self.btn_mode_timer = QPushButton("Таймер")
        # Compatibility aliases for tests
        self.btn_mode_notes = self.btn_mode_clock
        self.btn_mode_rem = self.btn_mode_sw

        for idx, b in enumerate((self.btn_mode_clock, self.btn_mode_sw, self.btn_mode_timer)):
            b.setCheckable(True)
            b.setMinimumHeight(36)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=idx: self._switch_mode(i))
            nav_lay.addWidget(b, 1)

        root.addWidget(nav_frame)

        # Timers
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
        pill_play = (
            f"QPushButton {{ background: rgba(255,255,255,0.14); color: {self._accent_hex}; "
            f"border: 1px solid rgba(255,255,255,0.22); border-radius: 21px; font-size: 16px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.22); }}"
        )
        pill_reset = (
            "QPushButton { background: rgba(255,255,255,0.07); color: #CBD5E1; "
            "border: 1px solid rgba(255,255,255,0.14); border-radius: 21px; font-size: 16px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.14); }"
        )
        self._sw_play_btn.setStyleSheet(pill_play)
        self._sw_reset_btn.setStyleSheet(pill_reset)
        self.timer_start_btn.setStyleSheet(pill_play)
        self.timer_reset_btn.setStyleSheet(pill_reset)

        nav_qss = (
            "QPushButton { min-height: 36px; border-radius: 18px; background: transparent; "
            "color: #94A3B8; font-size: 11px; font-weight: 600; border: none; padding: 0px 8px; }"
            "QPushButton:hover { color: #F8FAFC; }"
            "QPushButton:checked { background: rgba(255, 255, 255, 0.16); color: #FFFFFF; }"
        )
        for b in (self.btn_mode_clock, self.btn_mode_sw, self.btn_mode_timer):
            b.setStyleSheet(nav_qss)

    def apply_palette(self, pal) -> None:
        if pal is not None:
            self._accent_hex = getattr(pal, "accent", "#3B82F6")
            self._icon_fg = getattr(pal, "text_primary", "#F8FAFC")
        self._analog_clock.set_colors(self._accent_hex, self._icon_fg)
        self._style_buttons()

    def retranslate_ui(self) -> None:
        ru = True
        self.btn_mode_clock.setText("Мировые часы" if ru else "World Clock")
        self.btn_mode_sw.setText("Секундомер" if ru else "Stopwatch")
        self.btn_mode_timer.setText("Таймер" if ru else "Timer")

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
            self._sw_play_btn.setText("▶")
        else:
            self._sw_running = True
            self._sw_last_ts = time.monotonic()
            self._sw_play_btn.setText("⏸")

    def _reset_stopwatch(self) -> None:
        self._sw_running = False
        self._sw_elapsed_ms = 0
        self._sw_play_btn.setText("▶")
        self._sw_lbl.setText("00:00.00")

    def _on_drum_changed(self, _v: int = 0) -> None:
        if not self._timer_running:
            self._timer_total = self._col_h.value() * 3600 + self._col_m.value() * 60 + self._col_s.value()
            self._timer_remaining = self._timer_total

    def _toggle_timer(self) -> None:
        if self._timer_running:
            self._timer_running = False
            self._countdown_tick.stop()
            self.timer_start_btn.setText("▶")
            self.timer_updated.emit(self._timer_remaining, False, self._timer_label)
        else:
            if self._timer_remaining <= 0:
                self._on_drum_changed()
            if self._timer_remaining <= 0:
                return
            self._timer_running = True
            self._countdown_tick.start()
            self.timer_start_btn.setText("⏸")
            self.timer_updated.emit(self._timer_remaining, True, self._timer_label)

    def _reset_timer(self) -> None:
        self._timer_running = False
        self._countdown_tick.stop()
        self.timer_start_btn.setText("▶")
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
            self.timer_start_btn.setText("▶")
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

    # Compatibility methods called by main.py
    def start_timer(self, seconds: int, label: str = "") -> None:
        self._timer_total = max(1, int(seconds))
        self._timer_remaining = self._timer_total
        self._timer_label = label or tr("timer")
        self._timer_running = True
        self._update_timer_display()
        self._countdown_tick.start()
        self.timer_start_btn.setText("⏸")
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
