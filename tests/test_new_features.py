"""Focused unit and smoke tests for Borh-edition updates:
- Directional push-in dent vs outward fluid drop
- Spotify paused cover color retention & game+music compatibility
- Minute-stepped celestial trajectory, dense clouds, thunder sparks & lightning strikes
- Launcher native icons/favicons, Controls autostart toggle, Tray menu hotkeys & pure-text Gemini
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QFileIconProvider

from borh_island.core.config import ConfigManager
from borh_island.core.native_bridge import NativeBridge
from borh_island.core.storage import LauncherItem, LocalStorage
from borh_island.core.winrt_worker import WinRTWorker
from borh_island.services.agents_service import AgentsService
from borh_island.services.ai_service import AIService
from borh_island.services.clipboard_service import ClipboardService
from borh_island.services.media_service import MediaService, MediaSessionInfo, MediaStateSnapshot
from borh_island.services.system_service import SystemMonitorService, SystemStatsSnapshot
from borh_island.services.weather_service import WeatherService
from borh_island.ui.atmosphere import (
    ATMO_MODES,
    WEATHER_MODES,
    AtmosphericScenePainter,
    GrassSkeletonSim,
    compute_blade_joints,
    compute_celestial_layout,
    resolve_scene_state,
    resolve_weather_kind,
)
from borh_island.ui.expanded_panel import ExpandedPanel
from borh_island.ui.island_window import IslandWindow
from borh_island.ui.tabs.home_tab import _favicon_cache_path, resolve_launcher_qicon
from borh_island.ui.theme import resolve_palette


class NewFeaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_push_in_dent_and_compatible_in_game_spotify_paused(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
                cm = ConfigManager(root)
                st = LocalStorage(root / "nf.db")
                bridge = NativeBridge(root)
                worker = WinRTWorker()
                media = MediaService(worker, bridge)
                clip = ClipboardService(st, cm, root / "clip")
                weather = WeatherService()
                ai = AIService()
                agents = AgentsService(root, port=0)
                pal = resolve_palette("dark")
                panel = ExpandedPanel(cm, st, bridge, media, clip, weather, ai, agents, pal)
                island = IslandWindow(cm, bridge, panel)
                try:
                    island.show()
                    island.set_expanded(True)
                    island._anim.setCurrentTime(island._anim.duration())
                    self.app.processEvents()
                    island._watch_timer.stop()
                    island._tether_latched = False
                    island._drop_amp = 0.0
                    island._drop_vel = 0.0
                    island._prev_drop_dist = 999.0

                    core = island._core_rect()
                    cx = island.x() + core.center().x()
                    # Approach rapidly inward from far outside across bottom edge into interior (m_in <= 26)
                    for dy in (48.0, 32.0, 18.0, 8.0, -4.0, -10.0, -10.0, -10.0):
                        island._update_liquid_physics(QPointF(cx, island.y() + core.bottom() + dy))

                    self.assertTrue(island._pushing_in)
                    self.assertGreater(island._indent_amp, 0.15)
                    lpath = island._build_liquid_path(core, 24.0)
                    # Contour around cursor center X must bend INWARD (y < core.bottom() - 2)
                    inward_probe = QPointF(core.center().x(), core.bottom() - 1.5)
                    self.assertFalse(lpath.contains(inward_probe))

                    # Reset and verify collapsed Spotify paused color retention + in-game compact telemetry
                    island._pushing_in = False
                    island._indent_amp = 0.0
                    island._indent_vel = 0.0
                    island.set_expanded(False)
                    island._anim.setCurrentTime(island._anim.duration())
                    self.app.processEvents()

                    playing_snap = MediaStateSnapshot(
                        active=MediaSessionInfo(
                            app_id="Spotify.exe",
                            title="Midnight City",
                            artist="M83",
                            status="Playing",
                            source_id="spotify",
                            source_name="Spotify",
                            accent_color="#E11D48",
                        ),
                        sessions=[
                            MediaSessionInfo(
                                app_id="Spotify.exe",
                                title="Midnight City",
                                artist="M83",
                                status="Playing",
                                source_id="spotify",
                                source_name="Spotify",
                                accent_color="#E11D48",
                            )
                        ],
                    )
                    island.update_media(playing_snap)
                    self.assertEqual(island._spotify_sticky_hex, "#E11D48")
                    self.assertGreater(QColor(island.palette.bg_island).red(), QColor(island.palette.bg_island).blue())

                    # Pause Spotify while active/present -> retains crimson sticky cover color
                    paused_snap = MediaStateSnapshot(
                        active=MediaSessionInfo(
                            app_id="Spotify.exe",
                            title="Midnight City",
                            artist="M83",
                            status="Paused",
                            source_id="spotify",
                            source_name="Spotify",
                            accent_color="#E11D48",
                        ),
                        sessions=[
                            MediaSessionInfo(
                                app_id="Spotify.exe",
                                title="Midnight City",
                                artist="M83",
                                status="Paused",
                                source_id="spotify",
                                source_name="Spotify",
                                accent_color="#E11D48",
                            )
                        ],
                    )
                    island.update_media(paused_snap)
                    self.assertEqual(island._spotify_sticky_hex, "#E11D48")
                    self.assertGreater(QColor(island.palette.bg_island).red(), QColor(island.palette.bg_island).blue())

                    # Compatible in-game telemetry + media in collapsed pill
                    with mock.patch("borh_island.core.licensing.get_license_manager") as _m_lic:
                        _m_lic.return_value.is_feature_allowed.return_value = True
                        island.update_system_badges(
                            SystemStatsSnapshot(cpu_percent=38.0, ram_percent=61.0, gpu_load=74.0, in_game=True, active_game_name="cs2.exe")
                        )
                    self.assertFalse(island.game_hud.isHidden())
                    self.assertTrue(island.game_hud._compact)
                    self.assertLessEqual(island.game_hud.width(), 180)
                    self.assertFalse(island.pill_text.isHidden())

                    # Expanded view uses circadian accent cleanly (not red Spotify tint)
                    island.set_expanded(True)
                    self.assertNotEqual(island.palette.accent.upper(), "#E11D48")

                    # When Spotify session disappears completely, sticky cover clears on collapse
                    island.set_expanded(False)
                    island.update_media(MediaStateSnapshot(active=None, sessions=[]))
                    self.assertEqual(island._spotify_sticky_hex, "")
                finally:
                    panel.weather_tab._refresh_timer.stop()
                    island._watch_timer.stop()
                    media.stop()
                    worker.stop()
                    panel.close()
                    island.close()
                    st.close()

    def test_media_service_spotify_retention_while_process_alive(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            bridge = NativeBridge(root)
            worker = WinRTWorker()
            media = MediaService(worker, bridge)
            try:
                raw_active = {
                    "active": {
                        "app_id": "Spotify.exe",
                        "title": "Nights",
                        "artist": "Frank Ocean",
                        "status": "Playing",
                        "position": 12.0,
                        "duration": 200.0,
                        "thumbnail_b64": "",
                    },
                    "sessions": [],
                }
                snap1 = media._build_snapshot(raw_active)
                self.assertIsNotNone(snap1.active)
                self.assertEqual(snap1.active.source_id, "spotify")

                with mock.patch("borh_island.services.media_service.is_process_running", return_value=True):
                    snap2 = media._build_snapshot({"active": None, "sessions": []})
                    self.assertIsNotNone(snap2.active)
                    self.assertEqual(snap2.active.source_id, "spotify")
                    self.assertEqual(snap2.active.status, "Paused")

                with mock.patch("borh_island.services.media_service.is_process_running", return_value=False):
                    snap3 = media._build_snapshot({"active": None, "sessions": []})
                    self.assertIsNone(snap3.active)
            finally:
                media.stop()
                worker.stop()

    def test_celestial_minute_step_thunder_sparks_and_lightning_strike(self) -> None:
        core = QRectF(20.0, 20.0, 560.0, 420.0)
        # Continuous smooth celestial movement (gentle sub-minute step, no harsh teleports)
        s0, m0 = compute_celestial_layout(core, "sunrise", datetime(2026, 6, 15, 7, 15, 0))
        s59, m59 = compute_celestial_layout(core, "sunrise", datetime(2026, 6, 15, 7, 15, 59))
        self.assertIsNotNone(s0)
        self.assertIsNotNone(s59)
        self.assertIsNone(m0)
        self.assertIsNone(m59)
        self.assertNotEqual(s0, s59)
        self.assertLess(math.hypot(s59.x() - s0.x(), s59.y() - s0.y()), 3.5)

        # Sun rises morning -> day zenith -> evening -> strictly vanishes before Moon appears
        s_sun_morn, m_sun_morn = compute_celestial_layout(core, "sunrise", datetime(2026, 6, 15, 7, 0, 0))
        s_sun_day, m_sun_day = compute_celestial_layout(core, "day", datetime(2026, 6, 15, 13, 0, 0))
        s_sun_eve, m_moon_eve = compute_celestial_layout(core, "sunset", datetime(2026, 6, 15, 20, 15, 0))
        s_sun_night, m_moon_night = compute_celestial_layout(core, "night", datetime(2026, 6, 15, 22, 45, 0))
        self.assertIsNotNone(s_sun_morn)
        self.assertIsNone(m_sun_morn)
        self.assertIsNotNone(s_sun_day)
        self.assertIsNone(m_sun_day)
        self.assertIsNotNone(s_sun_eve)
        # Mutual exclusion: NEVER render Sun and Moon simultaneously
        self.assertIsNone(m_moon_eve)
        self.assertIsNone(s_sun_night)
        self.assertIsNotNone(m_moon_night)
        self.assertGreater(s_sun_morn.y(), s_sun_day.y())
        self.assertLessEqual(abs(s_sun_day.x() - core.center().x()), 18.0)

        # Winter SPb evening moon (e.g. 17:23 Nov 14) must be visible AND move continuously overnight
        spb_s, spb_m1 = compute_celestial_layout(core, "night", datetime(2026, 11, 14, 17, 23, 0), lat=59.93, lon=30.31)
        _, spb_m2 = compute_celestial_layout(core, "night", datetime(2026, 11, 14, 21, 10, 0), lat=59.93, lon=30.31)
        self.assertIsNone(spb_s)
        self.assertIsNotNone(spb_m1)
        self.assertIsNotNone(spb_m2)
        self.assertGreater(math.hypot(spb_m2.x() - spb_m1.x(), spb_m2.y() - spb_m1.y()), 25.0)

        # Wind speed drives cloud drift speed directly
        def _render_clouds(phase: float, wind: float) -> QImage:
            cim = QImage(320, 140, QImage.Format.Format_ARGB32_Premultiplied)
            cim.fill(Qt.GlobalColor.transparent)
            cp = QPainter(cim)
            AtmosphericScenePainter.paint_top_clouds_and_light(cp, QRectF(0, 0, 320, 140), "day", "partly", phase, wind_ms=wind)
            cp.end()
            return cim

        def _img_diff(ia: QImage, ib: QImage) -> float:
            tot = 0
            cnt = 0
            for yy in range(8, 130, 6):
                for xx in range(8, 312, 8):
                    ca, cb = ia.pixelColor(xx, yy), ib.pixelColor(xx, yy)
                    tot += abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue()) + abs(ca.alpha() - cb.alpha())
                    cnt += 4
            return tot / max(1, cnt)

        calm_diff = _img_diff(_render_clouds(0.0, 0.2), _render_clouds(4.0, 0.2))
        gale_diff = _img_diff(_render_clouds(0.0, 18.0), _render_clouds(4.0, 18.0))
        self.assertGreater(gale_diff, calm_diff * 2.5)

        # Thunder & Lightning weather modes + sky renders cleanly
        atmo_keys = dict(ATMO_MODES)
        weather_keys = dict(WEATHER_MODES)
        self.assertIn("day_thunder", atmo_keys)
        self.assertIn("day_lightning", atmo_keys)
        self.assertIn("thunder", weather_keys)
        self.assertIn("lightning", weather_keys)
        self.assertEqual(resolve_weather_kind("heavyrainandthunder"), "thunder")
        self.assertEqual(resolve_weather_kind("lightning"), "lightning")
        _, wk_th, _ = resolve_scene_state("auto", "", weather_mode="thunder")
        _, wk_li, _ = resolve_scene_state("auto", "", weather_mode="lightning")
        self.assertEqual(wk_th, "thunder")
        self.assertEqual(wk_li, "lightning")

        for wmode in ("thunder", "lightning"):
            img = QImage(600, 460, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            AtmosphericScenePainter.paint_expanded_atmosphere(
                p,
                core,
                preview_mode="day",
                weather_mode=wmode,
                phase_t=1.25,
                now=datetime(2026, 6, 15, 13, 10, 0),
                outer_bottom=core.bottom(),
                outer_rect=core,
            )
            p.end()
            self.assertGreater(img.pixelColor(int(core.center().x()), int(core.top() + 50)).alpha(), 100)

    def test_launcher_native_icons_autostart_tray_and_pure_text_gemini(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            provider = QFileIconProvider()
            app_item = LauncherItem(1, "Python", sys.executable, "app", "Dev", "", "", 0)
            qicon_app, need_fetch_app = resolve_launcher_qicon(app_item, root, provider)
            self.assertFalse(qicon_app.isNull())
            self.assertFalse(need_fetch_app)

            # Cached favicon PNG loads cleanly for URL items
            url = "https://example.org/docs"
            url_item = LauncherItem(2, "Docs", url, "url", "Web", "", "", 1)
            uncached_ico, need_fetch_1 = resolve_launcher_qicon(url_item, root, provider)
            self.assertFalse(uncached_ico.isNull())
            self.assertTrue(need_fetch_1)

            fav_path = _favicon_cache_path(url, root)
            fav_img = QImage(32, 32, QImage.Format.Format_ARGB32)
            fav_img.fill(QColor("#38BDF8"))
            fav_img.save(str(fav_path), "PNG")
            cached_ico, need_fetch_2 = resolve_launcher_qicon(url_item, root, provider)
            self.assertFalse(cached_ico.isNull())
            self.assertFalse(need_fetch_2)

            # Pure-text Gemini bridge via AgentsService (/gemini/pending -> /gemini/reply -> /gemini/ask)
            agents = AgentsService(root, port=0)
            self.assertTrue(agents.start())
            port = agents._server.server_port
            stop_evt = threading.Event()

            def _companion_worker() -> None:
                base = f"http://127.0.0.1:{port}"
                while not stop_evt.is_set():
                    try:
                        with urllib.request.urlopen(f"{base}/gemini/pending", timeout=1.5) as resp:
                            data = json.loads(resp.read().decode("utf-8"))
                        for q in data.get("queries", []):
                            req = urllib.request.Request(
                                f"{base}/gemini/reply",
                                data=json.dumps({"id": q["id"], "answer": f"LOCAL_ECHO:{q['prompt']}"}).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            urllib.request.urlopen(req, timeout=1.5).read()
                    except Exception:
                        pass
                    time.sleep(0.05)

            th = threading.Thread(target=_companion_worker, daemon=True)
            th.start()
            deadline = time.monotonic() + 2.0
            while not agents.is_gemini_worker_online() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(agents.is_gemini_worker_online())
            try:
                cm = ConfigManager(root)
                cm.config.ai_base_url = f"http://127.0.0.1:{port}"
                ai = AIService()
                ans = ai.send_chat(
                    cm.config,
                    [{"role": "user", "content": "Привет"}],
                    "Сколько будет 2+2?",
                    attachments=[str(fav_path)],
                    pure_text_only=True,
                    provider_override="gemini_local",
                )
                self.assertIn("LOCAL_ECHO:", ans)
                self.assertIn("Сколько будет 2+2?", ans)
            finally:
                stop_evt.set()
                agents.stop()

            # Controller tray menu + hotkeys + Controls autostart toggle + Gemini UI removed + full circular crop
            from main import BorhIslandController
            from borh_island.ui.tabs.home_tab import _AtollAppBubble, _make_circular_app_icon

            # Rounded-square tile (Xbox style) with soft drop-shadow margin must crop into a full-bleed circle
            sq_tile = QImage(160, 160, QImage.Format.Format_ARGB32)
            sq_tile.fill(Qt.GlobalColor.transparent)
            sp = QPainter(sq_tile)
            sp.setPen(Qt.PenStyle.NoPen)
            sp.setBrush(QColor(0, 0, 0, 48))  # Win11 soft outer drop-shadow halo
            sp.drawRoundedRect(6, 6, 148, 148, 28, 28)
            sp.setBrush(QColor("#107C10"))
            sp.drawRoundedRect(14, 14, 132, 132, 22, 22)
            sp.end()
            circ_qicon = _make_circular_app_icon(sq_tile, size=96)
            circ_img = circ_qicon.pixmap(96, 96).toImage()
            # Corners outside circle must remain transparent, while top/bottom/left/right edge rims are filled (no semi-circle gap!)
            self.assertEqual(circ_img.pixelColor(2, 2).alpha(), 0)
            self.assertGreater(circ_img.pixelColor(48, 3).alpha(), 200)
            self.assertGreater(circ_img.pixelColor(3, 48).alpha(), 200)

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
                with mock.patch("borh_island.ui.tabs.controls_tab.set_autostart", return_value=True) as mock_auto:
                    ctrl = BorhIslandController(initial_expand=True, initial_tab=4)
                    try:
                        self.assertEqual(ctrl.panel.stack.count(), 5)
                        self.assertFalse(hasattr(ctrl.panel, "chat_tab"))
                        self.assertFalse(hasattr(ctrl.panel, "stats_tab"))
                        self.assertFalse(hasattr(ctrl.panel, "clipboard_tab"))
                        self.assertFalse(hasattr(ctrl.panel, "shelf_tab"))
                        self.assertEqual(ctrl.panel.stack.currentIndex(), 4)
                        self.assertIsInstance(ctrl.panel.home_tab.add_btn, _AtollAppBubble)
                        self.assertIsNotNone(getattr(ctrl, "act_open", None))
                        self.assertIsNotNone(getattr(ctrl, "act_cmd", None))
                        self.assertIsNotNone(getattr(ctrl, "act_through", None))
                        self.assertIsNotNone(getattr(ctrl, "act_settings", None))
                        self.assertIsNotNone(getattr(ctrl, "act_quit", None))
                        self.assertIn("Ctrl+Alt+P", ctrl.act_settings.text())

                        prev = bool(ctrl.config_mgr.config.autostart)
                        ctrl.panel.controls_tab.autostart_btn.click()
                        self.assertEqual(bool(ctrl.config_mgr.config.autostart), not prev)
                        mock_auto.assert_called_once()
                    finally:
                        ctrl.cleanup()
                        ctrl.island._watch_timer.stop()
                        ctrl.island.close()
                        ctrl.command_bar.close()
                        ctrl.live_cards.close()

    def test_grass_skeletal_bones_and_non_teleporting_cursor_sweep(self) -> None:
        # 1) Articulated 4-bone (5-joint) stem keeps constant segment lengths across bends
        joints = compute_blade_joints(100.0, 400.0, 100.0, 0.72, 0.38)
        self.assertEqual(len(joints), 5)
        expected_segs = (24.0, 27.0, 27.0, 22.0)
        for idx, exp_len in enumerate(expected_segs):
            seg_len = math.hypot(joints[idx + 1][0] - joints[idx][0], joints[idx + 1][1] - joints[idx][1])
            self.assertAlmostEqual(seg_len, exp_len, places=3)

        # 2) Sweeping cursor continuously across grass blades bends smoothly with zero teleport flips
        sim = GrassSkeletonSim()
        n_blades = 80
        eff_left, eff_width, root_y = 0.0, 560.0, 400.0
        target_i = 40
        target_bx = eff_left + ((target_i + 0.5) / n_blades) * eff_width

        # Sweep rightwards across target_bx from -32px to +45px
        prev_ang: float | None = None
        max_step_jump = 0.0
        crossed_angles: list[float] = []
        for dx in range(-32, 48, 4):
            cx = target_bx + dx
            states = sim.step(
                n_blades,
                eff_left,
                eff_width,
                root_y,
                False,
                0.5,
                QPointF(cx, root_y - 38.0),
                1.0,
                True,
                0.0,
                None,
                dt=0.026,
            )
            ang = states[target_i][2]
            if prev_ang is not None:
                max_step_jump = max(max_step_jump, abs(ang - prev_ang))
            prev_ang = ang
            if -12 <= dx <= 24:
                crossed_angles.append(ang)

        # Must animate smoothly step-by-step (no instant sign flip teleport across dx=0)
        self.assertLess(max_step_jump, 0.30)
        # All samples while sweeping over blade maintain coherent deflection sign
        self.assertTrue(all(a < 0.05 for a in crossed_angles) or all(a > -0.05 for a in crossed_angles))
        self.assertTrue(sim.is_settling())

        # Release cursor and confirm spring rebounds gently toward upright rest
        for _ in range(45):
            states = sim.step(n_blades, eff_left, eff_width, root_y, False, 0.5, None, 0.0, False, 0.0, None, dt=0.026)
        self.assertLess(abs(states[target_i][2]), 0.16)

        # Stretching outer rect horizontally must preserve skeletal blade states (no reset!)
        prev_cnt = sim.n_blades
        prev_ang = list(sim.angles)
        sim.step(n_blades, eff_left - 40.0, eff_width + 80.0, root_y, False, 0.55, None, 0.0, False, 0.0, None, dt=0.026)
        self.assertEqual(sim.n_blades, prev_cnt)
        self.assertEqual(len(sim.angles), len(prev_ang))

        # Verify earth horizon line is drawn along bottom turf root
        g_img = QImage(320, 140, QImage.Format.Format_ARGB32_Premultiplied)
        g_img.fill(Qt.GlobalColor.transparent)
        gp = QPainter(g_img)
        AtmosphericScenePainter.paint_bottom_grass(
            gp,
            QRectF(10.0, 10.0, 300.0, 110.0),
            "day",
            "clear",
            0.5,
            outer_bottom=130.0,
            outer_rect=QRectF(10.0, 10.0, 300.0, 120.0),
            grass_sim=GrassSkeletonSim(),
        )
        gp.end()
        self.assertGreater(g_img.pixelColor(160, 124).alpha(), 100)

    def test_system_monitor_first_tick_cpu_nonzero_and_win32_isolated(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            svc = SystemMonitorService(cm)
            received: list[SystemStatsSnapshot] = []
            svc.stats_updated.connect(received.append)
            try:
                svc.poll_now()
                deadline = time.monotonic() + 3.0
                while not received and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.02)
                self.assertTrue(received, "Expected first telemetry snapshot")
                # Even with Win32 CPU idle > 0, CPU work during python/Qt test produces > 0% on tick 1
                self.assertGreater(received[0].cpu_percent, 0.0)
            finally:
                svc.stop()

    def test_new_characters_render_distinctly(self) -> None:
        from borh_island.ui.characters import CHARACTER_DEFS, CharacterWidget

        self.assertEqual(len(CHARACTER_DEFS), 40)
        ids = [cid for cid, _, _ in CHARACTER_DEFS]
        for required_id in ("borscht", "purple_star", "kamaz", "meimei", "claude"):
            self.assertIn(required_id, ids)
            cw = CharacterWidget(required_id)
            cw.resize(96, 96)
            cw.step_frame(1.2)
            img = cw.grab().toImage()
            non_empty = sum(
                1
                for y in range(12, 84, 3)
                for x in range(12, 84, 3)
                if img.pixelColor(x, y).alpha() > 30
            )
            self.assertGreater(non_empty, 25, f"Character {required_id} rendered empty")
            cw.close()

        # Verify borscht animation visibly moves the spoon/broth between frames
        borscht = CharacterWidget("borscht")
        borscht.resize(96, 96)
        f1 = borscht.grab().toImage()
        borscht.step_frame(8.0)
        f2 = borscht.grab().toImage()
        diff_pixels = sum(
            1
            for y in range(12, 84, 3)
            for x in range(12, 84, 3)
            if f1.pixelColor(x, y) != f2.pixelColor(x, y)
        )
        self.assertGreater(diff_pixels, 15)
        borscht.close()

    def test_installed_apps_scanner_dialog_and_shell_launch(self) -> None:
        from unittest.mock import patch
        from borh_island.ui.tabs.home_tab import (
            HomeTab,
            InstalledAppEntry,
            _InstalledAppsDialog,
            _parse_start_apps_json,
            scan_installed_apps,
        )

        raw = json.dumps([
            {"Name": "Xbox", "AppID": "Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App"},
            {"Name": "Google Chrome", "AppID": "Chrome"},
        ])
        parsed = _parse_start_apps_json(raw)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].target, "shell:AppsFolder\\Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App")
        self.assertEqual(parsed[0].kind, "uwp")

        with (
            patch("borh_island.ui.tabs.home_tab._fetch_start_apps_powershell", return_value=parsed),
            patch("borh_island.ui.tabs.home_tab._scan_shortcut_folders", return_value=[
                InstalledAppEntry(title="Xbox", target="C:\\Users\\Public\\Desktop\\Xbox.lnk", kind="app", source_tag="Рабочий стол"),
                InstalledAppEntry(title="Steam", target="C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\Steam.lnk", kind="app", source_tag="Меню Пуск"),
            ]),
        ):
            scanned = scan_installed_apps(force=True)
        # UWP Xbox must win over Desktop .lnk duplicate so icon extraction avoids shortcut overlay
        by_title = {e.title: e for e in scanned}
        self.assertIn("Steam", by_title)
        self.assertTrue(by_title["Xbox"].target.startswith("shell:AppsFolder\\"))

        dlg = _InstalledAppsDialog(entries=scanned)
        try:
            self.assertEqual(dlg.list_widget.count(), 3)
            dlg.search_edit.setText("xbox")
            self.app.processEvents()
            self.assertEqual(dlg.list_widget.count(), 1)
            dlg._accept_selected()
            self.assertEqual(dlg.chosen_action, "app")
            self.assertIsNotNone(dlg.selected_entry)
            self.assertEqual(dlg.selected_entry.title, "Xbox")
        finally:
            dlg.close()

        # Confirm shell:AppsFolder launching delegates cleanly without crashing
        with patch("os.startfile", side_effect=OSError("mocked")), patch("subprocess.Popen") as popen_mock:
            HomeTab._launch_item("shell:AppsFolder\\Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App")
            popen_mock.assert_called_once_with(
                ["explorer.exe", "shell:AppsFolder\\Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App"]
            )

    def test_rounded_buttons_and_expanded_panel_slide(self) -> None:
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.settings_window import SettingsWindow
        from borh_island.ui.theme import build_panel_qss, resolve_palette

        qss = build_panel_qss(resolve_palette("dark"))
        self.assertIn("QComboBox::drop-down", qss)
        self.assertIn("QTabBar::tab", qss)
        self.assertIn("QCheckBox::indicator", qss)
        self.assertIn("QScrollBar:horizontal", qss)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
                cm = ConfigManager(root)
                st = LocalStorage(root / "test.db")
                br = NativeBridge(root)
                wk = WinRTWorker()
                md = MediaService(wk, br)
                cl = ClipboardService(st, cm, root / "clip")
                we = WeatherService()
                ai = AIService()
                ag = AgentsService(root, port=0)
                panel = ExpandedPanel(cm, st, br, md, cl, we, ai, ag, resolve_palette("dark"))
                settings = SettingsWindow(cm, ag)
                try:
                    panel.resize(420, 560)
                    panel.show()
                    settings.resize(520, 620)
                    settings.show()
                    self.app.processEvents()

                    sess = MediaSessionInfo(
                        app_id="Spotify.exe",
                        title="Test Track",
                        artist="Borh",
                        status="Playing",
                        position=45.0,
                        duration=180.0,
                        source_id="spotify",
                        source_name="Spotify",
                        accent_color="#1DB954",
                    )
                    home = panel.home_tab
                    home.update_media_state(MediaStateSnapshot(active=sess, sessions=[sess]))
                    self.app.processEvents()

                    # Pill/circle heights and matching border-radius <= min(w, h)/2 on HomeTab
                    self.assertEqual(home.prev_btn.width(), 36)
                    self.assertEqual(home.prev_btn.height(), 36)
                    self.assertEqual(home.next_btn.width(), 36)
                    self.assertEqual(home.next_btn.height(), 36)
                    self.assertEqual(home.play_btn.height(), 36)
                    self.assertEqual(home.lyrics_btn.height(), 32)
                    self.assertEqual(home.vol_btn.width(), 32)
                    self.assertEqual(home.vol_btn.height(), 32)
                    self.assertIn("border-radius: 18px", home.play_btn.styleSheet())
                    self.assertIn("border-radius: 18px", home.prev_btn.styleSheet())
                    self.assertIn("border-radius: 16px", home.lyrics_btn.styleSheet())
                    self.assertIn("border-radius: 16px", home.vol_btn.styleSheet())
                    home.set_dev_tools_visible(True)
                    self.app.processEvents()
                    for tbtn in home.tool_buttons.values():
                        self.assertEqual(tbtn.height(), 30)
                        self.assertIn("border-radius: 12px", tbtn.styleSheet())
                    # No side swipe buttons & no circular collapse button + non-overflowing hovered launcher bubble
                    self.assertFalse(hasattr(panel, "prev_tab_btn"))
                    self.assertFalse(hasattr(panel, "next_tab_btn"))
                    self.assertFalse(hasattr(panel, "collapse_btn"))
                    self.assertLessEqual(home.add_btn._max_sz, home.launcher_scroll.height() - 6)

                    # Launcher wraps into row 2 first, then extends horizontally once 2 rows are full
                    for idx in range(12):
                        st.add_launcher_item(f"App{idx}", r"C:\Windows\notepad.exe", group_name="")
                    home.refresh_launcher()
                    self.app.processEvents()
                    cols = home._launcher_cols_per_row()
                    self.assertEqual(home.launcher_grid.rowCount(), 2)
                    self.assertGreater(home.launcher_grid.columnCount(), cols)
                    # Item right after row 1 wraps to row 1 (second row), col 0
                    self.assertIs(home.launcher_grid.itemAtPosition(1, 0).widget(), home._launcher_bubbles[cols])
                    # After 2 rows are full, overflow extends into col >= cols across rows 0..1
                    self.assertIs(home.launcher_grid.itemAtPosition(0, cols).widget(), home._launcher_bubbles[2 * cols])

                    # Notes tab uses large pill controls (>= 36px) & expanded island height > 560
                    self.assertGreaterEqual(panel.notes_tab.btn_mode_notes.height(), 36)
                    self.assertGreaterEqual(panel.notes_tab.btn_mode_rem.height(), 36)
                    self.assertGreaterEqual(panel.notes_tab.timer_start_btn.height(), 36)

                    # WeatherService.fetch_weather parses MET Norway payload with sunrise/sunset dict unpacking
                    ws = WeatherService()
                    fake_payload = {
                        "properties": {
                            "timeseries": [
                                {
                                    "time": "2026-11-15T00:00:00Z",
                                    "data": {
                                        "instant": {
                                            "details": {
                                                "air_temperature": 4.2,
                                                "relative_humidity": 82.0,
                                                "wind_speed": 5.1,
                                                "wind_from_direction": 210.0,
                                                "air_pressure_at_sea_level": 1008.0,
                                                "ultraviolet_index_clear_sky": 1.0,
                                            }
                                        },
                                        "next_1_hours": {
                                            "summary": {"symbol_code": "cloudy"},
                                            "details": {"precipitation_amount": 0.0},
                                        },
                                    },
                                }
                            ]
                        }
                    }
                    fake_resp = mock.MagicMock(json=lambda: fake_payload)
                    fake_client = mock.MagicMock()
                    fake_client.__enter__.return_value = fake_client
                    fake_client.get.return_value = fake_resp
                    with mock.patch("borh_island.services.weather_service.httpx.Client", return_value=fake_client):
                        rep = ws.fetch_weather("Санкт-Петербург", 59.93, 30.33, force=True)
                    self.assertIsNotNone(rep)
                    self.assertAlmostEqual(rep.temp_c, 4.2, places=1)
                    self.assertAlmostEqual(rep.humidity, 82.0, places=1)
                    self.assertAlmostEqual(rep.wind_ms, 5.1, places=1)
                    self.assertEqual(len(rep.hourly), 1)

                    # Character gallery uses a single shared timer only while visible
                    panel.switch_tab(1)
                    self.app.processEvents()
                    self.assertTrue(panel.character_tab._thumb_timer.isActive())

                    # Smooth horizontal tab swipe / slide transitions
                    panel.switch_tab(0)
                    panel.step_tab(1)
                    self.app.processEvents()
                    self.assertEqual(panel.stack.currentIndex(), 1)
                    panel.stack.slide_to(0, direction=-1)
                    self.app.processEvents()
                    self.assertEqual(panel.stack.currentIndex(), 0)
                finally:
                    panel.weather_tab._refresh_timer.stop()
                    settings.close()
                    md.stop()
                    wk.stop()
                    panel.close()
                    st.close()

    def test_per_app_audio_mixer_and_profiles(self) -> None:
        from borh_island.ui.tabs.controls_tab import ControlsTab

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            br = NativeBridge(root)
            sample_raw = json.dumps(
                [
                    {"pid": 11, "process": "BorhWinNative", "title": "BorhWinNative", "exe": "", "volume": 100, "muted": False, "active": True, "system": False},
                    {"pid": 22, "process": "Discord", "title": "Discord", "exe": "", "volume": 100, "muted": False, "active": True, "system": False},
                    {"pid": 23, "process": "Discord", "title": "Discord", "exe": "", "volume": 80, "muted": False, "active": False, "system": False},
                    {"pid": 33, "process": "Spotify", "title": "Spotify", "exe": "", "volume": 75, "muted": False, "active": True, "system": False},
                    {"pid": 0, "process": "__system__", "title": "System Sounds", "exe": "", "volume": 50, "muted": False, "active": False, "system": True},
                ]
            )
            with mock.patch.object(br, "_run", return_value=sample_raw):
                sessions = br.list_app_sessions()
            keys = [s["key"] for s in sessions]
            self.assertNotIn("borhwinnative", keys)
            self.assertEqual(keys.count("discord"), 1)
            self.assertEqual(next(s["volume"] for s in sessions if s["key"] == "discord"), 100)
            self.assertEqual(next(s["title"] for s in sessions if s["key"] == "__system__"), "Системные звуки")

            with mock.patch.object(br, "_run", return_value='{"applied": 2}') as run_mock:
                cnt = br.apply_audio_profile({"discord": {"volume": 100, "muted": False}, "spotify": {"volume": 40, "muted": False}})
            self.assertEqual(cnt, 2)
            self.assertEqual(run_mock.call_args[0][0], "app-profile-apply")
            self.assertIn("discord=100:0", run_mock.call_args[0][2])
            self.assertIn("spotify=40:0", run_mock.call_args[0][2])

            with (
                mock.patch.object(ControlsTab, "refresh_hardware", lambda self: None),
                mock.patch.object(ControlsTab, "refresh_app_sessions", lambda self: None),
            ):
                tab = ControlsTab(br, cm)
            try:
                tab.resize(420, 580)
                tab.show()
                tab._on_app_sessions_loaded(sessions)
                self.app.processEvents()
                self.assertEqual(set(tab._mixer_rows.keys()), {"discord", "spotify", "__system__"})
                self.assertIn(tab.mixer_refresh_btn.height(), (28, 30))
                self.assertIn("border-radius: 14px", tab.mixer_refresh_btn.styleSheet())
                self.assertIn(tab.mixer_apply_btn.height(), (28, 30))
                self.assertIn("border-radius: 14px", tab.mixer_apply_btn.styleSheet())
                self.assertIn(tab._mixer_rows["spotify"]["mute_btn"].height(), (28, 30))

                # Save a new profile from current mixer state and confirm persistence across ConfigManager reload
                tab._mixer_rows["spotify"]["slider"].setValue(33)
                tab._mixer_rows["discord"]["mute_btn"].setChecked(True)
                tab._toggle_new_profile_row()
                tab.mixer_new_edit.setText("Стрим тест")
                tab._confirm_new_profile()
                self.assertIn("Стрим тест", cm.config.audio_mixer_profiles)
                self.assertEqual(cm.config.audio_mixer_profiles["Стрим тест"]["spotify"]["volume"], 33)
                self.assertTrue(cm.config.audio_mixer_profiles["Стрим тест"]["discord"]["muted"])

                reloaded = ConfigManager(root)
                self.assertIn("Стрим тест", reloaded.config.audio_mixer_profiles)
                tab._delete_mixer_profile()
                self.assertNotIn("Стрим тест", cm.config.audio_mixer_profiles)
                reloaded2 = ConfigManager(root)
                self.assertNotIn("Стрим тест", reloaded2.config.audio_mixer_profiles)
            finally:
                tab.close()

    def test_bilingual_controls_and_zero_emoji_stickers(self) -> None:
        from PyQt6.QtWidgets import QLabel, QPushButton
        from borh_island.core.i18n import set_language
        from borh_island.services.agents_service import AgentsService
        from borh_island.services.ai_service import AIService
        from borh_island.services.clipboard_service import ClipboardService
        from borh_island.services.media_service import MediaService
        from borh_island.services.weather_service import WeatherService
        from borh_island.ui.expanded_panel import ExpandedPanel

        forbidden_emojis = ("🌙", "🌓", "☀", "🔒", "🔄", "⏻", "🚀", "🔊", "🔇", "🎙", "🛡", "🎧", "🎛", "☁", "⛅", "⚡")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            st = LocalStorage(root / "ctrl_i18n.db")
            br = NativeBridge(root)
            wk = WinRTWorker()
            md = MediaService(wk, br)
            cl = ClipboardService(st, cm, root / "clip")
            wt = WeatherService()
            ai = AIService()
            ag = AgentsService(root, port=17538)
            orig_lang = cm.config.language
            try:
                set_language("ru")
                panel = ExpandedPanel(cm, st, br, md, cl, wt, ai, ag, resolve_palette("dark"))
                ctrl = panel.controls_tab
                pwr_ru = {key: btn for btn, key, _ in ctrl._power_buttons}
                self.assertEqual(panel.tab_buttons[-1].text(), "")
                self.assertEqual(panel.tab_buttons[-1].toolTip(), "Настройки")
                self.assertEqual(ctrl.win_theme_btn.text(), "Тёмная тема Windows")
                self.assertEqual(pwr_ru["lock"].text(), "Блокировка")
                self.assertEqual(pwr_ru["sleep"].text(), "Сон")
                self.assertEqual(pwr_ru["restart"].text(), "Перезагрузка")
                self.assertEqual(pwr_ru["shutdown"].text(), "Выключение")

                set_language("en")
                panel.retranslate_ui()
                pwr_en = {key: btn for btn, key, _ in ctrl._power_buttons}
                self.assertEqual(panel.tab_buttons[-1].text(), "")
                self.assertEqual(panel.tab_buttons[-1].toolTip(), "Settings")
                self.assertEqual(ctrl.win_theme_btn.text(), "Windows Dark Theme")
                self.assertEqual(pwr_en["lock"].text(), "Lock")
                self.assertEqual(pwr_en["sleep"].text(), "Sleep")
                self.assertEqual(pwr_en["restart"].text(), "Restart")
                self.assertEqual(pwr_en["shutdown"].text(), "Shut down")
                self.assertEqual(ctrl.mixer_title_lbl.text(), "App Volume Mixer")

                for w in ctrl.findChildren(QPushButton) + ctrl.findChildren(QLabel):
                    txt = w.text()
                    for em in forbidden_emojis:
                        self.assertNotIn(em, txt, f"Unexpected raw emoji {em!r} in {txt!r}")
                self.assertFalse(ctrl.win_theme_btn.icon().isNull())
                self.assertFalse(pwr_en["lock"].icon().isNull())
            finally:
                set_language(orig_lang)
                panel.weather_tab._refresh_timer.stop()
                md.stop()
                wk.stop()
                panel.close()
                st.close()

    def test_smoke_clouds_spawn_and_mouse_dispersal(self) -> None:
        from PyQt6.QtCore import QPointF, QRectF
        from PyQt6.QtGui import QImage, QPainter
        from borh_island.ui.atmosphere import AtmosphericScenePainter, CloudSmokeField

        rect = QRectF(80.0, 20.0, 560.0, 480.0)
        sim_calm = CloudSmokeField(seed=9)
        wisps_calm = sim_calm.step(rect, 0.85, 4.0, 1.0, None, dt=0.04)
        self.assertGreaterEqual(len(wisps_calm), 30)

        sim_hover = CloudSmokeField(seed=9)
        for step_i in range(10):
            cur = QPointF(240.0 + step_i * 6.0, 42.0)
            wisps_hover = sim_hover.step(rect, 0.85, 4.0, 1.0 + step_i * 0.04, cur, dt=0.04)
        self.assertTrue(sim_hover.is_dispersing())
        max_disp = max(w[6] for w in wisps_hover)
        self.assertGreater(max_disp, 0.25)

        img = QImage(640, 260, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        AtmosphericScenePainter.paint_top_clouds_and_light(
            p,
            QRectF(0.0, 0.0, 640.0, 240.0),
            "day",
            "cloudy",
            2.4,
            cursor_local=QPointF(280.0, 35.0),
            cloud_sim=sim_hover,
        )
        p.end()

    def test_music_bottom_waves_and_diverse_spikes(self) -> None:
        from PyQt6.QtGui import QPainterPath
        from borh_island.services.agents_service import AgentsService
        from borh_island.services.ai_service import AIService
        from borh_island.services.clipboard_service import ClipboardService
        from borh_island.services.media_service import MediaService
        from borh_island.services.weather_service import WeatherService
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.island_window import IslandWindow

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            st = LocalStorage(root / "wave_test.db")
            br = NativeBridge(root)
            wk = WinRTWorker()
            md = MediaService(wk, br)
            cl = ClipboardService(st, cm, root / "clip")
            wt = WeatherService()
            ai = AIService()
            ag = AgentsService(root, port=17539)
            try:
                panel = ExpandedPanel(cm, st, br, md, cl, wt, ai, ag, resolve_palette("dark"))
                island = IslandWindow(cm, br, panel)
                island.resize(740, 640)
                island.set_expanded(True)
                island._playing = True
                island._music_energy = 0.88
                island._music_beat = 0.72
                island._music_bands = [0.9, 0.6, 0.85, 0.3, 0.75, 0.95, 0.5, 0.8, 0.6, 0.4] * 2
                core = island._core_rect()

                island._music_phase = 0.4
                path1 = island._build_liquid_path(core, 32.0)
                island._music_phase = 2.9
                path2 = island._build_liquid_path(core, 32.0)

                self.assertGreater(path1.boundingRect().bottom(), core.bottom() + 10.0)
                self.assertGreater(path2.boundingRect().bottom(), core.bottom() + 10.0)
                self.assertNotEqual(path1.boundingRect().bottom(), path2.boundingRect().bottom())

                elems = [path1.elementAt(i).type for i in range(path1.elementCount())]
                self.assertIn(QPainterPath.ElementType.LineToElement, elems)
                self.assertIn(QPainterPath.ElementType.CurveToElement, elems)

                # Collapsed bottom micro-spikes + fluid wave should also bulge cleanly
                island.set_expanded(False)
                core_c = island._core_rect()
                path_c = island._build_liquid_path(core_c, 21.5)
                self.assertGreater(path_c.boundingRect().bottom(), core_c.bottom() + 4.0)
            finally:
                panel.weather_tab._refresh_timer.stop()
                md.stop()
                wk.stop()
                island.close()
                panel.close()
                st.close()

    def test_settings_live_atmosphere_apply_and_thunder_storm_render(self) -> None:
        from datetime import datetime
        from PyQt6.QtCore import QRectF, Qt
        from PyQt6.QtGui import QImage, QPainter
        from borh_island.services.agents_service import AgentsService
        from borh_island.services.ai_service import AIService
        from borh_island.services.clipboard_service import ClipboardService
        from borh_island.services.media_service import MediaService
        from borh_island.services.weather_service import WeatherService
        from borh_island.ui.atmosphere import AtmosphericScenePainter, CloudSmokeField
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.settings_window import SettingsWindow

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            st = LocalStorage(root / "atmo_live.db")
            br = NativeBridge(root)
            wk = WinRTWorker()
            md = MediaService(wk, br)
            cl = ClipboardService(st, cm, root / "clip")
            wt = WeatherService()
            ai = AIService()
            ag = AgentsService(root, port=17540)
            panel = ExpandedPanel(cm, st, br, md, cl, wt, ai, ag, resolve_palette("dark"))
            settings = SettingsWindow(cm, ag)
            try:
                emitted: list[int] = []
                settings.config_applied.connect(lambda: emitted.append(1))
                idx_thunder = settings.weather_combo.findData("thunder")
                self.assertGreaterEqual(idx_thunder, 0)
                settings.weather_combo.setCurrentIndex(idx_thunder)
                settings.weather_combo.activated.emit(idx_thunder)
                self.assertEqual(cm.config.atmosphere_weather_mode, "thunder")
                self.assertEqual(len(emitted), 1)
                self.assertEqual(ConfigManager(root).config.atmosphere_weather_mode, "thunder")

                panel_atmo: list[int] = []
                panel.controls_tab.atmosphere_changed.connect(lambda: panel_atmo.append(1))
                ctab = panel.controls_tab
                w_idx = ctab.atmo_weather_combo.findData("lightning")
                self.assertGreaterEqual(w_idx, 0)
                ctab.atmo_weather_combo.setCurrentIndex(w_idx)
                ctab.atmo_weather_combo.activated.emit(w_idx)
                self.assertEqual(cm.config.atmosphere_weather_mode, "lightning")
                self.assertEqual(len(panel_atmo), 1)

                rect = QRectF(20.0, 20.0, 540.0, 420.0)
                sim = CloudSmokeField(seed=17)
                for _ in range(5):
                    sim.step(rect, 0.92, 6.0, 1.5, None, dt=0.05)

                img_th = QImage(580, 460, QImage.Format.Format_ARGB32_Premultiplied)
                img_th.fill(Qt.GlobalColor.transparent)
                p1 = QPainter(img_th)
                AtmosphericScenePainter.paint_expanded_atmosphere(
                    p1,
                    rect,
                    preview_mode="auto",
                    sky_mode="day",
                    weather_mode="thunder",
                    phase_t=0.85,
                    now=datetime(2026, 6, 15, 14, 0, 0),
                    outer_bottom=rect.bottom(),
                    outer_rect=rect,
                    cloud_sim=sim,
                )
                p1.end()

                img_st = QImage(580, 460, QImage.Format.Format_ARGB32_Premultiplied)
                img_st.fill(Qt.GlobalColor.transparent)
                p2 = QPainter(img_st)
                AtmosphericScenePainter.paint_expanded_atmosphere(
                    p2,
                    rect,
                    preview_mode="auto",
                    sky_mode="day",
                    weather_mode="lightning",
                    phase_t=0.10,
                    now=datetime(2026, 6, 15, 14, 0, 0),
                    outer_bottom=rect.bottom(),
                    outer_rect=rect,
                    cloud_sim=sim,
                )
                p2.end()
                self.assertNotEqual(img_th.constBits().asstring(img_th.sizeInBytes()), img_st.constBits().asstring(img_st.sizeInBytes()))
            finally:
                panel.weather_tab._refresh_timer.stop()
                settings.close()
                md.stop()
                wk.stop()
                panel.close()
                st.close()

    def test_realistic_moon_stars_smoke_wind_and_randomized_grass(self) -> None:
        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import QImage, QPainter
        from borh_island.services.agents_service import AgentsService
        from borh_island.services.ai_service import AIService
        from borh_island.services.clipboard_service import ClipboardService
        from borh_island.services.media_service import MediaService
        from borh_island.services.weather_service import WeatherService
        from borh_island.ui.atmosphere import (
            AtmosphericScenePainter,
            CloudSmokeField,
            _px_per_cm,
        )
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.island_window import IslandWindow

        # 1) 2x Smoke cloud density & rightward wind velocity scaling with wind m/s
        sky_rect = QRectF(40.0, 20.0, 560.0, 200.0)
        sim_slow = CloudSmokeField(seed=5)
        sim_fast = CloudSmokeField(seed=5)
        w_slow = sim_slow.step(sky_rect, 0.92, 0.5, 1.0, None, dt=0.05)
        w_fast = sim_fast.step(sky_rect, 0.92, 16.0, 1.0, None, dt=0.05)
        self.assertGreaterEqual(len(w_slow), 300)
        dx_slow = [float(sim_slow._wisps[i]["u"]) for i in range(10)]
        dx_fast = [float(sim_fast._wisps[i]["u"]) for i in range(10)]
        # Rightward modular advancement after identical dt is strictly ahead at 16 m/s vs 0.5 m/s
        self.assertTrue(all(0.004 < ((f - s) % 1.32) < 0.25 for f, s in zip(dx_fast, dx_slow)))

        # 2) All 8 lunar phases render distinctly via procedural 3D cratered moon texture
        phase_bytes: list[bytes] = []
        for ph in (0.0, 0.14, 0.25, 0.38, 0.50, 0.62, 0.75, 0.88):
            m_img = QImage(72, 72, QImage.Format.Format_ARGB32_Premultiplied)
            m_img.fill(Qt.GlobalColor.black)
            mp = QPainter(m_img)
            AtmosphericScenePainter.paint_moon(mp, QPointF(36.0, 36.0), 18.0, ph)
            mp.end()
            phase_bytes.append(m_img.constBits().asstring(m_img.sizeInBytes()))
        self.assertEqual(len(set(phase_bytes)), 8)

        # 3) Night starfield has abundant stars & distinct scintillation across time
        n1 = QImage(540, 200, QImage.Format.Format_ARGB32_Premultiplied)
        n2 = QImage(540, 200, QImage.Format.Format_ARGB32_Premultiplied)
        for im, pt in ((n1, 0.2), (n2, 2.7)):
            im.fill(Qt.GlobalColor.black)
            np_ = QPainter(im)
            AtmosphericScenePainter.paint_constellations(np_, QRectF(0.0, 0.0, 540.0, 200.0), pt, visibility=1.0)
            np_.end()
        self.assertNotEqual(n1.constBits().asstring(n1.sizeInBytes()), n2.constBits().asstring(n2.sizeInBytes()))

        # 4) Fixed world-locked precipitation & soil even under huge outer_rect distortion + invisible grass reseed on open
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            st = LocalStorage(root / "reseed_test.db")
            br = NativeBridge(root)
            wk = WinRTWorker()
            md = MediaService(wk, br)
            cl = ClipboardService(st, cm, root / "clip")
            wt = WeatherService()
            ai = AIService()
            ag = AgentsService(root, port=17541)
            panel = ExpandedPanel(cm, st, br, md, cl, wt, ai, ag, resolve_palette("dark"))
            island = IslandWindow(cm, br, panel)
            try:
                island.resize(740, 640)
                island.set_expanded(True)
                core = island._core_rect()
                island._grass_sim.step(180, core.left() - 36.0, core.width() + 72.0, core.bottom() - 2.5, False, 0.5, None, 0.0, False, 0.0, None, dt=0.02)
                heights_1 = list(island._grass_sim.heights)
                self.assertGreater(max(heights_1) - min(heights_1), 50.0)
                self.assertGreater(max(heights_1), _px_per_cm() * 5.0)
                self.assertLessEqual(max(heights_1), _px_per_cm() * 7.05)

                # Hover near edge never translates interior canvas via _panel_shake_dx
                island._update_liquid_physics(QPointF(island.x() + core.right() + 24.0, island.y() + core.center().y()))
                self.assertEqual((island._panel_shake_dx, island._panel_shake_dy), (0, 0))

                # Close/re-open invisibly generates new random grass lengths
                island.set_expanded(False)
                island.set_expanded(True)
                island._grass_sim.step(180, core.left() - 36.0, core.width() + 72.0, core.bottom() - 2.5, False, 0.5, None, 0.0, False, 0.0, None, dt=0.02)
                heights_2 = list(island._grass_sim.heights)
                self.assertNotEqual(heights_1, heights_2)

                # Stretched outer_rect keeps interior sky/earth/rain pixel at fixed core coordinate identical
                core_fix = QRectF(50.0, 30.0, 480.0, 360.0)
                img_norm = QImage(620, 440, QImage.Format.Format_ARGB32_Premultiplied)
                img_stretch = QImage(620, 440, QImage.Format.Format_ARGB32_Premultiplied)
                from datetime import datetime
                for im, orect in ((img_norm, core_fix), (img_stretch, QRectF(12.0, 20.0, 585.0, 405.0))):
                    im.fill(Qt.GlobalColor.transparent)
                    pt_ = QPainter(im)
                    AtmosphericScenePainter.paint_expanded_atmosphere(
                        pt_, core_fix, preview_mode="auto", sky_mode="day", weather_mode="rain",
                        phase_t=1.25, now=datetime(2026, 6, 15, 13, 0, 0),
                        outer_bottom=orect.bottom(), outer_rect=orect,
                    )
                    pt_.end()
                self.assertEqual(img_norm.pixelColor(290, 210), img_stretch.pixelColor(290, 210))
                self.assertEqual(img_norm.pixelColor(290, 385), img_stretch.pixelColor(290, 385))
            finally:
                panel.weather_tab._refresh_timer.stop()
                island._watch_timer.stop()
                md.stop()
                wk.stop()
                island.close()
                panel.close()
                st.close()

    def test_storm_darker_clouds_snowdrifts_deflector_sun_and_home_cmd_bar(self) -> None:
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtWidgets import QPushButton
        from unittest.mock import MagicMock

        from borh_island.core.i18n import get_language, set_language
        from borh_island.ui.atmosphere import (
            AtmosphereAnimator,
            AtmosphericScenePainter,
            CloudSmokeField,
            GrassSkeletonSim,
        )
        from borh_island.ui.tabs.home_tab import HomeTab
        from borh_island.ui.theme import build_panel_qss, resolve_palette

        # 1) Storm/rain smoke clouds are substantially darker than clear sunlit clouds
        sky_r = QRectF(0.0, 0.0, 580.0, 200.0)
        img_clear = QImage(580, 200, QImage.Format.Format_ARGB32_Premultiplied)
        img_clear.fill(Qt.GlobalColor.transparent)
        p = QPainter(img_clear)
        AtmosphericScenePainter.paint_top_clouds_and_light(
            p, sky_r, "day", "clear", 1.5, cloud_sim=CloudSmokeField(seed=9),
        )
        p.end()

        img_storm = QImage(580, 200, QImage.Format.Format_ARGB32_Premultiplied)
        img_storm.fill(Qt.GlobalColor.transparent)
        p = QPainter(img_storm)
        AtmosphericScenePainter.paint_top_clouds_and_light(
            p, sky_r, "day", "lightning", 1.5, cloud_sim=CloudSmokeField(seed=9),
        )
        p.end()

        def _cloud_lum(img: QImage) -> float:
            vals = []
            for y in range(10, 100, 4):
                for x in range(40, 540, 6):
                    c = img.pixelColor(x, y)
                    if c.alpha() >= 35:
                        vals.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
            return sum(vals) / max(1, len(vals))

        self.assertGreater(_cloud_lum(img_clear), _cloud_lum(img_storm) + 25.0)

        # 2) Progressive ~60s snow accumulation ("сугробы") + melting & visible white drift rise
        anim_early = AtmosphereAnimator()
        c_rect = QRectF(0.0, 0.0, 580.0, 400.0)
        anim_early.step(c_rect, sky_mode="day", weather_mode="snow", dt=0.1)
        anim_early.step(c_rect, sky_mode="day", weather_mode="snow", dt=2.0)
        self.assertGreater(anim_early.snow_accum, 0.01)
        self.assertLess(anim_early.snow_accum, 0.10)

        anim_full = AtmosphereAnimator()
        anim_full.step(c_rect, sky_mode="day", weather_mode="snow", dt=0.1)
        for _ in range(4):
            anim_full.step(c_rect, sky_mode="day", weather_mode="snow", dt=16.0)
        self.assertGreaterEqual(anim_full.snow_accum, 0.85)

        img_sn2 = QImage(580, 400, QImage.Format.Format_ARGB32_Premultiplied)
        img_sn2.fill(Qt.GlobalColor.transparent)
        p = QPainter(img_sn2)
        AtmosphericScenePainter.paint_bottom_grass(
            p, c_rect, "day", "snow", 2.0, grass_sim=GrassSkeletonSim(), anim_state=anim_early,
        )
        p.end()

        img_sn60 = QImage(580, 400, QImage.Format.Format_ARGB32_Premultiplied)
        img_sn60.fill(Qt.GlobalColor.transparent)
        p = QPainter(img_sn60)
        AtmosphericScenePainter.paint_bottom_grass(
            p, c_rect, "day", "snow", 62.0, grass_sim=GrassSkeletonSim(), anim_state=anim_full,
        )
        p.end()

        def _ground_lum(img: QImage) -> float:
            vals = []
            for y in range(380, 396, 2):
                for x in range(40, 540, 6):
                    c = img.pixelColor(x, y)
                    vals.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
            return sum(vals) / max(1, len(vals))

        self.assertGreater(_ground_lum(img_sn60), _ground_lum(img_sn2) + 12.0)

        for _ in range(12):
            anim_full.step(c_rect, sky_mode="day", weather_mode="rain", dt=2.0)
        self.assertLess(anim_full.snow_accum, 0.20)

        # 3) Local circular cursor deflector alters only halo drops & preserves distant precipitation
        cur = QPointF(290.0, 225.0)
        with mock.patch("borh_island.core.licensing.get_license_manager") as _m_lic:
            _m_lic.return_value.is_feature_allowed.return_value = True
            img_r0 = QImage(580, 400, QImage.Format.Format_ARGB32_Premultiplied)
            img_r0.fill(Qt.GlobalColor.transparent)
            p = QPainter(img_r0)
            AtmosphericScenePainter.paint_precipitation(
                p, c_rect, "rain", 2.45, turf_y=395.0, wind_ms=4.0, cursor_local=None,
            )
            p.end()

            img_r1 = QImage(580, 400, QImage.Format.Format_ARGB32_Premultiplied)
            img_r1.fill(Qt.GlobalColor.transparent)
            p = QPainter(img_r1)
            AtmosphericScenePainter.paint_precipitation(
                p, c_rect, "rain", 2.45, turf_y=395.0, wind_ms=4.0, cursor_local=cur,
            )
            p.end()

        for y in range(60, 260, 12):
            for x in range(30, 150, 12):
                self.assertEqual(img_r0.pixelColor(x, y), img_r1.pixelColor(x, y))

        halo_diffs = sum(
            1
            for y in range(180, 270, 2)
            for x in range(250, 345, 2)
            if img_r0.pixelColor(x, y) != img_r1.pixelColor(x, y)
        )
        self.assertGreater(halo_diffs, 8)

        # 4) Photorealistic Sun photosphere & soft corona
        img_sun = QImage(240, 240, QImage.Format.Format_ARGB32_Premultiplied)
        img_sun.fill(Qt.GlobalColor.black)
        p = QPainter(img_sun)
        AtmosphericScenePainter.paint_sun(p, QPointF(120.0, 120.0), 14.0, "day", "clear", 1.2)
        p.end()
        c_core = img_sun.pixelColor(120, 120)
        c_corona = img_sun.pixelColor(142, 120)
        c_bg = img_sun.pixelColor(10, 10)
        self.assertGreater(c_core.red(), 210)
        self.assertGreater(c_corona.red() + c_corona.green(), c_bg.red() + c_bg.green() + 30)

        # 5) HomeTab Russian "Командная строка" pill fits cleanly with no text/icon overflow
        class _StubMedia(QObject):
            state_changed = pyqtSignal(object)
            lyrics_updated = pyqtSignal(str, list)
            spectrum_updated = pyqtSignal(list)

            def __getattr__(self, name: str):
                return MagicMock()

        prev_lang = get_language()
        try:
            set_language("ru")
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                st = LocalStorage(Path(td) / "hometab_ru.db")
                ht = HomeTab(_StubMedia(), st)  # type: ignore[arg-type]
                ht.setStyleSheet(build_panel_qss(resolve_palette("dark")))
                ht.resize(980, 440)
                ht.show()
                self.app.processEvents()
                try:
                    cmd_btns = [b for b in ht.findChildren(QPushButton) if "Командная строка" in b.text()]
                    self.assertEqual(len(cmd_btns), 1)
                    cb = cmd_btns[0]
                    self.assertTrue(cb.isVisible())
                    self.assertEqual(cb.height(), 30)
                    self.assertIn("border-radius: 12px", cb.styleSheet())
                    need_w = cb.fontMetrics().horizontalAdvance(cb.text()) + cb.iconSize().width() + 20
                    self.assertGreaterEqual(cb.width(), need_w)
                finally:
                    ht.close()
                    st.close()
        finally:
            set_language(prev_lang)

    def test_atoll_ui_hud_battery_lyrics_fonts_quality_and_no_ai(self) -> None:
        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtWidgets import QSlider
        from borh_island.core.config import ConfigManager
        from borh_island.core.i18n import get_language, set_language
        from borh_island.core.native_bridge import NativeBridge
        from borh_island.core.storage import LocalStorage
        from borh_island.core.winrt_worker import WinRTWorker
        from borh_island.services.clipboard_service import ClipboardService
        from borh_island.services.media_service import MediaService, MediaSessionInfo, MediaStateSnapshot
        from borh_island.services.system_service import SystemStatsSnapshot
        from borh_island.services.weather_service import WeatherService
        from borh_island.ui.atmosphere import ATMO_MODES, WEATHER_MODES, AtmosphericScenePainter
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.island_window import IslandWindow
        from borh_island.ui.settings_window import SettingsWindow
        from borh_island.ui.theme import FIVE_PRESET_FONTS, resolve_palette

        self.assertEqual(len(FIVE_PRESET_FONTS), 5)
        self.assertTrue(all("дым" not in lbl.lower() for _, lbl in ATMO_MODES))
        self.assertTrue(all("дым" not in lbl.lower() for _, lbl in WEATHER_MODES))
        self.assertTrue(any("облак" in lbl.lower() for _, lbl in WEATHER_MODES))

        prev_lang = get_language()
        try:
            set_language("ru")
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                root = Path(td)
                with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)}):
                    with (
                        mock.patch("borh_island.ui.tabs.weather_tab.WeatherTab._maybe_autodetect_city"),
                        mock.patch("borh_island.ui.tabs.weather_tab.WeatherTab.refresh_weather"),
                    ):
                        cm = ConfigManager(root)
                        st = LocalStorage(root / "atoll.db")
                        bridge = NativeBridge(root)
                        worker = WinRTWorker()
                        media = MediaService(worker, bridge)
                        clip = ClipboardService(st, cm, root / "clip")
                        weather = WeatherService()
                        pal = resolve_palette("dark")

                        panel = ExpandedPanel(cm, st, bridge, media, clip, weather, None, None, pal)
                        island = IslandWindow(cm, bridge, panel)
                        settings = SettingsWindow(cm, None)
                        try:
                            # 5 icon-only centered tabs only, no Stats / Clipboard / AI Chat / Shelf / top-right Settings
                            self.assertEqual(panel.stack.count(), 5)
                            self.assertEqual(
                                panel._tab_keys,
                                ["home", "character", "notes", "weather", "controls"],
                            )
                            self.assertFalse(hasattr(panel, "stats_tab"))
                            self.assertFalse(hasattr(panel, "clipboard_tab"))
                            self.assertFalse(hasattr(panel, "chat_tab"))
                            self.assertFalse(hasattr(panel, "shelf_tab"))
                            self.assertFalse(hasattr(panel, "settings_btn"))
                            self.assertFalse(hasattr(panel.weather_tab, "quality_slider"))

                            # HomeTab Atoll Control Hub (6 circles, 4 power pills, 3 telemetry cards without sliders, Discord & DND, screen restart)
                            hub = panel.home_tab.control_hub
                            self.assertEqual(len(hub.radio_toggles), 6)
                            self.assertIn("night_light", hub.radio_toggles)
                            self.assertEqual(len(hub.power_btns), 4)
                            self.assertEqual(hub.findChildren(QSlider), [])
                            self.assertIn("restart_screens", panel.home_tab.tool_buttons)
                            hub.update_system_state(
                                SystemStatsSnapshot(
                                    cpu_percent=42.0,
                                    ram_percent=61.0,
                                    gpu_name="NVIDIA GeForce RTX 4070",
                                    gpu_load=55.0,
                                    gpu_temp=63.0,
                                    wifi_ssid="HomeNet",
                                    wifi_radio_on=True,
                                    bt_radio_on=False,
                                    bluetooth_devices=["AirPods"],
                                    battery_percent=78,
                                    battery_plugged=True,
                                ),
                            )
                            self.assertIn("RTX 4070", hub.gpu_card.sub_lbl.text())
                            self.assertNotIn("NVIDIA GeForce", hub.gpu_card.sub_lbl.text())
                            self.assertEqual(hub.gpu_card.title_lbl.text(), "ВИДЕОКАРТА")
                            self.assertEqual(hub.cpu_card.title_lbl.text(), "ПРОЦЕССОР")
                            self.assertEqual(hub.ram_card.title_lbl.text(), "ПАМЯТЬ")
                            self.assertTrue(hub.radio_toggles["wifi"]._active)
                            self.assertFalse(hub.radio_toggles["bt"]._active)
                            hub.update_discord_voice(True, muted=False, deafened=False)
                            self.assertIn("В канале", hub.discord_status_lbl.text())

                            # Power 2-click safety confirm on destructive Home hub actions + instant lock
                            reqs: list[str] = []
                            hub.power_requested.connect(reqs.append)
                            hub.power_btns["shutdown"].click()
                            self.assertEqual(reqs, [])
                            hub.power_btns["shutdown"].click()
                            self.assertEqual(reqs, ["shutdown"])

                            # Settings 5 preset fonts + consolidated quality slider in ControlsTab & SettingsWindow
                            self.assertEqual(settings.font_combo.count(), 5)
                            self.assertEqual(settings.quality_slider.minimum(), 25)
                            self.assertEqual(settings.quality_slider.maximum(), 100)
                            self.assertEqual(panel.controls_tab.quality_slider.minimum(), 25)
                            self.assertEqual(panel.controls_tab.quality_slider.maximum(), 100)

                            # IslandWindow: tendril removed, clock HH:MM, laptop-only battery dot, centered lyrics keeping track & hiding Spotify badge
                            self.assertFalse(hasattr(island, "_build_tendril_path"))
                            island.show()
                            self.app.processEvents()
                            self.assertRegex(island.clock_lbl.text(), r"^\d{2}:\d{2}$")

                            island.set_battery_state(-1, False)
                            self.assertTrue(island.battery_dot.isHidden())
                            island.set_battery_state(84, True)
                            self.assertFalse(island.battery_dot.isHidden())

                            cm.config.show_lyrics_in_pill = True
                            snap = MediaStateSnapshot(
                                active=MediaSessionInfo(
                                    app_id="Spotify.exe",
                                    title="Night Drive",
                                    artist="Synth",
                                    status="Playing",
                                    source_id="spotify",
                                    source_name="Spotify",
                                    accent_color="#1DB954",
                                ),
                                active_lyric="",
                            )
                            island.update_media(snap)
                            self.assertFalse(island.source_badge.isHidden())
                            snap.active_lyric = "Hold on tight"
                            island.update_media(snap)
                            self.assertFalse(island.pill_lyric_lbl.isHidden())
                            self.assertIn("Night Drive", island.pill_text.text())
                            self.assertTrue(island.source_badge.isHidden())
                            snap.active_lyric = ""
                            island.update_media(snap)
                            self.assertTrue(island.pill_lyric_lbl.isHidden())
                            self.assertFalse(island.source_badge.isHidden())

                            # Collapsed music rendering without stretch transform + quality=0.35 precipitation/grass
                            rect = QRectF(0.0, 0.0, 560.0, 400.0)
                            for q in (0.35, 1.0):
                                img = QImage(560, 400, QImage.Format.Format_ARGB32_Premultiplied)
                                img.fill(Qt.GlobalColor.transparent)
                                p = QPainter(img)
                                AtmosphericScenePainter.paint_precipitation(p, rect, "rain", 1.5, turf_y=385.0, quality=q)
                                AtmosphericScenePainter.paint_bottom_grass(p, rect, "day", "rain", 1.5, outer_bottom=395.0, quality=q)
                                p.end()
                                self.assertFalse(img.isNull())
                        finally:
                            panel.weather_tab.dispose()
                            island._watch_timer.stop()
                            settings.close()
                            island.close()
                            panel.close()
                            media.stop()
                            worker.stop()
                            st.close()
        finally:
            set_language(prev_lang)

    def test_claude_mascot_vpn_radios_desktop_clean_discord_and_hover_open(self) -> None:
        from types import SimpleNamespace
        from PyQt6.QtCore import QEvent
        from borh_island.core.config import ConfigManager
        from borh_island.core.native_bridge import NativeBridge
        from borh_island.core.storage import LocalStorage
        from borh_island.core.win32_api import is_desktop_clean, show_desktop_clean
        from borh_island.core.winrt_worker import WinRTWorker
        from borh_island.services.clipboard_service import ClipboardService
        from borh_island.services.discord_service import DiscordPresenceService
        from borh_island.services.media_service import MediaService
        from borh_island.services.system_service import _detect_vpn_interface
        from borh_island.services.weather_service import WeatherService
        from borh_island.ui.characters import CharacterWidget
        from borh_island.ui.expanded_panel import ExpandedPanel
        from borh_island.ui.island_window import IslandWindow
        from borh_island.ui.theme import resolve_palette

        # 1) Claude mascot renders distinct terracotta-orange pixels
        cw = CharacterWidget("claude")
        cw.resize(96, 96)
        cw.step_frame(0.8)
        img = cw.grab().toImage()
        orange_px = 0
        for y in range(16, 84, 2):
            for x in range(16, 84, 2):
                c = img.pixelColor(x, y)
                if c.alpha() > 120 and c.red() > 170 and 60 <= c.green() <= 165 and c.blue() < 120:
                    orange_px += 1
        cw.close()
        self.assertGreater(orange_px, 25, "Claude orange bug pixels not rendered")

        # 2) _detect_vpn_interface ignores Radmin VPN & Windows Teredo Tunneling, detects AmneziaVPN
        import socket as _sock
        fake_stats = {
            "Radmin VPN": SimpleNamespace(isup=True),
            "Teredo Tunneling Pseudo-Interface": SimpleNamespace(isup=True),
            "Ethernet": SimpleNamespace(isup=True),
        }
        fake_addrs = {
            "Radmin VPN": [SimpleNamespace(family=_sock.AF_INET, address="26.1.2.3")],
            "Teredo Tunneling Pseudo-Interface": [SimpleNamespace(family=_sock.AF_INET6, address="2001:0:4625:9800::1")],
            "Ethernet": [SimpleNamespace(family=_sock.AF_INET, address="192.168.1.10")],
            "AmneziaVPN": [SimpleNamespace(family=_sock.AF_INET, address="10.8.0.2")],
        }
        with (
            mock.patch("borh_island.services.system_service.psutil.net_if_stats", return_value=fake_stats),
            mock.patch("borh_island.services.system_service.psutil.net_if_addrs", return_value=fake_addrs),
        ):
            on, name = _detect_vpn_interface()
            self.assertFalse(on)
            self.assertEqual(name, "")

        fake_stats_amnezia = {
            "Radmin VPN": SimpleNamespace(isup=True),
            "AmneziaVPN": SimpleNamespace(isup=True),
        }
        with (
            mock.patch("borh_island.services.system_service.psutil.net_if_stats", return_value=fake_stats_amnezia),
            mock.patch("borh_island.services.system_service.psutil.net_if_addrs", return_value=fake_addrs),
        ):
            on2, name2 = _detect_vpn_interface()
            self.assertTrue(on2)
            self.assertEqual(name2, "AmneziaVPN")

        # 3) Clean desktop SendMessageTimeoutW 0x7402 & visibility check
        with (
            mock.patch("borh_island.core.win32_api._find_desktop_defview", return_value=(100, 200)),
            mock.patch("borh_island.core.win32_api.user32.SendMessageTimeoutW", return_value=1) as mock_smt,
            mock.patch("borh_island.core.win32_api.user32.IsWindowVisible", return_value=0),
        ):
            self.assertTrue(is_desktop_clean())
            self.assertTrue(show_desktop_clean())
            mock_smt.assert_called_once()

        # 4) Discord mute & deafen WASAPI fallback when IPC is unavailable
        svc = DiscordPresenceService()
        mock_bridge = mock.MagicMock()
        svc.attach_audio_bridge(mock_bridge)
        with mock.patch("borh_island.services.discord_service.is_discord_running", return_value=True):
            self.assertTrue(svc.set_voice_muted(True))
            mock_bridge.set_mute.assert_called_with(True, "capture", "default")
            self.assertTrue(svc.set_voice_deafened(True))
            mock_bridge.set_app_mute.assert_called_with("discord", True, "default")

        # 5) IslandWindow rectangular sizing + hover-to-open dwell timer
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cm = ConfigManager(root)
            st = LocalStorage(root / "hover.db")
            br = NativeBridge(root)
            wk = WinRTWorker()
            md = MediaService(wk, br)
            cl = ClipboardService(st, cm, root / "clip")
            wt = WeatherService()
            with mock.patch("borh_island.ui.tabs.weather_tab.WeatherTab.refresh_weather"):
                panel = ExpandedPanel(cm, st, br, md, cl, wt, None, None, resolve_palette("dark"))
                island = IslandWindow(cm, br, panel)
                try:
                    island.show()
                    self.app.processEvents()
                    self.assertFalse(island.is_expanded)

                    from PyQt6.QtCore import QPointF, Qt
                    from PyQt6.QtGui import QEnterEvent, QMouseEvent
                    center_local = QPointF(island._core_rect().center())
                    enter_ev = QEnterEvent(center_local, center_local, QPointF(island.mapToGlobal(center_local.toPoint())))
                    island.enterEvent(enter_ev)
                    # Hovering must NOT auto-open; clicking the pill bar opens it
                    self.assertFalse(island._hover_expand_timer.isActive())
                    self.assertFalse(island.is_expanded)
                    click_ev = QMouseEvent(
                        QMouseEvent.Type.MouseButtonPress,
                        center_local,
                        QPointF(island.mapToGlobal(center_local.toPoint())),
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier,
                    )
                    island.mousePressEvent(click_ev)
                    self.app.processEvents()
                    self.assertTrue(island.is_expanded)

                    # Expanded target geometry must be wide rectangular (~1020x520 on FullHD+)
                    tgt = island._target_geometry()
                    self.assertGreater(tgt.width(), tgt.height())
                    with mock.patch("borh_island.ui.island_window.QGuiApplication.screens", return_value=[]), mock.patch("borh_island.ui.island_window.QGuiApplication.primaryScreen", return_value=None):
                        fhd_tgt = island._target_geometry()
                        self.assertGreater(fhd_tgt.width(), fhd_tgt.height() * 1.45)

                    # Clicking top pill bar while expanded collapses it, and hover never starts auto-expand timer
                    click_close = QMouseEvent(
                        QMouseEvent.Type.MouseButtonPress,
                        center_local,
                        QPointF(island.mapToGlobal(center_local.toPoint())),
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier,
                    )
                    island.mousePressEvent(click_close)
                    self.app.processEvents()
                    self.assertFalse(island.is_expanded)
                    island.enterEvent(QEnterEvent(center_local, center_local, QPointF(island.mapToGlobal(center_local.toPoint()))))
                    self.assertFalse(island._hover_expand_timer.isActive())
                    island.leaveEvent(QEvent(QEvent.Type.Leave))
                    self.assertFalse(island._hover_expand_timer.isActive())
                finally:
                    panel.weather_tab.dispose()
                    island._watch_timer.stop()
                    island.close()
                    panel.close()
                    md.stop()
                    wk.stop()
                    st.close()


if __name__ == "__main__":
    unittest.main()
