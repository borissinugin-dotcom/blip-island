"""Click-through warm amber overlay that removes harsh blue light across all displays."""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter
from PyQt6.QtWidgets import QWidget

from ..core.win32_api import set_exclude_from_capture, set_overlay_window_flags


class BlueLightFilterOverlay(QWidget):
    """Full virtual-desktop translucent amber tint overlay for Yellow (No-Blue) Eye-Care Mode."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enabled = False
        self._exclude_capture = False
        # Warm amber tint (R=255, G=176, B=44, A=68): attenuates cold blue emission comfortably
        self._tint = QColor(255, 176, 44, 68)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        app = QGuiApplication.instance()
        if app is not None:
            for sig_name in ("screenAdded", "screenRemoved", "primaryScreenChanged"):
                sig = getattr(app, sig_name, None)
                if sig is not None:
                    sig.connect(self.sync_geometry)

    @property
    def is_filter_enabled(self) -> bool:
        return self._enabled

    def _virtual_geometry(self) -> QRect:
        screens = QGuiApplication.screens()
        if not screens:
            return QRect(0, 0, 1920, 1080)
        virt = QRect(screens[0].geometry())
        for scr in screens[1:]:
            virt = virt.united(scr.geometry())
        return virt

    def sync_geometry(self) -> None:
        rect = self._virtual_geometry()
        if self.geometry() != rect:
            self.setGeometry(rect)

    def _apply_native_flags(self) -> None:
        if not QGuiApplication.platformName().lower().startswith("windows"):
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        if hwnd:
            set_overlay_window_flags(hwnd, click_through=True, no_activate=True, app_window=False)
            set_exclude_from_capture(hwnd, self._exclude_capture)

    def set_exclude_capture(self, exclude: bool) -> None:
        self._exclude_capture = bool(exclude)
        if self.isVisible():
            self._apply_native_flags()

    def set_enabled(self, enabled: bool) -> None:
        want = bool(enabled)
        self._enabled = want
        if want:
            self.sync_geometry()
            self.show()
            self._apply_native_flags()
            self.raise_()
            self.update()
        else:
            self.hide()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_native_flags()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if not self._enabled:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), self._tint)
        p.end()
