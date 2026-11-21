"""File Shelf tab: drag-and-drop staging zone supporting single & multi-file drag-out."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QMimeData, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.i18n import tr
from ...core.storage import LocalStorage, ShelfItem
from ...core.win32_api import reveal_in_explorer
from ..icons import get_icon


class _DraggableShelfList(QListWidget):
    files_dropped = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def startDrag(self, supportedActions) -> None:  # noqa: N802
        selected = self.selectedItems()
        if not selected:
            return
        urls = [QUrl.fromLocalFile(it.data(Qt.ItemDataRole.UserRole + 1)) for it in selected if it.data(Qt.ItemDataRole.UserRole + 1)]
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 ** 2):.1f} MB"


class ShelfTab(QWidget):
    """Temporary file shelf for dropping files in and dragging them out individually or all at once."""

    send_to_chat = pyqtSignal(str)  # file_path

    def __init__(self, storage: LocalStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self._items: dict[int, ShelfItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        hint = QLabel(tr("shelf_hint"))
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = _DraggableShelfList()
        self.list_widget.files_dropped.connect(self.add_files)
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add File")
        add_btn.setMinimumWidth(0)
        add_btn.clicked.connect(self._browse_add)
        drag_all_btn = QPushButton(tr("drag_all"))
        drag_all_btn.setIcon(get_icon("shelf", 14, "#F8FAFC", "#93C5FD"))
        drag_all_btn.setMinimumWidth(0)
        drag_all_btn.setObjectName("Primary")
        drag_all_btn.pressed.connect(self._start_drag_all)
        clear_btn = QPushButton(tr("clear_shelf"))
        clear_btn.setMinimumWidth(0)
        clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(drag_all_btn, 1)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

        self.refresh()

    def add_files(self, paths: list[str]) -> None:
        for p in paths:
            self.storage.add_shelf_file(p)
        self.refresh()

    def refresh(self) -> None:
        self._items.clear()
        self.list_widget.clear()
        for item in self.storage.list_shelf_items():
            self._items[item.id] = item
            exists = Path(item.file_path).exists()
            li = QListWidgetItem(f"{item.display_name}  ({_fmt_size(item.size_bytes)})")
            li.setIcon(get_icon("folder" if exists else "warning", 14, "#F2F5FA", "#60A5FA" if exists else "#F59E0B"))
            li.setToolTip(item.file_path)
            li.setData(Qt.ItemDataRole.UserRole, item.id)
            li.setData(Qt.ItemDataRole.UserRole + 1, item.file_path)
            self.list_widget.addItem(li)

    def _browse_add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Files to Shelf")
        if paths:
            self.add_files(paths)

    def _start_drag_all(self) -> None:
        urls = [QUrl.fromLocalFile(it.file_path) for it in self._items.values() if Path(it.file_path).exists()]
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _clear_all(self) -> None:
        self.storage.clear_shelf()
        self.refresh()

    def _open_item(self, item: QListWidgetItem) -> None:
        fp = item.data(Qt.ItemDataRole.UserRole + 1)
        if fp and Path(fp).exists():
            os.startfile(fp)  # type: ignore[attr-defined]

    def _context_menu(self, pos) -> None:
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        si = self._items.get(item.data(Qt.ItemDataRole.UserRole))
        if not si:
            return
        menu = QMenu(self)
        act_open = menu.addAction(tr("open"))
        act_folder = menu.addAction(tr("show_in_folder"))
        act_text = menu.addAction("→ " + tr("tab_text"))
        act_del = menu.addAction("Удалить")
        chosen = menu.exec(self.list_widget.mapToGlobal(pos))
        if chosen == act_open and Path(si.file_path).exists():
            os.startfile(si.file_path)  # type: ignore[attr-defined]
        elif chosen == act_folder:
            reveal_in_explorer(si.file_path)
        elif chosen == act_text:
            self.send_to_chat.emit(si.file_path)
        elif chosen == act_del:
            self.storage.remove_shelf_item(si.id)
            self.refresh()
