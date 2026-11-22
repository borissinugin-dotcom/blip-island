"""Comprehensive tabbed Settings dialog: General, 39 Characters, Atmosphere & Tools, Capture/Privacy, Discord, Hotkeys, and Developers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import DEFAULT_HOTKEYS, ConfigManager
from ..core.i18n import set_language, tr
from ..core.qr_codec import render_qr_qimage
from ..core.win32_api import build_autostart_command, set_autostart
from ..services import text_tools
from ..services.discord_service import DiscordPresenceService, is_discord_running
from .atmosphere import ATMO_MODES, MOON_MODES, SKY_MODES, WEATHER_MODES
from .characters import CHARACTER_DEFS, CharacterWidget
from .icons import get_icon, get_pixmap
from .theme import FIVE_PRESET_FONTS, ThemePalette, apply_app_font, build_panel_qss, resolve_palette


class SettingsWindow(QDialog):
    """Unified settings window with Live Hover Preview and direct config persistence."""

    config_applied = pyqtSignal()
    live_preview_requested = pyqtSignal(dict)
    restart_app_requested = pyqtSignal()

    def __init__(
        self,
        config_mgr: ConfigManager,
        agents_service: object | None = None,
        discord_service: DiscordPresenceService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.agents_service = agents_service
        self.discord_service = discord_service
        self._saved = False
        self._hover_bindings: dict[QObject, dict] = {}
        self.setWindowTitle(f"{tr('settings')} — {tr('app_title')}")
        self.setMinimumSize(680, 580)

        pal: ThemePalette = resolve_palette(config_mgr.config.theme)
        self.setStyleSheet(f"QDialog {{ background-color: {pal.bg_island}; }}" + build_panel_qss(pal))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self._char_thumbs: list[CharacterWidget] = []
        self._char_timer = QTimer(self)
        self._char_timer.setInterval(42)
        self._char_timer.timeout.connect(self._step_char_thumbs)

        self._build_general_tab()
        self._build_characters_tab()
        self._build_atmosphere_tools_tab()
        self._build_capture_privacy_tab()
        self._build_discord_tab()
        self._build_hotkeys_tab()
        self._build_developers_tab()

        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.live_preview_box = _SettingsLivePreviewWidget(config_mgr)
        root.addWidget(self.live_preview_box)
        self.live_preview_requested.connect(self.live_preview_box.apply_patch)

        footer = QHBoxLayout()
        rst_btn = QPushButton("Перезагрузить Blip" if config_mgr.config.language == "ru" else "Reload Blip")
        rst_btn.clicked.connect(self.restart_app_requested.emit)
        footer.addWidget(rst_btn)
        footer.addStretch(1)
        save_btn = QPushButton(tr("save"))
        save_btn.setIcon(get_icon("save", 14, "#F8FAFC", "#93C5FD"))
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self._save_and_apply)
        close_btn = QPushButton(tr("cancel"))
        close_btn.clicked.connect(self.close)
        footer.addWidget(save_btn)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    def _bind_hover_preview(self, widget: QWidget, patch: dict) -> None:
        self._hover_bindings[widget] = dict(patch)
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in self._hover_bindings:
            if event.type() == QEvent.Type.Enter:
                self.live_preview_requested.emit(self._hover_bindings[watched])
            elif event.type() == QEvent.Type.Leave:
                self.live_preview_requested.emit({})
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ Tab 1: General
    def _build_general_tab(self) -> None:
        cfg = self.config_mgr.config
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(8)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Русский", "ru")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(cfg.language)))

        self.theme_combo = QComboBox()
        for k, v in (("dark", "Dark"), ("light", "Light"), ("system", "System (Windows)")):
            self.theme_combo.addItem(v, k)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(cfg.theme)))

        self.font_combo = QComboBox()
        for fam, label in FIVE_PRESET_FONTS:
            self.font_combo.addItem(label, fam)
        f_idx = self.font_combo.findData(getattr(cfg, "ui_font_family", "Segoe UI"))
        self.font_combo.setCurrentIndex(max(0, f_idx))

        self._custom_font_path = str(getattr(cfg, "custom_font_path", "") or "").strip()
        self.custom_font_lbl = QLabel(Path(self._custom_font_path).name if self._custom_font_path else "Не выбран")
        self.custom_font_lbl.setStyleSheet("color: #94A3B8;")
        self.custom_font_btn = QPushButton("Выбрать .ttf/.otf…")
        self.custom_font_btn.clicked.connect(self._pick_custom_font)
        self.custom_font_clr = QPushButton("Сброс")
        self.custom_font_clr.clicked.connect(self._clear_custom_font)
        cf_row = QWidget()
        cf_lay = QHBoxLayout(cf_row)
        cf_lay.setContentsMargins(0, 0, 0, 0)
        cf_lay.setSpacing(6)
        cf_lay.addWidget(self.custom_font_lbl, 1)
        cf_lay.addWidget(self.custom_font_btn)
        cf_lay.addWidget(self.custom_font_clr)

        self.mon_spin = QSpinBox()
        self.mon_spin.setRange(0, max(0, len(QGuiApplication.screens()) - 1))
        self.mon_spin.setValue(cfg.monitor_index)

        self.offx_spin = QSpinBox()
        self.offx_spin.setRange(-1500, 1500)
        self.offx_spin.setValue(cfg.offset_x)

        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(80, 200)
        self.scale_spin.setSuffix("%")
        self.scale_spin.setValue(int(round(cfg.scale * 100)))

        self.op_col_spin = QSpinBox()
        self.op_col_spin.setRange(25, 100)
        self.op_col_spin.setSuffix("%")
        self.op_col_spin.setValue(int(round(cfg.collapsed_opacity * 100)))

        self.op_exp_spin = QSpinBox()
        self.op_exp_spin.setRange(35, 100)
        self.op_exp_spin.setSuffix("%")
        self.op_exp_spin.setValue(int(round(cfg.expanded_opacity * 100)))

        self.pill_mode_combo = QComboBox()
        for k, v in (("compact", "Compact"), ("track", "Track Title"), ("miniplayer", "Mini-player")):
            self.pill_mode_combo.addItem(v, k)
        self.pill_mode_combo.setCurrentIndex(max(0, self.pill_mode_combo.findData(cfg.pill_mode)))

        self.chk_lyrics_pill = QCheckBox("Показывать строку текста песни в компактном острове")
        self.chk_lyrics_pill.setChecked(cfg.show_lyrics_in_pill)
        self.chk_weather_pill = QCheckBox("Show weather temperature in pill")
        self.chk_weather_pill.setChecked(cfg.show_weather_in_pill)

        self.chk_game_stats_pill = QCheckBox(
            "Показывать CPU, RAM, GPU в игре (игровой HUD)" if cfg.language == "ru" else "Show CPU, RAM, GPU in-game HUD"
        )
        self.chk_game_stats_pill.setChecked(bool(getattr(cfg, "show_game_stats_in_pill", True)))
        self.chk_game_cpu = QCheckBox("CPU")
        self.chk_game_cpu.setChecked(bool(getattr(cfg, "show_game_cpu", True)))
        self.chk_game_ram = QCheckBox("RAM")
        self.chk_game_ram.setChecked(bool(getattr(cfg, "show_game_ram", True)))
        self.chk_game_gpu = QCheckBox("GPU")
        self.chk_game_gpu.setChecked(bool(getattr(cfg, "show_game_gpu", True)))
        self.game_stats_preview_lbl = QLabel("CPU —% • RAM —% • GPU —%")
        self.game_stats_preview_lbl.setObjectName("Muted")
        self.game_stats_preview_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #93C5FD;")
        game_hud_row = QWidget()
        gh_lay = QHBoxLayout(game_hud_row)
        gh_lay.setContentsMargins(0, 0, 0, 0)
        gh_lay.setSpacing(10)
        gh_lay.addWidget(self.chk_game_stats_pill)
        gh_lay.addWidget(self.chk_game_cpu)
        gh_lay.addWidget(self.chk_game_ram)
        gh_lay.addWidget(self.chk_game_gpu)
        gh_lay.addStretch(1)
        gh_lay.addWidget(self.game_stats_preview_lbl)

        self.chk_rain_drip = QCheckBox("Rain / Snow drip reaction on character")
        self.chk_rain_drip.setChecked(cfg.weather_rain_drip)
        self.chk_eq = QCheckBox("Edge spectrum equalizer")
        self.chk_eq.setChecked(cfg.edge_equalizer)
        self.chk_liquid_drop = QCheckBox("Liquid Drop physics (форма капли при подходе мыши)")
        self.chk_liquid_drop.setChecked(cfg.liquid_drop)
        self.chk_autohide = QCheckBox("Auto-hide collapsed pill (hover top edge to reveal)")
        self.chk_autohide.setChecked(cfg.auto_hide)
        self.autohide_excl_edit = QLineEdit(", ".join(cfg.auto_hide_excluded_apps))
        self.chk_fullscreen = QCheckBox("Скрывать при полноэкранном видео" if cfg.language == "ru" else "Hide pill in fullscreen video")
        self.chk_fullscreen.setChecked(cfg.hide_in_fullscreen)
        self.chk_through = QCheckBox("Click-through collapsed pill")
        self.chk_through.setChecked(cfg.click_through)
        self.chk_excl_cap = QCheckBox("Скрывать остров от скриншотов и демки в Discord")
        self.chk_excl_cap.setChecked(cfg.exclude_from_capture)
        self.chk_autostart = QCheckBox("Автозапуск с Windows (вкл/выкл при входе)")
        self.chk_autostart.setChecked(cfg.autostart)
        self.chk_hot_reload = QCheckBox("Live Hot-Reload (apply UI/code updates on the fly)")
        self.chk_hot_reload.setChecked(cfg.hot_reload)

        # Appearance mode (Weather PRO vs Color FREE) + Swatch row + Player style + Home blocks
        app_row = QHBoxLayout()
        cur_am = str(getattr(cfg, "island_appearance_mode", "weather") or "weather").lower()
        self.appearance_combo = QComboBox()
        self.appearance_combo.addItem("Живая погода и сцена (PRO)", "weather")
        self.appearance_combo.addItem("🎵 Под цвет музыки (весь интерфейс)", "music")
        self.appearance_combo.addItem("Свой цвет (FREE / PRO)", "color")
        self.appearance_combo.addItem("Своя картинка (PNG/JPG/WEBP)", "image")
        self.appearance_combo.addItem("Своя GIF / Видео (PRO)", "video")
        self.appearance_combo.setCurrentIndex(max(0, self.appearance_combo.findData(cur_am)))
        self._bind_hover_preview(self.appearance_combo, {"island_appearance_mode": "color"})
        app_row.addWidget(self.appearance_combo, 1)

        self.sw_img_btn = QPushButton("🖼 Картинка…")
        self.sw_img_btn.setFixedHeight(26)
        self.sw_img_btn.clicked.connect(self._pick_sw_bg_image)
        self.sw_vid_btn = QPushButton("🎬 GIF/Видео…")
        self.sw_vid_btn.setFixedHeight(26)
        self.sw_vid_btn.clicked.connect(self._pick_sw_bg_video)
        app_row.addWidget(self.sw_img_btn)
        app_row.addWidget(self.sw_vid_btn)

        sw_widget = QWidget()
        sw_lay = QHBoxLayout(sw_widget)
        sw_lay.setContentsMargins(0, 0, 0, 0)
        sw_lay.setSpacing(6)
        self._sw_accent_val = str(getattr(cfg, "accent_color", "") or "#3B82F6")
        self._sw_btns: dict[str, QPushButton] = {}
        for hx in ("#3B82F6", "#8B5CF6", "#EC4899", "#EF4444", "#F59E0B", "#10B981", "#06B6D4", "#64748B"):
            sb = QPushButton("")
            sb.setFixedSize(26, 26)
            sb.setCheckable(True)
            sb.setChecked(hx.lower() == self._sw_accent_val.lower())
            sb.setStyleSheet(
                f"QPushButton {{ background: {hx}; border-radius: 13px; border: 2px solid transparent; padding: 0px; }}"
                f"QPushButton:checked {{ border: 2px solid #FFFFFF; }}"
            )
            sb.clicked.connect(lambda _=False, c=hx: self._pick_sw_color(c))
            self._bind_hover_preview(sb, {"island_appearance_mode": "color", "accent_color": hx})
            sw_lay.addWidget(sb)
            self._sw_btns[hx] = sb
        self.sw_wheel_btn = QPushButton("🌈 Колёсико…")
        self.sw_wheel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sw_wheel_btn.setFixedHeight(26)
        self.sw_wheel_btn.clicked.connect(self._open_sw_color_wheel)
        sw_lay.addWidget(self.sw_wheel_btn)
        sw_lay.addStretch(1)

        self.player_style_combo = QComboBox()
        for pk, plbl in (("mirror", "Mirror (Зеркало)"), ("fill", "Fill (Заливка)"), ("compact", "Compact")):
            self.player_style_combo.addItem(plbl, pk)
        self.player_style_combo.setCurrentIndex(max(0, self.player_style_combo.findData(getattr(cfg, "player_card_style", "mirror"))))

        self._bind_hover_preview(self.theme_combo, {"theme": "light" if cfg.theme == "dark" else "dark"})
        self._bind_hover_preview(self.pill_mode_combo, {"pill_mode": "miniplayer"})
        self._bind_hover_preview(self.chk_eq, {"edge_equalizer": True})
        self._bind_hover_preview(self.chk_liquid_drop, {"liquid_drop": True})

        self.player_style_combo.currentIndexChanged.connect(
            lambda _i=0: self.live_preview_requested.emit({"player_card_style": self.player_style_combo.currentData()})
        )
        self.pill_mode_combo.currentIndexChanged.connect(
            lambda _i=0: self.live_preview_requested.emit({"pill_mode": self.pill_mode_combo.currentData()})
        )
        self.theme_combo.currentIndexChanged.connect(
            lambda _i=0: self.live_preview_requested.emit({"theme": self.theme_combo.currentData()})
        )
        self.appearance_combo.currentIndexChanged.connect(
            lambda _i=0: self.live_preview_requested.emit({"island_appearance_mode": self.appearance_combo.currentData()})
        )
        self.scale_spin.valueChanged.connect(
            lambda v: self.live_preview_requested.emit({"scale": float(v) / 100.0})
        )
        self.op_col_spin.valueChanged.connect(
            lambda v: self.live_preview_requested.emit({"collapsed_opacity": float(v) / 100.0})
        )
        self.op_exp_spin.valueChanged.connect(
            lambda v: self.live_preview_requested.emit({"expanded_opacity": float(v) / 100.0})
        )
        self.chk_eq.toggled.connect(
            lambda checked: self.live_preview_requested.emit({"edge_equalizer": bool(checked)})
        )
        self.chk_liquid_drop.toggled.connect(
            lambda checked: self.live_preview_requested.emit({"liquid_drop": bool(checked)})
        )

        form.addRow("", self.chk_autostart)
        form.addRow("Language:", self.lang_combo)
        form.addRow("Theme:", self.theme_combo)
        form.addRow("Фон острова:", app_row)
        form.addRow("Палитра цвета:", sw_widget)
        form.addRow("Стиль плеера:", self.player_style_combo)
        form.addRow("Шрифт интерфейса:", self.font_combo)
        form.addRow("Свой шрифт (.ttf/.otf):", cf_row)
        form.addRow("Monitor Index:", self.mon_spin)
        form.addRow("Horizontal Offset (px):", self.offx_spin)
        form.addRow("Scale:", self.scale_spin)
        form.addRow("Collapsed Opacity:", self.op_col_spin)
        form.addRow("Expanded Opacity:", self.op_exp_spin)
        form.addRow("Pill Display Mode:", self.pill_mode_combo)
        form.addRow("Auto-hide Exclusions (exe):", self.autohide_excl_edit)
        form.addRow("", self.chk_lyrics_pill)
        form.addRow("", self.chk_weather_pill)
        form.addRow("Игровой HUD:" if cfg.language == "ru" else "Game HUD:", game_hud_row)
        form.addRow("", self.chk_rain_drip)
        form.addRow("", self.chk_eq)
        form.addRow("", self.chk_liquid_drop)
        form.addRow("", self.chk_autohide)
        form.addRow("", self.chk_fullscreen)
        form.addRow("", self.chk_through)
        form.addRow("", self.chk_excl_cap)
        form.addRow("", self.chk_hot_reload)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(w)
        self.tabs.addTab(sc, "General")

    # ------------------------------------------------------------------ Tab 2: Characters Gallery
    def _build_characters_tab(self) -> None:
        self._selected_char = self.config_mgr.config.character_id
        host = QWidget()
        grid = QGridLayout(host)
        grid.setSpacing(8)
        self._char_cards: dict[str, QPushButton] = {}
        lang = self.config_mgr.config.language

        for idx, (cid, n_ru, n_en) in enumerate(CHARACTER_DEFS):
            card = QFrame()
            card.setObjectName("Card")
            cv = QVBoxLayout(card)
            cv.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cw = CharacterWidget(cid)
            cw.setFixedSize(64, 64)
            cw.set_animated(False)
            cw._phase = idx * 0.55
            cw.poked.connect(lambda c=cid: self._pick_character(c))
            self._char_thumbs.append(cw)
            btn = QPushButton(n_ru if lang == "ru" else n_en)
            btn.setCheckable(True)
            btn.setChecked(cid == self._selected_char)
            btn.setMinimumHeight(28)
            btn.setStyleSheet("border-radius: 14px; padding: 0px 10px;")
            btn.clicked.connect(lambda _, c=cid: self._pick_character(c))
            self._char_cards[cid] = btn
            cv.addWidget(cw, 0, Qt.AlignmentFlag.AlignCenter)
            cv.addWidget(btn)
            grid.addWidget(card, idx // 3, idx % 3)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(host)
        self.tabs.addTab(sc, f"Characters ({len(CHARACTER_DEFS)})")

    def _pick_character(self, cid: str) -> None:
        self._selected_char = cid
        for k, b in self._char_cards.items():
            b.setChecked(k == cid)
        self.config_mgr.config.character_id = cid
        self.config_mgr.save()
        self.config_applied.emit()

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1 and self.isVisible():
            if not self._char_timer.isActive():
                self._char_timer.start()
        elif self._char_timer.isActive():
            self._char_timer.stop()

    def _step_char_thumbs(self) -> None:
        for cw in self._char_thumbs:
            cw.step_frame(1.25)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._on_tab_changed(self.tabs.currentIndex())

    def closeEvent(self, event) -> None:  # noqa: N802
        self._char_timer.stop()
        if not self._saved:
            self.live_preview_requested.emit({})
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._char_timer.stop()
        if not self._saved:
            self.live_preview_requested.emit({})
        super().done(result)

    def _pick_sw_color(self, hex_col: str) -> None:
        self._sw_accent_val = hex_col
        for k, b in self._sw_btns.items():
            b.blockSignals(True)
            b.setChecked(k.lower() == hex_col.lower())
            b.blockSignals(False)
        idx = self.appearance_combo.findData("color")
        if idx >= 0:
            self.appearance_combo.setCurrentIndex(idx)
        self.live_preview_requested.emit({"island_appearance_mode": "color", "accent_color": hex_col})

    def _open_sw_color_wheel(self) -> None:
        from .color_wheel_dialog import BorhColorWheelDialog

        dlg = BorhColorWheelDialog(self._sw_accent_val, parent=self)
        dlg.color_selected.connect(self._pick_sw_color)
        dlg.exec()

    def _pick_sw_bg_image(self) -> None:
        import shutil
        from .color_wheel_dialog import pick_bg_media_file

        path = pick_bg_media_file(self, "Выберите фоновую картинку", "Изображения (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        src = Path(path)
        dst_dir = self.config_mgr.data_dir / "custom_bg"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"bg_image{src.suffix.lower()}"
        try:
            shutil.copy2(src, dst)
        except Exception:
            dst = src
        self.config_mgr.config.custom_bg_image_path = str(dst)
        self.config_mgr.config.island_appearance_mode = "image"
        idx = self.appearance_combo.findData("image")
        if idx >= 0:
            self.appearance_combo.setCurrentIndex(idx)
        self.live_preview_requested.emit({"island_appearance_mode": "image"})

    def _pick_sw_bg_video(self) -> None:
        import shutil
        from ..core.licensing import get_license_manager
        from .color_wheel_dialog import pick_bg_media_file

        if not get_license_manager().is_feature_allowed("weather"):
            QMessageBox.information(self, "Blip PRO", "Загрузка GIF и зацикленных видео на фон доступна в Blip PRO.")
            return
        path = pick_bg_media_file(self, "Выберите GIF или видео (PRO)", "GIF и Видео (*.gif *.mp4 *.webm *.mov *.webp *.avi *.mkv)")
        if not path:
            return
        src = Path(path)
        dst_dir = self.config_mgr.data_dir / "custom_bg"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"bg_video{src.suffix.lower()}"
        try:
            shutil.copy2(src, dst)
        except Exception:
            dst = src
        self.config_mgr.config.custom_bg_video_path = str(dst)
        self.config_mgr.config.island_appearance_mode = "video"
        idx = self.appearance_combo.findData("video")
        if idx >= 0:
            self.appearance_combo.setCurrentIndex(idx)
        self.live_preview_requested.emit({"island_appearance_mode": "video"})

    # ------------------------------------------------------------------ Tab 3: Atmosphere & Dev Tools
    def _build_atmosphere_tools_tab(self) -> None:
        cfg = self.config_mgr.config
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        # Scene / Sky / Weather / Moon Overrides (moved out of main Island & WeatherTab)
        atmo_card = QFrame()
        atmo_card.setObjectName("Card")
        ac_lay = QVBoxLayout(atmo_card)
        ac_lay.setContentsMargins(14, 12, 14, 12)
        ac_lay.setSpacing(8)
        atmo_hdr = QHBoxLayout()
        atmo_hdr.setSpacing(6)
        atmo_ico = QLabel()
        atmo_ico.setPixmap(get_pixmap("sun", 15, "#F2F5FA", "#FBBF24"))
        atmo_title = QLabel("Сцена, Небо, Погода и Луна (по умолчанию: Авто)" if cfg.language == "ru" else "Scene, Sky, Weather & Moon (default: Auto)")
        atmo_title.setObjectName("Heading")
        atmo_hdr.addWidget(atmo_ico)
        atmo_hdr.addWidget(atmo_title, 1)
        ac_lay.addLayout(atmo_hdr)

        form = QFormLayout()
        form.setSpacing(8)
        self.atmo_combo = QComboBox()
        for key, label in ATMO_MODES:
            self.atmo_combo.addItem(label, key)
        self.atmo_combo.setCurrentIndex(max(0, self.atmo_combo.findData(cfg.atmosphere_preview)))
        self.atmo_combo.activated.connect(self._on_atmo_preset_picked)

        self.sky_combo = QComboBox()
        for key, label in SKY_MODES:
            self.sky_combo.addItem(label, key)
        self.sky_combo.setCurrentIndex(max(0, self.sky_combo.findData(cfg.atmosphere_sky_mode)))
        self.sky_combo.activated.connect(self._apply_atmosphere_live)

        self.weather_combo = QComboBox()
        for key, label in WEATHER_MODES:
            self.weather_combo.addItem(label, key)
        self.weather_combo.setCurrentIndex(max(0, self.weather_combo.findData(cfg.atmosphere_weather_mode)))
        self.weather_combo.activated.connect(self._apply_atmosphere_live)

        self.moon_combo = QComboBox()
        for key, label in MOON_MODES:
            self.moon_combo.addItem(label, key)
        self.moon_combo.setCurrentIndex(max(0, self.moon_combo.findData(cfg.atmosphere_moon_mode)))
        self.moon_combo.activated.connect(self._apply_atmosphere_live)

        q_row = QHBoxLayout()
        q_row.setSpacing(6)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(25, 100)
        self.quality_slider.setValue(max(25, min(100, int(getattr(cfg, "atmosphere_quality", 100)))))
        self.quality_pct_lbl = QLabel(f"{self.quality_slider.value()}%")
        self.quality_pct_lbl.setMinimumWidth(38)
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        q_row.addWidget(self.quality_slider, 1)
        q_row.addWidget(self.quality_pct_lbl)
        q_wrap = QWidget()
        q_wrap.setLayout(q_row)

        self.fps_combo = QComboBox()
        for fps_val in (240, 120, 60, 30):
            self.fps_combo.addItem(f"{fps_val} FPS", fps_val)
        cur_fps = min((30, 60, 120, 240), key=lambda v: abs(v - int(getattr(cfg, "target_fps", 120) or 120)))
        self.fps_combo.setCurrentIndex(max(0, self.fps_combo.findData(cur_fps)))
        self.fps_combo.activated.connect(self._apply_atmosphere_live)

        self.chk_remove_power_limits = QCheckBox(
            "Убрать лимиты Windows (режим ноутбука / без троттлинга)"
            if cfg.language == "ru"
            else "Remove Windows limits (laptop mode / no throttling)"
        )
        self.chk_remove_power_limits.setChecked(bool(getattr(cfg, "remove_power_limits", True)))
        self.chk_remove_power_limits.toggled.connect(self._apply_atmosphere_live)

        form.addRow("Пресет сцены:", self.atmo_combo)
        form.addRow("Небо / время суток:", self.sky_combo)
        form.addRow("Погодный слой:", self.weather_combo)
        form.addRow("Фаза луны:", self.moon_combo)
        form.addRow("Детализация графики:" if cfg.language == "ru" else "Graphics detail:", q_wrap)
        form.addRow("Лимит кадров (FPS):" if cfg.language == "ru" else "FPS Limit:", self.fps_combo)
        form.addRow("", self.chk_remove_power_limits)
        ac_lay.addLayout(form)

        reset_btn = QPushButton("Сбросить на Авто (по времени и городу)" if cfg.language == "ru" else "Reset to Auto (time & city)")
        reset_btn.setIcon(get_icon("refresh", 14, "#F2F5FA", "#60A5FA"))
        reset_btn.clicked.connect(self._reset_atmo_to_auto)
        ac_lay.addWidget(reset_btn)

        from ..core.licensing import APP_VERSION

        upd_row = QHBoxLayout()
        upd_row.setSpacing(8)
        self.upd_status_lbl = QLabel(f"Версия: v{APP_VERSION}" if cfg.language == "ru" else f"Version: v{APP_VERSION}")
        self.upd_status_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        self.upd_check_btn = QPushButton("Проверить обновления (GitHub)" if cfg.language == "ru" else "Check for Updates (GitHub)")
        self.upd_check_btn.setIcon(get_icon("refresh", 14, "#F2F5FA", "#60A5FA"))
        self.upd_check_btn.clicked.connect(self._check_github_updates)
        upd_row.addWidget(self.upd_status_lbl, 1)
        upd_row.addWidget(self.upd_check_btn)
        ac_lay.addLayout(upd_row)

        try:
            from ..core.licensing import LicenseManager

            if LicenseManager().get_status().is_master:
                master_btn = QPushButton("🛡️ Управление кодами (Master BORh)")
                master_btn.clicked.connect(self._open_master_code_control)
                ac_lay.addWidget(master_btn)
        except Exception:
            pass
        v.addWidget(atmo_card)

        # Dev Tools (QR, URL, Base64, JSON) hidden from main island UI by default
        dev_card = QFrame()
        dev_card.setObjectName("Card")
        dc_lay = QVBoxLayout(dev_card)
        dc_lay.setContentsMargins(14, 12, 14, 12)
        dc_lay.setSpacing(8)
        dev_hdr = QHBoxLayout()
        dev_hdr.setSpacing(6)
        dev_ico = QLabel()
        dev_ico.setPixmap(get_pixmap("tools", 15, "#F2F5FA", "#60A5FA"))
        dev_title = QLabel("Инструменты (QR, URL, Base64, JSON)" if cfg.language == "ru" else "Developer Tools (QR, URL, Base64, JSON)")
        dev_title.setObjectName("Heading")
        dev_hdr.addWidget(dev_ico)
        dev_hdr.addWidget(dev_title, 1)
        dc_lay.addLayout(dev_hdr)

        self.chk_dev_tools = QCheckBox("Показывать dev-кнопки (QR, URL, Base64, JSON) в главном интерфейсе")
        self.chk_dev_tools.setChecked(bool(getattr(cfg, "show_dev_text_tools", False)))
        dc_lay.addWidget(self.chk_dev_tools)

        self.settings_tool_in = QPlainTextEdit()
        self.settings_tool_in.setPlaceholderText("Текст, ссылка или JSON для конвертации / QR-кода…")
        self.settings_tool_in.setMaximumHeight(68)
        dc_lay.addWidget(self.settings_tool_in)

        btn_row = QHBoxLayout()
        for label, fn in (
            ("JSON Format", text_tools.format_json),
            ("JSON Min", text_tools.minify_json),
            ("Base64+", text_tools.base64_encode),
            ("Base64-", text_tools.base64_decode),
            ("URL+", text_tools.url_encode),
            ("URL-", text_tools.url_decode),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _, f=fn: self._run_settings_text_tool(f))
            btn_row.addWidget(b)
        qr_make_btn = QPushButton("QR-код")
        qr_make_btn.setObjectName("Primary")
        qr_make_btn.clicked.connect(self._generate_settings_qr)
        btn_row.addWidget(qr_make_btn)
        dc_lay.addLayout(btn_row)

        out_row = QHBoxLayout()
        self.settings_tool_out = QPlainTextEdit()
        self.settings_tool_out.setPlaceholderText("Результат…")
        self.settings_tool_out.setMaximumHeight(86)
        out_row.addWidget(self.settings_tool_out, 1)

        self.settings_qr_lbl = QLabel("")
        self.settings_qr_lbl.setFixedSize(90, 90)
        self.settings_qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.settings_qr_lbl.setStyleSheet("background: rgba(255,255,255,0.06); border-radius: 16px;")
        self.settings_qr_lbl.hide()
        out_row.addWidget(self.settings_qr_lbl)
        dc_lay.addLayout(out_row)

        v.addWidget(dev_card)
        v.addStretch(1)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(w)
        tab_lbl = "Атмосфера и Инструменты" if cfg.language == "ru" else "Atmosphere & Tools"
        self.tabs.addTab(sc, tab_lbl)

    def _on_atmo_preset_picked(self) -> None:
        for cb in (self.sky_combo, self.weather_combo, self.moon_combo):
            cb.blockSignals(True)
            idx = cb.findData("auto")
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        self._apply_atmosphere_live()

    def _on_quality_changed(self, val: int) -> None:
        self.quality_pct_lbl.setText(f"{int(val)}%")
        self._apply_atmosphere_live()

    def _apply_atmosphere_live(self) -> None:
        cfg = self.config_mgr.config
        cfg.atmosphere_preview = str(self.atmo_combo.currentData() or "auto")
        cfg.atmosphere_sky_mode = str(self.sky_combo.currentData() or "auto")
        cfg.atmosphere_weather_mode = str(self.weather_combo.currentData() or "auto")
        cfg.atmosphere_moon_mode = str(self.moon_combo.currentData() or "auto")
        cfg.atmosphere_quality = int(self.quality_slider.value())
        cfg.target_fps = int(self.fps_combo.currentData() or 120)
        cfg.remove_power_limits = bool(self.chk_remove_power_limits.isChecked())
        self.config_mgr.save()
        self.config_applied.emit()

    def _reset_atmo_to_auto(self) -> None:
        for cb in (self.atmo_combo, self.sky_combo, self.weather_combo, self.moon_combo):
            cb.blockSignals(True)
            idx = cb.findData("auto")
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        self._apply_atmosphere_live()

    def _open_master_code_control(self) -> None:
        from .pro_dialog import BlipProWidget

        w = BlipProWidget(self, inline=True)
        w._open_master_control()

    def _check_github_updates(self) -> None:
        import threading
        from PyQt6.QtWidgets import QMessageBox
        from ..services.updater_service import check_github_updates, download_and_stage_update

        self.upd_check_btn.setEnabled(False)
        self.upd_status_lbl.setText("Проверяю GitHub...")
        result_box: dict[str, object] = {}

        def _worker() -> None:
            info = check_github_updates()
            if info.error or not info.available:
                result_box["info"] = info
                return
            ok, msg = download_and_stage_update(info)
            result_box["info"] = info
            result_box["ok"] = ok
            result_box["msg"] = msg

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        def _poll() -> None:
            if t.is_alive():
                QTimer.singleShot(120, _poll)
                return
            self.upd_check_btn.setEnabled(True)
            info = result_box.get("info")
            if info is None:
                self.upd_status_lbl.setText("Ошибка проверки обновлений")
                return
            if getattr(info, "error", ""):
                self.upd_status_lbl.setText(f"Ошибка: {info.error[:48]}")
                QMessageBox.warning(self, "Обновление Blip", f"Не удалось проверить GitHub:\n{info.error}")
                return
            if not getattr(info, "available", False):
                self.upd_status_lbl.setText(f"Установлена актуальная версия v{info.current_version}")
                QMessageBox.information(self, "Обновление Blip", f"У вас уже актуальная версия v{info.current_version}.")
                return
            ok = bool(result_box.get("ok", False))
            msg = str(result_box.get("msg", ""))
            self.upd_status_lbl.setText(msg[:64])
            if ok:
                QTimer.singleShot(400, QApplication.quit)
            else:
                QMessageBox.warning(self, "Обновление Blip", msg)

        QTimer.singleShot(120, _poll)

    def _run_settings_text_tool(self, fn) -> None:
        src = self.settings_tool_in.toPlainText()
        try:
            self.settings_tool_out.setPlainText(str(fn(src)))
        except Exception as exc:
            self.settings_tool_out.setPlainText(f"Ошибка: {exc}")

    def _generate_settings_qr(self) -> None:
        txt = (self.settings_tool_in.toPlainText() or self.settings_tool_out.toPlainText()).strip()
        if not txt:
            return
        try:
            qimg = render_qr_qimage(txt, box_size=6, border=2)
            self.settings_qr_lbl.setPixmap(QPixmap.fromImage(qimg).scaled(86, 86, Qt.AspectRatioMode.KeepAspectRatio))
            self.settings_qr_lbl.show()
        except Exception as exc:
            self.settings_tool_out.setPlainText(f"QR error: {exc}")

    # ------------------------------------------------------------------ Tab 4: Capture & Privacy
    def _build_capture_privacy_tab(self) -> None:
        cfg = self.config_mgr.config
        w = QWidget()
        form = QFormLayout(w)

        self.scr_dir_edit = QLineEdit(cfg.screenshot_dir)
        scr_btn = QPushButton("Browse")
        scr_btn.clicked.connect(lambda: self._pick_dir(self.scr_dir_edit))
        r1 = QHBoxLayout()
        r1.addWidget(self.scr_dir_edit, 1)
        r1.addWidget(scr_btn)

        self.rec_dir_edit = QLineEdit(cfg.recording_dir)
        rec_btn = QPushButton("Browse")
        rec_btn.clicked.connect(lambda: self._pick_dir(self.rec_dir_edit))
        r2 = QHBoxLayout()
        r2.addWidget(self.rec_dir_edit, 1)
        r2.addWidget(rec_btn)

        self.rec_fmt_combo = QComboBox()
        self.rec_fmt_combo.addItem("MP4 (H.264)", "mp4")
        self.rec_fmt_combo.addItem("Animated GIF", "gif")
        self.rec_fmt_combo.setCurrentIndex(max(0, self.rec_fmt_combo.findData(cfg.recording_format)))

        self.rec_fps_spin = QSpinBox()
        self.rec_fps_spin.setRange(10, 60)
        self.rec_fps_spin.setValue(cfg.recording_fps)

        self.rec_audio_combo = QComboBox()
        for ak, albl in (("none", "Без звука"), ("system", "Звук компьютера"), ("both", "Компьютер и микрофон")):
            self.rec_audio_combo.addItem(albl, ak)
        self.rec_audio_combo.setCurrentIndex(max(0, self.rec_audio_combo.findData(getattr(cfg, "recording_audio_mode", "system"))))

        self.upload_combo = QComboBox()
        for k in ("none", "imgbb", "imgur"):
            self.upload_combo.addItem(k.upper(), k)
        self.upload_combo.setCurrentIndex(max(0, self.upload_combo.findData(cfg.upload_service)))
        self.imgbb_key = QLineEdit(cfg.get_secret("imgbb"))
        self.imgbb_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.imgur_key = QLineEdit(cfg.get_secret("imgur"))
        self.imgur_key.setEchoMode(QLineEdit.EchoMode.Password)

        self.chk_clip = QCheckBox("Enable clipboard history monitor")
        self.chk_clip.setChecked(cfg.clipboard_enabled)
        self.clip_max_spin = QSpinBox()
        self.clip_max_spin.setRange(20, 2000)
        self.clip_max_spin.setValue(cfg.clipboard_max_items)
        self.clip_excl_edit = QLineEdit(", ".join(cfg.clipboard_excluded_apps))

        self.chk_vpn_ip = QCheckBox("Lookup external IP/country on VPN connect (ipwho.is)")
        self.chk_vpn_ip.setChecked(cfg.vpn_ip_lookup)
        self.chk_steam = QCheckBox("Watch Steam active downloads")
        self.chk_steam.setChecked(cfg.steam_downloads_enabled)
        self.chk_obs = QCheckBox("Watch OBS Studio recording/streaming status")
        self.chk_obs.setChecked(cfg.obs_status_enabled)

        form.addRow("Screenshots Folder:", r1)
        form.addRow("Recordings Folder:", r2)
        form.addRow("Recording Format:", self.rec_fmt_combo)
        form.addRow("Recording FPS:", self.rec_fps_spin)
        form.addRow("Recording Audio:", self.rec_audio_combo)
        form.addRow("Upload Service:", self.upload_combo)
        form.addRow("ImgBB API Key (DPAPI):", self.imgbb_key)
        form.addRow("Imgur Client-ID (DPAPI):", self.imgur_key)
        form.addRow("", self.chk_clip)
        form.addRow("Max Clipboard Items:", self.clip_max_spin)
        form.addRow("Clipboard Blacklist (exe):", self.clip_excl_edit)
        form.addRow("", self.chk_vpn_ip)
        form.addRow("", self.chk_steam)
        form.addRow("", self.chk_obs)
        self.tabs.addTab(w, "Capture & Privacy")

    @staticmethod
    def _pick_dir(target: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory()
        if d:
            target.setText(d)

    # ------------------------------------------------------------------ Tab 5: Discord Integration
    def _build_discord_tab(self) -> None:
        cfg = self.config_mgr.config
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.setSpacing(8)

        title = QLabel("Discord — Демка и статус профиля")
        title.setObjectName("Heading")
        cv.addWidget(title)

        self.chk_discord_rpc = QCheckBox("Показывать статус Dynamic Island в профиле Discord (Rich Presence)")
        self.chk_discord_rpc.setChecked(cfg.discord_rpc_enabled)
        cv.addWidget(self.chk_discord_rpc)

        form = QFormLayout()
        self.discord_id_edit = QLineEdit(cfg.discord_client_id)
        self.discord_id_edit.setPlaceholderText("Application ID с discord.com/developers/applications")
        form.addRow("Discord Application ID:", self.discord_id_edit)
        cv.addLayout(form)

        row = QHBoxLayout()
        portal_btn = QPushButton("Открыть Discord Developer Portal")
        portal_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://discord.com/developers/applications"))
        )
        copy_btn = QPushButton("Скопировать имя окна")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText("Dynamic Island — Borh-edition"))
        row.addWidget(portal_btn)
        row.addWidget(copy_btn)
        row.addStretch(1)
        cv.addLayout(row)

        if self.discord_service is not None:
            st = self.discord_service.status_text
        elif is_discord_running():
            st = "Discord Desktop запущен (ipc-pipe найден)"
        else:
            st = "Discord Desktop не запущен"
        self.discord_status_lbl = QLabel(f"Статус: {st}")
        self.discord_status_lbl.setWordWrap(True)
        self.discord_status_lbl.setObjectName("Muted")
        cv.addWidget(self.discord_status_lbl)

        hint = QLabel(
            "1) На демке в Discord остров уже видим (если в General выключено «Скрывать от демки»).\n"
            "2) Чтобы статус «Играет в Dynamic Island — Borh-edition» появился в профиле без Application ID: "
            "откройте в Discord Настройки → Зарегистрированные игры → Добавить игру → выберите «Dynamic Island — Borh-edition».\n"
            "3) Для живого Rich Presence с текущим треком Spotify, игрой и таймером создайте App на портале и вставьте Application ID."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        cv.addWidget(hint)

        v.addWidget(card)
        v.addStretch(1)
        self.tabs.addTab(w, "Discord")

    # ------------------------------------------------------------------ Tab 7: Hotkeys
    def _build_hotkeys_tab(self) -> None:
        cfg = self.config_mgr.config
        w = QWidget()
        form = QFormLayout(w)
        self._hk_edits: dict[str, QLineEdit] = {}
        for k, default_val in DEFAULT_HOTKEYS.items():
            le = QLineEdit(cfg.hotkeys.get(k, default_val))
            self._hk_edits[k] = le
            form.addRow(k.replace("_", " ").title() + ":", le)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(w)
        self.tabs.addTab(sc, "Hotkeys")

    # ------------------------------------------------------------------ Tab 7: Developers / Credits
    def _build_developers_tab(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        h_ico = QLabel()
        h_ico.setPixmap(get_pixmap("character", 16, "#F2F5FA", "#60A5FA"))
        title = QLabel("Разработчики" if self.config_mgr.config.language == "ru" else "Developers")
        title.setObjectName("Heading")
        hdr.addWidget(h_ico)
        hdr.addWidget(title, 1)
        cv.addLayout(hdr)

        for name, role in (
            ("Борщ", "Создатель / Ведущий разработчик"),
            ("Новабис Чебурек", "Нищий нищеброд"),
            ("КАМАЗ БЕЛАЗ", "Соразработчик"),
            ("Барс Мэймэй Мася", "Соразработчик"),
        ):
            row = QFrame()
            row.setObjectName("Surface")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 8, 10, 8)
            n_ico = QLabel()
            n_ico.setPixmap(get_pixmap("character", 14, "#F2F5FA", "#FBBF24"))
            n_lbl = QLabel(name)
            n_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")
            r_lbl = QLabel(role)
            r_lbl.setObjectName("Muted")
            rl.addWidget(n_ico)
            rl.addWidget(n_lbl)
            rl.addStretch(1)
            rl.addWidget(r_lbl)
            cv.addWidget(row)

        info = QLabel(
            f"Dynamic Island — Borh-edition (Clean-room Windows Edition)\n"
            f"Data Dir: {self.config_mgr.data_dir}"
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        cv.addWidget(info)

        v.addWidget(card)
        v.addStretch(1)
        tab_title = "Разработчики" if self.config_mgr.config.language == "ru" else "Developers"
        self.tabs.addTab(w, tab_title)

    def _pick_custom_font(self) -> None:
        start_dir = str(Path(self._custom_font_path).parent) if self._custom_font_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл шрифта",
            start_dir,
            "Шрифты (*.ttf *.otf *.ttc);;Все файлы (*.*)",
        )
        if path:
            self._custom_font_path = path
            self.custom_font_lbl.setText(Path(path).name)
            loaded = apply_app_font(None, str(self.font_combo.currentData() or "Segoe UI"), path)
            if loaded:
                self.custom_font_lbl.setText(f"{Path(path).name} ({loaded})")

    def _clear_custom_font(self) -> None:
        self._custom_font_path = ""
        self.custom_font_lbl.setText("Не выбран")

    def update_game_stats_preview(self, cpu_pct: float, ram_pct: float, gpu_pct: float, game_name: str = "") -> None:
        if not hasattr(self, "game_stats_preview_lbl"):
            return
        gpu_s = f"{gpu_pct:.0f}%" if gpu_pct >= 0 else "—"
        txt = f"CPU {cpu_pct:.0f}% • RAM {ram_pct:.0f}% • GPU {gpu_s}"
        if game_name:
            txt += f" ({game_name})"
        if self.game_stats_preview_lbl.text() != txt:
            self.game_stats_preview_lbl.setText(txt)

    # ------------------------------------------------------------------ Save All
    def _save_and_apply(self) -> None:
        cfg = self.config_mgr.config
        cfg.language = self.lang_combo.currentData() or "ru"
        set_language(cfg.language)
        cfg.theme = self.theme_combo.currentData() or "dark"
        cfg.ui_font_family = str(self.font_combo.currentData() or "Segoe UI")
        cfg.custom_font_path = self._custom_font_path
        cfg.monitor_index = self.mon_spin.value()
        cfg.offset_x = self.offx_spin.value()
        cfg.scale = round(self.scale_spin.value() / 100.0, 2)
        cfg.collapsed_opacity = round(self.op_col_spin.value() / 100.0, 2)
        cfg.expanded_opacity = round(self.op_exp_spin.value() / 100.0, 2)
        cfg.pill_mode = self.pill_mode_combo.currentData() or "compact"
        cfg.show_lyrics_in_pill = self.chk_lyrics_pill.isChecked()
        cfg.show_weather_in_pill = self.chk_weather_pill.isChecked()
        cfg.show_game_stats_in_pill = self.chk_game_stats_pill.isChecked()
        cfg.show_game_cpu = self.chk_game_cpu.isChecked()
        cfg.show_game_ram = self.chk_game_ram.isChecked()
        cfg.show_game_gpu = self.chk_game_gpu.isChecked()
        cfg.weather_rain_drip = self.chk_rain_drip.isChecked()
        cfg.edge_equalizer = self.chk_eq.isChecked()
        cfg.liquid_drop = self.chk_liquid_drop.isChecked()
        cfg.auto_hide = self.chk_autohide.isChecked()
        cfg.auto_hide_excluded_apps = [x.strip() for x in self.autohide_excl_edit.text().split(",") if x.strip()]
        cfg.hide_in_fullscreen = self.chk_fullscreen.isChecked()
        cfg.click_through = self.chk_through.isChecked()
        cfg.exclude_from_capture = self.chk_excl_cap.isChecked()
        cfg.autostart = self.chk_autostart.isChecked()
        cfg.hot_reload = self.chk_hot_reload.isChecked()
        cfg.character_id = self._selected_char

        cfg.screenshot_dir = self.scr_dir_edit.text().strip()
        cfg.recording_dir = self.rec_dir_edit.text().strip()
        cfg.recording_format = self.rec_fmt_combo.currentData() or "mp4"
        cfg.recording_fps = self.rec_fps_spin.value()
        cfg.recording_audio_mode = str(self.rec_audio_combo.currentData() or "system")
        am_want = str(self.appearance_combo.currentData() or "weather")
        from ..core.licensing import get_license_manager

        if am_want == "weather" and not get_license_manager().is_feature_allowed("weather"):
            am_want = "color"
        cfg.island_appearance_mode = am_want
        cfg.accent_color = self._sw_accent_val
        cfg.player_card_style = str(self.player_style_combo.currentData() or "mirror")
        cfg.upload_service = self.upload_combo.currentData() or "none"
        cfg.set_secret("imgbb", self.imgbb_key.text())
        cfg.set_secret("imgur", self.imgur_key.text())
        cfg.clipboard_enabled = self.chk_clip.isChecked()
        cfg.clipboard_max_items = self.clip_max_spin.value()
        cfg.clipboard_excluded_apps = [x.strip() for x in self.clip_excl_edit.text().split(",") if x.strip()]
        cfg.vpn_ip_lookup = self.chk_vpn_ip.isChecked()
        cfg.steam_downloads_enabled = self.chk_steam.isChecked()
        cfg.obs_status_enabled = self.chk_obs.isChecked()

        cfg.atmosphere_preview = str(self.atmo_combo.currentData() or "auto")
        cfg.atmosphere_sky_mode = str(self.sky_combo.currentData() or "auto")
        cfg.atmosphere_weather_mode = str(self.weather_combo.currentData() or "auto")
        cfg.atmosphere_moon_mode = str(self.moon_combo.currentData() or "auto")
        cfg.atmosphere_quality = int(self.quality_slider.value())
        cfg.target_fps = int(self.fps_combo.currentData() or 120)
        cfg.remove_power_limits = bool(self.chk_remove_power_limits.isChecked())
        cfg.show_dev_text_tools = self.chk_dev_tools.isChecked()

        cfg.discord_rpc_enabled = self.chk_discord_rpc.isChecked()
        cfg.discord_client_id = self.discord_id_edit.text().strip()

        for k, le in self._hk_edits.items():
            cfg.hotkeys[k] = le.text().strip()

        self.config_mgr.save()
        try:
            proj_root = Path(__file__).resolve().parent.parent.parent
            set_autostart(cfg.autostart, build_autostart_command(proj_root))
        except Exception:
            pass
        self._saved = True
        self.config_applied.emit()
        self.accept()


class _MiniPreviewCanvas(QWidget):
    """Lightweight live canvas rendering a miniature island pill & player card."""

    def __init__(self, state: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = dict(state)
        self.setFixedHeight(86)
        self.setMinimumWidth(280)

    def update_state(self, state: dict) -> None:
        self._state = dict(state)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        st = self._state

        theme = str(st.get("theme", "dark") or "dark").lower()
        is_light = theme == "light"
        accent = QColor(str(st.get("accent_color", "#3B82F6") or "#3B82F6"))
        if not accent.isValid():
            accent = QColor("#3B82F6")

        scale = max(0.80, min(1.35, float(st.get("scale", 1.0) or 1.0)))
        op_col = max(0.25, min(1.0, float(st.get("collapsed_opacity", 0.92) or 0.92)))
        op_exp = max(0.35, min(1.0, float(st.get("expanded_opacity", 0.96) or 0.96)))
        player_style = str(st.get("player_card_style", "mirror") or "mirror").lower()
        pill_mode = str(st.get("pill_mode", "miniplayer") or "miniplayer").lower()
        app_mode = str(st.get("island_appearance_mode", "weather") or "weather").lower()
        eq_on = bool(st.get("edge_equalizer", True))

        # Subtle backdrop grid/frame
        bg_rect = QRectF(1.0, 1.0, w - 2.0, h - 2.0)
        p.setPen(QPen(QColor(255, 255, 255, 26) if not is_light else QColor(0, 0, 0, 26), 1.0))
        p.setBrush(QColor("#0B1020") if not is_light else QColor("#E2E8F0"))
        p.drawRoundedRect(bg_rect, 10.0, 10.0)

        # Left: miniature collapsed pill
        pill_w = min(130.0, 96.0 * (0.75 + 0.25 * scale))
        pill_h = min(30.0, 22.0 * (0.75 + 0.25 * scale))
        pill_rect = QRectF(14.0, (h - pill_h) * 0.5, pill_w, pill_h)
        pill_bg = QColor("#0F172A" if not is_light else "#FFFFFF")
        if app_mode == "color":
            pill_bg = QColor(accent.darker(180) if not is_light else accent.lighter(165))
        pill_bg.setAlphaF(op_col)

        p.setBrush(pill_bg)
        p.setPen(QPen(accent if eq_on else QColor(255, 255, 255, 45), 1.5 if eq_on else 1.0))
        p.drawRoundedRect(pill_rect, pill_h * 0.5, pill_h * 0.5)

        p.setPen(QColor("#F8FAFC" if not is_light else "#0F172A"))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        pill_txt = "♪ Blip" if pill_mode == "compact" else ("Track…" if pill_mode == "track" else "▶ 01:24")
        p.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, pill_txt)

        # Right: miniature expanded player card
        card_x = pill_rect.right() + 14.0
        card_w = max(120.0, w - card_x - 14.0)
        card_h = min(68.0, 52.0 * (0.78 + 0.22 * scale))
        card_rect = QRectF(card_x, (h - card_h) * 0.5, card_w, card_h)

        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 14.0, 14.0)
        p.save()
        p.setClipPath(card_path)

        if player_style == "fill":
            grad = QLinearGradient(card_rect.topLeft(), card_rect.bottomRight())
            c1 = QColor(accent)
            c1.setAlphaF(op_exp)
            c2 = QColor(accent.darker(175))
            c2.setAlphaF(op_exp)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            p.fillPath(card_path, grad)
        elif player_style == "compact":
            c_bg = QColor("#111827" if not is_light else "#F8FAFC")
            c_bg.setAlphaF(op_exp)
            p.fillPath(card_path, c_bg)
        else:
            # mirror
            grad = QLinearGradient(card_rect.topLeft(), card_rect.bottomRight())
            c1 = QColor("#1E293B" if not is_light else "#FFFFFF")
            c1.setAlphaF(op_exp)
            c2 = QColor(accent.darker(190) if not is_light else accent.lighter(170))
            c2.setAlphaF(op_exp)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            p.fillPath(card_path, grad)

        # Mini cover art square
        cov_sz = card_h - 16.0
        cov_rect = QRectF(card_rect.left() + 8.0, card_rect.top() + 8.0, cov_sz, cov_sz)
        p.setBrush(accent)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(cov_rect, 7.0, 7.0)

        # Mini track text
        txt_x = cov_rect.right() + 8.0
        txt_col = QColor("#FFFFFF" if (not is_light or player_style == "fill") else "#0F172A")
        p.setPen(txt_col)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(QRectF(txt_x, card_rect.top() + 9.0, card_rect.right() - txt_x - 8.0, 16.0), Qt.AlignmentFlag.AlignVCenter, f"Style: {player_style.title()}")
        sub_col = QColor(txt_col)
        sub_col.setAlpha(185)
        p.setPen(sub_col)
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(
            QRectF(txt_x, card_rect.top() + 26.0, card_rect.right() - txt_x - 8.0, 14.0),
            Qt.AlignmentFlag.AlignVCenter,
            f"Scale {int(round(scale * 100))}% • Op {int(round(op_col * 100))}%/{int(round(op_exp * 100))}%",
        )
        p.restore()

        p.setPen(QPen(accent if eq_on else QColor(255, 255, 255, 45), 1.4 if eq_on else 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(card_rect, 14.0, 14.0)


class _SettingsLivePreviewWidget(QFrame):
    """Footer card inside SettingsWindow showing live visual changes before saving."""

    def __init__(self, config_mgr: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        cfg = config_mgr.config
        self._base_state = {
            "theme": getattr(cfg, "theme", "dark"),
            "accent_color": getattr(cfg, "accent_color", "#3B82F6"),
            "scale": getattr(cfg, "scale", 1.0),
            "collapsed_opacity": getattr(cfg, "collapsed_opacity", 0.92),
            "expanded_opacity": getattr(cfg, "expanded_opacity", 0.96),
            "player_card_style": getattr(cfg, "player_card_style", "mirror"),
            "pill_mode": getattr(cfg, "pill_mode", "miniplayer"),
            "island_appearance_mode": getattr(cfg, "island_appearance_mode", "weather"),
            "edge_equalizer": getattr(cfg, "edge_equalizer", True),
            "liquid_drop": getattr(cfg, "liquid_drop", True),
        }
        self._cur_state = dict(self._base_state)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        title = QLabel("👁 Живой предпросмотр изменений" if getattr(cfg, "language", "ru") == "ru" else "👁 Live Visual Preview")
        title.setStyleSheet("font-weight: 700; font-size: 12px;")
        self._info_lbl = QLabel("")
        self._info_lbl.setObjectName("Muted")
        self._info_lbl.setStyleSheet("font-size: 11px;")
        hdr.addWidget(title)
        hdr.addStretch(1)
        hdr.addWidget(self._info_lbl)
        lay.addLayout(hdr)

        self._canvas = _MiniPreviewCanvas(self._cur_state, self)
        lay.addWidget(self._canvas)
        self._refresh_label()

    def apply_patch(self, patch: dict) -> None:
        if not patch:
            self._cur_state = dict(self._base_state)
        else:
            self._cur_state.update(patch)
        self._canvas.update_state(self._cur_state)
        self._refresh_label()

    def _refresh_label(self) -> None:
        st = self._cur_state
        self._info_lbl.setText(
            f"Плеер: {st.get('player_card_style', 'mirror')} • "
            f"Пилюля: {st.get('pill_mode', 'miniplayer')} • "
            f"Масштаб: {int(round(float(st.get('scale', 1.0)) * 100))}% • "
            f"Прозрачность: {int(round(float(st.get('collapsed_opacity', 0.92)) * 100))}% / {int(round(float(st.get('expanded_opacity', 0.96)) * 100))}%"
        )

