"""Clipboard history tab with instant search, pins, image thumbnails, and paste-into-prior-window."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.i18n import tr
from ...core.storage import ClipboardEntry, LocalStorage
from ...services.clipboard_service import ClipboardService


class ClipboardTab(QWidget):
    """Searchable SQLite clipboard history tab."""

    send_to_text = pyqtSignal(str)
    send_to_chat = pyqtSignal(str, bool)  # (text_or_path, is_image)

    def __init__(self, clipboard_service: ClipboardService, storage: LocalStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.clipboard_service = clipboard_service
        self.storage = storage
        self._entries: dict[int, ClipboardEntry] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search_placeholder"))
        self.search_edit.textChanged.connect(self.refresh)
        self.clear_btn = QPushButton(tr("clear"))
        self.clear_btn.clicked.connect(self._clear_unpinned)
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.clear_btn)
        layout.addLayout(top)

        self.list_widget = QListWidget()
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._open_context_menu)
        self.list_widget.itemDoubleClicked.connect(self._on_paste_item)
        self.list_widget.itemActivated.connect(self._on_paste_item)
        layout.addWidget(self.list_widget, 1)

        hint = QLabel("Enter / Двойной клик: вставить в окно • ПКМ: Закрепить / Обычный текст / В Текст")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setMinimumWidth(0)
        layout.addWidget(hint)

        self.clipboard_service.history_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        query = self.search_edit.text().strip()
        items = self.storage.list_clipboard(query=query, limit=150)
        self._entries.clear()
        self.list_widget.clear()
        for entry in items:
            self._entries[entry.id] = entry
            pin_prefix = "[*] " if entry.pinned else ""
            src = f"[{entry.source_app}] " if entry.source_app else ""
            if entry.kind == "image" and entry.image_path:
                label = f"{pin_prefix}{src}Image ({Path(entry.image_path).name})"
                li = QListWidgetItem(label)
                if Path(entry.image_path).exists():
                    li.setIcon(QIcon(QPixmap(entry.image_path).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio)))
            else:
                preview = " ".join(entry.text_content.split())[:95]
                li = QListWidgetItem(f"{pin_prefix}{src}{preview}")
                li.setToolTip(entry.text_content[:800])
            li.setData(Qt.ItemDataRole.UserRole, entry.id)
            self.list_widget.addItem(li)

    def _on_paste_item(self, item: QListWidgetItem) -> None:
        eid = item.data(Qt.ItemDataRole.UserRole)
        entry = self._entries.get(eid)
        if entry:
            self.clipboard_service.paste_entry(entry)

    def _clear_unpinned(self) -> None:
        self.storage.clear_unpinned_clipboard()
        self.refresh()

    def _open_context_menu(self, pos) -> None:
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        entry = self._entries.get(item.data(Qt.ItemDataRole.UserRole))
        if not entry:
            return
        menu = QMenu(self)
        act_paste = menu.addAction(tr("paste"))
        act_plain = menu.addAction("Paste Plain Text")
        act_pin = menu.addAction(tr("unpin") if entry.pinned else tr("pin"))
        act_txt = menu.addAction("→ " + tr("tab_text")) if entry.kind == "text" else None
        act_del = menu.addAction("Удалить")

        chosen = menu.exec(self.list_widget.mapToGlobal(pos))
        if chosen == act_paste:
            self.clipboard_service.paste_entry(entry)
        elif chosen == act_plain:
            self.clipboard_service.paste_entry(entry, plain_only=True)
        elif chosen == act_pin:
            self.storage.toggle_clipboard_pin(entry.id)
            self.refresh()
        elif act_txt and chosen == act_txt:
            self.send_to_text.emit(entry.text_content)
        elif chosen == act_del:
            self.storage.delete_clipboard_entry(entry.id)
            self.refresh()
