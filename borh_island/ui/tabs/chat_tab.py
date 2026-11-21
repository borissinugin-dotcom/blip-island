"""AI Chat tab: Gemini, DeepSeek, ChatGPT hosted via background Chrome/Edge/Yandex app windows docked cleanly inside the Island."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QCoreApplication, QPoint, QPropertyAnimation, QRect, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.browser_integration import (
    BrowserInfo,
    close_browser_hwnd,
    configure_docked_browser_hwnd,
    detect_installed_browsers,
    enum_chromium_hwnds,
    get_foreground_hwnd,
    get_window_rect,
    hide_browser_hwnd,
    is_hwnd_active_tree,
    is_window_alive,
    is_window_below_normal_sibling,
    is_window_visible,
    launch_in_browser,
    resolve_browser_for_mode,
    restore_browser_hwnd,
    sanitize_ai_url,
    send_browser_nav_key,
    sync_docked_browser_hwnd,
)
from ...core.config import ConfigManager
from ...core.storage import LocalStorage
from ...services.ai_service import AIService
from ..icons import get_icon

try:
    from PyQt6.QtWebEngineCore import (
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineSettings,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    _HAS_WEBENGINE = True
except Exception:
    QWebEnginePage = object  # type: ignore[assignment, misc]
    QWebEngineProfile = object  # type: ignore[assignment, misc]
    QWebEngineSettings = object  # type: ignore[assignment, misc]
    QWebEngineView = object  # type: ignore[assignment, misc]
    _HAS_WEBENGINE = False


_WEB_PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ("gemini", "Gemini", "https://gemini.google.com/app"),
    ("chatgpt", "ChatGPT", "https://chatgpt.com/"),
    ("claude", "Claude", "https://claude.ai/"),
    ("grok", "Grok", "https://grok.com/"),
    ("deepseek", "DeepSeek", "https://chat.deepseek.com/"),
)

_PROVIDER_URL_MAP: dict[str, str] = {sid: url for sid, _lbl, url in _WEB_PROVIDERS}
_PROVIDER_LABEL_MAP: dict[str, str] = {sid: lbl for sid, lbl, _url in _WEB_PROVIDERS}


def _native_dock_ready_in_process() -> bool:
    """Return True when real Win32 desktop browser docking should execute (never in headless offscreen tests)."""
    if sys.platform != "win32":
        return False
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return False
    return QCoreApplication.instance() is not None


def _webengine_ready_in_process() -> bool:
    """Emergency fallback guard when no desktop browser binary exists."""
    if not _HAS_WEBENGINE:
        return False
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return False
    app = QCoreApplication.instance()
    if app is None:
        return False
    if not app.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts):
        return False
    return bool(app.arguments())


def _needs_external_auth_hint(url_str: str, title_str: str = "") -> bool:
    u = (url_str or "").lower()
    t = (title_str or "").lower()
    if "accounts.google.com" in u or "/signin/rejected" in u or "auth.openai.com" in u or "/sign_in" in u:
        return True
    return any(tok in t for tok in ("не удалось войти", "небезопас", "couldn't sign you in", "couldn’t sign you in", "not be secure"))


class _OverlayComboBox(QComboBox):
    """QComboBox emitting synchronous popup visibility updates so dropdowns stay above native docked HWNDs."""

    popup_toggled = pyqtSignal(bool)

    def showPopup(self) -> None:  # noqa: N802
        super().showPopup()
        self.popup_toggled.emit(True)

    def hidePopup(self) -> None:  # noqa: N802
        super().hidePopup()
        self.popup_toggled.emit(False)


class ChatTab(QWidget):
    """Web AI Chat tab: Gemini, DeepSeek, ChatGPT backed by real installed Chrome/Edge/Yandex docked seamlessly."""

    reply_received = pyqtSignal(str, bool)
    request_screenshot_attach = pyqtSignal()
    open_settings_requested = pyqtSignal()
    thinking_changed = pyqtSignal(bool)
    tall_mode_changed = pyqtSignal(bool)

    def __init__(
        self,
        ai_service: AIService,
        config_mgr: ConfigManager,
        storage: LocalStorage,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ai_service = ai_service
        self.config_mgr = config_mgr
        self.storage = storage
        self._active_service = "gemini"
        self._initial_shown = False
        self._browsers: list[BrowserInfo] = detect_installed_browsers(only_available=True)

        # Docked native browser sessions: {service_id: (hwnd, 0, browser_id, orig_style, orig_exstyle)}
        self._docked_browsers: dict[str, tuple[int, int, str, int, int]] = {}
        # Pending background capture: (service_id, browser_info, prev_hwnds, deadline_ts)
        self._pending_capture: tuple[str, BrowserInfo, dict[int, tuple[str, str, int]], float] | None = None
        self._last_synced_rect: tuple[int, int, int, int] | None = None
        self._portal_live: bool = False

        self._web_profile: QWebEngineProfile | None = None
        self._web_hosts: dict[str, QWidget] = {}
        self._web_views: dict[str, QWebEngineView] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(6)

        # --- Top Bar: [Gemini] [ChatGPT] [Claude] [Grok] [DeepSeek] ... [Browser ▾] [◀] [↻] [🌐] [↗ В окно] ---
        top_bar = QHBoxLayout()
        top_bar.setSpacing(4)

        self._service_btns: dict[str, QPushButton] = {}
        for sid, label, _url in _WEB_PROVIDERS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(sid == self._active_service)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { min-height: 26px; max-height: 28px; padding: 0px 9px; border-radius: 14px; font-size: 11px; font-weight: 600; }"
            )
            btn.clicked.connect(lambda _, s=sid: self.select_service(s))
            self._service_btns[sid] = btn
            top_bar.addWidget(btn)

        top_bar.addStretch(1)

        self.browser_combo = _OverlayComboBox()
        self.browser_combo.setFixedHeight(28)
        self.browser_combo.setMinimumWidth(104)
        self.browser_combo.setMaximumWidth(165)
        self.browser_combo.setToolTip("Выбор установленного браузера для чата")
        self._populate_browser_combo()
        self.browser_combo.activated.connect(self._on_browser_mode_selected)
        self.browser_combo.popup_toggled.connect(self._on_browser_popup_toggled)
        top_bar.addWidget(self.browser_combo)

        self.web_back_btn = QPushButton("◀")
        self.web_back_btn.setToolTip("Назад")
        self.web_back_btn.setFixedSize(28, 28)
        self.web_back_btn.setStyleSheet(
            "min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        self.web_back_btn.clicked.connect(self._web_back)
        top_bar.addWidget(self.web_back_btn)

        self.web_reload_btn = QPushButton("")
        self.web_reload_btn.setIcon(get_icon("refresh", 13, "#F2F5FA", "#60A5FA"))
        self.web_reload_btn.setToolTip("Обновить страницу")
        self.web_reload_btn.setFixedSize(28, 28)
        self.web_reload_btn.setStyleSheet(
            "min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        self.web_reload_btn.clicked.connect(self._web_reload)
        top_bar.addWidget(self.web_reload_btn)

        self.open_ext_btn = QPushButton("🌐")
        self.open_ext_btn.setToolTip("Перезапустить / открыть чат в выбранном браузере")
        self.open_ext_btn.setFixedSize(28, 28)
        self.open_ext_btn.setStyleSheet(
            "min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        self.open_ext_btn.clicked.connect(self._relaunch_active_browser)
        top_bar.addWidget(self.open_ext_btn)

        self.popout_btn = QPushButton("↗ В окно")
        self.popout_btn.setToolTip("Открепить чат в обычное отдельное окно")
        self.popout_btn.setFixedHeight(28)
        self.popout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.popout_btn.setStyleSheet(
            "QPushButton { min-height: 26px; max-height: 28px; padding: 0px 10px; border-radius: 14px; font-size: 11px; font-weight: 600; }"
        )
        self.popout_btn.clicked.connect(self._popout_active)
        top_bar.addWidget(self.popout_btn)
        layout.addLayout(top_bar)

        self.pane_stack = QStackedWidget()
        layout.addWidget(self.pane_stack, 1)

        self._dock_card = self._build_dock_card()
        self.pane_stack.addWidget(self._dock_card)
        # Keep backward-compatible alias for any callers inspecting _ext_card
        self._ext_card = self._dock_card

        self._dock_poll_timer = QTimer(self)
        self._dock_poll_timer.setInterval(50)
        self._dock_poll_timer.timeout.connect(self._poll_pending_browser_hwnd)

        self._dock_sync_timer = QTimer(self)
        self._dock_sync_timer.setInterval(40)
        self._dock_sync_timer.timeout.connect(self._sync_active_docked_hwnd)

    def _populate_browser_combo(self) -> None:
        self._browsers = detect_installed_browsers(only_available=True)
        self.browser_combo.blockSignals(True)
        self.browser_combo.clear()
        self.browser_combo.addItem("Авто", "auto")
        for b in self._browsers:
            self.browser_combo.addItem(b.name, b.browser_id)

        saved_mode = str(getattr(self.config_mgr.config, "chat_browser_mode", "auto") or "auto").strip().lower()
        idx = self.browser_combo.findData(saved_mode)
        if idx < 0:
            idx = 0
            if saved_mode != "auto":
                self.config_mgr.config.chat_browser_mode = "auto"
                self.config_mgr.save()
        self.browser_combo.setCurrentIndex(idx)
        self.browser_combo.blockSignals(False)

    def _build_dock_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        lay.addStretch(1)

        self._ext_title_lbl = QLabel("Gemini  •  Google Chrome")
        self._ext_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ext_title_lbl.setStyleSheet("font-size: 16px; font-weight: 700;")
        lay.addWidget(self._ext_title_lbl)

        self._ext_desc_lbl = QLabel("")
        self._ext_desc_lbl.setWordWrap(True)
        self._ext_desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ext_desc_lbl.setObjectName("Muted")
        self._ext_desc_lbl.setStyleSheet("font-size: 12px;")
        lay.addWidget(self._ext_desc_lbl)

        self._ext_launch_btn = QPushButton("🚀 Открыть Gemini")
        self._ext_launch_btn.setObjectName("Primary")
        self._ext_launch_btn.setFixedHeight(36)
        self._ext_launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ext_launch_btn.clicked.connect(self._relaunch_active_browser)
        lay.addWidget(self._ext_launch_btn, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        return card

    def _current_browser_mode(self) -> str:
        raw = self.browser_combo.currentData()
        if raw:
            return str(raw).strip().lower()
        return str(getattr(self.config_mgr.config, "chat_browser_mode", "auto") or "auto").strip().lower()

    def _resolved_browser(self) -> BrowserInfo | None:
        return resolve_browser_for_mode(self._current_browser_mode(), browsers=self._browsers)

    def _refresh_dock_card_text(self, *, launching: bool = False) -> None:
        svc_name = _PROVIDER_LABEL_MAP.get(self._active_service, self._active_service.title())
        chosen = self._resolved_browser()
        b_name = chosen.name if chosen is not None else "Браузер"
        self._ext_title_lbl.setText(f"{svc_name}  •  {b_name}")
        if chosen is not None and chosen.supports_app_mode:
            if launching:
                self._ext_desc_lbl.setText(
                    f"Запускаю {svc_name} в фоне через {b_name} и встраиваю прямо в это окно…"
                )
            else:
                self._ext_desc_lbl.setText(
                    f"Чат {svc_name} работает через {b_name} с вашим профилем — авторизация Google, OpenAI, Claude, Grok и DeepSeek проходит без ограничений."
                )
            self._ext_launch_btn.setText(f"↻ Перезапустить {svc_name} ({b_name})")
        else:
            self._ext_desc_lbl.setText(
                f"Откройте {svc_name} через {b_name} для стабильного входа в аккаунт."
            )
            self._ext_launch_btn.setText(f"🚀 Открыть {svc_name} ({b_name})")

    def _set_browser_mode(self, mode: str) -> None:
        idx = self.browser_combo.findData(mode)
        if idx < 0:
            idx = 0
            mode = "auto"
        self.browser_combo.setCurrentIndex(idx)
        self.config_mgr.config.chat_browser_mode = mode
        self.config_mgr.save()
        self.select_service(self._active_service)

    def _on_browser_popup_toggled(self, _opened: bool) -> None:
        self._last_synced_rect = None
        self._sync_active_docked_hwnd()

    def _on_browser_mode_selected(self) -> None:
        mode = self._current_browser_mode()
        self.config_mgr.config.chat_browser_mode = mode
        self.config_mgr.save()
        chosen = self._resolved_browser()
        want_bid = chosen.browser_id if chosen is not None else ""
        # Close previously docked HWNDs if browser engine actually changed
        for sid in list(self._docked_browsers.keys()):
            hwnd, clipper, bid, _st, _ex = self._docked_browsers[sid]
            if bid != want_bid:
                self._docked_browsers.pop(sid, None)
                close_browser_hwnd(hwnd, clipper)
        self._last_synced_rect = None
        self.select_service(self._active_service)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._initial_shown:
            self._initial_shown = True
        self.select_service(self._active_service)
        if _native_dock_ready_in_process() and not self._dock_sync_timer.isActive():
            self._dock_sync_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._dock_sync_timer.stop()
        self._set_portal_live(False)
        self._hide_all_docked_hwnds()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._last_synced_rect = None
        if self.isVisible():
            self._sync_active_docked_hwnd()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._last_synced_rect = None
        if self.isVisible():
            self._sync_active_docked_hwnd()

    def wants_tall_island(self) -> bool:
        """Keep Island tall when displaying docked Chat UI."""
        return True

    def has_active_webview(self) -> bool:
        """Return True while docked native browser window/clipper or fallback WebEngine owns focus."""
        fg = get_foreground_hwnd()
        if fg and any(is_hwnd_active_tree(entry[0], entry[1], fg) for entry in self._docked_browsers.values()):
            return True
        if self._active_service in self._web_views:
            v = self._web_views[self._active_service]
            return v.hasFocus() or v.underMouse()
        return False

    def _target_logical_bounds(self) -> tuple[int, int, int, int] | None:
        win = self.window()
        if win is None or not win.isVisible():
            return None
        tl = self.pane_stack.mapToGlobal(QPoint(0, 0))
        return (
            int(tl.x()) + 2,
            int(tl.y()) + 2,
            max(420, int(self.pane_stack.width()) - 4),
            max(300, int(self.pane_stack.height()) - 4),
        )

    def _target_physical_rect(self) -> tuple[int, int, int, int] | None:
        win = self.window()
        if win is None or not win.isVisible() or win.isMinimized():
            return None
        wr = get_window_rect(int(win.winId()))
        if wr is None:
            return None
        dpr = max(1.0, float(win.devicePixelRatioF()))
        rel = self.pane_stack.mapTo(win, QPoint(0, 0))
        px = int(round(wr[0] + (rel.x() + 2) * dpr))
        py = int(round(wr[1] + (rel.y() + 2) * dpr))
        pw = max(320, int(round((self.pane_stack.width() - 4) * dpr)))
        ph = max(240, int(round((self.pane_stack.height() - 4) * dpr)))
        return px, py, pw, ph

    def _hide_all_docked_hwnds(self, *, except_sid: str = "") -> None:
        for sid, entry in list(self._docked_browsers.items()):
            if sid == except_sid:
                continue
            hwnd, clipper = entry[0], entry[1]
            if is_window_alive(hwnd):
                hide_browser_hwnd(hwnd, clipper)
            else:
                self._docked_browsers.pop(sid, None)

    def _set_portal_live(self, active: bool) -> None:
        flag = bool(active)
        if flag == self._portal_live:
            return
        self._portal_live = flag
        if flag:
            self._dock_card.setStyleSheet("QFrame { background: transparent; border: none; }")
            self.pane_stack.setStyleSheet("QStackedWidget { background: transparent; }")
            self._ext_title_lbl.hide()
            self._ext_desc_lbl.hide()
            self._ext_launch_btn.hide()
        else:
            self._dock_card.setStyleSheet("")
            self.pane_stack.setStyleSheet("")
            self._ext_title_lbl.show()
            self._ext_desc_lbl.show()
            self._ext_launch_btn.show()
        win = self.window()
        if win is not None:
            win.update()

    def portal_local_rect(self, ancestor: QWidget) -> QRect | None:
        """Return viewport rect mapped to ancestor when docked native browser is active so IslandWindow cuts an alpha=0 click-through portal."""
        if not self._portal_live or not self.isVisible() or self.pane_stack.currentWidget() is not self._dock_card:
            return None
        tl = self.pane_stack.mapTo(ancestor, QPoint(0, 0))
        return QRect(tl.x() + 2, tl.y() + 2, max(120, self.pane_stack.width() - 4), max(120, self.pane_stack.height() - 4))

    def _sync_active_docked_hwnd(self) -> None:
        if not _native_dock_ready_in_process():
            self._set_portal_live(False)
            return
        sid = self._active_service
        self._hide_all_docked_hwnds(except_sid=sid)
        entry = self._docked_browsers.get(sid)
        if entry is None:
            self._set_portal_live(False)
            return
        hwnd = entry[0]
        if not is_window_alive(hwnd):
            close_browser_hwnd(hwnd)
            self._docked_browsers.pop(sid, None)
            self._set_portal_live(False)
            self._refresh_dock_card_text(launching=False)
            return

        win = self.window()
        anim_running = bool(
            win is not None
            and hasattr(win, "_anim")
            and win._anim.state() == QPropertyAnimation.State.Running
        )
        should_show = (
            self.isVisible()
            and self.pane_stack.currentWidget() is self._dock_card
            and win is not None
            and win.isVisible()
            and not win.isMinimized()
            and not anim_running
        )
        if not should_show:
            hide_browser_hwnd(hwnd)
            self._last_synced_rect = None
            self._set_portal_live(False)
            return

        rect = self._target_physical_rect()
        if rect is None:
            hide_browser_hwnd(hwnd)
            self._set_portal_live(False)
            return

        self._set_portal_live(True)
        dpr = max(1.0, float(win.devicePixelRatioF()))
        crop_top = max(28, int(round(30.0 * dpr)))
        side_pad = max(6, int(round(7.0 * dpr)))
        want = (rect[0] - side_pad, rect[1] - crop_top, rect[2] + 2 * side_pad, rect[3] + crop_top + side_pad)
        cur_r = get_window_rect(hwnd)
        island_hwnd = int(win.winId())
        occluded = (not is_window_visible(hwnd)) or is_window_below_normal_sibling(hwnd, island_hwnd)
        if rect != self._last_synced_rect or cur_r != want or occluded:
            self._last_synced_rect = rect
            sync_docked_browser_hwnd(
                hwnd,
                *rect,
                dpr=dpr,
                visible=True,
                topmost=False,
                insert_after_hwnd=island_hwnd,
            )

    def _start_dock_launch(self, service_id: str, chosen: BrowserInfo) -> None:
        url = _PROVIDER_URL_MAP.get(service_id, "https://gemini.google.com/app")
        exe_name = Path(chosen.exe_path).name
        prev_hwnds = enum_chromium_hwnds(exe_name)
        bounds = self._target_logical_bounds()
        ok, _used = launch_in_browser(
            url,
            mode=chosen.browser_id,
            app_mode=True,
            browsers=self._browsers,
            logical_bounds=bounds,
        )
        if ok:
            self._pending_capture = (service_id, chosen, prev_hwnds, time.monotonic() + 5.5)
            self._refresh_dock_card_text(launching=True)
            if not self._dock_poll_timer.isActive():
                self._dock_poll_timer.start()

    def _poll_pending_browser_hwnd(self) -> None:
        pend = self._pending_capture
        if pend is None:
            self._dock_poll_timer.stop()
            return
        sid, chosen, prev_hwnds, deadline = pend
        exe_name = Path(chosen.exe_path).name
        now_hwnds = enum_chromium_hwnds(exe_name)
        existing_docked = {e[0] for e in self._docked_browsers.values()}
        candidates = [
            (h, meta)
            for h, meta in now_hwnds.items()
            if h not in prev_hwnds and h not in existing_docked and (meta[1] or time.monotonic() > deadline - 3.5)
        ]
        if candidates:
            # Prefer window with non-empty title
            candidates.sort(key=lambda item: (0 if item[1][1] else 1))
            hwnd = candidates[0][0]
            self._pending_capture = None
            self._dock_poll_timer.stop()
            saved = configure_docked_browser_hwnd(hwnd)
            if saved is not None:
                orig_style, orig_ex = saved
                self._docked_browsers[sid] = (hwnd, 0, chosen.browser_id, orig_style, orig_ex)
                self._last_synced_rect = None
                self._sync_active_docked_hwnd()
                self._refresh_dock_card_text(launching=False)
                return

        if time.monotonic() >= deadline:
            self._pending_capture = None
            self._dock_poll_timer.stop()
            self._refresh_dock_card_text(launching=False)

    def _relaunch_active_browser(self) -> None:
        sid = self._active_service
        entry = self._docked_browsers.pop(sid, None)
        if entry is not None:
            close_browser_hwnd(entry[0], entry[1])
        chosen = self._resolved_browser()
        if chosen is None or not chosen.available:
            return
        if chosen.supports_app_mode and _native_dock_ready_in_process():
            self._last_synced_rect = None
            self._start_dock_launch(sid, chosen)
        else:
            url = _PROVIDER_URL_MAP.get(sid, "https://gemini.google.com/app")
            ok, _ = launch_in_browser(url, mode=chosen.browser_id, app_mode=False, browsers=self._browsers)
            if not ok:
                QDesktopServices.openUrl(QUrl(url))

    def select_service(self, service_id: str) -> None:
        if service_id not in _PROVIDER_URL_MAP:
            service_id = "gemini"
        self._active_service = service_id
        for sid, btn in self._service_btns.items():
            btn.setChecked(sid == service_id)

        chosen = self._resolved_browser()
        use_native = chosen is not None and chosen.available

        if use_native:
            self.pane_stack.setCurrentWidget(self._dock_card)
            self.web_back_btn.setVisible(bool(chosen.supports_app_mode))
            self.web_reload_btn.setVisible(bool(chosen.supports_app_mode))
            self.popout_btn.setVisible(bool(chosen.supports_app_mode))

            entry = self._docked_browsers.get(service_id)
            if entry is not None and (not is_window_alive(entry[0]) or entry[2] != chosen.browser_id):
                self._docked_browsers.pop(service_id, None)
                close_browser_hwnd(entry[0], entry[1])
                entry = None

            self._hide_all_docked_hwnds(except_sid=service_id)
            self._refresh_dock_card_text(launching=(entry is None and chosen.supports_app_mode and _native_dock_ready_in_process() and self.isVisible()))

            if chosen.supports_app_mode and _native_dock_ready_in_process() and self.isVisible():
                if entry is not None:
                    self._last_synced_rect = None
                    self._sync_active_docked_hwnd()
                else:
                    if not (self._pending_capture is not None and self._pending_capture[0] == service_id):
                        self._start_dock_launch(service_id, chosen)
                if not self._dock_sync_timer.isActive():
                    self._dock_sync_timer.start()
        else:
            # Emergency fallback if zero desktop browsers are present on OS
            self._hide_all_docked_hwnds()
            self._refresh_dock_card_text(launching=False)
            self.pane_stack.setCurrentWidget(self._dock_card)
            self.web_back_btn.setVisible(False)
            self.web_reload_btn.setVisible(False)
            self.popout_btn.setVisible(False)

        self.tall_mode_changed.emit(self.wants_tall_island())

    def _web_back(self) -> None:
        entry = self._docked_browsers.get(self._active_service)
        if entry is not None and is_window_alive(entry[0]):
            send_browser_nav_key(entry[0], "back")

    def _web_reload(self) -> None:
        entry = self._docked_browsers.get(self._active_service)
        if entry is not None and is_window_alive(entry[0]):
            send_browser_nav_key(entry[0], "reload")
        else:
            self._relaunch_active_browser()

    def _popout_active(self) -> None:
        sid = self._active_service
        entry = self._docked_browsers.pop(sid, None)
        if entry is not None and is_window_alive(entry[0]):
            hwnd, clipper, _bid, orig_st, orig_ex = entry
            self._set_portal_live(False)
            restore_browser_hwnd(hwnd, orig_st, orig_ex, clipper)
            self._refresh_dock_card_text(launching=False)
            return
        self._relaunch_active_browser()

    def refresh_header(self) -> None:
        pass

    def ask_prompt(self, text: str) -> None:
        self.select_service("gemini")

    def attach_file(self, path: str) -> None:
        pass

    def closeEvent(self, event) -> None:  # noqa: N802
        self._dock_poll_timer.stop()
        self._dock_sync_timer.stop()
        for entry in list(self._docked_browsers.values()):
            close_browser_hwnd(entry[0], entry[1])
        self._docked_browsers.clear()
        super().closeEvent(event)
