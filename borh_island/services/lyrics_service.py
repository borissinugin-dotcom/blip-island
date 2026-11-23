"""LRCLIB synced/plain lyrics client, LRC parser, and O(log N) playback sync."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

import httpx

_LRC_TAG_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_NOISE_SUFFIX_RE = re.compile(
    r"\s*(?:[\(\[][^)\]]*(?:feat\.?|ft\.?|official|video|audio|lyrics?|remix|remaster(?:ed)?|live|explicit|clean|version|radio|edit|visualizer|hd|4k|клип|текст)[^)\]]*[\)\]]|\s*[-–—]\s*(?:remaster(?:ed)?|live|official|explicit|feat\.?|ft\.?).*$)",
    re.IGNORECASE,
)


def _clean_search_meta(title: str, artist: str) -> tuple[str, str]:
    """Strip common streaming/YouTube/VK title noise and infer 'Artist - Title' when artist is blank."""
    c_title = " ".join(_NOISE_SUFFIX_RE.sub("", title).split()).strip(" -–—|")
    c_artist = " ".join(_NOISE_SUFFIX_RE.sub("", artist).split()).strip(" -–—|,")
    if not c_title:
        c_title = title.strip()
    if not c_artist:
        for sep in (" — ", " - ", " – "):
            if sep in c_title:
                left, right = c_title.split(sep, 1)
                if left.strip() and right.strip():
                    c_artist, c_title = left.strip(), right.strip()
                break
    return c_title, c_artist


@dataclass
class LyricLine:
    time_sec: float
    text: str


@dataclass
class LyricsResult:
    track_key: str
    synced: list[LyricLine]
    plain: str
    instrumental: bool = False


def parse_lrc(lrc_text: str) -> list[LyricLine]:
    """Parse LRC string into timestamp-sorted LyricLine objects, splitting multi-line verse blobs."""
    raw_lines: list[LyricLine] = []
    for raw_line in lrc_text.splitlines():
        matches = list(_LRC_TAG_RE.finditer(raw_line))
        if not matches:
            continue
        text = _LRC_TAG_RE.sub("", raw_line).strip()
        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac_s = m.group(3) or "0"
            frac = int(frac_s) / (10.0 ** len(frac_s))
            raw_lines.append(LyricLine(time_sec=mins * 60.0 + secs + frac, text=text or "♪"))
    raw_lines.sort(key=lambda ln: ln.time_sec)

    lines: list[LyricLine] = []
    for idx, ln in enumerate(raw_lines):
        txt = ln.text.strip()
        if len(txt) > 64:
            # Split concatenated verse lines starting with a capital Russian/Latin word after space
            parts = [p.strip() for p in re.split(r"(?<=[а-яёa-z.!?,;])\s+(?=[А-ЯЁA-Z][а-яёa-z]|(?:[А-ЯЁA-Z]\s+[а-яёa-z]))", txt) if p.strip()]
            if len(parts) > 1:
                next_t = raw_lines[idx + 1].time_sec if idx + 1 < len(raw_lines) else (ln.time_sec + len(parts) * 2.8)
                span = max(1.0, next_t - ln.time_sec)
                step = span / len(parts)
                for p_i, part in enumerate(parts):
                    lines.append(LyricLine(time_sec=ln.time_sec + p_i * step, text=part))
                continue
        lines.append(ln)
    return lines


def active_line_index(lines: list[LyricLine], position_sec: float) -> int:
    """Binary search index of active lyric line at position_sec (-1 if before first line)."""
    if not lines:
        return -1
    times = [ln.time_sec for ln in lines]
    idx = bisect.bisect_right(times, position_sec + 0.15) - 1
    return idx if idx >= 0 else -1


class LyricsService:
    """Asynchronous-friendly LRCLIB client with in-memory LRU cache."""

    def __init__(self, max_cache: int = 64) -> None:
        self._cache: dict[str, LyricsResult] = {}
        self._max_cache = max_cache

    @staticmethod
    def make_key(artist: str, title: str, album: str = "", duration: float = 0.0) -> str:
        return f"{artist.strip().lower()}|{title.strip().lower()}|{album.strip().lower()}|{int(round(duration))}"

    def fetch(self, title: str, artist: str = "", album: str = "", duration: float = 0.0) -> LyricsResult | None:
        """Fetch lyrics synchronously (run from worker thread)."""
        clean_title = title.strip()
        if not clean_title:
            return None
        key = self.make_key(artist, clean_title, album, duration)
        if key in self._cache:
            return self._cache[key]

        headers = {"User-Agent": "DynamicIslandBorhEdition/1.0 (Windows)"}
        params: dict[str, str | int] = {"track_name": clean_title}
        if artist.strip():
            params["artist_name"] = artist.strip()
        if album.strip():
            params["album_name"] = album.strip()
        if duration > 10:
            params["duration"] = int(round(duration))

        data: dict | None = None
        try:
            with httpx.Client(timeout=8.0, headers=headers) as client:
                if artist.strip():
                    resp = client.get("https://lrclib.net/api/get", params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                if not data:
                    q = f"{artist.strip()} {clean_title}".strip()
                    resp = client.get("https://lrclib.net/api/search", params={"q": q})
                    if resp.status_code == 200:
                        items = resp.json()
                        if isinstance(items, list) and items:
                            # Prefer entry with syncedLyrics
                            data = next((it for it in items if it.get("syncedLyrics")), items[0])
                if not data:
                    t_cl, a_cl = _clean_search_meta(clean_title, artist)
                    q_cl = f"{a_cl} {t_cl}".strip()
                    if q_cl and q_cl.lower() != f"{artist.strip()} {clean_title}".strip().lower():
                        resp = client.get("https://lrclib.net/api/search", params={"q": q_cl})
                        if resp.status_code == 200:
                            items = resp.json()
                            if isinstance(items, list) and items:
                                data = next((it for it in items if it.get("syncedLyrics")), items[0])
                    if not data and t_cl and t_cl.lower() != q_cl.lower():
                        resp = client.get("https://lrclib.net/api/search", params={"q": t_cl})
                        if resp.status_code == 200:
                            items = resp.json()
                            if isinstance(items, list) and items:
                                data = next((it for it in items if it.get("syncedLyrics")), items[0])
        except Exception:
            return None

        if not data:
            return None

        synced_raw = data.get("syncedLyrics") or ""
        plain_raw = data.get("plainLyrics") or ""
        res = LyricsResult(
            track_key=key,
            synced=parse_lrc(synced_raw) if synced_raw else [],
            plain=plain_raw.strip(),
            instrumental=bool(data.get("instrumental", False)),
        )
        if len(self._cache) >= self._max_cache:
            oldest = next(iter(self._cache))
            self._cache.pop(oldest, None)
        self._cache[key] = res
        return res
