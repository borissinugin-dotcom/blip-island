"""Comprehensive unit & integration tests for core, Win32/Native, WinRT, QR, AI agents, and hot-reload."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from borh_island.core.config import ConfigManager
from borh_island.core.hot_reload import HotReloadManager
from borh_island.core.native_bridge import NativeBridge
from borh_island.core.qr_codec import decode_qr_from_image, render_qr_pil, render_qr_svg
from borh_island.core.security import decrypt_secret, encrypt_secret, mask_secret
from borh_island.core.storage import LocalStorage
from borh_island.core.winrt_worker import WinRTWorker
from borh_island.services import text_tools
from borh_island.services.agents_service import AgentsService
from borh_island.services.command_service import CommandService, convert_units, safe_eval_math
from borh_island.services.lyrics_service import active_line_index, parse_lrc
from borh_island.services.reminder_parser import next_occurrence, parse_duration_seconds, parse_reminder_phrase


class SecurityAndStorageTests(unittest.TestCase):
    def test_dpapi_roundtrip_and_mask(self) -> None:
        secret = "sk-test-ключ-1234567890"
        enc = encrypt_secret(secret)
        self.assertNotEqual(enc, secret)
        self.assertEqual(decrypt_secret(enc), secret)
        self.assertIn("...", mask_secret(secret))

    def test_config_and_sqlite_storage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cm = ConfigManager(root)
            cm.config.set_secret("groq", "gsk_my_secret_token")
            cm.config.hot_reload = True
            cm.save()

            cm2 = ConfigManager(root)
            self.assertEqual(cm2.config.get_secret("groq"), "gsk_my_secret_token")
            self.assertTrue(cm2.config.hot_reload)

            st = LocalStorage(root / "test.db")
            try:
                eid = st.add_clipboard_entry("text", "Hello world", "", "Hello world", "notepad.exe", "hash1")
                self.assertGreater(eid, 0)
                st.toggle_clipboard_pin(eid)
                clips = st.list_clipboard("Hello")
                self.assertEqual(len(clips), 1)
                self.assertTrue(clips[0].pinned)

                f = root / "sample.txt"
                f.write_text("demo", encoding="utf-8")
                sid = st.add_shelf_file(str(f))
                self.assertEqual(len(st.list_shelf_items()), 1)
                self.assertEqual(st.list_shelf_items()[0].size_bytes, 4)
                st.remove_shelf_item(sid)

                nid = st.save_note(None, "Work", "Sprint", "<b>Hi</b>", "Hi")
                self.assertTrue(any(n.id == nid for n in st.list_notes("Work")))

                rid = st.add_reminder("Call", time.time() + 60, "daily")
                self.assertEqual(len(st.list_reminders()), 1)
                st.delete_reminder(rid)

                st.add_chat_message("s1", "user", "ping", [str(f)])
                msgs = st.list_chat_messages("s1")
                self.assertEqual(msgs[0].attachments, [str(f)])

                st.add_picked_color("#FF5500", "rgb(255, 85, 0)", "hsl(20, 100%, 50%)")
                self.assertEqual(st.list_picked_colors()[0]["hex_code"], "#FF5500")

                lid = st.add_launcher_item("Steam", "steam://open/main", "url", "Games", "🎮", "Ctrl+Alt+S")
                self.assertEqual(st.list_launcher_items()[0].group_name, "Games")
                st.update_launcher_item(lid, group_name="Play")
                self.assertEqual(st.list_launcher_items()[0].group_name, "Play")
            finally:
                st.close()


class TextAndParserTests(unittest.TestCase):
    def test_text_tools(self) -> None:
        self.assertEqual(text_tools.fix_keyboard_layout("ghbdtn vbh"), "привет мир")
        self.assertEqual(text_tools.fix_keyboard_layout("Руддщ Цщкдв"), "Hello World")
        self.assertEqual(text_tools.transliterate("Привет"), "Privet")
        self.assertEqual(text_tools.base64_decode(text_tools.base64_encode("Борщ")), "Борщ")
        self.assertIn("\n", text_tools.format_json('{"a":1,"b":2}'))
        self.assertEqual(text_tools.minify_json('{\n "a": 1\n}'), '{"a":1}')
        self.assertEqual(text_tools.text_stats("a b c")["words"], 3)

    def test_reminders_and_durations(self) -> None:
        now = datetime(2026, 11, 13, 18, 0, 0)  # Friday
        self.assertEqual(parse_duration_seconds("1h 30m"), 5400)
        self.assertEqual(parse_duration_seconds("полчаса"), 1800)

        r1 = parse_reminder_phrase("через 20 мин выключить плиту", now=now)
        self.assertIsNotNone(r1)
        assert r1 is not None
        self.assertEqual(int((r1.due_at - now).total_seconds()), 1200)
        self.assertIn("плиту", r1.title)

        r2 = parse_reminder_phrase("каждый будний день в 09:00 созвон", now=now)
        self.assertIsNotNone(r2)
        assert r2 is not None
        self.assertEqual(r2.repeat_rule, "weekdays")
        nxt = next_occurrence(r2.due_at, "weekdays")
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertLess(nxt.weekday(), 5)  # skips weekend cleanly

    def test_command_math_units_and_lrc(self) -> None:
        self.assertAlmostEqual(safe_eval_math("(2 + 3)^2 * 10%"), 2.5)
        self.assertIsNone(safe_eval_math("__import__('os').system('echo x')"))
        u = convert_units("10 km to mi")
        self.assertIsNotNone(u)
        assert u is not None
        self.assertIn("6.21", u[0])

        lines = parse_lrc("[00:05.00]First\n[00:12.50]Second\n[00:30.00]Third")
        self.assertEqual(len(lines), 3)
        self.assertEqual(active_line_index(lines, 13.0), 1)

        cmd = CommandService()
        # Use fallback offline rates (avoid network dependency flake in unit test)
        cmd.currency._fetched_at = time.time()
        res = cmd.currency.convert("10 usd to rub")
        self.assertIsNotNone(res)


class QRCodecTests(unittest.TestCase):
    def test_qr_encode_and_decode_roundtrip(self) -> None:
        for payload in ("https://example.com/borh-island", "Привет, Новабис Чебурек и Борщ!"):
            img = render_qr_pil(payload, box_size=8, border=4)
            decoded = decode_qr_from_image(img)
            self.assertEqual(decoded, payload)
        svg = render_qr_svg("TEST123")
        self.assertTrue(svg.startswith("<svg") and "<rect" in svg)


class AgentsHookAndUsageTests(unittest.TestCase):
    def test_claude_allow_and_codex_hook_installer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            svc = AgentsService(root, port=17499)
            started = svc.start(17499)
            if not started:
                svc.port = 17511
                started = svc.start(17511)
            self.assertTrue(started)
            try:
                seen_perm: list[str] = []

                def on_perm(req_id: str, _tool: str, _detail: str) -> None:
                    seen_perm.append(req_id)
                    svc.resolve_permission(req_id, True)

                from PyQt6.QtCore import Qt

                svc.permission_requested.connect(on_perm, Qt.ConnectionType.DirectConnection)

                body = json.dumps({
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hi"},
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"http://127.0.0.1:{svc.port}/hook/claude",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    reply = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(seen_perm), 1)
                self.assertEqual(reply["hookSpecificOutput"]["permissionDecision"], "allow")

                # Codex notify
                creq = urllib.request.Request(
                    f"http://127.0.0.1:{svc.port}/hook/codex",
                    data=json.dumps({"last-assistant-message": "All done"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(creq, timeout=5) as resp2:
                    self.assertEqual(resp2.status, 200)

                # Hook installers with .bak backup check
                fake_home = root / "home"
                (fake_home / ".claude").mkdir(parents=True)
                (fake_home / ".claude" / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
                (fake_home / ".codex").mkdir(parents=True)
                (fake_home / ".codex" / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")

                cl_path = svc.install_claude_hooks(home_dir=fake_home)
                cx_path = svc.install_codex_notify(home_dir=fake_home)
                self.assertTrue((fake_home / ".claude" / "settings.json.bak").exists())
                self.assertTrue((fake_home / ".codex" / "config.toml.bak").exists())
                self.assertIn("send_agent_hook.py", cl_path.read_text(encoding="utf-8"))
                self.assertIn("send_agent_hook.py", cx_path.read_text(encoding="utf-8"))
                self.assertTrue(svc.uninstall_claude_hooks(home_dir=fake_home))
            finally:
                svc.stop()


class HotReloadAndNativeTests(unittest.TestCase):
    def test_hot_reload_classifies_ui_vs_core(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "pkg"
            (root / "ui").mkdir(parents=True)
            (root / "core").mkdir(parents=True)
            ui_f = root / "ui" / "demo.py"
            core_f = root / "core" / "config.py"
            ui_f.write_text("X = 1\n", encoding="utf-8")
            core_f.write_text("Y = 1\n", encoding="utf-8")

            mgr = HotReloadManager(root, enabled=True)
            ui_hits: list[list[str]] = []
            core_hits: list[list[str]] = []
            mgr.ui_reload_requested.connect(ui_hits.append)
            mgr.full_restart_requested.connect(core_hits.append)

            time.sleep(0.02)
            ui_f.write_text("X = 2\n", encoding="utf-8")
            mgr._check_files()
            mgr._dispatch_changes()
            self.assertEqual(len(ui_hits), 1)
            self.assertEqual(len(core_hits), 0)

            time.sleep(0.02)
            core_f.write_text("Y = 2\n", encoding="utf-8")
            mgr._check_files()
            mgr._dispatch_changes()
            self.assertEqual(len(core_hits), 1)
            mgr.set_enabled(False)

    def test_native_bridge_and_winrt_worker(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            bridge = NativeBridge(data_dir)
            exe = bridge.ensure_compiled()
            self.assertTrue(exe.exists())

            devs = bridge.get_audio_devices()
            self.assertIn("render", devs)
            self.assertIn("capture", devs)
            vol = bridge.get_volume("render", "default")
            self.assertIn("volume", vol)
            br = bridge.get_brightness()
            self.assertIsInstance(br, int)

            # Real Media Foundation H.264 MP4 output via mp4-pipe
            mp4 = data_dir / "out.mp4"
            proc = subprocess.Popen(
                [str(exe), "mp4-pipe", "64", "64", "15", str(mp4)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert proc.stdout and proc.stdin
            self.assertEqual(proc.stdout.readline().decode("utf-8").strip(), "READY")
            frame = bytes([30, 80, 200, 255] * (64 * 64))
            for _ in range(10):
                proc.stdin.write(frame)
            proc.stdin.close()
            proc.communicate(timeout=8)
            self.assertEqual(proc.returncode, 0)
            self.assertTrue(mp4.exists() and mp4.stat().st_size > 256)
            self.assertIn(b"ftyp", mp4.read_bytes()[:64])

            # WinRT Worker: GSMTC + RU/EN OCR
            worker = WinRTWorker()
            try:
                m = worker.get_media_state()
                self.assertTrue(m.get("ok"))

                img = Image.new("RGB", (600, 120), "white")
                d = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 36)
                except Exception:
                    font = None
                d.text((20, 30), "Привет Мир", fill="black", font=font)
                ocr_p = data_dir / "ocr.png"
                img.save(ocr_p)
                txt = worker.recognize_text(str(ocr_p))
                self.assertIn("Привет", txt)
            finally:
                worker.stop()

    def test_media_source_identification_and_spotify_snapshot(self) -> None:
        from borh_island.services.media_service import MediaService, identify_media_source

        self.assertEqual(identify_media_source("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")[:3], ("spotify", "Spotify", "🟢"))
        self.assertEqual(identify_media_source("spotify.exe")[:3], ("spotify", "Spotify", "🟢"))
        self.assertEqual(
            identify_media_source(
                "chrome.exe",
                title="Bohemian Rhapsody",
                window_titles=[(1, "chrome.exe", "Bohemian Rhapsody - YouTube Music - Google Chrome")],
            )[:3],
            ("youtube_music", "YouTube Music (Chrome)", "🔴"),
        )
        self.assertEqual(
            identify_media_source(
                "browser.exe",
                title="Кукушка",
                window_titles=[(2, "browser.exe", "Кукушка — Яндекс Музыка")],
            )[:3],
            ("yandex_music", "Яндекс Музыка (Яндекс.Браузер)", "🟡"),
        )
        self.assertEqual(identify_media_source("vlc.exe")[:2], ("vlc", "VLC"))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bridge = NativeBridge(root)
            worker = WinRTWorker()
            try:
                svc = MediaService(worker, bridge)
                svc._fetch_art_bg = lambda *_a, **_kw: None  # type: ignore[method-assign]
                raw = {
                    "ok": True,
                    "sessions": [
                        {
                            "app_id": "Spotify.exe",
                            "title": "Midnight City",
                            "artist": "M83",
                            "album": "Hurry Up",
                            "status": "Playing",
                            "position": 12.0,
                            "duration": 243.0,
                            "thumbnail_b64": "",
                        }
                    ],
                    "active": {
                        "app_id": "Spotify.exe",
                        "title": "Midnight City",
                        "artist": "M83",
                        "album": "Hurry Up",
                        "status": "Playing",
                        "position": 12.0,
                        "duration": 243.0,
                        "thumbnail_b64": "",
                    },
                }
                snap = svc._build_snapshot(raw)
                self.assertIsNotNone(snap.active)
                assert snap.active is not None
                self.assertEqual(snap.active.source_id, "spotify")
                self.assertEqual(snap.active.source_name, "Spotify")
                self.assertEqual(snap.active.accent_color, "#1DB954")
            finally:
                svc.stop()
                worker.stop()


if __name__ == "__main__":
    unittest.main()
