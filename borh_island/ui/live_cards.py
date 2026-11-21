"""Non-activating floating Live Cards shown below the island (OSD, Claude Allow/Deny, Screenshot, Downloads, Alarms, VPN)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PyQt6.QtCore import QMimeData, QPoint, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.win32_api import set_exclude_from_capture, set_overlay_window_flags
from .card_rain import CardRainSystem
from .icons import get_icon, get_pixmap
from .theme import ThemePalette

_EMOJI_TO_ICON = {
    "⚡": "bolt",
    "🎙": "dictate",
    "🔴": "record",
    "📷": "screenshot",
    "⏰": "timer",
    "🔔": "bell",
    "⚠": "warning",
    "⚙": "settings",
    "🔊": "volume",
    "🔇": "mute",
    "🎵": "music",
    "🛡": "shield",
    "🔍": "ocr",
    "🎨": "color",
    "🔳": "qr",
    "🎬": "record",
    "📥": "download",
    "🤖": "chat",
}


@dataclass
class LiveCardAction:
    label: str
    callback: Callable[[], None]
    primary: bool = False
    danger: bool = False


@dataclass
class LiveCardSpec:
    card_id: str
    title: str
    body: str = ""
    icon: str = "bolt"
    progress: int = -1  # 0..100 for OSD/dictation/download bars, -1 to hide
    timeout_ms: int = 4500  # 0 means persistent until clicked
    actions: list[LiveCardAction] = field(default_factory=list)
    thumbnail: QImage | QPixmap | None = None
    drag_file_path: str = ""


class _DraggableThumbLabel(QLabel):
    """Thumbnail widget supporting drag-and-drop into chats, Explorer, or editors."""

    def __init__(self, pix: QPixmap, qimg: QImage | None, file_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_pix = pix
        self._drag_img = qimg
        self._file_path = file_path
        self._drag_start = QPoint()
        self.setPixmap(pix)
        self.setFixedSize(pix.size())
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Перетащите скриншот в любое окно или чат")
        self.setStyleSheet("border: 1px solid rgba(255,255,255,0.22); border-radius: 8px; background: rgba(0,0,0,0.35);")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._drag_start).manhattanLength() < 8:
            return
        mime = QMimeData()
        if self._file_path:
            mime.setUrls([QUrl.fromLocalFile(self._file_path)])
        if self._drag_img is not None and not self._drag_img.isNull():
            mime.setImageData(self._drag_img)
        elif not self._drag_pix.isNull():
            mime.setImageData(self._drag_pix.toImage())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._drag_pix)
        drag.exec(Qt.DropAction.CopyAction)


class _SingleCardWidget(QWidget):
    closed_requested = pyqtSignal(str)

    def __init__(
        self,
        spec: LiveCardSpec,
        palette: ThemePalette,
        parent: QWidget | None = None,
        glass_mode: str = "liquid",
    ) -> None:
        super().__init__(parent)
        self.spec = spec
        self._palette = palette
        self._glass_mode = str(glass_mode or "liquid").lower()
        self.setObjectName("LiveCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(360)
        self._rain_sys: CardRainSystem | None = None
        self._rain_under_np = None
        self._rain_capturing = False
        self._rain_overlay: _LiveCardRainOverlay | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        icon_name = _EMOJI_TO_ICON.get(spec.icon, spec.icon if (spec.icon.replace("_", "").isalnum() and spec.icon.isascii()) else "bolt")
        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_pixmap(icon_name, 16, palette.text_primary, palette.accent))
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        top.addWidget(icon_lbl)

        title_lbl = QLabel(spec.title)
        title_lbl.setStyleSheet(
            f"color: {palette.text_primary}; font-size: 13px; font-weight: 600; background: transparent; border: none;"
        )
        title_lbl.setWordWrap(True)
        top.addWidget(title_lbl, 1)

        close_btn = QPushButton("")
        close_btn.setIcon(get_icon("close", 11, palette.text_secondary, palette.accent))
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.10); color: #E2E8F0; border: 1px solid rgba(255,255,255,0.16); border-radius: 11px; padding: 0px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.22); }"
        )
        close_btn.clicked.connect(lambda: self.closed_requested.emit(spec.card_id))
        top.addWidget(close_btn)
        root.addLayout(top)

        if spec.body or spec.thumbnail is not None:
            mid_row = QHBoxLayout()
            mid_row.setSpacing(8)
            if spec.thumbnail is not None:
                raw_qimg = spec.thumbnail.toImage() if isinstance(spec.thumbnail, QPixmap) else spec.thumbnail
                src_pix = spec.thumbnail if isinstance(spec.thumbnail, QPixmap) else QPixmap.fromImage(spec.thumbnail)
                if not src_pix.isNull():
                    scaled = src_pix.scaled(96, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    thumb_lbl = _DraggableThumbLabel(scaled, raw_qimg, spec.drag_file_path, self)
                    mid_row.addWidget(thumb_lbl)
            if spec.body:
                body_lbl = QLabel(spec.body)
                body_lbl.setWordWrap(True)
                body_lbl.setStyleSheet(
                    f"color: {palette.text_secondary}; font-size: 11px; background: transparent; border: none;"
                )
                mid_row.addWidget(body_lbl, 1)
            root.addLayout(mid_row)

        if spec.progress >= 0:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(max(0, min(100, spec.progress)))
            bar.setTextVisible(False)
            bar.setFixedHeight(7)
            bar.setStyleSheet(
                f"QProgressBar {{ background: {palette.bg_surface}; border-radius: 3px; border: none; }}"
                f"QProgressBar::chunk {{ background: {palette.accent}; border-radius: 3px; }}"
            )
            root.addWidget(bar)

        if spec.actions:
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)
            for act in spec.actions:
                btn = QPushButton(act.label)
                btn.setObjectName("LiveCardAction")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFixedHeight(28)
                bg = palette.danger if act.danger else (palette.accent if act.primary else palette.bg_surface)
                fg = "#FFFFFF" if (act.danger or act.primary) else palette.text_primary
                btn.setStyleSheet(
                    f"QPushButton#LiveCardAction {{ background-color: {bg}; color: {fg}; border: 1px solid {palette.border}; "
                    f"border-radius: 14px; padding: 0px 14px; font-weight: 600; font-size: 11px; }}"
                    f"QPushButton#LiveCardAction:hover {{ background-color: {palette.accent}; color: #FFFFFF; }}"
                )

                def _make_handler(fn: Callable[[], None], cid: str) -> Callable[[], None]:
                    def _run() -> None:
                        try:
                            fn()
                        finally:
                            self.closed_requested.emit(cid)
                    return _run

                btn.clicked.connect(_make_handler(act.callback, spec.card_id))
                btn_row.addWidget(btn)
            btn_row.addStretch(1)
            root.addLayout(btn_row)

        self.setStyleSheet("QWidget#LiveCard { background: transparent; border: none; }")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = QPainterPath()
        path.addRoundedRect(r, 20.0, 20.0)
        gm = self._glass_mode
        if gm == "liquid":
            grad = QLinearGradient(r.topLeft(), r.bottomRight())
            grad.setColorAt(0.0, QColor(34, 46, 74, 210))
            grad.setColorAt(0.5, QColor(24, 34, 58, 222))
            grad.setColorAt(1.0, QColor(14, 22, 42, 232))
            p.setPen(QPen(QColor(255, 255, 255, 95), 1.3))
            p.setBrush(grad)
            p.drawPath(path)
            p.save()
            p.setClipPath(path)
            shine = QLinearGradient(r.left(), r.top(), r.left(), r.top() + r.height() * 0.5)
            shine.setColorAt(0.0, QColor(255, 255, 255, 42))
            shine.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(r, shine)
            p.restore()
        elif gm == "frost":
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0.0, QColor(68, 78, 96, 208))
            grad.setColorAt(1.0, QColor(36, 44, 58, 224))
            p.setPen(QPen(QColor(255, 255, 255, 115), 1.3))
            p.setBrush(grad)
            p.drawPath(path)
        else:
            bg = QColor("#0B0D14" if gm == "off" else self._palette.bg_island)
            bg.setAlpha(242)
            p.setPen(QPen(QColor(self._palette.border), 1.2))
            p.setBrush(bg)
            p.drawPath(path)
        p.end()

    def ensure_rain_overlay(self) -> "_LiveCardRainOverlay":
        if self._rain_overlay is None:
            self._rain_overlay = _LiveCardRainOverlay(self)
            self._rain_overlay.setGeometry(self.rect())
        return self._rain_overlay

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._rain_overlay is not None:
            self._rain_overlay.setGeometry(self.rect())
            self._rain_overlay.mark_dirty()


class _LiveCardRainOverlay(QWidget):
    """Click-through 3D crystal water droplet layer on floating LiveCard popups during rain."""

    def __init__(self, card: _SingleCardWidget) -> None:
        super().__init__(card)
        self._card = card
        self._sys = CardRainSystem()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    def mark_dirty(self) -> None:
        return None

    def step_rain(self, active: bool, *, stormy: bool = False) -> bool:
        self._sys.set_params(active=active, intensity=(1.2 if stormy else 1.0), stormy=stormy)
        r = QRectF(self._card.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        alive = self._sys.step([(id(self._card), r, 20.0, None)])
        if alive:
            if not self.isVisible():
                self.setGeometry(self._card.rect())
                self.show()
                self.raise_()
            self.update()
        else:
            if self.isVisible():
                self.hide()
        return alive

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if not self._sys.is_active:
            return
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        r = QRectF(self._card.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        p = QPainter(self)
        self._sys.paint(p, self.width(), self.height(), dpr, [(id(self._card), r, 20.0, None)])
        p.end()


class LiveCardManager(QWidget):
    """Floating non-activating container positioned cleanly beneath the island pill."""

    def __init__(self, palette: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette = palette
        self._glass_mode = "liquid"
        self._cards: dict[str, _SingleCardWidget] = {}
        self._timers: dict[str, QTimer] = {}
        self._anchor_center_x = 600
        self._anchor_bottom_y = 60
        self._exclude_capture = False
        self._island = None
        self._active_island_card_id = ""
        self._rain_active = False
        self._rain_stormy = False
        self._rain_timer = QTimer(self)
        self._rain_timer.setInterval(38)
        self._rain_timer.timeout.connect(self._tick_rain)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(6)
        self.hide()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        set_overlay_window_flags(int(self.winId()), click_through=False)
        set_exclude_from_capture(int(self.winId()), self._exclude_capture)

    def set_palette(self, palette: ThemePalette) -> None:
        self.palette = palette

    def bind_island(self, island) -> None:
        """Route notifications directly onto the Dynamic Island pill."""
        self._island = island

    def set_glass_mode(self, glass_mode: str) -> None:
        self._glass_mode = str(glass_mode or "liquid").lower()
        for w in self._cards.values():
            w._glass_mode = self._glass_mode
            if w._rain_overlay is not None:
                w._rain_overlay.mark_dirty()
            w.update()

    def set_rain_state(self, active: bool, *, stormy: bool = False) -> None:
        self._rain_active = bool(active)
        self._rain_stormy = bool(stormy)
        if self._rain_active and self._cards and not self._rain_timer.isActive():
            self._rain_timer.start()

    def _tick_rain(self) -> None:
        if not self._cards:
            self._rain_timer.stop()
            return
        any_alive = False
        for w in list(self._cards.values()):
            if w._rain_overlay is None and not self._rain_active:
                continue
            ov = w.ensure_rain_overlay()
            if ov.step_rain(self._rain_active, stormy=self._rain_stormy):
                any_alive = True
        if not any_alive and not self._rain_active:
            self._rain_timer.stop()

    def set_exclude_capture(self, exclude: bool) -> None:
        self._exclude_capture = exclude
        if self.isVisible():
            set_exclude_from_capture(int(self.winId()), exclude)

    def update_anchor(self, center_x: int, bottom_y: int) -> None:
        self._anchor_center_x = center_x
        self._anchor_bottom_y = bottom_y
        self._reposition()

    def _route_to_island(self, spec: LiveCardSpec) -> bool:
        island = self._island
        if island is None:
            return False
        cid = spec.card_id or ""
        ico = _EMOJI_TO_ICON.get(spec.icon, spec.icon if spec.icon in _EMOJI_TO_ICON.values() else "bell")
        primary_cb = None
        for act in spec.actions:
            if act.primary or primary_cb is None:
                primary_cb = act.callback
                if act.primary:
                    break

        if cid == "osd_vol":
            badge = "Громкость"
            txt = f"{spec.progress}%" if spec.progress >= 0 else spec.title
            acc = "#38BDF8"
            ico = "mute" if spec.progress == 0 else "volume"
        elif cid == "vpn":
            badge = "VPN"
            txt = f"{spec.title} • {spec.body}" if spec.body else spec.title
            acc = "#22C55E" if "Connected" in spec.title else "#94A3B8"
        elif cid.startswith("dl_"):
            badge = "Скачано"
            txt = spec.body or spec.title
            acc = "#22C55E"
        elif cid == "shot_saved":
            badge = "Скрин"
            txt = (spec.body.splitlines()[0] if spec.body else spec.title)
            acc = "#22C55E"
        elif cid.startswith("ocr_"):
            badge = "OCR"
            txt = spec.body.replace("\n", " ")[:120] if spec.body else spec.title
            acc = "#38BDF8"
        elif cid.startswith("rec_"):
            badge = "Запись"
            txt = spec.body or spec.title
            acc = "#EF4444" if cid in ("rec_on", "rec_err") else "#22C55E"
        elif cid.startswith("shot_tr"):
            badge = "Перевод"
            txt = spec.body.replace("\n", " ")[:120] if spec.body else spec.title
            acc = "#38BDF8"
        elif cid == "color_pick":
            hx = spec.title.replace("Color Copied:", "").strip()
            badge = hx or "Цвет"
            txt = spec.body or spec.title
            acc = hx if hx.startswith("#") else "#38BDF8"
        elif cid == "qr_res":
            badge = "QR"
            txt = spec.body.replace("\n", " ")[:120] if spec.body else spec.title
            acc = "#38BDF8"
        elif cid.startswith("dict_"):
            badge = "Диктовка"
            txt = spec.body.replace("\n", " ")[:120] if spec.body else spec.title
            acc = "#EF4444" if "Error" in spec.title or "unavailable" in spec.title else "#38BDF8"
        elif cid == "mic_mute":
            badge = "Микрофон"
            txt = "Выключен" if "Muted" in spec.title else "Включён"
            acc = "#EF4444" if "Muted" in spec.title else "#22C55E"
        elif cid == "timer_alarm":
            badge = "⏰ Таймер"
            txt = spec.title.replace("⏰", "").strip()
            acc = "#F59E0B"
        elif cid.startswith("rem_"):
            badge = "Напоминание"
            txt = spec.body or spec.title
            acc = "#F59E0B"
        elif cid.startswith("agent_"):
            badge = cid.replace("agent_", "").capitalize()[:12] or "Agent"
            txt = f"{spec.title} — {spec.body}" if spec.body else spec.title
            acc = "#38BDF8"
        else:
            parts = spec.title.split(":", 1)
            if len(parts) == 2 and len(parts[0].strip()) <= 16:
                badge = parts[0].strip()
                t_rest = parts[1].strip()
                txt = f"{t_rest} — {spec.body}" if spec.body else t_rest
            else:
                badge = (spec.title[:16] if spec.body else "Островок")
                txt = (spec.body if spec.body else spec.title)
            acc = self.palette.accent

        # Smooth in-place update when the same continuous card (e.g. dictation RMS level) refreshes
        if (
            cid == "dict_rec"
            and self._active_island_card_id == "dict_rec"
            and getattr(island, "_activity_active", False)
            and spec.progress >= 0
        ):
            return True

        self._active_island_card_id = cid
        dur = spec.timeout_ms if spec.timeout_ms > 0 else (0 if cid in ("rec_on", "dict_rec") else 5500)
        island.show_activity(
            badge,
            txt,
            accent_hex=acc,
            thumb=spec.thumbnail,
            duration_ms=dur,
            action_cb=primary_cb,
            icon_name=ico,
        )
        return not cid.startswith("perm_")

    def push_card(self, spec: LiveCardSpec) -> None:
        """Display or update a notification directly on the island (or multi-button permission card)."""
        if self._route_to_island(spec):
            return
        self.dismiss_card(spec.card_id, hide_if_empty=False)
        w = _SingleCardWidget(spec, self.palette, self, glass_mode=self._glass_mode)
        w.closed_requested.connect(lambda cid: self.dismiss_card(cid, hide_if_empty=True))
        self._cards[spec.card_id] = w
        self._layout.addWidget(w)

        if spec.timeout_ms > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(spec.timeout_ms)
            timer.timeout.connect(lambda cid=spec.card_id: self.dismiss_card(cid, hide_if_empty=True))
            timer.start()
            self._timers[spec.card_id] = timer

        self._reposition()
        if self._rain_active:
            w.ensure_rain_overlay().step_rain(True, stormy=self._rain_stormy)
            if not self._rain_timer.isActive():
                self._rain_timer.start()
        self.show()

    def dismiss_card(self, card_id: str, hide_if_empty: bool = True) -> None:
        if self._island is not None and self._active_island_card_id == card_id:
            self._active_island_card_id = ""
            if getattr(self._island, "_activity_active", False) and hasattr(self._island, "_clear_activity"):
                self._island._clear_activity()
        timer = self._timers.pop(card_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        w = self._cards.pop(card_id, None)
        if w is not None:
            self._layout.removeWidget(w)
            w.deleteLater()
        if not self._cards and hide_if_empty:
            self._rain_timer.stop()
            self.hide()
        else:
            self._reposition()

    def _reposition(self) -> None:
        if not self._cards:
            return
        self.adjustSize()
        w = max(372, self.sizeHint().width())
        h = self.sizeHint().height()
        self.setFixedSize(w, h)
        self.move(self._anchor_center_x - w // 2, self._anchor_bottom_y + 8)
