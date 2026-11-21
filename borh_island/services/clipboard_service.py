"""System clipboard monitor, password-manager exclusion, history storage, and paste-to-prior-window."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QMimeData, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QClipboard, QGuiApplication, QImage

from ..core.config import ConfigManager
from ..core.storage import ClipboardEntry, LocalStorage
from ..core.win32_api import (
    get_foreground_window_info,
    is_clipboard_marked_private,
    paste_to_window,
    send_ctrl_v,
    send_unicode_text,
    user32,
)

# Built-in password managers excluded by default for security
PASSWORD_MANAGER_EXES = {
    "1password.exe",
    "bitwarden.exe",
    "keepass.exe",
    "keepassxc.exe",
    "lastpass.exe",
    "dashlane.exe",
    "enpass.exe",
    "roboform.exe",
    "nordpass.exe",
    "protonpass.exe",
}


class ClipboardService(QObject):
    """Monitors Windows clipboard, skips sensitive entries, and restores items into prior app."""

    history_changed = pyqtSignal()
    item_captured = pyqtSignal(str, str, str)  # (kind: text|image, preview_or_image_path, source_app)

    def __init__(
        self,
        storage: LocalStorage,
        config_mgr: ConfigManager,
        images_dir: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.config_mgr = config_mgr
        self.images_dir = Path(images_dir)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._own_pid = os.getpid()
        self._last_external_hwnd: int = 0
        self._last_external_exe: str = ""
        self._last_seen_hwnd: int = 0
        self._suppress_next_capture = False
        self._clipboard: QClipboard | None = QGuiApplication.clipboard()
        if self._clipboard is not None:
            self._clipboard.dataChanged.connect(self._on_clipboard_changed)

        # Periodically track the active external window so click-to-paste targets the right app
        self._fg_timer = QTimer(self)
        self._fg_timer.setInterval(600)
        self._fg_timer.timeout.connect(self._poll_foreground)

    def start(self) -> None:
        self._fg_timer.start()

    def stop(self) -> None:
        self._fg_timer.stop()

    @property
    def last_external_hwnd(self) -> int:
        return self._last_external_hwnd

    def _poll_foreground(self) -> None:
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd or hwnd == self._last_seen_hwnd:
            return
        self._last_seen_hwnd = hwnd
        info = get_foreground_window_info()
        if info and info.pid != self._own_pid and info.hwnd:
            self._last_external_hwnd = info.hwnd
            self._last_external_exe = info.exe_name

    def _on_clipboard_changed(self) -> None:
        if self._suppress_next_capture or not self.config_mgr.config.clipboard_enabled:
            self._suppress_next_capture = False
            return
        if is_clipboard_marked_private():
            return

        fg = get_foreground_window_info()
        source_app = fg.exe_name if fg else self._last_external_exe
        low_app = (source_app or "").lower()
        blacklist = {x.strip().lower() for x in self.config_mgr.config.clipboard_excluded_apps}
        if low_app and (low_app in PASSWORD_MANAGER_EXES or low_app in blacklist):
            return

        if self._clipboard is None:
            return
        mime = self._clipboard.mimeData()
        if mime is None:
            return

        if mime.hasImage():
            img = self._clipboard.image()
            if not img.isNull():
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                img.save(buf, "PNG")
                raw_bytes = bytes(buf.data())
                digest = hashlib.sha256(raw_bytes).hexdigest()
                out_path = self.images_dir / f"{digest[:16]}.png"
                if not out_path.exists():
                    out_path.write_bytes(raw_bytes)
                self.storage.add_clipboard_entry(
                    kind="image",
                    text_content="",
                    image_path=str(out_path),
                    preview_text=f"Image {img.width}x{img.height}",
                    source_app=source_app,
                    content_hash=digest,
                    max_items=self.config_mgr.config.clipboard_max_items,
                )
                self.history_changed.emit()
                self.item_captured.emit("image", str(out_path), source_app)
                return

        if mime.hasText():
            text = mime.text()
            if not text or not text.strip():
                return
            digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
            prev = " ".join(text.split())[:120]
            self.storage.add_clipboard_entry(
                kind="text",
                text_content=text,
                image_path="",
                preview_text=prev,
                source_app=source_app,
                content_hash=digest,
                max_items=self.config_mgr.config.clipboard_max_items,
            )
            self.history_changed.emit()
            self.item_captured.emit("text", prev, source_app)

    def copy_entry_to_clipboard(self, entry: ClipboardEntry, plain_only: bool = False) -> None:
        """Put entry onto system clipboard without duplicating it in history."""
        if self._clipboard is None:
            return
        self._suppress_next_capture = True
        if entry.kind == "image" and entry.image_path and Path(entry.image_path).exists() and not plain_only:
            qimg = QImage(entry.image_path)
            if not qimg.isNull():
                self._clipboard.setImage(qimg)
                return
        self._clipboard.setText(entry.text_content)

    def paste_entry(self, entry: ClipboardEntry, plain_only: bool = False) -> None:
        """Copy entry and send Ctrl+V to last active external window."""
        self.copy_entry_to_clipboard(entry, plain_only=plain_only)
        hwnd = self._last_external_hwnd
        threading.Thread(target=lambda: paste_to_window(hwnd), daemon=True).start()

    def paste_plain_current_clipboard(self) -> None:
        """Strip formatting from current clipboard text and paste into active app."""
        if self._clipboard is None:
            return
        txt = self._clipboard.text()
        if not txt:
            return
        self._suppress_next_capture = True
        mime = QMimeData()
        mime.setText(txt)
        self._clipboard.setMimeData(mime)
        hwnd = self._last_external_hwnd

        def _do() -> None:
            time.sleep(0.05)
            if hwnd:
                paste_to_window(hwnd)
            else:
                send_ctrl_v()

        threading.Thread(target=_do, daemon=True).start()

    def insert_text_into_active_app(self, text: str) -> None:
        """Copy and paste text (or send Unicode keystrokes) into last external window."""
        if not text or self._clipboard is None:
            return
        self._suppress_next_capture = True
        self._clipboard.setText(text)
        hwnd = self._last_external_hwnd
        threading.Thread(target=lambda: paste_to_window(hwnd) if hwnd else send_unicode_text(text), daemon=True).start()
