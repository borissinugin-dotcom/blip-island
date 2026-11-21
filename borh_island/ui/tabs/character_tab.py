"""In-island Character Switcher & Tray Controls tab replacing legacy File Shelf."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.config import ConfigManager
from ...core.i18n import set_language, tr
from ...core.licensing import DEV_EXCLUSIVE_CHARACTER_IDS, get_license_manager
from ..characters import BUILTIN_CHARACTER_DEFS, CHARACTER_DEFS, CharacterWidget
from ..icons import get_icon
from ..pro_dialog import show_pro_upgrade
from ..theme import ThemePalette, resolve_palette


class CharacterSettingsTab(QWidget):
    """Live 15-character companion gallery plus tray/window settings moved inside the island."""

    config_changed = pyqtSignal()
    character_picked = pyqtSignal(str)
    command_bar_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    hot_reload_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, config_mgr: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_mgr = config_mgr
        self._syncing = False
        self._char_buttons: dict[str, QPushButton] = {}
        self._card_thumbs: list[CharacterWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 4)
        root.setSpacing(8)

        # ---- Top active companion preview card (fixed) ----
        hero = QFrame()
        hero.setObjectName("Card")
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(10, 8, 10, 8)
        hl.setSpacing(12)

        self.preview_char = CharacterWidget(self.config_mgr.config.character_id)
        self.preview_char.setFixedSize(56, 56)
        self.preview_char.set_animated(False)
        self.active_name_lbl = QLabel()
        self.active_name_lbl.setObjectName("Title")
        self.active_sub_lbl = QLabel("Выберите персонажа прямо здесь — он сразу оживёт на островке")
        self.active_sub_lbl.setObjectName("Muted")
        self.active_sub_lbl.setWordWrap(True)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        info_col.addWidget(self.active_name_lbl)
        info_col.addWidget(self.active_sub_lbl)
        hl.addWidget(self.preview_char)
        hl.addLayout(info_col, 1)
        root.addWidget(hero)

        # ---- Framed character selector card with internal scroll ----
        gallery_frame = QFrame()
        gallery_frame.setObjectName("Card")
        gv = QVBoxLayout(gallery_frame)
        gv.setContentsMargins(8, 8, 8, 10)
        gv.setSpacing(6)

        gal_hdr = QHBoxLayout()
        gal_hdr.setSpacing(6)
        gal_title = QLabel(f"Персонажи ({len(CHARACTER_DEFS)})")
        gal_title.setObjectName("Section")
        gal_hdr.addWidget(gal_title)
        gal_hdr.addStretch(1)

        self._folder_btns: dict[str, QPushButton] = {}
        self._active_folder = "all"
        builtin_cnt = len(BUILTIN_CHARACTER_DEFS)
        classic_cnt = len(CHARACTER_DEFS) - builtin_cnt
        for f_key, f_lbl in (
            ("all", f"Все ({len(CHARACTER_DEFS)})"),
            ("builtin", f"📁 Встроенные ({builtin_cnt})"),
            ("classic", f"3D / Классика ({classic_cnt})"),
        ):
            fb = QPushButton(f_lbl)
            fb.setCheckable(True)
            fb.setChecked(f_key == "all")
            fb.setCursor(Qt.CursorShape.PointingHandCursor)
            fb.setFixedHeight(26)
            fb.setStyleSheet("border-radius: 13px; padding: 0px 10px; font-size: 11px; font-weight: 600;")
            fb.clicked.connect(lambda _chk=False, fk=f_key: self._set_folder(fk))
            self._folder_btns[f_key] = fb
            gal_hdr.addWidget(fb)
        gv.addLayout(gal_hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        self._gal_grid = QGridLayout(body)
        grid = self._gal_grid
        grid.setContentsMargins(0, 0, 4, 2)
        grid.setSpacing(5)
        cols = 7
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        lang = self.config_mgr.config.language
        self._char_cells: list[tuple[str, QFrame]] = []
        for idx, (cid, n_ru, n_en) in enumerate(CHARACTER_DEFS):
            cell = QFrame()
            cell.setObjectName("Surface")
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(4, 5, 4, 5)
            cv.setSpacing(3)
            cv.setAlignment(Qt.AlignmentFlag.AlignCenter)

            thumb = CharacterWidget(cid)
            thumb.setFixedSize(40, 40)
            thumb.set_animated(False)
            thumb._phase = idx * 0.55
            thumb.poked.connect(lambda c=cid: self._select_character(c))
            self._card_thumbs.append(thumb)

            btn = QPushButton(n_ru if lang == "ru" else n_en)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(24)
            btn.setMinimumWidth(0)
            btn.setStyleSheet("border-radius: 12px; padding: 0px 6px; font-size: 11px;")
            btn.clicked.connect(lambda _chk=False, c=cid: self._select_character(c))
            self._char_buttons[cid] = btn

            cv.addWidget(thumb, 0, Qt.AlignmentFlag.AlignCenter)
            cv.addWidget(btn)
            grid.addWidget(cell, idx // cols, idx % cols)
            self._char_cells.append((cid, cell))

        scroll.setWidget(body)
        gv.addWidget(scroll, 1)
        root.addWidget(gallery_frame, 1)

        self._accent = resolve_palette(self.config_mgr.config.theme).accent
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(65)
        self._thumb_timer.timeout.connect(self._step_gallery_thumbs)
        self.sync_from_config()

    def _set_folder(self, folder: str) -> None:
        self._active_folder = folder
        for k, fb in self._folder_btns.items():
            fb.blockSignals(True)
            fb.setChecked(k == folder)
            fb.blockSignals(False)
        cols = 7
        vis_idx = 0
        for cid, cell in self._char_cells:
            is_builtin = cid.startswith("builtin_")
            show = (
                folder == "all"
                or (folder == "builtin" and is_builtin)
                or (folder == "classic" and not is_builtin)
            )
            self._gal_grid.removeWidget(cell)
            cell.setVisible(show)
            if show:
                self._gal_grid.addWidget(cell, vis_idx // cols, vis_idx % cols)
                vis_idx += 1

    def _step_gallery_thumbs(self) -> None:
        for t in self._card_thumbs:
            if not t.visibleRegion().isEmpty():
                t.step_frame(1.25)

    def apply_palette(self, pal: ThemePalette) -> None:
        if getattr(self, "_last_char_pal_acc", None) == pal.accent:
            return
        self._last_char_pal_acc = pal.accent
        self._accent = pal.accent
        self.preview_char.set_accent(pal.accent)
        for t in self._card_thumbs:
            t.set_accent(pal.accent)
        self._refresh_character_highlight()

    def _select_character(self, character_id: str) -> None:
        mgr = get_license_manager()
        if not mgr.is_character_allowed(character_id):
            reason = (
                "Этот персонаж доступен только по специальному коду разработчика."
                if character_id in DEV_EXCLUSIVE_CHARACTER_IDS
                else "В Blip FREE доступны только 3 первых персонажа (Жидкий ферромагнит, Плазменная сфера, Астро-бот). Перейдите на PRO!"
            )
            show_pro_upgrade(self, reason=reason, on_unlocked=self._on_license_updated)
            self._refresh_character_highlight()
            return
        cfg = self.config_mgr.config
        if cfg.character_id != character_id:
            cfg.character_id = character_id
            self.config_mgr.save(cfg)
            self.character_picked.emit(character_id)
        self.preview_char.set_character(character_id)
        self._refresh_character_highlight()

    def _on_license_updated(self) -> None:
        self._refresh_character_highlight()
        self.config_changed.emit()

    def _refresh_character_highlight(self) -> None:
        cfg = self.config_mgr.config
        mgr = get_license_manager()
        cur = cfg.character_id if mgr.is_character_allowed(cfg.character_id) else "liquid"
        lang = cfg.language
        active_label = cur
        lock_icon = getattr(self, "_lock_icon", None)
        if lock_icon is None:
            lock_icon = get_icon("lock", size=14, color="#fbbf24")
            self._lock_icon = lock_icon
        for cid, n_ru, n_en in CHARACTER_DEFS:
            base_name = n_ru if lang == "ru" else n_en
            if cid == cur:
                active_label = base_name
            btn = self._char_buttons.get(cid)
            if btn is not None:
                allowed = mgr.is_character_allowed(cid)
                is_cur = cid == cur and allowed
                if btn.text() != base_name:
                    btn.setText(base_name)
                sig = (is_cur, allowed, self._accent)
                if btn.property("_hl_sig") != sig:
                    btn.setProperty("_hl_sig", sig)
                    if not allowed:
                        btn.setIcon(lock_icon)
                        btn.setIconSize(QSize(14, 14))
                    else:
                        btn.setIcon(QIcon())
                    btn.setChecked(is_cur)
                    if is_cur:
                        btn.setStyleSheet(
                            f"QPushButton {{ border: 1.5px solid {self._accent}; border-radius: 14px; padding: 0px 8px; font-weight: 700; }}"
                        )
                    elif not allowed:
                        btn.setStyleSheet("border-radius: 14px; padding: 0px 8px; color: #7c8599;")
                    else:
                        btn.setStyleSheet("border-radius: 14px; padding: 0px 8px;")
        self.active_name_lbl.setText(f"Персонаж: {active_label}")

    def sync_from_config(self) -> None:
        cfg = self.config_mgr.config
        self._syncing = True
        try:
            self.preview_char.set_character(cfg.character_id)
            self._refresh_character_highlight()
        finally:
            self._syncing = False

    def set_thumbs_animated(self, enabled: bool) -> None:
        """Start or resume shared 24fps thumbnail animation cleanly without per-card timer overhead."""
        self.preview_char.set_animated(enabled)
        if enabled:
            if not self._thumb_timer.isActive():
                self._thumb_timer.start()
        else:
            if self._thumb_timer.isActive():
                self._thumb_timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        self.set_thumbs_animated(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.set_thumbs_animated(False)
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.set_thumbs_animated(False)
        super().closeEvent(event)
