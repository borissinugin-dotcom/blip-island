"""Notes, Timer, and Natural-Language Reminders tab."""

from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.i18n import tr
from ...core.storage import LocalStorage, NoteItem, ReminderItem
from ...services.reminder_parser import next_occurrence, parse_duration_seconds, parse_reminder_phrase
from ..icons import get_icon, get_pixmap


class NotesTab(QWidget):
    """Auto-saved folders rich-text notes + task checkboxes + countdown timer + scheduled reminders."""

    timer_updated = pyqtSignal(int, bool, str)  # (remaining_seconds, running, label)
    timer_finished = pyqtSignal(str)
    reminder_triggered = pyqtSignal(object)  # ReminderItem

    def __init__(self, storage: LocalStorage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self._current_note: NoteItem | None = None
        self._notes_map: dict[int, NoteItem] = {}
        self._rem_map: dict[int, ReminderItem] = {}

        self._timer_total = 0
        self._timer_remaining = 0
        self._timer_running = False
        self._timer_label = tr("timer")

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(8)

        big_pill_qss = (
            "QPushButton { min-height: 36px; max-height: 36px; border-radius: 18px; "
            "padding: 0px 16px; font-size: 12px; font-weight: 600; }"
        )
        big_primary_qss = (
            "QPushButton { min-height: 36px; max-height: 36px; min-width: 92px; border-radius: 18px; "
            "padding: 0px 18px; font-size: 12px; font-weight: 700; }"
        )
        fmt_pill_qss = (
            "QPushButton { min-height: 34px; max-height: 34px; border-radius: 17px; "
            "padding: 0px 14px; font-size: 12px; font-weight: 600; }"
        )

        # --- Sub-switcher: Notes vs Timer & Reminders (Large Pill Tabs) ---
        sw_row = QHBoxLayout()
        sw_row.setSpacing(8)
        self.btn_mode_notes = QPushButton(tr("tab_notes"))
        self.btn_mode_notes.setIcon(get_icon("notes", 16, "#F2F5FA", "#60A5FA"))
        self.btn_mode_notes.setIconSize(self.btn_mode_notes.iconSize().__class__(16, 16))
        self.btn_mode_notes.setCheckable(True)
        self.btn_mode_notes.setChecked(True)
        self.btn_mode_notes.setFixedHeight(38)
        self.btn_mode_notes.setStyleSheet(
            "QPushButton { min-height: 36px; max-height: 36px; border-radius: 19px; "
            "padding: 0px 18px; font-size: 12px; font-weight: 600; }"
        )
        self.btn_mode_rem = QPushButton(f"{tr('timer')} · {tr('reminders')}")
        self.btn_mode_rem.setIcon(get_icon("timer", 16, "#F2F5FA", "#60A5FA"))
        self.btn_mode_rem.setIconSize(self.btn_mode_rem.iconSize().__class__(16, 16))
        self.btn_mode_rem.setCheckable(True)
        self.btn_mode_rem.setFixedHeight(38)
        self.btn_mode_rem.setStyleSheet(
            "QPushButton { min-height: 36px; max-height: 36px; border-radius: 19px; "
            "padding: 0px 18px; font-size: 12px; font-weight: 600; }"
        )
        self.btn_mode_notes.clicked.connect(lambda: self._switch_view(0))
        self.btn_mode_rem.clicked.connect(lambda: self._switch_view(1))
        sw_row.addWidget(self.btn_mode_notes, 1)
        sw_row.addWidget(self.btn_mode_rem, 1)
        root.addLayout(sw_row)

        # --- View 0: Notes (Atoll Card Layout with Large Controls) ---
        self.notes_host = QWidget()
        nh = QVBoxLayout(self.notes_host)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.setSpacing(8)

        bar_card = QFrame()
        bar_card.setObjectName("Card")
        bar = QHBoxLayout(bar_card)
        bar.setContentsMargins(10, 8, 10, 8)
        bar.setSpacing(8)
        self.folder_combo = QComboBox()
        self.folder_combo.setFixedHeight(36)
        self.folder_combo.currentIndexChanged.connect(self._reload_notes)
        new_note_btn = QPushButton("  Новая заметка")
        new_note_btn.setObjectName("Primary")
        new_note_btn.setIcon(get_icon("plus", 15, "#0B0D13", "#0B0D13"))
        new_note_btn.setFixedHeight(36)
        new_note_btn.setStyleSheet(big_primary_qss)
        new_note_btn.clicked.connect(self._new_note)
        del_note_btn = QPushButton("")
        del_note_btn.setIcon(get_icon("trash", 16, "#F8FAFC", "#F87171"))
        del_note_btn.setToolTip("Удалить заметку")
        del_note_btn.setFixedSize(36, 36)
        del_note_btn.setStyleSheet(
            "QPushButton { min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; "
            "padding: 0px; border-radius: 18px; }"
        )
        del_note_btn.clicked.connect(self._delete_current_note)
        bar.addWidget(self.folder_combo, 1)
        bar.addWidget(new_note_btn)
        bar.addWidget(del_note_btn)
        nh.addWidget(bar_card)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(8)

        list_card = QFrame()
        list_card.setObjectName("Card")
        list_card.setMinimumWidth(165)
        list_card.setMaximumWidth(215)
        lc_lay = QVBoxLayout(list_card)
        lc_lay.setContentsMargins(8, 8, 8, 8)
        lc_lay.setSpacing(6)
        list_hdr = QLabel("Список")
        list_hdr.setObjectName("Heading")
        lc_lay.addWidget(list_hdr)
        self.note_list = QListWidget()
        self.note_list.setStyleSheet("background: transparent; border: none;")
        self.note_list.currentItemChanged.connect(self._on_note_selected)
        lc_lay.addWidget(self.note_list, 1)

        ed_card = QFrame()
        ed_card.setObjectName("Card")
        ev = QVBoxLayout(ed_card)
        ev.setContentsMargins(10, 10, 10, 10)
        ev.setSpacing(8)

        self.title_edit = QLineEdit()
        self.title_edit.setFixedHeight(36)
        self.title_edit.setPlaceholderText("Заголовок заметки...")
        self.title_edit.setStyleSheet("font-size: 13px; font-weight: 600; border-radius: 14px; padding: 0px 12px;")
        self.title_edit.textEdited.connect(self._schedule_autosave)
        ev.addWidget(self.title_edit)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)
        b_btn = QPushButton("B")
        b_btn.setFixedSize(34, 34)
        b_btn.setStyleSheet(
            "QPushButton { min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; "
            "padding: 0px; border-radius: 17px; font-weight: 700; font-size: 13px; }"
        )
        b_btn.clicked.connect(self._toggle_bold)
        i_btn = QPushButton("I")
        i_btn.setFixedSize(34, 34)
        i_btn.setStyleSheet(
            "QPushButton { min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; "
            "padding: 0px; border-radius: 17px; font-style: italic; font-size: 13px; }"
        )
        i_btn.clicked.connect(self._toggle_italic)
        chk_btn = QPushButton(" Задача")
        chk_btn.setIcon(get_icon("plus", 14, "#F2F5FA", "#60A5FA"))
        chk_btn.setFixedHeight(34)
        chk_btn.setStyleSheet(fmt_pill_qss)
        chk_btn.clicked.connect(self._insert_checkbox)
        tog_btn = QPushButton(" Чекбокс")
        tog_btn.setIcon(get_icon("swap", 14, "#F2F5FA", "#60A5FA"))
        tog_btn.setFixedHeight(34)
        tog_btn.setStyleSheet(fmt_pill_qss)
        tog_btn.clicked.connect(self._toggle_line_checkbox)
        self.save_lbl = QLabel("Сохранено")
        self.save_lbl.setObjectName("Muted")
        fmt_row.addWidget(b_btn)
        fmt_row.addWidget(i_btn)
        fmt_row.addWidget(chk_btn)
        fmt_row.addWidget(tog_btn)
        fmt_row.addStretch(1)
        fmt_row.addWidget(self.save_lbl)
        ev.addLayout(fmt_row)

        self.editor = QTextEdit()
        self.editor.setStyleSheet("border-radius: 14px; padding: 8px; font-size: 13px;")
        self.editor.textChanged.connect(self._schedule_autosave)
        ev.addWidget(self.editor, 1)

        split.addWidget(list_card)
        split.addWidget(ed_card)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        nh.addWidget(split, 1)
        root.addWidget(self.notes_host, 1)

        # --- View 1: Timer + Reminders (Large Pill Controls) ---
        self.rem_host = QWidget()
        rv = QVBoxLayout(self.rem_host)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        timer_card = QFrame()
        timer_card.setObjectName("Card")
        tc = QVBoxLayout(timer_card)
        tc.setContentsMargins(12, 10, 12, 10)
        tc.setSpacing(8)

        th = QHBoxLayout()
        th.setSpacing(8)
        t_ico = QLabel()
        t_ico.setPixmap(get_pixmap("timer", 18, "#F2F5FA", "#60A5FA"))
        t_lbl = QLabel(tr("timer"))
        t_lbl.setObjectName("Heading")
        self.timer_display = QLabel("00:00")
        self.timer_display.setStyleSheet("font-size: 24px; font-weight: 700;")
        th.addWidget(t_ico)
        th.addWidget(t_lbl)
        th.addStretch(1)
        th.addWidget(self.timer_display)
        tc.addLayout(th)

        presets = QHBoxLayout()
        presets.setSpacing(8)
        for mins in (5, 10, 25, 45):
            pb = QPushButton(f"{mins} мин")
            pb.setFixedHeight(36)
            pb.setStyleSheet(big_pill_qss)
            pb.clicked.connect(lambda _, m=mins: self.start_timer(m * 60, f"{m}m Timer"))
            presets.addWidget(pb)
        tc.addLayout(presets)

        t_custom = QHBoxLayout()
        t_custom.setSpacing(8)
        self.timer_input = QLineEdit()
        self.timer_input.setFixedHeight(36)
        self.timer_input.setPlaceholderText("15m / 1h 30m / 90s")
        self.timer_input.setStyleSheet("border-radius: 14px; padding: 0px 12px; font-size: 12px;")
        self.timer_start_btn = QPushButton(tr("start"))
        self.timer_start_btn.setObjectName("Primary")
        self.timer_start_btn.setFixedHeight(36)
        self.timer_start_btn.setStyleSheet(big_primary_qss)
        from PyQt6.QtCore import QSize
        from ...core.licensing import get_license_manager
        if not get_license_manager().is_feature_allowed("timer"):
            self.timer_start_btn.setIcon(get_icon("lock", size=14, color="#fbbf24"))
            self.timer_start_btn.setIconSize(QSize(14, 14))
        self.timer_start_btn.clicked.connect(self._on_timer_start_pause)
        t_reset = QPushButton(tr("reset"))
        t_reset.setFixedHeight(36)
        t_reset.setStyleSheet(big_pill_qss)
        t_reset.clicked.connect(self.reset_timer)
        t_custom.addWidget(self.timer_input, 1)
        t_custom.addWidget(self.timer_start_btn)
        t_custom.addWidget(t_reset)
        tc.addLayout(t_custom)
        rv.addWidget(timer_card)

        rem_card = QFrame()
        rem_card.setObjectName("Card")
        rc = QVBoxLayout(rem_card)
        rc.setContentsMargins(12, 10, 12, 10)
        rc.setSpacing(8)

        rh = QHBoxLayout()
        rh.setSpacing(8)
        r_ico = QLabel()
        r_ico.setPixmap(get_pixmap("bell", 18, "#F2F5FA", "#F59E0B"))
        rlbl = QLabel(tr("reminders"))
        rlbl.setObjectName("Heading")
        rh.addWidget(r_ico)
        rh.addWidget(rlbl, 1)
        rc.addLayout(rh)

        r_in = QHBoxLayout()
        r_in.setSpacing(8)
        self.rem_edit = QLineEdit()
        self.rem_edit.setFixedHeight(36)
        self.rem_edit.setPlaceholderText(tr("reminder_placeholder"))
        self.rem_edit.setStyleSheet("border-radius: 14px; padding: 0px 12px; font-size: 12px;")
        self.rem_edit.returnPressed.connect(self._add_reminder_from_input)
        r_add = QPushButton(tr("add"))
        r_add.setObjectName("Primary")
        r_add.setFixedHeight(36)
        r_add.setStyleSheet(big_primary_qss)
        r_add.clicked.connect(self._add_reminder_from_input)
        r_in.addWidget(self.rem_edit, 1)
        r_in.addWidget(r_add)
        rc.addLayout(r_in)

        self.rem_list = QListWidget()
        self.rem_list.itemDoubleClicked.connect(self._delete_selected_reminder)
        rc.addWidget(self.rem_list, 1)
        rv.addWidget(rem_card, 1)

        self.rem_host.hide()
        root.addWidget(self.rem_host, 1)

        # Debounce autosave + 1-second clock tick for timer & reminders
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(450)
        self._save_timer.timeout.connect(self._flush_save_note)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._on_second_tick)
        self._clock_timer.start()

        self._reload_folders()
        self.refresh_reminders()

    def _switch_view(self, idx: int) -> None:
        self.btn_mode_notes.setChecked(idx == 0)
        self.btn_mode_rem.setChecked(idx == 1)
        self.notes_host.setVisible(idx == 0)
        self.rem_host.setVisible(idx == 1)

    # ------------------------------------------------------------------ Notes
    def _reload_folders(self) -> None:
        folders = self.storage.list_note_folders()
        cur = self.folder_combo.currentText()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem("All", "")
        for f in folders:
            self.folder_combo.addItem(f, f)
        idx = self.folder_combo.findText(cur)
        self.folder_combo.setCurrentIndex(max(0, idx))
        self.folder_combo.blockSignals(False)
        self._reload_notes()

    def _reload_notes(self) -> None:
        folder = self.folder_combo.currentData() or None
        notes = self.storage.list_notes(folder)
        self._notes_map = {n.id: n for n in notes}
        self.note_list.blockSignals(True)
        self.note_list.clear()
        for n in notes:
            li = QListWidgetItem(f"{n.title or 'Untitled'}\n[{n.folder}]")
            li.setData(Qt.ItemDataRole.UserRole, n.id)
            self.note_list.addItem(li)
        self.note_list.blockSignals(False)
        if notes:
            target_id = self._current_note.id if (self._current_note and self._current_note.id in self._notes_map) else notes[0].id
            for i in range(self.note_list.count()):
                if self.note_list.item(i).data(Qt.ItemDataRole.UserRole) == target_id:
                    self.note_list.setCurrentRow(i)
                    self._load_note_into_editor(self._notes_map[target_id])
                    break
        else:
            self._current_note = None
            self.title_edit.clear()
            self.editor.clear()

    def _on_note_selected(self, cur: QListWidgetItem | None, _prev=None) -> None:
        if not cur:
            return
        self._flush_save_note()
        nid = cur.data(Qt.ItemDataRole.UserRole)
        note = self._notes_map.get(nid)
        if note:
            self._load_note_into_editor(note)

    def _load_note_into_editor(self, note: NoteItem) -> None:
        self._current_note = note
        self.title_edit.blockSignals(True)
        self.editor.blockSignals(True)
        self.title_edit.setText(note.title)
        if note.content_html:
            self.editor.setHtml(note.content_html)
        else:
            self.editor.setPlainText(note.content_text)
        self.title_edit.blockSignals(False)
        self.editor.blockSignals(False)
        self.save_lbl.setText("Saved")

    def _schedule_autosave(self) -> None:
        if self._current_note is None:
            return
        self.save_lbl.setText("Saving...")
        self._save_timer.start()

    def _flush_save_note(self) -> None:
        if self._current_note is None:
            return
        title = self.title_edit.text().strip() or "Untitled"
        html = self.editor.toHtml()
        plain = self.editor.toPlainText()
        self.storage.save_note(self._current_note.id, self._current_note.folder, title, html, plain)
        self._current_note.title = title
        self._current_note.content_html = html
        self._current_note.content_text = plain
        self.save_lbl.setText("Saved")
        cur_item = self.note_list.currentItem()
        if cur_item:
            cur_item.setText(f"{title}\n[{self._current_note.folder}]")

    def _new_note(self) -> None:
        folder = self.folder_combo.currentData() or "Задачи"
        name, ok = QInputDialog.getText(self, "New Note", "Title:", text="New Note")
        if ok and name.strip():
            nid = self.storage.save_note(None, folder, name.strip(), "", "")
            self._reload_folders()
            if nid in self._notes_map:
                self._load_note_into_editor(self._notes_map[nid])

    def _delete_current_note(self) -> None:
        if not self._current_note:
            return
        self.storage.delete_note(self._current_note.id)
        self._current_note = None
        self._reload_folders()

    def _toggle_bold(self) -> None:
        fmt = self.editor.currentCharFormat()
        fmt.setFontWeight(QFont.Weight.Normal if fmt.fontWeight() > QFont.Weight.Normal else QFont.Weight.Bold)
        self.editor.mergeCurrentCharFormat(fmt)

    def _toggle_italic(self) -> None:
        fmt = self.editor.currentCharFormat()
        fmt.setFontItalic(not fmt.fontItalic())
        self.editor.mergeCurrentCharFormat(fmt)

    def _insert_checkbox(self) -> None:
        cursor = self.editor.textCursor()
        cursor.insertText("☐ ")

    def _toggle_line_checkbox(self) -> None:
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        line = cursor.selectedText()
        if line.startswith("☐"):
            cursor.insertText("☑" + line[1:])
        elif line.startswith("☑"):
            cursor.insertText("☐" + line[1:])

    # ------------------------------------------------------------------ Timer & Reminders
    def start_timer(self, seconds: int, label: str = "") -> None:
        from ...core.licensing import get_license_manager

        if not get_license_manager().is_feature_allowed("timer"):
            from ..pro_dialog import show_pro_upgrade

            show_pro_upgrade(self, reason="Таймер доступен только в Blip PRO.")
            return
        self._timer_total = max(1, seconds)
        self._timer_remaining = self._timer_total
        self._timer_running = True
        self._timer_label = label or tr("timer")
        self.timer_start_btn.setText(tr("pause"))
        self._update_timer_display()
        self.timer_updated.emit(self._timer_remaining, True, self._timer_label)

    def reset_timer(self) -> None:
        self._timer_running = False
        self._timer_remaining = 0
        self.timer_start_btn.setText(tr("start"))
        self._update_timer_display()
        self.timer_updated.emit(0, False, self._timer_label)

    def _on_timer_start_pause(self) -> None:
        from ...core.licensing import get_license_manager

        if not self._timer_running and not get_license_manager().is_feature_allowed("timer"):
            from ..pro_dialog import show_pro_upgrade

            show_pro_upgrade(self, reason="Таймер доступен только в Blip PRO.")
            return
        if self._timer_running:
            self._timer_running = False
            self.timer_start_btn.setText(tr("start"))
            self.timer_updated.emit(self._timer_remaining, False, self._timer_label)
            return
        if self._timer_remaining > 0:
            self._timer_running = True
            self.timer_start_btn.setText(tr("pause"))
            self.timer_updated.emit(self._timer_remaining, True, self._timer_label)
            return
        secs = parse_duration_seconds(self.timer_input.text())
        if secs:
            self.start_timer(secs, self.timer_input.text().strip())

    def _update_timer_display(self) -> None:
        s = max(0, self._timer_remaining)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        self.timer_display.setText(f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}")

    def add_reminder_phrase(self, phrase: str) -> bool:
        parsed = parse_reminder_phrase(phrase)
        if not parsed:
            return False
        self.storage.add_reminder(parsed.title, parsed.due_at.timestamp(), parsed.repeat_rule)
        self.refresh_reminders()
        return True

    def _add_reminder_from_input(self) -> None:
        txt = self.rem_edit.text().strip()
        if not txt:
            return
        if self.add_reminder_phrase(txt):
            self.rem_edit.clear()

    def refresh_reminders(self) -> None:
        items = self.storage.list_reminders(include_inactive=False)
        self._rem_map = {r.id: r for r in items}
        self.rem_list.clear()
        for r in items:
            due_s = datetime.fromtimestamp(r.due_at).strftime("%d.%m %H:%M")
            rep = f" [{r.repeat_days}]" if r.repeat_days and r.repeat_days != "none" else ""
            li = QListWidgetItem(f"{due_s}{rep} — {r.text}")
            li.setData(Qt.ItemDataRole.UserRole, r.id)
            self.rem_list.addItem(li)

    def _delete_selected_reminder(self, item: QListWidgetItem) -> None:
        rid = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(rid, int):
            self.storage.delete_reminder(rid)
            self.refresh_reminders()

    @staticmethod
    def _play_alert_chime() -> None:
        import sys
        import threading

        def _beep() -> None:
            if sys.platform != "win32":
                return
            try:
                import winsound

                winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
                winsound.Beep(880, 110)
                winsound.Beep(1175, 160)
            except Exception:
                pass

        threading.Thread(target=_beep, daemon=True).start()

    def _on_second_tick(self) -> None:
        if self._timer_running:
            self._timer_remaining -= 1
            self._update_timer_display()
            if self._timer_remaining <= 0:
                self._timer_running = False
                self.timer_start_btn.setText(tr("start"))
                self._play_alert_chime()
                self.timer_updated.emit(0, False, self._timer_label)
                self.timer_finished.emit(self._timer_label)
            else:
                self.timer_updated.emit(self._timer_remaining, True, self._timer_label)

        # Check due reminders
        now = time.time()
        changed = False
        for rem in list(self._rem_map.values()):
            if rem.active and not rem.triggered and rem.due_at <= now:
                self._play_alert_chime()
                self.reminder_triggered.emit(rem)
                nxt = next_occurrence(datetime.fromtimestamp(rem.due_at), rem.repeat_days)
                if nxt:
                    self.storage.update_reminder(rem.id, due_at=nxt.timestamp(), triggered=False)
                else:
                    self.storage.update_reminder(rem.id, active=False, triggered=True)
                changed = True
        if changed:
            self.refresh_reminders()
