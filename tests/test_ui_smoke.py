"""Headless Qt UI smoke tests verifying all windows, tabs, 15 characters, Settings Developers tab, and Hot Reload."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QLabel

from borh_island.core.config import ConfigManager
from borh_island.core.hot_reload import HotReloadManager
from borh_island.core.native_bridge import NativeBridge
from borh_island.core.storage import LocalStorage
from borh_island.core.winrt_worker import WinRTWorker
from borh_island.services.agents_service import AgentsService
from borh_island.services.ai_service import AIService
from borh_island.services.clipboard_service import ClipboardService
from borh_island.services.command_service import CommandService
from borh_island.services.media_service import MediaService
from borh_island.services.weather_service import WeatherService
from borh_island.ui.characters import CHARACTER_DEFS, CharacterWidget
from borh_island.ui.command_bar import CommandBarWindow
from borh_island.ui.expanded_panel import ExpandedPanel
from borh_island.ui.island_window import IslandWindow
from borh_island.ui.live_cards import LiveCardAction, LiveCardManager, LiveCardSpec
from borh_island.ui.screen_tools import AnnotationEditorWindow
from borh_island.ui.settings_window import SettingsWindow
from borh_island.ui.theme import resolve_palette


class UISmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_all_15_characters_render(self) -> None:
        self.assertEqual(len(CHARACTER_DEFS), 40)
        for cid, _, _ in CHARACTER_DEFS:
            cw = CharacterWidget(cid)
            cw.resize(64, 64)
            cw.set_cursor_norm(0.4, -0.3)
            cw.set_audio_level(0.7)
            cw.set_thinking(True)
            cw.set_precip("rain")
            cw.trigger_poke()
            pix = cw.grab()
            self.assertFalse(pix.isNull())

    def test_windows_tabs_settings_and_hot_reload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cm = ConfigManager(root)
            st = LocalStorage(root / "ui.db")
            bridge = NativeBridge(root)
            worker = WinRTWorker()
            media = MediaService(worker, bridge)
            clip = ClipboardService(st, cm, root / "clip")
            weather = WeatherService()
            ai = AIService()
            agents = AgentsService(root, port=17533)
            pal = resolve_palette("dark")

            try:
                panel = ExpandedPanel(cm, st, bridge, media, clip, weather, ai, agents, pal)
                for idx in range(panel.stack.count()):
                    panel.switch_tab(idx)
                    self.assertEqual(panel.stack.currentIndex(), idx)

                island = IslandWindow(cm, bridge, panel)
                island.show()
                island.set_expanded(True)
                self.assertTrue(island.is_expanded)

                cards = LiveCardManager(pal)
                cards.push_card(
                    LiveCardSpec(
                        card_id="smoke",
                        title="Claude Allow",
                        body="Bash",
                        progress=60,
                        actions=[LiveCardAction("Allow", lambda: None, primary=True)],
                    )
                )
                self.assertTrue(cards.isVisible())
                cards.dismiss_card("smoke")
                cards.close()

                bar = CommandBarWindow(CommandService(), pal)
                bar.open_bar()
                bar._refresh_results("2 + 2")
                self.assertGreater(bar.list_widget.count(), 0)
                bar.close()

                shot_path = root / "shot.png"
                img = QImage(QSize(200, 120), QImage.Format.Format_RGBA8888)
                img.fill(0xFF336699)
                img.save(str(shot_path), "PNG")
                editor = AnnotationEditorWindow(img, shot_path, cm, ai, pal)
                editor.canvas.undo()
                editor.close()

                settings = SettingsWindow(cm, agents)
                labels = [lbl.text() for lbl in settings.findChildren(QLabel)]
                joined = "\n".join(labels)
                self.assertIn("Новабис Чебурек", joined)
                self.assertIn("Борщ", joined)
                settings.close()

                # Verify live module reload succeeds cleanly
                reloaded = HotReloadManager.reload_ui_modules()
                self.assertIn("borh_island.ui.theme", reloaded)
                island.close()
                panel.close()
            finally:
                worker.stop()
                st.close()

    def test_controller_end_to_end_and_live_hot_reload(self) -> None:
        from main import BorhIslandController

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
                ctrl = BorhIslandController(initial_expand=True, initial_tab=2)
                try:
                    self.assertTrue(ctrl.island.is_expanded)
                    self.assertEqual(ctrl.panel.stack.currentIndex(), 2)
                    self.assertTrue(ctrl.hot_reloader.enabled)

                    # Exercise live hot-reload while expanded on tab 2
                    ctrl._on_hot_reload_ui(["theme.py"])
                    self.assertTrue(ctrl.island.is_expanded)
                    self.assertEqual(ctrl.panel.stack.currentIndex(), 2)

                    # Open Settings and confirm Разработчики credits + save works
                    ctrl.open_settings()
                    self.assertIsNotNone(ctrl.settings_win)
                    labels = "\n".join(lbl.text() for lbl in ctrl.settings_win.findChildren(QLabel))
                    self.assertIn("Новабис Чебурек", labels)
                    self.assertIn("Борщ", labels)
                    ctrl.settings_win._save_and_apply()
                    ctrl.settings_win.close()

                    # Verify collapse shrinks smoothly back to compact pill and never stays stretched
                    ctrl.island.set_expanded(False)
                    self.app.processEvents()
                    ctrl.island._anim.setCurrentTime(ctrl.island._anim.duration())
                    self.app.processEvents()
                    self.assertFalse(ctrl.island.is_expanded)
                    self.assertFalse(ctrl.panel.isVisible())
                    core = ctrl.island._core_rect()
                    self.assertLessEqual(core.width(), 480.0)
                    self.assertLessEqual(core.height(), 60.0)
                finally:
                    ctrl.cleanup()
                    ctrl.island._watch_timer.stop()
                    ctrl.island.close()
                    ctrl.command_bar.close()
                    ctrl.live_cards.close()

    def test_liquid_drop_taskbar_and_pill_live_activities(self) -> None:
        from PyQt6.QtCore import QPointF, Qt
        from borh_island.services.media_service import MediaSessionInfo, MediaStateSnapshot

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            st = LocalStorage(root / "ui2.db")
            bridge = NativeBridge(root)
            worker = WinRTWorker()
            media = MediaService(worker, bridge)
            clip = ClipboardService(st, cm, root / "clip")
            weather = WeatherService()
            ai = AIService()
            agents = AgentsService(root, port=17534)
            pal = resolve_palette("dark")
            try:
                panel = ExpandedPanel(cm, st, bridge, media, clip, weather, ai, agents, pal)
                island = IslandWindow(cm, bridge, panel)
                island.show()
                self.app.processEvents()

                # Tray-only Tool overlay (hidden from Windows Taskbar) + maximize blocked
                self.assertEqual(island.windowFlags() & Qt.WindowType.WindowType_Mask, Qt.WindowType.Tool)
                island.setWindowState(Qt.WindowState.WindowMaximized)
                self.app.processEvents()
                self.app.processEvents()
                self.assertFalse(bool(island.windowState() & Qt.WindowState.WindowMaximized))

                # Localized ~1cm edge droplet pull when mouse approaches bottom edge, plus over-stretch snap
                core_rect = island._core_rect()
                cursor_pt = QPointF(island.x() + core_rect.center().x(), island.y() + core_rect.bottom() + 24.0)
                for _ in range(12):
                    island._update_liquid_physics(cursor_pt)
                self.assertGreater(island._drop_amp, 0.25)
                lpath = island._build_liquid_path(core_rect, 20.5)
                self.assertGreater(lpath.boundingRect().height(), core_rect.height())

                far_pt = QPointF(island.x() + core_rect.center().x(), island.y() + core_rect.bottom() + 160.0)
                island._update_liquid_physics(far_pt)
                self.assertFalse(island._tether_latched)
                self.assertGreater(island._snap_shake_amp, 0.3)

                # Live music source + Spotify display in top pill
                snap = MediaStateSnapshot(
                    active=MediaSessionInfo(
                        app_id="Spotify.exe",
                        title="Starboy",
                        artist="The Weeknd",
                        status="Playing",
                        source_id="spotify",
                        source_name="Spotify",
                        source_icon="🟢",
                        accent_color="#1DB954",
                    )
                )
                island.update_media(snap)
                self.assertIn("Spotify", island.source_badge.text())
                self.assertIn("Starboy", island.pill_text.text())

                # Active Timer + Screenshot/Clipboard top pill activity banners
                island.update_timer_badge(125, True, "Coffee")
                self.assertIn("2:05", island.badge_lbl.text())
                island.show_activity("📋 Буфер", "Скопированный фрагмент", accent_hex="#38BDF8", duration_ms=1000)
                self.assertIn("Буфер", island.source_badge.text())
                self.assertIn("Скопированный", island.pill_text.text())

                pix = island.grab()
                self.assertFalse(pix.isNull())
                island._watch_timer.stop()
                panel.close()
                island.close()
                panel.close()
            finally:
                media.stop()
                worker.stop()
                st.close()


class TestBorhEditionFeatures(unittest.TestCase):
    """Focused checks for icons, circadian vs music colors, bottom vibration, outside click, game HUD, and volume."""

    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_vector_icons_circadian_and_red_cover_tint(self) -> None:
        from datetime import datetime
        from PyQt6.QtGui import QColor, QImage
        from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
        from borh_island.ui.icons import ICON_NAMES, app_pill_icon, get_pixmap
        from borh_island.ui.theme import resolve_palette, resolve_time_accent
        from borh_island.services.media_service import extract_dominant_color

        for name in ICON_NAMES:
            pm = get_pixmap(name, size=18, color="#F8FAFC", accent="#3B82F6")
            self.assertFalse(pm.isNull(), f"Icon {name} rendered null")
        self.assertIn("discord", ICON_NAMES)
        self.assertFalse(app_pill_icon().isNull())

        day_hex = resolve_time_accent(datetime(2026, 1, 1, 11, 30))
        night_hex = resolve_time_accent(datetime(2026, 1, 1, 0, 0))
        self.assertNotEqual(day_hex.upper(), "#F8FAFC")
        day_c = QColor(day_hex)
        self.assertLess(day_c.red(), 230)
        self.assertGreater(day_c.blue(), day_c.red())
        self.assertEqual(night_hex.upper(), "#2563EB")

        img = QImage(32, 32, QImage.Format.Format_RGBA8888)
        img.fill(QColor(225, 22, 42))
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        dominant = extract_dominant_color(bytes(ba))
        dc = QColor(dominant)
        self.assertGreater(dc.red(), 170)
        self.assertGreater(dc.red(), dc.green() + 70)
        self.assertGreater(dc.red(), dc.blue() + 70)

        music_pal = resolve_palette("dark", dominant, music_tint=True)
        bg = QColor(music_pal.bg_island)
        self.assertGreater(bg.red(), bg.blue())

    def test_game_filter_snipping_exempt_and_island_interactions(self) -> None:
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QColor
        from borh_island.core.config import ConfigManager
        from borh_island.core.native_bridge import NativeBridge
        from borh_island.core.storage import LocalStorage
        from borh_island.core.win32_api import SNIPPING_OVERLAY_EXES, is_clipboard_marked_private
        from borh_island.core.winrt_worker import WinRTWorker
        from borh_island.services.agents_service import AgentsService
        from borh_island.services.ai_service import AIService
        from borh_island.services.clipboard_service import ClipboardService
        from borh_island.services.media_service import MediaSessionInfo, MediaStateSnapshot
        from borh_island.services.system_service import SystemStatsSnapshot, is_game_process
        from borh_island.services.weather_service import WeatherService
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.island_window import IslandWindow
        from borh_island.ui.theme import resolve_palette

        self.assertIn("screenclippinghost.exe", SNIPPING_OVERLAY_EXES)
        self.assertIsInstance(is_clipboard_marked_private(), bool)

        self.assertTrue(is_game_process("cs2.exe", "Counter-Strike 2"))
        self.assertFalse(is_game_process("chrome.exe", "Google Chrome"))
        self.assertFalse(is_game_process("code.exe", "Visual Studio Code"))
        self.assertFalse(is_game_process("spotify.exe", "Spotify"))

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            old_local = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = tmp
            try:
                cfg = ConfigManager()
                st = LocalStorage(Path(tmp) / "test2.db")
                bridge = NativeBridge(Path(tmp))
                worker = WinRTWorker()
                media = MediaServiceMock(worker, bridge)
                clip = ClipboardService(st, cfg, Path(tmp) / "clip")
                weather = WeatherService()
                ai = AIService()
                agents = AgentsService(Path(tmp), port=0)
                pal = resolve_palette("dark")

                panel = ExpandedPanel(cfg, st, bridge, media, clip, weather, ai, agents, pal)
                self.assertEqual(panel.stack.count(), 5)
                self.assertFalse(hasattr(panel, "chat_tab"))
                self.assertFalse(hasattr(panel, "stats_tab"))
                self.assertFalse(hasattr(panel, "clipboard_tab"))
                self.assertFalse(hasattr(panel, "shelf_tab"))
                self.assertFalse(hasattr(panel, "settings_btn"))
                self.assertTrue(hasattr(panel, "notes_tab"))
                self.assertTrue(panel.home_tab.media_card.isHidden())
                self.assertTrue(panel.home_tab.tool_buttons["qr_scan"].isHidden())
                self.assertTrue(hasattr(panel.stack, "slide_to"))
                self.assertTrue(hasattr(panel.home_tab, "vol_slider"))
                self.assertTrue(hasattr(panel.home_tab, "vol_btn"))
                self.assertTrue(hasattr(bridge, "get_app_volume"))
                self.assertTrue(hasattr(bridge, "set_app_volume"))
                panel.step_tab(1)
                self.assertEqual(panel.stack.currentIndex(), 1)
                panel.step_tab(-1)
                self.assertEqual(panel.stack.currentIndex(), 0)

                island = IslandWindow(cfg, bridge, panel)
                try:
                    island.show()
                    # Localized cursor liquid droplet pull
                    core = island._core_rect()
                    cursor_pt = QPointF(island.x() + core.center().x(), island.y() + core.bottom() + 30.0)
                    for _ in range(18):
                        island._update_liquid_physics(cursor_pt)
                    lpath = island._build_liquid_path(core, 20.5)
                    self.assertGreater(lpath.boundingRect().bottom() - core.bottom(), 12.0)

                    # Bottom-edge-only music vibration in BOTH collapsed and expanded states + color split
                    island._drop_amp = 0.0
                    island._drop_vel = 0.0
                    island._drop_wobble = 0.0
                    snap = MediaStateSnapshot(
                        active=MediaSessionInfo(
                            app_id="Spotify.exe",
                            title="Crimson Track",
                            artist="Artist",
                            status="Playing",
                            source_id="spotify",
                            source_name="Spotify",
                            accent_color="#E11D48",
                        )
                    )
                    island.update_media(snap)
                    self.assertGreater(QColor(island.palette.bg_island).red(), QColor(island.palette.bg_island).blue())

                    island.update_spectrum([0.9] * 20)
                    cpath = island._build_liquid_path(core, 20.5)
                    cbounds = cpath.boundingRect()
                    # Collapsed music vibrates the entire pill silhouette (all edges), expanded vibrates strongly at bottom
                    self.assertLess(cbounds.top(), core.top() - 1.0)
                    self.assertGreater(cbounds.bottom(), core.bottom() + 2.0)

                    # Expanded uses circadian theme (no red music tint) while strongly vibrating bottom edge
                    island.set_expanded(True)
                    self.assertNotEqual(island.palette.accent.upper(), "#E11D48")
                    epath = island._build_liquid_path(island._core_rect(), 24.0)
                    ecore = island._core_rect()
                    ebounds = epath.boundingRect()
                    self.assertGreaterEqual(ebounds.top(), ecore.top() - 0.5)
                    self.assertGreater(ebounds.bottom(), ecore.bottom() + 12.0)

                    # Clicking outside collapses the island
                    island._expand_ts -= 1.0
                    island._on_outside_click(QPoint(-500, -500))
                    self.assertFalse(island.is_expanded)

                    # Game Telemetry HUD shows in top pill ONLY during active game
                    with mock.patch("borh_island.core.licensing.get_license_manager") as _m_lic:
                        _m_lic.return_value.is_feature_allowed.return_value = True
                        island.update_system_badges(
                            SystemStatsSnapshot(cpu_percent=41.0, ram_percent=63.0, gpu_load=78.0, in_game=True, active_game_name="cs2.exe")
                        )
                    self.assertFalse(island.game_hud.isHidden())
                    island.update_system_badges(SystemStatsSnapshot(cpu_percent=12.0, ram_percent=40.0, gpu_load=5.0, in_game=False))
                    self.assertTrue(island.game_hud.isHidden())

                    # Transient keyboard layout toast
                    island.show_activity(badge="RU", text="Раскладка: RU", accent_hex="#38BDF8", duration_ms=1000, icon_name="keyboard")
                    self.assertEqual(island.source_badge.text(), "RU")
                    self.assertIn("Раскладка", island.pill_text.text())

                    # Expanded window grab with atmospheric backdrop + top badge hidden (moved to Settings)
                    island.set_expanded(True)
                    self.assertTrue(island.atmo_badge_btn.isHidden())
                    grab_pm = island.grab()
                    self.assertFalse(grab_pm.isNull())
                finally:
                    panel.weather_tab._refresh_timer.stop()
                    island._watch_timer.stop()
                    island.close()
                    worker.stop()
                    st.close()
            finally:
                if old_local is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_local

    def test_discord_visibility_ipc_and_credits(self) -> None:
        from PyQt6.QtWidgets import QLabel, QTabWidget
        from borh_island.core.config import AppConfig, ConfigManager
        from borh_island.core.win32_api import _FULLSCREEN_EXEMPT_EXES
        from borh_island.services.agents_service import AgentsService
        from borh_island.services.discord_service import DiscordPresenceService, pack_ipc_frame, unpack_ipc_frame
        from borh_island.ui.live_cards import LiveCardManager
        from borh_island.ui.settings_window import SettingsWindow
        from borh_island.ui.theme import resolve_palette

        self.assertFalse(AppConfig().exclude_from_capture)
        mgr = LiveCardManager(resolve_palette("dark"))
        self.assertFalse(mgr._exclude_capture)
        self.assertIn("discord.exe", _FULLSCREEN_EXEMPT_EXES)

        raw = pack_ipc_frame(1, {"cmd": "SET_ACTIVITY", "n": 42})
        op, data = unpack_ipc_frame(raw)
        self.assertEqual(op, 1)
        self.assertEqual(data.get("cmd"), "SET_ACTIVITY")
        self.assertEqual(data.get("n"), 42)

        svc = DiscordPresenceService()
        svc.update_from_island(playing=True, title="Track", artist="Artist", source="Spotify", is_day=True)
        self.assertIn("Track", svc._details)
        self.assertIn("Spotify", svc._state)
        svc.update_from_island(in_game=True, game_name="cs2.exe", cpu_pct=35.0, gpu_pct=70.0)
        self.assertIn("cs2", svc._details)
        self.assertIn("CPU 35%", svc._state)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            old_local = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = tmp
            try:
                cfg = ConfigManager()
                agents = AgentsService(Path(tmp), port=0)
                win = SettingsWindow(cfg, agents, svc)
                try:
                    tabs = win.findChildren(QTabWidget)
                    tab_names = [t.tabText(i) for t in tabs for i in range(t.count())]
                    self.assertIn("Discord", tab_names)
                    all_labels = "\n".join(lbl.text() for lbl in win.findChildren(QLabel))
                    self.assertIn("Борщ", all_labels)
                    self.assertIn("Нищий нищеброд", all_labels)
                finally:
                    win.close()
            finally:
                if old_local is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_local

    def test_atmosphere_sky_moon_and_expanded_render(self) -> None:
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QPainter, QPixmap
        from borh_island.ui.atmosphere import (
            ATMO_MODES,
            AtmosphericScenePainter,
            calc_moon_phase,
            describe_atmosphere_badge,
            resolve_scene_state,
        )
        from borh_island.ui.theme import resolve_palette

        _, full_ru, _ = calc_moon_phase(force_phase=0.50)
        _, new_ru, _ = calc_moon_phase(force_phase=0.0)
        self.assertEqual(full_ru, "Полнолуние")
        self.assertEqual(new_ru, "Новолуние")

        self.assertEqual(resolve_scene_state("sunrise")[0], "sunrise")
        self.assertEqual(resolve_scene_state("day_overcast")[:2], ("day", "cloudy"))
        self.assertEqual(resolve_scene_state("day_rain")[:2], ("day", "rain"))
        self.assertEqual(resolve_scene_state("sunset")[0], "sunset")
        self.assertEqual(resolve_scene_state("night_full_moon"), ("night", "clear", 0.50))
        self.assertEqual(resolve_scene_state("night_new_moon"), ("night", "clear", 0.00))

        self.assertIn("Солнце", describe_atmosphere_badge("day_clear"))
        self.assertIn("Пасмурно", describe_atmosphere_badge("day_overcast"))
        self.assertIn("Закат", describe_atmosphere_badge("sunset"))
        self.assertIn("Полнолуние", describe_atmosphere_badge("night_full_moon"))
        self.assertIn("Новолуние", describe_atmosphere_badge("night_new_moon"))

        rain_pal = resolve_palette("dark", weather_symbol="rain", preview_mode="day_rain")
        self.assertNotEqual(rain_pal.bg_island.upper(), "#FFFFFF")

        for mode, _ in ATMO_MODES:
            pm = QPixmap(560, 420)
            pm.fill(QColor(0, 0, 0, 0))
            p = QPainter(pm)
            AtmosphericScenePainter.paint_expanded_atmosphere(
                p, QRectF(0, 0, 560, 420), preview_mode=mode, weather_symbol="rain" if "rain" in mode else "clearsky_day", phase_t=0.8
            )
            p.end()
            center_c = pm.toImage().pixelColor(280, 180)
            self.assertGreater(center_c.alpha(), 150)
            if mode == "day_clear":
                self.assertLess(center_c.red(), 200)

    def test_spb_sync_soil_grass_parting_localized_tether_and_character_tab(self) -> None:
        from datetime import datetime, timezone

        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import QColor, QPainter, QPixmap

        from borh_island.ui.atmosphere import (
            ATMO_MODES,
            MOON_MODES,
            AtmosphericScenePainter,
            describe_atmosphere_badge,
            moon_mode_to_phase,
            resolve_scene_state,
            solar_elevation_deg,
        )
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.island_window import IslandWindow

        # 1) Solar elevation & city-sync + detailed moon phases (incl. Молодой серп)
        nov14_day = datetime(2026, 11, 14, 10, 30, tzinfo=timezone.utc)
        nov14_night = datetime(2026, 11, 14, 20, 0, tzinfo=timezone.utc)
        self.assertGreater(solar_elevation_deg(59.96, 30.16, nov14_day), 5.0)
        self.assertLess(solar_elevation_deg(59.96, 30.16, nov14_night), -6.0)
        self.assertEqual(resolve_scene_state("auto", "rain", nov14_day, lat=59.96, lon=30.16)[:2], ("day", "rain"))
        self.assertEqual(resolve_scene_state("auto", "clearsky", nov14_night, lat=59.96, lon=30.16, moon_mode="waxing_crescent"), ("night", "clear", 0.12))
        self.assertIn("Молодой серп", describe_atmosphere_badge("night_crescent"))
        self.assertIsNotNone(moon_mode_to_phase("waxing_crescent"))
        self.assertTrue(any(k == "night_crescent" for k, _ in ATMO_MODES))
        self.assertTrue(any("Молодой серп" in lbl for _, lbl in MOON_MODES))

        # 2) Grass & top cloud parting + continuous earth soil below pulled edge (no blank void underneath)
        pm = QPixmap(560, 480)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        AtmosphericScenePainter.paint_expanded_atmosphere(
            p,
            QRectF(0, 0, 560, 420),
            preview_mode="auto",
            weather_symbol="lightrain",
            phase_t=0.6,
            now=nov14_day,
            lat=59.96,
            lon=30.16,
            outer_bottom=465.0,
            cursor_local=QPointF(280.0, 412.0),
        )
        AtmosphericScenePainter.paint_top_clouds_and_light(
            p,
            QRectF(0, 0, 560, 420),
            "day",
            "cloudy",
            0.6,
            cursor_local=QPointF(280.0, 24.0),
        )
        p.end()
        soil_c = pm.toImage().pixelColor(280, 452)
        self.assertGreater(soil_c.alpha(), 200)

        # 3) Character tab replaces removed file magnet & tray controls live inside island
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with (
                mock.patch("borh_island.ui.tabs.weather_tab.WeatherTab._maybe_autodetect_city"),
                mock.patch("borh_island.ui.tabs.weather_tab.WeatherTab.refresh_weather"),
            ):
                cfg = ConfigManager(Path(tmp))
                cfg.config.atmosphere_preview = "day_overcast"
                cfg.save()
                st = LocalStorage(Path(tmp) / "char_test.db")
                bridge = NativeBridge(Path(tmp))
                media = MediaServiceMock(WinRTWorker(), bridge)
                clip = ClipboardService(st, cfg, Path(tmp) / "clip")
                weather = WeatherService()
                ai = AIService()
                agents = AgentsService(Path(tmp), port=0)
                pal = resolve_palette(cfg.config.theme)
                panel = ExpandedPanel(cfg, st, bridge, media, clip, weather, ai, agents, pal)
                island = IslandWindow(cfg, bridge, panel)
                try:
                    self.assertFalse(island.acceptDrops())
                    self.assertFalse(hasattr(panel, "shelf_tab"))
                    self.assertFalse(hasattr(panel, "stats_tab"))
                    self.assertFalse(hasattr(panel, "clipboard_tab"))
                    self.assertFalse(hasattr(island, "files_dropped_to_shelf"))
                    self.assertTrue(hasattr(panel, "character_tab"))
                    self.assertTrue(hasattr(panel, "notes_tab"))

                    # Poking companion jumps straight to Character Settings tab (slot 1)
                    island.character.trigger_poke()
                    self.assertTrue(island.is_expanded)
                    self.assertEqual(panel.stack.currentIndex(), 1)

                    # Switching character live inside island updates pill companion immediately
                    with mock.patch("borh_island.core.licensing.get_license_manager") as _m_lic, \
                         mock.patch("borh_island.ui.tabs.character_tab.get_license_manager", _m_lic):
                        _m_lic.return_value.is_character_allowed.return_value = True
                        _m_lic.return_value.is_feature_allowed.return_value = True
                        panel.character_tab._select_character("fox")
                        island.apply_config()
                    self.assertEqual(island.character.character_id, "fox")

                    # Picking city in WeatherTab clears manual preview back to auto live weather sync
                    from PyQt6.QtWidgets import QListWidgetItem
                    from borh_island.services.weather_service import GeoPlace
                    panel.weather_tab._places = [GeoPlace("Санкт-Петербург", 59.96, 30.16)]
                    it = QListWidgetItem("SPB")
                    it.setData(Qt.ItemDataRole.UserRole, 0)
                    panel.weather_tab._pick_city(it)
                    self.assertEqual(cfg.config.atmosphere_preview, "auto")
                    self.assertEqual(cfg.config.atmosphere_sky_mode, "auto")
                    self.assertEqual(cfg.config.atmosphere_weather_mode, "auto")
                    self.assertEqual(cfg.config.atmosphere_moon_mode, "auto")

                    # Localized ~1cm edge pull leaves far side of bottom wall stationary
                    ec = island._core_rect()
                    target_x = ec.left() + 80.0
                    for _ in range(14):
                        island._update_liquid_physics(QPointF(island.x() + target_x, island.y() + ec.bottom() + 30.0))
                    ep = island._build_liquid_path(ec, 24.0)
                    self.assertGreater(ep.boundingRect().bottom() - ec.bottom(), 18.0)

                    # Pulling right edge stays cleanly inside widget bounds (zero flat clipping) + fills backdrop
                    island._tether_latched = False
                    island._tether_cooldown = 0
                    island._drop_amp = 0.0
                    for i in range(16):
                        island._update_liquid_physics(QPointF(island.x() + ec.right() + 34.0, island.y() + ec.center().y()))
                    rp = island._build_liquid_path(ec, 24.0)
                    rbounds = rp.boundingRect()
                    self.assertGreater(rbounds.right() - ec.right(), 18.0)
                    self.assertLess(rbounds.right(), island.width() - 2.0)

                    # Moving cursor inside & hovering nearby curves left/right edges like fluid
                    island._tether_latched = False
                    island._drop_amp = 0.0
                    for step in range(10):
                        island._update_liquid_physics(QPointF(island.x() + ec.center().x() + step * 18.0, island.y() + ec.center().y()))
                    self.assertGreater(island._fluid_swirl, 0.15)
                    fp = island._build_liquid_path(ec, 24.0)
                    self.assertTrue(fp.boundingRect().right() > ec.right() + 1.5 or fp.boundingRect().left() < ec.left() - 1.5)

                    # Nearby exterior motion (~130px away) also activates fluid curvature without crossing edge
                    island._tether_latched = False
                    island._drop_amp = 0.0
                    for step in range(8):
                        island._update_liquid_physics(QPointF(island.x() + ec.right() + 125.0, island.y() + ec.center().y() + step * 8.0))
                    self.assertGreater(island._fluid_swirl, 0.08)

                    # Over-stretching past ~46px snaps back & shudders interface
                    for _ in range(10):
                        island._update_liquid_physics(QPointF(island.x() + target_x, island.y() + ec.bottom() + 30.0))
                    island._update_liquid_physics(QPointF(island.x() + target_x, island.y() + ec.bottom() + 56.0))
                    self.assertFalse(island._tether_latched)
                    self.assertGreater(island._snap_shake_amp, 0.3)
                    island._update_liquid_physics(QPointF(island.x() + target_x, island.y() + ec.bottom() + 56.0))
                    self.assertTrue(island._panel_shake_dx != 0 or island._panel_shake_dy != 0)
                finally:
                    panel.weather_tab._refresh_timer.stop()
                    panel.character_tab.set_thumbs_animated(False)
                    island.close()
                    panel.close()
                    st.close()


class MediaServiceMock:
    def __init__(self, worker, bridge) -> None:
        self.worker = worker
        self.bridge = bridge

    def prev_track(self) -> None:
        pass

    def play_pause(self) -> None:
        pass

    def next_track(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
