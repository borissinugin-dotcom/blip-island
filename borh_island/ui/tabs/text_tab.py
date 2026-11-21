"""Text Lab tab: offline case/translit/layout/JSON/URL/Base64/regex transformations and QR generator."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.config import ConfigManager
from ...core.i18n import tr
from ...services import text_tools
from ...services.clipboard_service import ClipboardService
from ..icons import get_icon


class TextTab(QWidget):
    """Offline text transformations and QR code generator."""

    def __init__(
        self,
        ai_service: object | None,
        config_mgr: ConfigManager,
        clipboard_service: ClipboardService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.clipboard_service = clipboard_service

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        scroll.setWidget(host)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 6, 6, 8)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(5)
        from_clip = QPushButton("← Буфер")
        from_clip.setIcon(get_icon("clipboard", 13, "#F2F5FA", "#60A5FA"))
        from_clip.setMinimumWidth(0)
        from_clip.clicked.connect(self._load_clipboard)
        swap_btn = QPushButton("⇅")
        swap_btn.setFixedSize(28, 26)
        swap_btn.setStyleSheet("min-width: 28px; max-width: 28px; min-height: 26px; max-height: 26px; padding: 0px;")
        swap_btn.clicked.connect(self._swap)
        copy_out = QPushButton("Копировать")
        copy_out.setIcon(get_icon("copy", 13, "#F2F5FA", "#60A5FA"))
        copy_out.setMinimumWidth(0)
        copy_out.clicked.connect(self._copy_output)
        paste_out = QPushButton("Вставить")
        paste_out.setIcon(get_icon("paste", 13, "#F2F5FA", "#34D399"))
        paste_out.setObjectName("Primary")
        paste_out.setMinimumWidth(0)
        paste_out.clicked.connect(self._paste_output)
        bar.addWidget(from_clip)
        bar.addWidget(swap_btn)
        bar.addStretch(1)
        bar.addWidget(copy_out)
        bar.addWidget(paste_out)
        layout.addLayout(bar)

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("Вставьте или введите текст для преобразования и генерации QR…")
        self.input_edit.setMaximumHeight(96)
        self.input_edit.textChanged.connect(self._update_stats)
        layout.addWidget(self.input_edit)

        self.stats_lbl = QLabel("0 символов • 0 слов • 0 строк")
        self.stats_lbl.setObjectName("Muted")
        self.stats_lbl.setStyleSheet("font-size: 10px;")
        layout.addWidget(self.stats_lbl)

        self.output_edit = QTextEdit()
        self.output_edit.setPlaceholderText("Результат появится здесь…")
        self.output_edit.setMaximumHeight(96)
        layout.addWidget(self.output_edit)

        ops: list[tuple[str, object]] = [
            (tr("layout_fix"), text_tools.fix_keyboard_layout),
            (tr("upper"), lambda s: s.upper()),
            (tr("lower"), lambda s: s.lower()),
            (tr("title_case"), lambda s: s.title()),
            (tr("translit"), text_tools.transliterate),
            (tr("json_fmt"), text_tools.format_json),
            (tr("json_min"), text_tools.minify_json),
            (tr("b64_enc"), text_tools.base64_encode),
            (tr("b64_dec"), text_tools.base64_decode),
            (tr("url_enc"), text_tools.url_encode),
            (tr("url_dec"), text_tools.url_decode),
        ]
        grid = QGridLayout()
        grid.setSpacing(5)
        for i, (label, fn) in enumerate(ops):
            b = QPushButton(label)
            b.setMinimumWidth(0)
            b.setStyleSheet("padding: 4px 6px; font-size: 11px;")
            b.clicked.connect(lambda _, f=fn: self._apply_op(f))
            grid.addWidget(b, i // 4, i % 4)
        qr_btn = QPushButton(tr("make_qr"))
        qr_btn.setIcon(get_icon("qr", 13, "#F2F5FA", "#60A5FA"))
        qr_btn.setMinimumWidth(0)
        qr_btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
        qr_btn.clicked.connect(self._show_qr)
        grid.addWidget(qr_btn, len(ops) // 4, len(ops) % 4)
        layout.addLayout(grid)

    def _update_stats(self) -> None:
        st = text_tools.text_stats(self.input_edit.toPlainText())
        self.stats_lbl.setText(f"{st['chars']} символов ({st['chars_no_spaces']} без проб.) • {st['words']} слов • {st['lines']} строк")

    def _load_clipboard(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb:
            self.input_edit.setPlainText(cb.text())

    def _swap(self) -> None:
        a, b = self.input_edit.toPlainText(), self.output_edit.toPlainText()
        if b:
            self.input_edit.setPlainText(b)
            self.output_edit.setPlainText(a)

    def _copy_output(self) -> None:
        txt = self.output_edit.toPlainText() or self.input_edit.toPlainText()
        cb = QGuiApplication.clipboard()
        if cb and txt:
            cb.setText(txt)

    def _paste_output(self) -> None:
        txt = self.output_edit.toPlainText() or self.input_edit.toPlainText()
        if txt:
            self.clipboard_service.insert_text_into_active_app(txt)

    def _apply_op(self, fn) -> None:
        src = self.input_edit.toPlainText()
        if not src:
            return
        self.output_edit.setPlainText(str(fn(src)))

    def _show_qr(self) -> None:
        txt = self.output_edit.toPlainText().strip() or self.input_edit.toPlainText().strip()
        if not txt:
            return
        png_bytes = text_tools.make_qr_png(txt)
        qimg = QImage.fromData(png_bytes, "PNG")
        dlg = QDialog(self)
        dlg.setWindowTitle("QR-код")
        vl = QVBoxLayout(dlg)
        lbl = QLabel()
        lbl.setPixmap(QPixmap.fromImage(qimg).scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(lbl)
        row = QHBoxLayout()
        cp_btn = QPushButton("Копировать QR")
        cp_btn.clicked.connect(lambda: QGuiApplication.clipboard().setImage(qimg))  # type: ignore[union-attr]
        save_png = QPushButton("Сохранить PNG")
        save_svg = QPushButton("Сохранить SVG")

        def _save_png() -> None:
            p, _ = QFileDialog.getSaveFileName(dlg, "Сохранить QR PNG", "qrcode.png", "PNG (*.png)")
            if p:
                Path(p).write_bytes(png_bytes)

        def _save_svg() -> None:
            p, _ = QFileDialog.getSaveFileName(dlg, "Сохранить QR SVG", "qrcode.svg", "SVG (*.svg)")
            if p:
                Path(p).write_text(text_tools.make_qr_svg(txt), encoding="utf-8")

        save_png.clicked.connect(_save_png)
        save_svg.clicked.connect(_save_svg)
        row.addWidget(cp_btn)
        row.addWidget(save_png)
        row.addWidget(save_svg)
        vl.addLayout(row)
        dlg.exec()
