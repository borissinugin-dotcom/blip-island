"""Expanded multi-tab container hosted inside Dynamic Island with swipeable capsule navigation."""

from __future__ import annotations

import time

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QKeySequence, QLinearGradient, QMouseEvent, QPainter, QPixmap, QRegion, QShortcut, QWheelEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import ConfigManager
from ..core.i18n import tr
from ..core.licensing import get_license_manager
from ..core.native_bridge import NativeBridge
from ..core.storage import LocalStorage
from ..services.ai_service import AIService
from ..services.media_service import MediaService
from ..services.weather_service import WeatherService
from .card_rain import CardRainOverlay
from .icons import get_icon
from .pro_dialog import BlipProWidget
from .tabs.character_tab import CharacterSettingsTab
from .tabs.chat_tab import ChatTab
from .tabs.controls_tab import ControlsTab
from .tabs.home_tab import HomeTab
from .tabs.notes_tab import NotesTab
from .tabs.weather_tab import WeatherTab
from .theme import ThemePalette, build_panel_qss, contrast_fg


class _EdgeFadeStrip(QWidget):
    """Lightweight scroll-edge feathering strip sampling cached backdrop with zero child tree re-render."""

    def __init__(self, scroll_area: QScrollArea, *, is_bottom: bool) -> None:
        super().__init__(scroll_area)
        self._sa = scroll_area
        self._is_bottom = is_bottom
        self._fade_px = 0
        self._buf: QImage | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def set_fade_px(self, fade_px: int, vp_rect: QRect) -> None:
        if fade_px <= 0 or vp_rect.width() < 4 or vp_rect.height() < 8:
            self._fade_px = 0
            if self.isVisible():
                self.hide()
            return
        h = min(fade_px, max(4, vp_rect.height() // 2))
        y = (vp_rect.bottom() + 1 - h) if self._is_bottom else vp_rect.y()
        target = QRect(vp_rect.x(), y, vp_rect.width(), h)
        changed = (h != self._fade_px) or (self.geometry() != target)
        self._fade_px = h
        if self.geometry() != target:
            self.setGeometry(target)
        if not self.isVisible():
            self.show()
        self.raise_()
        if changed:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        h = self._fade_px
        w = self.width()
        if h <= 0 or w <= 0 or self.height() <= 0:
            return
        win = self.window()
        bg_img = getattr(win, "_exp_backdrop_img", None)
        p = QPainter(self)
        if bg_img is not None and not bg_img.isNull():
            dpr = max(1.0, float(bg_img.devicePixelRatio()))
            pw = max(1, int(round(w * dpr)))
            ph = max(1, int(round(self.height() * dpr)))
            if (
                self._buf is None
                or self._buf.width() != pw
                or self._buf.height() != ph
                or abs(self._buf.devicePixelRatio() - dpr) > 0.01
            ):
                self._buf = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
                self._buf.setDevicePixelRatio(dpr)
            tl = self.mapTo(win, QPoint(0, 0))
            bg_lw = bg_img.width() / dpr
            bg_lh = bg_img.height() / dpr
            sx = max(0.0, min(bg_lw - 1.0, float(tl.x())))
            sy = max(0.0, min(bg_lh - 1.0, float(tl.y())))
            sw = max(1.0, min(float(w), bg_lw - sx))
            sh = max(1.0, min(float(self.height()), bg_lh - sy))
            self._buf.fill(0)
            bp = QPainter(self._buf)
            src_r = QRectF(sx * dpr, sy * dpr, sw * dpr, sh * dpr)
            bp.drawImage(QRectF(0.0, 0.0, sw, sh), bg_img, src_r)
            grad = QLinearGradient(0.0, 0.0, 0.0, float(self.height()))
            if self._is_bottom:
                grad.setColorAt(0.0, QColor(0, 0, 0, 0))
                grad.setColorAt(0.38, QColor(0, 0, 0, 125))
                grad.setColorAt(0.75, QColor(0, 0, 0, 225))
                grad.setColorAt(1.0, QColor(0, 0, 0, 255))
            else:
                grad.setColorAt(0.0, QColor(0, 0, 0, 255))
                grad.setColorAt(0.25, QColor(0, 0, 0, 225))
                grad.setColorAt(0.62, QColor(0, 0, 0, 125))
                grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            bp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            bp.fillRect(QRectF(0.0, 0.0, float(w), float(self.height())), grad)
            bp.end()
            p.drawImage(0, 0, self._buf)
        else:
            pal = getattr(win, "palette", None)
            base_c = QColor(pal.bg_island) if pal is not None else QColor(14, 18, 28, 235)
            c0 = QColor(base_c.red(), base_c.green(), base_c.blue(), 0)
            c1 = QColor(base_c.red(), base_c.green(), base_c.blue(), min(230, max(160, base_c.alpha())))
            grad = QLinearGradient(0.0, 0.0, 0.0, float(self.height()))
            grad.setColorAt(0.0, c0 if self._is_bottom else c1)
            grad.setColorAt(1.0, c1 if self._is_bottom else c0)
            p.fillRect(self.rect(), grad)
        p.end()


class _ScrollEdgeFadePair(QObject):
    """Coordinates top & bottom fade strips for one QScrollArea."""

    def __init__(self, scroll_area: QScrollArea) -> None:
        super().__init__(scroll_area)
        self._sa = scroll_area
        self.top_strip = _EdgeFadeStrip(scroll_area, is_bottom=False)
        self.bot_strip = _EdgeFadeStrip(scroll_area, is_bottom=True)
        vp = scroll_area.viewport()
        if vp is not None:
            vp.installEventFilter(self)

    def sync_fades(self) -> None:
        vp = self._sa.viewport()
        if vp is None:
            return
        vp_rect = vp.geometry()
        vsb = self._sa.verticalScrollBar()
        mx = int(vsb.maximum())
        val = int(vsb.value())
        if mx <= 0:
            nt, nb = 0, 0
        else:
            nt = min(24, max(0, int(round(val * 0.9))))
            nb = min(28, max(0, int(round(max(0, mx - val) * 0.9))))
        self.top_strip.set_fade_px(nt, vp_rect)
        self.bot_strip.set_fade_px(nb, vp_rect)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move):
            self.sync_fades()
        return False


class _SlideOverlay(QWidget):
    """Translucent overlay rendering live / eased horizontal transitions between stacked tab pages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._pm_cur = QPixmap()
        self._pm_adj = QPixmap()
        self._offset_x = 0.0
        self._adj_side = 1  # +1 if incoming page sits to the right, -1 if to the left
        self._cur_widget: QWidget | None = None
        self._adj_widget: QWidget | None = None

    def _update_with_window(self) -> None:
        self.update()
        win = self.window()
        if win is not None and win is not self:
            win.update()

    def set_frames(
        self,
        pm_cur: QPixmap,
        pm_adj: QPixmap,
        offset_x: float,
        adj_side: int,
        cur_widget: QWidget | None = None,
        adj_widget: QWidget | None = None,
    ) -> None:
        self._pm_cur = pm_cur
        self._pm_adj = pm_adj
        self._offset_x = float(offset_x)
        self._adj_side = 1 if adj_side >= 0 else -1
        self._cur_widget = cur_widget
        self._adj_widget = adj_widget
        self._update_with_window()

    def set_offset(self, offset_x: float) -> None:
        self._offset_x = float(offset_x)
        self._update_with_window()

    def clear_frames(self) -> None:
        self._pm_cur = QPixmap()
        self._pm_adj = QPixmap()
        self._offset_x = 0.0
        self._cur_widget = None
        self._adj_widget = None

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        w = float(self.width())
        if w <= 1.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        prog = min(1.0, abs(self._offset_x) / max(1.0, w))
        if not self._pm_cur.isNull():
            p.setOpacity(max(0.28, 1.0 - 0.55 * prog))
            p.drawPixmap(QPointF(self._offset_x, 0.0), self._pm_cur)
        if not self._pm_adj.isNull():
            adj_x = self._offset_x + self._adj_side * w
            p.setOpacity(max(0.28, 0.45 + 0.55 * prog))
            p.drawPixmap(QPointF(adj_x, 0.0), self._pm_adj)
        p.end()


class _SlidingStackedWidget(QStackedWidget):
    """QStackedWidget with smooth eased page slides and live interactive horizontal scrub."""

    slide_finished = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overlay = _SlideOverlay(self)
        self._overlay.hide()
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._on_anim_finished)
        self._scrubbing = False
        self._scrub_base_idx = 0
        self._scrub_adj_idx = -1
        self._scrub_adj_side = 0
        self._scrub_pm_cur = QPixmap()
        self._scrub_pm_prev = QPixmap()
        self._scrub_pm_next = QPixmap()
        self._pending_clear = True

    def _clear_all_masks(self) -> None:
        for i in range(self.count()):
            w = self.widget(i)
            if w is not None:
                w.clearMask()

    def _capture_widget(self, w: QWidget | None) -> QPixmap:
        if w is None or self.width() <= 4 or self.height() <= 4:
            return QPixmap()
        was_masked = w.mask()
        if not was_masked.isEmpty():
            w.clearMask()
        w.resize(self.size())
        if w.layout() is not None:
            w.layout().activate()
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pm = QPixmap(max(1, int(round(self.width() * dpr))), max(1, int(round(self.height() * dpr))))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        w.render(pm, QPoint(), QRegion(), QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren)
        return pm

    def _mask_current(self) -> None:
        cur = self.currentWidget()
        if cur is not None:
            cur.setMask(QRegion(-4, -4, 1, 1))

    def _on_anim_value(self, val) -> None:
        self._overlay.set_offset(float(val))

    def _on_anim_finished(self) -> None:
        self._clear_all_masks()
        self._overlay.hide()
        self._overlay.clear_frames()
        win = self.window()
        if win is not None:
            win.update()
        self.slide_finished.emit()

    def stop_slide(self) -> None:
        if self._anim.state() == QVariantAnimation.State.Running:
            self._anim.stop()
        self._scrubbing = False
        self._clear_all_masks()
        self._overlay.hide()
        self._overlay.clear_frames()
        self.slide_finished.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self.stop_slide()
        super().setCurrentIndex(index)

    def slide_to(self, index: int, direction: int = 0) -> None:
        cnt = self.count()
        if cnt <= 0:
            return
        idx = max(0, min(cnt - 1, index))
        cur_idx = self.currentIndex()
        if idx == cur_idx and self._anim.state() != QVariantAnimation.State.Running and not self._scrubbing:
            return
        if not self.isVisible() or self.width() <= 16 or self.height() <= 16:
            self.setCurrentIndex(idx)
            return

        if direction == 0:
            direction = 1 if idx > cur_idx else -1

        # Snapshot current outgoing screen (preserve mid-flight overlay frame if interrupting!)
        old_w = self.widget(cur_idx)
        if self._overlay.isVisible():
            pm_old = self._capture_widget(self._overlay)
        else:
            pm_old = self._capture_widget(old_w)

        self._anim.stop()
        self._scrubbing = False
        self._clear_all_masks()
        super().setCurrentIndex(idx)

        next_w = self.widget(idx)
        pm_new = self._capture_widget(next_w)
        if pm_old.isNull() or pm_new.isNull():
            self._clear_all_masks()
            self._overlay.hide()
            return

        adj_side = 1 if direction > 0 else -1
        self._mask_current()
        self._overlay.setGeometry(self.rect())
        self._overlay.set_frames(pm_old, pm_new, 0.0, adj_side, cur_widget=old_w, adj_widget=next_w)
        self._overlay.show()
        self._overlay.raise_()
        win = self.window()
        if win is not None:
            win.update()

        w = float(self.width())
        self._anim.setDuration(260)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(-adj_side * w)
        self._anim.start()

    def begin_scrub(self) -> None:
        if not self.isVisible() or self.width() <= 16 or self.height() <= 16 or self.count() <= 1:
            return
        self._anim.stop()
        self._clear_all_masks()
        cur = self.currentIndex()
        self._scrub_base_idx = cur
        self._scrub_adj_idx = -1
        self._scrub_adj_side = 0
        self._scrub_pm_cur = self._capture_widget(self.widget(cur))
        prev_idx = (cur - 1) % self.count()
        next_idx = (cur + 1) % self.count()
        self._scrub_pm_prev = self._capture_widget(self.widget(prev_idx))
        self._scrub_pm_next = self._capture_widget(self.widget(next_idx))
        self._scrubbing = not self._scrub_pm_cur.isNull()
        if self._scrubbing:
            self._mask_current()
            self._overlay.setGeometry(self.rect())
            self._overlay.set_frames(
                self._scrub_pm_cur,
                self._scrub_pm_next,
                0.0,
                1,
                cur_widget=self.widget(cur),
                adj_widget=self.widget(next_idx),
            )
            self._overlay.show()
            self._overlay.raise_()

    def update_scrub(self, dx: float) -> None:
        if not self._scrubbing:
            return
        cnt = self.count()
        w = max(1.0, float(self.width()))
        clamped_dx = max(-w * 0.96, min(w * 0.96, float(dx)))
        if clamped_dx >= 0.0:
            adj_side = -1
            pm_adj = self._scrub_pm_prev
            self._scrub_adj_idx = (self._scrub_base_idx - 1) % cnt
        else:
            adj_side = 1
            pm_adj = self._scrub_pm_next
            self._scrub_adj_idx = (self._scrub_base_idx + 1) % cnt
        self._scrub_adj_side = adj_side
        self._overlay.set_frames(
            self._scrub_pm_cur,
            pm_adj,
            clamped_dx,
            adj_side,
            cur_widget=self.widget(self._scrub_base_idx),
            adj_widget=self.widget(self._scrub_adj_idx),
        )

    def finish_scrub(self, dx: float, vx: float = 0.0) -> int | None:
        """Complete live swipe: switch to neighbor or spring smoothly back; returns committed tab index or None."""
        if not self._scrubbing:
            return None
        self._scrubbing = False
        w = max(1.0, float(self.width()))
        clamped_dx = max(-w * 0.96, min(w * 0.96, float(dx)))
        commit = abs(clamped_dx) >= w * 0.16 or abs(vx) >= 0.35
        base_w = self.widget(self._scrub_base_idx)
        if commit and abs(dx) >= 12.0:
            dir_sign = -1 if (dx > 0 or (abs(dx) < 8 and vx > 0)) else 1
            cnt = self.count()
            target = (self._scrub_base_idx + dir_sign) % cnt
            pm_adj = self._scrub_pm_prev if dir_sign < 0 else self._scrub_pm_next
            adj_side = -1 if dir_sign < 0 else 1
            self._clear_all_masks()
            super().setCurrentIndex(target)
            self._mask_current()
            self._overlay.set_frames(
                self._scrub_pm_cur, pm_adj, clamped_dx, adj_side, cur_widget=base_w, adj_widget=self.widget(target)
            )
            self._overlay.show()
            self._overlay.raise_()
            self._anim.setDuration(210)
            self._anim.setStartValue(clamped_dx)
            self._anim.setEndValue(-adj_side * w)
            self._anim.start()
            return target
        # Spring smoothly back to origin
        adj_side = self._scrub_adj_side or (1 if clamped_dx < 0 else -1)
        pm_adj = self._scrub_pm_next if adj_side > 0 else self._scrub_pm_prev
        adj_idx = (self._scrub_base_idx + (1 if adj_side > 0 else -1)) % self.count()
        self._overlay.set_frames(
            self._scrub_pm_cur, pm_adj, clamped_dx, adj_side, cur_widget=base_w, adj_widget=self.widget(adj_idx)
        )
        self._anim.setDuration(190)
        self._anim.setStartValue(clamped_dx)
        self._anim.setEndValue(0.0)
        self._anim.start()
        return None


_HOME_BLOCK_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("media", "music", "Музыка", "Music"),
    ("control_hub", "controls", "Центр управления", "Control Hub"),
    ("tools", "tools", "Инструменты", "Quick Tools"),
    ("launcher", "rocket", "Приложения", "App Dock"),
)

_HOME_TOOL_SPECS: tuple[tuple[str, str, str], ...] = (
    ("screenshot_area", "screenshot", "screenshot"),
    ("screenshot_full", "fullscreen", "screenshot_full"),
    ("ocr", "ocr", "ocr"),
    ("color_picker", "color", "color_picker"),
    ("restart_screens", "monitor_reset", "restart_screens"),
    ("qr_scan", "qr", "qr_scan"),
    ("record", "record", "record"),
    ("dictate", "dictate", "dictate"),
    ("command_bar", "command", "command_bar"),
)


class _HomeCustomizePopup(QFrame):
    """Floating glass popover anchored to the Home edit pencil button."""

    closed = pyqtSignal()

    def __init__(
        self,
        config_mgr: ConfigManager,
        home_tab: HomeTab,
        palette: ThemePalette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.config_mgr = config_mgr
        self.home_tab = home_tab
        self._palette = palette

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("HomeCustomizeCard")
        outer.addWidget(self.card)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        self.title_lbl = QLabel()
        hdr.addWidget(self.title_lbl, 1)

        self.reset_btn = QPushButton()
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setFixedHeight(24)
        self.reset_btn.clicked.connect(self._show_all)
        hdr.addWidget(self.reset_btn)

        self.close_btn = QPushButton("")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.clicked.connect(self.close)
        hdr.addWidget(self.close_btn)
        lay.addLayout(hdr)

        self.blocks_lbl = QLabel()
        lay.addWidget(self.blocks_lbl)

        blocks_grid = QGridLayout()
        blocks_grid.setContentsMargins(0, 0, 0, 0)
        blocks_grid.setSpacing(6)
        self._block_btns: dict[str, QPushButton] = {}
        self._block_icons: dict[str, str] = {}
        for idx, (bk, ico, ru_lbl, en_lbl) in enumerate(_HOME_BLOCK_SPECS):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setIconSize(QSize(14, 14))
            btn.clicked.connect(self._on_toggled)
            self._block_btns[bk] = btn
            self._block_icons[bk] = ico
            blocks_grid.addWidget(btn, idx // 2, idx % 2)
        lay.addLayout(blocks_grid)

        self.tools_lbl = QLabel()
        lay.addWidget(self.tools_lbl)

        tools_grid = QGridLayout()
        tools_grid.setContentsMargins(0, 0, 0, 0)
        tools_grid.setSpacing(5)
        self._tool_btns: dict[str, QPushButton] = {}
        self._tool_icons: dict[str, str] = {}
        for idx, (tk, ico, _tr_key) in enumerate(_HOME_TOOL_SPECS):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(27)
            btn.setIconSize(QSize(13, 13))
            btn.clicked.connect(self._on_toggled)
            self._tool_btns[tk] = btn
            self._tool_icons[tk] = ico
            tools_grid.addWidget(btn, idx // 3, idx % 3)
        lay.addLayout(tools_grid)

        self.setFixedWidth(396)
        self.retranslate_ui()
        self.sync_from_config()
        self.apply_palette(palette)

    def retranslate_ui(self) -> None:
        ru = getattr(self.config_mgr.config, "language", "ru") == "ru"
        self.title_lbl.setText("Что показывать на главной" if ru else "Customize Home Screen")
        self.reset_btn.setText("Показать все" if ru else "Show all")
        self.blocks_lbl.setText("БЛОКИ" if ru else "BLOCKS")
        self.tools_lbl.setText("БЫСТРЫЕ ДЕЙСТВИЯ" if ru else "QUICK ACTIONS")
        for bk, _ico, ru_lbl, en_lbl in _HOME_BLOCK_SPECS:
            self._block_btns[bk].setText(" " + (ru_lbl if ru else en_lbl))
        for tk, _ico, tr_key in _HOME_TOOL_SPECS:
            self._tool_btns[tk].setText(" " + tr(tr_key))

    def sync_from_config(self) -> None:
        cfg = self.config_mgr.config
        hb = set(getattr(cfg, "home_hidden_blocks", []) or [])
        ht = set(getattr(cfg, "home_hidden_tools", []) or [])
        for bk, btn in self._block_btns.items():
            btn.blockSignals(True)
            btn.setChecked(bk not in hb)
            btn.blockSignals(False)
        for tk, btn in self._tool_btns.items():
            btn.blockSignals(True)
            btn.setChecked(tk not in ht)
            btn.blockSignals(False)
        self._refresh_button_icons()

    def _refresh_button_icons(self) -> None:
        pal = self._palette
        on_fg = contrast_fg(pal.accent)
        off_fg = getattr(pal, "text_secondary", pal.text_primary)
        for bk, btn in self._block_btns.items():
            chk = btn.isChecked()
            fg = on_fg if chk else pal.text_primary
            acc = on_fg if chk else pal.accent
            btn.setIcon(get_icon(self._block_icons[bk], size=14, color=fg, accent=acc))
        for tk, btn in self._tool_btns.items():
            chk = btn.isChecked()
            fg = on_fg if chk else off_fg
            acc = on_fg if chk else pal.accent
            btn.setIcon(get_icon(self._tool_icons[tk], size=13, color=fg, accent=acc))
        self.close_btn.setIcon(get_icon("close", size=12, color=pal.text_primary, accent=pal.accent))

    def apply_palette(self, pal: ThemePalette) -> None:
        self._palette = pal
        on_fg = contrast_fg(pal.accent)
        muted = getattr(pal, "text_secondary", getattr(pal, "text_muted", "#94a3b8"))
        self.card.setStyleSheet(
            f"QFrame#HomeCustomizeCard {{ background: rgba(14, 17, 25, 246); "
            f"border: 1px solid {pal.border}; border-radius: 16px; }}"
        )
        self.title_lbl.setStyleSheet(
            f"color: {pal.text_primary}; font-size: 12px; font-weight: 700; background: transparent;"
        )
        sec_qss = (
            f"color: {muted}; font-size: 10px; font-weight: 700; "
            "letter-spacing: 0.6px; margin-top: 2px; background: transparent;"
        )
        self.blocks_lbl.setStyleSheet(sec_qss)
        self.tools_lbl.setStyleSheet(sec_qss)
        self.reset_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255, 255, 255, 16); color: {pal.text_primary}; "
            f"border: 1px solid {pal.border}; border-radius: 12px; padding: 0px 10px; "
            "font-size: 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 28); border-color: {pal.accent}; }}"
        )
        self.close_btn.setStyleSheet(
            f"QPushButton {{ min-width: 24px; max-width: 26px; min-height: 24px; max-height: 26px; "
            f"border-radius: 13px; padding: 0px; background: rgba(255, 255, 255, 16); "
            f"border: 1px solid {pal.border}; }}"
            f"QPushButton:hover {{ background: rgba(239, 68, 68, 48); border-color: #EF4444; }}"
        )
        pill_qss = (
            f"QPushButton {{ background: rgba(255, 255, 255, 12); color: {muted}; "
            f"border: 1px solid rgba(255, 255, 255, 22); border-radius: 11px; "
            "padding: 0px 8px; font-size: 11px; font-weight: 600; text-align: left; }}"
            f"QPushButton:hover {{ background: rgba(255, 255, 255, 22); color: {pal.text_primary}; }}"
            f"QPushButton:checked {{ background: {pal.accent}; color: {on_fg}; "
            f"border: 1px solid {pal.accent}; font-weight: 700; }}"
        )
        for btn in self._block_btns.values():
            btn.setStyleSheet(pill_qss)
        for btn in self._tool_btns.values():
            btn.setStyleSheet(pill_qss)
        self._refresh_button_icons()

    def _on_toggled(self) -> None:
        cfg = self.config_mgr.config
        cfg.home_hidden_blocks = [bk for bk, btn in self._block_btns.items() if not btn.isChecked()]
        cfg.home_hidden_tools = [tk for tk, btn in self._tool_btns.items() if not btn.isChecked()]
        self.config_mgr.save()
        self._refresh_button_icons()
        self.home_tab.apply_home_layout()

    def _show_all(self) -> None:
        for btn in self._block_btns.values():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
        for btn in self._tool_btns.values():
            btn.blockSignals(True)
            btn.setChecked(True)
            btn.blockSignals(False)
        self._on_toggled()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.closed.emit()


class ExpandedPanel(QWidget):
    """Swipeable capsule tab bar + QStackedWidget containing all feature tabs."""

    tab_changed = pyqtSignal(int)
    collapse_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    tall_mode_changed = pyqtSignal(bool)

    def __init__(
        self,
        config_mgr: ConfigManager,
        storage: LocalStorage,
        bridge: NativeBridge,
        media_service: MediaService,
        clipboard_service: object | None,
        weather_service: WeatherService,
        ai_service: object | None,
        agents_service: object | None,
        palette: ThemePalette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_mgr = config_mgr
        self._drag_origin: QPoint | None = None
        self._drag_scroll_start = 0
        self._drag_start_t = 0.0
        self._drag_last_x = 0
        self._drag_last_t = 0.0
        self._drag_vx = 0.0
        self._did_drag = False
        self._scrub_active = False
        self._scrub_base_dx = 0
        self._wheel_target = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 0, 8, 0)
        root.setSpacing(0)

        nav = QHBoxLayout()
        nav.setSpacing(4)
        self._palette = palette

        self.brand_badge_btn = QPushButton("Blip Island")
        self.brand_badge_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.brand_badge_btn.setFixedHeight(26)
        self.brand_badge_btn.setToolTip("Подписка Blip / Активация PRO")
        self.brand_badge_btn.clicked.connect(self._open_pro_dialog)
        self.brand_badge_btn.setVisible(False)
        nav.addWidget(self.brand_badge_btn)
        self.refresh_license_badge()

        self._home_customize_popup: _HomeCustomizePopup | None = None
        self.home_edit_btn = QPushButton("")
        self.home_edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_edit_btn.setCheckable(True)
        self.home_edit_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.home_edit_btn.setFixedSize(38, 38)
        self.home_edit_btn.setIconSize(QSize(17, 17))
        self.home_edit_btn.setToolTip("Что показывать на главной")
        self.home_edit_btn.clicked.connect(self._toggle_home_customize_popup)
        nav.addWidget(self.home_edit_btn)

        self.tab_scroll = QScrollArea()
        self.tab_scroll.setWidgetResizable(True)
        self.tab_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tab_scroll.setFixedHeight(42)
        self.tab_scroll.setStyleSheet("background: transparent;")
        self.tab_scroll.viewport().installEventFilter(self)

        self._tab_strip = QWidget()
        self._tab_strip.setStyleSheet("background: transparent;")
        strip_lay = QHBoxLayout(self._tab_strip)
        strip_lay.setContentsMargins(0, 1, 0, 1)
        strip_lay.setSpacing(3)
        strip_lay.addStretch(1)

        self.tab_buttons: list[QPushButton] = []
        self._tab_icon_names: list[str] = []
        tab_specs = [
            ("home", tr("tab_home")),
            ("chat", tr("tab_chat")),
            ("character", tr("tab_character")),
            ("timer", "Часы и таймер"),
            ("weather", tr("tab_weather")),
            ("controls", tr("tab_controls")),
        ]
        self._tab_keys = [k for k, _ in tab_specs]
        self._tab_label_keys = [
            "tab_home",
            "tab_chat",
            "tab_character",
            "tab_notes",
            "tab_weather",
            "tab_controls",
        ]
        for idx, (ico_name, title) in enumerate(tab_specs):
            b = QPushButton("")
            b.setIcon(get_icon(ico_name, size=21, color=palette.text_primary, accent=palette.accent))
            b.setIconSize(QSize(21, 21))
            b.setToolTip(title)
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            b.setFixedSize(52, 38)
            b.setStyleSheet("border-radius: 19px; padding: 0px;")
            b.clicked.connect(lambda _, i=idx: self._on_pill_clicked(i))
            b.installEventFilter(self)
            self.tab_buttons.append(b)
            self._tab_icon_names.append(ico_name)
            strip_lay.addWidget(b)
        strip_lay.addStretch(1)
        self.tab_scroll.setWidget(self._tab_strip)
        nav.addWidget(self.tab_scroll, 1)
        self._nav_right_spacer = QWidget()
        self._nav_right_spacer.setFixedSize(38, 38)
        self._nav_right_spacer.setStyleSheet("background: transparent;")
        self._nav_right_spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        nav.addWidget(self._nav_right_spacer)
        self._bar_anim = QPropertyAnimation(self.tab_scroll.horizontalScrollBar(), b"value", self)
        self._bar_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._bar_anim.setDuration(250)
        root.addLayout(nav)

        self.stack = _SlidingStackedWidget()
        self.stack.setMinimumSize(0, 0)
        self.setMinimumSize(0, 0)
        eff_ai = ai_service if isinstance(ai_service, AIService) else AIService()
        self.home_tab = HomeTab(media_service, storage, config_mgr=config_mgr)
        self.home_tab.set_dev_tools_visible(bool(getattr(config_mgr.config, "show_dev_text_tools", False)))
        self.chat_tab = ChatTab(eff_ai, config_mgr, storage)
        self.chat_tab.tall_mode_changed.connect(lambda _: self._emit_tall_mode())
        self.character_tab = CharacterSettingsTab(config_mgr)
        self.notes_tab = NotesTab(storage)
        self.weather_tab = WeatherTab(weather_service, config_mgr)
        self.controls_tab = ControlsTab(bridge, config_mgr)
        self.pro_page = BlipProWidget(self, inline=True)
        self.pro_page.apply_palette(palette)
        self.pro_page.license_updated.connect(self._on_license_updated)
        self.pro_page.close_requested.connect(self._close_pro_page)
        self.pro_page.setVisible(False)
        self._last_regular_tab = 0
        self._pro_on_unlocked = None

        for tab in (
            self.home_tab,
            self.chat_tab,
            self.character_tab,
            self.notes_tab,
            self.weather_tab,
            self.controls_tab,
        ):
            self.stack.addWidget(tab)

        root.addWidget(self.stack, 1)
        root.addWidget(self.pro_page, 1)

        self.card_rain = CardRainOverlay(self.stack, self)
        self.stack.slide_finished.connect(lambda: self.card_rain.mark_underlay_dirty())
        self.tab_changed.connect(lambda _idx: self.card_rain.mark_underlay_dirty())

        # Keyboard left/right swipe shortcuts
        self._sc_prev = QShortcut(QKeySequence("Ctrl+Left"), self)
        self._sc_prev.activated.connect(lambda: self.step_tab(-1))
        self._sc_next = QShortcut(QKeySequence("Ctrl+Right"), self)
        self._sc_next.activated.connect(lambda: self.step_tab(1))

        self.apply_palette(palette)
        self.switch_tab(0)
        self.installEventFilter(self)
        self._install_smooth_wheel_all()

    def _install_smooth_wheel_all(self) -> None:
        """Install ultra-smooth damped wheel scrolling on every QScrollArea in all menus."""
        from PyQt6.QtWidgets import QScrollArea

        self._smooth_scrollers: list[tuple[QScrollArea, float, float]] = []
        self._smooth_scroll_timer = QTimer(self)
        self._smooth_scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._smooth_scroll_timer.setInterval(8)  # True 120 Hz silky scroll
        self._smooth_scroll_timer.timeout.connect(self._tick_smooth_scroll)
        self._smooth_scroll_last_ts = 0.0
        self._smooth_scroll_state: dict[int, list[float]] = {}
        for sa in self.findChildren(QScrollArea):
            if sa is self.tab_scroll:
                continue
            vp = sa.viewport()
            if vp is not None:
                vp.installEventFilter(self)
                vp.setProperty("_smooth_sa_id", id(sa))
                self._smooth_scrollers.append((sa, 0.0, 0.0))
            sb = sa.verticalScrollBar()
            if sb is not None and hasattr(self, "card_rain"):
                sb.valueChanged.connect(lambda _v=0: self.card_rain.mark_underlay_dirty())
        self._install_scroll_edge_fades()

    def _tick_smooth_scroll(self) -> None:
        import time as _t_mod

        now_s = _t_mod.monotonic()
        dt_s = 0.008 if self._smooth_scroll_last_ts <= 0.0 else max(0.003, min(0.032, now_s - self._smooth_scroll_last_ts))
        self._smooth_scroll_last_ts = now_s
        step_k = dt_s / 0.016
        scroll_gain = 0.22 * step_k
        vel_decay = 0.84 ** step_k
        any_active = False
        for sa, _, _ in self._smooth_scrollers:
            st = self._smooth_scroll_state.get(id(sa))
            if not st:
                continue
            vel, frac = st[0], st[1]
            if abs(vel) < 0.25 and abs(frac) < 0.25:
                st[0] = 0.0
                st[1] = 0.0
                continue
            sb = sa.verticalScrollBar()
            step_f = vel * scroll_gain + frac
            step_i = int(round(step_f))
            st[1] = step_f - step_i
            st[0] = vel * vel_decay
            if step_i != 0:
                nv = max(sb.minimum(), min(sb.maximum(), sb.value() + step_i))
                if nv == sb.value():
                    st[0] = 0.0
                    st[1] = 0.0
                else:
                    sb.setValue(nv)
            any_active = True
        self._smooth_scroll_active = any_active
        if not any_active:
            self._smooth_scroll_last_ts = 0.0
            self._smooth_scroll_timer.stop()
            win = self.window()
            if win is not None and hasattr(win, "_exp_composite_cache"):
                win._exp_composite_cache = None
                win.update()

    def _on_pill_clicked(self, idx: int) -> None:
        if self._did_drag:
            self._did_drag = False
            # Restore checked highlight to current active tab
            cur = self.stack.currentIndex()
            for i, b in enumerate(self.tab_buttons):
                b.setChecked(i == cur)
            return
        self.switch_tab(idx)

    def step_tab(self, delta: int) -> None:
        cur = self.stack.currentIndex()
        cnt = max(1, self.stack.count())
        nxt = (cur + delta) % cnt
        self.switch_tab(nxt, direction=(1 if delta >= 0 else -1))

    def _install_scroll_edge_fades(self) -> None:
        """Attach lightweight backdrop-sampled edge fade strips to every vertical scroll area."""
        if not hasattr(self, "_scroll_fade_effects"):
            self._scroll_fade_effects: list[_ScrollEdgeFadePair] = []
        for sa in self.findChildren(QScrollArea):
            if sa is getattr(self, "tab_scroll", None):
                continue
            if sa.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
                continue
            if sa.property("_edge_fade_pair") is not None:
                continue
            pair = _ScrollEdgeFadePair(sa)
            sa.setProperty("_edge_fade_pair", pair)
            pair.sync_fades()
            vsb = sa.verticalScrollBar()
            vsb.valueChanged.connect(lambda _val, pr=pair: pr.sync_fades())
            vsb.rangeChanged.connect(lambda _lo, _hi, pr=pair: pr.sync_fades())
            self._scroll_fade_effects.append(pair)

    def _smooth_scroll_to_tab(self, idx: int) -> None:
        if not (0 <= idx < len(self.tab_buttons)):
            return
        sb = self.tab_scroll.horizontalScrollBar()
        if sb.maximum() <= 0:
            return
        btn = self.tab_buttons[idx]
        vp_w = max(1, self.tab_scroll.viewport().width())
        btn_center = btn.x() + btn.width() * 0.5
        target = int(round(btn_center - vp_w * 0.5))
        target = max(sb.minimum(), min(sb.maximum(), target))
        if target == sb.value():
            return
        self._bar_anim.stop()
        self._wheel_target = target
        self._bar_anim.setDuration(260)
        self._bar_anim.setStartValue(sb.value())
        self._bar_anim.setEndValue(target)
        self._bar_anim.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if not hasattr(self, "tab_scroll") or not hasattr(self, "tab_buttons"):
            return super().eventFilter(obj, event)
        is_strip = obj is self.tab_scroll.viewport() or obj in self.tab_buttons
        is_header_self = obj is self
        if isinstance(event, QWheelEvent) and not is_strip:
            sa_id = obj.property("_smooth_sa_id") if hasattr(obj, "property") else None
            if sa_id is not None and hasattr(self, "_smooth_scroll_state"):
                dy = event.pixelDelta().y() or event.angleDelta().y()
                if dy != 0:
                    st = self._smooth_scroll_state.setdefault(int(sa_id), [0.0, 0.0])
                    step = -float(dy) * (0.48 if abs(event.angleDelta().y()) >= 120 else 0.75)
                    st[0] = max(-220.0, min(220.0, st[0] + step))
                    if not self._smooth_scroll_timer.isActive():
                        self._smooth_scroll_timer.start()
                    event.accept()
                    return True
        if isinstance(event, QWheelEvent) and is_strip:
            dx = event.pixelDelta().x() or event.angleDelta().x()
            dy = event.pixelDelta().y() or event.angleDelta().y()
            sb = self.tab_scroll.horizontalScrollBar()
            step = dx if abs(dx) > abs(dy) else dy
            if step != 0:
                if abs(event.angleDelta().y()) >= 120 and sb.maximum() > 0:
                    delta = int(step * 0.55)
                else:
                    delta = int(step)
                base = self._wheel_target if self._bar_anim.state() == QPropertyAnimation.State.Running else sb.value()
                target = max(sb.minimum(), min(sb.maximum(), base - delta))
                self._wheel_target = target
                self._bar_anim.stop()
                self._bar_anim.setDuration(150)
                self._bar_anim.setStartValue(sb.value())
                self._bar_anim.setEndValue(target)
                self._bar_anim.start()
                return True
        if is_strip or is_header_self:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton:
                    if is_header_self and event.position().y() > 68:
                        return super().eventFilter(obj, event)
                    now = time.monotonic()
                    self._drag_origin = event.globalPosition().toPoint()
                    self._drag_start_t = now
                    self._drag_last_x = self._drag_origin.x()
                    self._drag_last_t = now
                    self._drag_vx = 0.0
                    self._drag_scroll_start = self.tab_scroll.horizontalScrollBar().value()
                    self._did_drag = False
                    self._scrub_active = False
                    self._scrub_base_dx = 0
                    self._bar_anim.stop()
            elif et == QEvent.Type.MouseMove and isinstance(event, QMouseEvent) and self._drag_origin is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    now = time.monotonic()
                    cur_pt = event.globalPosition().toPoint()
                    dx = cur_pt.x() - self._drag_origin.x()
                    dt_inst = max(0.004, now - self._drag_last_t)
                    inst_vx = (cur_pt.x() - self._drag_last_x) / (dt_inst * 1000.0)  # px / ms
                    self._drag_vx = self._drag_vx * 0.45 + inst_vx * 0.55
                    self._drag_last_x = cur_pt.x()
                    self._drag_last_t = now
                    if abs(dx) >= 8:
                        self._did_drag = True
                        sb = self.tab_scroll.horizontalScrollBar()
                        if is_header_self or sb.maximum() == 0:
                            if not self._scrub_active:
                                self._scrub_active = True
                                self._scrub_base_dx = dx
                                self.stack.begin_scrub()
                            self.stack.update_scrub(float(dx - self._scrub_base_dx))
                            return True
                        desired = self._drag_scroll_start - dx
                        if desired < sb.minimum() or desired > sb.maximum():
                            clamped = max(sb.minimum(), min(sb.maximum(), desired))
                            sb.setValue(clamped)
                            overscroll = -(desired - clamped)
                            if not self._scrub_active:
                                self._scrub_active = True
                                self._scrub_base_dx = dx
                                self.stack.begin_scrub()
                            self.stack.update_scrub(float(dx - self._scrub_base_dx) if overscroll else 0.0)
                        else:
                            if self._scrub_active:
                                self.stack.update_scrub(0.0)
                            sb.setValue(desired)
                        return True
            elif et == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                if self._drag_origin is not None:
                    now = time.monotonic()
                    dx = event.globalPosition().toPoint().x() - self._drag_origin.x()
                    elapsed = max(0.02, now - self._drag_start_t)
                    was_drag = self._did_drag
                    was_scrub = self._scrub_active
                    scrub_dx = dx - self._scrub_base_dx
                    vx = self._drag_vx
                    self._drag_origin = None
                    self._scrub_active = False
                    if was_scrub:
                        committed = self.stack.finish_scrub(float(scrub_dx), vx)
                        if committed is not None:
                            self._last_regular_tab = committed
                            self._sync_tab_chrome(committed)
                            self.tab_changed.emit(committed)
                            self._emit_tall_mode()
                        self._did_drag = False
                        return True
                    if was_drag and abs(dx) >= 52 and (elapsed <= 0.42 or abs(vx) >= 0.28):
                        self.step_tab(-1 if dx > 0 else 1)
                        self._did_drag = False
                        return True
        if event.type() in (QEvent.Type.MouseButtonRelease, QEvent.Type.KeyRelease) and hasattr(self, "card_rain"):
            self.card_rain.mark_underlay_dirty()
        return super().eventFilter(obj, event)

    def apply_palette(self, palette: ThemePalette, font_family: str = "") -> None:
        self._palette = palette
        self._font_family = font_family or getattr(self.config_mgr.config, "ui_font_family", "")
        gm = getattr(self.config_mgr.config, "glass_mode", "liquid")
        if not get_license_manager().is_feature_allowed("glass"):
            gm = "off"
        qss_sig = (
            palette.is_dark,
            palette.accent,
            palette.bg_island,
            palette.bg_card,
            palette.border,
            palette.text_primary,
            palette.text_secondary,
            self._font_family,
            gm,
        )
        if getattr(self, "_panel_qss_sig", None) != qss_sig:
            self._panel_qss_sig = qss_sig
            self.setStyleSheet(build_panel_qss(palette, font_family=self._font_family, glass_mode=gm))
        self.refresh_license_badge()
        self._refresh_nav_icons()
        if hasattr(self, "home_tab"):
            self.home_tab.apply_palette(palette)
        if hasattr(self, "character_tab"):
            self.character_tab.apply_palette(palette)
        if hasattr(self, "controls_tab") and hasattr(self.controls_tab, "apply_palette"):
            self.controls_tab.apply_palette(palette)
        elif hasattr(self, "controls_tab") and hasattr(self.controls_tab, "_sync_autostart_btn_text"):
            self.controls_tab._sync_autostart_btn_text()
        if hasattr(self, "pro_page"):
            self.pro_page.apply_palette(palette)
        if self._home_customize_popup is not None:
            self._home_customize_popup.apply_palette(palette)
        if hasattr(self, "card_rain"):
            self.card_rain.mark_underlay_dirty()
        if hasattr(self, "stack"):
            self._install_scroll_edge_fades()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "card_rain"):
            self.card_rain.sync_geometry()

    def switch_tab_by_key(self, key: str, *, direction: int = 0) -> None:
        """Activate a tab by its stable identifier key (e.g. 'home', 'character', 'weather')."""
        keys = getattr(self, "_tab_keys", [])
        if key in keys:
            self.switch_tab(keys.index(key), direction=direction)

    def retranslate_ui(self) -> None:
        """Update panel tab tooltips and child tab translations when language changes live."""
        keys = getattr(self, "_tab_label_keys", [])
        for idx, btn in enumerate(getattr(self, "tab_buttons", [])):
            if idx < len(keys):
                label = tr(keys[idx])
                btn.setText("")
                btn.setToolTip(label)
        if hasattr(self, "home_edit_btn"):
            ru = getattr(self.config_mgr.config, "language", "ru") == "ru"
            self.home_edit_btn.setToolTip("Что показывать на главной" if ru else "Customize Home Screen")
        if self._home_customize_popup is not None:
            self._home_customize_popup.retranslate_ui()
        if hasattr(self, "home_tab") and hasattr(self.home_tab, "retranslate_ui"):
            self.home_tab.retranslate_ui()
        if hasattr(self, "controls_tab") and hasattr(self.controls_tab, "retranslate_ui"):
            self.controls_tab.retranslate_ui()

    def _refresh_nav_icons(self) -> None:
        pal = self._palette
        cur = self.stack.currentIndex() if hasattr(self, "stack") else 0
        for i, (b, ico_name) in enumerate(zip(self.tab_buttons, self._tab_icon_names)):
            fg = contrast_fg(pal.accent) if i == cur else pal.text_primary
            acc = fg if i == cur else pal.accent
            b.setIcon(get_icon(ico_name, size=21, color=fg, accent=acc))
        if hasattr(self, "home_edit_btn"):
            popup_open = bool(self._home_customize_popup is not None and self._home_customize_popup.isVisible())
            self.home_edit_btn.blockSignals(True)
            self.home_edit_btn.setChecked(popup_open)
            self.home_edit_btn.blockSignals(False)
            on_fg = contrast_fg(pal.accent)
            fg = on_fg if popup_open else pal.text_primary
            acc = on_fg if popup_open else pal.accent
            self.home_edit_btn.setIcon(get_icon("pen", size=17, color=fg, accent=acc))
            self.home_edit_btn.setStyleSheet("border-radius: 19px; padding: 0px;")

    def _toggle_home_customize_popup(self) -> None:
        if self._home_customize_popup is not None and self._home_customize_popup.isVisible():
            self._home_customize_popup.close()
            self._refresh_nav_icons()
            return
        if self._home_customize_popup is None:
            self._home_customize_popup = _HomeCustomizePopup(
                self.config_mgr,
                self.home_tab,
                self._palette,
                self,
            )
            self._home_customize_popup.closed.connect(self._refresh_nav_icons)
        else:
            self._home_customize_popup.retranslate_ui()
            self._home_customize_popup.sync_from_config()
            self._home_customize_popup.apply_palette(self._palette)
        self._home_customize_popup.adjustSize()
        anchor = self.home_edit_btn.mapToGlobal(QPoint(0, self.home_edit_btn.height() + 6))
        self._home_customize_popup.move(anchor)
        self._home_customize_popup.show()
        self._refresh_nav_icons()

    def _sync_tab_chrome(self, idx: int) -> None:
        for i, b in enumerate(self.tab_buttons):
            b.setChecked(i == idx)
        on_home = (idx == 0) and not (hasattr(self, "pro_page") and self.pro_page.isVisible())
        if hasattr(self, "home_edit_btn"):
            self.home_edit_btn.setVisible(on_home)
        if hasattr(self, "_nav_right_spacer"):
            self._nav_right_spacer.setVisible(on_home)
        if not on_home and self._home_customize_popup is not None and self._home_customize_popup.isVisible():
            self._home_customize_popup.close()
        self._refresh_nav_icons()
        self._smooth_scroll_to_tab(idx)
        self._emit_tall_mode()

    def wants_tall_island(self) -> bool:
        if hasattr(self, "pro_page") and self.pro_page.isVisible():
            return False
        if hasattr(self, "stack"):
            cur = self.stack.currentWidget()
            if hasattr(self, "character_tab") and cur is self.character_tab:
                return True
            if hasattr(self, "chat_tab") and cur is self.chat_tab:
                return self.chat_tab.wants_tall_island()
        return False

    def _emit_tall_mode(self) -> None:
        self.tall_mode_changed.emit(self.wants_tall_island())

    def switch_tab(self, index: int, *, direction: int = 0) -> None:
        if hasattr(self, "pro_page") and self.pro_page.isVisible():
            self.pro_page.stop_polling()
            self.pro_page.setVisible(False)
            self.stack.setVisible(True)
        idx = max(0, min(self.stack.count() - 1, index))
        if hasattr(self, "_tab_keys") and idx < len(self._tab_keys) and self._tab_keys[idx] == "weather":
            if not get_license_manager().is_feature_allowed("weather"):
                self.show_pro_page(
                    "Погода и живой атмосферный фон доступны только в Blip PRO (300 ₽ / 3 мес).",
                    on_unlocked=lambda: self.switch_tab_by_key("weather"),
                )
                return
        self._last_regular_tab = idx
        self.stack.slide_to(idx, direction=direction)
        self._sync_tab_chrome(idx)
        self.tab_changed.emit(idx)
        self._emit_tall_mode()

    def show_pro_page(self, reason: str = "", on_unlocked=None) -> None:
        self._last_regular_tab = self.stack.currentIndex()
        self._pro_on_unlocked = on_unlocked
        self.pro_page.refresh_status(reason)
        self.pro_page.apply_palette(self._palette)
        self.stack.setVisible(False)
        self.pro_page.setVisible(True)
        if hasattr(self, "home_edit_btn"):
            self.home_edit_btn.setVisible(False)
        if hasattr(self, "_nav_right_spacer"):
            self._nav_right_spacer.setVisible(False)
        if self._home_customize_popup is not None and self._home_customize_popup.isVisible():
            self._home_customize_popup.close()
        if hasattr(self, "pro_page") and hasattr(self, "card_rain"):
            self.card_rain.hide()
        self._emit_tall_mode()

    def _close_pro_page(self) -> None:
        self.pro_page.stop_polling()
        self.pro_page.setVisible(False)
        self.stack.setVisible(True)
        self.switch_tab(getattr(self, "_last_regular_tab", 0), direction=-1)
        if hasattr(self, "card_rain"):
            self.card_rain.mark_underlay_dirty()
        self._emit_tall_mode()

    def refresh_license_badge(self) -> None:
        st = get_license_manager().get_status()
        hide_pro = bool(getattr(self.config_mgr.config, "hide_pro_badge", False))
        pal = self._palette
        if st.is_pro:
            label = "Blip" if hide_pro else "Blip PRO"
            self.brand_badge_btn.setText(label)
            self.brand_badge_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(255, 255, 255, 24); color: {pal.text_primary};"
                f" border: 1px solid {pal.border}; border-radius: 13px;"
                " padding: 0px 9px; font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background: rgba(255, 255, 255, 38); }"
            )
        else:
            muted = getattr(pal, "text_secondary", getattr(pal, "text_muted", "#94a3b8"))
            self.brand_badge_btn.setText("Blip FREE")
            self.brand_badge_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(255, 255, 255, 14); color: {muted};"
                f" border: 1px solid {pal.border}; border-radius: 13px;"
                " padding: 0px 9px; font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background: rgba(255, 255, 255, 26); }"
            )

    def _open_pro_dialog(self) -> None:
        self.show_pro_page()

    def _on_license_updated(self) -> None:
        self.refresh_license_badge()
        self.character_tab.sync_from_config()
        self.controls_tab.sync_from_config()
        self.controls_tab.config_changed.emit()
        cb = getattr(self, "_pro_on_unlocked", None)
        if callable(cb):
            cb()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._bar_anim.stop()
        if hasattr(self.stack, "stop_slide"):
            self.stack.stop_slide()
        if hasattr(self, "weather_tab"):
            self.weather_tab.close()
        if hasattr(self, "chat_tab"):
            self.chat_tab.close()
        if hasattr(self, "character_tab"):
            self.character_tab.close()
        super().closeEvent(event)
