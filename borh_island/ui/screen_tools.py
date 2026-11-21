"""Multi-monitor screen overlay (area capture, dual RU+EN OCR, magnifier color picker, QR scan, record) and Annotation Editor."""

from __future__ import annotations

import colorsys
import ctypes
import ctypes.wintypes as wt
import sys
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageGrab
from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.config import ConfigManager
from ..core.qr_codec import decode_qr_from_image
from ..core.storage import LocalStorage
from ..core.win32_api import enum_visible_process_windows
from .icons import get_icon
from ..services.ai_service import AIService
from .theme import ThemePalette, build_panel_qss


def _enum_physical_monitor_rects() -> list[QRect]:
    """Return physical Win32 monitor rectangles via EnumDisplayMonitors."""
    if sys.platform != "win32":
        return [s.geometry() for s in QGuiApplication.screens()]
    try:
        u = ctypes.windll.user32
        MONPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
        )
        rects: list[QRect] = []

        def _cb(_h, _d, r_ptr, _l) -> int:
            rc = r_ptr.contents
            rects.append(QRect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top))
            return 1

        u.EnumDisplayMonitors(None, None, MONPROC(_cb), 0)
        if rects:
            return rects
    except Exception:
        pass
    return [s.geometry() for s in QGuiApplication.screens()]


def _match_physical_rect_for_qscreen(scr, phys_rects: list[QRect], idx: int) -> QRect:
    """Match a Qt QScreen to its exact Win32 physical monitor rectangle."""
    sg = scr.geometry()
    dpr = max(1.0, float(scr.devicePixelRatio()))
    pw = int(round(sg.width() * dpr))
    ph = int(round(sg.height() * dpr))
    best: QRect | None = None
    best_dist = 10**12
    for pr in phys_rects:
        dist = abs(pr.x() - sg.x()) + abs(pr.y() - sg.y()) + abs(pr.width() - pw) * 2 + abs(pr.height() - ph) * 2
        if dist < best_dist:
            best_dist = dist
            best = pr
    if best is not None:
        return best
    if 0 <= idx < len(phys_rects):
        return phys_rects[idx]
    return QRect(sg.x(), sg.y(), pw, ph)


def _logical_to_physical_screen_rect(global_r: QRect) -> QRect:
    """Convert a Qt logical global rectangle to physical Win32 screen pixels."""
    screens = QGuiApplication.screens()
    if not screens:
        return global_r
    phys_rects = _enum_physical_monitor_rects()
    center = global_r.center()
    target_idx = 0
    for i, s in enumerate(screens):
        if s.geometry().contains(center):
            target_idx = i
            break
    scr = screens[target_idx]
    sg = scr.geometry()
    pr = _match_physical_rect_for_qscreen(scr, phys_rects, target_idx)
    dpr = max(1.0, float(scr.devicePixelRatio()))
    px = pr.x() + int(round((global_r.x() - sg.x()) * dpr))
    py = pr.y() + int(round((global_r.y() - sg.y()) * dpr))
    pw = max(16, int(round(global_r.width() * dpr)))
    ph = max(16, int(round(global_r.height() * dpr)))
    return QRect(px, py, pw, ph)


def _grab_active_screen_qimage() -> tuple[QImage, QRect]:
    """Capture the monitor currently containing the cursor at full physical resolution."""
    scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    screens = QGuiApplication.screens()
    if scr is None and screens:
        scr = screens[0]
    if scr is None:
        return QImage(), QRect(0, 0, 1920, 1080)
    pix = scr.grabWindow(0)
    img = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    phys_rects = _enum_physical_monitor_rects()
    idx = screens.index(scr) if scr in screens else 0
    return img, _match_physical_rect_for_qscreen(scr, phys_rects, idx)


def _grab_virtual_desktop_qimage() -> tuple[QImage, QRect]:
    """Capture entire multi-monitor virtual desktop mapped 1:1 to each QScreen's logical geometry (zero left shift)."""
    screens = QGuiApplication.screens()
    if not screens:
        return QImage(), QRect(0, 0, 1920, 1080)
    virt = screens[0].geometry()
    for s in screens[1:]:
        virt = virt.united(s.geometry())

    max_dpr = max(1.0, max(float(s.devicePixelRatio()) for s in screens))
    cw = max(1, int(round(virt.width() * max_dpr)))
    ch = max(1, int(round(virt.height() * max_dpr)))
    canvas = QImage(cw, ch, QImage.Format.Format_RGBA8888)
    canvas.fill(QColor(0, 0, 0, 255))

    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    grabbed_any = False
    for s in screens:
        try:
            spix = s.grabWindow(0)
            if spix.isNull():
                continue
            simg = spix.toImage()
            sg = s.geometry()
            dst = QRect(
                int(round((sg.x() - virt.x()) * max_dpr)),
                int(round((sg.y() - virt.y()) * max_dpr)),
                int(round(sg.width() * max_dpr)),
                int(round(sg.height() * max_dpr)),
            )
            p.drawImage(dst, simg)
            grabbed_any = True
        except Exception:
            continue
    p.end()

    if grabbed_any:
        return canvas, virt

    try:
        pil_img = ImageGrab.grab(all_screens=True).convert("RGBA")
        raw = pil_img.tobytes("raw", "RGBA")
        qimg = QImage(raw, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888).copy()
        return qimg, virt
    except Exception:
        pix = screens[0].grabWindow(0)
        return pix.toImage(), screens[0].geometry()


def _pixelate_qimage_region(
    img: QImage,
    rect: QRect,
    block: int = 14,
    *,
    painter: QPainter | None = None,
) -> None:
    """In-place smooth + mosaic pixelation blur on img inside rect (safe even when a QPainter is already active)."""
    r = rect.normalized()
    if r.width() < 20:
        r = r.adjusted(-12, 0, 12, 0)
    if r.height() < 20:
        r = r.adjusted(0, -12, 0, 12)
    r = r.intersected(img.rect())
    if r.width() < 2 or r.height() < 2:
        return
    eff_block = max(6, int(block))
    crop = img.copy(r)
    soft = crop.scaled(
        max(1, r.width() // max(2, eff_block // 2)),
        max(1, r.height() // max(2, eff_block // 2)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    small = soft.scaled(
        max(1, r.width() // eff_block),
        max(1, r.height() // eff_block),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    mosaic = small.scaled(
        r.size(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    if painter is not None and painter.isActive():
        painter.drawImage(r.topLeft(), mosaic)
    else:
        p = QPainter(img)
        p.drawImage(r.topLeft(), mosaic)
        p.end()


def _blur_freehand_path(
    img: QImage,
    pts: list[QPoint],
    start: QPoint,
    end: QPoint,
    *,
    block: int = 14,
    brush_radius: int = 16,
    painter: QPainter | None = None,
) -> None:
    """Apply either box blur (for rectangular drags) or brush-stroke blur (for swiping across text)."""
    box = QRect(start, end).normalized()
    if box.width() >= 24 and box.height() >= 24:
        _pixelate_qimage_region(img, box, block=block, painter=painter)
        return
    stroke_pts = pts if len(pts) >= 2 else [start, end]
    rad = max(12, int(brush_radius))
    bound = QRect(stroke_pts[0], stroke_pts[0])
    for pt in stroke_pts:
        bound = bound.united(QRect(pt.x() - rad, pt.y() - rad, rad * 2, rad * 2))
    _pixelate_qimage_region(img, bound, block=block, painter=painter)


class ScreenCaptureOverlay(QWidget):
    """Full virtual-screen selector overlay for area screenshot (Lightshot quick tools), OCR, color picker, QR, or record."""

    area_captured = pyqtSignal(str, object, QRect)  # (mode, QImage, global_rect)
    color_picked = pyqtSignal(str, str, str)  # (hex, rgb_str, hsl_str)
    qr_decoded = pyqtSignal(str)
    audio_mode_changed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, mode: str, parent: QWidget | None = None, *, audio_mode: str = "system") -> None:
        super().__init__(parent)
        self.mode = mode  # screenshot | ocr | color | qr | record
        self.selected_audio_mode = audio_mode if audio_mode in ("none", "system", "both") else "system"
        self._bg_img, self._virt_rect = _grab_virtual_desktop_qimage()
        self._bg_pix = QPixmap.fromImage(self._bg_img)
        self._origin = QPoint()
        self._cur = QPoint()
        self._dragging = False
        self._dpr_x = (self._bg_img.width() / max(1, self._virt_rect.width())) if not self._bg_img.isNull() else 1.0
        self._dpr_y = (self._bg_img.height() / max(1, self._virt_rect.height())) if not self._bg_img.isNull() else 1.0

        # Lightshot-style inline overlay annotation state (active when tool != "quick")
        self._shot_tool = "quick"  # quick | pen | arrow | marker | rect | blur
        self._sel_locked = QRect()
        self._draw_active = False
        self._strokes: list[tuple[str, QColor, int, QPoint, QPoint, list[QPoint]]] = []
        self._cur_free_pts: list[QPoint] = []
        self._toolbar_host: QWidget | None = None
        self._tool_btns: dict[str, QPushButton] = {}
        self._audio_btns: dict[str, QPushButton] = {}

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(self._virt_rect)
        self._build_overlay_toolbar()
        self.show()
        self.activateWindow()

    def _build_overlay_toolbar(self) -> None:
        if self.mode not in ("screenshot", "record"):
            return
        bar = QWidget(self)
        bar.setStyleSheet(
            "QWidget { background: rgba(15, 23, 42, 235); border: 1px solid rgba(148,163,184,0.35); border-radius: 14px; }"
            "QPushButton { background: rgba(255,255,255,0.08); color: #F8FAFC; border: none; border-radius: 10px; padding: 4px 9px; font-size: 11px; font-weight: 600; }"
            "QPushButton:checked { background: #3B82F6; color: #FFFFFF; }"
            "QPushButton:hover { background: rgba(255,255,255,0.20); }"
        )
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(8, 5, 8, 5)
        hl.setSpacing(5)
        if self.mode == "record":
            lbl = QLabel("Звук:")
            lbl.setStyleSheet("color: #CBD5E1; font-size: 11px; border: none; background: transparent;")
            hl.addWidget(lbl)
            for am_k, am_lbl in (("none", "🔇 Без звука"), ("system", "🔊 Звук ПК"), ("both", "🎙 ПК + Микро")):
                b = QPushButton(am_lbl)
                b.setCheckable(True)
                b.setChecked(am_k == self.selected_audio_mode)
                b.clicked.connect(lambda _=False, k=am_k: self._pick_audio_mode(k))
                hl.addWidget(b)
                self._audio_btns[am_k] = b
        else:
            for tk, tlbl in (
                ("quick", "⚡ Сразу"),
                ("pen", "✎ Карандаш"),
                ("arrow", "↗ Стрелка"),
                ("marker", "🖍 Маркер"),
                ("rect", "▭ Рамка"),
                ("blur", "▦ Блюр"),
            ):
                b = QPushButton(tlbl)
                b.setCheckable(True)
                b.setChecked(tk == "quick")
                b.clicked.connect(lambda _=False, t=tk: self._pick_shot_tool(t))
                hl.addWidget(b)
                self._tool_btns[tk] = b
            undo_b = QPushButton("↩")
            undo_b.setToolTip("Отменить последний штрих")
            undo_b.clicked.connect(self._undo_overlay_stroke)
            done_b = QPushButton("✓ Копировать")
            done_b.setStyleSheet("background: #22C55E; color: #FFFFFF; font-weight: 700; border-radius: 10px; padding: 4px 10px;")
            done_b.clicked.connect(self._finalize_locked_capture)
            hl.addWidget(undo_b)
            hl.addWidget(done_b)
        bar.adjustSize()
        scr = QGuiApplication.primaryScreen()
        s_geo = scr.geometry() if scr else self._virt_rect
        bx = (s_geo.x() - self._virt_rect.x()) + (s_geo.width() - bar.width()) // 2
        by = (s_geo.y() - self._virt_rect.y()) + 22
        bar.move(max(10, bx), max(10, by))
        bar.show()
        self._toolbar_host = bar

    def _pick_audio_mode(self, mode: str) -> None:
        self.selected_audio_mode = mode
        for k, b in self._audio_btns.items():
            b.blockSignals(True)
            b.setChecked(k == mode)
            b.blockSignals(False)
        self.audio_mode_changed.emit(mode)

    def _pick_shot_tool(self, tool: str) -> None:
        self._shot_tool = tool
        for k, b in self._tool_btns.items():
            b.blockSignals(True)
            b.setChecked(k == tool)
            b.blockSignals(False)

    def _undo_overlay_stroke(self) -> None:
        if self._strokes:
            self._strokes.pop()
            self.update()

    def _finalize_locked_capture(self) -> None:
        r = self._sel_locked.normalized()
        if r.width() < 8 or r.height() < 8:
            return
        self._emit_captured_rect(r)

    def _emit_captured_rect(self, local_r: QRect) -> None:
        # Burn any Lightshot inline strokes onto a working image before cropping
        work_img = self._bg_img.copy()
        if self._strokes and not work_img.isNull():
            p = QPainter(work_img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            for t, col, pw, a, b, pts in self._strokes:
                sa = QPoint(int(round(a.x() * self._dpr_x)), int(round(a.y() * self._dpr_y)))
                sb = QPoint(int(round(b.x() * self._dpr_x)), int(round(b.y() * self._dpr_y)))
                spts = [QPoint(int(round(q.x() * self._dpr_x)), int(round(q.y() * self._dpr_y))) for q in pts]
                if t == "blur":
                    _blur_freehand_path(work_img, spts, sa, sb, block=14, painter=p)
                else:
                    self._paint_stroke(p, t, col, int(round(pw * self._dpr_x)), sa, sb, spts)
            p.end()

        img_r = QRect(
            int(round(local_r.x() * self._dpr_x)),
            int(round(local_r.y() * self._dpr_y)),
            int(round(local_r.width() * self._dpr_x)),
            int(round(local_r.height() * self._dpr_y)),
        )
        cropped = work_img.copy(img_r)
        logical_global_r = QRect(
            self._virt_rect.x() + local_r.x(),
            self._virt_rect.y() + local_r.y(),
            local_r.width(),
            local_r.height(),
        )
        global_r = _logical_to_physical_screen_rect(logical_global_r) if self.mode == "record" else logical_global_r
        self.hide()
        if self.mode == "qr":
            rgba = cropped.convertToFormat(QImage.Format.Format_RGBA8888)
            raw = bytes(rgba.constBits().asstring(rgba.sizeInBytes()))  # type: ignore[union-attr]
            pil_crop = Image.frombytes("RGBA", (rgba.width(), rgba.height()), raw)
            decoded = decode_qr_from_image(pil_crop) or ""
            self.qr_decoded.emit(decoded)
        else:
            self.area_captured.emit(self.mode, cropped, global_r)
        self.close()

    @staticmethod
    def _paint_stroke(p: QPainter, tool: str, col: QColor, width: int, a: QPoint, b: QPoint, pts: list[QPoint]) -> None:
        if tool == "pen":
            p.setPen(QPen(col, max(2, width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for i in range(1, len(pts)):
                p.drawLine(pts[i - 1], pts[i])
        elif tool == "marker":
            mc = QColor(col.red(), col.green(), col.blue(), 110)
            p.setPen(QPen(mc, max(12, width * 4), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
        elif tool == "rect":
            p.setPen(QPen(col, max(2, width)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRect(a, b).normalized())
        elif tool == "arrow":
            p.setPen(QPen(col, max(2, width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
            dx = b.x() - a.x()
            dy = b.y() - a.y()
            dist = max(1.0, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / dist, dy / dist
            nx, ny = -uy, ux
            hl = max(10.0, width * 3.5)
            p1 = QPoint(int(b.x() - ux * hl + nx * hl * 0.5), int(b.y() - uy * hl + ny * hl * 0.5))
            p2 = QPoint(int(b.x() - ux * hl - nx * hl * 0.5), int(b.y() - uy * hl - ny * hl * 0.5))
            p.drawLine(b, p1)
            p.drawLine(b, p2)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._sel_locked.width() >= 8:
            self._finalize_locked_capture()
            return
        super().keyPressEvent(event)

    def _cur_color_at(self, pt: QPoint) -> QColor:
        ix = max(0, min(self._bg_img.width() - 1, int(round(pt.x() * self._dpr_x))))
        iy = max(0, min(self._bg_img.height() - 1, int(round(pt.y() * self._dpr_y))))
        if self._bg_img.isNull():
            return QColor("#000000")
        return self._bg_img.pixelColor(ix, iy)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            self.close()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pt = event.position().toPoint()
            if self._toolbar_host and self._toolbar_host.geometry().contains(pt):
                return
            if self.mode == "color":
                col = self._cur_color_at(pt)
                hx = col.name().upper()
                rgb = f"rgb({col.red()}, {col.green()}, {col.blue()})"
                h, l, s = colorsys.rgb_to_hls(col.redF(), col.greenF(), col.blueF())
                hsl = f"hsl({int(round(h * 360))}, {int(round(s * 100))}%, {int(round(l * 100))}%)"
                self.color_picked.emit(hx, rgb, hsl)
                self.close()
                return
            if self.mode == "screenshot" and self._shot_tool != "quick" and self._sel_locked.width() >= 8 and self._sel_locked.contains(pt):
                self._draw_active = True
                self._origin = pt
                self._cur = pt
                self._cur_free_pts = [pt]
                self.update()
                return
            self._origin = pt
            self._cur = pt
            self._dragging = True
            self._sel_locked = QRect()
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._cur = event.position().toPoint()
        if self._draw_active and self._shot_tool in ("pen", "blur"):
            self._cur_free_pts.append(self._cur)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pt = event.position().toPoint()
        if self._draw_active:
            self._draw_active = False
            col = QColor("#FACC15") if self._shot_tool == "marker" else QColor("#EF4444")
            self._strokes.append((self._shot_tool, col, 3, self._origin, pt, list(self._cur_free_pts)))
            self._cur_free_pts.clear()
            self.update()
            return
        if not self._dragging:
            return
        self._dragging = False
        self._cur = pt
        local_r = QRect(self._origin, self._cur).normalized()
        if local_r.width() < 8 or local_r.height() < 8:
            self.cancelled.emit()
            self.close()
            return
        if self.mode == "screenshot" and self._shot_tool != "quick":
            self._sel_locked = local_r
            self.update()
            return
        self._emit_captured_rect(local_r)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        if not self._bg_pix.isNull():
            p.drawPixmap(self.rect(), self._bg_pix)

        if self.mode != "color":
            p.fillRect(self.rect(), QColor(0, 0, 0, 95))
            r = QRect(self._origin, self._cur).normalized() if self._dragging else self._sel_locked
            if r.width() >= 2 and r.height() >= 2:
                if not self._bg_pix.isNull():
                    src_r = QRect(
                        int(round(r.x() * self._dpr_x)),
                        int(round(r.y() * self._dpr_y)),
                        int(round(r.width() * self._dpr_x)),
                        int(round(r.height() * self._dpr_y)),
                    )
                    p.drawPixmap(r, self._bg_pix, src_r)
                p.save()
                p.setClipRect(r)
                for t, col, pw, a, b, pts in self._strokes:
                    if t == "blur":
                        p.fillRect(QRect(a, b).normalized(), QColor(30, 41, 59, 210))
                    else:
                        self._paint_stroke(p, t, col, pw, a, b, pts)
                if self._draw_active:
                    col = QColor("#FACC15") if self._shot_tool == "marker" else QColor("#EF4444")
                    if self._shot_tool == "blur":
                        p.fillRect(QRect(self._origin, self._cur).normalized(), QColor(30, 41, 59, 170))
                    else:
                        self._paint_stroke(p, self._shot_tool, col, 3, self._origin, self._cur, self._cur_free_pts)
                p.restore()
                p.setPen(QPen(QColor("#38BDF8"), 2))
                p.drawRect(r)
                p.fillRect(r.x(), max(0, r.y() - 24), 110, 20, QColor(15, 23, 42, 210))
                p.setPen(QColor("#FFFFFF"))
                p.drawText(r.x() + 6, max(14, r.y() - 10), f"{r.width()} × {r.height()}")
            else:
                p.fillRect(20, 20, 340, 32, QColor(15, 23, 42, 220))
                p.setPen(QColor("#FFFFFF"))
                p.drawText(30, 40, f"[{self.mode.upper()}] Выделите область • ПКМ / Esc: Отмена")
        else:
            # Color picker magnifier louver around cursor
            pt = self._cur
            col = self._cur_color_at(pt)
            louver_r = 56
            lx = min(self.width() - louver_r * 2 - 20, pt.x() + 22)
            ly = min(self.height() - louver_r * 2 - 48, pt.y() + 22)
            cx = int(round(pt.x() * self._dpr_x))
            cy = int(round(pt.y() * self._dpr_y))
            src = QRect(cx - 7, cy - 7, 15, 15)
            target = QRect(lx, ly, louver_r * 2, louver_r * 2)
            clip = QPainterPath()
            clip.addEllipse(target.toRectF())
            p.save()
            p.setClipPath(clip)
            if not self._bg_pix.isNull():
                p.drawPixmap(target, self._bg_pix, src)
            p.restore()
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.drawEllipse(target)
            p.drawRect(lx + louver_r - 4, ly + louver_r - 4, 8, 8)
            p.fillRect(lx, ly + louver_r * 2 + 6, louver_r * 2, 24, QColor(15, 23, 42, 225))
            p.setPen(col)
            p.drawRect(lx + 4, ly + louver_r * 2 + 10, 14, 14)
            p.fillRect(lx + 4, ly + louver_r * 2 + 10, 14, 14, col)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(lx + 24, ly + louver_r * 2 + 22, col.name().upper())
        p.end()


class _AnnotationCanvas(QWidget):
    """Interactive drawing canvas supporting Pen, Marker, Arrow, Rect, Text, and Blur redaction."""

    changed = pyqtSignal()

    def __init__(self, image: QImage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.base_image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        self.working = self.base_image.copy()
        self._history: list[QImage] = [self.working.copy()]
        self.tool = "arrow"  # arrow | rect | pen | marker | text | blur
        self.color = QColor("#EF4444")
        self.stroke_width = 4
        self._drawing = False
        self._start = QPoint()
        self._last = QPoint()
        self._freehand: list[QPoint] = []
        self._preview_img: QImage | None = None
        self.setFixedSize(min(1100, self.working.width()), min(700, self.working.height()))

    @property
    def _scale(self) -> tuple[float, float]:
        return (self.working.width() / max(1, self.width()), self.working.height() / max(1, self.height()))

    def _to_img(self, pt: QPoint) -> QPoint:
        sx, sy = self._scale
        return QPoint(int(round(pt.x() * sx)), int(round(pt.y() * sy)))

    def undo(self) -> None:
        if len(self._history) > 1:
            self._history.pop()
            self.working = self._history[-1].copy()
            self._preview_img = None
            self.update()
            self.changed.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        ipt = self._to_img(event.position().toPoint())
        if self.tool == "text":
            txt, ok = QInputDialog.getText(self, "Add Text", "Annotation text:")
            if ok and txt.strip():
                self._commit_text(ipt, txt.strip())
            return
        self._drawing = True
        self._start = ipt
        self._last = ipt
        self._freehand = [ipt]
        self._preview_img = None
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._drawing:
            return
        ipt = self._to_img(event.position().toPoint())
        self._last = ipt
        if self.tool in ("pen", "marker", "blur"):
            self._freehand.append(ipt)
        if self.tool == "blur":
            prev = self.working.copy()
            _blur_freehand_path(prev, self._freehand, self._start, ipt, block=14, brush_radius=max(14, self.stroke_width * 4))
            self._preview_img = prev
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._drawing:
            return
        self._drawing = False
        ipt = self._to_img(event.position().toPoint())
        self._last = ipt
        self._preview_img = None
        if self.tool == "blur":
            _blur_freehand_path(self.working, self._freehand, self._start, ipt, block=14, brush_radius=max(14, self.stroke_width * 4))
        else:
            p = QPainter(self.working)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_shape(p, self.tool, self._start, ipt, self._freehand)
            p.end()
        self._history.append(self.working.copy())
        if len(self._history) > 25:
            self._history.pop(0)
        self.update()
        self.changed.emit()

    def _commit_text(self, pos: QPoint, text: str) -> None:
        p = QPainter(self.working)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = p.font()
        f.setPixelSize(max(16, self.stroke_width * 4))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#000000"))
        p.drawText(pos + QPoint(2, 2), text)
        p.setPen(self.color)
        p.drawText(pos, text)
        p.end()
        self._history.append(self.working.copy())
        self.update()
        self.changed.emit()

    def _draw_shape(self, p: QPainter, tool: str, a: QPoint, b: QPoint, pts: list[QPoint]) -> None:
        if tool == "marker":
            c = QColor(self.color)
            c.setAlpha(105)
            p.setPen(QPen(c, self.stroke_width * 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for i in range(1, len(pts)):
                p.drawLine(pts[i - 1], pts[i])
        elif tool == "pen":
            p.setPen(QPen(self.color, self.stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for i in range(1, len(pts)):
                p.drawLine(pts[i - 1], pts[i])
        elif tool == "rect":
            p.setPen(QPen(self.color, self.stroke_width))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRect(a, b).normalized())
        elif tool == "arrow":
            p.setPen(QPen(self.color, self.stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
            dx, dy = b.x() - a.x(), b.y() - a.y()
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > 4:
                ux, uy = dx / dist, dy / dist
                head = max(12.0, self.stroke_width * 3.5)
                p1 = QPoint(int(b.x() - ux * head + uy * head * 0.45), int(b.y() - uy * head - ux * head * 0.45))
                p2 = QPoint(int(b.x() - ux * head - uy * head * 0.45), int(b.y() - uy * head + ux * head * 0.45))
                p.drawLine(b, p1)
                p.drawLine(b, p2)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        img_to_draw = self._preview_img if (self._drawing and self.tool == "blur" and self._preview_img is not None) else self.working
        p.drawImage(self.rect(), img_to_draw)
        if self._drawing:
            sx, sy = self._scale
            p.save()
            p.scale(1.0 / sx, 1.0 / sy)
            if self.tool == "blur":
                p.setPen(QPen(QColor("#38BDF8"), 2, Qt.PenStyle.DashLine))
                p.drawRect(QRect(self._start, self._last).normalized())
            else:
                self._draw_shape(p, self.tool, self._start, self._last, self._freehand)
            p.restore()
        p.end()


class AnnotationEditorWindow(QWidget):
    """Screenshot markup & upload window."""

    send_to_chat_requested = pyqtSignal(str)
    upload_finished = pyqtSignal(str, bool)  # (url_or_err, is_ok)

    def __init__(
        self,
        image: QImage,
        saved_path: Path,
        config_mgr: ConfigManager,
        ai_service: AIService,
        palette: ThemePalette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.saved_path = saved_path
        self.config_mgr = config_mgr
        self.ai_service = ai_service
        self.setWindowTitle(f"Screenshot Editor — {saved_path.name}")
        self.setStyleSheet(f"background-color: {palette.bg_island};" + build_panel_qss(palette))

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        tb = QHBoxLayout()
        self.canvas = _AnnotationCanvas(image)
        self.tool_btns: dict[str, QPushButton] = {}
        for tid, lbl, ico in (
            ("arrow", "Arrow", "bolt"),
            ("rect", "Box", "screenshot"),
            ("pen", "Pen", "pen"),
            ("marker", "Marker", "pen"),
            ("text", "Text", "text"),
            ("blur", "Blur", "cloud"),
        ):
            b = QPushButton(lbl)
            b.setIcon(get_icon(ico, 13, "#F2F5FA", "#60A5FA"))
            b.setCheckable(True)
            b.setChecked(tid == "arrow")
            b.clicked.connect(lambda _, t=tid: self._select_tool(t))
            self.tool_btns[tid] = b
            tb.addWidget(b)

        for hx in ("#EF4444", "#F59E0B", "#22C55E", "#3B82F6", "#FFFFFF"):
            cb = QPushButton()
            cb.setFixedSize(22, 22)
            cb.setStyleSheet(f"background-color: {hx}; border: 1px solid #475569; border-radius: 11px;")
            cb.clicked.connect(lambda _, c=hx: setattr(self.canvas, "color", QColor(c)))
            tb.addWidget(cb)

        undo_btn = QPushButton("Undo")
        undo_btn.setIcon(get_icon("undo", 13, "#F2F5FA", "#60A5FA"))
        undo_btn.clicked.connect(self.canvas.undo)
        tb.addWidget(undo_btn)
        tb.addStretch(1)

        copy_btn = QPushButton("Copy")
        copy_btn.setIcon(get_icon("copy", 13, "#F8FAFC", "#93C5FD"))
        copy_btn.setObjectName("Primary")
        copy_btn.clicked.connect(self._copy)
        save_btn = QPushButton("Save As")
        save_btn.setIcon(get_icon("save", 13, "#F2F5FA", "#60A5FA"))
        save_btn.clicked.connect(self._save_as)
        self.up_btn = QPushButton("Upload")
        self.up_btn.setIcon(get_icon("globe", 13, "#F2F5FA", "#60A5FA"))
        self.up_btn.clicked.connect(self._upload)
        tb.addWidget(copy_btn)
        tb.addWidget(save_btn)
        tb.addWidget(self.up_btn)
        root.addLayout(tb)

        root.addWidget(self.canvas, 1, Qt.AlignmentFlag.AlignCenter)
        self.status_lbl = QLabel(f"Saved: {saved_path}")
        self.status_lbl.setObjectName("Muted")
        root.addWidget(self.status_lbl)

        self.canvas.changed.connect(self._on_canvas_changed)
        self.upload_finished.connect(self._on_upload_done)

    def _on_canvas_changed(self) -> None:
        self._save_working()
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setImage(self.canvas.working)
        self.status_lbl.setText(f"Сохранено и скопировано в буфер: {self.saved_path.name}")

    def _select_tool(self, tool: str) -> None:
        self.canvas.tool = tool
        for k, b in self.tool_btns.items():
            b.setChecked(k == tool)

    def _save_working(self) -> Path:
        self.canvas.working.save(str(self.saved_path), "PNG")
        return self.saved_path

    def _copy(self) -> None:
        self._save_working()
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setImage(self.canvas.working)
        self.status_lbl.setText("Copied to clipboard & saved!")

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Screenshot", str(self.saved_path), "PNG (*.png)")
        if path:
            self.canvas.working.save(path, "PNG")
            self.status_lbl.setText(f"Saved to {path}")

    def _upload(self) -> None:
        p = self._save_working()
        cfg = self.config_mgr.config
        svc = cfg.upload_service if cfg.upload_service != "none" else "imgbb"
        key = cfg.get_secret(svc)
        if not key:
            self.status_lbl.setText(f"Configure {svc} API key in Settings first.")
            return
        self.up_btn.setEnabled(False)
        self.status_lbl.setText(f"Uploading to {svc}...")
        threading.Thread(
            target=self._do_upload,
            args=(p, svc, key),
            daemon=True,
        ).start()

    def _do_upload(self, p: Path, svc: str, key: str) -> None:
        try:
            url = self.ai_service.upload_screenshot(p, svc, key)
            self.upload_finished.emit(url, True)
        except Exception as exc:
            self.upload_finished.emit(str(exc), False)

    def _on_upload_done(self, msg: str, ok: bool) -> None:
        self.up_btn.setEnabled(True)
        if ok:
            cb = QGuiApplication.clipboard()
            if cb:
                cb.setText(msg)
            self.status_lbl.setText(f"Uploaded (copied): {msg}")
        else:
            self.status_lbl.setText(f"Upload failed: {msg}")


def _get_physical_hwnd_rect(hwnd: int) -> QRect | None:
    """Return physical Win32 window bounds for hwnd, or None if invalid/cloaked/minimized."""
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        u = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
        if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return None
        cloaked = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return None
        rc = wt.RECT()
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rc), ctypes.sizeof(rc)) != 0:
            if not u.GetWindowRect(hwnd, ctypes.byref(rc)):
                return None
        w = rc.right - rc.left
        h = rc.bottom - rc.top
        if w < 160 or h < 100:
            return None
        return QRect(rc.left, rc.top, w, h)
    except Exception:
        return None


def _grab_hwnd_preview_pixmap(hwnd: int, phys_rect: QRect) -> QPixmap:
    """Grab a 240x135 16:9 preview thumbnail of a window via Qt screen grab or placeholder."""
    thumb_w, thumb_h = 240, 135
    canvas = QPixmap(thumb_w, thumb_h)
    canvas.fill(QColor("#0F172A"))
    try:
        screens = QGuiApplication.screens()
        if screens:
            phys_rects = _enum_physical_monitor_rects()
            best_idx = 0
            best_area = -1
            for i, s in enumerate(screens):
                pr = _match_physical_rect_for_qscreen(s, phys_rects, i)
                inter = pr.intersected(phys_rect)
                area = max(0, inter.width()) * max(0, inter.height())
                if area > best_area:
                    best_area = area
                    best_idx = i
            scr = screens[best_idx]
            pr = _match_physical_rect_for_qscreen(scr, phys_rects, best_idx)
            dpr = max(1.0, float(scr.devicePixelRatio()))
            lx = int(round((phys_rect.x() - pr.x()) / dpr))
            ly = int(round((phys_rect.y() - pr.y()) / dpr))
            lw = max(32, int(round(phys_rect.width() / dpr)))
            lh = max(32, int(round(phys_rect.height() / dpr)))
            wpix = scr.grabWindow(0, lx, ly, lw, lh)
            if not wpix.isNull():
                scaled = wpix.scaled(
                    thumb_w,
                    thumb_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                p = QPainter(canvas)
                p.drawPixmap((thumb_w - scaled.width()) // 2, (thumb_h - scaled.height()) // 2, scaled)
                p.end()
    except Exception:
        pass
    return canvas


class _ShareTargetCard(QFrame):
    """Discord-style selectable screen/application preview tile."""

    clicked = pyqtSignal(object)
    double_clicked = pyqtSignal(object)

    def __init__(
        self,
        *,
        kind: str,
        title: str,
        subtitle: str,
        thumb: QPixmap,
        phys_rect: QRect,
        hwnd: int = 0,
        accent_hex: str = "#5865F2",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind  # "screen" | "app"
        self.title = title
        self.subtitle = subtitle
        self.phys_rect = phys_rect
        self.hwnd = hwnd
        self._accent = accent_hex
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(236, 186)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        self.thumb_lbl = QLabel()
        self.thumb_lbl.setFixedSize(220, 124)
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_lbl.setStyleSheet("background: #090D16; border-radius: 9px; border: 1px solid rgba(255,255,255,0.10);")
        if not thumb.isNull():
            self.thumb_lbl.setPixmap(
                thumb.scaled(220, 124, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        lay.addWidget(self.thumb_lbl)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet("color: #F8FAFC; font-size: 11px; font-weight: 700; background: transparent; border: none;")
        fm = self.title_lbl.fontMetrics()
        self.title_lbl.setText(fm.elidedText(title, Qt.TextElideMode.ElideRight, 214))
        self.title_lbl.setToolTip(title)
        lay.addWidget(self.title_lbl)

        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setStyleSheet("color: #94A3B8; font-size: 10px; font-weight: 600; background: transparent; border: none;")
        lay.addWidget(self.sub_lbl)
        self._update_style()

    def set_selected(self, sel: bool) -> None:
        self._selected = bool(sel)
        self._update_style()

    def _update_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                f"QFrame {{ background: rgba(88, 101, 242, 46); border: 2px solid {self._accent}; border-radius: 13px; }}"
            )
        else:
            self.setStyleSheet(
                "QFrame { background: rgba(255, 255, 255, 12); border: 1px solid rgba(255, 255, 255, 22); border-radius: 13px; }"
                f"QFrame:hover {{ background: rgba(255, 255, 255, 22); border: 1px solid {self._accent}; }}"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)
            self.double_clicked.emit(self)
        super().mouseDoubleClickEvent(event)


class DiscordRecordPickerDialog(QWidget):
    """Discord-style Screen Share & Recording Picker supporting any Monitor (1, 2, 3...) or open Application Window."""

    target_selected = pyqtSignal(QRect, int, str, str)  # (phys_rect, target_hwnd, label, audio_mode)
    custom_area_requested = pyqtSignal(str)  # (audio_mode)
    audio_mode_changed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        *,
        audio_mode: str = "system",
        palette: ThemePalette | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.selected_audio_mode = audio_mode if audio_mode in ("none", "system", "both") else "system"
        self._accent = palette.accent if palette is not None else "#5865F2"
        self._tab = "screens"  # "screens" | "apps"
        self._cards: list[_ShareTargetCard] = []
        self._selected_card: _ShareTargetCard | None = None
        self._audio_btns: dict[str, QPushButton] = {}

        self.setWindowTitle("Запись экрана — Выбор экрана или приложения")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedSize(780, 540)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("DiscordPickerRoot")
        self.card.setStyleSheet(
            "QFrame#DiscordPickerRoot { background: rgba(15, 18, 28, 248); "
            "border: 1px solid rgba(148, 163, 184, 0.32); border-radius: 18px; }"
        )
        outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # Header row
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.hdr_lbl = QLabel("Запись экрана и приложений")
        self.hdr_lbl.setStyleSheet("color: #F8FAFC; font-size: 15px; font-weight: 800; background: transparent;")
        self.sub_hdr_lbl = QLabel("Выберите любой монитор (Экран 1, 2, 3…) или конкретное приложение, как в Discord")
        self.sub_hdr_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent;")
        title_box.addWidget(self.hdr_lbl)
        title_box.addWidget(self.sub_hdr_lbl)
        hdr.addLayout(title_box, 1)

        self.area_btn = QPushButton("✂ Выбрать область")
        self.area_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.area_btn.setFixedHeight(30)
        self.area_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,16); color: #E2E8F0; border: 1px solid rgba(255,255,255,28); "
            "border-radius: 10px; padding: 0px 12px; font-size: 11px; font-weight: 600; }"
            f"QPushButton:hover {{ background: rgba(255,255,255,28); border-color: {self._accent}; }}"
        )
        self.area_btn.clicked.connect(self._on_custom_area)
        hdr.addWidget(self.area_btn)

        self.refresh_btn = QPushButton("")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setFixedSize(30, 30)
        self.refresh_btn.setIcon(get_icon("refresh", size=14, color="#F8FAFC", accent=self._accent))
        self.refresh_btn.setIconSize(QSize(14, 14))
        self.refresh_btn.setToolTip("Обновить список экранов и приложений")
        self.refresh_btn.setStyleSheet(
            "QPushButton { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; "
            "border-radius: 15px; padding: 0px; background: rgba(255,255,255,16); border: 1px solid rgba(255,255,255,28); }"
            f"QPushButton:hover {{ background: rgba(255,255,255,28); border-color: {self._accent}; }}"
        )
        self.refresh_btn.clicked.connect(self.refresh_targets)
        hdr.addWidget(self.refresh_btn)

        self.close_btn = QPushButton("")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setIcon(get_icon("close", size=13, color="#F8FAFC", accent="#EF4444"))
        self.close_btn.setIconSize(QSize(13, 13))
        self.close_btn.setStyleSheet(
            "QPushButton { min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; "
            "border-radius: 15px; padding: 0px; background: rgba(255,255,255,16); border: 1px solid rgba(255,255,255,28); }"
            "QPushButton:hover { background: rgba(239,68,68,55); border-color: #EF4444; }"
        )
        self.close_btn.clicked.connect(self._on_cancel)
        hdr.addWidget(self.close_btn)
        root.addLayout(hdr)

        # Discord-style segmented tab bar: [Экраны] [Приложения]
        tab_row = QHBoxLayout()
        tab_row.setSpacing(8)
        self.screens_tab_btn = QPushButton("🖥 Экраны")
        self.screens_tab_btn.setCheckable(True)
        self.screens_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.screens_tab_btn.setFixedHeight(34)
        self.screens_tab_btn.clicked.connect(lambda: self._switch_tab("screens"))

        self.apps_tab_btn = QPushButton("🪟 Приложения")
        self.apps_tab_btn.setCheckable(True)
        self.apps_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apps_tab_btn.setFixedHeight(34)
        self.apps_tab_btn.clicked.connect(lambda: self._switch_tab("apps"))

        tab_qss = (
            "QPushButton { background: rgba(255,255,255,12); color: #CBD5E1; border: 1px solid rgba(255,255,255,22); "
            "border-radius: 11px; padding: 0px 18px; font-size: 12px; font-weight: 700; }"
            "QPushButton:hover { background: rgba(255,255,255,22); color: #FFFFFF; }"
            f"QPushButton:checked {{ background: {self._accent}; color: #FFFFFF; border-color: {self._accent}; }}"
        )
        self.screens_tab_btn.setStyleSheet(tab_qss)
        self.apps_tab_btn.setStyleSheet(tab_qss)
        tab_row.addWidget(self.screens_tab_btn)
        tab_row.addWidget(self.apps_tab_btn)
        tab_row.addStretch(1)
        root.addLayout(tab_row)

        # Scrollable 3-column grid of cards
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background: rgba(8, 11, 18, 145); border-radius: 13px;")
        self.grid_host = QWidget()
        self.grid_host.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(10, 10, 10, 10)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        root.addWidget(self.scroll, 1)

        # Bottom audio + start bar
        bot = QHBoxLayout()
        bot.setSpacing(6)
        aud_lbl = QLabel("Звук:")
        aud_lbl.setStyleSheet("color: #CBD5E1; font-size: 11px; font-weight: 700; background: transparent;")
        bot.addWidget(aud_lbl)
        aud_qss = (
            "QPushButton { background: rgba(255,255,255,12); color: #CBD5E1; border: 1px solid rgba(255,255,255,22); "
            "border-radius: 9px; padding: 0px 10px; font-size: 11px; font-weight: 600; min-height: 28px; }"
            "QPushButton:hover { background: rgba(255,255,255,22); color: #FFFFFF; }"
            f"QPushButton:checked {{ background: {self._accent}; color: #FFFFFF; border-color: {self._accent}; }}"
        )
        for am_k, am_lbl in (("none", "🔇 Без звука"), ("system", "🔊 Звук ПК"), ("both", "🎙 ПК + Микро")):
            ab = QPushButton(am_lbl)
            ab.setCheckable(True)
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setChecked(am_k == self.selected_audio_mode)
            ab.setStyleSheet(aud_qss)
            ab.clicked.connect(lambda _=False, k=am_k: self._pick_audio_mode(k))
            bot.addWidget(ab)
            self._audio_btns[am_k] = ab

        bot.addStretch(1)
        self.sel_info_lbl = QLabel("")
        self.sel_info_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 600; background: transparent;")
        bot.addWidget(self.sel_info_lbl)

        self.start_btn = QPushButton("🔴 Начать запись")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setFixedHeight(34)
        self.start_btn.setStyleSheet(
            "QPushButton { background: #EF4444; color: #FFFFFF; border: none; border-radius: 11px; "
            "padding: 0px 18px; font-size: 12px; font-weight: 800; }"
            "QPushButton:hover { background: #DC2626; }"
            "QPushButton:disabled { background: rgba(239, 68, 68, 90); color: rgba(255,255,255,130); }"
        )
        self.start_btn.clicked.connect(self._on_start_clicked)
        bot.addWidget(self.start_btn)
        root.addLayout(bot)

        self._screen_items: list[dict] = []
        self._app_items: list[dict] = []
        self.refresh_targets()
        self._center_on_active_screen()

    def _center_on_active_screen(self) -> None:
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if scr is not None:
            sg = scr.geometry()
            self.move(sg.x() + (sg.width() - self.width()) // 2, sg.y() + (sg.height() - self.height()) // 2)

    def _pick_audio_mode(self, mode: str) -> None:
        self.selected_audio_mode = mode
        for k, b in self._audio_btns.items():
            b.blockSignals(True)
            b.setChecked(k == mode)
            b.blockSignals(False)
        self.audio_mode_changed.emit(mode)

    def refresh_targets(self) -> None:
        """Scan all connected monitors (1, 2, 3...) and all visible open application windows."""
        self._screen_items.clear()
        self._app_items.clear()

        screens = QGuiApplication.screens()
        phys_rects = _enum_physical_monitor_rects()
        primary = QGuiApplication.primaryScreen()
        for idx, scr in enumerate(screens):
            pr = _match_physical_rect_for_qscreen(scr, phys_rects, idx)
            pix = scr.grabWindow(0)
            thumb = (
                pix.scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                if not pix.isNull()
                else QPixmap(240, 135)
            )
            is_prim = (scr == primary)
            title = f"Экран {idx + 1}" + (" (Основной)" if is_prim else "")
            sub = f"{pr.width()} × {pr.height()} • {scr.name()}"
            self._screen_items.append(
                {
                    "kind": "screen",
                    "title": title,
                    "subtitle": sub,
                    "thumb": thumb,
                    "phys_rect": pr,
                    "hwnd": 0,
                }
            )

        skip_titles = {
            "program manager",
            "windows input experience",
            "dynamic island",
            "запись экрана — выбор экрана или приложения",
            "microsoft text input application",
        }
        skip_exes = {"textinputhost.exe", "shellexperiencehost.exe", "searchhost.exe", "lockapp.exe"}
        seen_hwnds: set[int] = set()
        my_hwnd = int(self.winId()) if self.isVisible() else 0
        for hwnd, exe_low, wtitle in enum_visible_process_windows():
            if hwnd in seen_hwnds or hwnd == my_hwnd:
                continue
            if exe_low in skip_exes or wtitle.strip().lower() in skip_titles:
                continue
            pr = _get_physical_hwnd_rect(hwnd)
            if pr is None:
                continue
            seen_hwnds.add(hwnd)
            app_pretty = exe_low.removesuffix(".exe").capitalize()
            thumb = _grab_hwnd_preview_pixmap(hwnd, pr)
            self._app_items.append(
                {
                    "kind": "app",
                    "title": wtitle,
                    "subtitle": f"{app_pretty} • {pr.width()}×{pr.height()}",
                    "thumb": thumb,
                    "phys_rect": pr,
                    "hwnd": hwnd,
                }
            )

        self.screens_tab_btn.setText(f"🖥 Экраны ({len(self._screen_items)})")
        self.apps_tab_btn.setText(f"🪟 Приложения ({len(self._app_items)})")
        self._switch_tab(self._tab)

    def _switch_tab(self, tab: str) -> None:
        self._tab = "apps" if tab == "apps" else "screens"
        self.screens_tab_btn.blockSignals(True)
        self.screens_tab_btn.setChecked(self._tab == "screens")
        self.screens_tab_btn.blockSignals(False)
        self.apps_tab_btn.blockSignals(True)
        self.apps_tab_btn.setChecked(self._tab == "apps")
        self.apps_tab_btn.blockSignals(False)

        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._cards.clear()
        self._selected_card = None

        items = self._screen_items if self._tab == "screens" else self._app_items
        for idx, item in enumerate(items):
            card = _ShareTargetCard(
                kind=item["kind"],
                title=item["title"],
                subtitle=item["subtitle"],
                thumb=item["thumb"],
                phys_rect=item["phys_rect"],
                hwnd=item["hwnd"],
                accent_hex=self._accent,
                parent=self.grid_host,
            )
            card.clicked.connect(self._on_card_clicked)
            card.double_clicked.connect(self._on_card_double_clicked)
            self.grid.addWidget(card, idx // 3, idx % 3)
            self._cards.append(card)

        if self._cards:
            self._on_card_clicked(self._cards[0])
        else:
            self.sel_info_lbl.setText("Нет доступных окон")
            self.start_btn.setEnabled(False)

    def _on_card_clicked(self, card: _ShareTargetCard) -> None:
        self._selected_card = card
        for c in self._cards:
            c.set_selected(c is card)
        self.sel_info_lbl.setText(f"Выбрано: {card.title[:28]}")
        self.start_btn.setEnabled(True)

    def _on_card_double_clicked(self, card: _ShareTargetCard) -> None:
        self._on_card_clicked(card)
        self._on_start_clicked()

    def _on_start_clicked(self) -> None:
        card = self._selected_card
        if card is None:
            return
        pr = card.phys_rect
        hwnd = int(card.hwnd or 0)
        lbl = card.title
        amode = self.selected_audio_mode
        self.hide()
        self.target_selected.emit(pr, hwnd, lbl, amode)
        self.close()

    def _on_custom_area(self) -> None:
        amode = self.selected_audio_mode
        self.hide()
        self.custom_area_requested.emit(amode)
        self.close()

    def _on_cancel(self) -> None:
        self.cancelled.emit()
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._selected_card is not None:
            self._on_start_clicked()
            return
        super().keyPressEvent(event)
