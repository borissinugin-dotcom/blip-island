"""Application entrypoint wiring all services, overlay windows, hotkeys, tray icon, and live hot-reload."""

from __future__ import annotations

import argparse
import faulthandler
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-blink-features=AutomationControlled")

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QGuiApplication, QImage
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
try:
    import PyQt6.QtWebEngineWidgets  # noqa: F401
except Exception:
    pass

from borh_island.core.config import ConfigManager, DEFAULT_HOTKEYS
from borh_island.core.hot_reload import HotReloadManager
from borh_island.core.i18n import set_language, tr
from borh_island.core.native_bridge import NativeBridge
from borh_island.core.screen_recorder import ScreenRecorder
from borh_island.core.storage import LocalStorage, ReminderItem
from borh_island.core.win32_api import (
    GlobalHotkeyManager,
    build_autostart_command,
    is_airplane_mode_on,
    is_desktop_clean,
    lock_workstation,
    open_windows_uri,
    restart_computer,
    restart_graphics_displays,
    reveal_in_explorer,
    set_app_user_model_id,
    set_autostart,
    show_desktop_clean,
    shutdown_computer,
    sleep_computer,
    toggle_airplane_mode_async,
    toggle_amnezia_vpn_async,
    toggle_radio_async,
    toggle_windows_dark_theme,
)
from borh_island.core.winrt_worker import WinRTWorker
from borh_island.services.ai_service import AIService, VoiceRecorder
from borh_island.services.clipboard_service import ClipboardService
from borh_island.services.command_service import CommandService
from borh_island.services.discord_service import DiscordPresenceService
from borh_island.services.media_service import MediaService
from borh_island.services.system_service import SystemMonitorService, SystemStatsSnapshot
from borh_island.services.weather_service import WeatherService
from borh_island.ui.atmosphere import resolve_scene_state
from borh_island.ui.icons import app_pill_icon
from borh_island.ui.live_cards import LiveCardAction, LiveCardManager, LiveCardSpec
from borh_island.ui.theme import apply_app_font, resolve_palette

_SINGLE_INSTANCE_KEY = "BorhIslandSingleInstance_v1"

_make_tray_icon = app_pill_icon


class BorhIslandController(QObject):
    """Coordinates lifecycle, hotkeys, capture tools, live cards, and hot-reload."""

    ocr_finished = pyqtSignal(str)
    stt_finished = pyqtSignal(str, str)  # (text, error)
    shot_translated = pyqtSignal(str, str)  # (source_text, translated_text)

    def __init__(self, initial_expand: bool = False, initial_tab: int = 0) -> None:
        super().__init__()
        self.config_mgr = ConfigManager()
        cfg = self.config_mgr.config
        if not getattr(cfg, "show_system_notifications", False):
            cfg.show_system_notifications = True
            self.config_mgr.save()
        set_language(cfg.language)

        self.storage = LocalStorage(self.config_mgr.data_dir / "borh_island.db")
        self.bridge = NativeBridge(self.config_mgr.data_dir)
        self.winrt_worker = WinRTWorker()
        self.media_winrt_worker = WinRTWorker()

        # Services
        self.media_service = MediaService(self.media_winrt_worker, self.bridge, self)
        self.clipboard_service = ClipboardService(
            self.storage, self.config_mgr, self.config_mgr.data_dir / "clipboard_images", self
        )
        self.weather_service = WeatherService()
        self.command_service = CommandService()
        self.ai_service = AIService()
        self.voice_recorder = VoiceRecorder(self.config_mgr.data_dir, self)
        self.system_service = SystemMonitorService(self.config_mgr, winrt_worker=self.winrt_worker, parent=self)
        self.discord_service = DiscordPresenceService(self)
        self.discord_service.attach_audio_bridge(self.bridge, self.config_mgr)
        self.screen_recorder = ScreenRecorder(self.bridge, cfg.effective_recording_dir(self.config_mgr.data_dir))
        from borh_island.services.agents_service import AgentsService

        self.agents_service = AgentsService(self.config_mgr.data_dir, cfg.agent_server_port, self)
        self.agents_service.agent_event.connect(self._on_agent_event)
        self.agents_service.permission_requested.connect(self._on_agent_permission)
        self.agents_service.choice_requested.connect(self._on_agent_choice)
        if cfg.agent_server_enabled:
            self.agents_service.start()
        self.hotkeys = GlobalHotkeyManager(self)
        self._airplane_state = is_airplane_mode_on()
        self._desktop_clean_state = is_desktop_clean()
        self._preview_snapshot: dict | None = None

        # Windows (imported dynamically so HotReloadManager can reload them cleanly)
        from borh_island.ui.blue_light import BlueLightFilterOverlay
        from borh_island.ui.command_bar import CommandBarWindow
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.island_window import IslandWindow

        apply_app_font(QApplication.instance(), cfg.ui_font_family, cfg.custom_font_path)

        pal = self._resolve_island_palette(cfg)
        self.blue_filter_overlay = BlueLightFilterOverlay()
        self.blue_filter_overlay.set_exclude_capture(cfg.exclude_from_capture)
        self.blue_filter_overlay.set_enabled(False)
        self.live_cards = LiveCardManager(pal)
        self.live_cards.set_glass_mode(str(getattr(cfg, "glass_mode", "liquid") or "liquid").lower())
        self.live_cards.set_exclude_capture(cfg.exclude_from_capture)
        self.panel = self._create_panel(pal)
        self.island = IslandWindow(self.config_mgr, self.bridge, self.panel)
        self.live_cards.bind_island(self.island)
        self.island.setWindowIcon(_make_tray_icon())
        self.island.set_art_resolver(self.media_service.get_art_qimage)
        self._wire_panel(self.panel)
        self.command_bar = self._create_command_bar(pal)
        self.settings_win = None
        self._active_overlay = None
        self._editor_windows: list[QObject] = []
        self._mic_muted = False
        self._last_screenshot_toast_t = 0.0
        self._last_media_snap = None
        self._last_sys_snap = None

        # Connect core persistent signals -> controller/island
        self.island.geometry_changed.connect(self.live_cards.update_anchor)
        self.island.wheel_volume_changed.connect(self._on_wheel_volume)
        self.island.media_prev_requested.connect(self.media_service.prev_track)
        self.island.media_play_pause_requested.connect(self.media_service.play_pause)
        self.island.media_next_requested.connect(self.media_service.next_track)
        self.island.media_open_source_requested.connect(self.media_service.open_source_app)
        self.island.media_seek_requested.connect(self.media_service.seek)

        self.media_service.state_changed.connect(self._on_media_changed)
        self.media_service.spectrum_updated.connect(self._on_spectrum_updated)
        self.clipboard_service.item_captured.connect(self._on_clipboard_captured)
        self.system_service.stats_updated.connect(self._on_system_stats)
        self.system_service.vpn_changed.connect(self._on_vpn_changed)
        self.system_service.download_started.connect(self._on_download_started)
        self.system_service.download_completed.connect(self._on_download_done)
        self.system_service.notification_received.connect(self._on_system_notification)
        self.discord_service.voice_state_changed.connect(self._on_discord_voice_changed)
        self.discord_service.running_changed.connect(self._on_discord_running_changed)
        self.voice_recorder.level_changed.connect(self._on_dictation_level)

        self.ocr_finished.connect(self._on_ocr_done)
        self.stt_finished.connect(self._on_stt_done)
        self.shot_translated.connect(self._on_shot_translated)
        self.hotkeys.triggered.connect(self._on_hotkey)

        # Hot reload watcher
        pkg_root = Path(__file__).resolve().parent / "borh_island"
        self.hot_reloader = HotReloadManager(
            pkg_root,
            enabled=(bool(cfg.hot_reload) and not getattr(sys, "frozen", False)),
            parent=self,
        )
        self.hot_reloader.ui_reload_requested.connect(self._on_hot_reload_ui)
        self.hot_reloader.native_recompile_requested.connect(self._on_native_recompile)
        self.hot_reloader.full_restart_requested.connect(self._on_full_restart_requested)

        # Single-instance listener
        self._ipc_server = QLocalServer(self)
        QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)
        if self._ipc_server.listen(_SINGLE_INSTANCE_KEY):
            self._ipc_server.newConnection.connect(self._on_ipc_connection)

        self._setup_tray()
        self._bind_hotkeys()

        # Start services cleanly
        self.media_service.start()
        self.media_service.set_spectrum_enabled(cfg.edge_equalizer, cfg.output_device_id or "default")
        self.clipboard_service.start()
        self.system_service.start()
        self.discord_service.configure(cfg.discord_rpc_enabled, cfg.discord_client_id)
        self.discord_service.start()
        self._sync_discord_presence()

        self.island.show()
        if initial_expand:
            self.island.set_expanded(True)
            self.panel.switch_tab(initial_tab)
        if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen" and "unittest" not in sys.modules:
            if cfg.autostart:
                threading.Thread(
                    target=lambda: set_autostart(True, build_autostart_command(Path(__file__).resolve().parent)),
                    daemon=True,
                ).start()
            QTimer.singleShot(350, self._show_startup_trial_dialog)

    def _show_startup_trial_dialog(self) -> None:
        from borh_island.core.licensing import get_license_manager

        st = get_license_manager().get_status(force_refresh=True)
        if st.is_permanent_pro:
            return
        self.island.set_expanded(True)
        self.panel.show_pro_page(on_unlocked=self._apply_config_live)

    # ------------------------------------------------------------------ Builders & Panel Signal Wiring
    def _create_panel(self, pal):
        from borh_island.ui.expanded_panel import ExpandedPanel

        panel = ExpandedPanel(
            self.config_mgr,
            self.storage,
            self.bridge,
            self.media_service,
            self.clipboard_service,
            self.weather_service,
            None,
            None,
            pal,
        )
        eff_fam = apply_app_font(None, self.config_mgr.config.ui_font_family, self.config_mgr.config.custom_font_path)
        panel.apply_palette(pal, font_family=eff_fam)
        return panel

    def _wire_panel(self, panel) -> None:
        panel.collapse_requested.connect(lambda: self.island.set_expanded(False))
        panel.settings_requested.connect(lambda: self.open_settings())
        panel.home_tab.tool_triggered.connect(self._on_tool_action)
        panel.home_tab.control_toggled.connect(self._on_home_control_toggled)
        panel.home_tab.power_requested.connect(self._on_home_power_requested)
        panel.home_tab.discord_action.connect(self._on_discord_action)
        panel.home_tab.dnd_requested.connect(self._on_dnd_requested)
        panel.home_tab.update_dnd_status(getattr(self.config_mgr.config, "dnd_until_ts", 0.0))
        panel.home_tab.update_discord_voice(
            self.discord_service._in_voice,
            self.discord_service._muted,
            self.discord_service._deafened,
            running=self.discord_service.is_discord_running(),
        )
        panel.character_tab.config_changed.connect(self._apply_config_live)
        panel.character_tab.character_picked.connect(self._on_character_picked)
        panel.character_tab.command_bar_requested.connect(lambda: self.command_bar.open_bar())
        panel.character_tab.settings_requested.connect(lambda: self.open_settings())
        panel.character_tab.hot_reload_requested.connect(lambda: self._on_hot_reload_ui(["manual"]))
        panel.character_tab.quit_requested.connect(self.shutdown_app)
        panel.notes_tab.timer_updated.connect(self.island.update_timer_badge)
        panel.notes_tab.timer_finished.connect(self._on_timer_finished)
        panel.notes_tab.reminder_triggered.connect(self._on_reminder_triggered)
        panel.weather_tab.weather_updated.connect(self._on_weather_updated)
        panel.weather_tab.atmosphere_changed.connect(self._on_atmosphere_changed)
        panel.controls_tab.theme_toggled.connect(self._apply_config_live)
        panel.controls_tab.blue_filter_toggled.connect(self._on_blue_filter_toggled)
        panel.controls_tab.atmosphere_changed.connect(self._on_atmosphere_changed)
        panel.controls_tab.color_previewed.connect(self.island.preview_accent_color)
        panel.controls_tab.config_applied.connect(self._apply_config_live)
        panel.controls_tab.settings_requested.connect(lambda: self.open_settings())
        panel.controls_tab.restart_app_requested.connect(lambda: self._on_full_restart_requested(["manual_restart"]))

    def _create_command_bar(self, pal):
        from borh_island.ui.command_bar import CommandBarWindow

        bar = CommandBarWindow(self.command_service, pal)
        bar.timer_requested.connect(self._start_timer_from_command)
        bar.reminder_requested.connect(self._add_reminder_from_command)
        bar.system_command_requested.connect(self._run_system_cmd)
        return bar

    # ------------------------------------------------------------------ Tray & IPC
    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_tray_icon(), self)
        self.tray.setToolTip(tr("app_title"))
        menu = QMenu()
        self.act_open = menu.addAction(tr("open_island"), self.island.toggle_expanded)
        self.act_cmd = menu.addAction(tr("command_bar"), self.command_bar.open_bar)
        self.act_through = menu.addAction(tr("through_mode"))
        self.act_through.setCheckable(True)
        self.act_through.setChecked(bool(self.config_mgr.config.click_through))
        self.act_through.toggled.connect(self._toggle_click_through)
        menu.addSeparator()
        self.act_settings = menu.addAction(tr("settings"), lambda: self.open_settings())
        menu.addSeparator()
        self.act_quit = menu.addAction(tr("quit"), self.shutdown_app)
        self._tray_menu = menu
        self._refresh_tray_menu()
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._tray_retries = 0
        self._ensure_tray_visible()

    def _ensure_tray_visible(self) -> None:
        if not hasattr(self, "tray"):
            return
        if QSystemTrayIcon.isSystemTrayAvailable() or self._tray_retries >= 12:
            self.tray.show()
            return
        self._tray_retries += 1
        QTimer.singleShot(2500, self._ensure_tray_visible)

    def _refresh_tray_menu(self) -> None:
        if not hasattr(self, "act_open"):
            return
        hk = self.config_mgr.config.hotkeys or DEFAULT_HOTKEYS
        fmt = lambda base, key: f"{base}\t{key}" if key else base
        self.act_open.setText(fmt(tr("open_island"), hk.get("toggle_island", "Ctrl+Alt+I")))
        self.act_cmd.setText(fmt(tr("command_bar"), hk.get("command_bar", "Alt+Space")))
        self.act_through.setText(fmt(tr("through_mode"), hk.get("toggle_through", "Ctrl+Alt+Shift+T")))
        self.act_through.blockSignals(True)
        self.act_through.setChecked(bool(self.config_mgr.config.click_through))
        self.act_through.blockSignals(False)
        self.act_settings.setText(fmt(tr("settings"), hk.get("settings", "Ctrl+Alt+P")))
        self.act_quit.setText(fmt(tr("quit"), hk.get("quit_app", "")))

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.island.toggle_expanded()

    def _on_ipc_connection(self) -> None:
        sock: QLocalSocket | None = self._ipc_server.nextPendingConnection()
        if sock:
            sock.waitForReadyRead(300)
            cmd = bytes(sock.readAll()).decode("utf-8", errors="ignore").strip().upper()
            sock.close()
            if cmd == "QUIT":
                self.shutdown_app()
                return
            if self.island.isMinimized():
                self.island.showNormal()
            self.island.show()
            self.island.set_expanded(True)

    # ------------------------------------------------------------------ Hotkeys
    def _bind_hotkeys(self) -> None:
        bindings = dict(self.config_mgr.config.hotkeys or DEFAULT_HOTKEYS)
        for item in self.storage.list_launcher_items():
            if item.hotkey.strip():
                bindings[f"launch:{item.id}"] = item.hotkey.strip()
        self.hotkeys.register_all(bindings)
        self._refresh_tray_menu()

    def _on_hotkey(self, action_id: str) -> None:
        if action_id.startswith("launch:"):
            lid = int(action_id.split(":", 1)[1])
            for it in self.storage.list_launcher_items():
                if it.id == lid:
                    self.panel.home_tab._launch_item(it.target)
                    return
            return
        mapping = {
            "toggle_island": self.island.toggle_expanded,
            "command_bar": self.command_bar.open_bar,
            "clipboard": lambda: (self.island.set_expanded(True), self.panel.switch_tab_by_key("notes")),
            "dictation": lambda: self._on_tool_action("dictate"),
            "screenshot_area": lambda: self._on_tool_action("screenshot_area"),
            "screenshot_full": lambda: self._on_tool_action("screenshot_full"),
            "ocr": lambda: self._on_tool_action("ocr"),
            "qr_scan": lambda: self._on_tool_action("qr_scan"),
            "color_picker": lambda: self._on_tool_action("color_picker"),
            "screen_record": lambda: self._on_tool_action("record"),
            "paste_plain": self.clipboard_service.paste_plain_current_clipboard,
            "mute_mic": self._toggle_mic_mute,
            "toggle_theme": self._toggle_theme_hotkey,
            "toggle_through": lambda: self._toggle_click_through(not self.config_mgr.config.click_through),
            "settings": lambda: self.open_settings(),
            "quit_app": self.shutdown_app,
        }
        fn = mapping.get(action_id)
        if fn:
            fn()

    # ------------------------------------------------------------------ Event Dispatchers
    def _on_clipboard_captured(self, kind: str, payload: str, source_app: str) -> None:
        if time.monotonic() - self._last_screenshot_toast_t < 0.8:
            return
        src = source_app.replace(".exe", "") if source_app else ""
        badge = src if src else "Буфер"
        if kind == "image":
            qimg = QImage(payload)
            self.island.show_activity(
                badge,
                "Скопировано изображение",
                accent_hex="#38BDF8",
                thumb=qimg,
                duration_ms=4200,
                action_cb=lambda: (self.island.set_expanded(True), self.panel.switch_tab_by_key("notes")),
                icon_name="clipboard",
            )
        else:
            preview = " ".join(payload.split())
            if len(preview) > 38:
                preview = preview[:37] + "…"
            self.island.show_activity(
                badge,
                preview or "Скопирован текст",
                accent_hex="#38BDF8",
                duration_ms=4200,
                action_cb=lambda: (self.island.set_expanded(True), self.panel.switch_tab_by_key("notes")),
                icon_name="clipboard",
            )

    def _on_media_changed(self, snap) -> None:
        self._last_media_snap = snap
        self.island.update_media(snap)
        self.panel.home_tab.update_media_state(snap)
        if self.island._effective_appearance_mode() == "music":
            pal = self.island.palette
            sig_m = (pal.accent, pal.bg_island)
            if sig_m != getattr(self, "_last_music_chrome_sig", None):
                self._last_music_chrome_sig = sig_m
                self.live_cards.set_palette(pal)
                self.command_bar.apply_palette(pal)
        self._sync_discord_presence()

    def _on_spectrum_updated(self, bands: list[float]) -> None:
        playing = bool(getattr(self.island, "_playing", False))
        eff_bands = bands if playing else [0.0] * 20
        if self.island.isVisible():
            self.island.update_spectrum(eff_bands)
        if self.island.is_expanded and self.panel.home_tab.isVisible():
            self.panel.home_tab.update_spectrum(eff_bands)

    def _dnd_active(self) -> bool:
        return time.time() < float(getattr(self.config_mgr.config, "dnd_until_ts", 0.0) or 0.0)

    def _push_live_card(self, spec: LiveCardSpec, *, critical: bool = False) -> None:
        if not critical and self._dnd_active():
            return
        self.live_cards.push_card(spec)

    def _on_system_stats(self, snap: SystemStatsSnapshot) -> None:
        self._last_sys_snap = snap
        air_attr = getattr(snap, "airplane_on", None)
        self._airplane_state = bool(air_attr) if air_attr is not None else is_airplane_mode_on()
        self._desktop_clean_state = bool(getattr(snap, "desktop_clean", self._desktop_clean_state))
        self.island.update_system_badges(snap)
        self.panel.home_tab.update_system_state(
            snap,
            airplane_on=self._airplane_state,
            desktop_clean=self._desktop_clean_state,
            night_light_on=getattr(snap, "night_light_on", None),
        )
        self.panel.home_tab.update_dnd_status(getattr(self.config_mgr.config, "dnd_until_ts", 0.0))
        self.panel.home_tab.update_discord_voice(
            self.discord_service._in_voice,
            self.discord_service._muted,
            self.discord_service._deafened,
            running=self.discord_service.is_discord_running(),
        )
        self.panel.controls_tab.update_system_state(snap)
        sw = getattr(self, "settings_win", None)
        if sw is not None and sw.isVisible() and hasattr(sw, "update_game_stats_preview"):
            sw.update_game_stats_preview(
                snap.cpu_percent,
                snap.ram_percent,
                snap.gpu_load,
                snap.active_game_name if snap.in_game else "",
            )
        self._sync_discord_presence()

    def _on_weather_updated(self, rep) -> None:
        self.island.update_weather(rep)
        if hasattr(self, "panel") and hasattr(self.panel, "weather_tab"):
            self.panel.weather_tab._on_weather_ready(rep)
        raining, wkind, _ = self.island._is_card_rain_active()
        self.live_cards.set_rain_state(raining, stormy=(wkind in ("thunder", "lightning")))
        self._sync_discord_presence()

    def _on_atmosphere_changed(self) -> None:
        self.island._exp_composite_cache = None
        self.island._exp_surface_cache = None
        self.island._video_glass_cache = None
        self.island._exp_off_cache = None
        self._apply_config_live()
        self.island._sync_palettes(force_panel=True)
        raining, wkind, _ = self.island._is_card_rain_active()
        self.live_cards.set_rain_state(raining, stormy=(wkind in ("thunder", "lightning")))
        self.island.update()

    def _sync_discord_presence(self) -> None:
        cfg = self.config_mgr.config
        ms = self._last_media_snap
        active_m = getattr(ms, "active", None) if ms is not None else None
        playing = bool(active_m and getattr(active_m, "status", "") == "Playing")
        title = getattr(active_m, "title", "") if active_m else ""
        artist = getattr(active_m, "artist", "") if active_m else ""
        source = getattr(active_m, "source_name", "") if active_m else ""

        ss = self._last_sys_snap
        in_game = bool(ss and getattr(ss, "in_game", False))
        game_name = getattr(ss, "active_game_name", "") if ss else ""
        cpu_pct = float(getattr(ss, "cpu_percent", 0.0)) if ss else 0.0
        gpu_pct = float(getattr(ss, "gpu_load", 0.0)) if ss else 0.0
        rec = bool(self.screen_recorder.is_recording)
        timer_t = self.island._timer_text
        if hasattr(self.system_service, "set_telemetry_demand"):
            self.system_service.set_telemetry_demand(bool(self.island._expanded))

        sig = (
            cfg.atmosphere_preview,
            self.island._weather_symbol,
            cfg.atmosphere_sky_mode,
            cfg.atmosphere_weather_mode,
            cfg.atmosphere_moon_mode,
            playing,
            title,
            artist,
            source,
            in_game,
            game_name,
            int(round(cpu_pct)) if in_game else 0,
            int(round(gpu_pct)) if in_game else 0,
            rec,
            timer_t,
        )
        if getattr(self, "_last_dc_sync_sig", None) == sig and (time.monotonic() - getattr(self, "_last_dc_sync_ts", 0.0)) < 15.0:
            return
        self._last_dc_sync_sig = sig
        self._last_dc_sync_ts = time.monotonic()

        segment, _, _ = resolve_scene_state(
            cfg.atmosphere_preview,
            self.island._weather_symbol,
            lat=cfg.weather_lat,
            lon=cfg.weather_lon,
            sky_mode=cfg.atmosphere_sky_mode,
            weather_mode=cfg.atmosphere_weather_mode,
            moon_mode=cfg.atmosphere_moon_mode,
        )
        is_day = segment != "night"

        self.discord_service.update_from_island(
            playing=playing,
            title=title,
            artist=artist,
            source=source,
            in_game=in_game,
            game_name=game_name,
            cpu_pct=cpu_pct,
            gpu_pct=gpu_pct,
            recording=rec,
            timer_text=timer_t,
            is_day=is_day,
        )

    def _on_wheel_volume(self, vol: int) -> None:
        self.panel.controls_tab.set_output_volume_ui(vol, False)
        self.live_cards.push_card(
            LiveCardSpec(card_id="osd_vol", title=f"Volume: {vol}%", icon="🔊", progress=vol, timeout_ms=1600)
        )

    def _on_vpn_changed(self, active: bool, iface: str, country: str, ip: str) -> None:
        title = f"VPN Connected ({country or iface})" if active else "VPN Disconnected"
        body = f"{iface} • {ip}" if (active and ip) else iface
        self._push_live_card(LiveCardSpec(card_id="vpn", title=title, body=body, icon="🛡", timeout_ms=4200))

    def _on_download_started(self, full_path: str) -> None:
        p = Path(full_path)
        self.island.show_activity(
            "Загрузка",
            p.name,
            accent_hex="#3B82F6",
            duration_ms=4200,
            icon_name="download",
        )

    def _on_download_done(self, full_path: str) -> None:
        p = Path(full_path)
        self.island.show_activity(
            "Скачано",
            p.name,
            accent_hex="#22C55E",
            duration_ms=5200,
            action_cb=lambda: reveal_in_explorer(str(p)),
            icon_name="download",
        )

    def _on_system_notification(self, app_name: str, title: str, body: str) -> None:
        if self._dnd_active():
            return
        if time.monotonic() - self._last_screenshot_toast_t < 2.2:
            return
        badge = (app_name or "Уведомление").strip()[:18]
        t = (title or "").strip()
        b = (body or "").strip().replace("\n", " ")
        if t and b and t.lower() != badge.lower():
            text = f"{t} — {b}"
        else:
            text = b or t
        if not text:
            return
        self.island.show_activity(
            badge,
            text[:140],
            accent_hex="#38BDF8",
            duration_ms=4800,
            icon_name="bell",
        )

    def _force_poll_radios(self) -> None:
        self.system_service._force_radio_refresh = True
        self.system_service.poll_now()

    def _on_home_control_toggled(self, key: str) -> None:
        if key in ("wifi", "bt"):
            kind = "wifi" if key == "wifi" else "bluetooth"

            def _tog() -> None:
                res = self.winrt_worker.toggle_radio(kind)
                ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res[0] if isinstance(res, (tuple, list)) and res else res)
                if not ok:
                    toggle_radio_async(kind)

            threading.Thread(target=_tog, daemon=True).start()
            QTimer.singleShot(450, self._force_poll_radios)
            QTimer.singleShot(1300, self._force_poll_radios)
            label = "Wi-Fi" if key == "wifi" else "BT"
            txt = "Переключение Wi-Fi…" if key == "wifi" else "Переключение Bluetooth…"
            col = "#2563EB" if key == "wifi" else "#3B82F6"
            ico = "wifi" if key == "wifi" else "bluetooth"
            self.island.show_activity(label, txt, accent_hex=col, duration_ms=1800, icon_name=ico)
        elif key == "airplane":
            self._airplane_state = not bool(getattr(self, "_airplane_state", False))
            toggle_airplane_mode_async(enable=self._airplane_state)
            if hasattr(self, "_last_sys_snap") and self._last_sys_snap is not None:
                self.panel.home_tab.update_system_state(
                    self._last_sys_snap,
                    airplane_on=self._airplane_state,
                    desktop_clean=self._desktop_clean_state,
                )
            QTimer.singleShot(700, self._force_poll_radios)
            QTimer.singleShot(1700, self._force_poll_radios)
            self.island.show_activity(
                "Режим полёта",
                "Режим полёта включён" if self._airplane_state else "Режим полёта выключен",
                accent_hex="#F59E0B",
                duration_ms=2000,
                icon_name="plane",
            )
        elif key == "vpn":
            launched = toggle_amnezia_vpn_async()
            if not launched:
                open_windows_uri("ms-settings:network-vpn")
            QTimer.singleShot(1200, self._force_poll_radios)
            msg = "Переключение AmneziaVPN…" if launched else "Настройки VPN Windows"
            self.island.show_activity("AmneziaVPN" if launched else "VPN", msg, accent_hex="#22C55E", duration_ms=2000, icon_name="vpn")
        elif key == "desktop":
            self._desktop_clean_state = bool(show_desktop_clean())
            if hasattr(self, "_last_sys_snap") and self._last_sys_snap is not None:
                self._last_sys_snap.desktop_clean = self._desktop_clean_state
                self.panel.home_tab.update_system_state(
                    self._last_sys_snap,
                    airplane_on=self._airplane_state,
                    desktop_clean=self._desktop_clean_state,
                )
            self.island.show_activity(
                "Стол",
                "Значки рабочего стола скрыты" if self._desktop_clean_state else "Значки рабочего стола показаны",
                accent_hex="#10B981",
                duration_ms=1600,
                icon_name="desktop",
            )
        elif key == "night_light":
            from borh_island.core.win32_api import is_windows_night_light_enabled, set_windows_night_light
            target = not is_windows_night_light_enabled()
            actual = bool(set_windows_night_light(target))
            if hasattr(self, "_last_sys_snap") and self._last_sys_snap is not None:
                self.panel.home_tab.update_system_state(
                    self._last_sys_snap,
                    airplane_on=self._airplane_state,
                    desktop_clean=self._desktop_clean_state,
                    night_light_on=actual,
                )
            if hasattr(self.panel, "controls_tab"):
                self.panel.controls_tab._sync_blue_filter_btn()
            self.island.show_activity(
                "Синий свет",
                "Ночной свет включён (синий убран)" if actual else "Ночной свет выключен",
                accent_hex="#F59E0B",
                duration_ms=1800,
                icon_name="sun",
            )

    def _on_home_power_requested(self, action: str) -> None:
        if action == "lock":
            lock_workstation()
        elif action == "sleep":
            sleep_computer()
        elif action == "restart":
            restart_computer()
        elif action == "shutdown":
            shutdown_computer()

    def _on_discord_action(self, action: str) -> None:
        running = self.discord_service.is_discord_running()
        if not running:
            self.island.show_activity("Discord", "Discord не запущен", accent_hex="#818CF8", duration_ms=2200, icon_name="discord")
            self.panel.home_tab.update_discord_voice(False, False, False, running=False)
            return
        if action == "mute":
            was_deaf = self.discord_service._deafened
            m = self.discord_service.toggle_mute()
            msg = "Микрофон выключен" if m else ("Микрофон и звук включены" if was_deaf else "Микрофон включён")
            self.island.show_activity("Discord", msg, accent_hex="#818CF8", duration_ms=1700, icon_name="mic")
        elif action == "deafen":
            d = self.discord_service.toggle_deafen()
            self.island.show_activity("Discord", "Звук и микрофон выключены" if d else "Звук и микрофон включены", accent_hex="#818CF8", duration_ms=1700, icon_name="discord")
        elif action == "leave":
            self.discord_service.leave_voice_channel()
            self.island.show_activity("Discord", "Выход из голосового канала", accent_hex="#EF4444", duration_ms=1800, icon_name="discord")

    def _on_discord_running_changed(self, running: bool) -> None:
        if hasattr(self, "panel"):
            self.panel.home_tab.update_discord_voice(
                self.discord_service._in_voice,
                self.discord_service._muted,
                self.discord_service._deafened,
                running=running,
            )

    def _on_discord_voice_changed(self, in_voice: bool, muted: bool, deafened: bool) -> None:
        if hasattr(self, "panel"):
            self.panel.home_tab.update_discord_voice(
                in_voice, muted, deafened, running=self.discord_service.is_discord_running()
            )

    def _on_dnd_requested(self, minutes: int) -> None:
        cfg = self.config_mgr.config
        cfg.dnd_until_ts = (time.time() + float(minutes) * 60.0) if minutes > 0 else 0.0
        self.config_mgr.save()
        if hasattr(self, "panel"):
            self.panel.home_tab.update_dnd_status(cfg.dnd_until_ts)
        txt = f"Не беспокоить: {minutes} мин" if minutes > 0 else "Режим «Не беспокоить» выключен"
        self.island.show_activity("DND", txt, accent_hex="#A78BFA" if minutes > 0 else "#64748B", duration_ms=2000, icon_name="dnd")

    def _on_timer_finished(self, label: str) -> None:
        self.island.set_timer_alarm(True)
        self.island.show_activity("⏰ Таймер", f"{label} завершён!", accent_hex="#F59E0B", duration_ms=6000)
        QTimer.singleShot(15000, lambda: self.island.set_timer_alarm(False))
        self.live_cards.push_card(
            LiveCardSpec(
                card_id="timer_alarm",
                title=f"⏰ {label} Finished!",
                body="Нажмите Закрыть чтобы остановить сигнал.",
                icon="⏰",
                timeout_ms=0,
                actions=[LiveCardAction("Закрыть", lambda: self.island.set_timer_alarm(False), primary=True)],
            )
        )

    def _on_reminder_triggered(self, rem: ReminderItem) -> None:
        self.live_cards.push_card(
            LiveCardSpec(
                card_id=f"rem_{rem.id}",
                title="🔔 Reminder",
                body=rem.text,
                icon="🔔",
                timeout_ms=0,
                actions=[
                    LiveCardAction("Snooze 5m", lambda t=rem.text: self._snooze_reminder(t)),
                    LiveCardAction("Done", lambda: None, primary=True),
                ],
            )
        )

    def _snooze_reminder(self, text: str) -> None:
        self.storage.add_reminder(text, time.time() + 300, "")
        self.panel.notes_tab.refresh_reminders()

    # ------------------------------------------------------------------ Screen Tools, Dictation & Commands
    def _on_tool_action(self, tid: str) -> None:
        if tid == "reload_hotkeys":
            self._bind_hotkeys()
        elif tid == "settings":
            self.open_settings()
        elif tid == "command_bar":
            self.command_bar.open_bar()
        elif tid == "screenshot_area":
            self._start_overlay("screenshot")
        elif tid == "screenshot_full":
            self._capture_fullscreen()
        elif tid == "ocr":
            self._start_overlay("ocr")
        elif tid == "color_picker":
            self._start_overlay("color")
        elif tid == "restart_screens":
            restart_graphics_displays()
            self.island.show_activity("Экраны", "Перезапуск графики (Win+Ctrl+Shift+B)", accent_hex="#38BDF8", duration_ms=2200, icon_name="monitor_reset")
        elif tid == "qr_scan":
            self._start_overlay("qr")
        elif tid == "record":
            self._toggle_recording()
        elif tid == "dictate":
            self._toggle_dictation()

    def _start_overlay(self, mode: str) -> None:
        from borh_island.ui.screen_tools import ScreenCaptureOverlay

        cfg = self.config_mgr.config
        ov = ScreenCaptureOverlay(mode, audio_mode=getattr(cfg, "recording_audio_mode", "system"))
        self._active_overlay = ov
        ov.audio_mode_changed.connect(self._on_overlay_audio_mode_changed)
        ov.area_captured.connect(lambda m, qimg, rect: self._on_area_captured(mode, qimg, rect))
        ov.color_picked.connect(self._on_color_picked)
        ov.qr_decoded.connect(self._on_qr_decoded)

    def _on_overlay_audio_mode_changed(self, amode: str) -> None:
        self.config_mgr.config.recording_audio_mode = amode
        self.config_mgr.save()
        if hasattr(self, "panel") and hasattr(self.panel, "home_tab"):
            self.panel.home_tab.apply_home_layout()

    def _capture_fullscreen(self) -> None:
        from borh_island.ui.screen_tools import _grab_active_screen_qimage

        qimg, rect = _grab_active_screen_qimage()
        if not qimg.isNull():
            self._on_area_captured("screenshot", qimg, rect)

    def _on_area_captured(self, mode: str, qimg: QImage, rect) -> None:
        cfg = self.config_mgr.config
        if mode == "screenshot":
            out_dir = cfg.effective_screenshot_dir(self.config_mgr.data_dir)
            out_path = out_dir / f"shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            qimg.save(str(out_path), "PNG")
            cb = QGuiApplication.clipboard()
            if cb:
                cb.setImage(qimg)
            self._last_screenshot_toast_t = time.monotonic()
            self.island.show_activity(
                "Скрин",
                out_path.name,
                accent_hex="#22C55E",
                thumb=qimg,
                duration_ms=5200,
                action_cb=lambda p=out_path, im=qimg: self._open_editor(im, p),
                icon_name="screenshot",
            )
            self.live_cards.push_card(
                LiveCardSpec(
                    card_id="shot_saved",
                    title="Скриншот готов",
                    body=f"{out_path.name}\nПеретащите миниатюру в любой чат",
                    icon="📷",
                    timeout_ms=7500,
                    thumbnail=qimg,
                    drag_file_path=str(out_path),
                    actions=[
                        LiveCardAction("Аннотировать", lambda p=out_path, im=qimg: self._open_editor(im, p), primary=True),
                        LiveCardAction("Перевести", lambda p=out_path: self._translate_screenshot(p)),
                        LiveCardAction("Папка", lambda p=out_path: reveal_in_explorer(str(p))),
                    ],
                )
            )
        elif mode == "ocr":
            tmp = self.config_mgr.data_dir / "ocr_tmp.png"
            qimg.save(str(tmp), "PNG")
            self.live_cards.push_card(LiveCardSpec(card_id="ocr_busy", title="Recognizing text (RU+EN)...", icon="🔍", timeout_ms=4000))
            threading.Thread(target=lambda: self.ocr_finished.emit(self.winrt_worker.recognize_text(str(tmp))), daemon=True).start()
        elif mode == "record":
            amode = str(getattr(cfg, "recording_audio_mode", "system") or "system")
            if self._active_overlay is not None and hasattr(self._active_overlay, "selected_audio_mode"):
                amode = str(self._active_overlay.selected_audio_mode or amode)
            try:
                self.screen_recorder.start(
                    rect.x(),
                    rect.y(),
                    rect.width(),
                    rect.height(),
                    fmt=cfg.recording_format,
                    fps=cfg.recording_fps,
                    output_dir=cfg.effective_recording_dir(self.config_mgr.data_dir),
                    audio_mode=amode,
                )
                self.island.set_recording_indicator(True)
                self.panel.home_tab.set_recording_state(True)
                self.live_cards.push_card(
                    LiveCardSpec(
                        card_id="rec_on",
                        title=f"Recording {cfg.recording_format.upper()} ({rect.width()}x{rect.height()})",
                        icon="🔴",
                        timeout_ms=0,
                        actions=[LiveCardAction("Stop Recording", self._toggle_recording, danger=True)],
                    )
                )
            except Exception as exc:
                self.live_cards.push_card(LiveCardSpec(card_id="rec_err", title="Recording failed", body=str(exc), icon="⚠"))

    def _translate_screenshot(self, img_path: Path) -> None:
        self.live_cards.push_card(LiveCardSpec(card_id="shot_tr", title="Распознаём и переводим…", icon="🔍", timeout_ms=6000))

        def _work() -> None:
            import urllib.parse
            import urllib.request
            import json as _json

            txt = (self.winrt_worker.recognize_text(str(img_path)) or "").strip()
            if not txt:
                self.shot_translated.emit("", "Текст на скриншоте не найден.")
                return
            has_cyr = any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in txt)
            sl, tl = ("ru", "en") if has_cyr else ("auto", "ru")
            translated = ""
            try:
                cfg = self.config_mgr.config
                if cfg.get_secret("ai") or cfg.ai_provider in ("groq", "openai", "gemini", "ollama"):
                    prompt = (
                        f"Translate the following screen text accurately into {'English' if tl == 'en' else 'Russian'}. Return ONLY the translation:\n\n{txt[:2500]}"
                    )
                    translated = self.ai_service.send_chat(cfg, [], prompt, [], pure_text_only=True)
            except Exception:
                translated = ""
            if not translated:
                try:
                    q = urllib.parse.quote(txt[:1600])
                    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        data = _json.loads(r.read().decode("utf-8", errors="ignore"))
                    translated = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
                except Exception as exc:
                    translated = f"Не удалось перевести онлайн: {exc}"
            self.shot_translated.emit(txt, translated.strip() or txt)

        threading.Thread(target=_work, daemon=True).start()

    def _on_shot_translated(self, _src: str, translated: str) -> None:
        self.live_cards.dismiss_card("shot_tr")
        cb = QGuiApplication.clipboard()
        if cb and translated:
            cb.setText(translated)
        self.live_cards.push_card(
            LiveCardSpec(
                card_id="shot_tr_done",
                title="Перевод (скопирован)",
                body=translated[:280],
                icon="⚡",
                timeout_ms=9500,
                actions=[
                    LiveCardAction("Копировать", lambda t=translated: QGuiApplication.clipboard().setText(t), primary=True),  # type: ignore[union-attr]
                ],
            )
        )

    def _open_editor(self, qimg: QImage, path: Path) -> None:
        from borh_island.ui.screen_tools import AnnotationEditorWindow

        ed = AnnotationEditorWindow(qimg, path, self.config_mgr, self.ai_service, self.island.palette)
        self._editor_windows.append(ed)
        ed.show()

    def _on_ocr_done(self, text: str) -> None:
        self.live_cards.dismiss_card("ocr_busy")
        cleaned = text.strip()
        if not cleaned:
            self.live_cards.push_card(LiveCardSpec(card_id="ocr_res", title="OCR: No text found", icon="🔍"))
            return
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(cleaned)
        self.live_cards.push_card(
            LiveCardSpec(
                card_id="ocr_res",
                title="Text Copied (OCR)",
                body=cleaned[:220],
                icon="🔍",
                timeout_ms=6500,
                actions=[
                    LiveCardAction(
                        "В заметки",
                        lambda t=cleaned: (
                            self.storage.save_note(None, "General", (t.splitlines()[0][:60] if t.splitlines() else "OCR"), "", t),
                            self.island.set_expanded(True),
                            self.panel.notes_tab._reload_notes(),
                            self.panel.switch_tab_by_key("notes"),
                        ),
                        primary=True,
                    ),
                ],
            )
        )

    def _on_color_picked(self, hx: str, rgb: str, hsl: str) -> None:
        self.storage.add_picked_color(hx, rgb, hsl)
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(hx)
        self.live_cards.push_card(
            LiveCardSpec(
                card_id="color_pick",
                title=f"Color Copied: {hx}",
                body=f"{rgb}  •  {hsl}",
                icon="🎨",
                timeout_ms=5500,
                actions=[
                    LiveCardAction("Copy RGB", lambda r=rgb: QGuiApplication.clipboard().setText(r)),  # type: ignore[union-attr]
                    LiveCardAction("Copy HSL", lambda h=hsl: QGuiApplication.clipboard().setText(h)),  # type: ignore[union-attr]
                ],
            )
        )

    def _on_qr_decoded(self, decoded: str) -> None:
        if not decoded:
            self.live_cards.push_card(LiveCardSpec(card_id="qr_res", title="No QR code detected", icon="📷"))
            return
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(decoded)
        actions: list[LiveCardAction] = []
        if decoded.startswith(("http://", "https://")):
            actions.append(LiveCardAction("Open Link", lambda u=decoded: os.startfile(u), primary=True))  # type: ignore[attr-defined]
        self.live_cards.push_card(
            LiveCardSpec(card_id="qr_res", title="QR Decoded & Copied", body=decoded[:200], icon="🔳", timeout_ms=6500, actions=actions)
        )

    def _toggle_recording(self) -> None:
        if self.screen_recorder.is_recording:
            out_path = self.screen_recorder.stop()
            self.island.set_recording_indicator(False)
            self.panel.home_tab.set_recording_state(False)
            self.live_cards.dismiss_card("rec_on")
            if out_path and out_path.exists():
                self.live_cards.push_card(
                    LiveCardSpec(
                        card_id="rec_saved",
                        title="Screen Recording Saved",
                        body=out_path.name,
                        icon="🎬",
                        timeout_ms=6500,
                        actions=[
                            LiveCardAction("Play", lambda p=out_path: os.startfile(str(p)), primary=True),  # type: ignore[attr-defined]
                            LiveCardAction("Folder", lambda p=out_path: reveal_in_explorer(str(p))),
                        ],
                    )
                )
        else:
            self._open_discord_record_picker()

    def _open_discord_record_picker(self) -> None:
        from borh_island.ui.screen_tools import DiscordRecordPickerDialog

        cfg = self.config_mgr.config
        picker = DiscordRecordPickerDialog(
            audio_mode=getattr(cfg, "recording_audio_mode", "system"),
            palette=self.island.palette if hasattr(self, "island") else None,
        )
        self._active_overlay = picker
        picker.audio_mode_changed.connect(self._on_overlay_audio_mode_changed)
        picker.target_selected.connect(self._start_recording_target)
        picker.custom_area_requested.connect(self._start_custom_area_recording)
        picker.show()
        picker.activateWindow()

    def _start_custom_area_recording(self, amode: str) -> None:
        self._on_overlay_audio_mode_changed(amode)
        self._start_overlay("record")

    def _start_recording_target(self, rect, target_hwnd: int, label: str, amode: str) -> None:
        self._on_overlay_audio_mode_changed(amode)
        cfg = self.config_mgr.config
        try:
            self.screen_recorder.start(
                rect.x(),
                rect.y(),
                rect.width(),
                rect.height(),
                fmt=cfg.recording_format,
                fps=cfg.recording_fps,
                output_dir=cfg.effective_recording_dir(self.config_mgr.data_dir),
                audio_mode=amode,
                target_hwnd=int(target_hwnd or 0),
            )
            self.island.set_recording_indicator(True)
            self.panel.home_tab.set_recording_state(True)
            self.live_cards.push_card(
                LiveCardSpec(
                    card_id="rec_on",
                    title=f"Запись: {label[:28]} ({rect.width()}×{rect.height()})",
                    icon="🔴",
                    timeout_ms=0,
                    actions=[LiveCardAction("Stop Recording", self._toggle_recording, danger=True)],
                )
            )
        except Exception as exc:
            self.live_cards.push_card(LiveCardSpec(card_id="rec_err", title="Recording failed", body=str(exc), icon="⚠"))

    def _toggle_dictation(self) -> None:
        if self.voice_recorder.is_recording:
            wav_path = self.voice_recorder.stop()
            self.island.set_dictating_indicator(False)
            self.panel.home_tab.set_dictating_state(False)
            if not wav_path:
                self.live_cards.dismiss_card("dict_rec")
                return
            self.live_cards.push_card(LiveCardSpec(card_id="dict_rec", title="Transcribing speech...", icon="🎙", timeout_ms=5000))
            cfg = self.config_mgr.config
            threading.Thread(target=lambda: self._run_stt(wav_path, cfg), daemon=True).start()
        else:
            ok = self.voice_recorder.start()
            if not ok:
                self.live_cards.push_card(LiveCardSpec(card_id="dict_rec", title="Microphone unavailable", icon="⚠"))
                return
            self.island.set_dictating_indicator(True)
            self.panel.home_tab.set_dictating_state(True)
            self.live_cards.push_card(
                LiveCardSpec(
                    card_id="dict_rec",
                    title="Listening... (Dictation)",
                    icon="🎙",
                    progress=5,
                    timeout_ms=0,
                    actions=[LiveCardAction("Stop & Transcribe", self._toggle_dictation, primary=True)],
                )
            )

    def _on_dictation_level(self, rms: float) -> None:
        if self.voice_recorder.is_recording:
            self.live_cards.push_card(
                LiveCardSpec(
                    card_id="dict_rec",
                    title="Listening... (Dictation)",
                    icon="🎙",
                    progress=int(round(rms * 100)),
                    timeout_ms=0,
                    actions=[LiveCardAction("Stop & Transcribe", self._toggle_dictation, primary=True)],
                )
            )

    def _run_stt(self, wav_path: Path, cfg) -> None:
        try:
            txt = self.ai_service.transcribe_audio(wav_path, cfg)
            self.stt_finished.emit(txt, "")
        except Exception as exc:
            self.stt_finished.emit("", str(exc))

    def _on_stt_done(self, text: str, err: str) -> None:
        self.live_cards.dismiss_card("dict_rec")
        if err:
            self.live_cards.push_card(LiveCardSpec(card_id="dict_res", title="Dictation Error", body=err[:200], icon="⚠"))
            return
        if not text.strip():
            self.live_cards.push_card(LiveCardSpec(card_id="dict_res", title="No speech detected", icon="🎙"))
            return
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(text)
        if self.config_mgr.config.stt_autotype:
            self.clipboard_service.insert_text_into_active_app(text)
        self.live_cards.push_card(
            LiveCardSpec(
                card_id="dict_res",
                title="Dictated & Copied",
                body=text[:220],
                icon="🎙",
                timeout_ms=5500,
                actions=[
                    LiveCardAction("Paste", lambda t=text: self.clipboard_service.insert_text_into_active_app(t), primary=True),
                ],
            )
        )

    def _toggle_mic_mute(self) -> None:
        self._mic_muted = not self._mic_muted
        dev = self.config_mgr.config.input_device_id or "default"
        threading.Thread(target=lambda: self.bridge.set_mute(self._mic_muted, "capture", dev), daemon=True).start()
        self.live_cards.push_card(
            LiveCardSpec(
                card_id="mic_mute",
                title="Microphone Muted" if self._mic_muted else "Microphone Unmuted",
                icon="🔇" if self._mic_muted else "🎙",
                timeout_ms=2000,
            )
        )

    def _toggle_click_through(self, checked: bool) -> None:
        self.config_mgr.config.click_through = checked
        self.config_mgr.save()
        if hasattr(self, "act_through"):
            self.act_through.blockSignals(True)
            self.act_through.setChecked(checked)
            self.act_through.blockSignals(False)
        self.island.apply_config()

    def _start_timer_from_command(self, seconds: int, label: str) -> None:
        self.panel.notes_tab.start_timer(seconds, label)

    def _add_reminder_from_command(self, phrase: str) -> None:
        self.panel.notes_tab.add_reminder_phrase(phrase)
        self.live_cards.push_card(LiveCardSpec(card_id="rem_ok", title="Reminder Scheduled", body=phrase, icon="🔔"))

    def _run_system_cmd(self, cmd_id: str) -> None:
        if cmd_id == "settings":
            self.open_settings()
        elif cmd_id == "theme":
            toggle_windows_dark_theme()
            self._apply_config_live()
        elif cmd_id == "mute_mic":
            self._toggle_mic_mute()
        elif cmd_id == "empty_bin":
            subprocess.Popen(["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"])
        elif cmd_id in ("lock", "sleep", "restart", "shutdown"):
            self.panel.controls_tab._power_clicked(cmd_id)

    def _toggle_theme_hotkey(self) -> None:
        cfg = self.config_mgr.config
        cfg.theme = "light" if cfg.theme == "dark" else "dark"
        self.config_mgr.save()
        self._apply_config_live()
        self.island.show_activity(
            "Тема",
            "Светлая тема" if cfg.theme == "light" else "Тёмная тема",
            accent_hex="#38BDF8" if cfg.theme == "light" else "#6366F1",
            duration_ms=1700,
        )

    def _resolve_island_palette(self, cfg):
        from borh_island.core.licensing import get_license_manager

        lic = get_license_manager()
        am = str(getattr(cfg, "island_appearance_mode", "weather") or "weather").lower()
        if am == "weather" and lic.is_feature_allowed("weather"):
            return resolve_palette(
                cfg.theme,
                weather_symbol=self.island._weather_symbol if hasattr(self, "island") else "",
                preview_mode=cfg.atmosphere_preview,
                lat=cfg.weather_lat,
                lon=cfg.weather_lon,
                sky_mode=cfg.atmosphere_sky_mode,
                weather_mode=cfg.atmosphere_weather_mode,
                moon_mode=cfg.atmosphere_moon_mode,
            )
        if am == "music":
            m_acc = (
                (self.island._accent_hex if (hasattr(self, "island") and self.island._accent_hex) else "")
                or (self.island._spotify_sticky_hex if hasattr(self, "island") else "")
                or str(getattr(cfg, "music_last_accent", "") or "").strip()
                or str(getattr(cfg, "accent_color", "") or "").strip()
                or "#3B82F6"
            )
            return resolve_palette(cfg.theme, m_acc, music_tint=True)
        if am in ("image", "video") and hasattr(self, "island"):
            bg_acc = getattr(self.island, "_custom_bg_accent", "") or str(getattr(cfg, "accent_color", "") or "").strip() or "#3B82F6"
            return resolve_palette(cfg.theme, bg_acc, color_tint=True)
        custom_acc = str(getattr(cfg, "accent_color", "") or "").strip() or "#3B82F6"
        return resolve_palette(cfg.theme, custom_acc, color_tint=True)

    def _on_agent_event(self, agent: str, title: str, detail: str) -> None:
        self.island.show_activity(agent[:10], title, accent_hex="#38BDF8", duration_ms=3400)
        self.live_cards.push_card(
            LiveCardSpec(card_id=f"agent_{agent.lower()}", title=title, body=detail, icon="🤖", timeout_ms=5500)
        )

    def _on_agent_permission(self, req_id: str, tool_name: str, summary: str) -> None:
        self.live_cards.push_card(
            LiveCardSpec(
                card_id=f"perm_{req_id}",
                title=f"Claude Code: {tool_name}",
                body=summary[:220],
                icon="🤖",
                timeout_ms=0,
                actions=[
                    LiveCardAction("Разрешить", lambda r=req_id: self.agents_service.resolve_permission(r, "allow"), primary=True),
                    LiveCardAction("Для сессии", lambda r=req_id: self.agents_service.resolve_permission(r, "allow_session")),
                    LiveCardAction("Запретить", lambda r=req_id: self.agents_service.resolve_permission(r, "deny"), danger=True),
                ],
            )
        )

    def _on_agent_choice(self, req_id: str, title: str, detail: str, choices: list) -> None:
        acts = [
            LiveCardAction(str(ch)[:28], lambda r=req_id, c=str(ch): self.agents_service.resolve_permission(r, c), primary=(i == 0))
            for i, ch in enumerate(choices[:4])
        ]
        acts.append(LiveCardAction("Отмена", lambda r=req_id: self.agents_service.resolve_permission(r, "deny"), danger=True))
        self.live_cards.push_card(
            LiveCardSpec(card_id=f"perm_{req_id}", title=f"Claude: {title}", body=detail[:220], icon="🤖", timeout_ms=0, actions=acts)
        )

    # ------------------------------------------------------------------ Settings & Live Hot Reload
    def open_settings(self, tab_index: int = 0) -> None:
        from borh_island.ui.settings_window import SettingsWindow

        if self.settings_win is not None:
            self.settings_win.close()
        self.settings_win = SettingsWindow(self.config_mgr, self.agents_service, self.discord_service)
        if self._last_sys_snap is not None and hasattr(self.settings_win, "update_game_stats_preview"):
            ss = self._last_sys_snap
            self.settings_win.update_game_stats_preview(
                ss.cpu_percent,
                ss.ram_percent,
                ss.gpu_load,
                ss.active_game_name if ss.in_game else "",
            )
        if 0 <= tab_index < self.settings_win.tabs.count():
            self.settings_win.tabs.setCurrentIndex(tab_index)
        self.settings_win.config_applied.connect(self._on_settings_saved)
        self.settings_win.live_preview_requested.connect(self._on_live_preview)
        self.settings_win.restart_app_requested.connect(lambda: self._on_full_restart_requested(["settings_restart"]))
        self.settings_win.show()
        self.settings_win.raise_()
        self.settings_win.activateWindow()

    def _on_settings_saved(self) -> None:
        self._preview_snapshot = None
        self._apply_config_live()

    def _on_live_preview(self, patch: dict) -> None:
        cfg = self.config_mgr.config
        if not patch:
            if self._preview_snapshot is not None:
                for k, v in self._preview_snapshot.items():
                    setattr(cfg, k, v)
                self._preview_snapshot = None
                self._apply_config_live()
            return
        if self._preview_snapshot is None:
            self._preview_snapshot = {
                "theme": cfg.theme,
                "island_appearance_mode": getattr(cfg, "island_appearance_mode", "weather"),
                "accent_color": getattr(cfg, "accent_color", ""),
                "pill_mode": cfg.pill_mode,
                "edge_equalizer": cfg.edge_equalizer,
                "liquid_drop": cfg.liquid_drop,
            }
        for k, v in patch.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        self.island.apply_config()
        pal = self._resolve_island_palette(cfg)
        self.live_cards.set_palette(pal)
        self.panel.apply_palette(pal)

    def _on_character_picked(self, character_id: str) -> None:
        from borh_island.core.licensing import get_license_manager

        eff = character_id if get_license_manager().is_character_allowed(character_id) else "liquid"
        self.island.character.set_character(eff)
        self.island.character.update()

    def _on_blue_filter_toggled(self, enabled: bool) -> None:
        if hasattr(self, "blue_filter_overlay"):
            self.blue_filter_overlay.set_enabled(False)
        from borh_island.core.win32_api import set_windows_night_light

        set_windows_night_light(bool(enabled))

    def _apply_config_live(self) -> None:
        cfg = self.config_mgr.config
        set_language(cfg.language)
        eff_fam = apply_app_font(QApplication.instance(), cfg.ui_font_family, cfg.custom_font_path)
        self.island.apply_config()
        pal = self._resolve_island_palette(cfg)
        if hasattr(self, "blue_filter_overlay"):
            self.blue_filter_overlay.set_exclude_capture(cfg.exclude_from_capture)
            self.blue_filter_overlay.set_enabled(False)
        self.live_cards.set_palette(pal)
        self.live_cards.set_glass_mode(str(getattr(cfg, "glass_mode", "liquid") or "liquid").lower())
        self.live_cards.set_exclude_capture(cfg.exclude_from_capture)
        raining_now, wkind_now, _ = self.island._is_card_rain_active()
        self.live_cards.set_rain_state(raining_now, stormy=(wkind_now in ("thunder", "lightning")))
        self.command_bar.apply_palette(pal)
        self.media_service.set_spectrum_enabled(cfg.edge_equalizer, cfg.output_device_id or "default")
        self.hot_reloader.set_enabled(cfg.hot_reload)
        if cfg.agent_server_enabled and self.agents_service._server is None:
            self.agents_service.start(cfg.agent_server_port)
        elif not cfg.agent_server_enabled and self.agents_service._server is not None:
            self.agents_service.stop()
        self._refresh_tray_menu()
        if hasattr(self, "panel"):
            self.panel.apply_palette(pal, font_family=eff_fam)
            if hasattr(self.panel, "retranslate_ui"):
                self.panel.retranslate_ui()
            if hasattr(self.panel, "controls_tab") and hasattr(self.panel.controls_tab, "sync_from_config"):
                self.panel.controls_tab.sync_from_config()
            if hasattr(self.panel.home_tab, "apply_home_layout"):
                self.panel.home_tab.apply_home_layout()
            self.panel.home_tab.set_dev_tools_visible(bool(cfg.show_dev_text_tools))
            self.panel.weather_tab.sync_atmosphere_combo()
        self.discord_service.configure(cfg.discord_rpc_enabled, cfg.discord_client_id)
        if self.island._effective_appearance_mode() == "music":
            # Immediately apply current track colors and pull fresh album art if needed
            snap = getattr(self, "_last_media_snap", None) or getattr(self.media_service, "_last_snapshot", None)
            if snap is not None and getattr(snap, "active", None) is not None:
                self.island.update_media(snap)
            self.media_service.poll_now(skip_thumb=False)
        self._sync_discord_presence()
        self._bind_hotkeys()

    def _on_hot_reload_ui(self, changed: list[str]) -> None:
        """Reload changed UI modules in-place while preserving open state, tab, and active timer."""
        try:
            was_expanded = self.island.is_expanded
            cur_tab = self.panel.stack.currentIndex()
            t_rem = self.panel.notes_tab._timer_remaining
            t_run = self.panel.notes_tab._timer_running
            t_lbl = self.panel.notes_tab._timer_label

            HotReloadManager.reload_ui_modules()
            cfg = self.config_mgr.config
            pal = resolve_palette(
                cfg.theme,
                weather_symbol=self.island._weather_symbol,
                preview_mode=cfg.atmosphere_preview,
                lat=cfg.weather_lat,
                lon=cfg.weather_lon,
                sky_mode=cfg.atmosphere_sky_mode,
                weather_mode=cfg.atmosphere_weather_mode,
                moon_mode=cfg.atmosphere_moon_mode,
            )
            new_panel = self._create_panel(pal)
            self._wire_panel(new_panel)
            new_panel.switch_tab(cur_tab)
            if t_rem > 0:
                new_panel.notes_tab._timer_remaining = t_rem
                new_panel.notes_tab._timer_running = t_run
                new_panel.notes_tab._timer_label = t_lbl
                new_panel.notes_tab._update_timer_display()

            # Swap ExpandedPanel inside IslandWindow layout cleanly
            old_panel = self.panel
            if hasattr(old_panel, "weather_tab") and hasattr(old_panel.weather_tab, "dispose"):
                old_panel.weather_tab.dispose()
            self.island._root.replaceWidget(old_panel, new_panel)
            old_panel.close()
            old_panel.hide()
            old_panel.deleteLater()
            self.panel = new_panel
            self.island.panel = new_panel
            new_panel.setVisible(was_expanded)

            old_bar = self.command_bar
            was_bar = old_bar.isVisible()
            self.command_bar = self._create_command_bar(pal)
            old_bar.close()
            if was_bar:
                self.command_bar.open_bar()

            self.island.apply_config()
            self.live_cards.set_palette(pal)
            self.live_cards.push_card(
                LiveCardSpec(
                    card_id="hot_reload",
                    title="⚡ Live Hot-Reload Applied",
                    body=", ".join(changed[:4]),
                    icon="♻",
                    timeout_ms=2600,
                )
            )
        except Exception as exc:
            self.live_cards.push_card(
                LiveCardSpec(card_id="hot_reload_err", title="Hot-Reload fallback -> Restarting", body=str(exc)[:180], icon="⚠")
            )
            self._on_full_restart_requested(changed)

    def _on_native_recompile(self) -> None:
        def _build() -> None:
            try:
                self.bridge.ensure_compiled()
            except Exception:
                pass

        threading.Thread(target=_build, daemon=True).start()

    def _on_full_restart_requested(self, _changed: list[str]) -> None:
        """Gracefully release sockets/hooks and relaunch preserving expanded state and active tab."""
        if getattr(sys, "frozen", False):
            args = [
                sys.executable,
                "--replace",
                "--tab",
                str(self.panel.stack.currentIndex()),
            ]
            cwd_dir = str(Path(sys.executable).resolve().parent)
        else:
            args = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--replace",
                "--tab",
                str(self.panel.stack.currentIndex()),
            ]
            cwd_dir = str(Path(__file__).resolve().parent)
        if self.island.is_expanded:
            args.append("--expand")
        self.cleanup()
        subprocess.Popen(args, cwd=cwd_dir)
        QApplication.quit()

    def cleanup(self) -> None:
        self.hot_reloader.set_enabled(False)
        self.hotkeys.unregister_all()
        self.clipboard_service.stop()
        self.system_service.stop()
        self.discord_service.stop()
        self.media_service.stop()
        if hasattr(self, "media_winrt_worker"):
            self.media_winrt_worker.stop()
        self.winrt_worker.stop()
        if hasattr(self, "agents_service"):
            self.agents_service.stop()
        self._ipc_server.close()
        if hasattr(self, "panel"):
            if hasattr(self.panel, "weather_tab") and hasattr(self.panel.weather_tab, "dispose"):
                self.panel.weather_tab.dispose()
            if hasattr(self.panel, "character_tab") and hasattr(self.panel.character_tab, "set_thumbs_animated"):
                self.panel.character_tab.set_thumbs_animated(False)
        if hasattr(self, "island") and hasattr(self.island, "_watch_timer"):
            self.island._watch_timer.stop()
        if hasattr(self, "_topmost_timer"):
            self._topmost_timer.stop()
        for w in (getattr(self, "panel", None), getattr(self, "island", None), getattr(self, "command_bar", None), getattr(self, "live_cards", None), getattr(self, "blue_filter_overlay", None)):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass
        if hasattr(self, "tray"):
            self.tray.hide()
        self.storage.close()

    def shutdown_app(self) -> None:
        self.cleanup()
        QApplication.quit()


def _acquire_single_instance_mutex() -> object | None:
    """Acquire a process-lifetime Win32 named mutex to eliminate boot-time duplicate-launch races."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.CreateMutexW(None, False, "Local\\BorhIslandSingleInstanceMutex_v1")
        if h and k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            k32.CloseHandle(h)
            return False
        return h or True
    except Exception:
        return True


def main() -> int:
    app_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    os.chdir(str(app_dir))
    parser = argparse.ArgumentParser(description="Dynamic Island — Borh-edition")
    parser.add_argument("--replace", action="store_true", help="Wait briefly for prior instance cleanup and take over")
    parser.add_argument("--autostart", action="store_true", help="Launched automatically on Windows startup")
    parser.add_argument("--expand", action="store_true", help="Start in expanded state")
    parser.add_argument("--tab", type=int, default=0, help="Initial tab index when expanded")
    args = parser.parse_args()

    set_app_user_model_id("Borh.DynamicIsland.Edition.1")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Dynamic Island — Borh-edition")
    app.setWindowIcon(_make_tray_icon())

    if args.replace:
        probe = QLocalSocket()
        probe.connectToServer(_SINGLE_INSTANCE_KEY)
        if probe.waitForConnected(250):
            probe.write(b"QUIT")
            probe.waitForBytesWritten(200)
            probe.close()
            time.sleep(0.55)
    else:
        probe = QLocalSocket()
        probe.connectToServer(_SINGLE_INSTANCE_KEY)
        if probe.waitForConnected(250):
            if not args.autostart:
                probe.write(b"SHOW")
                probe.waitForBytesWritten(200)
            probe.close()
            return 0

    _instance_mutex = _acquire_single_instance_mutex()
    if _instance_mutex is False and not args.replace:
        if args.autostart:
            return 0
        probe2 = QLocalSocket()
        for _ in range(8):
            probe2.connectToServer(_SINGLE_INSTANCE_KEY)
            if probe2.waitForConnected(150):
                probe2.write(b"SHOW")
                probe2.waitForBytesWritten(150)
                probe2.close()
                return 0
            time.sleep(0.15)
        return 0

    ctrl = BorhIslandController(initial_expand=(args.expand and not args.autostart), initial_tab=args.tab)
    app.installNativeEventFilter(ctrl.hotkeys)
    app.aboutToQuit.connect(ctrl.cleanup)
    return app.exec()


def _run_with_crash_log() -> int:
    log_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "BorhIsland"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "startup.log"
    fh = None

    def _log_exc(prefix: str, exc_type, exc_val, exc_tb) -> None:
        msg = f"\n[{datetime.now().isoformat()}] {prefix}:\n" + "".join(
            traceback.format_exception(exc_type, exc_val, exc_tb)
        )
        if fh is not None and not fh.closed:
            try:
                fh.write(msg)
                fh.flush()
            except Exception:
                pass

    old_excepthook = sys.excepthook
    old_thread_hook = getattr(threading, "excepthook", None)

    def _sys_hook(exc_type, exc_val, exc_tb) -> None:
        _log_exc("UNHANDLED", exc_type, exc_val, exc_tb)
        old_excepthook(exc_type, exc_val, exc_tb)

    def _thread_hook(args) -> None:
        _log_exc(f"THREAD({getattr(args.thread, 'name', 'worker')})", args.exc_type, args.exc_value, args.exc_traceback)
        if old_thread_hook is not None:
            old_thread_hook(args)

    try:
        fh = log_path.open("a", encoding="utf-8")
        fh.write(f"\n--- Launch {datetime.now().isoformat()} ({sys.executable}) ---\n")
        fh.flush()
        faulthandler.enable(fh, all_threads=True)
        sys.excepthook = _sys_hook
        threading.excepthook = _thread_hook
        return main()
    except Exception:
        tb = traceback.format_exc()
        if fh is not None:
            fh.write(tb + "\n")
            fh.flush()
        raise
    finally:
        sys.excepthook = old_excepthook
        if old_thread_hook is not None:
            threading.excepthook = old_thread_hook
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(_run_with_crash_log())
