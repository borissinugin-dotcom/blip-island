"""Media playback tracking (Windows GSMTC), album-art color extraction, and loopback audio spectrum."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage

from ..core.native_bridge import NativeBridge
from ..core.win32_api import (
    bring_window_to_front,
    enum_visible_process_windows,
    find_spotify_window_track,
    is_process_running,
    send_media_app_command,
)
from ..core.winrt_worker import WinRTWorker
from .lyrics_service import LyricLine, LyricsResult, LyricsService, active_line_index


@dataclass
class MediaSessionInfo:
    app_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "Closed"  # Playing | Paused | Stopped | Closed
    position: float = 0.0
    duration: float = 0.0
    thumbnail_b64: str = ""
    accent_color: str = "#3B82F6"
    art_key: str = ""
    source_id: str = ""
    source_name: str = ""
    source_icon: str = "🎵"


_BROWSER_EXES = {
    "chrome": ("chrome.exe", "Chrome"),
    "msedge": ("msedge.exe", "Edge"),
    "edge": ("msedge.exe", "Edge"),
    "firefox": ("firefox.exe", "Firefox"),
    "opera": ("opera.exe", "Opera"),
    "brave": ("brave.exe", "Brave"),
    "yandex": ("browser.exe", "Яндекс.Браузер"),
    "browser.exe": ("browser.exe", "Яндекс.Браузер"),
}

_KNOWN_SOURCES: tuple[tuple[tuple[str, ...], str, str, str, str], ...] = (
    (("spotifyab", "spotify.exe", "spotify"), "spotify", "Spotify", "🟢", "#1DB954"),
    (("yandexmusic", "music.yandex", "yandex.music", "яндекс музыка"), "yandex_music", "Яндекс Музыка", "🟡", "#FFCC00"),
    (("applemusic", "apple.music", "itunes"), "apple_music", "Apple Music", "🍎", "#FA243C"),
    (("youtube music", "youtubemusic", "music.youtube", "yt music", "ytmusic"), "youtube_music", "YouTube Music", "🔴", "#FF0033"),
    (("youtube", "youtu.be"), "youtube", "YouTube", "▶", "#FF0000"),
    (("vk.com", "вконтакте", "vk music", "vk музыка", "vkmusic"), "vk_music", "VK Музыка", "🔵", "#0077FF"),
    (("soundcloud",), "soundcloud", "SoundCloud", "🟠", "#FF5500"),
    (("twitch",), "twitch", "Twitch", "🟣", "#9146FF"),
    (("vlc",), "vlc", "VLC", "🟠", "#FF8800"),
    (("foobar2000",), "foobar", "foobar2000", "🎛", "#3B82F6"),
    (("aimp",), "aimp", "AIMP", "🎧", "#F59E0B"),
    (("winamp",), "winamp", "Winamp", "⚡", "#F97316"),
    (("telegram",), "telegram", "Telegram", "✈", "#2AABEE"),
    (("discord",), "discord", "Discord", "🎮", "#5865F2"),
    (("zune", "mediaplayer", "microsoft.windows.mediaplayer"), "media_player", "Media Player", "🎵", "#38BDF8"),
)


def identify_media_source(
    app_id: str,
    title: str = "",
    artist: str = "",
    window_titles: list[tuple[int, str, str]] | None = None,
) -> tuple[str, str, str, str]:
    """Identify the human-friendly media source, display label, icon, and brand color."""
    low = (app_id or "").lower()
    for keys, sid, sname, ico, col in _KNOWN_SOURCES:
        if any(k in low for k in keys):
            return sid, sname, ico, col

    # Check if GSMTC reported a browser executable / AUMID; inspect visible browser tab titles
    browser_exe = ""
    browser_label = ""
    for bkey, (exe, blabel) in _BROWSER_EXES.items():
        if bkey in low:
            browser_exe = exe
            browser_label = blabel
            break

    if browser_exe:
        wins = (
            window_titles
            if window_titles is not None
            else enum_visible_process_windows({browser_exe})
        )
        t_low = (title or "").lower()
        for _hwnd, w_exe, w_title in wins:
            if w_exe.lower() != browser_exe:
                continue
            wl = w_title.lower()
            if t_low and t_low[:20] in wl:
                # Found exact playing tab title! Match service from tab title
                for keys, sid, sname, ico, col in _KNOWN_SOURCES[:8]:
                    if any(k in wl for k in keys):
                        return sid, f"{sname} ({browser_label})", ico, col
        # Check any matching browser tab even if title wasn't substring-matched
        for _hwnd, w_exe, w_title in wins:
            if w_exe.lower() != browser_exe:
                continue
            wl = w_title.lower()
            for keys, sid, sname, ico, col in _KNOWN_SOURCES[:8]:
                if any(k in wl for k in keys):
                    return sid, f"{sname} ({browser_label})", ico, col
        return browser_exe.replace(".exe", ""), browser_label, "🌐", "#38BDF8"

    clean = (app_id or "").split("!")[-1].split(".")[0].strip()
    if clean:
        return clean.lower(), clean, "🎵", "#3B82F6"
    return "unknown", "Media", "🎵", "#3B82F6"


@dataclass
class MediaStateSnapshot:
    active: MediaSessionInfo | None = None
    sessions: list[MediaSessionInfo] = field(default_factory=list)
    active_lyric: str = ""
    lyrics: LyricsResult | None = None


def extract_dominant_color(image_bytes: bytes) -> str:
    """Extract a vivid dominant accent hex color from album cover bytes using saturation-weighted hue bucketing."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            small = img.convert("RGB").resize((36, 36), Image.Resampling.BILINEAR)
            arr = np.asarray(small, dtype=np.float32).reshape(-1, 3) / 255.0
            r_c, g_c, b_c = arr[:, 0], arr[:, 1], arr[:, 2]
            cmax = arr.max(axis=1)
            cmin = arr.min(axis=1)
            delta = cmax - cmin
            sat = np.where(cmax > 1e-4, delta / cmax, 0.0)
            lum = 0.299 * r_c + 0.587 * g_c + 0.114 * b_c

            # Focus on vivid saturated pixels so a dark red or vibrant cover isn't dulled by black/white bg
            vivid = (sat > 0.22) & (lum > 0.08) & (lum < 0.92)
            if np.any(vivid):
                sub = arr[vivid]
                sub_sat = sat[vivid]
                sub_delta = delta[vivid]
                sub_max = cmax[vivid]
                # Compute HSV hue (0..24 buckets) for crisp dominant cluster selection
                hue = np.zeros(len(sub), dtype=np.float32)
                sr, sg, sb = sub[:, 0], sub[:, 1], sub[:, 2]
                mr = (sub_max == sr) & (sub_delta > 1e-4)
                mg = (sub_max == sg) & (~mr) & (sub_delta > 1e-4)
                mb = (~mr) & (~mg) & (sub_delta > 1e-4)
                hue[mr] = ((sg[mr] - sb[mr]) / sub_delta[mr]) % 6.0
                hue[mg] = ((sb[mg] - sr[mg]) / sub_delta[mg]) + 2.0
                hue[mb] = ((sr[mb] - sg[mb]) / sub_delta[mb]) + 4.0
                bins = np.clip((hue * 4.0).astype(np.int32), 0, 23)
                weights = (sub_sat ** 1.6) * np.clip(sub_max, 0.25, 1.0)
                bin_scores = np.bincount(bins, weights=weights, minlength=24)
                best_bin = int(np.argmax(bin_scores))
                # Include neighboring hue bins (+-1) so red near 0/24 wraps cleanly
                near = ((bins - best_bin) % 24 <= 1) | ((best_bin - bins) % 24 <= 1)
                sel = sub[near] if np.any(near) else sub
                sel_w = weights[near] if np.any(near) else weights
                mean = (sel * sel_w[:, None]).sum(axis=0) / max(1e-5, sel_w.sum())
                # Keep chroma vibrant and readable against dark island cards
                mx = float(mean.max())
                mn = float(mean.min())
                if mx > 0.15 and mx < 0.72:
                    mean = mean * (0.72 / mx)
                elif mn > 0.35 and (mx - mn) > 0.08:
                    mean = np.clip((mean - mn * 0.35), 0.0, 1.0)
            else:
                mean = arr.mean(axis=0)

            r, g, b = [max(24, min(248, int(round(float(c) * 255)))) for c in mean]
            return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return "#3B82F6"


class MediaService(QObject):
    """Polls GSMTC media state, fetches synced lyrics, and streams WASAPI loopback FFT."""

    state_changed = pyqtSignal(object)  # MediaStateSnapshot
    spectrum_updated = pyqtSignal(list)  # list[float] 0..1 across 20 bands
    _poll_interval_req = pyqtSignal(int)

    def __init__(self, worker: WinRTWorker, bridge: NativeBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.worker = worker
        self.bridge = bridge
        self.lyrics_service = LyricsService()

        self.preferred_app_id: str = ""
        self._art_cache: dict[str, tuple[QImage, str]] = {}
        self._art_loading: set[str] = set()
        self._art_hq_tried: set[str] = set()
        self._spotify_hwnd: int = 0
        self._last_spotify_active: MediaSessionInfo | None = None
        self._last_snapshot = MediaStateSnapshot()
        self._lyrics_cache_key = ""
        self._current_lyrics: LyricsResult | None = None
        self._lyrics_loading: set[str] = set()
        self._poll_busy = False
        self._stopped = False
        self._spectrum_running = False
        self._last_spectrum_emit = 0.0
        self._last_zero_spectrum = False
        self._last_fallback_scan_t = 0.0
        self._cached_fb_sp: tuple[int, str, str, str] | None = None
        self._cached_fb_ym_wins: list[tuple[int, str, str]] = []
        self._cached_fb_aw_wins: list[tuple[int, str, str]] = []
        self._last_emitted_idle = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(260)
        self._poll_timer.timeout.connect(self.poll_now)
        self._poll_interval_req.connect(self._set_poll_interval)

    def _set_poll_interval(self, ms: int) -> None:
        want = max(180, min(1500, int(ms)))
        if self._poll_timer.interval() != want:
            self._poll_timer.setInterval(want)

    def start(self) -> None:
        self._poll_timer.start()
        self.poll_now()

    def stop(self) -> None:
        self._stopped = True
        self._poll_timer.stop()
        self.set_spectrum_enabled(False)

    def set_preferred_player(self, app_id: str) -> None:
        self.preferred_app_id = app_id
        self._set_poll_interval(220)
        self.poll_now()

    def set_spectrum_enabled(self, enabled: bool, device_id: str = "default") -> None:
        if enabled and not self._spectrum_running:
            self._spectrum_running = True
            self.bridge.start_loopback_stream(self._on_loopback_bands, device_id=device_id)
        elif not enabled and self._spectrum_running:
            self._spectrum_running = False
            self.bridge.stop_loopback_stream()
            self.spectrum_updated.emit([0.0] * 20)

    def _on_loopback_bands(self, bands: list[float]) -> None:
        now = time.monotonic()
        if now - self._last_spectrum_emit < 0.025:
            return
        act = self._last_snapshot.active
        playing_music = bool(act and act.status == "Playing" and (act.source_id or "") not in ("discord", "telegram"))
        if not playing_music and all(float(v) < 0.002 for v in bands[:20]):
            if self._last_zero_spectrum:
                return
            self._last_zero_spectrum = True
            self._last_spectrum_emit = now
            self.spectrum_updated.emit([0.0] * 20)
            return
        self._last_zero_spectrum = False
        self._last_spectrum_emit = now
        self.spectrum_updated.emit(bands[:20])

    def get_art_qimage(self, art_key: str) -> QImage | None:
        item = self._art_cache.get(art_key)
        return item[0] if item else None

    def poll_now(self, *, skip_thumb: bool = False) -> None:
        if self._poll_busy:
            return
        self._poll_busy = True
        pref = self.preferred_app_id

        def _work() -> None:
            try:
                if getattr(self, "_stopped", False):
                    return
                raw = self.worker.get_media_state(pref, skip_thumb=skip_thumb)
                if getattr(self, "_stopped", False):
                    return
                snap = self._build_snapshot(raw)
                self._last_snapshot = snap
                act = snap.active
                is_play = bool(act and act.status == "Playing" and (act.source_id or "") not in ("discord", "telegram"))
                src_tok = (act.app_id or act.source_id or "") if (act and is_play) else ""
                sig = (is_play, src_tok.lower())
                if sig != getattr(self, "_last_loopback_filter", None):
                    self._last_loopback_filter = sig
                    self.bridge.update_loopback_filter(src_tok, is_play)

                # Adapt polling rate: fast when music plays (synced lyrics/seekbar), calm backoff when paused/idle
                if act is not None and act.status == "Playing":
                    want_ms = 220
                elif act is not None or snap.sessions:
                    want_ms = 650
                else:
                    want_ms = 1000
                try:
                    self._poll_interval_req.emit(want_ms)
                except RuntimeError:
                    return

                is_idle_now = act is None and not snap.sessions
                if not (is_idle_now and self._last_emitted_idle):
                    self._last_emitted_idle = is_idle_now
                    self.state_changed.emit(snap)
            except RuntimeError:
                pass
            finally:
                try:
                    self._poll_busy = False
                except RuntimeError:
                    pass

        threading.Thread(target=_work, daemon=True).start()

    def _build_snapshot(self, raw: dict) -> MediaStateSnapshot:
        from ..core.win32_api import list_running_process_names

        sessions: list[MediaSessionInfo] = []
        raw_sessions = list(raw.get("sessions") or [])
        act_raw = raw.get("active")
        procs = list_running_process_names()

        browser_exes = {
            "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
            "opera_gx.exe", "brave.exe", "browser.exe", "yandex.exe", "vivaldi.exe",
        }
        ym_exes = {"yandexmusic.exe", "яндекс музыка.exe"}
        aw_exes = {"aimp.exe", "aimp3.exe", "aimp4.exe", "aimp5.exe", "winamp.exe"}
        sp_exes = {"spotify.exe"}

        browser_keys = ("chrome", "msedge", "edge", "firefox", "opera", "brave", "yandex", "browser")
        need_browsers = any(
            not s.get("app_id") or any(bk in s.get("app_id", "").lower() for bk in browser_keys)
            for s in (raw_sessions + ([act_raw] if act_raw else []))
        ) and bool(procs & browser_exes)

        act_playing_raw = bool(act_raw and act_raw.get("status") == "Playing" and (act_raw.get("title") or act_raw.get("artist")))
        has_sp_proc = bool(procs & sp_exes)
        has_ym_proc = bool(procs & ym_exes) and (not act_playing_raw) and (not self.preferred_app_id or "yandex" in self.preferred_app_id.lower())
        has_aw_proc = bool(procs & aw_exes) and (not act_playing_raw) and (
            not self.preferred_app_id or any(k in self.preferred_app_id.lower() for k in ("aimp", "winamp"))
        )

        now_fb = time.monotonic()
        fb_playing = bool(
            (getattr(self, "_cached_fb_sp", None) and self._cached_fb_sp[1] == "Playing")
            or getattr(self, "_cached_fb_ym_wins", None)
            or getattr(self, "_cached_fb_aw_wins", None)
        )
        fb_ttl = 0.65 if fb_playing else 1.5
        need_fb_scan = (now_fb - getattr(self, "_last_fallback_scan_t", 0.0)) >= fb_ttl

        # Perform AT MOST ONE combined desktop HWND enumeration per tick!
        scan_exes: set[str] = set()
        if need_browsers:
            scan_exes |= browser_exes
        if has_ym_proc and need_fb_scan:
            scan_exes |= ym_exes
        if has_aw_proc and need_fb_scan:
            scan_exes |= aw_exes
        if has_sp_proc and (not act_playing_raw or need_fb_scan):
            scan_exes |= sp_exes

        all_wins = enum_visible_process_windows(scan_exes) if scan_exes else []
        if need_fb_scan and (has_sp_proc or has_ym_proc or has_aw_proc):
            self._last_fallback_scan_t = now_fb

        browser_wins = [w for w in all_wins if w[1] in browser_exes] if need_browsers else []
        for s in raw_sessions:
            app_id = s.get("app_id", "")
            title = s.get("title", "")
            artist = s.get("artist", "")
            sid, sname, sico, scol = identify_media_source(app_id, title, artist, window_titles=browser_wins)
            sessions.append(
                MediaSessionInfo(
                    app_id=app_id,
                    title=title,
                    artist=artist,
                    album=s.get("album", ""),
                    status=s.get("status", "Closed"),
                    position=float(s.get("position", 0.0) or 0.0),
                    duration=float(s.get("duration", 0.0) or 0.0),
                    accent_color=scol,
                    source_id=sid,
                    source_name=sname,
                    source_icon=sico,
                )
            )

        if has_sp_proc:
            if "spotify.exe" in scan_exes:
                sp = None
                for h_sp, _e_sp, t_sp in all_wins:
                    if _e_sp == "spotify.exe":
                        tt = (t_sp or "").strip()
                        if not tt:
                            continue
                        low_t = tt.lower()
                        if low_t in ("spotify", "spotify free", "spotify premium", "advertisement", "реклама"):
                            sp = (h_sp, "Paused", "", "")
                            break
                        for sep in (" — ", " – ", " - "):
                            if sep in tt:
                                art_s, trk_s = tt.split(sep, 1)
                                sp = (h_sp, "Playing", art_s.strip(), trk_s.strip())
                                break
                        if sp is not None:
                            break
                self._cached_fb_sp = sp
            else:
                sp = getattr(self, "_cached_fb_sp", None)
        else:
            sp = None
            self._cached_fb_sp = None
        self._spotify_hwnd = sp[0] if sp is not None else 0

        # Fallback / supplement for Yandex Music desktop app window titles if GSMTC has no playing track
        act_playing = bool(act_raw and act_raw.get("status") == "Playing" and (act_raw.get("title") or act_raw.get("artist")))
        if (not act_playing) and (
            not self.preferred_app_id or "yandex" in self.preferred_app_id.lower()
        ):
            if has_ym_proc and (ym_exes & scan_exes):
                ym_wins = [w for w in all_wins if w[1] in ym_exes]
                self._cached_fb_ym_wins = ym_wins
            elif has_ym_proc:
                ym_wins = getattr(self, "_cached_fb_ym_wins", [])
            else:
                ym_wins = []
                self._cached_fb_ym_wins = []
            for _yh, _yexe, ytitle in ym_wins:
                yt = (ytitle or "").strip()
                if yt and yt.lower() not in ("яндекс музыка", "yandex music"):
                    if " — " in yt:
                        y_art, y_tr = yt.split(" — ", 1)
                    elif " - " in yt:
                        y_art, y_tr = yt.split(" - ", 1)
                    else:
                        y_art, y_tr = "", yt
                    act_raw = {
                        "app_id": "YandexMusic.exe",
                        "title": y_tr.strip(),
                        "artist": y_art.strip(),
                        "album": "",
                        "status": "Playing",
                        "position": 0.0,
                        "duration": 0.0,
                        "thumbnail_b64": "",
                    }
                    act_playing = True
                    if not any(x.source_id == "yandex_music" for x in sessions):
                        sessions.append(
                            MediaSessionInfo(
                                app_id="YandexMusic.exe",
                                title=y_tr.strip(),
                                artist=y_art.strip(),
                                status="Playing",
                                accent_color="#FFCC00",
                                source_id="yandex_music",
                                source_name="Яндекс Музыка",
                                source_icon="🟡",
                            )
                        )
                    break

        # Fallback / supplement for AIMP / Winamp desktop windows
        if not act_playing and (
            not self.preferred_app_id
            or any(k in self.preferred_app_id.lower() for k in ("aimp", "winamp"))
        ):
            if has_aw_proc and (aw_exes & scan_exes):
                aw_wins = [w for w in all_wins if w[1] in aw_exes]
                self._cached_fb_aw_wins = aw_wins
            elif has_aw_proc:
                aw_wins = getattr(self, "_cached_fb_aw_wins", [])
            else:
                aw_wins = []
                self._cached_fb_aw_wins = []
            for _ah, aexe, atitle in aw_wins:
                raw_t = (atitle or "").strip()
                if not raw_t:
                    continue
                is_wa = "winamp" in aexe.lower()
                idle_names = ("winamp", "aimp", "aimp3", "aimp4", "aimp5")
                cleaned = raw_t
                for suf in (" - Winamp", " — Winamp", " - AIMP", " — AIMP"):
                    if cleaned.lower().endswith(suf.lower()):
                        cleaned = cleaned[: -len(suf)].strip()
                if not cleaned or cleaned.lower() in idle_names or "[stopped]" in cleaned.lower():
                    continue
                is_paused = "[paused]" in cleaned.lower()
                cleaned = cleaned.replace("[Paused]", "").replace("[paused]", "").strip()
                if " — " in cleaned:
                    a_art, a_tr = cleaned.split(" — ", 1)
                elif " - " in cleaned:
                    a_art, a_tr = cleaned.split(" - ", 1)
                else:
                    a_art, a_tr = "", cleaned
                a_status = "Paused" if is_paused else "Playing"
                if (not act_raw) or (not act_playing and a_status == "Playing"):
                    act_raw = {
                        "app_id": aexe,
                        "title": a_tr.strip(),
                        "artist": a_art.strip(),
                        "album": "",
                        "status": a_status,
                        "position": 0.0,
                        "duration": 0.0,
                        "thumbnail_b64": "",
                    }
                    act_playing = a_status == "Playing"
                if not any(x.source_id in ("aimp", "winamp") for x in sessions):
                    sessions.append(
                        MediaSessionInfo(
                            app_id=aexe,
                            title=a_tr.strip(),
                            artist=a_art.strip(),
                            status=a_status,
                            accent_color="#F97316" if is_wa else "#F59E0B",
                            source_id="winamp" if is_wa else "aimp",
                            source_name="Winamp" if is_wa else "AIMP",
                            source_icon="⚡" if is_wa else "🎧",
                        )
                    )
                break

        # Fallback / supplement for Spotify.exe desktop window if GSMTC has no playing track (and Spotify window IS playing or nothing else active)
        if (not act_playing) and (
            not self.preferred_app_id or "spotify" in self.preferred_app_id.lower()
        ):
            if sp is not None:
                _, sp_status, sp_artist, sp_title = sp
                if (sp_title or sp_artist) and (sp_status == "Playing" or not act_raw or not (act_raw.get("title") or act_raw.get("artist"))):
                    act_raw = {
                        "app_id": "spotify.exe",
                        "title": sp_title,
                        "artist": sp_artist,
                        "album": "",
                        "status": sp_status,
                        "position": 0.0,
                        "duration": 0.0,
                        "thumbnail_b64": "",
                    }
                    if not any(x.source_id == "spotify" for x in sessions):
                        sessions.append(
                            MediaSessionInfo(
                                app_id="spotify.exe",
                                title=sp_title,
                                artist=sp_artist,
                                status=sp_status,
                                accent_color="#1DB954",
                                source_id="spotify",
                                source_name="Spotify",
                                source_icon="🟢",
                            )
                        )

        if not act_raw or not (act_raw.get("title") or act_raw.get("artist")):
            spotify_alive = (not sessions) and (sp is not None or (self._last_spotify_active is not None and is_process_running("Spotify.exe")))
            if spotify_alive and self._last_spotify_active is not None:
                cached = self._last_spotify_active
                retained = MediaSessionInfo(
                    app_id=cached.app_id,
                    title=cached.title,
                    artist=cached.artist,
                    album=cached.album,
                    status="Paused",
                    position=cached.position,
                    duration=cached.duration,
                    thumbnail_b64=cached.thumbnail_b64,
                    accent_color=cached.accent_color,
                    art_key=cached.art_key,
                    source_id=cached.source_id,
                    source_name=cached.source_name,
                    source_icon=cached.source_icon,
                )
                return MediaStateSnapshot(active=retained, sessions=sessions)
            if not spotify_alive:
                self._last_spotify_active = None
            return MediaStateSnapshot(active=None, sessions=sessions)

        b64 = act_raw.get("thumbnail_b64") or ""
        art_key = hashlib.sha1(
            f"{act_raw.get('app_id')}|{act_raw.get('title')}|{act_raw.get('artist')}|{act_raw.get('album')}".encode(
                "utf-8", errors="ignore"
            )
        ).hexdigest()[:16]
        sid, sname, sico, default_brand_color = identify_media_source(
            act_raw.get("app_id", ""),
            act_raw.get("title", ""),
            act_raw.get("artist", ""),
            window_titles=browser_wins,
        )
        accent = default_brand_color
        cached_entry = self._art_cache.get(art_key)
        if cached_entry is not None:
            accent = cached_entry[1]
            cimg = cached_entry[0]
            if (
                min(cimg.width(), cimg.height()) < 240
                and art_key not in self._art_hq_tried
                and art_key not in self._art_loading
                and (act_raw.get("title") or act_raw.get("artist"))
            ):
                self._art_hq_tried.add(art_key)
                self._art_loading.add(art_key)
                threading.Thread(
                    target=self._fetch_art_bg,
                    args=(art_key, act_raw.get("title", ""), act_raw.get("artist", ""), accent),
                    daemon=True,
                ).start()
        elif b64:
            try:
                img_bytes = base64.b64decode(b64)
                qimg = QImage.fromData(img_bytes)
                accent = extract_dominant_color(img_bytes)
                if not qimg.isNull():
                    if len(self._art_cache) > 24:
                        self._art_cache.pop(next(iter(self._art_cache)), None)
                    self._art_cache[art_key] = (qimg, accent)
                    # Upgrade tiny Windows SMTC thumbnails (<240px) with full high-res cover art in background
                    if (
                        min(qimg.width(), qimg.height()) < 240
                        and art_key not in self._art_hq_tried
                        and art_key not in self._art_loading
                        and (act_raw.get("title") or act_raw.get("artist"))
                    ):
                        self._art_hq_tried.add(art_key)
                        self._art_loading.add(art_key)
                        threading.Thread(
                            target=self._fetch_art_bg,
                            args=(art_key, act_raw.get("title", ""), act_raw.get("artist", ""), accent),
                            daemon=True,
                        ).start()
            except Exception:
                pass
        elif art_key not in self._art_loading and (act_raw.get("title") or act_raw.get("artist")):
            # Non-blocking album artwork lookup (especially helpful for Spotify Desktop/Web without GSMTC thumbnail)
            self._art_hq_tried.add(art_key)
            self._art_loading.add(art_key)
            threading.Thread(
                target=self._fetch_art_bg,
                args=(art_key, act_raw.get("title", ""), act_raw.get("artist", ""), default_brand_color),
                daemon=True,
            ).start()

        active = MediaSessionInfo(
            app_id=act_raw.get("app_id", ""),
            title=act_raw.get("title", ""),
            artist=act_raw.get("artist", ""),
            album=act_raw.get("album", ""),
            status=act_raw.get("status", "Paused"),
            position=float(act_raw.get("position", 0.0) or 0.0),
            duration=float(act_raw.get("duration", 0.0) or 0.0),
            thumbnail_b64=b64,
            accent_color=accent,
            art_key=art_key,
            source_id=sid,
            source_name=sname,
            source_icon=sico,
        )

        # Trigger async lyrics fetch if track changed
        lkey = LyricsService.make_key(active.artist, active.title, active.album, active.duration)
        if lkey != self._lyrics_cache_key and lkey not in self._lyrics_loading:
            self._lyrics_cache_key = lkey
            self._current_lyrics = None
            self._lyrics_loading.add(lkey)
            threading.Thread(
                target=self._fetch_lyrics_bg,
                args=(lkey, active.title, active.artist, active.album, active.duration),
                daemon=True,
            ).start()

        lyric_line = self._extract_active_lyric_line(self._current_lyrics, lkey, active.position, active.duration)

        if active.source_id == "spotify":
            self._last_spotify_active = active
        elif sp is None and self._last_spotify_active is not None and not is_process_running("Spotify.exe"):
            self._last_spotify_active = None

        return MediaStateSnapshot(
            active=active,
            sessions=sessions,
            active_lyric=lyric_line,
            lyrics=self._current_lyrics if (self._current_lyrics and self._current_lyrics.track_key == lkey) else None,
        )

    @staticmethod
    def _extract_active_lyric_line(
        lyr: LyricsResult | None,
        lkey: str,
        position: float,
        duration: float,
    ) -> str:
        if not lyr or lyr.track_key != lkey:
            return ""
        if lyr.synced:
            idx = active_line_index(lyr.synced, position)
            if 0 <= idx < len(lyr.synced):
                return lyr.synced[idx].text
            # Before first timestamped cue, preview the opening lyric cleanly instead of staying empty
            return lyr.synced[0].text
        if lyr.plain:
            plain_lines = [ln.strip() for ln in lyr.plain.splitlines() if ln.strip()]
            if plain_lines:
                if duration > 1.0 and position >= 0.0:
                    frac = max(0.0, min(0.999, position / duration))
                    idx = min(len(plain_lines) - 1, int(frac * len(plain_lines)))
                    return plain_lines[idx]
                return plain_lines[0]
        return ""

    def _fetch_art_bg(self, art_key: str, title: str, artist: str, fallback_color: str) -> None:
        try:
            q = urllib.parse.quote(f"{artist} {title}".strip())
            if not q:
                return
            art_url = ""
            # 1) Fast iTunes artwork lookup
            try:
                url = f"https://itunes.apple.com/search?term={q}&entity=song&limit=1"
                req = urllib.request.Request(url, headers={"User-Agent": "BorhIsland/1.0"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                results = data.get("results") or []
                if results:
                    art_url = str(results[0].get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
            except Exception:
                art_url = ""

            # 2) Deezer artwork fallback if iTunes returned no match
            if not art_url:
                try:
                    d_url = f"https://api.deezer.com/search?q={q}&limit=1"
                    d_req = urllib.request.Request(d_url, headers={"User-Agent": "BorhIsland/1.0"})
                    with urllib.request.urlopen(d_req, timeout=2.5) as d_resp:
                        d_data = json.loads(d_resp.read().decode("utf-8", errors="ignore"))
                    d_hits = d_data.get("data") or []
                    if d_hits:
                        alb = d_hits[0].get("album") or {}
                        art_url = str(alb.get("cover_big") or alb.get("cover_xl") or alb.get("cover_medium") or alb.get("cover") or "")
                except Exception:
                    art_url = ""

            if not art_url:
                return
            with urllib.request.urlopen(urllib.request.Request(art_url, headers={"User-Agent": "BorhIsland/1.0"}), timeout=2.5) as r2:
                img_bytes = r2.read()
            qimg = QImage.fromData(img_bytes)
            if not qimg.isNull():
                prev = self._art_cache.get(art_key)
                if prev is not None and (prev[0].width() * prev[0].height()) > (qimg.width() * qimg.height()):
                    return
                accent = extract_dominant_color(img_bytes) or fallback_color
                if len(self._art_cache) > 24:
                    self._art_cache.pop(next(iter(self._art_cache)), None)
                self._art_cache[art_key] = (qimg, accent)
                # Push cover + dominant accent color to UI IMMEDIATELY without waiting for next poll
                if self._last_spotify_active and self._last_spotify_active.art_key == art_key:
                    self._last_spotify_active.accent_color = accent
                cur = self._last_snapshot.active
                if cur and cur.art_key == art_key:
                    cur.accent_color = accent
                    self.state_changed.emit(self._last_snapshot)
        except Exception:
            pass
        finally:
            self._art_loading.discard(art_key)

    def _fetch_lyrics_bg(self, lkey: str, title: str, artist: str, album: str, duration: float) -> None:
        try:
            res = self.lyrics_service.fetch(title, artist, album, duration)
            if res and self._lyrics_cache_key == lkey:
                self._current_lyrics = res
                snap = self._last_snapshot
                act = snap.active if snap is not None else None
                if snap is not None and act is not None:
                    cur_key = LyricsService.make_key(act.artist, act.title, act.album, act.duration)
                    if cur_key == lkey:
                        snap.lyrics = res
                        snap.active_lyric = self._extract_active_lyric_line(res, lkey, act.position, act.duration)
                        try:
                            self.state_changed.emit(snap)
                        except RuntimeError:
                            pass
        finally:
            self._lyrics_loading.discard(lkey)

    def _dispatch_cmd(self, action: str) -> None:
        app_id = self._last_snapshot.active.app_id if self._last_snapshot.active else self.preferred_app_id
        def _run() -> None:
            res = False
            if app_id != "spotify.exe":
                res = bool(self.worker.send_media_command(action, app_id))
            ok = bool(res)
            if not ok and self._spotify_hwnd:
                send_media_app_command(self._spotify_hwnd, action)
            time.sleep(0.025)
            self.poll_now(skip_thumb=True)
            time.sleep(0.075)
            self.poll_now(skip_thumb=True)
            time.sleep(0.14)
            self.poll_now(skip_thumb=False)
        threading.Thread(target=_run, daemon=True).start()

    def play_pause(self) -> None:
        self._dispatch_cmd("play_pause")

    def next_track(self) -> None:
        self._dispatch_cmd("next")

    def prev_track(self) -> None:
        self._dispatch_cmd("prev")

    def seek(self, seconds: float) -> None:
        app_id = self._last_snapshot.active.app_id if self._last_snapshot.active else self.preferred_app_id
        threading.Thread(target=lambda: (self.worker.send_media_command("seek", app_id, seconds), self.poll_now()), daemon=True).start()

    def open_source_app(self, source_id: str = "") -> None:
        """Bring the active player window (or Spotify desktop / spotify: URI) to the front."""
        sid = source_id or (self._last_snapshot.active.source_id if self._last_snapshot.active else "")
        if sid == "spotify":
            if self._spotify_hwnd and bring_window_to_front(self._spotify_hwnd):
                return
            try:
                os.startfile("spotify:")  # type: ignore[attr-defined]
            except Exception:
                pass
            return
        if self._last_snapshot.active:
            exe_hint = (self._last_snapshot.active.app_id or "").lower()
            for hwnd, exe, _t in enum_visible_process_windows():
                if exe and (exe in exe_hint or exe.replace(".exe", "") in exe_hint):
                    if bring_window_to_front(hwnd):
                        return
