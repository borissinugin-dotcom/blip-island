"""Bespoke app-styled HSV Color Wheel widget & dialog + safe non-native media file picker."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QConicalGradient,
    QCursor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.win32_api import raise_hwnd_topmost
from .theme import ThemePalette, contrast_fg


def pick_bg_media_file(
    parent_widget: QWidget | None,
    title: str,
    name_filter: str,
) -> str:
    """Open a safe, always-on-top dark-glass Qt file picker (avoids Win32 COM 0x8001010e crashes and keeps Island open)."""
    from pathlib import Path
    from PyQt6.QtCore import QTimer

    win = parent_widget.window() if parent_widget is not None else None
    if win is not None and hasattr(win, "push_modal_hold"):
        win.push_modal_hold()
    try:
        pic_dir = Path.home() / "Pictures"
        start_dir = str(pic_dir if pic_dir.is_dir() else Path.home())
        # Use parent=None so QFileDialog never inherits frameless/translucent/tool flags from IslandWindow
        dlg = QFileDialog(None, title, start_dir, name_filter)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dlg.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        dlg.setStyleSheet(
            "QFileDialog { background: #101420; color: #F8FAFC; font-size: 12px; }"
            "QWidget { background: #101420; color: #F8FAFC; }"
            "QLineEdit, QComboBox { background: #1A2033; color: #F8FAFC; border: 1px solid #334155; "
            "border-radius: 8px; padding: 4px 8px; }"
            "QTreeView, QListView { background: #141928; color: #F8FAFC; border: 1px solid #2A344B; "
            "border-radius: 8px; selection-background-color: #3B82F6; }"
            "QPushButton { background: #1E293B; color: #F8FAFC; border: 1px solid #3B4963; "
            "border-radius: 8px; padding: 5px 14px; font-weight: 600; }"
            "QPushButton:hover { border-color: #60A5FA; background: #283548; }"
        )
        dlg.resize(700, 460)
        QTimer.singleShot(
            35,
            lambda: (
                raise_hwnd_topmost(int(dlg.winId())),
                dlg.raise_(),
                dlg.activateWindow(),
            )
            if dlg.isVisible()
            else None,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            files = dlg.selectedFiles()
            return str(files[0]) if files else ""
        return ""
    finally:
        if win is not None and hasattr(win, "pop_modal_hold"):
            win.pop_modal_hold()


class _HSVWheelWidget(QWidget):
    """Hardware-cached anti-aliased HSV Hue/Saturation wheel (240+ FPS zero-lag dragging)."""

    hsv_changed = pyqtSignal(float, float)  # (h 0..1, s 0..1)
    interaction_finished = pyqtSignal()

    def __init__(self, size_px: int = 188, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._h = 0.60
        self._s = 0.78
        self._v = 0.96
        self._dragging = False
        self._cache_key: tuple[int, int, float, int] | None = None
        self._cache_pix: QPixmap | None = None
        self.setFixedSize(size_px, size_px)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_hsv(self, h: float, s: float, v: float) -> None:
        self._h = max(0.0, min(1.0, float(h)))
        self._s = max(0.0, min(1.0, float(s)))
        nv = max(0.15, min(1.0, float(v)))
        if abs(nv - self._v) > 0.008:
            self._v = nv
            self._cache_key = None
        self.update()

    def _update_from_pos(self, pt: QPointF) -> None:
        cx = self.width() * 0.5
        cy = self.height() * 0.5
        rad = min(cx, cy) - 8.0
        dx = pt.x() - cx
        dy = cy - pt.y()
        dist = math.hypot(dx, dy)
        angle = math.atan2(dy, dx)
        if angle < 0:
            angle += 2.0 * math.pi
        self._h = max(0.0, min(1.0, angle / (2.0 * math.pi)))
        self._s = max(0.0, min(1.0, dist / max(1.0, rad)))
        self.hsv_changed.emit(self._h, self._s)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_from_pos(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._update_from_pos(event.position())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.interaction_finished.emit()

    def _ensure_wheel_pixmap(self) -> QPixmap:
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        w, h = self.width(), self.height()
        v_q = int(round(self._v * 100))
        key = (w, h, round(dpr, 2), v_q)
        if self._cache_pix is not None and self._cache_key == key:
            return self._cache_pix

        pix = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = w * 0.5, h * 0.5
        rad = min(cx, cy) - 8.0
        wheel_rect = QRectF(cx - rad, cy - rad, rad * 2.0, rad * 2.0)

        clip = QPainterPath()
        clip.addEllipse(wheel_rect)
        p.save()
        p.setClipPath(clip)

        conical = QConicalGradient(QPointF(cx, cy), 0.0)
        for i in range(13):
            frac = i / 12.0
            conical.setColorAt(frac, QColor.fromHsvF(frac % 1.0, 1.0, self._v))
        p.fillRect(wheel_rect, conical)

        radial = QRadialGradient(QPointF(cx, cy), rad)
        radial.setColorAt(0.0, QColor.fromHsvF(0.0, 0.0, self._v, 1.0))
        radial.setColorAt(1.0, QColor.fromHsvF(0.0, 0.0, self._v, 0.0))
        p.fillRect(wheel_rect, radial)
        p.restore()

        p.setPen(QPen(QColor(255, 255, 255, 55), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(wheel_rect)
        p.end()

        self._cache_key = key
        self._cache_pix = pix
        return pix

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._ensure_wheel_pixmap())

        cx = self.width() * 0.5
        cy = self.height() * 0.5
        rad = min(cx, cy) - 8.0
        ang = self._h * 2.0 * math.pi
        tx = cx + math.cos(ang) * (self._s * rad)
        ty = cy - math.sin(ang) * (self._s * rad)
        cur_col = QColor.fromHsvF(self._h, self._s, self._v)

        p.setPen(QPen(QColor(0, 0, 0, 175), 3.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(tx, ty), 8.0, 8.0)

        p.setPen(QPen(QColor("#FFFFFF"), 2.0))
        p.setBrush(cur_col)
        p.drawEllipse(QPointF(tx, ty), 7.0, 7.0)
        p.end()


class _ColorPreviewPill(QWidget):
    """Fast GPU-friendly QPainter pill previewing active hex color with zero QSS parsing overhead."""

    def __init__(self, hex_col: str = "#3B82F6", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hex = hex_col
        self.setFixedHeight(30)

    def set_color(self, hex_col: str) -> None:
        if hex_col != self._hex:
            self._hex = hex_col
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        from PyQt6.QtGui import QLinearGradient

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor("#141926"))
        g.setColorAt(1.0, QColor(self._hex))
        p.setPen(QPen(QColor(self._hex), 1.5))
        p.setBrush(g)
        p.drawRoundedRect(r, 14.0, 14.0)
        p.setPen(QColor("#F8FAFC"))
        f = p.font()
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"Островок • {self._hex}")
        p.end()


class BorhColorWheelPanel(QWidget):
    """Reusable throttled HSV color wheel panel embedded in ControlsTab or hosted inside BorhColorWheelDialog."""

    color_previewed = pyqtSignal(str)  # #RRGGBB throttled during drag (~55ms)
    color_committed = pyqtSignal(str)  # #RRGGBB final commit on release / click

    def __init__(
        self,
        initial_hex: str = "#3B82F6",
        *,
        wheel_size: int = 180,
        horizontal: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        c = QColor(initial_hex if QColor(initial_hex).isValid() else "#3B82F6")
        self._cur_color = c
        self._h = max(0.0, c.hueF())
        self._s = max(0.0, c.saturationF())
        self._v = max(0.15, c.valueF())
        self._pending_preview_hex: str = ""

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(55)
        self._preview_timer.timeout.connect(self._flush_preview)

        self.wheel = _HSVWheelWidget(wheel_size, self)
        self.wheel.set_hsv(self._h, self._s, self._v)
        self.wheel.hsv_changed.connect(self._on_wheel_hsv)
        self.wheel.interaction_finished.connect(self._commit_current)

        val_row = QHBoxLayout()
        val_lbl = QLabel("Яркость")
        val_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 700; background: transparent;")
        self.val_slider = QSlider(Qt.Orientation.Horizontal)
        self.val_slider.setRange(15, 100)
        self.val_slider.setValue(int(round(self._v * 100)))
        self.val_slider.valueChanged.connect(self._on_val_changed)
        self.val_slider.sliderReleased.connect(self._commit_current)
        val_row.addWidget(val_lbl)
        val_row.addWidget(self.val_slider, 1)

        prev_row = QHBoxLayout()
        prev_row.setSpacing(6)
        self.preview_pill = _ColorPreviewPill(c.name().upper(), self)
        prev_row.addWidget(self.preview_pill, 1)

        self.hex_edit = QLineEdit(c.name().upper())
        self.hex_edit.setFixedWidth(82)
        self.hex_edit.setFixedHeight(30)
        self.hex_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hex_edit.textEdited.connect(self._on_hex_edited)
        self.hex_edit.editingFinished.connect(self._commit_current)
        prev_row.addWidget(self.hex_edit)

        sw_row = QHBoxLayout()
        sw_row.setSpacing(4)
        for hx in ("#3B82F6", "#8B5CF6", "#EC4899", "#EF4444", "#F59E0B", "#10B981", "#06B6D4", "#A855F7"):
            sb = QPushButton("")
            sb.setCursor(Qt.CursorShape.PointingHandCursor)
            sb.setFixedSize(20, 20)
            sb.setStyleSheet(
                f"QPushButton {{ min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; "
                f"border-radius: 10px; background: {hx}; border: 1px solid rgba(255,255,255,65); padding: 0px; }}"
                "QPushButton:hover { border: 2px solid #FFFFFF; }"
            )
            sb.clicked.connect(lambda _=False, c_hx=hx: self.set_hex(c_hx, commit=True))
            sw_row.addWidget(sb)
        sw_row.addStretch(1)

        if horizontal:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)
            lay.addWidget(self.wheel)
            right_ctrl = QVBoxLayout()
            right_ctrl.setSpacing(7)
            right_ctrl.addStretch(1)
            right_ctrl.addLayout(prev_row)
            right_ctrl.addLayout(val_row)
            right_ctrl.addLayout(sw_row)
            right_ctrl.addStretch(1)
            lay.addLayout(right_ctrl, 1)
        else:
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            w_row = QHBoxLayout()
            w_row.addStretch(1)
            w_row.addWidget(self.wheel)
            w_row.addStretch(1)
            lay.addLayout(w_row)
            lay.addLayout(val_row)
            lay.addLayout(prev_row)
            lay.addLayout(sw_row)

        self._sync_ui(schedule_preview=False)

    @property
    def selected_hex(self) -> str:
        return self._cur_color.name().upper()

    def set_hex(self, hx: str, *, commit: bool = False, emit_preview: bool = False) -> None:
        c = QColor(hx)
        if not c.isValid():
            return
        self._cur_color = c
        self._h = max(0.0, c.hueF())
        self._s = max(0.0, c.saturationF())
        self._v = max(0.15, c.valueF())
        self.wheel.set_hsv(self._h, self._s, self._v)
        self.val_slider.blockSignals(True)
        self.val_slider.setValue(int(round(self._v * 100)))
        self.val_slider.blockSignals(False)
        if commit:
            self._pending_preview_hex = ""
            self._preview_timer.stop()
            self._sync_ui(schedule_preview=False)
            self.color_committed.emit(self.selected_hex)
        elif emit_preview:
            self._sync_ui(schedule_preview=True)
        else:
            # Silent sync from config must never schedule a preview that would reset appearance mode.
            self._pending_preview_hex = ""
            self._preview_timer.stop()
            self._sync_ui(schedule_preview=False)

    def _on_wheel_hsv(self, h: float, s: float) -> None:
        self._h = h
        self._s = s
        self._cur_color = QColor.fromHsvF(self._h, self._s, self._v)
        self._sync_ui(schedule_preview=True)

    def _on_val_changed(self, val: int) -> None:
        self._v = max(0.15, min(1.0, val / 100.0))
        self.wheel.set_hsv(self._h, self._s, self._v)
        self._cur_color = QColor.fromHsvF(self._h, self._s, self._v)
        self._sync_ui(schedule_preview=True)

    def _on_hex_edited(self, txt: str) -> None:
        t = txt.strip()
        if not t.startswith("#"):
            t = "#" + t
        if len(t) == 7 and QColor(t).isValid():
            c = QColor(t)
            self._cur_color = c
            self._h = max(0.0, c.hueF())
            self._s = max(0.0, c.saturationF())
            self._v = max(0.15, c.valueF())
            self.wheel.set_hsv(self._h, self._s, self._v)
            self.val_slider.blockSignals(True)
            self.val_slider.setValue(int(round(self._v * 100)))
            self.val_slider.blockSignals(False)
            self._sync_ui(schedule_preview=True, update_line_edit=False)

    def _sync_ui(self, *, schedule_preview: bool = True, update_line_edit: bool = True) -> None:
        hx = self._cur_color.name().upper()
        if update_line_edit:
            self.hex_edit.blockSignals(True)
            self.hex_edit.setText(hx)
            self.hex_edit.blockSignals(False)
        self.preview_pill.set_color(hx)
        if schedule_preview:
            self._pending_preview_hex = hx
            if not self._preview_timer.isActive():
                self._preview_timer.start()

    def _flush_preview(self) -> None:
        hx = self._pending_preview_hex
        self._pending_preview_hex = ""
        if hx:
            self.color_previewed.emit(hx)

    def _commit_current(self) -> None:
        self._pending_preview_hex = ""
        self._preview_timer.stop()
        self.color_committed.emit(self.selected_hex)


class BorhColorWheelDialog(QDialog):
    """Always-on-top frameless dark-glass color wheel popup for SettingsWindow."""

    color_selected = pyqtSignal(str)  # #RRGGBB

    def __init__(
        self,
        initial_hex: str = "#3B82F6",
        palette: ThemePalette | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(360, 430)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("ColorWheelCard")
        border_hex = palette.border if palette else "rgba(148,163,184,0.34)"
        self.card.setStyleSheet(
            f"QFrame#ColorWheelCard {{ background: rgba(14, 17, 26, 248); "
            f"border: 1px solid {border_hex}; border-radius: 20px; }}"
        )
        outer.addWidget(self.card)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Палитра цвета острова")
        title.setStyleSheet("color: #F8FAFC; font-size: 14px; font-weight: 800; background: transparent;")
        hdr.addWidget(title, 1)
        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(
            "QPushButton { min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; "
            "border-radius: 13px; padding: 0px; background: rgba(255,255,255,16); color: #E2E8F0; border: 1px solid rgba(255,255,255,26); }"
            "QPushButton:hover { background: rgba(239,68,68,55); border-color: #EF4444; }"
        )
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self.panel = BorhColorWheelPanel(initial_hex, wheel_size=204, parent=self.card)
        self.wheel = self.panel.wheel
        self.panel.color_previewed.connect(self._on_panel_color)
        self.panel.color_committed.connect(self._on_panel_color)
        lay.addWidget(self.panel)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel_b = QPushButton("Отмена")
        cancel_b.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_b.setFixedHeight(34)
        cancel_b.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,14); color: #CBD5E1; border: 1px solid rgba(255,255,255,24); "
            "border-radius: 11px; padding: 0px 14px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(255,255,255,24); color: #FFFFFF; }"
        )
        cancel_b.clicked.connect(self.reject)

        self.apply_b = QPushButton("Применить цвет")
        self.apply_b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_b.setFixedHeight(34)
        self.apply_b.clicked.connect(self.accept)
        btn_row.addWidget(cancel_b)
        btn_row.addWidget(self.apply_b, 1)
        lay.addLayout(btn_row)

        self._style_apply_btn(self.panel.selected_hex)
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if scr is not None:
            sg = scr.geometry()
            self.move(sg.x() + (sg.width() - self.width()) // 2, sg.y() + (sg.height() - self.height()) // 2)

    @property
    def selected_hex(self) -> str:
        return self.panel.selected_hex

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        raise_hwnd_topmost(int(self.winId()))

    def _style_apply_btn(self, hx: str) -> None:
        fg = contrast_fg(hx)
        self.apply_b.setStyleSheet(
            f"QPushButton {{ background: {hx}; color: {fg}; border: none; border-radius: 11px; "
            "padding: 0px 16px; font-size: 12px; font-weight: 800; }}"
        )

    def _on_panel_color(self, hx: str) -> None:
        self._style_apply_btn(hx)
        self.color_selected.emit(hx)
