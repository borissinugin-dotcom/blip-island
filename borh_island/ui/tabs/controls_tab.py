"""Controls tab: Output/Mic volume & device switcher, Brightness, Privacy dots, Wi-Fi/BT/VPN, Dark theme, Power actions."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QFileInfo, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.config import DEFAULT_HOTKEYS, ConfigManager
from ...core.i18n import set_language, tr
from ...core.native_bridge import NativeBridge
from ...core.win32_api import (
    build_autostart_command,
    is_windows_dark_theme,
    lock_workstation,
    restart_computer,
    set_autostart,
    shutdown_computer,
    sleep_computer,
    toggle_windows_dark_theme,
)
from ...services.system_service import SystemStatsSnapshot
from ..atmosphere import ATMO_MODES, MOON_MODES, SKY_MODES, WEATHER_MODES
from ..icons import get_icon, get_pixmap
from ..theme import FIVE_PRESET_FONTS, ThemePalette, apply_app_font
from .home_tab import _make_circular_app_icon

_PROFILE_I18N_MAP = {
    "Стандартный": "profile_default",
    "Игра + Discord": "profile_game_discord",
    "Музыка и фокус": "profile_music_focus",
    "Ночной режим": "profile_night",
}


def _localize_profile_name(pname: str) -> str:
    key = _PROFILE_I18N_MAP.get(pname)
    return tr(key) if key else pname


class ControlsTab(QWidget):
    """Unified Settings & Controls hub: Audio/System, Graphics/Atmosphere, and General/Hotkeys."""

    devices_loaded = pyqtSignal(dict, dict, dict, int)  # (devs, out_vol, mic_vol, brightness)
    app_sessions_loaded = pyqtSignal(list)
    profile_applied = pyqtSignal(str, int)
    theme_toggled = pyqtSignal()
    blue_filter_toggled = pyqtSignal(bool)
    settings_requested = pyqtSignal()
    atmosphere_changed = pyqtSignal()
    color_previewed = pyqtSignal(str)
    config_applied = pyqtSignal()
    restart_app_requested = pyqtSignal()

    def __init__(self, bridge: NativeBridge, config_mgr: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.config_mgr = config_mgr
        self._icon_fg = "#F2F5FA"
        self._accent_hex = "#3B82F6"
        self._pending_power: tuple[str, float] | None = None
        self._app_icon_cache: dict[str, QPixmap] = {}
        self._icon_provider = QFileIconProvider()
        self._mixer_rows: dict[str, dict] = {}
        self._mixer_sessions_data: list[dict] = []
        self._last_devs: dict = {}
        self._last_ov: dict = {"volume": 50, "muted": False}
        self._last_mv: dict = {"volume": 80, "muted": False}
        self._last_br: int = -1
        self._last_snap: SystemStatsSnapshot | None = None
        self._vol_debounce_pending: dict[str, int] = {}
        self._vol_debounce_timer = QTimer(self)
        self._vol_debounce_timer.setSingleShot(True)
        self._vol_debounce_timer.setInterval(45)
        self._vol_debounce_timer.timeout.connect(self._flush_pending_app_volumes)
        self._custom_font_path = str(getattr(self.config_mgr.config, "custom_font_path", "") or "").strip()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self.inner_tabs = QTabWidget(self)
        self.inner_tabs.setDocumentMode(True)
        self.inner_tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: transparent; }"
            "QTabBar::tab { background: rgba(255,255,255,0.06); color: #CBD5E1; border-radius: 11px; "
            "padding: 4px 12px; margin-right: 4px; font-size: 11px; font-weight: 600; }"
            "QTabBar::tab:selected { background: rgba(59,130,246,0.28); color: #FFFFFF; }"
        )
        outer.addWidget(self.inner_tabs, 1)

        # Sub-tab 1: Audio & System (2 balanced columns)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        scroll.setWidget(host)
        sys_cols = QHBoxLayout(host)
        sys_cols.setContentsMargins(4, 4, 4, 4)
        sys_cols.setSpacing(8)
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        sys_cols.addLayout(left_col, 1)
        sys_cols.addLayout(right_col, 1)

        # --- Output Audio Card ---
        out_card = QFrame()
        out_card.setObjectName("Card")
        ov = QVBoxLayout(out_card)
        ov.setContentsMargins(10, 8, 10, 10)
        ov.setSpacing(6)

        oh = QHBoxLayout()
        oh.setSpacing(6)
        self.out_hdr_ico = QLabel()
        self.out_hdr_ico.setFixedSize(16, 16)
        self.out_title_lbl = QLabel()
        self.out_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.out_combo = QComboBox()
        self.out_combo.setMinimumWidth(90)
        self.out_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        oh.addWidget(self.out_hdr_ico)
        oh.addWidget(self.out_title_lbl)
        oh.addWidget(self.out_combo, 1)
        ov.addLayout(oh)

        oslider_row = QHBoxLayout()
        self.out_mute_btn = QPushButton("")
        self.out_mute_btn.setFixedSize(34, 28)
        self.out_mute_btn.setStyleSheet(
            "min-width: 34px; max-width: 34px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        self.out_mute_btn.setCheckable(True)
        self.out_mute_btn.clicked.connect(self._toggle_out_mute)
        self.out_slider = QSlider(Qt.Orientation.Horizontal)
        self.out_slider.setRange(0, 100)
        self.out_slider.valueChanged.connect(self._on_out_volume_changed)
        self.out_pct_lbl = QLabel("50%")
        self.out_pct_lbl.setMinimumWidth(42)
        self.out_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        oslider_row.addWidget(self.out_mute_btn)
        oslider_row.addWidget(self.out_slider, 1)
        oslider_row.addWidget(self.out_pct_lbl)
        ov.addLayout(oslider_row)
        left_col.addWidget(out_card)

        # --- Per-App Audio Mixer & Profiles Card ---
        mixer_card = QFrame()
        mixer_card.setObjectName("Card")
        mx = QVBoxLayout(mixer_card)
        mx.setContentsMargins(10, 8, 10, 10)
        mx.setSpacing(6)

        mx_hdr = QHBoxLayout()
        mx_hdr.setSpacing(6)
        self.mixer_hdr_ico = QLabel()
        self.mixer_hdr_ico.setFixedSize(16, 16)
        self.mixer_title_lbl = QLabel()
        self.mixer_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.mixer_status_lbl = QLabel("")
        self.mixer_status_lbl.setObjectName("Muted")
        self.mixer_status_lbl.setStyleSheet("font-size: 11px;")
        self.mixer_refresh_btn = self._make_circle_btn(self.refresh_app_sessions)
        mx_hdr.addWidget(self.mixer_hdr_ico)
        mx_hdr.addWidget(self.mixer_title_lbl)
        mx_hdr.addStretch(1)
        mx_hdr.addWidget(self.mixer_status_lbl)
        mx_hdr.addWidget(self.mixer_refresh_btn)
        mx.addLayout(mx_hdr)

        prof_row = QHBoxLayout()
        prof_row.setSpacing(4)
        self.mixer_profile_combo = QComboBox()
        self.mixer_profile_combo.setMinimumHeight(28)
        self.mixer_profile_combo.setMinimumWidth(90)
        self.mixer_profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mixer_profile_combo.currentIndexChanged.connect(self._on_mixer_profile_selected)
        self.mixer_apply_btn = QPushButton("")
        self.mixer_apply_btn.setFixedHeight(28)
        self.mixer_apply_btn.setStyleSheet(
            "min-height: 28px; max-height: 28px; padding: 0px 10px; font-size: 11px; border-radius: 14px;"
        )
        self.mixer_apply_btn.clicked.connect(lambda: self.apply_mixer_profile())
        self.mixer_save_btn = self._make_circle_btn(self._save_current_to_profile)
        self.mixer_new_btn = self._make_circle_btn(self._toggle_new_profile_row)
        self.mixer_del_btn = self._make_circle_btn(self._delete_mixer_profile)
        prof_row.addWidget(self.mixer_profile_combo, 1)
        prof_row.addWidget(self.mixer_apply_btn)
        prof_row.addWidget(self.mixer_save_btn)
        prof_row.addWidget(self.mixer_new_btn)
        prof_row.addWidget(self.mixer_del_btn)
        mx.addLayout(prof_row)

        self.mixer_new_row = QWidget()
        new_prof_lay = QHBoxLayout(self.mixer_new_row)
        new_prof_lay.setContentsMargins(0, 0, 0, 0)
        new_prof_lay.setSpacing(5)
        self.mixer_new_edit = QLineEdit()
        self.mixer_new_edit.returnPressed.connect(self._confirm_new_profile)
        self.mixer_new_ok_btn = QPushButton("")
        self.mixer_new_ok_btn.setFixedHeight(28)
        self.mixer_new_ok_btn.setStyleSheet(
            "min-height: 28px; max-height: 28px; padding: 0px 10px; font-size: 11px; border-radius: 14px;"
        )
        self.mixer_new_ok_btn.clicked.connect(self._confirm_new_profile)
        new_prof_lay.addWidget(self.mixer_new_edit, 1)
        new_prof_lay.addWidget(self.mixer_new_ok_btn)
        self.mixer_new_row.hide()
        mx.addWidget(self.mixer_new_row)

        self.mixer_sessions_host = QWidget()
        self.mixer_sessions_host.setStyleSheet("background: transparent;")
        self.mixer_sessions_lay = QVBoxLayout(self.mixer_sessions_host)
        self.mixer_sessions_lay.setContentsMargins(0, 2, 0, 0)
        self.mixer_sessions_lay.setSpacing(5)
        self.mixer_empty_lbl = QLabel()
        self.mixer_empty_lbl.setObjectName("Muted")
        self.mixer_empty_lbl.setWordWrap(True)
        self.mixer_empty_lbl.setStyleSheet("font-size: 11px; padding: 4px 2px;")
        self.mixer_sessions_lay.addWidget(self.mixer_empty_lbl)
        mx.addWidget(self.mixer_sessions_host)
        right_col.addWidget(mixer_card)

        # --- Mic Audio & Brightness Card ---
        mic_card = QFrame()
        mic_card.setObjectName("Card")
        mv = QVBoxLayout(mic_card)
        mv.setContentsMargins(10, 8, 10, 10)
        mv.setSpacing(6)

        mh = QHBoxLayout()
        mh.setSpacing(6)
        self.mic_hdr_ico = QLabel()
        self.mic_hdr_ico.setFixedSize(16, 16)
        self.mic_title_lbl = QLabel()
        self.mic_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(90)
        self.mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        mh.addWidget(self.mic_hdr_ico)
        mh.addWidget(self.mic_title_lbl)
        mh.addWidget(self.mic_combo, 1)
        mv.addLayout(mh)

        ms_row = QHBoxLayout()
        self.mic_mute_btn = QPushButton("")
        self.mic_mute_btn.setFixedSize(34, 28)
        self.mic_mute_btn.setStyleSheet(
            "min-width: 34px; max-width: 34px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        self.mic_mute_btn.setCheckable(True)
        self.mic_mute_btn.clicked.connect(self._toggle_mic_mute)
        self.mic_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_slider.setRange(0, 100)
        self.mic_slider.valueChanged.connect(self._on_mic_volume_changed)
        self.mic_pct_lbl = QLabel("80%")
        self.mic_pct_lbl.setMinimumWidth(42)
        self.mic_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ms_row.addWidget(self.mic_mute_btn)
        ms_row.addWidget(self.mic_slider, 1)
        ms_row.addWidget(self.mic_pct_lbl)
        mv.addLayout(ms_row)

        br_row = QHBoxLayout()
        self.bright_hdr_ico = QLabel()
        self.bright_hdr_ico.setFixedSize(16, 16)
        self.bright_slider = QSlider(Qt.Orientation.Horizontal)
        self.bright_slider.setRange(0, 100)
        self.bright_slider.valueChanged.connect(self._on_brightness_changed)
        self.bright_lbl = QLabel()
        self.bright_lbl.setMinimumWidth(42)
        self.bright_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        br_row.addWidget(self.bright_hdr_ico)
        br_row.addWidget(self.bright_slider, 1)
        br_row.addWidget(self.bright_lbl)
        mv.addLayout(br_row)
        left_col.addWidget(mic_card)

        # --- Connectivity & Privacy ---
        net_card = QFrame()
        net_card.setObjectName("Card")
        nv = QVBoxLayout(net_card)
        nv.setContentsMargins(10, 8, 10, 10)
        nv.setSpacing(5)
        priv_row = QHBoxLayout()
        priv_row.setSpacing(6)
        self.privacy_ico = QLabel()
        self.privacy_ico.setFixedSize(15, 15)
        self.privacy_lbl = QLabel()
        priv_row.addWidget(self.privacy_ico)
        priv_row.addWidget(self.privacy_lbl, 1)
        nv.addLayout(priv_row)

        self.wifi_lbl = QLabel()
        nv.addWidget(self.wifi_lbl)

        vpn_row = QHBoxLayout()
        vpn_row.setSpacing(6)
        self.vpn_ico = QLabel()
        self.vpn_ico.setFixedSize(15, 15)
        self.vpn_lbl = QLabel()
        vpn_row.addWidget(self.vpn_ico)
        vpn_row.addWidget(self.vpn_lbl, 1)
        nv.addLayout(vpn_row)

        self.bt_list = QListWidget()
        self.bt_list.setMaximumHeight(85)
        nv.addWidget(self.bt_list)
        right_col.addWidget(net_card)

        # --- Theme & Power ---
        pwr_card = QFrame()
        pwr_card.setObjectName("Card")
        pv = QVBoxLayout(pwr_card)
        pv.setContentsMargins(10, 8, 10, 10)
        pv.setSpacing(6)

        sys_row = QHBoxLayout()
        sys_row.setSpacing(6)
        self.win_theme_btn = QPushButton("")
        self.win_theme_btn.clicked.connect(self._toggle_win_theme)
        self.autostart_btn = QPushButton("")
        self.autostart_btn.setCheckable(True)
        self.autostart_btn.setChecked(bool(self.config_mgr.config.autostart))
        self.autostart_btn.clicked.connect(self._toggle_autostart)
        self.blue_filter_btn = QPushButton("")
        self.blue_filter_btn.setCheckable(True)
        self.blue_filter_btn.setChecked(bool(self.config_mgr.config.blue_light_filter))
        self.blue_filter_btn.clicked.connect(self._toggle_blue_filter)
        self.open_settings_btn = QPushButton("")
        self.open_settings_btn.clicked.connect(lambda: self.inner_tabs.setCurrentIndex(1))
        sys_row.addWidget(self.win_theme_btn, 1)
        sys_row.addWidget(self.autostart_btn, 1)
        pv.addLayout(sys_row)
        sys_row2 = QHBoxLayout()
        sys_row2.setSpacing(6)
        sys_row2.addWidget(self.blue_filter_btn, 1)
        sys_row2.addWidget(self.open_settings_btn, 1)
        pv.addLayout(sys_row2)

        pgrid = QGridLayout()
        pgrid.setSpacing(6)
        self._power_buttons: list[tuple[QPushButton, str, str]] = []
        for idx, (act_id, ico_name) in enumerate(
            (("lock", "lock"), ("sleep", "sleep"), ("restart", "restart"), ("shutdown", "power"))
        ):
            b = QPushButton("")
            b.clicked.connect(lambda _, a=act_id: self._power_clicked(a))
            pgrid.addWidget(b, idx // 2, idx % 2)
            self._power_buttons.append((b, act_id, ico_name))
        pv.addLayout(pgrid)

        self.power_hint = QLabel("")
        self.power_hint.setObjectName("Muted")
        self.power_hint.hide()
        pv.addWidget(self.power_hint)
        right_col.addWidget(pwr_card)
        left_col.addStretch(1)
        right_col.addStretch(1)

        self.inner_tabs.addTab(scroll, tr("settings_audio_system"))
        self._build_graphics_atmo_subtab()
        self._build_general_hotkeys_subtab()

        self.devices_loaded.connect(self._on_devices_loaded)
        self.app_sessions_loaded.connect(self._on_app_sessions_loaded)
        self.profile_applied.connect(self._on_profile_applied)
        self.out_combo.currentIndexChanged.connect(self._on_out_dev_changed)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_dev_changed)

        self.retranslate_ui()
        self.refresh_hardware()

    def _make_circle_btn(self, slot) -> QPushButton:
        b = QPushButton("")
        b.setFixedSize(28, 28)
        b.setStyleSheet(
            "min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        b.clicked.connect(slot)
        return b

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh_app_sessions()

    def apply_palette(self, pal: ThemePalette) -> None:
        pal_sig = (pal.text_primary, pal.accent)
        if getattr(self, "_last_ctrl_pal_sig", None) == pal_sig:
            return
        self._last_ctrl_pal_sig = pal_sig
        self._icon_fg = pal.text_primary
        self._accent_hex = pal.accent
        self._refresh_stickers()

    def _build_graphics_atmo_subtab(self) -> None:
        cfg = self.config_mgr.config
        sc = QScrollArea(self)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        sc.setWidget(host)

        from ...core.licensing import APP_VERSION
        from ...services.updater_service import check_for_updates

        outer = QVBoxLayout(host)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(8)

        # Prominent top Update Check banner at the very top of Graphics & Atmosphere
        upd_card = QFrame()
        upd_card.setObjectName("Card")
        uv = QVBoxLayout(upd_card)
        uv.setContentsMargins(12, 10, 12, 10)
        uv.setSpacing(6)
        init_upd = check_for_updates()
        latest_txt = (
            f"v{init_upd.latest_version}"
            if getattr(init_upd, "latest_version", "")
            else f"v{APP_VERSION}"
        )
        upd_row = QHBoxLayout()
        upd_row.setContentsMargins(0, 0, 0, 0)
        upd_row.setSpacing(8)

        upd_col = QVBoxLayout()
        upd_col.setContentsMargins(0, 0, 0, 0)
        upd_col.setSpacing(4)
        self.check_upd_btn = QPushButton(
            f"Проверить обновления  •  Установлена: v{APP_VERSION}  |  Актуальная: {latest_txt}"
            if cfg.language == "ru"
            else f"Check Updates  •  Installed: v{APP_VERSION}  |  Latest: {latest_txt}"
        )
        self.check_upd_btn.setObjectName("Primary")
        self.check_upd_btn.setFixedHeight(40)
        self.check_upd_btn.setStyleSheet("font-size: 13px; font-weight: 700; border-radius: 12px;")
        self.check_upd_btn.clicked.connect(self._on_check_updates)
        upd_col.addWidget(self.check_upd_btn)
        self.upd_status_lbl = QLabel(
            f"Текущая версия: v{APP_VERSION}  •  Последняя доступная: {latest_txt}"
            if cfg.language == "ru"
            else f"Installed version: v{APP_VERSION}  •  Latest available: {latest_txt}"
        )
        self.upd_status_lbl.setWordWrap(True)
        self.upd_status_lbl.setStyleSheet("font-size: 11px; color: #94A3B8;")
        upd_col.addWidget(self.upd_status_lbl)
        upd_row.addLayout(upd_col, 1)

        sup_col = QVBoxLayout()
        sup_col.setContentsMargins(0, 0, 0, 0)
        sup_col.setSpacing(4)
        self.support_btn = QPushButton(
            "Техподдержка" if cfg.language == "ru" else "Support"
        )
        self.support_btn.setObjectName("Primary")
        self.support_btn.setFixedHeight(40)
        self.support_btn.setStyleSheet("font-size: 13px; font-weight: 700; border-radius: 12px;")
        self.support_btn.clicked.connect(self._on_support_clicked)
        sup_col.addWidget(self.support_btn)
        sup_sub = QLabel(
            "Для идей и предложений" if cfg.language == "ru" else "For ideas & suggestions"
        )
        sup_sub.setWordWrap(True)
        sup_sub.setStyleSheet("font-size: 11px; color: #94A3B8;")
        sup_col.addWidget(sup_sub)
        upd_row.addLayout(sup_col, 1)

        rst_col = QVBoxLayout()
        rst_col.setContentsMargins(0, 0, 0, 0)
        rst_col.setSpacing(4)
        self.restart_app_btn = QPushButton("Перезагрузить" if cfg.language == "ru" else "Reload Blip")
        self.restart_app_btn.setFixedHeight(40)
        self.restart_app_btn.setStyleSheet("font-size: 12px; font-weight: 700; border-radius: 12px;")
        self.restart_app_btn.clicked.connect(self.restart_app_requested.emit)
        rst_col.addWidget(self.restart_app_btn)
        rst_sub = QLabel("Быстрый рестарт UI" if cfg.language == "ru" else "Quick UI reload")
        rst_sub.setWordWrap(True)
        rst_sub.setStyleSheet("font-size: 11px; color: #94A3B8;")
        rst_col.addWidget(rst_sub)
        upd_row.addLayout(rst_col)

        uv.addLayout(upd_row)
        outer.addWidget(upd_card)

        cols = QHBoxLayout()
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(8)
        outer.addLayout(cols, 1)
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        cols.addLayout(left_col, 1)
        cols.addLayout(right_col, 1)

        # Left card: Graphics, Scale & Physics
        gfx_card = QFrame()
        gfx_card.setObjectName("Card")
        gv = QVBoxLayout(gfx_card)
        gv.setContentsMargins(10, 8, 10, 10)
        gv.setSpacing(6)
        self.gfx_title_lbl = QLabel("Графика и эффекты" if cfg.language == "ru" else "Graphics & Effects")
        self.gfx_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        gv.addWidget(self.gfx_title_lbl)

        gform = QFormLayout()
        gform.setSpacing(6)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark (Тёмная)", "dark")
        self.theme_combo.addItem("Light (Светлая)", "light")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(cfg.theme)))

        self.mon_spin = QSpinBox()
        self.mon_spin.setRange(0, max(0, len(QGuiApplication.screens()) - 1))
        self.mon_spin.setValue(cfg.monitor_index)

        self.offx_spin = QSpinBox()
        self.offx_spin.setRange(-1500, 1500)
        self.offx_spin.setValue(cfg.offset_x)

        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(75, 180)
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

        gform.addRow("Тема:" if cfg.language == "ru" else "Theme:", self.theme_combo)
        gform.addRow("Масштаб:" if cfg.language == "ru" else "Scale:", self.scale_spin)
        gform.addRow("Прозр. пилюли:" if cfg.language == "ru" else "Pill opacity:", self.op_col_spin)
        gform.addRow("Прозр. панели:" if cfg.language == "ru" else "Panel opacity:", self.op_exp_spin)
        gform.addRow("Монитор / Offset X:", self._pair_row(self.mon_spin, self.offx_spin))
        gv.addLayout(gform)

        self.chk_liquid_drop = QCheckBox("Liquid Drop физика при подходе курсора" if cfg.language == "ru" else "Liquid Drop physics")
        self.chk_liquid_drop.setChecked(cfg.liquid_drop)
        self.chk_eq = QCheckBox("Эквалайзер по краям пилюли" if cfg.language == "ru" else "Edge spectrum equalizer")
        self.chk_eq.setChecked(cfg.edge_equalizer)
        self.chk_rain_drip = QCheckBox("Реакция персонажа на дождь/снег" if cfg.language == "ru" else "Weather drip reaction")
        self.chk_rain_drip.setChecked(cfg.weather_rain_drip)
        gv.addWidget(self.chk_liquid_drop)
        gv.addWidget(self.chk_eq)
        gv.addWidget(self.chk_rain_drip)

        # Big prominent Glass Style selector (Liquid Glass / Frost Glass / Off)
        glass_lbl = QLabel("Стиль стекла интерфейса:" if cfg.language == "ru" else "Interface Glass Style:")
        glass_lbl.setStyleSheet("font-weight: 700; font-size: 12px; margin-top: 4px;")
        gv.addWidget(glass_lbl)
        glass_row = QHBoxLayout()
        glass_row.setSpacing(6)
        self._glass_btns: dict[str, QPushButton] = {}
        glass_specs = (
            ("frost", "Frost Glass"),
            ("off", "Выкл (Чёрный)" if cfg.language == "ru" else "Off (Dark)"),
        )
        cur_gm = str(getattr(cfg, "glass_mode", "frost") or "frost").lower()
        if cur_gm == "liquid":
            cur_gm = "frost"
        for gm_key, gm_label in glass_specs:
            gb = QPushButton(gm_label)
            gb.setCheckable(True)
            gb.setChecked(cur_gm == gm_key)
            gb.setFixedHeight(36)
            gb.setStyleSheet("font-size: 11px; font-weight: 700; border-radius: 12px; padding: 0px 8px;")
            gb.clicked.connect(lambda _=False, k=gm_key: self._on_glass_mode_picked(k))
            glass_row.addWidget(gb, 1)
            self._glass_btns[gm_key] = gb
        gv.addLayout(glass_row)

        # Glass quality presets (low / medium / high / ultra) for Liquid & Frost + Benchmark auto-tune
        ref_lbl = QLabel("Качество стекла (Liquid / Frost):" if cfg.language == "ru" else "Glass Quality (Liquid / Frost):")
        ref_lbl.setStyleSheet("font-weight: 700; font-size: 11px; margin-top: 4px;")
        gv.addWidget(ref_lbl)
        ref_row = QHBoxLayout()
        ref_row.setSpacing(4)
        self._ref_q_btns: dict[str, QPushButton] = {}
        ref_specs = (
            ("low", "Низк." if cfg.language == "ru" else "Low"),
            ("medium", "Средн." if cfg.language == "ru" else "Med"),
            ("high", "Высок." if cfg.language == "ru" else "High"),
            ("ultra", "Ультра" if cfg.language == "ru" else "Ultra"),
        )
        cur_rq = str(getattr(cfg, "refraction_quality", "ultra") or "ultra").lower()
        for rq_key, rq_label in ref_specs:
            rb = QPushButton(rq_label)
            rb.setCheckable(True)
            rb.setChecked(cur_rq == rq_key)
            rb.setFixedHeight(30)
            rb.setStyleSheet("font-size: 10px; font-weight: 700; border-radius: 10px; padding: 0px 6px;")
            rb.clicked.connect(lambda _=False, k=rq_key: self._on_refraction_quality_picked(k))
            ref_row.addWidget(rb, 1)
            self._ref_q_btns[rq_key] = rb
        gv.addLayout(ref_row)

        bench_row = QHBoxLayout()
        bench_row.setSpacing(6)
        self.bench_btn = QPushButton("Бенчмарк стекла" if cfg.language == "ru" else "Run Glass Benchmark")
        self.bench_btn.setFixedHeight(30)
        self.bench_btn.setStyleSheet("font-size: 11px; font-weight: 700; border-radius: 10px; padding: 0px 10px;")
        self.bench_btn.clicked.connect(self._on_run_refraction_benchmark)
        last_ms = float(getattr(cfg, "refraction_bench_ms", 0.0) or 0.0)
        self.bench_res_lbl = QLabel(f"{last_ms:.1f} мс" if last_ms > 0.0 else "—")
        self.bench_res_lbl.setStyleSheet("font-size: 11px; color: #94A3B8;")
        bench_row.addWidget(self.bench_btn, 1)
        bench_row.addWidget(self.bench_res_lbl)
        gv.addLayout(bench_row)

        self.apply_gfx_btn = QPushButton("Применить графику" if cfg.language == "ru" else "Apply Graphics")
        self.apply_gfx_btn.clicked.connect(self._save_consolidated_settings)
        gv.addWidget(self.apply_gfx_btn)

        extra_form = QFormLayout()
        extra_form.setSpacing(5)
        self.player_style_combo = QComboBox()
        for pk, plbl in (("mirror", "Mirror (Зеркало)"), ("fill", "Fill (Заливка)"), ("compact", "Compact (Компакт)")):
            self.player_style_combo.addItem(plbl, pk)
        self.player_style_combo.setCurrentIndex(max(0, self.player_style_combo.findData(getattr(cfg, "player_card_style", "mirror"))))

        self.rec_audio_combo = QComboBox()
        for ak, albl in (("none", "Без звука"), ("system", "Звук компьютера"), ("both", "Компьютер и микрофон")):
            self.rec_audio_combo.addItem(albl, ak)
        self.rec_audio_combo.setCurrentIndex(max(0, self.rec_audio_combo.findData(getattr(cfg, "recording_audio_mode", "system"))))
        extra_form.addRow("Стиль плеера:" if cfg.language == "ru" else "Player style:", self.player_style_combo)
        extra_form.addRow("Звук записи:" if cfg.language == "ru" else "Recording audio:", self.rec_audio_combo)
        gv.addLayout(extra_form)
        left_col.addWidget(gfx_card)

        # Blip PRO subscription, badge visibility & auto-updates card
        pro_card = QFrame()
        pro_card.setObjectName("Card")
        pv = QVBoxLayout(pro_card)
        pv.setContentsMargins(12, 10, 12, 10)
        pv.setSpacing(6)
        from ...core.licensing import APP_VERSION, format_trial_remaining, get_license_manager

        st = get_license_manager().get_status()
        self.pro_status_lbl = QLabel(
            f"Подписка: <b>{st.tier}</b>"
            + (f" (осталось {format_trial_remaining(st.trial_days_left)})" if st.tier == "TRIAL" else "")
        )
        self.pro_status_lbl.setStyleSheet("font-size: 12px;")
        pv.addWidget(self.pro_status_lbl)

        self.open_pro_btn = QPushButton("Открыть Blip Island PRO / Оплата QR")
        self.open_pro_btn.setFixedHeight(32)
        self.open_pro_btn.clicked.connect(self._open_pro_dialog)
        pv.addWidget(self.open_pro_btn)

        self.chk_hide_pro = QCheckBox("Скрыть надпись PRO рядом с Blip Island")
        self.chk_hide_pro.setChecked(bool(getattr(cfg, "hide_pro_badge", False)))
        self.chk_hide_pro.setEnabled(st.is_pro)
        self.chk_hide_pro.toggled.connect(self._on_hide_pro_toggled)
        pv.addWidget(self.chk_hide_pro)

        left_col.addWidget(pro_card)
        left_col.addStretch(1)

        # Top-Right card (immediately visible without scrolling!): Background Modes + Music Sync + Upload Image/GIF + Embedded Color Wheel
        from ..color_wheel_dialog import BorhColorWheelPanel

        appear_card = QFrame()
        appear_card.setObjectName("Card")
        av_top = QVBoxLayout(appear_card)
        av_top.setContentsMargins(10, 8, 10, 10)
        av_top.setSpacing(6)

        self.wheel_title_lbl = QLabel("🎨 Фон, музыка и колесо цвета" if cfg.language == "ru" else "🎨 Appearance, Music Sync & Color Wheel")
        self.wheel_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        av_top.addWidget(self.wheel_title_lbl)

        app_grid = QGridLayout()
        app_grid.setSpacing(5)
        cur_am = str(getattr(cfg, "island_appearance_mode", "weather") or "weather").lower()
        ru = cfg.language == "ru"
        self.app_weather_btn = QPushButton("🌤 Погода" if ru else "Weather")
        self.app_weather_btn.setCheckable(True)
        self.app_weather_btn.setChecked(cur_am == "weather")
        self.app_weather_btn.setFixedHeight(28)
        self.app_weather_btn.setToolTip("Живая погода и время суток на фоне (повторное нажатие выключает)" if ru else "Toggle live weather backdrop")
        self.app_weather_btn.clicked.connect(lambda: self._on_appearance_mode_picked("weather"))

        self.app_music_btn = QPushButton("🎵 Под цвет музыки" if ru else "Music Colors")
        self.app_music_btn.setCheckable(True)
        self.app_music_btn.setChecked(cur_am == "music")
        self.app_music_btn.setFixedHeight(28)
        self.app_music_btn.setToolTip("Активно перестраивать весь интерфейс под обложку текущей музыки")
        self.app_music_btn.clicked.connect(lambda: self._on_appearance_mode_picked("music"))

        self.app_color_btn = QPushButton("🎨 Свой цвет" if ru else "Custom Color")
        self.app_color_btn.setCheckable(True)
        self.app_color_btn.setChecked(cur_am == "color")
        self.app_color_btn.setFixedHeight(28)
        self.app_color_btn.clicked.connect(lambda: self._on_appearance_mode_picked("color"))

        self.app_image_btn = QPushButton("🖼 Своя картинка" if ru else "Custom Image")
        self.app_image_btn.setCheckable(True)
        self.app_image_btn.setChecked(cur_am == "image")
        self.app_image_btn.setFixedHeight(28)
        self.app_image_btn.setToolTip("Включить свою фоновую картинку (или выбрать файл, если ещё не задана)" if ru else "Use custom image backdrop")
        self.app_image_btn.clicked.connect(lambda: self._on_appearance_mode_picked("image"))

        self.pick_bg_image_btn = QPushButton("📁")
        self.pick_bg_image_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_bg_image_btn.setFixedSize(34, 28)
        self.pick_bg_image_btn.setStyleSheet(
            "min-width: 34px; max-width: 34px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        self.pick_bg_image_btn.setToolTip("Выбрать картинку (PNG, JPG, WebP)…" if ru else "Upload image…")
        self.pick_bg_image_btn.clicked.connect(self._on_pick_custom_bg_image)

        img_split = QWidget()
        img_lay = QHBoxLayout(img_split)
        img_lay.setContentsMargins(0, 0, 0, 0)
        img_lay.setSpacing(4)
        img_lay.addWidget(self.app_image_btn, 1)
        img_lay.addWidget(self.pick_bg_image_btn, 0)

        self.app_video_btn = QPushButton("🎬 GIF / Видео" if ru else "GIF / Video")
        self.app_video_btn.setCheckable(True)
        self.app_video_btn.setChecked(cur_am == "video")
        self.app_video_btn.setFixedHeight(28)
        self.app_video_btn.setToolTip("Включить GIF или зацикленное видео на фоне" if ru else "Use GIF / video backdrop")
        self.app_video_btn.clicked.connect(lambda: self._on_appearance_mode_picked("video"))

        self.pick_bg_video_btn = QPushButton("📁")
        self.pick_bg_video_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_bg_video_btn.setFixedSize(34, 28)
        self.pick_bg_video_btn.setStyleSheet(
            "min-width: 34px; max-width: 34px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
        )
        self.pick_bg_video_btn.setToolTip("Выбрать GIF или видео…" if ru else "Upload GIF / Video…")
        self.pick_bg_video_btn.clicked.connect(self._on_pick_custom_bg_video)

        vid_split = QWidget()
        vid_lay = QHBoxLayout(vid_split)
        vid_lay.setContentsMargins(0, 0, 0, 0)
        vid_lay.setSpacing(4)
        vid_lay.addWidget(self.app_video_btn, 1)
        vid_lay.addWidget(self.pick_bg_video_btn, 0)

        app_grid.addWidget(self.app_weather_btn, 0, 0)
        app_grid.addWidget(self.app_music_btn, 0, 1)
        app_grid.addWidget(self.app_color_btn, 1, 0)
        app_grid.addWidget(img_split, 1, 1)
        app_grid.addWidget(vid_split, 2, 0, 1, 2)
        av_top.addLayout(app_grid)

        self._swatch_btns: dict[str, QPushButton] = {}
        self.color_wheel_panel = BorhColorWheelPanel(
            str(getattr(cfg, "accent_color", "") or "#3B82F6"),
            wheel_size=140,
            horizontal=True,
            parent=appear_card,
        )
        self.color_wheel_panel.color_previewed.connect(self._on_wheel_previewed)
        self.color_wheel_panel.color_committed.connect(self._on_swatch_picked)
        av_top.addWidget(self.color_wheel_panel)
        right_col.addWidget(appear_card)

        # Right card: Atmosphere, Sky, Weather, Moon & Graphics Detail (Quality)
        atmo_card = QFrame()
        atmo_card.setObjectName("Card")
        av = QVBoxLayout(atmo_card)
        av.setContentsMargins(10, 8, 10, 10)
        av.setSpacing(6)
        self.atmo_title_lbl = QLabel("Атмосфера, сцена и детализация" if cfg.language == "ru" else "Atmosphere, Scene & Detail")
        self.atmo_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        av.addWidget(self.atmo_title_lbl)

        aform = QFormLayout()
        aform.setSpacing(6)

        q_wrap = QWidget()
        q_row = QHBoxLayout(q_wrap)
        q_row.setContentsMargins(0, 0, 0, 0)
        q_row.setSpacing(6)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(25, 100)
        self.quality_slider.setValue(max(25, min(100, int(getattr(cfg, "atmosphere_quality", 100)))))
        self.quality_pct_lbl = QLabel(f"{self.quality_slider.value()}%")
        self.quality_pct_lbl.setMinimumWidth(36)
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        q_row.addWidget(self.quality_slider, 1)
        q_row.addWidget(self.quality_pct_lbl)

        from ...core.licensing import get_license_manager as _glm_atmo

        _rt_ok = _glm_atmo().is_feature_allowed("realtime_weather")
        _init_scene = cfg.atmosphere_preview if (_rt_ok or cfg.atmosphere_preview != "auto") else "day_clear"
        _init_weather = cfg.atmosphere_weather_mode if (_rt_ok or cfg.atmosphere_weather_mode != "auto") else "clear"
        self._last_valid_scene = _init_scene
        self._last_valid_weather = _init_weather

        self.atmo_preview_combo = QComboBox()
        for key, label in ATMO_MODES:
            self.atmo_preview_combo.addItem(label, key)
        self.atmo_preview_combo.setCurrentIndex(max(0, self.atmo_preview_combo.findData(_init_scene)))
        self.atmo_preview_combo.activated.connect(self._on_atmo_preset_picked)

        self.atmo_sky_combo = QComboBox()
        for key, label in SKY_MODES:
            self.atmo_sky_combo.addItem(label, key)
        self.atmo_sky_combo.setCurrentIndex(max(0, self.atmo_sky_combo.findData(cfg.atmosphere_sky_mode)))
        self.atmo_sky_combo.activated.connect(self._apply_atmosphere_live)

        self.atmo_weather_combo = QComboBox()
        for key, label in WEATHER_MODES:
            self.atmo_weather_combo.addItem(label, key)
        self.atmo_weather_combo.setCurrentIndex(max(0, self.atmo_weather_combo.findData(_init_weather)))
        self.atmo_weather_combo.activated.connect(self._apply_atmosphere_live)

        self.atmo_moon_combo = QComboBox()
        for key, label in MOON_MODES:
            self.atmo_moon_combo.addItem(label, key)
        self.atmo_moon_combo.setCurrentIndex(max(0, self.atmo_moon_combo.findData(cfg.atmosphere_moon_mode)))
        self.atmo_moon_combo.activated.connect(self._apply_atmosphere_live)

        aform.addRow("Детализация:" if cfg.language == "ru" else "Detail:", q_wrap)
        aform.addRow("Сцена:" if cfg.language == "ru" else "Scene:", self.atmo_preview_combo)
        aform.addRow("Небо:" if cfg.language == "ru" else "Sky:", self.atmo_sky_combo)
        aform.addRow("Погода:" if cfg.language == "ru" else "Weather:", self.atmo_weather_combo)
        aform.addRow("Луна:" if cfg.language == "ru" else "Moon:", self.atmo_moon_combo)
        av.addLayout(aform)

        self.atmo_reset_btn = QPushButton("Сбросить на Авто" if cfg.language == "ru" else "Reset to Auto")
        self.atmo_reset_btn.clicked.connect(self._reset_atmo_to_auto)
        av.addWidget(self.atmo_reset_btn)
        right_col.addWidget(atmo_card)
        right_col.addStretch(1)

        self.inner_tabs.addTab(sc, tr("settings_graphics_atmo"))

    def _build_general_hotkeys_subtab(self) -> None:
        cfg = self.config_mgr.config
        sc = QScrollArea(self)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        sc.setWidget(host)

        cols = QHBoxLayout(host)
        cols.setContentsMargins(4, 4, 4, 4)
        cols.setSpacing(8)
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        cols.addLayout(left_col, 1)
        cols.addLayout(right_col, 1)

        # Left card: General, Fonts, Pill & Privacy
        gen_card = QFrame()
        gen_card.setObjectName("Card")
        gv = QVBoxLayout(gen_card)
        gv.setContentsMargins(10, 8, 10, 10)
        gv.setSpacing(5)
        self.gen_title_lbl = QLabel("Общие, пилюля и интеграции" if cfg.language == "ru" else "General, Pill & Integrations")
        self.gen_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        gv.addWidget(self.gen_title_lbl)

        form = QFormLayout()
        form.setSpacing(5)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Русский (ru)", "ru")
        self.lang_combo.addItem("English (en)", "en")
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(cfg.language)))

        self.font_combo = QComboBox()
        for fam, label in FIVE_PRESET_FONTS:
            self.font_combo.addItem(label, fam)
        self.font_combo.setCurrentIndex(max(0, self.font_combo.findData(getattr(cfg, "ui_font_family", "Segoe UI"))))

        cf_row = QWidget()
        cf_lay = QHBoxLayout(cf_row)
        cf_lay.setContentsMargins(0, 0, 0, 0)
        cf_lay.setSpacing(4)
        self.custom_font_lbl = QLabel(Path(self._custom_font_path).name if self._custom_font_path else "—")
        self.custom_font_lbl.setObjectName("Muted")
        self.custom_font_btn = QPushButton(".ttf…")
        self.custom_font_btn.setFixedHeight(28)
        self.custom_font_btn.setStyleSheet("min-height: 26px; padding: 0px 10px; border-radius: 12px;")
        self.custom_font_btn.clicked.connect(self._pick_custom_font)
        self.custom_font_clr = QPushButton("✕")
        self.custom_font_clr.setFixedSize(30, 28)
        self.custom_font_clr.setStyleSheet("min-width: 28px; min-height: 26px; padding: 0px; border-radius: 12px; font-weight: 700;")
        self.custom_font_clr.clicked.connect(self._clear_custom_font)
        cf_lay.addWidget(self.custom_font_lbl, 1)
        cf_lay.addWidget(self.custom_font_btn)
        cf_lay.addWidget(self.custom_font_clr)

        self.pill_mode_combo = QComboBox()
        for k, v in (("compact", "Compact"), ("track", "Track Title"), ("miniplayer", "Mini-player")):
            self.pill_mode_combo.addItem(v, k)
        self.pill_mode_combo.setCurrentIndex(max(0, self.pill_mode_combo.findData(cfg.pill_mode)))

        self.autohide_excl_edit = QLineEdit(", ".join(cfg.auto_hide_excluded_apps))
        self.scr_dir_edit = QLineEdit(cfg.screenshot_dir)
        self.rec_dir_edit = QLineEdit(cfg.recording_dir)
        self.discord_id_edit = QLineEdit(cfg.discord_client_id)

        form.addRow("Язык / Language:", self.lang_combo)
        form.addRow("Шрифт:" if cfg.language == "ru" else "Font:", self.font_combo)
        form.addRow("Свой шрифт:" if cfg.language == "ru" else "Custom font:", cf_row)
        form.addRow("Режим пилюли:" if cfg.language == "ru" else "Pill mode:", self.pill_mode_combo)
        form.addRow("Искл. auto-hide:", self.autohide_excl_edit)
        form.addRow("Скриншоты:" if cfg.language == "ru" else "Screenshots:", self.scr_dir_edit)
        form.addRow("Запись видео:" if cfg.language == "ru" else "Recordings:", self.rec_dir_edit)
        form.addRow("Discord App ID:", self.discord_id_edit)
        gv.addLayout(form)

        self.chk_lyrics_pill = QCheckBox("Текст песни в пилюле" if cfg.language == "ru" else "Lyrics in pill")
        self.chk_lyrics_pill.setChecked(cfg.show_lyrics_in_pill)
        self.chk_weather_pill = QCheckBox("Температура в пилюле" if cfg.language == "ru" else "Weather temp in pill")
        self.chk_weather_pill.setChecked(cfg.show_weather_in_pill)
        self.chk_game_stats_pill = QCheckBox("CPU, RAM, GPU в игре" if cfg.language == "ru" else "In-game CPU, RAM, GPU")
        self.chk_game_stats_pill.setChecked(bool(getattr(cfg, "show_game_stats_in_pill", True)))
        self.chk_game_cpu = QCheckBox("CPU в игре" if cfg.language == "ru" else "Game CPU")
        self.chk_game_cpu.setChecked(bool(getattr(cfg, "show_game_cpu", True)))
        self.chk_game_ram = QCheckBox("RAM в игре" if cfg.language == "ru" else "Game RAM")
        self.chk_game_ram.setChecked(bool(getattr(cfg, "show_game_ram", True)))
        self.chk_game_gpu = QCheckBox("GPU в игре" if cfg.language == "ru" else "Game GPU")
        self.chk_game_gpu.setChecked(bool(getattr(cfg, "show_game_gpu", True)))
        self.game_stats_live_lbl = QLabel("CPU —% • RAM —% • GPU —%")
        self.game_stats_live_lbl.setObjectName("Muted")
        self.game_stats_live_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #93C5FD; padding: 2px 0px;")
        self.chk_autohide = QCheckBox("Auto-hide пилюли" if cfg.language == "ru" else "Auto-hide pill")
        self.chk_autohide.setChecked(cfg.auto_hide)
        self.chk_fullscreen = QCheckBox("Скрывать при полноэкранном видео" if cfg.language == "ru" else "Hide in fullscreen video")
        self.chk_fullscreen.setChecked(cfg.hide_in_fullscreen)
        self.chk_through = QCheckBox("Прокликиваемая пилюля" if cfg.language == "ru" else "Click-through pill")
        self.chk_through.setChecked(cfg.click_through)
        self.chk_excl_cap = QCheckBox("Скрывать от скриншотов и демки Discord" if cfg.language == "ru" else "Exclude from capture")
        self.chk_excl_cap.setChecked(cfg.exclude_from_capture)
        self.chk_hot_reload = QCheckBox("Live Hot-Reload" if cfg.language == "ru" else "Live Hot-Reload")
        self.chk_hot_reload.setChecked(cfg.hot_reload)
        self.chk_vpn_ip = QCheckBox("Определять VPN IP" if cfg.language == "ru" else "Lookup VPN IP")
        self.chk_vpn_ip.setChecked(cfg.vpn_ip_lookup)
        self.chk_steam = QCheckBox("Steam загрузки" if cfg.language == "ru" else "Steam downloads")
        self.chk_steam.setChecked(cfg.steam_downloads_enabled)
        self.chk_obs = QCheckBox("OBS статус" if cfg.language == "ru" else "OBS status")
        self.chk_obs.setChecked(cfg.obs_status_enabled)
        self.chk_discord_rpc = QCheckBox("Discord Rich Presence")
        self.chk_discord_rpc.setChecked(cfg.discord_rpc_enabled)

        checks_grid = QGridLayout()
        checks_grid.setSpacing(4)
        for ci, cb in enumerate((
            self.chk_lyrics_pill,
            self.chk_weather_pill,
            self.chk_game_stats_pill,
            self.chk_game_cpu,
            self.chk_game_ram,
            self.chk_game_gpu,
            self.chk_autohide,
            self.chk_fullscreen,
            self.chk_through,
            self.chk_excl_cap,
            self.chk_hot_reload,
            self.chk_vpn_ip,
            self.chk_steam,
            self.chk_obs,
            self.chk_discord_rpc,
        )):
            checks_grid.addWidget(cb, ci // 2, ci % 2)
        gv.addLayout(checks_grid)
        gv.addWidget(self.game_stats_live_lbl)

        left_col.addWidget(gen_card)
        left_col.addStretch(1)

        # Right card: Hotkeys + Save
        hk_card = QFrame()
        hk_card.setObjectName("Card")
        hv = QVBoxLayout(hk_card)
        hv.setContentsMargins(10, 8, 10, 10)
        hv.setSpacing(6)
        self.hk_title_lbl = QLabel("Горячие клавиши" if cfg.language == "ru" else "Global Hotkeys")
        self.hk_title_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        hv.addWidget(self.hk_title_lbl)

        hk_form = QFormLayout()
        hk_form.setSpacing(4)
        self._hk_edits: dict[str, QLineEdit] = {}
        for k, default_val in DEFAULT_HOTKEYS.items():
            le = QLineEdit(cfg.hotkeys.get(k, default_val))
            self._hk_edits[k] = le
            hk_form.addRow(k.replace("_", " ").title() + ":", le)
        hv.addLayout(hk_form)

        save_row = QHBoxLayout()
        save_row.setSpacing(6)
        self.save_settings_btn = QPushButton("Применить и сохранить" if cfg.language == "ru" else "Apply & Save")
        self.save_settings_btn.setObjectName("Primary")
        self.save_settings_btn.setMinimumHeight(30)
        self.save_settings_btn.clicked.connect(self._save_consolidated_settings)
        self.settings_saved_lbl = QLabel("")
        self.settings_saved_lbl.setObjectName("Muted")
        save_row.addWidget(self.save_settings_btn)
        save_row.addWidget(self.settings_saved_lbl, 1)
        hv.addLayout(save_row)
        right_col.addWidget(hk_card)
        right_col.addStretch(1)

        self.inner_tabs.addTab(sc, tr("settings_general_hotkeys"))

    @staticmethod
    def _pair_row(a: QWidget, b: QWidget) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(a, 1)
        lay.addWidget(b, 1)
        return w

    def _on_quality_changed(self, val: int) -> None:
        self.quality_pct_lbl.setText(f"{int(val)}%")
        self.config_mgr.config.atmosphere_quality = max(25, min(100, int(val)))
        self.config_mgr.save()
        self.atmosphere_changed.emit()

    def _on_atmo_preset_picked(self) -> None:
        from ...core.licensing import get_license_manager

        rt_ok = get_license_manager().is_feature_allowed("realtime_weather")
        chosen_scene = str(self.atmo_preview_combo.currentData() or "auto")
        if chosen_scene == "auto" and not rt_ok:
            fallback_scene = getattr(self, "_last_valid_scene", "day_clear")
            if fallback_scene == "auto":
                fallback_scene = "day_clear"
            idx_fb = self.atmo_preview_combo.findData(fallback_scene)
            if idx_fb >= 0:
                self.atmo_preview_combo.blockSignals(True)
                self.atmo_preview_combo.setCurrentIndex(idx_fb)
                self.atmo_preview_combo.blockSignals(False)
            self._open_pro_dialog("Авто-сцена и погода в реальном времени доступны только в Blip PRO. В FREE доступны ручные сцены.")
            return
        self._last_valid_scene = chosen_scene

        for cb in (self.atmo_sky_combo, self.atmo_moon_combo):
            cb.blockSignals(True)
            idx = cb.findData("auto")
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        self.atmo_weather_combo.blockSignals(True)
        w_target = "auto" if rt_ok else (getattr(self, "_last_valid_weather", "clear") if getattr(self, "_last_valid_weather", "clear") != "auto" else "clear")
        idx_w = self.atmo_weather_combo.findData(w_target)
        if idx_w >= 0:
            self.atmo_weather_combo.setCurrentIndex(idx_w)
        self.atmo_weather_combo.blockSignals(False)
        self._apply_atmosphere_live()

    def _apply_atmosphere_live(self) -> None:
        from ...core.licensing import get_license_manager

        cfg = self.config_mgr.config
        rt_ok = get_license_manager().is_feature_allowed("realtime_weather")
        wanted_scene = str(self.atmo_preview_combo.currentData() or "auto")
        show_pro = False
        if wanted_scene == "auto" and not rt_ok:
            fallback_scene = getattr(self, "_last_valid_scene", "day_clear")
            if fallback_scene == "auto":
                fallback_scene = "day_clear"
            wanted_scene = fallback_scene
            idx_sc = self.atmo_preview_combo.findData(fallback_scene)
            if idx_sc >= 0:
                self.atmo_preview_combo.blockSignals(True)
                self.atmo_preview_combo.setCurrentIndex(idx_sc)
                self.atmo_preview_combo.blockSignals(False)
            show_pro = True
        else:
            self._last_valid_scene = wanted_scene
        cfg.atmosphere_preview = wanted_scene
        cfg.atmosphere_sky_mode = str(self.atmo_sky_combo.currentData() or "auto")
        wanted_weather = str(self.atmo_weather_combo.currentData() or "auto")
        if wanted_weather == "auto" and not rt_ok:
            fallback_w = getattr(self, "_last_valid_weather", "clear")
            if fallback_w == "auto":
                fallback_w = "clear"
            wanted_weather = fallback_w
            idx_clear = self.atmo_weather_combo.findData(fallback_w)
            if idx_clear >= 0:
                self.atmo_weather_combo.blockSignals(True)
                self.atmo_weather_combo.setCurrentIndex(idx_clear)
                self.atmo_weather_combo.blockSignals(False)
            show_pro = True
        else:
            self._last_valid_weather = wanted_weather
        cfg.atmosphere_weather_mode = wanted_weather
        cfg.atmosphere_moon_mode = str(self.atmo_moon_combo.currentData() or "auto")
        cfg.player_card_style = str(self.player_style_combo.currentData() or "mirror")
        cfg.recording_audio_mode = str(self.rec_audio_combo.currentData() or "system")
        for k, gb in getattr(self, "_glass_btns", {}).items():
            if gb.isChecked():
                cfg.glass_mode = k
                break
        for k, rb in getattr(self, "_ref_q_btns", {}).items():
            if rb.isChecked():
                cfg.refraction_quality = k
                break
        cfg.atmosphere_quality = int(self.quality_slider.value())
        if get_license_manager().is_feature_allowed("weather"):
            cfg.island_appearance_mode = "weather"
            for attr_n, mode_k in (
                ("app_weather_btn", "weather"),
                ("app_music_btn", "music"),
                ("app_color_btn", "color"),
                ("app_image_btn", "image"),
                ("app_video_btn", "video"),
            ):
                btn_w = getattr(self, attr_n, None)
                if btn_w is not None:
                    btn_w.blockSignals(True)
                    btn_w.setChecked(mode_k == "weather")
                    btn_w.blockSignals(False)
        self.config_mgr.save()
        self.config_applied.emit()
        self.atmosphere_changed.emit()
        if show_pro:
            self._open_pro_dialog("Погода фона в реальном времени доступна только в Blip PRO. В FREE доступна ручная смена погоды.")

    def _reset_atmo_to_auto(self) -> None:
        from ...core.licensing import get_license_manager

        rt_ok = get_license_manager().is_feature_allowed("realtime_weather")
        if not rt_ok:
            for cb, target in (
                (self.atmo_preview_combo, "day_clear"),
                (self.atmo_sky_combo, "auto"),
                (self.atmo_weather_combo, "clear"),
                (self.atmo_moon_combo, "auto"),
            ):
                cb.blockSignals(True)
                idx = cb.findData(target)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
                cb.blockSignals(False)
            self._last_valid_scene = "day_clear"
            self._last_valid_weather = "clear"
            self._apply_atmosphere_live()
            self._open_pro_dialog("Режим «Авто» для погоды и сцены доступен только в Blip PRO.")
            return
        for cb in (self.atmo_preview_combo, self.atmo_sky_combo, self.atmo_weather_combo, self.atmo_moon_combo):
            cb.blockSignals(True)
            idx = cb.findData("auto")
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        self._apply_atmosphere_live()

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
        self.custom_font_lbl.setText("—")

    def _save_consolidated_settings(self) -> None:
        cfg = self.config_mgr.config
        cfg.language = str(self.lang_combo.currentData() or "ru")
        set_language(cfg.language)
        cfg.theme = str(self.theme_combo.currentData() or "dark")
        cfg.ui_font_family = str(self.font_combo.currentData() or "Segoe UI")
        cfg.custom_font_path = self._custom_font_path
        cfg.monitor_index = self.mon_spin.value()
        cfg.offset_x = self.offx_spin.value()
        cfg.scale = round(self.scale_spin.value() / 100.0, 2)
        cfg.collapsed_opacity = round(self.op_col_spin.value() / 100.0, 2)
        cfg.expanded_opacity = round(self.op_exp_spin.value() / 100.0, 2)
        cfg.pill_mode = str(self.pill_mode_combo.currentData() or "compact")
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
        cfg.hot_reload = self.chk_hot_reload.isChecked()
        cfg.vpn_ip_lookup = self.chk_vpn_ip.isChecked()
        cfg.steam_downloads_enabled = self.chk_steam.isChecked()
        cfg.obs_status_enabled = self.chk_obs.isChecked()
        cfg.discord_rpc_enabled = self.chk_discord_rpc.isChecked()
        cfg.discord_client_id = self.discord_id_edit.text().strip()
        cfg.screenshot_dir = self.scr_dir_edit.text().strip()
        cfg.recording_dir = self.rec_dir_edit.text().strip()
        from ...core.licensing import get_license_manager

        rt_ok = get_license_manager().is_feature_allowed("realtime_weather")
        cfg.atmosphere_quality = int(self.quality_slider.value())
        sc_val = str(self.atmo_preview_combo.currentData() or "auto")
        w_val = str(self.atmo_weather_combo.currentData() or "auto")
        if not rt_ok:
            if sc_val == "auto":
                sc_val = "day_clear"
                idx_sc = self.atmo_preview_combo.findData(sc_val)
                if idx_sc >= 0:
                    self.atmo_preview_combo.blockSignals(True)
                    self.atmo_preview_combo.setCurrentIndex(idx_sc)
                    self.atmo_preview_combo.blockSignals(False)
            if w_val == "auto":
                w_val = "clear"
                idx_w = self.atmo_weather_combo.findData(w_val)
                if idx_w >= 0:
                    self.atmo_weather_combo.blockSignals(True)
                    self.atmo_weather_combo.setCurrentIndex(idx_w)
                    self.atmo_weather_combo.blockSignals(False)
        cfg.atmosphere_preview = sc_val
        cfg.atmosphere_sky_mode = str(self.atmo_sky_combo.currentData() or "auto")
        cfg.atmosphere_weather_mode = w_val
        cfg.atmosphere_moon_mode = str(self.atmo_moon_combo.currentData() or "auto")
        cfg.player_card_style = str(self.player_style_combo.currentData() or "mirror")
        cfg.recording_audio_mode = str(self.rec_audio_combo.currentData() or "system")
        for k, gb in getattr(self, "_glass_btns", {}).items():
            if gb.isChecked():
                cfg.glass_mode = k
                break
        for k, rb in getattr(self, "_ref_q_btns", {}).items():
            if rb.isChecked():
                cfg.refraction_quality = k
                break
        for k, le in self._hk_edits.items():
            cfg.hotkeys[k] = le.text().strip()
        self.config_mgr.save()
        self.settings_saved_lbl.setText("Сохранено" if cfg.language == "ru" else "Saved")
        QTimer.singleShot(2500, lambda: self.settings_saved_lbl.setText(""))
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def sync_from_config(self) -> None:
        cfg = self.config_mgr.config
        self.quality_slider.blockSignals(True)
        self.quality_slider.setValue(max(25, min(100, int(getattr(cfg, "atmosphere_quality", 100)))))
        self.quality_slider.blockSignals(False)
        self.quality_pct_lbl.setText(f"{self.quality_slider.value()}%")
        from ...core.licensing import get_license_manager as _glm_sync

        _rt_sync = _glm_sync().is_feature_allowed("realtime_weather")
        _sync_scene = cfg.atmosphere_preview if (_rt_sync or cfg.atmosphere_preview != "auto") else "day_clear"
        _sync_weather = cfg.atmosphere_weather_mode if (_rt_sync or cfg.atmosphere_weather_mode != "auto") else "clear"
        self._last_valid_scene = _sync_scene
        self._last_valid_weather = _sync_weather
        for cb, val in (
            (self.atmo_preview_combo, _sync_scene),
            (self.atmo_sky_combo, cfg.atmosphere_sky_mode),
            (self.atmo_weather_combo, _sync_weather),
            (self.atmo_moon_combo, cfg.atmosphere_moon_mode),
            (self.theme_combo, cfg.theme),
            (self.lang_combo, cfg.language),
            (self.pill_mode_combo, cfg.pill_mode),
            (self.player_style_combo, getattr(cfg, "player_card_style", "mirror")),
            (self.rec_audio_combo, getattr(cfg, "recording_audio_mode", "system")),
        ):
            cb.blockSignals(True)
            idx = cb.findData(val)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        self.scale_spin.setValue(int(round(cfg.scale * 100)))
        self.op_col_spin.setValue(int(round(cfg.collapsed_opacity * 100)))
        self.op_exp_spin.setValue(int(round(cfg.expanded_opacity * 100)))
        self.mon_spin.setValue(cfg.monitor_index)
        self.offx_spin.setValue(cfg.offset_x)
        self.chk_liquid_drop.setChecked(cfg.liquid_drop)
        self.chk_eq.setChecked(cfg.edge_equalizer)
        self.chk_rain_drip.setChecked(cfg.weather_rain_drip)
        cur_gm = str(getattr(cfg, "glass_mode", "liquid") or "liquid").lower()
        for k, gb in getattr(self, "_glass_btns", {}).items():
            gb.blockSignals(True)
            gb.setChecked(k == cur_gm)
            gb.blockSignals(False)
        cur_rq = str(getattr(cfg, "refraction_quality", "ultra") or "ultra").lower()
        for k, rb in getattr(self, "_ref_q_btns", {}).items():
            rb.blockSignals(True)
            rb.setChecked(k == cur_rq)
            rb.blockSignals(False)
        cur_am = str(getattr(cfg, "island_appearance_mode", "weather") or "weather").lower()
        for attr_n, mode_k in (
            ("app_weather_btn", "weather"),
            ("app_music_btn", "music"),
            ("app_color_btn", "color"),
            ("app_image_btn", "image"),
            ("app_video_btn", "video"),
        ):
            btn_w = getattr(self, attr_n, None)
            if btn_w is not None:
                btn_w.blockSignals(True)
                btn_w.setChecked(cur_am == mode_k)
                btn_w.blockSignals(False)
        cur_acc = (getattr(cfg, "accent_color", "") or "").lower()
        for sw_hex, sb in getattr(self, "_swatch_btns", {}).items():
            sb.blockSignals(True)
            sb.setChecked(sw_hex.lower() == cur_acc)
            sb.blockSignals(False)
        if hasattr(self, "color_wheel_panel") and cur_acc:
            self.color_wheel_panel.set_hex(cur_acc, commit=False)
        self._sync_pro_locks()

    def _on_appearance_mode_picked(self, mode: str) -> None:
        from pathlib import Path
        from ...core.licensing import get_license_manager

        cur_mode = str(getattr(self.config_mgr.config, "island_appearance_mode", "weather") or "weather").lower()
        m = mode if mode in ("weather", "music", "color", "image", "video") else "weather"
        lic = get_license_manager()
        if m == "weather":
            if not lic.is_feature_allowed("weather"):
                m = "color"
                self._open_pro_dialog("Живая погода и сцена на фоне доступны в Blip PRO (300 ₽ / 3 мес).")
            elif cur_mode == "weather":
                # Clicking active Weather toggles it off cleanly back to Custom Color
                m = "color"
        elif m == "video":
            if not lic.is_feature_allowed("weather"):
                m = "image" if getattr(self.config_mgr.config, "custom_bg_image_path", "") else "color"
                self._open_pro_dialog("Анимированные GIF и зацикленные видео на фоне острова доступны в Blip PRO (300 ₽ / 3 мес).")
            else:
                vp = str(getattr(self.config_mgr.config, "custom_bg_video_path", "") or "").strip()
                if not vp or not Path(vp).is_file():
                    self._on_pick_custom_bg_video()
                    return
                if cur_mode == "video":
                    m = "color"
        elif m == "image":
            ip = str(getattr(self.config_mgr.config, "custom_bg_image_path", "") or "").strip()
            if not ip or not Path(ip).is_file():
                self._on_pick_custom_bg_image()
                return
            if cur_mode == "image":
                m = "color"

        for attr_n, mode_k in (
            ("app_weather_btn", "weather"),
            ("app_music_btn", "music"),
            ("app_color_btn", "color"),
            ("app_image_btn", "image"),
            ("app_video_btn", "video"),
        ):
            btn_w = getattr(self, attr_n, None)
            if btn_w is not None:
                btn_w.blockSignals(True)
                btn_w.setChecked(m == mode_k)
                btn_w.blockSignals(False)
        self.config_mgr.config.island_appearance_mode = m
        self.config_mgr.save()
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def _on_wheel_previewed(self, hex_col: str) -> None:
        self.config_mgr.config.accent_color = hex_col
        self.config_mgr.config.island_appearance_mode = "color"
        for k, sb in getattr(self, "_swatch_btns", {}).items():
            sb.blockSignals(True)
            sb.setChecked(k.lower() == hex_col.lower())
            sb.blockSignals(False)
        for attr_n, mode_k in (
            ("app_weather_btn", "weather"),
            ("app_music_btn", "music"),
            ("app_color_btn", "color"),
            ("app_image_btn", "image"),
            ("app_video_btn", "video"),
        ):
            btn_w = getattr(self, attr_n, None)
            if btn_w is not None:
                btn_w.blockSignals(True)
                btn_w.setChecked(mode_k == "color")
                btn_w.blockSignals(False)
        self.color_previewed.emit(hex_col)

    def _on_pick_custom_bg_image(self) -> None:
        import shutil
        import time
        from pathlib import Path
        from ..color_wheel_dialog import pick_bg_media_file

        path = pick_bg_media_file(
            self,
            "Выберите фоновую картинку для острова",
            "Изображения (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            self.sync_from_config()
            return
        src = Path(path)
        dst_dir = self.config_mgr.data_dir / "custom_bg"
        dst_dir.mkdir(parents=True, exist_ok=True)
        for old in dst_dir.glob("bg_image*"):
            try:
                old.unlink()
            except OSError:
                pass
        dst = dst_dir / f"bg_image_{int(time.time() * 1000)}{src.suffix.lower()}"
        try:
            shutil.copy2(src, dst)
        except Exception:
            dst = src
        self.config_mgr.config.custom_bg_image_path = str(dst)
        self.config_mgr.config.island_appearance_mode = "image"
        self.config_mgr.save()
        self.sync_from_config()
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def _on_pick_custom_bg_video(self) -> None:
        import shutil
        import time
        from pathlib import Path
        from ...core.licensing import get_license_manager
        from ..color_wheel_dialog import pick_bg_media_file

        if not get_license_manager().is_feature_allowed("weather"):
            self.sync_from_config()
            self._open_pro_dialog("Загрузка GIF и зацикленных видео на фон острова доступна в Blip PRO (300 ₽ / 3 мес).")
            return
        path = pick_bg_media_file(
            self,
            "Выберите GIF или зацикленное видео для фона острова (PRO)",
            "GIF и Видео (*.gif *.mp4 *.webm *.mov *.webp *.avi *.mkv)",
        )
        if not path:
            self.sync_from_config()
            return
        win = self.window()
        if win is not None and hasattr(win, "_stop_custom_bg_players"):
            win._stop_custom_bg_players()
        src = Path(path)
        dst_dir = self.config_mgr.data_dir / "custom_bg"
        dst_dir.mkdir(parents=True, exist_ok=True)
        for old in dst_dir.glob("bg_video*"):
            try:
                old.unlink()
            except OSError:
                pass
        dst = dst_dir / f"bg_video_{int(time.time() * 1000)}{src.suffix.lower()}"
        try:
            shutil.copy2(src, dst)
        except Exception:
            dst = src
        self.config_mgr.config.custom_bg_video_path = str(dst)
        self.config_mgr.config.island_appearance_mode = "video"
        self.config_mgr.save()
        self.sync_from_config()
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def _on_swatch_picked(self, hex_col: str) -> None:
        for k, sb in getattr(self, "_swatch_btns", {}).items():
            sb.blockSignals(True)
            sb.setChecked(k.lower() == hex_col.lower())
            sb.blockSignals(False)
        self.config_mgr.config.accent_color = hex_col
        self.config_mgr.config.island_appearance_mode = "color"
        self.config_mgr.save()
        self.sync_from_config()
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def _sync_pro_locks(self) -> None:
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QIcon
        from ...core.licensing import get_license_manager
        from ..icons import get_icon

        lic = get_license_manager()
        glass_ok = lic.is_feature_allowed("glass")
        lock_ico = QIcon() if glass_ok else get_icon("lock", size=14, color="#fbbf24")
        for k, btn in getattr(self, "_glass_btns", {}).items():
            if k in ("liquid", "frost"):
                btn.setIcon(lock_ico)
                btn.setIconSize(QSize(14, 14))
        w_ok = lic.is_feature_allowed("weather")
        ru = getattr(self.config_mgr.config, "language", "ru") == "ru"
        if hasattr(self, "app_weather_btn"):
            self.app_weather_btn.setText(("🌤 Погода" if w_ok else "🌤 Погода (PRO)") if ru else ("Weather" if w_ok else "Weather (PRO)"))
            self.app_weather_btn.setIcon(QIcon() if w_ok else get_icon("lock", size=14, color="#fbbf24"))
            self.app_weather_btn.setIconSize(QSize(14, 14))
        if hasattr(self, "app_video_btn"):
            self.app_video_btn.setText(("🎬 GIF / Видео" if w_ok else "🎬 GIF / Видео (PRO)") if ru else ("GIF / Video" if w_ok else "GIF / Video (PRO)"))
            self.app_video_btn.setIcon(QIcon() if w_ok else get_icon("lock", size=14, color="#fbbf24"))
            self.app_video_btn.setIconSize(QSize(14, 14))

    def _on_refraction_quality_picked(self, quality: str) -> None:
        q = quality if quality in ("low", "medium", "high", "ultra") else "ultra"
        for k, rb in getattr(self, "_ref_q_btns", {}).items():
            rb.blockSignals(True)
            rb.setChecked(k == q)
            rb.blockSignals(False)
        self.config_mgr.config.refraction_quality = q
        self.config_mgr.save()
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def _on_run_refraction_benchmark(self) -> None:
        from ..liquid_glass_3d import run_refraction_benchmark

        rec, avg_ms = run_refraction_benchmark()
        self.config_mgr.config.refraction_bench_ms = avg_ms
        names = {"low": "Низк.", "medium": "Средн.", "high": "Высок.", "ultra": "Ультра"}
        self.bench_res_lbl.setText(f"{avg_ms:.1f} мс → {names.get(rec, rec)}")
        self._on_refraction_quality_picked(rec)

    def _on_glass_mode_picked(self, mode: str) -> None:
        from ...core.licensing import get_license_manager

        m = mode if mode in ("off", "liquid", "frost") else "liquid"
        if m in ("liquid", "frost") and not get_license_manager().is_feature_allowed("glass"):
            m = "off"
            self._open_pro_dialog("Стекло Liquid Glass и Frost Glass доступны только в Blip PRO.")
        for k, gb in getattr(self, "_glass_btns", {}).items():
            gb.blockSignals(True)
            gb.setChecked(k == m)
            gb.blockSignals(False)
        self.config_mgr.config.glass_mode = m
        self.config_mgr.save()
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def _open_pro_dialog(self, reason: str = "") -> None:
        from ..pro_dialog import show_pro_upgrade

        show_pro_upgrade(self, reason=reason, on_unlocked=self._on_license_updated)

    def _on_license_updated(self) -> None:
        from ...core.licensing import get_license_manager

        st = get_license_manager().get_status(force_refresh=True)
        if hasattr(self, "pro_status_lbl"):
            self.pro_status_lbl.setText(
                f"Подписка: <b>{st.tier}</b>"
                + (f" ({st.trial_days_left:.1f} дн.)" if st.tier == "TRIAL" else "")
            )
        if hasattr(self, "chk_hide_pro"):
            self.chk_hide_pro.setEnabled(st.is_pro)
        self.config_applied.emit()
        self.atmosphere_changed.emit()

    def _on_hide_pro_toggled(self, checked: bool) -> None:
        self.config_mgr.config.hide_pro_badge = bool(checked)
        self.config_mgr.save()
        self.config_applied.emit()

    def _on_support_clicked(self) -> None:
        import webbrowser

        webbrowser.open("https://t.me/Delevoperborhisland")

    def _on_check_updates(self) -> None:
        from ...core.licensing import APP_VERSION
        from ...services.updater_service import check_for_updates, download_and_stage_update

        self.upd_status_lbl.setText("Проверяем обновления...")
        info = check_for_updates()
        latest_txt = f"v{info.latest_version}" if getattr(info, "latest_version", "") else f"v{info.current_version}"
        self.check_upd_btn.setText(
            f"Проверить обновления  •  Установлена: v{APP_VERSION}  |  Актуальная: {latest_txt}"
        )
        if info.error:
            self.upd_status_lbl.setText(f"Не удалось проверить: {info.error}")
            return
        if not info.available:
            self.upd_status_lbl.setText(
                f"У вас последняя версия: v{info.current_version}  •  Актуальная на сервере: {latest_txt}"
            )
            return
        self.upd_status_lbl.setText(
            f"Доступно обновление: v{info.current_version} → {latest_txt}. Подготовка..."
        )
        ok, msg = download_and_stage_update(info)
        self.upd_status_lbl.setText(msg)
        if ok:
            from PyQt6.QtWidgets import QApplication

            QApplication.quit()

    def retranslate_ui(self) -> None:
        if hasattr(self, "inner_tabs") and self.inner_tabs.count() >= 3:
            self.inner_tabs.setTabText(0, tr("settings_audio_system"))
            self.inner_tabs.setTabText(1, tr("settings_graphics_atmo"))
            self.inner_tabs.setTabText(2, tr("settings_general_hotkeys"))
        self.out_title_lbl.setText(tr("output_audio"))
        self.mixer_title_lbl.setText(tr("app_mixer"))
        self.mixer_refresh_btn.setToolTip(tr("mixer_refresh_tip"))
        self.mixer_apply_btn.setText(tr("mixer_apply"))
        self.mixer_apply_btn.setToolTip(tr("mixer_apply_tip"))
        self.mixer_save_btn.setToolTip(tr("mixer_save_tip"))
        self.mixer_new_btn.setToolTip(tr("mixer_new_tip"))
        self.mixer_del_btn.setToolTip(tr("mixer_del_tip"))
        self.mixer_new_edit.setPlaceholderText(tr("mixer_profile_ph"))
        self.mixer_new_ok_btn.setText(tr("mixer_create"))
        self.mixer_empty_lbl.setText(tr("mixer_empty"))
        self.mic_title_lbl.setText(tr("input_mic"))
        self.win_theme_btn.setText(tr("dark_theme"))
        self.open_settings_btn.setText(tr("settings"))
        for b, key, _ in self._power_buttons:
            b.setText(tr(key))
        self._sync_autostart_btn_text()
        self._sync_blue_filter_btn()
        self._populate_mixer_profiles()
        if self._last_devs:
            self._on_devices_loaded(self._last_devs, self._last_ov, self._last_mv, self._last_br)
        else:
            self.bright_lbl.setText(tr("brightness_na"))
            self.privacy_lbl.setText(tr("privacy_fmt", mic=tr("mic_idle"), cam=tr("cam_idle")))
            self.wifi_lbl.setText(f"{tr('wifi')}: ...")
            self.vpn_lbl.setText(f"{tr('vpn')}: Off")
        if self._last_snap is not None:
            self.update_system_state(self._last_snap)
        if self._mixer_sessions_data:
            self._on_app_sessions_loaded(self._mixer_sessions_data)
        self._refresh_stickers()

    def _refresh_stickers(self) -> None:
        fg, acc = self._icon_fg, self._accent_hex
        self.out_hdr_ico.setPixmap(get_pixmap("volume", size=15, color=fg, accent=acc))
        self.mixer_hdr_ico.setPixmap(get_pixmap("mixer", size=15, color=fg, accent=acc))
        self.mic_hdr_ico.setPixmap(get_pixmap("mic", size=15, color=fg, accent=acc))
        self.bright_hdr_ico.setPixmap(get_pixmap("sun", size=15, color=fg, accent=acc))
        self.privacy_ico.setPixmap(get_pixmap("camera", size=14, color=fg, accent=acc))
        self.vpn_ico.setPixmap(get_pixmap("shield", size=14, color=fg, accent=acc))

        self.mixer_refresh_btn.setIcon(get_icon("refresh", size=14, color=fg, accent=acc))
        self.mixer_save_btn.setIcon(get_icon("save", size=14, color=fg, accent=acc))
        self.mixer_new_btn.setIcon(get_icon("plus", size=14, color=fg, accent=acc))
        self.mixer_del_btn.setIcon(get_icon("close", size=13, color=fg, accent=acc))
        self.win_theme_btn.setIcon(get_icon("theme", size=15, color=fg, accent=acc))
        self.autostart_btn.setIcon(get_icon("rocket", size=15, color=fg, accent=acc))
        self._sync_blue_filter_btn()
        self.open_settings_btn.setIcon(get_icon("settings", size=15, color=fg, accent=acc))
        for b, _, ico_name in self._power_buttons:
            b.setIcon(get_icon(ico_name, size=14, color=fg, accent=acc))
        self._sync_audio_button_icons()
        for i in range(self.bt_list.count()):
            it = self.bt_list.item(i)
            if it is not None:
                it.setIcon(get_icon("headphones", size=14, color=fg, accent=acc))

    def _sync_audio_button_icons(self) -> None:
        fg, acc = self._icon_fg, self._accent_hex
        self.out_mute_btn.setIcon(get_icon("mute" if self.out_mute_btn.isChecked() else "volume", size=15, color=fg, accent=acc))
        self.mic_mute_btn.setIcon(get_icon("mic_mute" if self.mic_mute_btn.isChecked() else "mic", size=15, color=fg, accent=acc))
        for row in self._mixer_rows.values():
            mb: QPushButton = row["mute_btn"]
            mb.setIcon(get_icon("mute" if mb.isChecked() else "volume", size=14, color=fg, accent=acc))

    def refresh_hardware(self) -> None:
        cfg = self.config_mgr.config
        out_id = cfg.output_device_id or "default"
        mic_id = cfg.input_device_id or "default"

        def _work() -> None:
            try:
                devs = self.bridge.get_audio_devices()
                ov = self.bridge.get_volume("render", out_id)
                mv = self.bridge.get_volume("capture", mic_id)
                br = self.bridge.get_brightness()
                self.devices_loaded.emit(devs, ov, mv, br)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()
        self.refresh_app_sessions()

    # ------------------------------------------------------------------ Per-App Mixer & Profiles
    def refresh_app_sessions(self) -> None:
        out_id = self.config_mgr.config.output_device_id or "default"

        def _work() -> None:
            try:
                sessions = self.bridge.list_app_sessions(out_id)
                self.app_sessions_loaded.emit(sessions)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _app_badge_pixmap(self, key: str, exe_path: str) -> QPixmap:
        cache_key = f"{key}:{exe_path}"
        if cache_key in self._app_icon_cache:
            return self._app_icon_cache[cache_key]
        pm = QPixmap()
        if exe_path and Path(exe_path).exists():
            try:
                ico = self._icon_provider.icon(QFileInfo(exe_path))
                raw_pm = ico.pixmap(32, 32)
                if not raw_pm.isNull():
                    circ = _make_circular_app_icon(raw_pm.toImage(), size=22)
                    pm = circ.pixmap(22, 22)
            except Exception:
                pm = QPixmap()
        if pm.isNull() and key == "__system__":
            pm = get_pixmap("volume", size=20, color=self._icon_fg, accent=self._accent_hex)
        if pm.isNull():
            img = QImage(22, 22, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#6366F1"))
            p.drawEllipse(1, 1, 20, 20)
            p.setPen(QColor("#FFFFFF"))
            font = p.font()
            font.setBold(True)
            font.setPointSize(8)
            p.setFont(font)
            p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, key[:2].upper())
            p.end()
            pm = QPixmap.fromImage(img)
        self._app_icon_cache[cache_key] = pm
        return pm

    def _on_app_sessions_loaded(self, sessions: list) -> None:
        self._mixer_sessions_data = list(sessions)
        new_keys = [str(s.get("key", "")) for s in sessions if s.get("key")]
        old_keys = list(self._mixer_rows.keys())
        self.mixer_empty_lbl.setVisible(len(new_keys) == 0)

        if new_keys == old_keys and new_keys:
            for s in sessions:
                key = str(s["key"])
                row = self._mixer_rows.get(key)
                if not row:
                    continue
                vol = int(s.get("volume", 100))
                muted = bool(s.get("muted", False))
                title = tr("system_sounds") if key == "__system__" else str(s.get("title", key))
                sl: QSlider = row["slider"]
                mb: QPushButton = row["mute_btn"]
                lbl: QLabel = row["name_lbl"]
                pct: QLabel = row["pct_lbl"]
                if not sl.isSliderDown():
                    sl.blockSignals(True)
                    sl.setValue(vol)
                    sl.blockSignals(False)
                    pct.setText(tr("mute_short") if muted else f"{vol}%")
                mb.blockSignals(True)
                mb.setChecked(muted)
                mb.setIcon(get_icon("mute" if muted else "volume", size=14, color=self._icon_fg, accent=self._accent_hex))
                mb.blockSignals(False)
                dot = "● " if s.get("active") else ""
                lbl.setText(f"{dot}{title}")
            return

        for row in self._mixer_rows.values():
            w = row.get("widget")
            if w is not None:
                self.mixer_sessions_lay.removeWidget(w)
                w.deleteLater()
        self._mixer_rows.clear()

        for s in sessions:
            key = str(s.get("key", ""))
            if not key:
                continue
            vol = int(s.get("volume", 100))
            muted = bool(s.get("muted", False))
            title = tr("system_sounds") if key == "__system__" else str(s.get("title", key))
            dot = "● " if s.get("active") else ""

            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(2, 1, 2, 1)
            rl.setSpacing(6)

            ico_lbl = QLabel()
            ico_lbl.setFixedSize(22, 22)
            ico_lbl.setPixmap(self._app_badge_pixmap(key, str(s.get("exe", ""))))

            name_lbl = QLabel(f"{dot}{title}")
            name_lbl.setMinimumWidth(68)
            name_lbl.setMaximumWidth(86)
            name_lbl.setStyleSheet("font-size: 11px; font-weight: 600;")
            name_lbl.setToolTip(title)

            mute_btn = QPushButton("")
            mute_btn.setIcon(get_icon("mute" if muted else "volume", size=14, color=self._icon_fg, accent=self._accent_hex))
            mute_btn.setCheckable(True)
            mute_btn.setChecked(muted)
            mute_btn.setFixedSize(28, 28)
            mute_btn.setStyleSheet(
                "min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0px; border-radius: 14px;"
            )
            mute_btn.clicked.connect(lambda checked, k=key: self._on_app_mute_toggled(k, checked))

            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, 100)
            sl.setMinimumWidth(60)
            sl.setValue(vol)
            sl.valueChanged.connect(lambda v, k=key: self._on_app_slider_changed(k, v))

            pct_lbl = QLabel(tr("mute_short") if muted else f"{vol}%")
            pct_lbl.setMinimumWidth(42)
            pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pct_lbl.setStyleSheet("font-size: 11px;")

            rl.addWidget(ico_lbl)
            rl.addWidget(name_lbl)
            rl.addWidget(mute_btn)
            rl.addWidget(sl, 1)
            rl.addWidget(pct_lbl)
            self.mixer_sessions_lay.addWidget(row_w)

            self._mixer_rows[key] = {
                "widget": row_w,
                "name_lbl": name_lbl,
                "mute_btn": mute_btn,
                "slider": sl,
                "pct_lbl": pct_lbl,
            }

    def _on_app_slider_changed(self, app_key: str, val: int) -> None:
        row = self._mixer_rows.get(app_key)
        if row:
            muted = row["mute_btn"].isChecked()
            row["pct_lbl"].setText(tr("mute_short") if muted else f"{val}%")
        self._vol_debounce_pending[app_key] = int(val)
        self._vol_debounce_timer.start()

    def _flush_pending_app_volumes(self) -> None:
        batch = dict(self._vol_debounce_pending)
        self._vol_debounce_pending.clear()
        if not batch:
            return
        dev = self.config_mgr.config.output_device_id or "default"

        def _work() -> None:
            for k, v in batch.items():
                self.bridge.set_app_volume(k, v, dev)

        threading.Thread(target=_work, daemon=True).start()

    def _on_app_mute_toggled(self, app_key: str, checked: bool) -> None:
        row = self._mixer_rows.get(app_key)
        if row:
            row["mute_btn"].setIcon(get_icon("mute" if checked else "volume", size=14, color=self._icon_fg, accent=self._accent_hex))
            row["pct_lbl"].setText(tr("mute_short") if checked else f"{row['slider'].value()}%")
        dev = self.config_mgr.config.output_device_id or "default"
        threading.Thread(target=lambda: self.bridge.set_app_mute(app_key, checked, dev), daemon=True).start()

    def _populate_mixer_profiles(self) -> None:
        cfg = self.config_mgr.config
        profiles = cfg.audio_mixer_profiles
        if not profiles:
            profiles["Стандартный"] = {}
        active = cfg.audio_mixer_active_profile
        if active not in profiles:
            active = next(iter(profiles.keys()))
            cfg.audio_mixer_active_profile = active
        self.mixer_profile_combo.blockSignals(True)
        self.mixer_profile_combo.clear()
        for pname in profiles.keys():
            self.mixer_profile_combo.addItem(_localize_profile_name(pname), pname)
        idx = max(0, self.mixer_profile_combo.findData(active))
        self.mixer_profile_combo.setCurrentIndex(idx)
        self.mixer_profile_combo.blockSignals(False)
        self.mixer_del_btn.setEnabled(len(profiles) > 1 and active != "Стандартный")

    def _on_mixer_profile_selected(self) -> None:
        pname = self.mixer_profile_combo.currentData()
        if not pname:
            return
        self.apply_mixer_profile(str(pname))

    def apply_mixer_profile(self, profile_name: str | None = None) -> None:
        cfg = self.config_mgr.config
        pname = profile_name or str(self.mixer_profile_combo.currentData() or cfg.audio_mixer_active_profile)
        if pname not in cfg.audio_mixer_profiles:
            return
        cfg.audio_mixer_active_profile = pname
        self.config_mgr.save()
        self.mixer_del_btn.setEnabled(len(cfg.audio_mixer_profiles) > 1 and pname != "Стандартный")

        raw_rules = dict(cfg.audio_mixer_profiles.get(pname) or {})
        if not raw_rules and pname == "Стандартный":
            raw_rules = {k: {"volume": 100, "muted": False} for k in self._mixer_rows.keys()}

        for k, row in self._mixer_rows.items():
            if k in raw_rules:
                v = max(0, min(100, int(raw_rules[k].get("volume", 100))))
                m = bool(raw_rules[k].get("muted", False))
                row["slider"].blockSignals(True)
                row["slider"].setValue(v)
                row["slider"].blockSignals(False)
                row["mute_btn"].blockSignals(True)
                row["mute_btn"].setChecked(m)
                row["mute_btn"].setIcon(get_icon("mute" if m else "volume", size=14, color=self._icon_fg, accent=self._accent_hex))
                row["mute_btn"].blockSignals(False)
                row["pct_lbl"].setText(tr("mute_short") if m else f"{v}%")

        dev = cfg.output_device_id or "default"

        def _work() -> None:
            cnt = self.bridge.apply_audio_profile(raw_rules, dev) if raw_rules else 0
            self.profile_applied.emit(pname, cnt)

        threading.Thread(target=_work, daemon=True).start()

    def _on_profile_applied(self, pname: str, count: int) -> None:
        self.mixer_status_lbl.setText(tr("mixer_applied_cnt", name=_localize_profile_name(pname), count=count))
        self.refresh_app_sessions()

    def _capture_current_mixer_rules(self) -> dict[str, dict[str, int | bool]]:
        return {
            k: {"volume": int(row["slider"].value()), "muted": bool(row["mute_btn"].isChecked())}
            for k, row in self._mixer_rows.items()
        }

    def _save_current_to_profile(self) -> None:
        cfg = self.config_mgr.config
        pname = str(self.mixer_profile_combo.currentData() or cfg.audio_mixer_active_profile)
        captured = self._capture_current_mixer_rules()
        existing = dict(cfg.audio_mixer_profiles.get(pname) or {})
        existing.update(captured)
        cfg.audio_mixer_profiles[pname] = existing
        cfg.audio_mixer_active_profile = pname
        self.config_mgr.save()
        self.mixer_status_lbl.setText(tr("mixer_saved", count=len(existing)))

    def _toggle_new_profile_row(self) -> None:
        vis = not self.mixer_new_row.isVisible()
        self.mixer_new_row.setVisible(vis)
        if vis:
            self.mixer_new_edit.clear()
            self.mixer_new_edit.setFocus()

    def _confirm_new_profile(self) -> None:
        name = self.mixer_new_edit.text().strip()
        if not name:
            return
        cfg = self.config_mgr.config
        cfg.audio_mixer_profiles[name] = self._capture_current_mixer_rules()
        cfg.audio_mixer_active_profile = name
        self.config_mgr.save()
        self.mixer_new_row.hide()
        self._populate_mixer_profiles()
        self.mixer_status_lbl.setText(tr("mixer_created", name=name))

    def _delete_mixer_profile(self) -> None:
        cfg = self.config_mgr.config
        pname = str(self.mixer_profile_combo.currentData() or "")
        if not pname or pname == "Стандартный" or len(cfg.audio_mixer_profiles) <= 1:
            return
        cfg.audio_mixer_profiles.pop(pname, None)
        cfg.audio_mixer_active_profile = next(iter(cfg.audio_mixer_profiles.keys()))
        self.config_mgr.save()
        self._populate_mixer_profiles()
        self.mixer_status_lbl.setText(tr("mixer_deleted", name=_localize_profile_name(pname)))

    def _on_devices_loaded(self, devs: dict, ov: dict, mv: dict, br: int) -> None:
        self._last_devs = devs
        self._last_ov = ov
        self._last_mv = mv
        self._last_br = br
        cfg = self.config_mgr.config
        self.out_combo.blockSignals(True)
        self.out_combo.clear()
        self.out_combo.addItem(tr("system_default"), "default")
        for d in devs.get("render", []):
            tag = " [★]" if d.get("is_default") else ""
            self.out_combo.addItem(f"{d.get('name')}{tag}", d.get("id"))
        self.out_combo.setCurrentIndex(max(0, self.out_combo.findData(cfg.output_device_id or "default")))
        self.out_combo.blockSignals(False)

        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem(tr("system_default"), "default")
        for d in devs.get("capture", []):
            tag = " [★]" if d.get("is_default") else ""
            self.mic_combo.addItem(f"{d.get('name')}{tag}", d.get("id"))
        self.mic_combo.setCurrentIndex(max(0, self.mic_combo.findData(cfg.input_device_id or "default")))
        self.mic_combo.blockSignals(False)

        self.set_output_volume_ui(int(ov.get("volume", 50)), bool(ov.get("muted", False)))

        mvol = int(mv.get("volume", 80))
        mmute = bool(mv.get("muted", False))
        self.mic_slider.blockSignals(True)
        self.mic_slider.setValue(mvol)
        self.mic_slider.blockSignals(False)
        self.mic_mute_btn.setChecked(mmute)
        self.mic_mute_btn.setIcon(get_icon("mic_mute" if mmute else "mic", size=15, color=self._icon_fg, accent=self._accent_hex))
        self.mic_pct_lbl.setText(tr("mute_short") if mmute else f"{mvol}%")

        if br >= 0:
            self.bright_slider.setEnabled(True)
            self.bright_slider.blockSignals(True)
            self.bright_slider.setValue(br)
            self.bright_slider.blockSignals(False)
            self.bright_lbl.setText(f"{br}%")
        else:
            self.bright_slider.setEnabled(False)
            self.bright_lbl.setText(tr("brightness_na"))

    def set_output_volume_ui(self, vol: int, muted: bool) -> None:
        self.out_slider.blockSignals(True)
        self.out_slider.setValue(vol)
        self.out_slider.blockSignals(False)
        self.out_mute_btn.setChecked(muted)
        self.out_mute_btn.setIcon(get_icon("mute" if muted else "volume", size=15, color=self._icon_fg, accent=self._accent_hex))
        self.out_pct_lbl.setText(tr("mute_short") if muted else f"{vol}%")

    def _on_out_dev_changed(self) -> None:
        dev_id = self.out_combo.currentData() or "default"
        self.config_mgr.config.output_device_id = dev_id
        self.config_mgr.save()
        self.refresh_hardware()

    def _on_mic_dev_changed(self) -> None:
        dev_id = self.mic_combo.currentData() or "default"
        self.config_mgr.config.input_device_id = dev_id
        self.config_mgr.save()
        self.refresh_hardware()

    def _on_out_volume_changed(self, val: int) -> None:
        self.out_pct_lbl.setText(tr("mute_short") if self.out_mute_btn.isChecked() else f"{val}%")
        dev = self.config_mgr.config.output_device_id or "default"
        threading.Thread(target=lambda: self.bridge.set_volume(val, "render", dev), daemon=True).start()

    def _toggle_out_mute(self, checked: bool) -> None:
        dev = self.config_mgr.config.output_device_id or "default"
        self.out_mute_btn.setIcon(get_icon("mute" if checked else "volume", size=15, color=self._icon_fg, accent=self._accent_hex))
        self.out_pct_lbl.setText(tr("mute_short") if checked else f"{self.out_slider.value()}%")
        threading.Thread(target=lambda: self.bridge.set_mute(checked, "render", dev), daemon=True).start()

    def _on_mic_volume_changed(self, val: int) -> None:
        self.mic_pct_lbl.setText(tr("mute_short") if self.mic_mute_btn.isChecked() else f"{val}%")
        dev = self.config_mgr.config.input_device_id or "default"
        threading.Thread(target=lambda: self.bridge.set_volume(val, "capture", dev), daemon=True).start()

    def _toggle_mic_mute(self, checked: bool) -> None:
        dev = self.config_mgr.config.input_device_id or "default"
        self.mic_mute_btn.setIcon(get_icon("mic_mute" if checked else "mic", size=15, color=self._icon_fg, accent=self._accent_hex))
        self.mic_pct_lbl.setText(tr("mute_short") if checked else f"{self.mic_slider.value()}%")
        threading.Thread(target=lambda: self.bridge.set_mute(checked, "capture", dev), daemon=True).start()

    def _on_brightness_changed(self, val: int) -> None:
        self.bright_lbl.setText(f"{val}%")
        threading.Thread(target=lambda: self.bridge.set_brightness(val), daemon=True).start()

    def update_system_state(self, snap: SystemStatsSnapshot) -> None:
        self._last_snap = snap
        gpu_s = f"{snap.gpu_load:.0f}%" if snap.gpu_load >= 0 else "—"
        g_txt = f"Игровой HUD: CPU {snap.cpu_percent:.0f}% • RAM {snap.ram_percent:.0f}% • GPU {gpu_s}" + (
            f" ({snap.active_game_name})" if getattr(snap, "in_game", False) and getattr(snap, "active_game_name", "") else ""
        )
        if hasattr(self, "game_stats_live_lbl") and self.game_stats_live_lbl.text() != g_txt:
            self.game_stats_live_lbl.setText(g_txt)
        mic_s = tr("mic_active", app=snap.mic_app or tr("active_word")) if snap.mic_in_use else tr("mic_idle")
        cam_s = tr("cam_active", app=snap.cam_app or tr("active_word")) if snap.cam_in_use else tr("cam_idle")
        priv_txt = tr("privacy_fmt", mic=mic_s, cam=cam_s)
        if self.privacy_lbl.text() != priv_txt:
            self.privacy_lbl.setText(priv_txt)
        wifi_txt = f"{tr('wifi')}: {snap.wifi_ssid or snap.net_interface}"
        if self.wifi_lbl.text() != wifi_txt:
            self.wifi_lbl.setText(wifi_txt)
        if snap.vpn_active:
            extra = " ".join(x for x in (snap.vpn_country, snap.vpn_ip) if x)
            vpn_txt = f"{tr('vpn')}: {snap.vpn_name}" + (f" ({extra})" if extra else "")
        else:
            vpn_txt = f"{tr('vpn')}: Off"
        if self.vpn_lbl.text() != vpn_txt:
            self.vpn_lbl.setText(vpn_txt)

        bt_names = [b.name for b in snap.bluetooth_devices]
        if getattr(self, "_last_bt_names", None) != bt_names:
            self._last_bt_names = list(bt_names)
            self.bt_list.clear()
            ico = get_icon("headphones", size=14, color=self._icon_fg, accent=self._accent_hex)
            for nm in bt_names:
                self.bt_list.addItem(QListWidgetItem(ico, nm))

    def _sync_autostart_btn_text(self) -> None:
        on = bool(self.config_mgr.config.autostart)
        self.autostart_btn.blockSignals(True)
        self.autostart_btn.setChecked(on)
        self.autostart_btn.blockSignals(False)
        self.autostart_btn.setText(tr("autostart_on") if on else tr("autostart_off"))

    def _toggle_autostart(self, checked: bool) -> None:
        self.config_mgr.config.autostart = bool(checked)
        self.config_mgr.save()
        try:
            proj_root = Path(__file__).resolve().parent.parent.parent.parent
            cmd = build_autostart_command(proj_root)
            set_autostart(bool(checked), cmd)
        except Exception:
            pass
        self._sync_autostart_btn_text()

    def _sync_blue_filter_btn(self) -> None:
        from ...core.win32_api import is_windows_night_light_enabled

        on = is_windows_night_light_enabled()
        self.config_mgr.config.blue_light_filter = on
        self.blue_filter_btn.blockSignals(True)
        self.blue_filter_btn.setChecked(on)
        self.blue_filter_btn.blockSignals(False)
        self.blue_filter_btn.setText(tr("blue_filter_on") if on else tr("blue_filter_off"))
        ico_col = "#FBBF24" if on else "#94A3B8"
        ico_acc = "#F59E0B" if on else self._accent_hex
        self.blue_filter_btn.setIcon(get_icon("sun" if on else "theme", size=15, color=ico_col, accent=ico_acc))
        if on:
            self.blue_filter_btn.setStyleSheet(
                "QPushButton { background: rgba(245, 158, 11, 0.22); border: 1px solid rgba(251, 191, 36, 0.65); color: #FDE68A; }"
            )
        else:
            self.blue_filter_btn.setStyleSheet("")

    def _toggle_blue_filter(self, checked: bool) -> None:
        from ...core.win32_api import set_windows_night_light

        actual = set_windows_night_light(bool(checked))
        self.config_mgr.config.blue_light_filter = bool(actual)
        self.config_mgr.save()
        self._sync_blue_filter_btn()
        self.blue_filter_toggled.emit(bool(actual))

    def _toggle_win_theme(self) -> None:
        toggle_windows_dark_theme()
        self.win_theme_btn.setText(tr("dark_theme"))
        self.win_theme_btn.setIcon(get_icon("theme", size=15, color=self._icon_fg, accent=self._accent_hex))
        self.theme_toggled.emit()

    def _power_clicked(self, action: str) -> None:
        now = time.monotonic()
        if self._pending_power and self._pending_power[0] == action and now - self._pending_power[1] <= 4.0:
            self._pending_power = None
            self.power_hint.hide()
            if action == "lock":
                lock_workstation()
            elif action == "sleep":
                sleep_computer()
            elif action == "restart":
                restart_computer()
            elif action == "shutdown":
                shutdown_computer()
        else:
            self._pending_power = (action, now)
            self.power_hint.setText(f"[{action.upper()}] {tr('confirm_twice')}")
            self.power_hint.show()
