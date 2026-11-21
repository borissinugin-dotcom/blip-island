"""Floating Command Bar (Alt+Space) spotlight launcher, calculator, currency/unit converter, and shell."""

from __future__ import annotations

import os
import threading
import urllib.parse

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QKeyEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..services.command_service import CommandService, IndexedItem
from .icons import get_icon
from .theme import ThemePalette, build_panel_qss


class CommandBarWindow(QWidget):
    """Spotlight-style quick command bar centered at top of active monitor."""

    timer_requested = pyqtSignal(int, str)
    reminder_requested = pyqtSignal(str)
    system_command_requested = pyqtSignal(str)
    shell_output_ready = pyqtSignal(str)

    def __init__(self, command_service: CommandService, palette: ThemePalette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.command_service = command_service
        self.palette = palette
        self._items: list[IndexedItem] = []

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(620)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Apps, =math, 100 usd to rub, >powershell, timer 10m, remind...")
        self.input_edit.setFixedHeight(38)
        self.input_edit.setStyleSheet("font-size: 14px; font-weight: 500; padding: 6px 14px; border-radius: 19px;")
        self.input_edit.textChanged.connect(self._on_text_changed)
        self.input_edit.installEventFilter(self)
        root.addWidget(self.input_edit)

        self.list_widget = QListWidget()
        self.list_widget.setMaximumHeight(290)
        self.list_widget.itemActivated.connect(lambda _: self._execute_selected(ctrl=False, shift=False))
        root.addWidget(self.list_widget)

        self.shell_out = QPlainTextEdit()
        self.shell_out.setReadOnly(True)
        self.shell_out.setMaximumHeight(160)
        self.shell_out.hide()
        root.addWidget(self.shell_out)

        hint = QLabel("Enter: Run / Copy   •   Ctrl+Enter: Run as Admin   •   Esc: Close")
        hint.setObjectName("Muted")
        root.addWidget(hint)

        self.shell_output_ready.connect(self._show_shell_output)
        self.apply_palette(palette)

    def apply_palette(self, palette: ThemePalette) -> None:
        self.palette = palette
        self.setStyleSheet(build_panel_qss(palette))

    def open_bar(self) -> None:
        scr = QGuiApplication.primaryScreen()
        geo = scr.geometry() if scr else None
        if geo:
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + 140)
        self.shell_out.hide()
        self.input_edit.clear()
        self._refresh_results("")
        self.adjustSize()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_edit.setFocus()
        threading.Thread(target=self.command_service.ensure_indexed, daemon=True).start()

    def _on_text_changed(self, txt: str) -> None:
        self.shell_out.hide()
        self._refresh_results(txt)

    def _refresh_results(self, query: str) -> None:
        self._items = self.command_service.search(query)
        self.list_widget.clear()
        icon_map = {
            "app": "rocket", "uwp": "app_icon", "file": "folder", "calc": "tools", "unit": "swap",
            "currency": "stats", "shell": "command", "system": "controls", "timer": "timer",
            "reminder": "bell", "web": "globe",
        }
        fg = self.palette.text_primary
        acc = self.palette.accent
        for idx, it in enumerate(self._items):
            li = QListWidgetItem(f"{it.title}   —   {it.subtitle}")
            li.setIcon(get_icon(icon_map.get(it.kind, "command"), size=16, color=fg, accent=acc))
            li.setData(Qt.ItemDataRole.UserRole, idx)
            self.list_widget.addItem(li)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.adjustSize()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.input_edit and isinstance(event, QKeyEvent) and event.type() == QKeyEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
            if key == Qt.Key.Key_Down:
                row = min(self.list_widget.count() - 1, self.list_widget.currentRow() + 1)
                self.list_widget.setCurrentRow(row)
                return True
            if key == Qt.Key.Key_Up:
                row = max(0, self.list_widget.currentRow() - 1)
                self.list_widget.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
                shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
                self._execute_selected(ctrl=ctrl, shift=shift)
                return True
        return super().eventFilter(obj, event)

    def _execute_selected(self, ctrl: bool, shift: bool) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._items)):
            return
        it = self._items[row]

        if it.kind in ("calc", "unit", "currency"):
            cb = QGuiApplication.clipboard()
            if cb and it.target:
                cb.setText(it.target)
            self.hide()
        elif it.kind in ("app", "uwp", "file"):
            self.command_service.launch_target(it.target, run_as_admin=ctrl)
            self.hide()
        elif it.kind == "shell":
            if it.target:
                self.shell_out.setPlainText("Running...")
                self.shell_out.show()
                self.adjustSize()
                threading.Thread(
                    target=lambda cmd=it.target: self.shell_output_ready.emit(self.command_service.run_shell_command(cmd)),
                    daemon=True,
                ).start()
        elif it.kind == "timer":
            self.timer_requested.emit(int(it.target), it.title)
            self.hide()
        elif it.kind == "reminder":
            self.reminder_requested.emit(it.target)
            self.hide()
        elif it.kind == "system":
            self.system_command_requested.emit(it.target)
            self.hide()
        elif it.kind == "web":
            os.startfile(f"https://www.google.com/search?q={urllib.parse.quote(it.target)}")  # type: ignore[attr-defined]
            self.hide()

    def _show_shell_output(self, out: str) -> None:
        self.shell_out.setPlainText(out)
        self.shell_out.show()
        self.adjustSize()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(self.palette.accent), 1.5))
        p.setBrush(QColor(self.palette.bg_island))
        p.drawRoundedRect(r, 18, 18)
        p.end()
